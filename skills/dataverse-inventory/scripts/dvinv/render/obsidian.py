"""Gera o vault Obsidian a partir de _raw/*.json (convenções kepano/obsidian-skills).

- Uma nota por componente, com properties tipadas (frontmatter) para alimentar Obsidian Bases.
- Wikilinks com caminho a partir da raiz do vault (`[[Pasta/Nota|alias]]`) — sem ambiguidade entre
  ambientes no mesmo vault.
- Tudo abaixo do marcador MANUAL é do analista e é preservado em re-execuções; idem para as
  properties em PRESERVE_PROPS (ex.: status/responsável de um achado).
"""

import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import yaml

from .. import dependencies as deps_mod
from .. import findings as findings_mod
from .. import xlsx
from ..config import SYSTEM_SOLUTIONS
from ..util import load_json, save_json
from . import bases, canvas

MANUAL = "%% dvinv:manual — o conteúdo abaixo desta linha é preservado nas próximas extrações %%"
PRESERVE_PROPS = {"status", "responsavel", "decisao", "prazo", "revisado", "tags_extra"}
BAD_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]]+')

SEV_CALLOUT = {"crítico": "danger", "alto": "warning", "médio": "caution", "baixo": "note", "info": "info"}
CATEGORY_FOLDER = {
    "Workflow": "Workflows", "Dialog": "Dialogs", "Business Rule": "Business Rules", "Action": "Actions",
    "Business Process Flow": "BPFs", "Cloud Flow": "Cloud Flows", "Desktop Flow": "Desktop Flows",
}


def safe(name, maxlen=110):
    s = BAD_CHARS.sub("-", str(name or "sem-nome")).strip().strip(".")
    return (s[:maxlen].rstrip() or "sem-nome")


def cell(v):
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "✅" if v else "❌"
    return str(v).replace("|", "\\|").replace("\n", " ")


def mdtable(headers, rows):
    if not rows:
        return "_nenhum_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def split_frontmatter(text):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            try:
                return yaml.safe_load(text[4:end]) or {}, text[end + 5:]
            except yaml.YAMLError:
                pass
    return {}, text


