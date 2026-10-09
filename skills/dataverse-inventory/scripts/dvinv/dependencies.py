"""Grafo e matriz de dependências entre componentes.

Aresta = "A depende de B" (se B mudar ou sumir, A é afetado). Duas origens:

- inferida  — derivada do que o kit coletou: step → tabela/assembly, processo → tabela, flow →
  conexão/variável/Custom API/child flow/host HTTP, formulário → biblioteca JS, botão → JS, JS →
  tabela/Custom API/host, Custom API → tabela/plugin, app → tabela, papel → tabela, tabela filha →
  tabela pai (lookup).
- plataforma — RetrieveDependentComponents (deep.platform_dependencies): o que o Dataverse registra
  e usa para bloquear exclusão.

Saídas: _derived/<run_id>/dependencies.json (render; o diff recalcula), matriz por tabela, matriz tipo × tipo, fan-in/fan-out, órfãos.
"""

import re
from collections import Counter, defaultdict

from .util import field_refs

QUOTED_RE = re.compile(r"[\"'`]([A-Za-z][A-Za-z0-9_]{2,})[\"'`]")

KIND_LABEL = {
    "table": "Tabela", "column": "Coluna", "form": "Formulário", "view": "View", "webresource": "Web resource",
    "step": "Plugin step", "assembly": "Plugin assembly", "plugintype": "Classe de plugin",
    "process": "Processo", "customapi": "Custom API", "app": "App", "role": "Papel", "envvar": "Variável de ambiente",
    "connref": "Referência de conexão", "endpoint": "Service endpoint", "connector": "Conector (externo)",
    "host": "Host HTTP (externo)", "optionset": "Option set", "platform": "Outro (plataforma)",
}
# Processos são subdivididos por categoria na matriz por tabela.
PROC_KIND = {"Workflow": "workflow", "Dialog": "workflow", "Business Rule": "businessrule", "Action": "action",
             "Business Process Flow": "bpf", "Cloud Flow": "flow", "Desktop Flow": "flow"}
IMPACT_COLS = [("form", "Formulários"), ("webresource", "JS/web resources"), ("step", "Plugin steps"),
               ("businessrule", "Business rules"), ("workflow", "Workflows"), ("action", "Actions"), ("bpf", "BPFs"),
               ("flow", "Cloud flows"), ("customapi", "Custom APIs"), ("app", "Apps"), ("role", "Papéis"),
               ("child_table", "Tabelas filhas"), ("platform", "Outros (plataforma)")]


class Graph:
    def __init__(self):
        self.nodes = {}            # (kind, key) -> {"kind", "key", "label", "sub"}
        self.edges = {}            # (src, dst, relation) -> {"source": inferida|plataforma, "detail"}
        self._out, self._in = defaultdict(list), defaultdict(list)

    def node(self, kind, key, label=None, sub=None):
        k = (kind, key)
        if k not in self.nodes:
            self.nodes[k] = {"kind": kind, "key": key, "label": label or str(key), "sub": sub}
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
    for r in d.get("relationships") or []:
        if r["kind"] == "1:N" and r["from"] in known_tables and r["to"] in known_tables:
            g.edge(("table", r["to"]), ("table", r["from"]), f"lookup {r.get('lookup')}")

    # --- dependências da plataforma ---
    id_index = {**{k: ("table", v) for k, v in table_ids.items()},
                **{w["id"].lower(): ("webresource", w["name"]) for w in wrs},
                **{p["id"].lower(): ("process", p["id"]) for p in procs},
                **{a["id"].lower(): ("assembly", a["name"]) for a in plugins.get("assemblies") or []},
                **{s["id"].lower(): ("step", s["id"]) for s in plugins.get("steps") or []},
                **{e["id"].lower(): ("envvar", e["name"]) for e in alm.get("envvars") or []},
                **{a["id"].lower(): ("customapi", a["unique"]) for a in apis},
                **{a["id"].lower(): ("app", a["id"]) for k in ("modeldriven", "canvas", "bots") for a in apps.get(k) or []},
                **{r["id"].lower(): ("role", r["name"]) for r in sec.get("roles") or [] if r.get("scope_reason")}}
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

    def resolve(oid, ctype_label, ctype):
        if oid in id_index:
            return id_index[oid]
        if oid in col_ids:
            t, c = col_ids[oid]
            return g.node("column", f"{t}.{c}", f"{t}.{c}", sub=t)
        return g.node("platform", oid, f"{ctype_label or ctype} {oid[:8]}", sub=ctype_label or str(ctype))

    for e in (d.get("platform_dependencies") or {}).get("edges") or []:
        src = resolve(e["dependent_id"], e.get("dependent_type_label"), e.get("dependent_type"))
        dst = resolve(e["required_id"], e.get("required_type_label"), e.get("required_type"))
        g.edge(src, dst, f"plataforma ({e.get('dependency_type')})", source="plataforma")
    return g


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
    unused = [n for n in g.nodes if n[0] in {"webresource", "customapi", "envvar", "connref"} and not fan_in[n]]
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

