"""Consultas de impacto sobre o próprio vault (Componentes.csv + Dependências.csv).

O vault publicado no repositório é o produto: quem o clona consulta sem `inventory.yaml`, sem `.env` e sem
binário — `dvinv onde-usa X --vault <pasta do ambiente>`. Os mesmos arquivos atendem grep, a busca de código
do GitHub e qualquer agente (Copilot, Claude, Hermes):

    grep ";column:account.contoso_x;" Dependências.csv      # quem depende / do que depende
    grep -i "contoso_x" Componentes.csv                      # existe? tipo, GUID, soluções, nota

Saída compacta (tabela de texto), com o caminho da nota de cada resultado.
"""

import csv
from collections import defaultdict, deque
from pathlib import Path

GUIDISH = set("0123456789abcdef-")


class Inventario:
    def __init__(self, vault_env_dir):
        self.dir = Path(vault_env_dir)
        self.comp = {}                      # chave -> {chave, id, tipo, nome, pai, escopo, nota}
        self.into = defaultdict(list)       # destino -> [(origem, relação, fonte)]
        self.out = defaultdict(list)        # origem -> [(destino, relação, fonte)]
        self._load()

    @staticmethod
    def _rows(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f, delimiter=";")

    def _add(self, chave, **kw):
        c = self.comp.setdefault(chave, {"chave": chave, "id": "", "tipo": "", "nome": "", "pai": "", "escopo": False,
                                         "nota": "", "alias": ""})
        for k, v in kw.items():
            if v and not c.get(k):
                c[k] = v
            elif k == "nome" and v and v != c["nome"] and v not in c["alias"]:
                c["alias"] = (c["alias"] + " | " + v).strip(" |")  # ex. nome único (kit) × nome de exibição (catálogo)
        return c

    def _load(self):
        comps, deps = self.dir / "Componentes.csv", self.dir / "Dependências.csv"
        if not deps.exists():
            raise SystemExit(f"{deps} não encontrado — aponte --vault para a pasta do ambiente (ex. Dataverse/PRD) "
                             "gerada pelo dvinv 0.6+")
        for r in self._rows(deps):
            o = self._add(r["Origem"], id=r["Origem id"], tipo=r["Origem tipo"], nome=r["Origem nome"],
                          nota=r["Nota origem"])
            t = self._add(r["Destino"], id=r["Destino id"], tipo=r["Destino tipo"], nome=r["Destino nome"],
                          nota=r["Nota destino"])
            o["escopo"] = o["escopo"] or r.get("Origem no escopo") == "sim"
            t["escopo"] = t["escopo"] or r.get("Destino no escopo") == "sim"
            self.out[r["Origem"]].append((r["Destino"], r["Relação"], r["Fonte"]))
            self.into[r["Destino"]].append((r["Origem"], r["Relação"], r["Fonte"]))
        if comps.exists():
            for r in self._rows(comps):
                # "No escopo" do catálogo = pertence a solução do escopo (mais amplo que o escopo das notas)
                self._add(r.get("Chave") or r["Id"], id=r.get("Id"), tipo=r.get("Tipo"), nome=r.get("Nome"),
                          pai=r.get("Pai"), nota=r.get("Nota"))
        for c in self.comp.values():
            c["escopo"] = c["escopo"] or bool(c["nota"])

    # ---------- busca ----------
    def find(self, termo, limit=20):
        t = termo.strip()
        tl = t.lower()
        for k in (t, tl, f"column:{t}", f"table:{t}", f"column:{tl}", f"table:{tl}"):
            if k in self.comp:
                return [self.comp[k]]
        if set(tl) <= GUIDISH and len(tl) == 36:
            hit = [c for c in self.comp.values() if c["id"] == tl]
            if hit:
                return hit
        exact = [c for c in self.comp.values() if (c["nome"] or "").lower() == tl
                 or tl in [a.strip().lower() for a in c["alias"].split("|") if a.strip()]]
        if exact:
            return sorted(exact, key=lambda c: not c["escopo"])[:limit]
        part = [c for c in self.comp.values()
                if tl in (c["nome"] or "").lower() or tl in c["chave"].lower() or tl in c["alias"].lower()]
        return sorted(part, key=lambda c: (not c["escopo"], len(c["nome"] or c["chave"])))[:limit]

    def walk(self, chave, direction, depth):
        """BFS: impacto = quem depende (entradas); depende = do que depende (saídas). → {chave: (nível, relação)}"""
        edges = self.into if direction == "impacto" else self.out
        seen, q = {chave: (0, None)}, deque([(chave, 0)])
        while q:
            cur, lvl = q.popleft()
            if lvl >= depth:
                continue
            for other, rel, _fonte in edges.get(cur, []):
                if other not in seen:
                    seen[other] = (lvl + 1, rel if lvl == 0 else None)
                    q.append((other, lvl + 1))
                elif lvl == 0 and seen[other][0] == 1:
                    seen[other] = (1, ", ".join(sorted({seen[other][1] or "", rel} - {""})))
        seen.pop(chave)
        return seen