class Vault:
    def __init__(self, vault_root, folder, env_name):
        self.root = Path(vault_root)
        self.folder = folder.strip("/")
        self.env = env_name
        self.paths = {}        # (kind, key) -> caminho relativo à raiz do vault, sem .md
        self.taken = set()
        self.written = []
        self.dep_counts = {}   # caminho da nota -> {"dependencias": n, "dependentes": n}
        self.today = date.today()

    # ---------- caminhos e links ----------
    def reserve(self, kind, key, subfolder, title):
        base = f"{self.folder}/{subfolder}/{safe(title)}" if subfolder else f"{self.folder}/{safe(title)}"
        rel, n = base, 2
        while rel.lower() in self.taken:
            rel = f"{base} ({n})"
            n += 1
        self.taken.add(rel.lower())
        self.paths[(kind, key)] = rel
        return rel

    def link(self, kind, key, alias=None, in_table=False):
        rel = self.paths.get((kind, key))
        if not rel:
            return alias or (str(key) if key else "—")
        alias = alias or rel.rsplit("/", 1)[-1]
        return f"[[{rel}|{alias}]]"  # dentro de tabela, cell() escapa o pipe

    def link_path(self, rel, alias, in_table=False):
        return f"[[{self.folder}/{rel}|{alias}]]"

    # ---------- escrita ----------
    def write(self, rel, props, body, raw=False):
        path = self.root / (rel if raw else rel + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw:
            path.write_text(body, encoding="utf-8")
            self.written.append(path)
            return
        manual = f"{MANUAL}\n\n## Notas do analista\n\n"
        if path.exists():
            old_props, old_body = split_frontmatter(path.read_text(encoding="utf-8"))
            for k in PRESERVE_PROPS:
                if k in old_props:
                    props[k] = old_props[k]
            if MANUAL in old_body:
                manual = old_body[old_body.index(MANUAL):]
        props = {"ambiente": self.env, **props, **self.dep_counts.get(rel, {}), "extraido_em": self.today}
        fm = yaml.safe_dump(props, allow_unicode=True, sort_keys=False, width=1000).strip()
        path.write_text(f"---\n{fm}\n---\n{body.rstrip()}\n\n{manual.rstrip()}\n", encoding="utf-8")
        self.written.append(path)

    def tags(self, kind):
        return ["dataverse", f"dataverse/{kind}", f"ambiente/{safe(self.env).lower().replace(' ', '-')}"]


def load_raw(raw_dir):
    raw_dir = Path(raw_dir)
    d = {}
    for p in raw_dir.glob("*.json"):
        d[p.stem] = load_json(p)
    return d


def render(cfg):
    d = load_raw(cfg.raw_dir)
    if not d.get("manifest"):
        raise SystemExit(f"Nada para renderizar em {cfg.raw_dir} — rode 'extract' antes.")
    fnd = findings_mod.compute(d)
    save_json(cfg.raw_dir / "findings.json", fnd)
    v = Vault(cfg.vault_root, cfg.vault_folder, cfg.name)

    tables = d.get("tables") or []
    plugins = d.get("plugins") or {"assemblies": [], "steps": []}
    procs = d.get("processes") or []
    wrs = d.get("webresources") or []
    forms = d.get("forms") or []
    views = d.get("views") or []
    rels = d.get("relationships") or []
    apis = d.get("customapis") or []
    apps = d.get("apps") or {}
    sols = d.get("solutions") or []
    sec = d.get("security") or {}
    alm = d.get("alm") or {}
    ribbons = d.get("ribbons") or []

    # ---- 1) reservar caminhos (para que todo link resolva, independente da ordem de escrita) ----
    for f in fnd:
        v.reserve("finding", f["id"], "Achados", f"{f['id']} {f['title']}"[:90])
    for t in tables:
        v.reserve("table", t["logical"], "Tabelas", t["logical"])
    for a in plugins["assemblies"]:
        v.reserve("assembly", a["name"], "Plugins", a["name"])
    for s in plugins["steps"]:
        v.reserve("step", s["id"], "Plugin Steps", s["name"] or s["id"])
    for p in procs:
        v.reserve("process", p["id"], f"Processos/{CATEGORY_FOLDER.get(p['category'], p['category'])}", p["name"])
    for w in wrs:
        v.reserve("webresource", w["name"], "Web Resources", w["name"].replace("/", "-"))
    for a in apis:
        v.reserve("customapi", a["unique"], "Custom APIs", a["unique"])
    for kind in ("modeldriven", "canvas", "bots"):
        for a in apps.get(kind) or []:
            v.reserve("app", a["id"], "Apps", a["name"])
    for s in sols:
        if s["uniquename"].lower() in SYSTEM_SOLUTIONS:
            continue
        if s["managed"] is False or s.get("in_scope"):
            v.reserve("solution", s["uniquename"], "Soluções", s["uniquename"])
    for r in sec.get("roles") or []:
        if r.get("scope_reason"):
            v.reserve("role", r["name"], "Papéis", r["name"])
    for kind, section in (("envvar", "Variáveis de ambiente"), ("connref", "Referências de conexão"),
                          ("endpoint", "Service endpoints")):
        src = (alm.get("envvars") if kind == "envvar" else alm.get("connrefs") if kind == "connref"
               else d.get("serviceendpoints")) or []
        for x in src:
            v.paths[(kind, x["name"])] = f"{v.folder}/03 Integrações#{section}"

    def sol_links(names):
        return [v.link("solution", n, n) for n in names or [] if ("solution", n) in v.paths]

    finding_refs = defaultdict(list)
    for f in fnd:
        for kind, key in f["refs"]:
            finding_refs[(kind, key)].append(f)

    # ---- grafo de dependências (antes das notas: cada nota mostra 'depende de' / 'usado por') ----
    graph = deps_mod.build(d)
    dsum = deps_mod.summarize(graph, d)
    save_json(cfg.raw_dir / "dependencies.json", deps_mod.to_json(graph, dsum))
    for n in graph.nodes:
        if n in v.paths:
            v.dep_counts[v.paths[n]] = {"dependencias": dsum["fan_out"][n], "dependentes": dsum["fan_in"][n]}

    def node_link(n, in_table=False):
        node = graph.nodes[n]
        kind, key = n
        if n in v.paths:
            return v.link(kind, key, node["label"])
        if kind in ("form", "view", "column") and ("table", node.get("sub")) in v.paths:
            return f"{node['label']} ({v.link('table', node['sub'], node['sub'])})"
        return f"{node['label']}" + ("" if kind in ("process",) else f" _{deps_mod.KIND_LABEL.get(kind, kind).lower()}_")

    def deps_block(kind, key):
        n = (kind, key)
        if n not in graph.nodes:
            return ""
        out, inn = graph.out_of(n), graph.into(n)
        if not (out or inn):
            return "## Dependências\n\n_Nenhuma dependência encontrada (nem dependentes)._\n"
        lines = ["## Dependências", ""]
        for title, items in ((f"Depende de ({len(out)})", out), (f"Usado por ({len(inn)})", inn)):
            if not items:
                continue
            lines += [f"### {title}", ""]
            by_rel = defaultdict(list)
            for other, rel, e in items:
                by_rel[rel].append((other, e))
            for rel, lst in sorted(by_rel.items()):
                shown = ", ".join(node_link(o) + (" ⚙︎" if "plataforma" in e["source"] else "") for o, e in lst[:150])
                more = f" … +{len(lst) - 150}" if len(lst) > 150 else ""
                lines.append(f"- **{rel}:** {shown}{more}")
            lines.append("")
        if any("plataforma" in e["source"] for _, _, e in out + inn):
            lines.append("⚙︎ = registrada pela plataforma (RetrieveDependentComponents)\n")
        return "\n".join(lines) + "\n"

    def findings_block(kind, key):
        return deps_block(kind, key) + _findings_only(kind, key)

    def _findings_only(kind, key):
        fs = list({f["id"]: f for f in finding_refs.get((kind, key), [])}.values())
        if not fs:
            return ""
        lines = ["## Achados", ""]
        for f in fs:
            lines.append(f"- {v.link('finding', f['id'], f['id'] + ' — ' + f['title'])} · **{f['severity']}**")
        return "\n".join(lines) + "\n"

    # índices auxiliares
    steps_by_table = defaultdict(list)
    for s in plugins["steps"]:
        steps_by_table[s["entity"]].append(s)
    procs_by_table = defaultdict(list)
    flows_touching = defaultdict(list)
    for p in procs:
        if p.get("entity"):
            procs_by_table[p["entity"]].append(p)
        for t in p.get("tables") or []:
            if p.get("entity") != t:
                flows_touching[t].append(p)
    forms_by_table = defaultdict(list)
    for f in forms:
        forms_by_table[f["entity"]].append(f)
    views_by_table = defaultdict(list)
    for x in views:
        views_by_table[x["entity"]].append(x)
    rels_by_table = defaultdict(list)
    for r in rels:
        rels_by_table[r["from"]].append(r)
        if r["to"] != r["from"]:
            rels_by_table[r["to"]].append(r)
    ribbons_by_table = defaultdict(list)
    for r in ribbons:
        ribbons_by_table[r["entity"]].append(r)
    wr_usage = defaultdict(list)
    for f in forms:
        for lib in f.get("libraries") or []:
            wr_usage[lib.lower()].append(f)
    matrix = sec.get("privilege_matrix") or {}
    fu_by_table = {t["table"]: t for t in d.get("field_usage") or []}
    wr_repo, type_repo = defaultdict(list), defaultdict(list)
    for r in d.get("repos") or []:
        for w in r.get("webresources") or []:
            wr_repo[w["name"]].append((r["name"], w))
        for t in r.get("plugin_types") or []:
            type_repo[t["type"]].append((r["name"], t))
    apis_by_table = defaultdict(list)
    for a in apis:
        if a.get("bound_entity"):
            apis_by_table[a["bound_entity"]].append(a)

    # ---- 2) tabelas ----
    for t in tables:
        ln = t["logical"]
        custom_cols = [c for c in t["columns"] if c["custom"]]
        st, pr, fl = steps_by_table.get(ln, []), procs_by_table.get(ln, []), flows_touching.get(ln, [])
        props = {
            "tipo": "tabela", "nome_logico": ln, "nome_exibicao": t.get("display"), "aliases": [t["display"]] if t.get("display") else [],
            "customizada": bool(t["custom"]), "gerenciada": t.get("managed"), "propriedade": t.get("ownership"),
            "registros": t.get("record_count"), "colunas_custom": len(custom_cols), "colunas_total": t.get("column_count"),
            "relacionamentos": len(rels_by_table.get(ln, [])), "formularios": len(forms_by_table.get(ln, [])),
            "plugin_steps": len(st), "processos": len(pr), "flows_que_tocam": len(fl),
            "achados": len(finding_refs.get(("table", ln), [])), "escopo": t.get("scope_reason"),
            "solucoes": sol_links(t.get("solutions")), "tags": v.tags("tabela"),
        }
        b = [f"# {t.get('display') or ln} (`{ln}`)", ""]
        b += ["> [!abstract] Resumo",
              f"> {'Tabela customizada' if t['custom'] else 'Tabela nativa customizada'} · propriedade **{t.get('ownership')}** · "
              f"{'**' + format(t['record_count'], ',').replace(',', '.') + '** registros' if t.get('record_count') is not None else 'registros não medidos'} · "
              f"**{len(custom_cols)}** colunas customizadas de {t.get('column_count')} · entity set `{t.get('entityset')}`", ""]
        b += ["## Colunas customizadas", "",
              mdtable(["Coluna", "Nome de exibição", "Tipo", "Obrigatoriedade", "Auditoria", "Descrição"],
                      [[f"`{c['logical']}`", c.get("display"), c.get("type"), c.get("required"), c.get("audit"),
                        (c.get("description") or "")[:120]] for c in custom_cols])]
        if t.get("keys"):
            b += ["## Chaves alternativas", "", mdtable(["Chave", "Colunas"], [[k["name"], ", ".join(k["columns"] or [])] for k in t["keys"]])]
        rr = rels_by_table.get(ln, [])
        if rr:
            def other(r):
                o = r["to"] if r["from"] == ln else r["from"]
                return v.link("table", o, o, in_table=True)
            b += ["## Relacionamentos", "",
                  mdtable(["Tipo", "Relacionamento", "Com", "Direção", "Lookup / Intersect", "Delete em cascata"],
                          [[r["kind"], f"`{r['schema']}`", other(r),
                            ("pai" if r["from"] == ln else "filho") if r["kind"] == "1:N" else "N:N",
                            r.get("lookup") or r.get("intersect"), r.get("cascade_delete")] for r in rr])]
        ff = forms_by_table.get(ln, [])
        if ff:
            b += ["## Formulários", "",
                  mdtable(["Formulário", "Tipo", "Ativo", "Gerenciado", "Bibliotecas JS"],
                          [[f["name"], f["type"], f["active"], f["managed"],
                            ", ".join(v.link("webresource", lib, lib, in_table=True) for lib in f.get("libraries") or [])]
                           for f in ff])]
            hs = [(f, h) for f in ff for h in f.get("handlers") or []]
            if hs:
                b += ["### Eventos de formulário", "",
                      mdtable(["Formulário", "Evento", "Campo", "Biblioteca", "Função", "Ativo"],
                              [[f["name"], h["event"], h.get("field"), v.link("webresource", h.get("library"), h.get("library"), in_table=True),
                                f"`{h.get('function')}`", h.get("enabled")] for f, h in hs])]
        vv = views_by_table.get(ln, [])
        if vv:
            b += [f"> [!example]- Views ({len(vv)})", "> " + mdtable(["View", "Tipo", "Padrão", "Ativa", "Gerenciada"],
                  [[x["name"], x["querytype"], x["default"], x["active"], x["managed"]] for x in vv]).replace("\n", "\n> "), ""]
        if st or pr or fl or apis_by_table.get(ln):
            b += ["## Automação", ""]
        if st:
            b += ["### Plugin steps", "", mdtable(["Step", "Mensagem", "Estágio", "Modo", "Ordem", "Ativo", "Filtering"],
                  [[v.link("step", s["id"], s["name"], in_table=True), s["message"], s["stage"], s["mode"], s["rank"],
                    s["enabled"], s.get("filtering")] for s in sorted(st, key=lambda s: (s["message"] or "", str(s["stage"]), s["rank"] or 0))])]
        if pr:
            b += ["### Processos com esta tabela como primária", "",
                  mdtable(["Processo", "Categoria", "Ativo", "Gatilho / modo", "Proprietário"],
                          [[v.link("process", p["id"], p["name"], in_table=True), p["category"], p["active"],
                            p.get("trigger") or p.get("mode"), p.get("owner")] for p in pr])]
        if fl:
            b += ["### Flows que leem/gravam esta tabela", "",
                  "\n".join(f"- {v.link('process', p['id'], p['name'])} ({p['category']})" for p in fl), ""]
        if apis_by_table.get(ln):
            b += ["### Custom APIs vinculadas", "", "\n".join(f"- {v.link('customapi', a['unique'], a['unique'])}" for a in apis_by_table[ln]), ""]
        if ribbons_by_table.get(ln):
            b += ["## Botões customizados (ribbon)", "",
                  mdtable(["Botão", "Rótulo", "Chamadas"],
                          [[f"`{r['button']}`", r.get("label"),
                            "; ".join(f"{c['library']} → {c['function']}" for c in r["calls"])] for r in ribbons_by_table[ln]])]
        if ln in fu_by_table:
            fu = fu_by_table[ln]
            from ..collectors.usage import BUCKETS
            cnt = Counter(f["bucket"] for f in fu["fields"])
            b += ["## Uso das colunas customizadas", "",
                  f"Registros considerados: **{fu.get('total')}** · método: {fu.get('method')}"
                  f"{'' if fu.get('views_measured') else ' · colunas de views não lidas'}"
                  f"{'' if fu.get('repos_measured') else ' · sem repositório de código'}", "",
                  " · ".join(f"**{cnt[k]}** {BUCKETS[k].lower()}" for k in BUCKETS if cnt.get(k)), "",
                  mdtable(["Coluna", "Preenchido", "%", "Forms", "Views", "Processos/flows", "Plugins", "JS", "Repo", "Classificação"],
                          [[f"`{f['logical']}`", f.get("populated"), f.get("pct"), len(f["forms"]) + len(f["form_events"]),
                            len(f["views"]), len(f["processes"]), len(f["plugin_steps"]), len(f["javascript"]),
                            f["repo_files"], f["bucket"]] for f in fu["fields"]])]
        if ln in matrix:
            acts = ["Create", "Read", "Write", "Delete", "Append", "AppendTo", "Assign", "Share"]
            b += ["## Privilégios (papéis analisados)", "",
                  mdtable(["Papel"] + acts, [[v.link("role", rn, rn, in_table=True)] + [p.get(a) for a in acts]
                                             for rn, p in sorted(matrix[ln].items())])]
        b.append(findings_block("table", ln))
        v.write(v.paths[("table", ln)], props, "\n".join(b))

    # ---- 3) plugins ----
    for a in plugins["assemblies"]:
        props = {"tipo": "plugin-assembly", "nome": a["name"], "versao": a.get("version"), "isolamento": a.get("isolation"),
                 "origem": a.get("source"), "gerenciado": a.get("managed"), "tipos": len(a["types"]),
                 "steps": sum(t["steps"] for t in a["types"]), "modificado": a.get("modified"),
                 "solucoes": sol_links(a.get("solutions")), "tags": v.tags("plugin-assembly")}
        steps = [s for s in plugins["steps"] if s.get("assembly") == a["name"]]
        b = [f"# {a['name']}", "", f"Versão **{a.get('version')}** · isolamento {a.get('isolation')} · origem {a.get('source')} · "
             f"public key token `{a.get('publickeytoken')}` · modificado em {a.get('modified')}", ""]
        if a.get("binary"):
            b += [f"> [!tip] Binário salvo em `{a['binary']}` — decompilar com `ilspycmd -p -o <dir> <dll>` "
                  "para comparar com o repositório (drift repo × produção).", ""]
        b += ["## Classes", "", mdtable(["Classe", "Workflow activity", "Steps"],
              [[f"`{t['typename']}`", t["workflow_activity"], t["steps"]] for t in a["types"]])]
        repo_rows = [(rn, t) for ty in a["types"] for rn, t in type_repo.get(ty["typename"], [])]
        if repo_rows:
            b += ["## Repositório × produção", "", mdtable(["Classe", "Repositório", "Status", "Arquivo(s)", "Métodos só em PRD", "Métodos só no repo"],
                  [[f"`{t['type']}`", rn, t["status"], ", ".join(t["repo_files"][:3]), ", ".join(t.get("methods_only_prd") or []),
                    ", ".join(t.get("methods_only_repo") or [])] for rn, t in repo_rows])]
        b += ["## Steps registrados", "", mdtable(["Step", "Tabela", "Mensagem", "Estágio", "Modo", "Ativo"],
              [[v.link("step", s["id"], s["name"], in_table=True), v.link("table", s["entity"], s["entity"], in_table=True),
                s["message"], s["stage"], s["mode"], s["enabled"]] for s in steps])]
        b.append(findings_block("assembly", a["name"]))
        v.write(v.paths[("assembly", a["name"])], props, "\n".join(b))

    for s in plugins["steps"]:
        props = {"tipo": "plugin-step", "nome": s["name"], "assembly": v.link("assembly", s.get("assembly"), s.get("assembly")) if s.get("assembly") else None,
                 "classe": s.get("type"), "handler": s.get("handler_kind"), "mensagem": s["message"],
                 "tabela": v.link("table", s["entity"], s["entity"]) if s.get("entity") else None,
                 "estagio": s["stage"], "modo": s["mode"], "ordem": s["rank"], "ativo": s["enabled"],
                 "filtering": s.get("filtering"), "imagens": len(s.get("images") or []), "gerenciado": s.get("managed"),
                 "impersonando": s.get("impersonating"), "modificado": s.get("modified"),
                 "solucoes": sol_links(s.get("solutions")), "tags": v.tags("plugin-step")}
        b = [f"# {s['name']}", "",
             f"**{s['message']}** em {v.link('table', s['entity'], s['entity']) if s.get('entity') else '(qualquer tabela)'} · "
             f"{s['stage']} · {s['mode']} · ordem {s['rank']} · {'ativo' if s['enabled'] else '**desativado**'}", "",
             f"- Handler: {'`' + str(s.get('type')) + '`' if s.get('type') else s.get('handler')} ({s.get('handler_kind')})",
             f"- Filtering attributes: {('`' + s['filtering'] + '`') if s.get('filtering') else '_nenhum (dispara para qualquer coluna)_'}",
             f"- Configuração unsecure: {'sim' if s.get('has_config') else 'não'}", ""]
        if s.get("images"):
            b += ["## Imagens", "", mdtable(["Nome", "Alias", "Tipo", "Atributos"],
                  [[i["name"], i["alias"], i["type"], i.get("attributes") or "(todas)"] for i in s["images"]])]
        b.append(findings_block("step", s["id"]))
        v.write(v.paths[("step", s["id"])], props, "\n".join(b))

    # ---- 4) processos ----
    for p in procs:
        props = {"tipo": "processo", "nome": p["name"], "categoria": p["category"],
                 "tabela": v.link("table", p["entity"], p["entity"]) if p.get("entity") else None,
                 "ativo": p["active"], "gerenciado": p.get("managed"), "modo": p.get("mode"), "gatilho": p.get("trigger"),
                 "conectores": p.get("connectors") or [], "hosts_http": p.get("http_hosts") or [],
                 "proprietario": p.get("owner"), "proprietario_desativado": p.get("owner_disabled"),
                 "modificado": p.get("modified"), "criado": p.get("created"), "modificado_por": p.get("modified_by"),
                 "solucoes": sol_links(p.get("solutions")), "tags": v.tags("processo")}
        b = [f"# {p['name']}", "", f"**{p['category']}** · {'ativo' if p['active'] else 'inativo'}"
             f"{' · ' + v.link('table', p['entity'], p['entity']) if p.get('entity') else ''} · proprietário {p.get('owner')}"
             f"{' ⚠️ desativado' if p.get('owner_disabled') else ''}", ""]
        if p.get("description"):
            b += [f"> {p['description']}", ""]
        trig = []
        if p.get("on_create"):
            trig.append("Create")
        if p.get("on_update"):
            trig.append(f"Update de `{p['on_update']}`")
        if p.get("on_delete"):
            trig.append("Delete")
        if p.get("on_demand"):
            trig.append("Sob demanda")
        if trig:
            b += [f"- Dispara em: {', '.join(trig)}"]
        if p.get("mode"):
            b += [f"- Modo: {p['mode']}"]
        if p.get("trigger"):
            b += [f"- Gatilho: {p['trigger']} — `{p.get('trigger_detail')}`",
                  f"- Ações: {p.get('actions')} · child flows: {p.get('child_flows')}",
                  f"- Conectores: {', '.join(p.get('connectors') or []) or '—'}",
                  f"- Hosts HTTP: {', '.join(p.get('http_hosts') or []) or '—'}"]
            if p.get("tables"):
                b += ["- Tabelas tocadas: " + ", ".join(v.link("table", t, t) for t in p["tables"])]
            if p.get("action_types"):
                b += [f"- Condições: {p.get('conditions')} · tratamentos de erro (runAfter Failed/TimedOut): "
                      f"{p.get('error_handlers')} · profundidade máx.: {p.get('max_depth')}",
                      "- Tipos de ação: " + ", ".join(f"{k} ({n})" for k, n in p["action_types"].items())]
        if p.get("field_refs"):
            b += ["", f"**Colunas citadas ({len(p['field_refs'])}):** " + ", ".join(f"`{f}`" for f in p["field_refs"])]
        if p.get("outline"):
            b += ["", f"> [!example]- Estrutura do flow ({p.get('actions')} ações)"] + [f"> {line}" for line in p["outline"]]
        b.append("")
        b.append(findings_block("process", p["id"]))
        v.write(v.paths[("process", p["id"])], props, "\n".join(b))

    # ---- 5) web resources ----
    fence = {"JScript": "js", "HTML": "html", "CSS": "css", "XML": "xml", "SVG": "xml", "XSL": "xml", "RESX": "xml", "TS": "ts"}
    for w in wrs:
        used = wr_usage.get(w["name"].lower(), [])
        props = {"tipo": "webresource", "nome": w["name"], "tipo_arquivo": w["type"], "gerenciado": w.get("managed"),
                 "tamanho_bytes": w.get("size"), "funcoes": len(w.get("functions") or []), "formularios": len(used),
                 "segredos": len(w.get("secret_hits") or []), "modificado": w.get("modified"),
                 "solucoes": sol_links(w.get("solutions")), "tags": v.tags("webresource")}
        b = [f"# {w['name']}", "", f"{w['type']} · {w.get('display') or ''} · modificado em {w.get('modified')}", ""]
        if w.get("secret_hits"):
            b += ["> [!danger] Possível segredo em texto claro",
                  "> " + "; ".join(f"linha {h['line']}: {h['kind']}" for h in w["secret_hits"]) +
                  " — valor redigido neste vault; **rotacionar na origem**.", ""]
        if used:
            b += ["## Usado em formulários", "", "\n".join(sorted({f"- {v.link('table', f['entity'], f['entity'])} — {f['name']}" for f in used})), ""]
        js = w.get("js")
        if js:
            flags = [("API obsoleta Xrm.Page", js.get("xrm_page")), ("eval()", js.get("eval")),
                     ("endpoint SOAP/OData 2011", js.get("odata_2011")), ("XHR síncrono", js.get("sync_xhr")),
                     ("XrmServiceToolkit", js.get("xrm_service_toolkit")), ("jQuery", js.get("jquery")),
                     ("HTTP direto (fetch/ajax/XHR)", js.get("raw_http")), ("formContext (moderno)", js.get("form_context"))]
            b += ["## Análise estática", "",
                  f"{js.get('lines')} linhas{' · **biblioteca de terceiros**' if js.get('third_party') else ''} · "
                  f"console.*: {js.get('console_calls')} · TODO/FIXME: {js.get('todo_count')}", "",
                  "- Usa: " + (", ".join(n for n, on in flags if on) or "—"),
                  f"- Xrm.WebApi: {', '.join(js.get('webapi_methods') or []) or '—'}",
                  f"- Hosts externos: {', '.join(js.get('external_hosts') or []) or '—'}",
                  f"- Colunas lidas (getAttribute): {', '.join(f'`{a}`' for a in js.get('attributes_read') or []) or '—'}", ""]
        for rn, rw in wr_repo.get(w["name"], []):
            callout = "success" if rw["status"] == "idêntico" else "warning"
            b += [f"> [!{callout}] Repositório {rn}: {rw['status']}",
                  f"> Arquivo: `{rw.get('file', '—')}` · casado por {rw.get('match', '—')}"
                  + (f" · similaridade {rw['similarity']:.0%}" if rw.get("similarity") is not None else "")]
            if rw.get("functions_only_env"):
                b += [f"> Funções só no ambiente: {', '.join(rw['functions_only_env'])}"]
            if rw.get("functions_only_repo"):
                b += [f"> Funções só no repo: {', '.join(rw['functions_only_repo'])}"]
            b.append("")
        if w.get("functions"):
            b += [f"## Funções ({len(w['functions'])})", "", ", ".join(f"`{x}`" for x in w["functions"]), ""]
        if w.get("content"):
            content = w["content"]
            if len(content) > 300_000:
                content = content[:300_000] + "\n/* … truncado pelo dvinv (300 KB) … */"
            b += ["## Código-fonte (redigido)", "", f"```{fence.get(w['type'], '')}", content.replace("```", "``\u200b`"), "```", ""]
        b.append(findings_block("webresource", w["name"]))
        v.write(v.paths[("webresource", w["name"])], props, "\n".join(b))

    # ---- 6) custom APIs, apps, soluções, papéis ----
    for a in apis:
        props = {"tipo": "customapi", "nome": a["unique"], "binding": a.get("binding"),
                 "tabela": v.link("table", a["bound_entity"], a["bound_entity"]) if a.get("bound_entity") else None,
                 "funcao": a.get("is_function"), "privada": a.get("private"), "plugin": a.get("plugin_type"),
                 "gerenciado": a.get("managed"), "solucoes": sol_links(a.get("solutions")), "tags": v.tags("customapi")}
        b = [f"# {a['unique']}", "", a.get("description") or "", "",
             f"- Binding: {a.get('binding')} {('→ ' + v.link('table', a['bound_entity'], a['bound_entity'])) if a.get('bound_entity') else ''}",
             f"- {'Function (GET)' if a.get('is_function') else 'Action (POST)'} · privada: {a.get('private')} · steps permitidos: {a.get('steps_allowed')}",
             f"- Implementação: `{a.get('plugin_type') or '—'}`", "",
             "## Parâmetros de entrada", "", mdtable(["Nome", "Tipo", "Opcional"], [[p["name"], p["type"], p["optional"]] for p in a["request"]]),
             "## Propriedades de resposta", "", mdtable(["Nome", "Tipo"], [[p["name"], p["type"]] for p in a["response"]]),
             findings_block("customapi", a["unique"])]
        v.write(v.paths[("customapi", a["unique"])], props, "\n".join(b))

    kinds = {"modeldriven": "App model-driven", "canvas": "Canvas app / custom page", "bots": "Agente Copilot Studio"}
    for k, label_ in kinds.items():
        for a in apps.get(k) or []:
            props = {"tipo": "app", "subtipo": label_, "nome": a["name"], "nome_unico": a.get("unique"),
                     "gerenciado": a.get("managed"), "modificado": a.get("modified") or a.get("published"),
                     "solucoes": sol_links(a.get("solutions")), "tags": v.tags("app")}
            body = [f"# {a['name']}", "", f"{label_} · `{a.get('unique')}`", "", a.get("description") or ""]
            body.append(findings_block("app", a["id"]))
            v.write(v.paths[("app", a["id"])], props, "\n".join(body))

    for s in sols:
        if ("solution", s["uniquename"]) not in v.paths:
            continue
        cc = s.get("component_counts") or {}
        props = {"tipo": "solucao", "nome": s["uniquename"], "nome_exibicao": s.get("name"), "versao": s.get("version"),
                 "gerenciada": s.get("managed"), "publisher": s.get("publisher"), "prefixo": s.get("publisher_prefix"),
                 "componentes": s.get("component_total"), "instalada": s.get("installed"), "modificada": s.get("modified"),
                 "tags": v.tags("solucao")}
        b = [f"# {s.get('name')} (`{s['uniquename']}`)", "", f"v{s.get('version')} · publisher {s.get('publisher')} "
             f"(`{s.get('publisher_prefix')}`) · {'gerenciada' if s.get('managed') else 'não gerenciada'}", "",
             s.get("description") or "", "", "## Componentes por tipo", "", mdtable(["Tipo", "Qtd"], list(cc.items()))]
        v.write(v.paths[("solution", s["uniquename"])], props, "\n".join(b))

    for r in sec.get("roles") or []:
        if ("role", r["name"]) not in v.paths:
            continue
        mine = {t: p[r["name"]] for t, p in matrix.items() if r["name"] in p}
        props = {"tipo": "papel", "nome": r["name"], "gerenciado": r.get("managed"),
                 "tabelas_com_privilegio": len(mine) if matrix else None,
                 "solucoes": sol_links(r.get("solutions")), "tags": v.tags("papel")}
        acts = ["Create", "Read", "Write", "Delete", "Append", "AppendTo", "Assign", "Share"]
        b = [f"# {r['name']}", ""]
        if matrix:
            b += ["## Privilégios em tabelas do escopo", "",
                  mdtable(["Tabela"] + acts, [[v.link("table", t, t, in_table=True)] + [p.get(a) for a in acts] for t, p in sorted(mine.items())])]
        else:
            b += ["> [!info] Privilégios não coletados — rode com `deep.role_privileges: true`.", ""]
        b.append(findings_block("role", r["name"]))
        v.write(v.paths[("role", r["name"])], props, "\n".join(b))

    # ---- 7) achados ----
    for f in fnd:
        props = {"tipo": "achado", "id_achado": f["id"], "severidade": f["severity"], "titulo": f["title"],
                 "metrica": f.get("metric"), "status": "aberto", "responsavel": None, "tags": v.tags("achado")}
        b = [f"# {f['id']} — {f['title']}", "", f"> [!{SEV_CALLOUT.get(f['severity'], 'note')}] Severidade: {f['severity']}",
             f"> {f['detail']}", ""]
        refs = [r for r in f["refs"] if tuple(r) in v.paths]
        if refs:
            uniq = list(dict.fromkeys((k, key) for k, key in refs))
            b += [f"## Componentes afetados ({len(uniq)})", "", "\n".join(f"- {v.link(k, key)}" for k, key in uniq[:500]), ""]
        if f.get("evidence"):
            b += ["## Evidências", "", "\n".join(f"- {e}" for e in f["evidence"][:500]), ""]
        b += ["Fonte: `_raw/findings.json` (regra em `dvinv/findings.py`). Status e responsável editáveis — preservados entre extrações."]
        v.write(v.paths[("finding", f["id"])], props, "\n".join(b))

    # ---- 8) notas de seção ----
    manifest = d["manifest"]
    write_integrations(v, d, procs, apis)
    write_field_usage(v, d)
    write_storage(v, d)
    write_repos(v, d)
    write_dependencies(v, d, graph, dsum, node_link)
    write_security(v, sec)
    write_environment(v, d, manifest)
    bases.write_all(v, fnd)
    canvas.write_map(v, d)
    write_index(v, d, manifest, fnd)
    return v


def write_integrations(v, d, procs, apis):
    alm = d.get("alm") or {}
    flows = [p for p in procs if p["category"] == "Cloud Flow"]
    conn = Counter(c for p in flows for c in p.get("connectors") or [])
    hosts = defaultdict(list)
    for p in flows:
        for h in p.get("http_hosts") or []:
            hosts[h].append(p)
    b = ["# Integrações", "", "Mapa de tudo que entra ou sai do Dataverse: conectores usados por flows, chamadas HTTP, "
         "service endpoints, Custom APIs, variáveis de ambiente e referências de conexão.", ""]
    b += ["## Conectores usados por cloud flows", "", mdtable(["Conector", "Flows"], conn.most_common())]
    b += ["## Hosts HTTP chamados por flows", "", mdtable(["Host", "Flows"],
          [[h, ", ".join(v.link("process", p["id"], p["name"], in_table=True) for p in ps)] for h, ps in sorted(hosts.items())])]
    b += ["## Service endpoints", "", mdtable(["Nome", "Contrato", "Auth", "URL (redigida)"],
          [[e["name"], e.get("contract"), e.get("auth"), e.get("url")] for e in d.get("serviceendpoints") or []])]
    b += ["## Custom APIs", "", mdtable(["API", "Binding", "Tipo", "Plugin"],
          [[v.link("customapi", a["unique"], a["unique"], in_table=True), a.get("bound_entity") or a.get("binding"),
            "Function" if a.get("is_function") else "Action", a.get("plugin_type")] for a in apis])]
    b += ["## Variáveis de ambiente", "", mdtable(["Variável", "Tipo", "Valor atual (redigido)", "Default", "Tem valor"],
          [[f"`{e['name']}`", e.get("type"), (e.get("value") or "")[:80], (e.get("default") or "")[:80], e["has_value"]]
           for e in alm.get("envvars") or []])]
    b += ["## Referências de conexão", "", mdtable(["Referência", "Conector", "Conectada", "Ativa"],
          [[f"`{r['name']}`", r["connector"], r["connected"], r["active"]] for r in alm.get("connrefs") or []])]
    b += ["## Conectores customizados", "", mdtable(["Conector", "Tipo", "Gerenciado"],
          [[k["name"], k.get("kind"), k.get("managed")] for k in alm.get("connectors") or []])]
    v.write(f"{v.folder}/03 Integrações", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))


