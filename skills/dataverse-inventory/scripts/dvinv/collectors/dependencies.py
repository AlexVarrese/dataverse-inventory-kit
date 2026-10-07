"""Dependências registradas pela própria plataforma (deep.platform_dependencies).

- RetrieveDependentComponents: o que depende de cada componente do escopo. É a mesma base que o
  Dataverse usa para bloquear exclusão ("não é possível excluir porque é usado por…").
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


def collect_platform_dependencies(ctx):
    c, cfg = ctx.client, ctx.cfg
    if not cfg.deep.get("platform_dependencies"):
        return
    rows, failed = [], 0
    tg = targets(ctx)

    def fetch(t):
        ctype, oid, _ = t
        return c.get(f"RetrieveDependentComponents(ObjectId=@p1,ComponentType=@p2)?@p1={oid}&@p2={ctype}")

    for (ctype, oid, label_), res, err in c.parallel(fetch, tg, label="dependências da plataforma"):
        if err:
            failed += 1
            if failed <= 20:
                ctx.gap("platform_dependencies", f"dependentes de {label_}", err)
            continue
        rows += [_row(r) for r in res.get("value") or []]

    missing = []
    for s in ctx.data.get("solutions") or []:
        if s["uniquename"].lower() in SYSTEM_SOLUTIONS or not (s.get("in_scope") or s.get("managed") is False):
            continue
        try:
            res = c.get(f"RetrieveMissingDependencies(SolutionUniqueName=@p1)?@p1='{s['uniquename']}'")
            for r in res.get("value") or []:
                missing.append({"solution": s["uniquename"], **_row(r)})
        except Exception as e:  # noqa: BLE001
            ctx.gap("platform_dependencies", f"dependências ausentes da solução {s['uniquename']}", e)

    # dedupe (o mesmo par aparece quando se consulta assembly e classe)
    seen, uniq = set(), []
    for r in rows:
        k = (r["dependent_id"], r["required_id"], r["dependency_type"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    ctx.stats["platform_dependencies"] = {"queried": len(tg), "edges": len(uniq), "failed": failed,
                                          "missing": len(missing)}
    ctx.data["platform_dependencies"] = {"edges": uniq, "missing": missing}
