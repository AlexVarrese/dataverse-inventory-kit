"""Regra única de escopo: o que é 'componente do cliente' neste ambiente.

Um componente entra no escopo se qualquer critério bater (motivo fica registrado):
  solution  — pertence a uma das soluções listadas em scope.solutions
  prefix    — nome lógico começa com um prefixo de publisher (ex. contoso_)
  keyword   — nome contém a palavra-chave (ex. 'contoso' em nomes de processos/assemblies)
  unmanaged — componente não gerenciado (customização feita direto no ambiente)
  all       — scope.all: true

scope.exclude_prefixes descarta componentes por prefixo de nome (ex. bibliotecas de terceiros não
gerenciadas), exceto os que pertencem a uma solução do escopo. Cada decisão positiva é contada por
motivo × prefixo em `breakdown` — vai para o manifest e para `01 Ambiente`, para o analista ver de onde
veio o volume e ajustar o escopo.
"""

import re
from collections import defaultdict

_PREFIX_RE = re.compile(r"^([a-z0-9]+_)")


class Scope:
    def __init__(self, cfg):
        self.cfg = cfg
        self.solution_objects = set()   # objectids dos componentes das soluções do escopo
        self.membership = {}            # objectid -> [uniquename de soluções não-sistema]
        self.tables = set()             # logical names das tabelas no escopo (preenchido pelo coletor de tabelas)
        self._seen = defaultdict(set)   # (motivo, prefixo) -> nomes únicos

    def reason(self, name=None, managed=None, objectid=None, keyword_match=True):
        r = self._reason(name, managed, objectid, keyword_match)
        if r and name:
            n = name.lower()
            self._seen[(r, (_PREFIX_RE.match(n) or [None, "(sem prefixo)"])[1])].add(n)
        return r

    def _reason(self, name, managed, objectid, keyword_match):
        cfg = self.cfg
        n = (name or "").lower()
        if objectid and objectid.lower() in self.solution_objects:
            return "solution"
        if cfg.exclude_prefixes and any(n.startswith(p) for p in cfg.exclude_prefixes):
            return None
        if cfg.prefixes and any(n.startswith(p) for p in cfg.prefixes):
            return "prefix"
        if keyword_match and cfg.keywords and any(k in n for k in cfg.keywords):
            return "keyword"
        if cfg.include_unmanaged and managed is False:
            return "unmanaged"
        if cfg.scope_all:
            return "all"
        return None

    def breakdown(self):
        out = defaultdict(dict)
        for (r, p), names in self._seen.items():
            out[r][p] = len(names)
        return {r: dict(sorted(v.items(), key=lambda kv: -kv[1])) for r, v in out.items()}

    def solutions_of(self, objectid):
        return self.membership.get((objectid or "").lower(), [])