def write_field_usage(v, d):
    fu = d.get("field_usage")
    if not fu:
        return
    from ..collectors.usage import BUCKETS
    total = Counter(f["bucket"] for t in fu for f in t["fields"])
    b = ["# Uso de campos", "",
         "Preenchimento real × onde cada coluna customizada é usada. **Candidato seguro** = zero registros "
         "preenchidos e nenhuma referência encontrada; ainda assim confirme integrações externas (ETL, Power BI, portais) antes de remover.", "",
         mdtable(["Classificação", "Colunas"], [[BUCKETS[k], total.get(k, 0)] for k in BUCKETS]),
         "## Por tabela", "",
         mdtable(["Tabela", "Registros", "Método"] + list(BUCKETS),
                 [[v.link("table", t["table"], t["table"]), t.get("total"), t.get("method")] +
                  [sum(1 for f in t["fields"] if f["bucket"] == k) for k in BUCKETS] for t in fu])]
    safe = [(t, f) for t in fu for f in t["fields"] if f["bucket"] == "candidato-seguro"]
    b += ["## Candidatos seguros a remoção", "", mdtable(["Tabela", "Coluna", "Nome", "Tipo"],
          [[v.link("table", t["table"], t["table"]), f"`{f['logical']}`", f.get("display"), f.get("type")] for t, f in safe])]
    v.write(f"{v.folder}/05 Uso de Campos", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))