def _fmt(c):
    nome = (c["nome"] or c["chave"]).replace("|", "/")
    return f"{nome} [{c['tipo'] or '?'}]" + (" ·escopo" if c["escopo"] else "")


def _choose(inv, termo, out):
    rows = inv.find(termo)
    if not rows:
        out.append(f"nada encontrado para '{termo}' — tente `buscar`")
        return None
    if len(rows) == 1:
        return rows[0]
    out.append(f"'{termo}' é ambíguo — use a chave:")
    out += [f"  {c['chave']}  {_fmt(c)}" for c in rows]
    return None


def cmd_buscar(inv, termo, limit=30):
    out = []
    for c in inv.find(termo, limit):
        out.append(f"{c['chave']} | {_fmt(c)} | dependentes {len(inv.into.get(c['chave'], []))} · "
                   f"depende de {len(inv.out.get(c['chave'], []))}" + (f" | {c['nota']}" if c["nota"] else "")
                   + (f" | id {c['id']}" if c["id"] and c["id"] != c["chave"] else ""))
    return "\n".join(out) or f"nada encontrado para '{termo}'"


def cmd_walk(inv, termo, direction, depth=1, limit=150):
    out = []
    alvo = _choose(inv, termo, out)
    if alvo is None:
        return "\n".join(out)
    hits = inv.walk(alvo["chave"], direction, depth)
    head = (f"# {'Impacto' if direction == 'impacto' else 'Dependências'} — {_fmt(alvo)} ({alvo['chave']})"
            + (f"\nnota: {alvo['nota']}" if alvo["nota"] else ""))
    if not hits:
        return head + f"\nnenhum componente {'depende deste' if direction == 'impacto' else 'exigido'} (profundidade {depth})"
    rows = sorted(((lvl, inv.comp[k], rel) for k, (lvl, rel) in hits.items()),
                  key=lambda x: (x[0], not x[1]["escopo"], x[1]["tipo"] or "", x[1]["nome"] or ""))
    resumo = defaultdict(int)
    for lvl, c, _ in rows:
        resumo[(lvl, c["tipo"] or "?")] += 1
    out = [head, f"{len(rows)} componente(s) — {'quem depende' if direction == 'impacto' else 'do que depende'} "
                 f"(profundidade {depth})", "", "nível | tipo | qtde"]
    out += [f"{n} | {t} | {q}" for (n, t), q in sorted(resumo.items(), key=lambda x: (x[0][0], -x[1]))]
    out += ["", "nível | componente | relação | nota"]
    for lvl, c, rel in rows[:limit]:
        out.append(f"{lvl} | {_fmt(c)} | {(rel or '(indireto)')[:120]} | {c['nota'] or '—'}")
    if len(rows) > limit:
        out.append(f"… +{len(rows) - limit} (use --limite; os do escopo vêm primeiro)")
    return "\n".join(out)
