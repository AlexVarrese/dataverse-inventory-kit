"""Navegação e grafo por tipo de componente (`Mapas/`) e cores do Graph view (`.obsidian/graph.json`).

O Graph view do Obsidian não tem "visões salvas" nem comandos na CLI (a CLI exige o app aberto e não abre o
grafo com filtro). Então o vault traz o grafo por tipo de três formas, todas texto versionável:

- `Mapas/<Tipo>.md` — hub do tipo: liga todos os componentes do tipo e resume os relacionamentos de cada um. O
  **grafo local** dessa nota (profundidade 2) mostra o tipo inteiro e seus vizinhos; a nota traz também os
  filtros prontos para colar no grafo global (`path:"…"`).
- `Mapas/<Tipo>.canvas` — mapa visual fixo (JSON Canvas): componentes do tipo à esquerda, o que eles usam à
  direita, setas com a relação. Não depende de configurar o grafo.
- `.obsidian/graph.json` — criado só se não existir: um grupo de cor por tipo (consulta `path:`), válido para
  todos os ambientes do vault. Depois disso, a configuração é de quem usa o vault.
"""

import hashlib
import json
from collections import defaultdict

# (pasta relativa ao ambiente, nome do mapa, cor RGB) — ordem = ordem no índice de mapas e nas cores do grafo
TYPES = [
    ("Tabelas", "Tabelas", 0x4C78A8),
    ("Processos/Cloud Flows", "Cloud Flows", 0xF58518),
    ("Processos/Workflows", "Workflows", 0xE45756),
    ("Processos/Business Rules", "Business Rules", 0x72B7B2),
    ("Processos/Actions", "Actions", 0x54A24B),
    ("Processos/BPFs", "BPFs", 0xEECA3B),
    ("Processos/Desktop Flows", "Desktop Flows", 0xB279A2),
    ("Plugins", "Plugins", 0x9D755D),
    ("Plugin Steps", "Plugin Steps", 0xBAB0AC),
    ("Web Resources", "Web Resources", 0xFF9DA6),
    ("Custom APIs", "Custom APIs", 0x79706E),
    ("Componentes PCF", "Componentes PCF", 0xD67195),
    ("Apps", "Apps", 0x439894),
    ("Site Maps", "Site Maps", 0x83BCB6),
    ("SLAs", "SLAs", 0xF2CF5B),
    ("Papéis", "Papéis", 0x8CD17D),
]
CANVAS_MAX = 200     # componentes por canvas (os de mais relacionamentos); a lista completa está no hub .md
REL_SHOWN = 12       # relacionamentos listados por componente no hub


def _id(*parts):
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _mdtable():
    from .obsidian import mdtable  # import tardio: obsidian importa este módulo
    return mdtable


def _folder_of(v, rel):
    inner = rel.split("#")[0][len(v.folder) + 1:]
    return inner.rsplit("/", 1)[0] if "/" in inner else ""


def _collapse(g, rels):
    """Relações com colunas viram uma por tabela ("campos (7)"): o mapa é visão por componente; o detalhe
    campo a campo está em Dependências.csv e na nota do componente."""
    out, cols = [], defaultdict(set)
    for t, r in rels:
        node = g.nodes.get(t) or {}
        if t[0] == "column" and node.get("sub") and ("table", node["sub"]) in g.nodes:
            cols[("table", node["sub"])].add(t)
        else:
            out.append((t, r))
    have = {t for t, _ in out}
    for tb, cs in cols.items():
        out.append((tb, f"campos ({len(cs)})"))
        have.add(tb)
    return sorted(out, key=lambda tr: (tr[1], str(g.nodes[tr[0]]["label"]).lower()))