def write_storage(v, d):
    st = d.get("storage")
    if not st:
        return
    gb = lambda x: f"{(x or 0) / 1e9:,.2f} GB".replace(",", "X").replace(".", ",").replace("X", ".")  # noqa: E731
    b = ["# Armazenamento e auditoria", "",
         "> [!info] Números lidos pela Web API (contagens e somas de `filesize`). O consumo oficial de capacidade "
         "(Database/File/Log contratado × usado) só existe no Power Platform Admin Center.", "",
         "## Maiores tabelas (registros)", "",
         mdtable(["Tabela", "Registros"], [[v.link("table", r["table"], r["table"]), r["records"]] for r in st.get("largest_tables") or []])]
    for key in ("annotations", "email_attachments"):
        sec = st.get(key) or {}
        b += [f"## {sec.get('label', key)}", "", f"**{sec.get('count')}** arquivos · **{gb(sec.get('bytes'))}** "
              f"({sec.get('bytes_method', '—')})", "",
              mdtable(["Tabela", "Quantidade", "Bytes"], [[v.link("table", r["table"], r["table"]), r.get("count"),
                                                         gb(r["bytes"]) if r.get("bytes") is not None else "—"]
                                                        for r in (sec.get("by_table") or [])[:25]]),
              mdtable(["Tipo de arquivo", "Quantidade", "Bytes"], [[m["mimetype"], m.get("count"), gb(m.get("bytes"))]
                                                                 for m in sec.get("by_mimetype") or []])]
    aud = st.get("audit") or {}
    b += ["## Auditoria", "", f"**{aud.get('total')}** registros · mais antigo: **{aud.get('oldest')}**", "",
          mdtable(["Tabela", "Registros"], [[v.link("table", r["table"], r["table"]), r["count"]] for r in (aud.get("by_table") or [])[:30]]),
          mdtable(["Ação", "Registros"], [[r["action"], r["count"]] for r in aud.get("by_action") or []])]
    v.write(f"{v.folder}/06 Armazenamento e Auditoria", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))


