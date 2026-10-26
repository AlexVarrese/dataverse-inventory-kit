"""Grafo e matriz de dependências entre componentes.

Aresta = "A depende de B" (se B mudar ou sumir, A é afetado). Duas origens:

- inferida  — derivada do que o kit coletou: step → tabela/assembly, processo → tabela, flow →
  conexão/variável/Custom API/child flow/host HTTP, formulário → biblioteca JS, botão → JS, JS →
  tabela/Custom API/host, Custom API → tabela/plugin, app → tabela, papel → tabela, tabela filha →
  tabela pai (lookup).
- plataforma — tabela `dependency` (deep.platform_dependencies): o que o Dataverse registra e usa
  para bloquear exclusão. No grafo entram as arestas que tocam o escopo (nos dois sentidos), sem as internas
  de metadados; o conjunto completo fica no snapshot.

Saídas: _derived/<run_id>/dependencies.json (render; o diff recalcula), matriz por tabela, matriz tipo × tipo, fan-in/fan-out, órfãos.
"""

import re
import time
from collections import Counter, defaultdict

from .util import field_refs, field_tokens

QUOTED_RE = re.compile(r"[\"'`]([A-Za-z][A-Za-z0-9_]{2,})[\"'`]")

KIND_LABEL = {
    "table": "Tabela", "column": "Coluna", "form": "Formulário", "view": "View", "webresource": "Web resource",
    "step": "Plugin step", "assembly": "Plugin assembly", "plugintype": "Classe de plugin",
    "process": "Processo", "customapi": "Custom API", "app": "App", "role": "Papel", "envvar": "Variável de ambiente",
    "connref": "Referência de conexão", "endpoint": "Service endpoint", "connector": "Conector (externo)",
    "host": "Host HTTP (externo)", "optionset": "Option set", "platform": "Outro (plataforma)",
    "pcf": "Componente PCF", "team": "Equipe", "bu": "Business unit",
    "dataprovider": "Provedor de dados virtual", "sitemap": "Site map", "sla": "SLA", "command": "Comando",
}
# Processos são subdivididos por categoria na matriz por tabela.
PROC_KIND = {"Workflow": "workflow", "Dialog": "workflow", "Business Rule": "businessrule", "Action": "action",
             "Business Process Flow": "bpf", "Cloud Flow": "flow", "Desktop Flow": "flow"}
IMPACT_COLS = [("form", "Formulários"), ("webresource", "JS/web resources"), ("step", "Plugin steps"),
               ("businessrule", "Business rules"), ("workflow", "Workflows"), ("action", "Actions"), ("bpf", "BPFs"),
               ("flow", "Cloud flows"), ("customapi", "Custom APIs"), ("app", "Apps"), ("role", "Papéis"),
               ("child_table", "Tabelas filhas"), ("platform", "Outros (plataforma)")]


# dependency.dependencytype (rótulos da plataforma)
DEP_TYPE = {0: "None", 1: "Solution Internal", 2: "Published", 4: "Unpublished", 8: "Shared"}


class Graph:
    def __init__(self):
        self.nodes = {}            # (kind, key) -> {"kind", "key", "label", "sub"}
        self.edges = {}            # (src, dst, relation) -> {"source": inferida|plataforma, "detail"}
        self._out, self._in = defaultdict(list), defaultdict(list)

    def node(self, kind, key, label=None, sub=None, ct=None):
        k = (kind, key)
        if k not in self.nodes:
            self.nodes[k] = {"kind": kind, "key": key, "label": label or str(key), "sub": sub, "ct": ct}
        return k

    def edge(self, src, dst, relation, source="inferida", detail=None):
        if src == dst:
            return
        k = (src, dst, relation)
        if k not in self.edges:
            self.edges[k] = {"source": source, "detail": detail}
            self._out[src].append(k)
            self._in[dst].append(k)
        elif self.edges[k]["source"] != source:
            self.edges[k]["source"] = "inferida + plataforma"

    def out_of(self, n):
        return [(k[1], k[2], self.edges[k]) for k in self._out.get(n, [])]

    def into(self, n):
        return [(k[0], k[2], self.edges[k]) for k in self._in.get(n, [])]


