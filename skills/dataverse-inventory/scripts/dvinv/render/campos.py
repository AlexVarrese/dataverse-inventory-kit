"""Matriz de uso dos campos: para cada coluna (nativa ou customizada), quem a usa — formulário, evento onChange,
JavaScript, plugin, business rule, workflow, BPF, action, cloud flow, view, PCF, fórmula — e o que roda quando ela
muda (gatilhos).

Fonte: o grafo de dependências (as mesmas arestas de `Dependências.csv` e do `dvinv onde-usa`), mais os gatilhos
lidos de cada componente: filtering attributes dos steps de Update, `triggeronupdateattributelist` dos workflows,
colunas de filtro do gatilho dos cloud flows e eventos onChange dos formulários (exatos); campos citados nas
business rules (provável — a plataforma não expõe a lista de gatilho delas).
"""

from collections import defaultdict

CATS = [("forms", "Formulários"), ("events", "Eventos onChange"), ("js", "JavaScript"), ("plugins", "Plugins"),
        ("businessrule", "Business rules"), ("workflow", "Workflows"), ("bpf", "BPFs"), ("action", "Actions"),
        ("flow", "Cloud flows"), ("views", "Views"), ("pcf", "PCF"), ("formula", "Fórmulas e calculadas"),
        ("other", "Outros")]
TRIGGERS = [("t_plugins", "Plugins (filtering)"), ("t_workflows", "Workflows (gatilho)"),
            ("t_flows", "Cloud flows (filtro)"), ("t_onchange", "onChange (JS)"),
            ("t_rules", "Business rules (provável)")]
PROCESS_CATS = {"workflow", "businessrule", "bpf", "action", "flow"}
UPDATE_FLOW_MESSAGES = {"3", "4", "6", "7"}  # gatilho Dataverse que inclui Update (igual a findings.DV_TRIGGER_UPDATE)


def _category(g, src, rel):
    kind = src[0]
    if kind == "form":
        return "events" if rel.startswith("evento") else "pcf" if rel == "controle PCF" else "forms"
    if kind == "process":
        sub = g.nodes[src].get("sub")
        return sub if sub in PROCESS_CATS else "workflow"
    if kind == "column":
        return "formula" if rel.endswith("usa") else "other"
    return {"webresource": "js", "step": "plugins", "view": "views", "pcf": "pcf"}.get(kind, "other")


def _short(label):
    return label.split(" / ", 1)[1] if " / " in label else label


def triggers(d):
    """→ ({(tabela, coluna): {gatilho: {nomes}}}, {tabela: {"plugins": [...], "flows": [...]}} que rodam em qualquer
    alteração da tabela)."""
    per, any_change = defaultdict(lambda: defaultdict(set)), defaultdict(lambda: defaultdict(set))
    for s in (d.get("plugins") or {}).get("steps") or []:
        if s.get("message") != "Update" or s.get("enabled") is False or not s.get("entity"):
            continue
        attrs = {a.strip().lower() for a in (s.get("filtering") or "").split(",") if a.strip()}
        for a in attrs:
            per[(s["entity"], a)]["t_plugins"].add(s["name"])
        if not attrs:
            any_change[s["entity"]]["plugins"].add(s["name"])
    for p in d.get("processes") or []:
        if not p.get("active"):
            continue
        if p.get("category") == "Workflow" and p.get("entity") and p.get("on_update"):
            for a in {x.strip().lower() for x in p["on_update"].split(",") if x.strip()}:
                per[(p["entity"], a)]["t_workflows"].add(p["name"])
        elif p.get("category") == "Business Rule" and p.get("entity"):
            for a in p.get("field_refs") or []:
                per[(p["entity"], a)]["t_rules"].add(p["name"])
        elif p.get("category") == "Cloud Flow" and p.get("trigger_table"):
            dv = p.get("dv_trigger") or {}
            if str(dv.get("message")) not in UPDATE_FLOW_MESSAGES:
                continue
            for a in p.get("trigger_fields") or []:
                per[(p["trigger_table"], a)]["t_flows"].add(p["name"])
            if not p.get("trigger_fields") and not dv.get("filter"):
                any_change[p["trigger_table"]]["flows"].add(p["name"])
    for f in d.get("forms") or []:
        if not f.get("active"):
            continue
        for h in f.get("handlers") or []:
            if h.get("field") and str(h.get("event") or "").lower() == "onchange":
                per[(f["entity"], h["field"])]["t_onchange"].add(f"{f['name']}: {h.get('function') or '?'}")
    return per, any_change


def matrix(d, g):
    """→ {tabela: [linha]}: só colunas com ao menos um uso ou gatilho; customizadas primeiro.

    linha = {"column", "display", "custom", <categoria>: [nomes], <gatilho>: [nomes]}"""
    trig, _any = triggers(d)
    out = {}
    for t in d.get("tables") or []:
        ln, rows = t["logical"], []
        for c in t.get("columns") or []:
            cats = defaultdict(set)
            n = ("column", f"{ln}.{c['logical']}")
            if n in g.nodes:
                for src, rel, _e in g.into(n):
                    cat, name = _category(g, src, rel), _short(g.nodes[src]["label"])
                    if cat in ("pcf", "formula", "other"):  # sem coluna própria na matriz: diz o que é
                        name = f"{name} ({rel})"
                    cats[cat].add(name)
            tr = trig.get((ln, c["logical"])) or {}
            if not cats and not tr:
                continue
            rows.append({"column": c["logical"], "display": c.get("display"), "custom": bool(c.get("custom")),
                         **{k: sorted(cats.get(k, ())) for k, _ in CATS},
                         **{k: sorted(tr.get(k, ())) for k, _ in TRIGGERS}})
        rows.sort(key=lambda r: (not r["custom"], r["column"]))
        if rows:
            out[ln] = rows
    return out


def logic_count(r):
    """Quantos componentes de lógica (código e automação) usam a coluna — o que quebra se ela mudar."""
    return sum(len(r[k]) for k in ("events", "js", "plugins", "businessrule", "workflow", "bpf", "action", "flow"))