def write_repos(v, d):
    repos = d.get("repos")
    if not repos:
        return
    b = ["# Repositórios × ambiente", ""]
    for r in repos:
        wrs = r.get("webresources") or []
        tys = r.get("plugin_types") or []
        git = (f"branch **{r['branch']}** · último commit `{r.get('commit')}`" if r.get("branch")
               else "não é um clone Git (sem branch/commit)")
        b += [f"## {r['name']}", "", f"`{r['path']}` · {git} · {r['files']} arquivos de texto lidos", ""]
        if wrs:
            cnt = Counter(w["status"] for w in wrs)
            b += ["### Web resources", "", " · ".join(f"**{n}** {k}" for k, n in cnt.items()), "",
                  mdtable(["Web resource", "Status", "Arquivo", "Similaridade", "Só no ambiente", "Só no repo"],
                          [[v.link("webresource", w["name"], w["name"]), w["status"], w.get("file"),
                            f"{w['similarity']:.0%}" if w.get("similarity") is not None else None,
                            ", ".join(w.get("functions_only_env") or []), ", ".join(w.get("functions_only_repo") or [])] for w in wrs])]
        if tys:
            cnt = Counter(t["status"] for t in tys)
            b += ["### Classes de plugin", "", " · ".join(f"**{n}** {k}" for k, n in cnt.items()), "",
                  mdtable(["Classe", "Assembly", "Status", "Arquivo(s)", "Métodos só em PRD", "Métodos só no repo"],
                          [[f"`{t['type']}`", v.link("assembly", t["assembly"], t["assembly"]), t["status"],
                            ", ".join(t["repo_files"][:3]), ", ".join(t.get("methods_only_prd") or []),
                            ", ".join(t.get("methods_only_repo") or [])] for t in tys])]
        if r.get("secret_hits"):
            b += ["> [!danger] Possíveis segredos versionados (valores não reproduzidos aqui)"] + \
                 [f"> - `{h['file']}` linha {h['line']}: {h['kind']}" for h in r["secret_hits"]] + [""]
    v.write(f"{v.folder}/07 Repositórios", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))


