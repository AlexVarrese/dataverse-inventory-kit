"""Cache SQLite opcional do inventário (`render --sqlite` ou `output.sqlite: true`), para SQL ad hoc.

O produto é o vault: as consultas de impacto (`dvinv onde-usa/impacto/depende/buscar`) leem
Componentes.csv e Dependências.csv do próprio vault (consulta.py) e funcionam em qualquer clone do
repositório. Este banco acrescenta o que não vai para o vault — TODAS as arestas da plataforma, inclusive
nativo → nativo e as internas de metadados — para análises em SQL na máquina de quem extraiu.

Fica em `_derived/<run_id>/inventario.sqlite`, FORA do vault (binário de centenas de MB, regenerável,
não deve ir para o Git) e tem a mesma sensibilidade do vault. Antes de publicar, todo texto do banco passa
pela varredura de segredos (fail-closed, como os demais arquivos gerados).

Tabelas:
  tipos(ct, nome, fonte, tabela)
  componentes(id, chave, guid, ct, tipo, nome, pai, no_escopo, nota)
      chave = "<tipo do kit>:<nome>" para o que o kit conhece (table:account, column:account.x,
      webresource:..., process:<guid>...) ou o GUID para o restante do catálogo
  solucoes(id, uniquename, nome, versao, gerenciada, publisher, prefixo, no_escopo)
  componente_solucao(componente, solucao, comportamento)
  dependencias(origem, destino, relacao, fonte, interna, detalhe)   origem DEPENDE DE destino
  achados(id, severidade, titulo, regra, componente)
"""

import sqlite3
from pathlib import Path

from . import secrets

DB_NAME = "inventario.sqlite"
SCHEMA_VERSION = "1"
INTERNAL = {"Solution Internal"}  # pai/filho de metadados (coluna → tabela): ruído para impacto

DDL = """
CREATE TABLE meta(chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE tipos(ct INTEGER PRIMARY KEY, nome TEXT, fonte TEXT, tabela TEXT);
CREATE TABLE componentes(id INTEGER PRIMARY KEY, chave TEXT UNIQUE NOT NULL, guid TEXT, ct INTEGER, tipo TEXT,
                         nome TEXT, pai TEXT, no_escopo INTEGER DEFAULT 0, nota TEXT);
CREATE TABLE solucoes(id TEXT PRIMARY KEY, uniquename TEXT, nome TEXT, versao TEXT, gerenciada INTEGER,
                      publisher TEXT, prefixo TEXT, no_escopo INTEGER);
CREATE TABLE componente_solucao(componente INTEGER, solucao TEXT, comportamento INTEGER);
CREATE TABLE dependencias(origem INTEGER, destino INTEGER, relacao TEXT, fonte TEXT, interna INTEGER, detalhe TEXT);
CREATE TABLE achados(id TEXT, severidade TEXT, titulo TEXT, regra TEXT, componente INTEGER);
"""
INDEXES = """
CREATE INDEX ix_comp_guid ON componentes(guid);
CREATE INDEX ix_comp_nome ON componentes(nome COLLATE NOCASE);
CREATE INDEX ix_dep_destino ON dependencias(destino);
CREATE INDEX ix_dep_origem ON dependencias(origem);
CREATE INDEX ix_cs_comp ON componente_solucao(componente);
CREATE INDEX ix_ach_comp ON achados(componente);
"""


class _Builder:
    def __init__(self, con):
        self.con = con
        self.ids = {}       # chave -> id
        self.by_guid = {}   # guid -> chave

    def comp(self, chave, **kw):
        cid = self.ids.get(chave)
        if cid is None:
            cur = self.con.execute(
                "INSERT INTO componentes(chave, guid, ct, tipo, nome, pai, no_escopo, nota) VALUES (?,?,?,?,?,?,?,?)",
                (chave, kw.get("guid"), kw.get("ct"), kw.get("tipo"), kw.get("nome"), kw.get("pai"),
                 int(bool(kw.get("no_escopo"))), kw.get("nota")))
            cid = self.ids[chave] = cur.lastrowid
        else:  # completa campos vazios (o catálogo dá ct/guid a um nó que o kit criou, e vice-versa)
            sets = {k: v for k, v in kw.items() if v is not None and k in ("guid", "ct", "tipo", "nome", "pai", "nota")}
            if sets:
                cols = ", ".join(f"{k} = COALESCE({k}, ?)" for k in sets)
                self.con.execute(f"UPDATE componentes SET {cols} WHERE id = ?", (*sets.values(), cid))
            if kw.get("no_escopo"):
                self.con.execute("UPDATE componentes SET no_escopo = 1 WHERE id = ?", (cid,))
        if kw.get("guid"):
            self.by_guid.setdefault(kw["guid"], chave)
        return cid