def structural_teams(sec):
    """Equipes que viram nota/nó: owner e grupo Entra. Equipes de acesso (teamtype 1) são criadas por
    registro (access team templates) e podem ser milhares — ficam só na contagem."""
    return [t for t in sec.get("teams") or [] if t.get("type_code") != 1]


TEAM_NOTES_MAX = 1000  # acima disso, só equipes com papel ou grupo do Entra viram nota/nó (o resto: CSV + índice)


def teams_with_notes(sec):
    """Equipes que viram nota e nó do grafo. Org com dezenas de milhares de equipes de proprietário (uma por
    registro/usuário — ESAB-TEST: 72 mil, todas com o mesmo papel) geraria notas, arestas e uma nota de papel
    de vários MB sem análise: ficam as com papel ou grupo do Entra, as de mais membros primeiro."""
    ts = structural_teams(sec)
    if len(ts) <= TEAM_NOTES_MAX:
        return ts
    keep = [t for t in ts if t.get("roles") or t.get("aad_group")]
    return sorted(keep, key=lambda t: (-(t.get("members") or 0), t["name"]))[:TEAM_NOTES_MAX]


def build(d):
    g = Graph()
    tables = d.get("tables") or []
    procs = d.get("processes") or []
    plugins = d.get("plugins") or {}
    wrs = d.get("webresources") or []
    apis = d.get("customapis") or []
    alm = d.get("alm") or {}
    apps = d.get("apps") or {}
    sec = d.get("security") or {}

    # --- nós do escopo (todos, inclusive os sem aresta: viram candidatos a órfão) ---
    by_entityset = {}
    table_ids, col_ids = {}, {}
    for t in tables:
        g.node("table", t["logical"], t["logical"])
        if t.get("entityset"):
            by_entityset[t["entityset"].lower()] = t["logical"]
        if t.get("id"):
            table_ids[t["id"].lower()] = t["logical"]
        for col in t.get("columns") or []:
            if col.get("id"):
                col_ids[col["id"].lower()] = (t["logical"], col["logical"])
    for w in wrs:
        g.node("webresource", w["name"], w["name"])
    for a in plugins.get("assemblies") or []:
        g.node("assembly", a["name"], a["name"])
    proc_by_id = {}
    for p in procs:
        n = g.node("process", p["id"], p["name"], sub=PROC_KIND.get(p["category"], "workflow"))
        proc_by_id[p["id"].lower()] = n
    for s in plugins.get("steps") or []:
        g.node("step", s["id"], s["name"])
    for a in apis:
        g.node("customapi", a["unique"], a["unique"])
    for e in alm.get("envvars") or []:
        g.node("envvar", e["name"], e["name"])
    for r in alm.get("connrefs") or []:
        g.node("connref", r["name"], r["name"])
    for kind_ in ("modeldriven", "canvas", "bots"):
        for a in apps.get(kind_) or []:
            g.node("app", a["id"], a["name"])
    for r in sec.get("roles") or []:
        if r.get("scope_reason"):
            g.node("role", r["name"], r["name"])
    for p in d.get("pcf") or []:
        g.node("pcf", p["name"], p["name"])

    known_tables = {t["logical"] for t in tables}

    def table_node(name):
        name = (name or "").lower()
        name = by_entityset.get(name, name)
        return ("table", name) if name in known_tables else None

    # --- plugins ---
    type_to_asm = {}
    for a in plugins.get("assemblies") or []:
        for t in a.get("types") or []:
            type_to_asm[(t.get("typename") or "").lower()] = a["name"]
            if t.get("friendly"):
                type_to_asm[t["friendly"].lower()] = a["name"]
    for s in plugins.get("steps") or []:
        sn = ("step", s["id"])
        tn = table_node(s.get("entity"))
        if tn:
            g.edge(sn, tn, f"registrado em {s.get('message')}")
        if s.get("assembly"):  # pode ser assembly fora do escopo (ex. step não gerenciado em plugin da Microsoft)
            g.edge(sn, g.node("assembly", s["assembly"], s["assembly"]), "executa código de")
        if s.get("handler_kind") == "serviceendpoint" and s.get("handler"):
            g.edge(sn, g.node("endpoint", s["handler"], s["handler"]), "envia mensagem para")

    # --- processos e flows ---
    for p in procs:
        pn = ("process", p["id"])
        tn = table_node(p.get("entity"))
        if tn:
            g.edge(pn, tn, "tabela primária")
        for t in p.get("tables") or []:
            tn = table_node(t)
            if tn:
                g.edge(pn, tn, "lê/grava")
        for r in p.get("connrefs") or []:
            g.edge(pn, g.node("connref", r, r), "usa conexão")
        for e in p.get("envvar_refs") or []:
            g.edge(pn, ("envvar", e), "lê variável")
        for a in p.get("customapi_refs") or []:
            g.edge(pn, ("customapi", a), "chama")
        for wid in p.get("child_flow_ids") or []:
            child = proc_by_id.get(wid)
            g.edge(pn, child or g.node("process", wid, f"child flow {wid[:8]} (fora do escopo)", sub="flow"),
                   "chama child flow")
        for c in p.get("connectors") or []:
            if c != "commondataserviceforapps":
                g.edge(pn, g.node("connector", c, c), "usa conector")
        for h in p.get("http_hosts") or []:
            g.edge(pn, g.node("host", h, h), "chama HTTP")

    # --- formulários, ribbons ---
    for f in d.get("forms") or []:
        tn = table_node(f.get("entity"))
        if not tn:
            continue
        fn = g.node("form", f["id"], f"{f['entity']} / {f['name']}", sub=f["entity"])
        g.edge(fn, tn, "formulário de")
        for lib in f.get("libraries") or []:
            g.edge(fn, g.node("webresource", lib, lib), "carrega biblioteca")
        for p in f.get("pcf") or []:
            if ("pcf", p["control"]) in g.nodes:
                g.edge(fn, ("pcf", p["control"]), "usa controle PCF", detail=p.get("field"))
    for p in d.get("pcf") or []:
        for h in (p.get("manifest") or {}).get("external_domains") or []:
            g.edge(("pcf", p["name"]), g.node("host", h, h), "chama HTTP (PCF)")
    for r in d.get("ribbons") or []:
        tn = table_node(r.get("entity"))
        for c in r.get("calls") or []:
            if tn:
                g.edge(tn, g.node("webresource", c["library"], c["library"]),
                       f"botão {r.get('label') or r.get('button')}")

    # --- web resources (análise do código) ---
    api_names = {a["unique"].lower(): a["unique"] for a in apis}
    names = set(known_tables) | set(by_entityset) | set(api_names)
    for w in wrs:
        wn = ("webresource", w["name"])
        content = w.get("content") or ""
        if content:
            # só nomes entre aspas ("contoso_project"): evita casar variáveis como Contoso.Account
            quoted = " ".join(QUOTED_RE.findall(content))
            for tok in field_refs(quoted, names):
                tn = table_node(tok)
                if tn:
                    g.edge(wn, tn, "consulta/grava (JS)")
                elif tok in api_names:
                    g.edge(wn, ("customapi", api_names[tok]), "chama (JS)")
        for h in (w.get("js") or {}).get("external_hosts") or []:
            g.edge(wn, g.node("host", h, h), "chama HTTP (JS)")

    # --- Custom APIs ---
    for a in apis:
        an = ("customapi", a["unique"])
        tn = table_node(a.get("bound_entity"))
        if tn:
            g.edge(an, tn, "vinculada a")
        asm = type_to_asm.get((a.get("plugin_type") or "").lower())
        if asm:
            g.edge(an, ("assembly", asm), "implementada por")

    # --- apps, papéis, relacionamentos ---
    for a in apps.get("modeldriven") or []:
        for comp in a.get("components") or []:
            if comp.get("type") == 1 and comp["objectid"] in table_ids:
                g.edge(("app", a["id"]), ("table", table_ids[comp["objectid"]]), "inclui tabela")
    for t, roles in (sec.get("privilege_matrix") or {}).items():
        for rn in roles:
            if ("role", rn) in g.nodes and t in known_tables:
                g.edge(("role", rn), ("table", t), "concede acesso a")
    # --- estrutura de segurança: BU → BU pai, equipe → BU, equipe → papel do escopo ---
    for b in sec.get("business_units") or []:
        g.node("bu", b["id"], b["name"])
    for b in sec.get("business_units") or []:
        if b.get("parent_id") and ("bu", b["parent_id"]) in g.nodes:
            g.edge(("bu", b["id"]), ("bu", b["parent_id"]), "filha de")
    for t in teams_with_notes(sec):
        tn = g.node("team", t["id"], t["name"])
        if t.get("bu_id") and ("bu", t["bu_id"]) in g.nodes:
            g.edge(tn, ("bu", t["bu_id"]), "pertence a")
        for rn in t.get("roles") or []:
            if ("role", rn) in g.nodes:
                g.edge(tn, ("role", rn), "recebe papel")
    for r in d.get("relationships") or []:
        if r["kind"] == "1:N" and r["from"] in known_tables and r["to"] in known_tables:
            g.edge(("table", r["to"]), ("table", r["from"]), f"lookup {r.get('lookup')}")

    # --- uso de campos: componente → coluna (o que a pergunta "onde o campo X é usado?" precisa) ---
    # Mesmas fontes da matriz de uso de campos (05), para TODAS as tabelas do escopo e todas as colunas.
    # Formulário/view → coluna a plataforma já registra (tabela dependency); só entram aqui sem ela.
    cols_of = {t["logical"]: {c["logical"] for c in t.get("columns") or []} for t in tables}
    custom_of = {t["logical"]: {c["logical"] for c in t.get("columns") or [] if c.get("custom")} for t in tables}
    has_platform = bool((d.get("platform_dependencies") or {}).get("edges"))

    def col(tbl, c):
        c = (c or "").strip().lower()
        if tbl in cols_of and c in cols_of[tbl]:
            return g.node("column", f"{tbl}.{c}", f"{tbl}.{c}", sub=tbl)
        return None

    lib_tables = defaultdict(set)
    for f in d.get("forms") or []:
        fn = ("form", f["id"])
        if fn not in g.nodes:
            continue
        for lib in f.get("libraries") or []:
            lib_tables[lib].add(f["entity"])
        for h in f.get("handlers") or []:
            cn = col(f["entity"], h.get("field"))
            if cn:
                g.edge(fn, cn, f"evento {h.get('event')}", detail=h.get("function"))
        for pc in f.get("pcf") or []:
            for fld in ([pc["field"]] if pc.get("field") else []) + list(pc.get("bound") or []):
                cn = col(f["entity"], fld)
                if cn:
                    g.edge(fn, cn, "controle PCF", detail=pc.get("control"))
        if not has_platform:
            for fld in f.get("fields") or []:
                cn = col(f["entity"], fld)
                if cn:
                    g.edge(fn, cn, "exibe campo")
    if not has_platform:
        for vw in d.get("views") or []:
            for fld in vw.get("columns") or []:
                cn = col(vw["entity"], fld)
                if cn and ("view", vw["id"]) in g.nodes:
                    g.edge(("view", vw["id"]), cn, "coluna da view")
    for s in plugins.get("steps") or []:
        attrs = {(a.strip(), "filtering attribute") for a in (s.get("filtering") or "").split(",") if a.strip()}
        for im in s.get("images") or []:
            attrs |= {(a.strip(), "imagem do step") for a in (im.get("attributes") or "").split(",") if a.strip()}
        for fld, rel in sorted(attrs):
            cn = col(s.get("entity"), fld)
            if cn:
                g.edge(("step", s["id"]), cn, rel)
    for p in procs:
        ents = [p.get("entity")] + [t for t in p.get("tables") or [] if t != p.get("entity")]
        for fld in p.get("field_refs") or []:
            for tbl in ents:  # primeira tabela do processo que tem a coluna
                cn = col(tbl, fld)
                if cn:
                    g.edge(("process", p["id"]), cn, "lê/grava campo")
                    break
    # Índice inverso montado uma única vez para JS sem vínculo conhecido a formulários.
    # O código anterior tokenizava novamente o mesmo JS para TODAS as tabelas do escopo.
    custom_field_tables = defaultdict(list)
    for tbl, fields in custom_of.items():
        for fld in fields:
            custom_field_tables[fld].append(tbl)

    for w in wrs:
        content = w.get("content") or ""
        if not content:
            continue
        # A mesma expressão de field_refs(), agora executada somente uma vez por web resource.
        tokens = field_tokens(content)
        # JS carregado em formulários: colunas das tabelas desses formulários; sem formulário: colunas
        # custom de qualquer tabela do escopo que casem pelo nome (ambíguo por natureza — detalhe avisa)
        tabs = lib_tables.get(w["name"])
        if tabs:
            targets = ((tbl, sorted(tokens & cols_of[tbl])) for tbl in sorted(tabs) if tbl in cols_of)
        else:
            matched = defaultdict(list)
            for fld in tokens:
                for tbl in custom_field_tables.get(fld, ()):
                    matched[tbl].append(fld)
            targets = ((tbl, sorted(fields)) for tbl, fields in sorted(matched.items()))
        for tbl, fields in targets:
            for fld in fields:
                cn = col(tbl, fld)
                if cn:
                    g.edge(("webresource", w["name"]), cn, "usa campo (JS)",
                           detail=None if tabs else "casado só pelo nome da coluna")

    # --- colunas calculadas/rollup/fórmula, mapeamentos entre tabelas e comandos modernos ---
    def any_col(ref):  # coluna de tabela fora do escopo também vira nó (chave column:<tabela>.<coluna>)
        tb, _, cn = ref.partition(".")
        return col(tb, cn) or g.node("column", ref, ref, sub=tb)

    for t in tables:
        for c_ in t.get("columns") or []:
            fm = c_.get("formula")
            if fm and fm.get("refs"):
                me = col(t["logical"], c_["logical"])
                for ref in fm["refs"]:
                    g.edge(me, any_col(ref), f"{fm['kind']}: usa")
    for m in d.get("mappings") or []:
        for f in m.get("fields") or []:
            if f.get("source") and f.get("target") and (m["target"] in cols_of or m["source"] in cols_of):
                g.edge(any_col(f"{m['target']}.{f['target']}"), any_col(f"{m['source']}.{f['source']}"),
                       "mapeado de", detail="mapeamento do sistema" if f.get("system") else None)
    for cm in d.get("commands") or []:
        cn = g.node("command", cm["id"], cm.get("label") or cm["name"], sub=cm.get("table"))
        if cm.get("table") in known_tables:
            g.edge(cn, ("table", cm["table"]), f"botão em ({cm.get('location') or '—'})")
        if cm.get("webresource"):
            g.edge(cn, g.node("webresource", cm["webresource"], cm["webresource"]), "executa JS",
                   detail=cm.get("function"))
        if cm.get("app_id") and ("app", cm["app_id"]) in g.nodes:
            g.edge(("app", cm["app_id"]), cn, "usa comando")

    # --- site maps: app → site map → tabela / web resource ---
    for sm in d.get("sitemaps") or []:
        sn = g.node("sitemap", sm["id"], sm["name"])
        for a in sm.get("apps") or []:
            if ("app", a["id"]) in g.nodes:
                g.edge(("app", a["id"]), sn, "usa site map")
        for t in sm.get("tables") or []:
            if t in known_tables:
                g.edge(sn, ("table", t), "navega para")
        for w in sm.get("webresources") or []:
            if ("webresource", w) in g.nodes:
                g.edge(sn, ("webresource", w), "usa web resource")
        for u in sm.get("external_urls") or []:
            host = re.sub(r"^https?://([^/:?#]+).*$", r"\1", u, flags=re.I)
            g.edge(sn, g.node("host", host, host), "abre URL externa")

    # --- SLAs: SLA → tabela, colunas das condições e do KPI, workflow de ação ---
    for s in d.get("slas") or []:
        sn = g.node("sla", s["id"], s["name"] or s["id"], sub=s.get("table"))
        if s.get("table") in known_tables:
            g.edge(sn, ("table", s["table"]), "SLA de")
        for it in s.get("items") or []:
            ent = it.get("entity") or s.get("table")
            if ent not in known_tables:
                continue
            for rel, cols in (("condição: aplicável quando", it.get("applicable_when_fields")),
                              ("condição: sucesso", it.get("success_fields")),
                              ("condição: pausa", it.get("pause_fields")),
                              ("KPI", [it["kpi_field"]] if it.get("kpi_field") else []),
                              ("aplicável a partir de", [it["applicable_from"]] if it.get("applicable_from") else [])):
                for col in cols or []:
                    g.edge(sn, g.node("column", f"{ent}.{col}", f"{ent}.{col}", sub=ent), rel, detail=it.get("name"))
            wid = it.get("workflow_id")
            if wid and ("process", wid) in g.nodes:
                g.edge(sn, ("process", wid), "dispara ação", detail=it.get("name"))
        if s.get("workflow_id") and ("process", s["workflow_id"]) in g.nodes:
            g.edge(sn, ("process", s["workflow_id"]), "dispara ação")

    # --- tabelas virtuais: tabela → provedor de dados → classe de plugin ---
    asm_by_name = {a["name"]: a["name"] for a in plugins.get("assemblies") or []}
    vt = d.get("virtual_tables") or {}
    provs = {p["id"].lower(): p for p in vt.get("providers") or []}
    for t in vt.get("tables") or []:
        p = provs.get(t.get("provider_id") or "")
        if not p or ("table", t["logical"]) not in g.nodes:
            continue
        pn = g.node("dataprovider", p["id"].lower(), p.get("name") or p["id"])
        g.edge(("table", t["logical"]), pn, "dados via provedor virtual", detail=t.get("datasource"))
        for op in p.get("operations") or []:
            dst = (("assembly", asm_by_name[op["assembly"]]) if op.get("assembly") in asm_by_name
                   else g.node("plugintype", op["plugintype_id"], op.get("typename") or op["plugintype_id"]))
            g.edge(pn, dst, f"executa {op['operation']}")

    # --- dependências da plataforma ---
    id_index = {**{k: ("table", v) for k, v in table_ids.items()},
                **{w["id"].lower(): ("webresource", w["name"]) for w in wrs},
                **{p["id"].lower(): ("process", p["id"]) for p in procs},
                **{a["id"].lower(): ("assembly", a["name"]) for a in plugins.get("assemblies") or []},
                **{s["id"].lower(): ("step", s["id"]) for s in plugins.get("steps") or []},
                **{e["id"].lower(): ("envvar", e["name"]) for e in alm.get("envvars") or []},
                **{a["id"].lower(): ("customapi", a["unique"]) for a in apis},
                **{a["id"].lower(): ("app", a["id"]) for k in ("modeldriven", "canvas", "bots") for a in apps.get(k) or []},
                **{r["id"].lower(): ("role", r["name"]) for r in sec.get("roles") or [] if r.get("scope_reason")},
                **{p["id"].lower(): ("pcf", p["name"]) for p in d.get("pcf") or [] if p.get("id")},
                **{s["id"]: ("sitemap", s["id"]) for s in d.get("sitemaps") or []},
                **{cm["id"]: ("command", cm["id"]) for cm in d.get("commands") or []},
                **{s["id"]: ("sla", s["id"]) for s in d.get("slas") or []}}
    for a in plugins.get("assemblies") or []:
        for t in a.get("types") or []:
            id_index[t["id"].lower()] = ("assembly", a["name"])  # classe → assembly (nota existente)
    for f in d.get("forms") or []:
        if ("form", f["id"]) in g.nodes:
            id_index[f["id"].lower()] = ("form", f["id"])
    for v in d.get("views") or []:
        id_index[v["id"].lower()] = ("view", v["id"])
        if ("view", v["id"]) not in g.nodes:
            g.node("view", v["id"], f"{v['entity']} / {v['name']}", sub=v["entity"])

    g.guid_index = {}  # nó do kit → GUID (catálogo e Dependências.csv casam o kit por aqui)
    for oid, node in id_index.items():
        g.guid_index.setdefault(node, oid)  # setdefault: assembly fica com o próprio id, não o de uma classe
    for oid, (t, c) in col_ids.items():
        g.guid_index.setdefault(("column", f"{t}.{c}"), oid)

    cat = d.get("catalog") or {}
    cat_names, cat_types = cat.get("names") or {}, cat.get("types") or {}

    def type_label(ct):
        return (cat_types.get(str(ct)) or {}).get("name") or str(ct)

    def resolve(oid, ctype_label, ctype):
        if oid in id_index:
            return id_index[oid]
        if oid in col_ids:
            t, c = col_ids[oid]
            return g.node("column", f"{t}.{c}", f"{t}.{c}", sub=t)
        nm = (cat_names.get(oid) or [None, None])[1]
        lbl = ctype_label or type_label(ctype)
        return g.node("platform", oid, f"{lbl} {nm}" if nm else f"{lbl} {oid[:8]}", sub=lbl, ct=ctype)

    pd = d.get("platform_dependencies") or {}
    if pd.get("format") == "compacto/1":
        nodes = pd.get("nodes") or []
        in_scope = set(id_index) | set(col_ids)
        platform_start = time.monotonic()
        platform_edges = pd.get("edges") or []
        platform_total = len(platform_edges)
        platform_added_before = len(g.edges)
        print(f"[render] Filtrando {platform_total:,} dependências da plataforma...", flush=True)
        for edge_ix, (a, b, dt) in enumerate(platform_edges, 1):
            if edge_ix % 100_000 == 0:
                print(f"[render] Dependências analisadas: {edge_ix:,}/{platform_total:,} "
                      f"({time.monotonic() - platform_start:.1f}s)", flush=True)
            (ca, ia), (cb, ib) = nodes[a], nodes[b]
            # arestas que tocam o escopo, nos dois sentidos (quem depende do escopo e do que o escopo depende).
            # Fora: internas de metadados (coluna → tabela, "Solution Internal") e nativo → nativo.
            if dt == 1 or (ib not in in_scope and ia not in in_scope):
                continue
            g.edge(resolve(ia, None, ca), resolve(ib, None, cb), f"plataforma ({DEP_TYPE.get(dt, dt)})",
                   source="plataforma")
        print(f"[render] Dependências: {platform_total:,} examinadas, "
              f"{len(g.edges) - platform_added_before:,} novas arestas, "
              f"{time.monotonic() - platform_start:.1f}s", flush=True)
    else:  # snapshots até a 0.5.0: lista de dicionários (RetrieveDependentComponents)
        for e in pd.get("edges") or []:
            src = resolve(e["dependent_id"], e.get("dependent_type_label"), e.get("dependent_type"))
            dst = resolve(e["required_id"], e.get("required_type_label"), e.get("required_type"))
            g.edge(src, dst, f"plataforma ({e.get('dependency_type')})", source="plataforma")
    return g


GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def canonical_key(n, cat_names=None):
    """Chave estável de um nó, a mesma em Componentes.csv, Dependências.csv, frontmatter e `dvinv onde-usa`.

    Componente do escopo: `<tipo>:<nome>` (table:account, column:account.x, webresource:...). Fora do escopo:
    tabela/coluna pelo nome lógico (table:systemuser, column:account.name) e o resto pelo GUID."""
    kind, key = n
    if kind != "platform":
        return f"{kind}:{key}"
    nm = (cat_names or {}).get(key)
    if nm and nm[1] and nm[0] in (1, 2):
        return f"{'table' if nm[0] == 1 else 'column'}:{nm[1]}"
    return key


def guid_of(g, n):
    oid = (getattr(g, "guid_index", {}) or {}).get(n)
    if oid:
        return oid
    return str(n[1]).lower() if GUID_RE.match(str(n[1]).lower()) else None


def kind_of(g, n):
    node = g.nodes.get(n) or {}
    return node.get("sub") if n[0] == "process" else n[0]


def summarize(g, d):
    """Estruturas prontas para renderizar/exportar."""
    fan_in, fan_out = Counter(), Counter()
    for (s, t, _r) in g.edges:
        fan_out[s] += 1
        fan_in[t] += 1

    # matriz por tabela: quem depende da tabela, por tipo (diretos)
    per_table = []
    for (kind, key), node in sorted(g.nodes.items()):
        if kind != "table":
            continue
        row = Counter()
        for s, _r, _e in g.into((kind, key)):
            k = kind_of(g, s)
            k = "child_table" if k == "table" else k  # tabela que aponta para esta via lookup
            row[k if k in dict(IMPACT_COLS) else "platform"] += 1
        per_table.append({"table": key, **{c: row.get(c, 0) for c, _ in IMPACT_COLS}, "total": sum(row.values()),
                          "depends_on": sum(1 for _ in g.out_of((kind, key)))})
    per_table.sort(key=lambda r: (-r["total"], r["table"]))

    # tipo × tipo
    tt = Counter((g.nodes[s]["kind"], g.nodes[t]["kind"]) for (s, t, _r) in g.edges)

    scoped = {"webresource", "process", "customapi", "envvar", "connref", "assembly"}
    orphans = [n for n in g.nodes if n[0] in scoped and not fan_in[n] and not fan_out[n]
               and not (n[0] == "process" and g.nodes[n]["label"].endswith("(fora do escopo)"))]
    # sem dependentes: componentes que ninguém usa (mas que podem usar algo)
    unused = [n for n in g.nodes if n[0] in {"webresource", "customapi", "envvar", "connref", "pcf"} and not fan_in[n]]
    external = sorted(((n, fan_in[n]) for n in g.nodes if n[0] in ("host", "connector", "endpoint")),
                      key=lambda x: -x[1])
    return {"fan_in": fan_in, "fan_out": fan_out, "per_table": per_table, "type_x_type": tt,
            "orphans": orphans, "unused": unused, "external": external,
            "missing": (d.get("platform_dependencies") or {}).get("missing") or []}


def to_json(g, summary):
    return {
        "nodes": [dict(v, id=f"{k[0]}:{k[1]}", fan_in=summary["fan_in"][k], fan_out=summary["fan_out"][k])
                  for k, v in g.nodes.items()],
        "edges": [{"from": f"{s[0]}:{s[1]}", "from_label": g.nodes[s]["label"], "from_kind": g.nodes[s]["kind"],
                   "to": f"{t[0]}:{t[1]}", "to_label": g.nodes[t]["label"], "to_kind": g.nodes[t]["kind"],
                   "relation": r, **e} for (s, t, r), e in g.edges.items()],
        "per_table": summary["per_table"],
        "missing_in_solution": summary["missing"],
    }