def write_dependencies(v, d, g, dsum, node_link):
    kinds = deps_mod.KIND_LABEL
    n_inf = sum(1 for e in g.edges.values() if "inferida" in e["source"])
    n_plat = sum(1 for e in g.edges.values() if "plataforma" in e["source"])
    plat_on = bool(d.get("platform_dependencies"))
    b = ["# Matriz de dependências", "",
         "**A → B** significa *A depende de B*: se B mudar ou for removido, A é afetado. Use a matriz por tabela "
         "para dimensionar impacto de mudança e as listas de órfãos para limpeza.", "",
         f"**{len(g.nodes)}** componentes · **{len(g.edges)}** dependências ({n_inf} inferidas pelo kit, "
         f"{n_plat} registradas pela plataforma) · exportação: [[{v.folder}/Matriz de Dependências.xlsx|Excel]] · "
         f"[[{v.folder}/Dependências.csv|CSV]]", ""]
    if not plat_on:
        b += ["> [!info] Dependências da plataforma não coletadas",
              "> Ligue `deep.platform_dependencies` para incluir o que o Dataverse registra (ex. view → coluna, "
              "sitemap → tabela) e as dependências ausentes de cada solução.", ""]
    cols = deps_mod.IMPACT_COLS
    b += ["## Impacto por tabela (quem depende de cada tabela)", "",
          mdtable(["Tabela"] + [c for _, c in cols] + ["Total", "Depende de"],
                  [[v.link("table", r["table"], r["table"])] + [r[k] or "" for k, _ in cols] + [r["total"], r["depends_on"]]
                   for r in dsum["per_table"]])]
    tt = dsum["type_x_type"]
    src_k = sorted({a for a, _ in tt}, key=lambda k: list(kinds).index(k) if k in kinds else 99)
    dst_k = sorted({b_ for _, b_ in tt}, key=lambda k: list(kinds).index(k) if k in kinds else 99)
    b += ["## Tipo × tipo", "", "Linhas dependem das colunas.", "",
          mdtable(["Depende ↓ / de →"] + [kinds.get(k, k) for k in dst_k],
                  [[kinds.get(a, a)] + [tt.get((a, c), "") for c in dst_k] for a in src_k])]
    top_in = [n for n, c in dsum["fan_in"].most_common(25) if c]
    top_out = [n for n, c in dsum["fan_out"].most_common(25) if c]
    b += ["## Mais dependidos (maior impacto de mudança)", "",
          mdtable(["Componente", "Tipo", "Dependentes"], [[node_link(n), kinds.get(n[0], n[0]), dsum["fan_in"][n]] for n in top_in]),
          "## Que mais dependem de outros (mais frágeis)", "",
          mdtable(["Componente", "Tipo", "Dependências"], [[node_link(n), kinds.get(n[0], n[0]), dsum["fan_out"][n]] for n in top_out])]
    if dsum["external"]:
        b += ["## Dependências externas", "",
              mdtable(["Destino", "Tipo", "Usado por"],
                      [[g.nodes[n]["label"], kinds.get(n[0], n[0]), ", ".join(node_link(s) for s, _, _ in g.into(n)[:20])]
                       for n, _ in dsum["external"]])]
    if dsum["unused"]:
        b += ["## Sem nenhum dependente encontrado", "",
              "Candidatos a limpeza — confirme chamadas de fora do Dataverse (integrações, apps externos, Power BI).", "",
              mdtable(["Componente", "Tipo"], [[node_link(n), kinds.get(n[0], n[0])] for n in sorted(dsum["unused"])])]
    if dsum["missing"]:
        b += ["## Dependências ausentes nas soluções", "",
              "Componentes exigidos que **não estão na solução** — a importação falha em ambiente que não os tenha.", "",
              mdtable(["Solução", "Componente que exige", "Tipo", "Componente exigido", "Tipo"],
                      [[m["solution"], m["dependent_id"], m.get("dependent_type_label") or m.get("dependent_type"),
                        m["required_id"], m.get("required_type_label") or m.get("required_type")] for m in dsum["missing"]])]
    v.write(f"{v.folder}/08 Matriz de Dependências", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))

    # ---- exportações: Excel e CSV ----
    rows_edges = [["Componente", "Tipo", "Depende de", "Tipo (destino)", "Relação", "Origem"]] + sorted(
        [[g.nodes[s]["label"], kinds.get(g.nodes[s]["kind"]), g.nodes[t]["label"], kinds.get(g.nodes[t]["kind"]), r, e["source"]]
         for (s, t, r), e in g.edges.items()], key=lambda x: (x[1] or "", x[0], x[2]))
    rows_tables = [["Tabela"] + [c for _, c in cols] + ["Total dependentes", "Depende de"]] + [
        [r["table"]] + [r[k] for k, _ in cols] + [r["total"], r["depends_on"]] for r in dsum["per_table"]]
    rows_tt = [["Depende ↓ / de →"] + [kinds.get(k, k) for k in dst_k]] + [
        [kinds.get(a, a)] + [tt.get((a, c), 0) for c in dst_k] for a in src_k]
    rows_nodes = [["Componente", "Tipo", "Dependentes (fan-in)", "Dependências (fan-out)"]] + sorted(
        [[nd["label"], kinds.get(nd["kind"]), dsum["fan_in"][k], dsum["fan_out"][k]] for k, nd in g.nodes.items()],
        key=lambda x: (-x[2], x[0]))
    sheets = [("Matriz por tabela", rows_tables), ("Arestas", rows_edges), ("Tipo x tipo", rows_tt), ("Componentes", rows_nodes)]
    if dsum["missing"]:
        sheets.append(("Ausentes na solução", [["Solução", "Exige (id)", "Tipo", "Exigido (id)", "Tipo"]] + [
            [m["solution"], m["dependent_id"], m.get("dependent_type_label"), m["required_id"], m.get("required_type_label")]
            for m in dsum["missing"]]))
    path = v.root / v.folder / "Matriz de Dependências.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    xlsx.write(path, sheets)
    v.written.append(path)
    import csv
    import io
    buf = io.StringIO()
    csv.writer(buf, delimiter=";").writerows(rows_edges)
    v.write(f"{v.folder}/Dependências.csv", None, "\ufeff" + buf.getvalue(), raw=True)