def write_maps(v, g):
    """Gera um hub (.md) e um canvas por tipo presente no vault. → lista de (nome, pasta, n) gerados."""
    mdtable = _mdtable()
    members = defaultdict(list)                     # pasta -> [nó]
    for n, rel in v.paths.items():
        if "#" not in rel:
            members[_folder_of(v, rel)].append(n)
    made = []
    for folder, name, _color in TYPES:
        nodes = sorted(members.get(folder, []), key=lambda n: str(g.nodes.get(n, {}).get("label", n[1])).lower())
        if not nodes:
            continue
        rels = {n: _collapse(g, [(t, r) for t, r, _e in g.out_of(n)] if n in g.nodes else []) for n in nodes}
        used_by = {n: len(g.into(n)) if n in g.nodes else 0 for n in nodes}

        def link(n):
            node = g.nodes.get(n) or {}
            if n in v.paths:
                return v.link(n[0], n[1], node.get("label") or str(n[1]), in_table=True)
            return str(node.get("label") or n[1]).replace("|", "/")

        rows = []
        for n in nodes:
            rs = rels[n]
            shown = "; ".join(f"{r} → {link(t)}" for t, r in rs[:REL_SHOWN])
            rows.append([link(n), len(rs), used_by[n], shown + (f" … +{len(rs) - REL_SHOWN}" if len(rs) > REL_SHOWN else "")])
        hub = f"{v.folder}/Mapas/{name}"
        b = [f"# Mapa — {name}", "",
             f"**{len(nodes)}** componente(s) em `{folder}/`. Para ver o tipo como grafo:", "",
             f"- **Grafo local desta nota** (menu ⋯ → *Abrir grafo local*), profundidade 2: o tipo inteiro e o que ele usa.",
             f"- **Grafo global filtrado** — cole no campo de busca do Graph view:",
             f"  - só o tipo: `path:\"{v.folder}/{folder}/\"`",
             f"  - o tipo e as tabelas: `path:\"{v.folder}/{folder}/\" OR path:\"{v.folder}/Tabelas/\"`",
             f"  - sem o ruído das soluções: acrescente ` -path:\"{v.folder}/Soluções/\"`",
             f"- **Mapa visual fixo**: [[{v.folder}/Mapas/{name}.canvas|{name}.canvas]]"
             + (f" (os {CANVAS_MAX} de mais relacionamentos)" if len(nodes) > CANVAS_MAX else ""), "",
             "## Componentes e relacionamentos", "",
             mdtable(["Componente", "Depende de", "Usado por", "Relacionamentos (depende de)"], rows)]
        v.write(hub, {"tipo": "mapa", "tipo_componente": name, "componentes": len(nodes), "tags": v.tags("mapa")},
                "\n".join(b))
        _canvas(v, g, name, nodes, rels, used_by)
        made.append((name, folder, len(nodes)))
    if made:
        v.write(f"{v.folder}/Mapas/00 Mapas por tipo", {"tipo": "mapa", "tags": v.tags("mapa")}, "\n".join(
            ["# Mapas por tipo de componente", "",
             "Cada mapa liga todos os componentes de um tipo e mostra o que eles usam. Abra o **grafo local** do mapa "
             "ou use os filtros prontos dentro dele no Graph view. As cores do grafo seguem o tipo (`.obsidian/graph.json`).",
             "", _mdtable()(["Mapa", "Pasta", "Componentes", "Canvas"],
                            [[f"[[{v.folder}/Mapas/{n}|{n}]]", f"`{f}/`", c, f"[[{v.folder}/Mapas/{n}.canvas|canvas]]"]
                             for n, f, c in made])]))
    return made


def _canvas(v, g, name, nodes, rels, used_by):
    """Canvas do tipo: componentes (arquivo) à esquerda, alvos à direita, aresta = relação."""
    top = sorted(nodes, key=lambda n: (-(len(rels[n]) + used_by[n]), str(g.nodes.get(n, {}).get("label", n[1])).lower()))
    top = sorted(top[:CANVAS_MAX], key=lambda n: str(g.nodes.get(n, {}).get("label", n[1])).lower())
    targets = sorted({t for n in top for t, _r in rels[n] if t not in set(top)},
                     key=lambda t: (g.nodes[t]["kind"], str(g.nodes[t]["label"]).lower()))
    W, H, GAP = 360, 60, 20
    out_nodes, ids = [], {}

    def add(n, x, y):
        rel = v.paths.get(n)
        nid = _id(name, n)
        ids[n] = nid
        if rel and "#" not in rel:
            out_nodes.append({"id": nid, "type": "file", "file": rel + ".md", "x": x, "y": y, "width": W, "height": H})
        else:
            node = g.nodes.get(n) or {}
            out_nodes.append({"id": nid, "type": "text", "x": x, "y": y, "width": W, "height": H,
                              "text": f"**{node.get('label', n[1])}**\n{node.get('sub') or node.get('kind', '')}"})
    for i, n in enumerate(top):
        add(n, 0, i * (H + GAP))
    for i, t in enumerate(targets):
        add(t, W * 3, i * (H + GAP))
    edges = []
    for n in top:
        for t, r in rels[n]:
            if t in ids:
                edges.append({"id": _id(name, n, t, r), "fromNode": ids[n], "fromSide": "right",
                              "toNode": ids[t], "toSide": "left", "label": r})
    edges.sort(key=lambda e: (e["fromNode"], e["toNode"], e["id"]))
    v.write(f"{v.folder}/Mapas/{name}.canvas", None,
            json.dumps({"nodes": out_nodes, "edges": edges}, ensure_ascii=False, indent=1), raw=True)


def graph_settings(vault_root):
    """`.obsidian/graph.json` com um grupo de cor por tipo — só se ainda não existir (depois é do usuário)."""
    p = vault_root / ".obsidian" / "graph.json"
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    groups = [{"query": f"path:\"/{folder}/\"", "color": {"a": 1, "rgb": color}} for folder, _n, color in TYPES]
    p.write_text(json.dumps({
        "collapse-filter": False, "search": "-path:\"/Soluções/\" -path:\"/Achados/\" -path:\"/Bases/\"",
        "showTags": False, "showAttachments": False, "hideUnresolved": True, "showOrphans": False,
        "collapse-color-groups": False, "colorGroups": groups, "collapse-display": True, "showArrow": True,
        "textFadeMultiplier": 0, "nodeSizeMultiplier": 1, "lineSizeMultiplier": 1, "collapse-forces": True,
        "centerStrength": 0.5, "repelStrength": 10, "linkStrength": 1, "linkDistance": 250, "scale": 1, "close": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return True
