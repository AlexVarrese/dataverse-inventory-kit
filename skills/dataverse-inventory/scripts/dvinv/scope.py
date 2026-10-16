"""Regra única de escopo: o que é 'componente do cliente' neste ambiente.

Um componente entra no escopo se qualquer critério bater (motivo fica registrado):
  solution  — pertence a uma das soluções listadas em scope.solutions
  prefix    — nome lógico começa com um prefixo de publisher (ex. contoso_)
  keyword   — nome contém a palavra-chave (ex. 'contoso' em nomes de processos/assemblies)
  unmanaged — componente não gerenciado (customização feita direto no ambiente)
  all       — scope.all: true

scope.exclude_prefixes descarta componentes por prefixo de nome (ex. bibliotecas de terceiros não
gerenciadas), exceto os que pertencem a uma solução do escopo. scope.exclude_microsoft (padrão: ligado) descarta
os prefixos dos publishers da Microsoft (msdyn_, mscrm_, adx_…) mesmo dentro de uma solução do escopo: incluir uma
tabela da Microsoft na solução traz junto web resources dela, que não são customização do cliente. Tabela da
Microsoft com coluna do cliente continua no escopo pelas colunas (motivo custom-columns). Cada decisão positiva é contada por
motivo × prefixo em `breakdown` — vai para o manifest e para `01 Ambiente`, para o analista ver de onde
veio o volume e ajustar o escopo.
"""

import re
from collections import defaultdict

_PREFIX_RE = re.compile(r"^([a-z0-9]+_)")

# Prefixos de publishers da Microsoft (apps de primeira parte: Sales, Service, Field Service, Marketing, Portais,
# USD, Finance & Operations…). Nome de web resource também começa por eles (ex. msdyn_/Scripts/x.js).
MICROSOFT_PREFIXES = ("msdyn_", "msdynce_", "msdyncrm_", "msdynmkt_", "msdynci_", "msdyusd_", "mscrm_", "msevtmgt_",
                      "msfp_", "msnfp_", "msemr_", "mserp_", "msiot_", "mspcat_", "msft_", "adx_", "mspp_", "uii_")


class Scope:
    def __init__(self, cfg):
        self.cfg = cfg
        self.solution_objects = set()   # objectids dos componentes das soluções do escopo
        self.membership = {}            # objectid -> [uniquename de soluções não-sistema]
        self.tables = set()             # logical names das tabelas no escopo (preenchido pelo coletor de tabelas)
        self._seen = defaultdict(set)   # (motivo, prefixo) -> nomes únicos
        self.excluded_microsoft = defaultdict(set)  # prefixo Microsoft -> nomes descartados

    def reason(self, name=None, managed=None, objectid=None, keyword_match=True):
        r = self._reason(name, managed, objectid, keyword_match)
        if r and name:
            n = name.lower()
            self._seen[(r, (_PREFIX_RE.match(n) or [None, "(sem prefixo)"])[1])].add(n)
        return r

    def _reason(self, name, managed, objectid, keyword_match):
        n = (name or "").lower()
        if self.is_microsoft(n):
            if self._base_reason(n, managed, objectid, keyword_match):  # conta só o que entraria sem o filtro
                self.excluded_microsoft[_PREFIX_RE.match(n).group(1)].add(n)
            return None
        return self._base_reason(n, managed, objectid, keyword_match)

    def _base_reason(self, n, managed, objectid, keyword_match):
        cfg = self.cfg
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

    def foreign_column(self, name):
        """Coluna que não é do cliente mesmo sendo "custom" para o Dataverse: app da Microsoft ou prefixo excluído."""
        n = (name or "").lower()
        return self.is_microsoft(n) or any(n.startswith(p) for p in self.cfg.exclude_prefixes or [])

    def is_microsoft(self, name):
        return bool(getattr(self.cfg, "exclude_microsoft", True)) and (name or "").lower().startswith(MICROSOFT_PREFIXES)

    def excluded_summary(self):
        return {p: len(ns) for p, ns in sorted(self.excluded_microsoft.items(), key=lambda kv: -len(kv[1]))}

    def breakdown(self):
        out = defaultdict(dict)
        for (r, p), names in self._seen.items():
            out[r][p] = len(names)
        return {r: dict(sorted(v.items(), key=lambda kv: -kv[1])) for r, v in out.items()}

    def solutions_of(self, objectid):
        return self.membership.get((objectid or "").lower(), [])
