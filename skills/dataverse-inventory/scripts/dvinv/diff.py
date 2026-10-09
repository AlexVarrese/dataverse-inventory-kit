"""Compara dois snapshots (mesmo ambiente em datas diferentes, ou DEV × TEST × PRD).

Cada lado pode ser: diretório de snapshot (`_raw/<run_id>`), raiz de snapshots (`_raw` → último
íntegro), run_id (relativo ao raw_dir da config) ou diretório no formato antigo.
Identidade de componente = nome lógico/único (não GUID), porque GUIDs mudam entre ambientes
para componentes recriados. Gera uma nota Obsidian com o que existe só de um lado.
As dependências são recalculadas a partir do snapshot (não dependem de saída do render).
"""

import os
from datetime import date
from pathlib import Path

from . import dependencies as deps_mod
from . import secrets
from .render.obsidian import load_raw, mdtable, safe


def _with_dependencies(d):
    g = deps_mod.build(d)
    d["dependencies"] = deps_mod.to_json(g, deps_mod.summarize(g, d))
    return d


def keys(d):
    k = {}
    k["Tabelas"] = {t["logical"] for t in d.get("tables") or []}
    k["Colunas customizadas"] = {f"{t['logical']}.{c['logical']}" for t in d.get("tables") or []
                                  for c in t.get("columns") or [] if c.get("custom")}
    k["Relacionamentos"] = {r["schema"] for r in d.get("relationships") or []}
    k["Formulários"] = {f"{f['entity']}/{f['name']}" for f in d.get("forms") or []}
    k["Web resources"] = {w["name"] for w in d.get("webresources") or []}
    k["Plugin assemblies"] = {f"{a['name']} v{a.get('version')}" for a in (d.get("plugins") or {}).get("assemblies") or []}
    k["Plugin steps"] = {f"{s.get('type')} | {s['message']} | {s.get('entity')} | {s['stage']}"
                         for s in (d.get("plugins") or {}).get("steps") or []}
    k["Processos"] = {f"{p['category']}: {p['name']}" for p in d.get("processes") or []}
    k["Processos ativos"] = {f"{p['category']}: {p['name']}" for p in d.get("processes") or [] if p.get("active")}
    k["Custom APIs"] = {a["unique"] for a in d.get("customapis") or []}
    alm = d.get("alm") or {}
    k["Variáveis de ambiente"] = {e["name"] for e in alm.get("envvars") or []}
    k["Referências de conexão"] = {r["name"] for r in alm.get("connrefs") or []}
    k["Papéis"] = {r["name"] for r in (d.get("security") or {}).get("roles") or [] if r.get("scope_reason")}
    dep = d.get("dependencies") or {}
    k["Dependências"] = {f"{e['from_label']} → {e['to_label']} ({e['relation']})" for e in dep.get("edges") or []}
    k["Soluções"] = {f"{s['uniquename']} v{s.get('version')}" for s in d.get("solutions") or []
                     if s.get("managed") is False or s.get("in_scope")}
    return k


def compare(raw_a, raw_b, vault_root, folder, root=None):
    a, b = _with_dependencies(load_raw(raw_a, root)), _with_dependencies(load_raw(raw_b, root))
    na = (a.get("manifest") or {}).get("environment", "A")
    nb = (b.get("manifest") or {}).get("environment", "B")
    ta = (a.get("manifest") or {}).get("extracted_at", "")[:10]
    tb = (b.get("manifest") or {}).get("extracted_at", "")[:10]
    ka, kb = keys(a), keys(b)
    if na == nb:  # mesmo ambiente em dois momentos: o rótulo precisa da data/hora para distinguir os lados
        ta = (a.get("manifest") or {}).get("extracted_at", "")[:16].replace("T", " ")
        tb = (b.get("manifest") or {}).get("extracted_at", "")[:16].replace("T", " ")
    la, lb = f"{na} {ta}", f"{nb} {tb}"
    lines = [f"# Comparação {na} ({ta}) × {nb} ({tb})", "",
             mdtable(["Tipo", la, lb, f"Só em {la}", f"Só em {lb}"],
                     [[t, len(ka[t]), len(kb[t]), len(ka[t] - kb[t]), len(kb[t] - ka[t])] for t in ka])]
    for t in ka:
        only_a, only_b = sorted(ka[t] - kb[t]), sorted(kb[t] - ka[t])
        if not (only_a or only_b):
            continue
        lines += [f"## {t}", ""]
        if only_a:
            lines += [f"> [!minus]- Só em {la} ({len(only_a)})"] + [f"> - `{x}`" for x in only_a] + [""]
        if only_b:
            lines += [f"> [!plus]- Só em {lb} ({len(only_b)})"] + [f"> - `{x}`" for x in only_b] + [""]
    title = safe(f"Comparação {na} {ta} x {nb} {tb}")
    path = Path(vault_root) / folder / "Comparações" / f"{title}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = (f"---\ntipo: comparacao\nambiente_a: {na}\nambiente_b: {nb}\ndata_a: {ta}\ndata_b: {tb}\n"
          f"extraido_em: {date.today().isoformat()}\ntags:\n  - dataverse\n  - dataverse/comparacao\n---\n")
    tmp = path.with_name(f".tmp-{os.getpid()}-{path.name}")  # mesma extensão: a varredura reconhece
    tmp.write_text(fm + "\n".join(lines) + "\n", encoding="utf-8")
    try:
        secrets.assert_clean([tmp], "diff")
    except secrets.SecretLeakError:
        tmp.unlink()
        raise
    os.replace(tmp, path)
    return path
