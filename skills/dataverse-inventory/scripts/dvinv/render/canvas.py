"""Mapa do ambiente em JSON Canvas 1.0 (.canvas): apps → tabelas ← plugins, tabelas → integrações."""

import hashlib
import json
from collections import Counter, defaultdict

W, H, GAP, PAD = 300, 60, 20, 40


def _id(*parts):
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:16]


def write_map(v, d):
    tables = d.get("tables") or []
    procs = d.get("processes") or []
    plugins = d.get("plugins") or {}
    apps = d.get("apps") or {}

    # Tabelas mais "quentes": soma de steps + processos + flows que tocam.
    heat = Counter()
    for s in plugins.get("steps") or []:
        heat[s.get("entity")] += 1
    for p in procs:
        if p.get("entity"):
            heat[p["entity"]] += 1
        for t in p.get("tables") or []:
            heat[t] += 1
    known = {t["logical"] for t in tables}
    top_tables = [t for t, _ in heat.most_common() if t in known][:25]

    flows = [p for p in procs if p["category"] == "Cloud Flow"]
    integ = Counter(c for p in flows for c in p.get("connectors") or [] if c != "commondataserviceforapps")
    integ.update({f"HTTP {h}": 1 for p in flows for h in p.get("http_hosts") or []})
    integ.update({f"Endpoint {e['name']}": 1 for e in d.get("serviceendpoints") or []})
    integ_names = [n for n, _ in integ.most_common(25)]

    app_items = [("app", a["id"], a["name"]) for k in ("modeldriven", "canvas", "bots") for a in apps.get(k) or []][:20]
    asm_items = [("assembly", a["name"], a["name"]) for a in plugins.get("assemblies") or []][:20]

    nodes, edges, node_of = [], [], {}

    def column(title, x, items, color):
        y = PAD + 60
        members = []
        for kind, key, label in items:
            nid = _id(kind, key)
            rel = v.paths.get((kind, key))
            node = {"id": nid, "x": x + PAD, "y": y, "width": W, "height": H}
            if rel:
                node.update(type="file", file=rel + ".md")
            else:
                node.update(type="text", text=f"**{label}**")
            members.append(node)
            node_of[(kind, key)] = nid
            y += H + GAP
        height = max(y - PAD + PAD, 160)
        nodes.append({"id": _id("group", title), "type": "group", "label": title, "x": x, "y": 0,
                      "width": W + 2 * PAD, "height": height, "color": color})
        nodes.extend(members)

    column("Apps e agentes", 0, app_items, "5")
    column("Tabelas mais automatizadas", 480, [("table", t, t) for t in top_tables], "4")
    column("Plugins", 960, asm_items, "2")
    column("Integrações", 1440, [("integ", n, n) for n in integ_names], "6")

    seen = set()

    def edge(a, b, label, from_side="right", to_side="left"):
        if a in node_of and b in node_of and (a, b) not in seen:
            seen.add((a, b))
            edges.append({"id": _id("e", a, b), "fromNode": node_of[a], "fromSide": from_side,
                          "toNode": node_of[b], "toSide": to_side, "label": label})

    per_asm = defaultdict(Counter)
    for s in plugins.get("steps") or []:
        if s.get("assembly") and s.get("entity"):
            per_asm[s["assembly"]][s["entity"]] += 1
    for asm, ents in per_asm.items():
        for ent, n in ents.items():
            edge(("assembly", asm), ("table", ent), f"{n} step(s)", "left", "right")
    for p in flows:
        for t in set(p.get("tables") or []) | ({p["entity"]} if p.get("entity") else set()):
            for c in p.get("connectors") or []:
                edge(("table", t), ("integ", c), "flow")
            for h in p.get("http_hosts") or []:
                edge(("table", t), ("integ", f"HTTP {h}"), "flow HTTP")

    body = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=1)
    v.write(f"{v.folder}/Mapa do Ambiente.canvas", None, body, raw=True)
