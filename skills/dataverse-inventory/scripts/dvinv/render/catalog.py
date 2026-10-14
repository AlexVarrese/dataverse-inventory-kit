"""Notas de navegação para 100% dos componentes e para agentes de IA.

- `09 Catálogo de Componentes.md`: resumo por tipo (todos os tipos do ambiente) + `Componentes.csv`
  com uma linha por componente. Componentes fora do escopo NÃO viram nota (seriam dezenas de milhares
  de notas sem análise): ficam no CSV e no índice SQLite.
- `AGENT-INDEX.md`: ponto de entrada compacto para agentes (Claude, Copilot, Hermes…): onde está
  cada coisa, como consultar o índice e o que nunca ler inteiro.
"""

import csv
import io
from collections import Counter

LARGE_NOTE_BYTES = 300_000  # notas acima disso aparecem no AGENT-INDEX como "não ler inteira"


def _mdtable():
    from .obsidian import mdtable  # import tardio: obsidian importa este módulo
    return mdtable


def write_csv(v, name, rows):
    buf = io.StringIO()
    csv.writer(buf, delimiter=";").writerows(rows)
    v.write(f"{v.folder}/{name}", None, "﻿" + buf.getvalue(), raw=True)


def write_catalog(v, d):
    mdtable = _mdtable()
    cat = d.get("catalog") or {}
    if not cat:
        return False
    types, names = cat.get("types") or {}, cat.get("names") or {}
    sols = {s["id"].lower(): s for s in d.get("solutions") or []}
    per = {}
    for ct, oid, sid, _beh in cat.get("members") or []:
        rec = per.setdefault(oid, {"ct": ct, "sols": []})
        s = sols.get(sid)
        if s:
            rec["sols"].append(s)
    kit = getattr(v, "graph", None)
    kit_guid = {g: n for n, g in (getattr(kit, "guid_index", {}) or {}).items()} if kit else {}

    def tname(ct):
        return (types.get(str(ct)) or {}).get("name") or f"tipo {ct}"

    by_type, in_scope, managed_only, with_note = Counter(), Counter(), Counter(), Counter()
    rows = [["Tipo", "Código do tipo", "Nome", "Pai", "Id", "No escopo", "Só em soluções gerenciadas",
             "Soluções", "Nota"]]
    for oid, rec in per.items():
        ct = rec["ct"]
        nm = names.get(oid) or [ct, None, None]
        scoped = any(s.get("in_scope") for s in rec["sols"])
        only_managed = bool(rec["sols"]) and all(s.get("managed") for s in rec["sols"])
        node = kit_guid.get(oid)
        rel = v.paths.get(node) if node else None
        by_type[ct] += 1
        in_scope[ct] += scoped
        managed_only[ct] += only_managed
        with_note[ct] += bool(rel)
        rows.append([tname(ct), ct, nm[1] or "", nm[2] or "", oid, "sim" if scoped else "", "sim" if only_managed else "",
                     ", ".join(sorted({s["uniquename"] for s in rec["sols"]})[:15]), (rel.split("#")[0] + ".md") if rel else ""])
    rows[1:] = sorted(rows[1:], key=lambda r: (r[0], r[2].lower(), r[4]))
    write_csv(v, "Componentes.csv", rows)

    st = (d.get("manifest") or {}).get("stats", {}).get("catalog") or {}
    b = ["# Catálogo de componentes", "",
         f"**{len(per)}** componentes distintos em **{len(by_type)}** tipos, de todas as soluções do ambiente "
         f"(inclusive as gerenciadas da Microsoft) · {st.get('components_named', '?')} com nome · lista completa em "
         f"[[{v.folder}/Componentes.csv|Componentes.csv]] e no índice SQLite (`dvinv buscar`, `dvinv impacto`).", "",
         "> [!info] Por que nem todo componente tem nota",
         "> Só o que está no **escopo** (prefixos/soluções do cliente) vira nota com análise. O restante fica no "
         "CSV e no índice — assim o vault continua leve para o Obsidian e para a IA, sem perder nenhum componente.", "",
         mdtable(["Tipo", "Código", "Componentes", "No escopo", "Só gerenciado", "Com nota", "Fonte do nome do tipo"],
                 [[tname(ct), ct, n, in_scope[ct] or "", managed_only[ct] or "", with_note[ct] or "",
                   (types.get(str(ct)) or {}).get("source") or "—"]
                  for ct, n in sorted(by_type.items(), key=lambda kv: -kv[1])])]
    v.write(f"{v.folder}/09 Catálogo de Componentes", {"tipo": "secao", "componentes": len(per),
                                                         "tipos": len(by_type), "tags": v.tags("secao")}, "\n".join(b))
    return True