def write_security(v, sec):
    bus = sec.get("business_units") or []
    children = defaultdict(list)
    for b_ in bus:
        children[b_.get("parent_id")].append(b_)

    def tree(pid, depth):
        lines = []
        for b_ in sorted(children.get(pid, []), key=lambda x: x["name"]):
            lines.append("  " * depth + f"- {b_['name']}{' (desativada)' if b_.get('disabled') else ''}")
            if depth < 12:
                lines += tree(b_["id"], depth + 1)
        return lines

    u = sec.get("users") or {}
    roles = sec.get("roles") or []
    b = ["# Segurança", "",
         f"**{len(bus)}** business units · **{len(roles)}** papéis (cópia da BU raiz) · **{len(sec.get('teams') or [])}** equipes · "
         f"**{u.get('active', '?')}** usuários ativos ({u.get('active_human', '?')} humanos, {u.get('application', '?')} de aplicação)", ""]
    b += ["## Árvore de business units", ""] + tree(None, 0) + [""]
    b += ["## Papéis", "", mdtable(["Papel", "Gerenciado", "No escopo"],
          [[v.link("role", r["name"], r["name"], in_table=True), r.get("managed"), r.get("scope_reason")] for r in roles])]
    b += ["## Equipes", "", mdtable(["Equipe", "Tipo", "BU", "Grupo Entra ID", "Membros"],
          [[t["name"], t.get("type"), t.get("bu"), t.get("aad_group"), t.get("members")] for t in sec.get("teams") or []])]
    b += ["## Perfis de segurança de campo", "", mdtable(["Perfil", "Gerenciado"],
          [[f["name"], f.get("managed")] for f in sec.get("field_security_profiles") or []])]
    v.write(f"{v.folder}/04 Segurança", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))


