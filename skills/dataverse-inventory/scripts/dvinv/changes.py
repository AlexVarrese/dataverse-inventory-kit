"""Mudanças desde a extração anterior do mesmo ambiente.

A cada render, o snapshot atual é comparado com o snapshot íntegro imediatamente anterior da mesma pasta `_raw`:
componentes novos, removidos e alterados (dizendo o que mudou) e achados novos, resolvidos e que continuam, com as
ocorrências corrigidas. Vira a nota `Mudanças desde a extração anterior`, `Mudanças.csv` e uma linha no `00 Índice`
— as notas de versão do ambiente, sem precisar rodar `diff`.

Alterado = algum campo do registro mudou, ignorando o que muda sem mudança no ambiente (contagem de registros, origem
do conteúdo, campos derivados de análise). A data de modificação conta e aparece como tal: um deploy que regrava um
componente sem mudar o conteúdo fica visível como "só data de modificação".
"""

import hashlib
import json
from pathlib import Path

from . import findings as findings_mod
from . import snapshot as snapshot_mod

# Campos que mudam sem mudança no ambiente (ou que são derivados de outro campo já comparado).
VOLATILE = {"record_count", "content_source", "scope_reason", "secret_hits", "config_secret_hits", "binary",
            "xml_read", "js", "functions", "usage", "column_count", "custom_column_count", "modified_by", "created"}

LABELS = {"content": "código", "size": "tamanho", "columns": "colunas", "keys": "chaves", "handlers": "eventos",
          "libraries": "bibliotecas", "fields": "campos", "pcf": "controles PCF", "filtering": "filtro de colunas",
          "images": "imagens", "enabled": "ativo", "active": "ativo", "state": "estado", "stage": "estágio",
          "mode": "modo", "rank": "ordem", "version": "versão", "modified": "data de modificação",
          "solutions": "soluções", "field_refs": "campos usados", "envvar_refs": "variáveis usadas",
          "customapi_refs": "Custom APIs usadas", "value": "valor", "default": "padrão", "connected": "conexão",
          "components": "componentes", "request": "parâmetros", "response": "respostas", "types": "classes",
          "owner": "proprietário", "owner_disabled": "proprietário desativado", "manifest": "manifesto",
          "description": "descrição", "display": "nome de exibição", "required": "obrigatoriedade",
          "cascade_delete": "exclusão em cascata", "cascade_assign": "atribuição em cascata", "lookup": "lookup",
          "handler": "handler", "impersonating": "executa como", "plugin_type": "plugin", "items": "itens",
          "areas": "áreas", "formula": "fórmula", "audit": "auditoria", "type": "tipo"}


def _alm(d, k):
    return (d.get("alm") or {}).get(k) or []


def _columns(d):
    for t in d.get("tables") or []:
        for c in t.get("columns") or []:
            if c.get("custom"):
                yield dict(c, _key=f"{t['logical']}.{c['logical']}", _table=t["logical"])


# (rótulo, coletor de origem, registros, chave, nome exibido, (tipo de nota, chave da nota) para o link)
TYPES = [
    ("Tabelas", "tables", lambda d: d.get("tables") or [], lambda r: r["logical"], lambda r: r["logical"],
     lambda r: ("table", r["logical"])),
    ("Colunas customizadas", "tables", lambda d: list(_columns(d)), lambda r: r["_key"], lambda r: r["_key"],
     lambda r: ("table", r["_table"])),
    ("Relacionamentos", "relationships", lambda d: d.get("relationships") or [], lambda r: r.get("schema") or r["id"],
     lambda r: r.get("schema") or r["id"], None),
    ("Option sets", "optionsets", lambda d: d.get("optionsets") or [], lambda r: r["name"], lambda r: r["name"], None),
    ("Formulários", "forms", lambda d: d.get("forms") or [], lambda r: r["id"],
     lambda r: f"{r.get('entity')} / {r.get('name')}", lambda r: ("table", r.get("entity"))),
    ("Views", "views", lambda d: d.get("views") or [], lambda r: r["id"],
     lambda r: f"{r.get('entity')} / {r.get('name')}", lambda r: ("table", r.get("entity"))),
    ("Web resources", "webresources", lambda d: d.get("webresources") or [], lambda r: r["name"], lambda r: r["name"],
     lambda r: ("webresource", r["name"])),
    ("Componentes PCF", "pcf", lambda d: d.get("pcf") or [], lambda r: r["name"], lambda r: r["name"],
     lambda r: ("pcf", r["name"])),
    ("Plugin assemblies", "plugins", lambda d: (d.get("plugins") or {}).get("assemblies") or [], lambda r: r["name"],
     lambda r: r["name"], lambda r: ("assembly", r["name"])),
    ("Plugin steps", "plugins", lambda d: (d.get("plugins") or {}).get("steps") or [], lambda r: r["id"],
     lambda r: r.get("name") or r["id"], lambda r: ("step", r["id"])),
    ("Processos", "processes", lambda d: d.get("processes") or [], lambda r: r["id"],
     lambda r: f"{r.get('category')}: {r.get('name')}", lambda r: ("process", r["id"])),
    ("Custom APIs", "customapis", lambda d: d.get("customapis") or [], lambda r: r["unique"], lambda r: r["unique"],
     lambda r: ("customapi", r["unique"])),
    ("Service endpoints", "serviceendpoints", lambda d: d.get("serviceendpoints") or [], lambda r: r["name"],
     lambda r: r["name"], None),
    ("Variáveis de ambiente", "alm", lambda d: _alm(d, "envvars"), lambda r: r["name"], lambda r: r["name"], None),
    ("Referências de conexão", "alm", lambda d: _alm(d, "connrefs"), lambda r: r["name"], lambda r: r["name"], None),
    ("Apps", "apps", lambda d: [a for k in ("modeldriven", "canvas", "bots") for a in (d.get("apps") or {}).get(k) or []],
     lambda r: r["id"], lambda r: r.get("name") or r["id"], lambda r: ("app", r["id"])),
    ("Site maps", "sitemaps", lambda d: d.get("sitemaps") or [], lambda r: r["id"], lambda r: r.get("name") or r["id"],
     lambda r: ("sitemap", r["id"])),
    ("Comandos", "commands", lambda d: d.get("commands") or [], lambda r: r["id"], lambda r: r.get("name") or r["id"],
     lambda r: ("command", r["id"])),
    ("SLAs", "slas", lambda d: d.get("slas") or [], lambda r: r["id"], lambda r: r.get("name") or r["id"],
     lambda r: ("sla", r["id"])),
    ("Mapeamentos", "mappings", lambda d: d.get("mappings") or [], lambda r: f"{r.get('source')} → {r.get('target')}",
     lambda r: f"{r.get('source')} → {r.get('target')}", None),
    ("Papéis", "security", lambda d: [r for r in (d.get("security") or {}).get("roles") or [] if r.get("scope_reason")],
     lambda r: r["name"], lambda r: r["name"], lambda r: ("role", r["name"])),
]