def write_agent_index(v, d, has_catalog):
    """AGENT-INDEX.md: entrada para agentes. Curto de propósito (lido inteiro em toda consulta)."""
    mdtable = _mdtable()
    folders = Counter()
    for rel in v.paths.values():
        parts = rel.split("#")[0][len(v.folder) + 1:].split("/")
        if len(parts) > 1:
            folders[parts[0] if parts[0] != "Processos" else "/".join(parts[:2])] += 1
    big = sorted({(p, p.stat().st_size) for p in v.written
                  if str(p).endswith(".md") and p.exists() and p.stat().st_size > LARGE_NOTE_BYTES},
                 key=lambda x: -x[1])
    run_id = getattr(v, "run_id", None)
    b = [f"# Índice para agentes — {v.env}", "",
         "Entrada compacta para agentes de IA. **Leia este arquivo primeiro** e abra só o que a pergunta exigir.", "",
         "## Regra de leitura econômica", "",
         "1. Pergunta estrutural (impacto, onde é usado, do que depende, existe X?) → **consulte o índice**, não leia notas:",
         "   - `python3 <skill>/scripts/dvinv.py buscar <texto> -c inventory.yaml` — encontra o componente e mostra a chave",
         "   - `python3 <skill>/scripts/dvinv.py onde-usa <tabela.coluna | nome | chave> -c inventory.yaml` — dependentes diretos",
         "   - `python3 <skill>/scripts/dvinv.py impacto <alvo> --profundidade 2 -c inventory.yaml` — cadeia de impacto",
         "   - `python3 <skill>/scripts/dvinv.py depende <alvo> -c inventory.yaml` — do que o componente precisa",
         "2. Depois, abra **só as notas** que o resultado indicar (coluna `nota`).",
         "3. Números e escopo: `00 Índice.md`; lacunas da coleta: `01 Ambiente.md` (seção *Lacunas declaradas*).",
         "4. Nunca carregue de uma vez os arquivos da lista *Não ler inteiro*; use busca por termo (grep) dentro deles.",
         "5. Abaixo do marcador `%% dvinv:manual %%` de cada nota está a curadoria humana: é a fonte de decisões e "
         "validações — não sobrescreva.", "",
         "## Mapa do vault", "",
         mdtable(["Pasta", "Notas", "Conteúdo"],
                 [[f"`{k}/`", n, "uma nota por componente do escopo"] for k, n in sorted(folders.items())]),
         "Notas de seção: `00 Índice`, `01 Ambiente`, `03 Integrações`, `04 Segurança`, `05 Uso de Campos`, "
         "`06 Armazenamento e Auditoria`, `07 Repositórios`, `08 Matriz de Dependências`"
         + (", `09 Catálogo de Componentes`" if has_catalog else "") + ". Properties: campo `tipo:` em todo frontmatter "
         "(ex. `grep -rl '^tipo: tabela' .`).", "",
         "## Fontes fora do vault", "",
         f"- Índice SQLite: `_derived/{run_id or '<run_id>'}/inventario.sqlite` (o `dvinv` acha o mais recente sozinho).",
         f"- Snapshot bruto: `_raw/{run_id or '<run_id>'}/*.json` — inclui código de web resources e definições de "
         "flows/processos, quando coletados. Use grep, não leitura integral.",
         *(["- `Componentes.csv`: 1 linha por componente do ambiente (100%)."] if has_catalog else []),
         "- `Dependências.csv` / `Matriz de Dependências.xlsx`: arestas do escopo (o conjunto completo está no índice).", ""]
    if big:
        b += ["## Não ler inteiro", "", mdtable(["Arquivo", "Tamanho"],
              [[f"`{p.relative_to(v.root / v.folder).as_posix()}`", f"{s // 1024} KB"] for p, s in big[:30]])]
    env = d.get("environment") or {}
    v.write(f"{v.folder}/AGENT-INDEX", {"tipo": "indice-agente", "snapshot": run_id, "url": env.get("url"),
                                        "config": getattr(v, "config_rel", None), "tags": v.tags("indice")},
            "\n".join(b))