def write_environment(v, d, manifest):
    env = d.get("environment") or {}
    b = ["# Ambiente", "", mdtable(["Item", "Valor"], [
        ["URL", env.get("url")], ["Organização", env.get("org_name")], ["Versão", env.get("version")],
        ["Organization ID", env.get("organizationid")], ["Criada em", env.get("created")],
        ["Auditoria", env.get("audit_enabled")], ["Plugin trace", env.get("plugin_trace_setting")],
        ["Extraído em (UTC)", manifest.get("extracted_at")], ["Chamadas à API", len(manifest.get("queries") or [])],
    ])]
    sc = manifest.get("scope") or {}
    b += ["## Critério de escopo", "", f"- Prefixos: {', '.join(f'`{p}`' for p in sc.get('prefixes') or []) or '—'}",
          f"- Palavras-chave: {', '.join(f'`{k}`' for k in sc.get('keywords') or []) or '—'}",
          f"- Soluções: {', '.join(sc.get('solutions') or []) or '—'}",
          f"- Inclui não gerenciados: {sc.get('include_unmanaged')} · tudo: {sc.get('all')}", "",
          "## Escopo × organização", "",
          mdtable(["Coletor", "Métricas"], [[k, ", ".join(f"{a}={b_}" for a, b_ in (s or {}).items())]
                                            for k, s in (manifest.get("stats") or {}).items()])]
    gaps = manifest.get("gaps") or []
    b += ["## Lacunas declaradas", "",
          "> [!warning] O que **não** foi coletado nesta extração\n> Números deste vault só valem para o que foi efetivamente lido.", "",
          mdtable(["Coletor", "O quê", "Motivo"], [[g["collector"], g["what"], g["error"].splitlines()[0][:200]] for g in gaps])]
    b += ["## Soluções", "", mdtable(["Solução", "Versão", "Gerenciada", "Publisher", "Componentes"],
          [[v.link("solution", s["uniquename"], s["uniquename"], in_table=True), s.get("version"), s.get("managed"),
            s.get("publisher"), s.get("component_total")] for s in d.get("solutions") or []])]
    b += ["## Coleta (deep)", "", mdtable(["Opção", "Ligada"], list((manifest.get("deep") or {}).items())),
          "## Tempo por coletor (s)", "", mdtable(["Coletor", "s"], list((manifest.get("timings_s") or {}).items()))]
    v.write(f"{v.folder}/01 Ambiente", {"tipo": "secao", "url": env.get("url"), "versao": env.get("version"),
                                        "tags": v.tags("secao")}, "\n".join(b))


def write_index(v, d, manifest, fnd):
    st = manifest.get("stats") or {}
    procs = d.get("processes") or []
    plugins = d.get("plugins") or {}
    apps = d.get("apps") or {}
    by_cat = Counter(p["category"] for p in procs)
    sev = Counter(f["severity"] for f in fnd)
    rows = [
        ["Tabelas", f"{st.get('tables', {}).get('scope', 0)} ({st.get('tables', {}).get('custom', 0)} custom + "
                    f"{st.get('tables', {}).get('native_customized', 0)} nativas customizadas)", st.get("tables", {}).get("org_total")],
        ["Colunas customizadas", st.get("tables", {}).get("columns_custom"), None],
        ["Relacionamentos", st.get("relationships", {}).get("scope"), None],
        ["Formulários / views", f"{st.get('forms', {}).get('scope')} / {st.get('views', {}).get('scope')}", None],
        ["Web resources", st.get("webresources", {}).get("scope"), st.get("webresources", {}).get("org_total")],
        ["Plugin assemblies / classes / steps", f"{len(plugins.get('assemblies') or [])} / "
         f"{sum(len(a['types']) for a in plugins.get('assemblies') or [])} / {len(plugins.get('steps') or [])}",
         st.get("plugins", {}).get("org_assemblies")],
        ["Custom APIs", st.get("customapis", {}).get("scope"), st.get("customapis", {}).get("org_total")],
    ] + [[f"Processos — {k}", n, None] for k, n in sorted(by_cat.items())] + [
        ["Apps model-driven / canvas / agentes", f"{len(apps.get('modeldriven') or [])} / {len(apps.get('canvas') or [])} / "
         f"{len(apps.get('bots') or [])}", None],
        ["Papéis no escopo (BU raiz)", st.get("security", {}).get("roles_scope"), st.get("security", {}).get("roles_root_bu")],
        ["Usuários ativos", st.get("security", {}).get("users_active"), None],
    ]
    env = d.get("environment") or {}
    b = [f"# Inventário Dataverse — {v.env}", "",
         f"`{env.get('url')}` · versão {env.get('version')} · extraído em {manifest.get('extracted_at')} · "
         f"{len(manifest.get('gaps') or [])} lacuna(s) declarada(s) em {v.link_path('01 Ambiente', 'Ambiente')}", "",
         "> [!summary] Achados automáticos",
         "> " + " · ".join(f"**{n}** {s}" for s, n in sorted(sev.items(), key=lambda x: findings_mod.SEV_ORDER.get(x[0], 9))) +
         f" — ver {v.link_path('Bases/Achados.base', 'Achados')}" if fnd else "> Nenhum achado automático.", "",
         "## Números", "", mdtable(["Componente", "No escopo", "Total na org"], rows),
         "## Navegação", "",
         f"- {v.link_path('01 Ambiente', 'Ambiente, escopo e lacunas')}",
         f"- {v.link_path('03 Integrações', 'Integrações')}",
         f"- {v.link_path('04 Segurança', 'Segurança')}",
         *([f"- {v.link_path('05 Uso de Campos', 'Uso de campos')}"] if d.get("field_usage") else []),
         *([f"- {v.link_path('06 Armazenamento e Auditoria', 'Armazenamento e auditoria')}"] if d.get("storage") else []),
         *([f"- {v.link_path('07 Repositórios', 'Repositórios × ambiente')}"] if d.get("repos") else []),
         f"- {v.link_path('08 Matriz de Dependências', 'Matriz de dependências')} "
         f"([[{v.folder}/Matriz de Dependências.xlsx|Excel]])",
         f"- {v.link_path('Mapa do Ambiente.canvas', 'Mapa do ambiente (canvas)')}", "",
         "## Achados", "", f"![[{v.folder}/Bases/Achados.base]]", "",
         "## Tabelas", "", f"![[{v.folder}/Bases/Tabelas.base]]", "",
         "## Automação", "", f"![[{v.folder}/Bases/Automações.base]]", ""]
    v.write(f"{v.folder}/00 Índice", {"tipo": "indice", "tags": v.tags("indice")}, "\n".join(b))