# Etapas de que cada família de achados depende: se alguma falhou numa das duas extrações, o achado não é comparado
# (sumir porque a etapa falhou não é "resolvido").
FINDING_SOURCES = {"SEC": ("webresources", "processes", "plugins", "alm", "serviceendpoints"),
                   "UI": ("forms", "webresources"), "JS": ("webresources",), "PLG": ("plugins",), "OPS": ("environment",),
                   "PRC": ("processes",), "FLW": ("processes",), "SEG": ("security",), "ALM": ("alm", "processes"),
                   "DEP": ("platform_dependencies",), "FLD": ("field_usage",), "PCF": ("pcf", "forms"),
                   "NAV": ("sitemaps",), "SLA": ("slas",), "REPO": ("repos",), "STO": ("storage",),
                   "AUD": ("storage",)}
# Achados que dependem de uma etapa específica, além (ou no lugar) da família
FINDING_SOURCES_BY_ID = {"ALM-06": ("solution_history",), "ALM-07": ("solution_layers",), "PLG-04": ("health",),
                         "SEG-02": ("security",), "SEG-04": ("users",), "SEG-05": ("users",), "OPS-01": ("environment",),
                         "DEP-01": ("platform_dependencies",), "UI-01": ("forms", "webresources", "ribbons")}


def _keys(x, out):
    """Todos os nomes de campo, em qualquer nível."""
    if isinstance(x, dict):
        for k, v in x.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(x, list):
        for v in x:
            _keys(v, out)
    return out


def _strip(x, allowed):
    """Remove, em qualquer nível, campos fora de `allowed` (os que só uma das duas versões do kit grava)."""
    if isinstance(x, dict):
        return {k: _strip(v, allowed) for k, v in x.items() if k in allowed}
    if isinstance(x, list):
        return [_strip(v, allowed) for v in x]
    return x


def _sig(rec, allowed=None):
    """→ {campo: hash} dos campos comparáveis (os voláteis e os internos `_…` ficam de fora)."""
    return {k: hashlib.sha1(json.dumps(v if allowed is None else _strip(v, allowed), sort_keys=True,
                                       ensure_ascii=False, default=str).encode()).hexdigest()
            for k, v in rec.items() if k not in VOLATILE and not k.startswith("_")}


def previous_snapshot(snap):
    """Snapshot íntegro e completo imediatamente anterior a `snap` na mesma pasta `_raw` (ou None).

    Execução parcial (`--only` sem `--completar`) não serve de base: quase todas as etapas ficam sem dado."""
    snap = Path(snap)
    for p in snapshot_mod.list_snapshots(snap.parent):
        if p.name >= snap.name:
            continue
        m = snapshot_mod.read_manifest(p) or {}
        if m.get("only") and not m.get("base"):
            continue
        if snapshot_mod.verify(p)[0]:
            return p
    return None


def _usable(m, collector):
    return ((m.get("collectors") or {}).get(collector) or {}).get("status") in ("ok", "parcial")