def build(path, d, graph, v, fnd):
    """Grava o índice em `path`. d = snapshot carregado; graph = dependencies.build(d); v = Vault (caminhos)."""
    from .dependencies import DEP_TYPE, KIND_LABEL, structural_teams

    path = Path(path)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.executescript(DDL)
    b = _Builder(con)
    man = d.get("manifest") or {}
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("schema", SCHEMA_VERSION), ("ambiente", man.get("environment")), ("url", man.get("url")),
        ("run_id", man.get("run_id")), ("extraido_em", man.get("extracted_at"))])

    cat = d.get("catalog") or {}
    types = cat.get("types") or {}
    con.executemany("INSERT INTO tipos VALUES (?,?,?,?)",
                    [(int(k), t.get("name"), t.get("source"), t.get("table")) for k, t in types.items()])

    def tipo_de(ct):
        return (types.get(str(ct)) or {}).get("name") or (str(ct) if ct is not None else None)

    def nota(n):
        rel = v.paths.get(n)
        return f"{rel.split('#')[0]}.md" if rel else None

    # 1) nós do kit (escopo): chave legível + nota
    guid_of = {}
    for (kind, key), oid in (getattr(graph, "guid_index", {}) or {}).items():
        guid_of[(kind, key)] = oid
    for n, node in graph.nodes.items():
        kind, key = n
        if kind == "platform":
            continue
        b.comp(f"{kind}:{key}", guid=guid_of.get(n), tipo=KIND_LABEL.get(kind, kind), nome=node.get("label"),
               pai=node.get("sub") if kind in ("column", "form", "view") else None, no_escopo=True, nota=nota(n))

    # 2) catálogo (100%): GUID → chave do kit quando houver; tabela/coluna pelo nome lógico
    names = cat.get("names") or {}

    def canon(ct, oid):
        if oid in b.by_guid:
            return b.by_guid[oid]
        nm = names.get(oid)
        if nm and nm[1]:
            if nm[0] == 1:
                return f"table:{nm[1]}"
            if nm[0] == 2:
                return f"column:{nm[1]}"
        return oid

    def ensure(ct, oid):
        chave = canon(ct, oid)
        if chave in b.ids:
            if chave != oid:
                b.comp(chave, guid=oid, ct=ct, tipo=tipo_de(ct))
            return b.ids[chave]
        nm = names.get(oid) or [ct, None, None]
        # chave = GUID: não grava o GUID de novo (eram ~600 mil linhas com o valor duplicado e indexado duas vezes)
        return b.comp(chave, guid=oid if chave != oid else None, ct=ct if ct is not None else nm[0],
                      tipo=tipo_de(ct if ct is not None else nm[0]), nome=nm[1], pai=nm[2])

    scope_sols = set()
    for s in d.get("solutions") or []:
        con.execute("INSERT OR REPLACE INTO solucoes VALUES (?,?,?,?,?,?,?,?)",
                    (s["id"].lower(), s["uniquename"], s.get("name"), s.get("version"), int(bool(s.get("managed"))),
                     s.get("publisher"), s.get("publisher_prefix"), int(bool(s.get("in_scope")))))
        if s.get("in_scope"):
            scope_sols.add(s["id"].lower())
    rows = []
    for ct, oid, sid, beh in cat.get("members") or []:
        cid = ensure(ct, oid)
        rows.append((cid, sid, beh))
        if sid in scope_sols:
            con.execute("UPDATE componentes SET no_escopo = 1 WHERE id = ?", (cid,))
    con.executemany("INSERT INTO componente_solucao VALUES (?,?,?)", rows)

    # 3) dependências: plataforma (conjunto completo) + inferidas pelo kit
    pd = d.get("platform_dependencies") or {}
    dep_rows = []
    if pd.get("format") == "compacto/1":
        nodes = pd.get("nodes") or []
        nid = [None] * len(nodes)
        for i, (ct, oid) in enumerate(nodes):
            nid[i] = ensure(ct, oid)
        for a, bb, dt in pd.get("edges") or []:
            lbl = DEP_TYPE.get(dt, str(dt))
            dep_rows.append((nid[a], nid[bb], f"plataforma ({lbl})", "plataforma", int(lbl in INTERNAL), None))
    for (s, t, rel), e in graph.edges.items():
        src_platform = "plataforma" in e["source"]
        if pd.get("format") == "compacto/1" and src_platform and "inferida" not in e["source"]:
            continue  # já entrou pelo conjunto completo
        ks = s[1] if s[0] == "platform" else f"{s[0]}:{s[1]}"
        kt = t[1] if t[0] == "platform" else f"{t[0]}:{t[1]}"
        for k, n in ((ks, s), (kt, t)):
            if k not in b.ids:
                node = graph.nodes[n]
                b.comp(k, tipo=node.get("sub") if n[0] == "platform" else KIND_LABEL.get(n[0], n[0]),
                       nome=node.get("label"))
        fonte = "inferida" if "inferida" in e["source"] else "plataforma"
        dep_rows.append((b.ids[ks], b.ids[kt], rel, fonte, 0, e.get("detail") if isinstance(e.get("detail"), str) else None))
    # equipes fora do grafo (org com dezenas de milhares de equipes de proprietário): todas entram no índice
    for t in structural_teams((d.get("security") or {})):
        k = f"team:{t['id']}"
        if k in b.ids:
            continue
        tid = b.comp(k, tipo=KIND_LABEL["team"], nome=t.get("name"), no_escopo=True)
        for rn in t.get("roles") or []:
            if f"role:{rn}" in b.ids:
                dep_rows.append((tid, b.ids[f"role:{rn}"], "recebe papel", "inferida", 0, None))
        if t.get("bu_id") and f"bu:{t['bu_id']}" in b.ids:
            dep_rows.append((tid, b.ids[f"bu:{t['bu_id']}"], "pertence a", "inferida", 0, None))
    con.executemany("INSERT INTO dependencias VALUES (?,?,?,?,?,?)", dep_rows)

    # 4) achados → componente
    for f in fnd or []:
        refs = f.get("refs") or [[None, None]]
        for kind, key in refs:
            con.execute("INSERT INTO achados VALUES (?,?,?,?,?)",
                        (f["id"], f.get("severity"), f.get("title"), f.get("rule"), b.ids.get(f"{kind}:{key}")))
    con.executescript(INDEXES)
    con.commit()
    con.execute("VACUUM")
    con.close()
    assert_clean_db(path)
    return path


def assert_clean_db(path):
    """Varredura de segredos sobre todo texto do banco (o binário não passa por scan_paths)."""
    con = sqlite3.connect(path)
    hits = []
    try:
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})") if (r[2] or "").upper() == "TEXT"]
            for col in cols:
                buf, size = [], 0
                for (val,) in con.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL"):
                    buf.append(val)
                    size += len(val)
                    if size > 2_000_000:
                        hits += [(f"{path}:{table}.{col}", h) for h in secrets.scan("\n".join(buf))]
                        buf, size = [], 0
                if buf:
                    hits += [(f"{path}:{table}.{col}", h) for h in secrets.scan("\n".join(buf))]
    finally:
        con.close()
    if hits:
        path.unlink(missing_ok=True)
        raise secrets.SecretLeakError(f"índice {path.name}", hits)


# ---------------------------------------------------------------------------------------------
# leitura (SQL ad hoc). As consultas do dia a dia (`onde-usa`, `impacto`…) leem os CSVs do vault: consulta.py

def connect(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con
