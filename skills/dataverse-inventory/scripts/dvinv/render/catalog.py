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
    csv.writer(buf, delimiter=";", lineterminator="\n").writerows(rows)  # LF: diff igual em Windows e Linux
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
    from ..dependencies import canonical_key
    rows = [["Chave", "Tipo", "Código do tipo", "Nome", "Pai", "Id", "No escopo", "Só em soluções gerenciadas",
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
        chave = canonical_key(node, names) if node else canonical_key(("platform", oid), names)
        rows.append([chave, tname(ct), ct, nm[1] or "", nm[2] or "", oid, "sim" if scoped else "", "sim" if only_managed else "",
                     ", ".join(sorted({s["uniquename"] for s in rec["sols"]})[:15]), (rel.split("#")[0] + ".md") if rel else ""])
    rows[1:] = sorted(rows[1:], key=lambda r: (r[1], r[3].lower(), r[5]))
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
         "Entrada compacta para agentes de IA (Claude, Copilot, Hermes…) e para quem consulta o repositório. "
         "**Leia este arquivo primeiro** e abra só o que a pergunta exigir. Caminhos abaixo são relativos a esta pasta.", "",
         "## 1. Pergunta estrutural → índice textual (não leia notas)", "",
         "Impacto, onde é usado, do que depende, existe X? Os CSVs desta pasta respondem com poucas linhas:", "",
         "| Pergunta | grep (qualquer agente/terminal) | dvinv (mais legível, faz vários níveis) |",
         "|---|---|---|",
         "| Existe? Qual a chave/GUID? | `grep -i \"esab_campo\" Componentes.csv` | `dvinv.py buscar esab_campo --vault .` |",
         "| Quem depende de X? | `grep \";column:account.esab_campo;\" Dependências.csv` | `dvinv.py onde-usa account.esab_campo --vault .` |",
         "| Impacto em cadeia | repetir o grep para cada origem | `dvinv.py impacto account.esab_campo --profundidade 2 --vault .` |",
         "| Do que X depende? | `grep \"^webresource:esab_/js/x.js;\" Dependências.csv` | `dvinv.py depende esab_/js/x.js --vault .` |",
         "| Achei um GUID num log/trace | `grep -rl \"id: <guid>\" --include=*.md .` | `dvinv.py buscar <guid> --vault .` |", "",
         "Chave = `<tipo>:<nome>` para o que tem nota (`table:account`, `column:account.x`, `webresource:…`, "
         "`process:<guid>`); fora do escopo, `table:`/`column:` pelo nome lógico e o resto pelo GUID. "
         "`dvinv.py` = `<skill>/scripts/dvinv.py` (skill `dataverse-inventory`).", "",
         "## 2. Depois, só as notas indicadas", "",
         "- A coluna `Nota` (CSV) ou `nota` (dvinv) dá o caminho. Cada nota tem `chave` e `id` (GUID) no frontmatter "
         "e a seção **Dependências** (depende de / usado por).",
         "- Números e escopo: `00 Índice.md`; lacunas da coleta: `01 Ambiente.md` (*Lacunas declaradas*).",
         "- Abaixo do marcador `%% dvinv:manual %%` de cada nota está a curadoria humana — fonte de decisões; não sobrescreva.",
         "- Ausência de aresta não prova ausência de uso (integrações externas, Power BI, código fora do Dataverse).", "",
         "## Mapa do vault", "",
         mdtable(["Pasta", "Notas", "Conteúdo"],
                 [[f"`{k}/`", n, "uma nota por componente do escopo"] for k, n in sorted(folders.items())]),
         "Notas de seção: `00 Índice`, `01 Ambiente`, `03 Integrações`, `04 Segurança`, `05 Uso de Campos`, "
         "`06 Armazenamento e Auditoria`, `07 Repositórios`, `08 Matriz de Dependências`, `10 Histórico de Soluções`"
         + (", `09 Catálogo de Componentes`" if has_catalog else "") + ". Filtrar por tipo: "
         "`grep -rl '^tipo: tabela' .`", "",
         "## Arquivos tabulares", "",
         *(["- `Componentes.csv`: 1 linha por componente do ambiente (100%, todas as soluções), com chave, GUID e nota."]
           if has_catalog else []),
         "- `Dependências.csv`: 1 linha por aresta que toca o escopo (chave, GUID, tipo, nome e nota das duas pontas).",
         "- `Equipes.csv`: todas as equipes com BU e papéis (inclusive as que não viraram nota).",
         f"- Fora do repositório, na máquina que extraiu: snapshot `_raw/{run_id or '<run_id>'}/*.json` (código de web "
         "resources, definições de flows) e, se ligado, o cache SQLite com todas as arestas da plataforma.", ""]
    if big:
        b += ["## Não ler inteiro", "", mdtable(["Arquivo", "Tamanho"],
              [[f"`{p.relative_to(v.root / v.folder).as_posix()}`", f"{s // 1024} KB"] for p, s in big[:30]])]
    env = d.get("environment") or {}
    v.write(f"{v.folder}/AGENT-INDEX", {"tipo": "indice-agente", "snapshot": run_id, "url": env.get("url"),
                                        "config": getattr(v, "config_rel", None), "tags": v.tags("indice")},
            "\n".join(b))