def _same_options(m, pm, collector):
    """Mesmas opções deep.* que afetam o coletor (e as etapas de que ele depende) nas duas extrações — ex. com e
    sem código de web resources. Opção que não existia na versão do kit da extração antiga não conta."""
    from .collectors import DEPENDS, FLAGS_BY_COLLECTOR
    a, b = m.get("deep") or {}, pm.get("deep") or {}
    seen, todo = set(), [collector]
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        todo += list(DEPENDS.get(c, ()))
    flags = {f for c in seen for f in FLAGS_BY_COLLECTOR.get(c, ())}
    return all(a[f] == b[f] for f in flags if f in a and f in b)


def _pair_ok(m, pm, collector):
    return _usable(m, collector) and _usable(pm, collector) and _same_options(m, pm, collector)


def compare(snap, d, fnd):
    """→ dict com a comparação entre `snap` (já carregado em `d`, achados `fnd`) e o snapshot anterior, ou None.

    Nunca derruba o render: snapshot anterior ilegível ou em formato inesperado vira {"error": …} e a nota diz isso."""
    try:
        prev_dir = previous_snapshot(snap)
    except Exception:  # noqa: BLE001
        return None
    if prev_dir is None:
        return None
    try:
        return _compare(d, fnd, prev_dir)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:200], "prev": prev_dir.name}


def _compare(d, fnd, prev_dir):
    # o catálogo (dezenas de MB) não entra na comparação nem nos achados: não carregar poupa memória na VDI
    p = snapshot_mod.load(prev_dir, skip=("catalog",))
    p_fnd = findings_mod.compute(p)
    m, pm = d.get("manifest") or {}, p.get("manifest") or {}
    out = {"prev": prev_dir.name, "prev_at": pm.get("extracted_at"), "cur": m.get("run_id"),
           "cur_at": m.get("extracted_at"), "scope_changed": (pm.get("scope") or {}) != (m.get("scope") or {}),
           "types": [], "findings": []}
    for label, collector, get, key, name, link in TYPES:
        row = {"label": label, "new": [], "removed": [], "changed": [], "comparable": True}
        if not _pair_ok(m, pm, collector):
            row["comparable"] = False
            out["types"].append(row)
            continue
        cur = {key(r): r for r in get(d)}
        old = {key(r): r for r in get(p)}
        allowed = _keys(list(cur.values()), set()) & _keys(list(old.values()), set())
        for k in sorted(cur.keys() - old.keys(), key=str):
            row["new"].append((name(cur[k]), link(cur[k]) if link else None))
        for k in sorted(old.keys() - cur.keys(), key=str):
            row["removed"].append((name(old[k]), None))
        for k in sorted(cur.keys() & old.keys(), key=str):
            a, b = _sig(old[k], allowed), _sig(cur[k], allowed)
            # só campos presentes nas duas: campo novo de uma versão mais nova do kit não é mudança no ambiente
            diff = sorted(f for f in a.keys() & b.keys() if a[f] != b[f])
            if diff:
                row["changed"].append((name(cur[k]), link(cur[k]) if link else None,
                                       [LABELS.get(f, f) for f in diff]))
        out["types"].append(row)
    old_f, cur_f = {f["id"]: f for f in p_fnd}, {f["id"]: f for f in fnd}

    def comparable(fid):
        srcs = FINDING_SOURCES_BY_ID.get(fid) or FINDING_SOURCES.get(fid.split("-")[0], ())
        return all(_pair_ok(m, pm, c) for c in srcs)

    for fid in sorted(old_f.keys() | cur_f.keys(), key=lambda i: (findings_mod.SEV_ORDER.get(
            (cur_f.get(i) or old_f.get(i))["severity"], 9), i)):
        a, b = old_f.get(fid), cur_f.get(fid)
        ea, eb = set(map(str, (a or {}).get("evidence") or [])), set(map(str, (b or {}).get("evidence") or []))
        status = ("não comparável" if not comparable(fid) else "novo" if a is None else "resolvido" if b is None else
                  "piorou" if len(eb) > len(ea) else "melhorou" if len(eb) < len(ea) else
                  "mudou" if ea != eb else "igual")
        sev = (b or a)["severity"]
        if status == "não comparável" or sev == "info":  # contagens informativas (ex. dependentes) não são correção
            ea = eb = set()
            if status not in ("não comparável", "novo", "resolvido"):
                status = "igual"
        out["findings"].append({"id": fid, "title": (b or a)["title"], "severity": (b or a)["severity"],
                                "status": status, "before": len(ea) if a else 0, "after": len(eb) if b else 0,
                                "fixed": sorted(ea - eb), "added": sorted(eb - ea)})
    return out


def summary(chg):
    """→ (novos, removidos, alterados, achados resolvidos, achados novos)."""
    t = [r for r in chg.get("types") or [] if r["comparable"]]
    f = chg.get("findings") or []
    return (sum(len(r["new"]) for r in t), sum(len(r["removed"]) for r in t), sum(len(r["changed"]) for r in t),
            sum(1 for x in f if x["status"] == "resolvido"), sum(1 for x in f if x["status"] == "novo"))
