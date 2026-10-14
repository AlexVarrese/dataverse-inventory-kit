"""Dependências registradas pela própria plataforma (deep.platform_dependencies).

- Tabela `dependency`, lida em lote: TODAS as dependências do ambiente (ESAB-TEST: 844 mil linhas em
  3 min, sem throttling). É a mesma base que o Dataverse usa para bloquear exclusão ("não é possível
  excluir porque é usado por…") e que RetrieveDependentComponents consulta item a item.
- Se a leitura em lote falhar (ex. 403), cai para RetrieveDependentComponents por componente do
  escopo (comportamento até a 0.5.0) e registra a lacuna.
- RetrieveMissingDependencies: por solução não gerenciada do escopo, componentes exigidos que não
  estão na solução — ela falharia ao importar num ambiente que não os tenha.

Complementa as dependências *inferidas* pelo kit (render/dependencies): a plataforma não enxerga
JS chamando Custom API, flow chamando host HTTP ou código citando coluna; o kit não enxerga
dependências internas de metadata (ex. view que usa uma coluna, sitemap que aponta para tabela).
"""

from ..config import SYSTEM_SOLUTIONS
from ..util import fv

# componenttype da tabela solutioncomponent / dependency
CT_ENTITY, CT_ATTRIBUTE, CT_OPTIONSET, CT_RELATIONSHIP, CT_ROLE = 1, 2, 9, 10, 20
CT_VIEW, CT_WORKFLOW, CT_FORM, CT_WEBRESOURCE, CT_APP = 26, 29, 60, 61, 80
CT_PLUGINTYPE, CT_ASSEMBLY, CT_STEP, CT_ENVVAR = 90, 91, 92, 380


def _row(r):
    return {
        "dependent_type": r.get("dependentcomponenttype"),
        "dependent_type_label": fv(r, "dependentcomponenttype"),
        "dependent_id": (r.get("dependentcomponentobjectid") or "").lower(),
        "dependent_parent_id": (r.get("dependentcomponentparentid") or "").lower() or None,
        "required_type": r.get("requiredcomponenttype"),
        "required_type_label": fv(r, "requiredcomponenttype"),
        "required_id": (r.get("requiredcomponentobjectid") or "").lower(),
        "required_parent_id": (r.get("requiredcomponentparentid") or "").lower() or None,
        "dependency_type": fv(r, "dependencytype") or r.get("dependencytype"),
    }


def targets(ctx):
    """(componenttype, objectid, rótulo) de tudo do escopo que vale perguntar 'quem depende de mim?'."""
    d = ctx.data
    out = []
    for t in d.get("tables") or []:
        if t.get("id"):
            out.append((CT_ENTITY, t["id"], f"tabela {t['logical']}"))
        if ctx.cfg.deep.get("platform_dependencies_columns"):
            out += [(CT_ATTRIBUTE, c["id"], f"coluna {t['logical']}.{c['logical']}")
                    for c in t.get("columns") or [] if c.get("custom") and c.get("id")]
    out += [(CT_WEBRESOURCE, w["id"], f"web resource {w['name']}") for w in d.get("webresources") or []]
    out += [(CT_WORKFLOW, p["id"], f"processo {p['name']}") for p in d.get("processes") or []]
    for a in (d.get("plugins") or {}).get("assemblies") or []:
        out.append((CT_ASSEMBLY, a["id"], f"assembly {a['name']}"))
        out += [(CT_PLUGINTYPE, t["id"], f"classe {t['typename']}") for t in a.get("types") or []]
    out += [(CT_OPTIONSET, o["id"], f"option set {o['name']}") for o in d.get("optionsets") or [] if o.get("id")]
    out += [(CT_ENVVAR, e["id"], f"variável {e['name']}") for e in (d.get("alm") or {}).get("envvars") or []]
    return out


DEP_SELECT = ("dependentcomponenttype,dependentcomponentobjectid,requiredcomponenttype,"
              "requiredcomponentobjectid,dependencytype")


class _Compact:
    """Arestas em listas de inteiros: nós [[ct, id]] + arestas [[i_dependente, i_requerido, tipo]].
    844 mil arestas em dicionários passariam de 500 MB; assim ficam em ~25 MB."""

    def __init__(self):
        self.nodes, self.index, self.edges, self.seen = [], {}, [], set()

    def node(self, ct, oid):
        oid = (oid or "").lower()
        k = self.index.get(oid)
        if k is None:
            k = self.index[oid] = len(self.nodes)
            self.nodes.append([ct, oid])
        return k

    def add(self, r):
        if not r.get("dependentcomponentobjectid") or not r.get("requiredcomponentobjectid"):
            return
        a = self.node(r.get("dependentcomponenttype"), r["dependentcomponentobjectid"])
        b = self.node(r.get("requiredcomponenttype"), r["requiredcomponentobjectid"])
        k = (a, b, r.get("dependencytype"))
        if a != b and k not in self.seen:  # o mesmo par aparece quando se consulta assembly e classe
            self.seen.add(k)
            self.edges.append(list(k))


def collect_platform_dependencies(ctx):
    c, cfg = ctx.client, ctx.cfg
    if not cfg.deep.get("platform_dependencies"):
        return
    g, failed, source, tg = _Compact(), 0, "tabela dependency (lote)", []
    try:
        for r in c.iter_all(f"dependencies?$select={DEP_SELECT}"):
            g.add(r)
    except Exception as e:  # noqa: BLE001
        ctx.gap("platform_dependencies", "leitura em lote da tabela dependency — usando RetrieveDependentComponents "
                "por componente do escopo", e)
        g, source, tg = _Compact(), "RetrieveDependentComponents (por componente do escopo)", targets(ctx)

        def fetch(t):
            ctype, oid, _ = t
            return c.get(f"RetrieveDependentComponents(ObjectId=@p1,ComponentType=@p2)?@p1={oid}&@p2={ctype}")

        for (ctype, oid, label_), res, err in c.parallel(fetch, tg, label="dependências da plataforma"):
            if err:
                failed += 1
                if failed <= 20:
                    ctx.gap("platform_dependencies", f"dependentes de {label_}", err)
                continue
            for r in res.get("value") or []:
                g.add(r)

    # Uma chamada por solução, em paralelo (cfg.parallel): no ESAB-TEST são ~780 soluções e, com o servidor
    # lento, a versão sequencial passava de 40 min.
    missing = []
    sols = [s for s in ctx.data.get("solutions") or []
            if s["uniquename"].lower() not in SYSTEM_SOLUTIONS and (s.get("in_scope") or s.get("managed") is False)]

    def fetch_missing(s):
        return c.get(f"RetrieveMissingDependencies(SolutionUniqueName=@p1)?@p1='{s['uniquename']}'")

    for s, res, err in c.parallel(fetch_missing, sols, label="dependências ausentes por solução"):
        if err:
            ctx.gap("platform_dependencies", f"dependências ausentes da solução {s['uniquename']}", err)
            continue
        missing += [{"solution": s["uniquename"], **_row(r)} for r in res.get("value") or []]
    missing.sort(key=lambda m: (m["solution"].lower(), m["dependent_id"], m["required_id"]))  # ordem estável

    ctx.stats["platform_dependencies"] = {"source": source, "queried": len(tg), "edges": len(g.edges),
                                          "nodes": len(g.nodes), "failed": failed, "missing": len(missing)}
    ctx.data["platform_dependencies"] = {"format": "compacto/1", "source": source, "nodes": g.nodes,
                                         "edges": g.edges, "missing": missing}
