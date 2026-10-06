"""Regra única de escopo: o que é 'componente do cliente' neste ambiente.

Um componente entra no escopo se qualquer critério bater (motivo fica registrado):
  solution  — pertence a uma das soluções listadas em scope.solutions
  prefix    — nome lógico começa com um prefixo de publisher (ex. contoso_)
  keyword   — nome contém a palavra-chave (ex. 'contoso' em nomes de processos/assemblies)
  unmanaged — componente não gerenciado (customização feita direto no ambiente)
  all       — scope.all: true
"""


class Scope:
    def __init__(self, cfg):
        self.cfg = cfg
        self.solution_objects = set()   # objectids dos componentes das soluções do escopo
        self.membership = {}            # objectid -> [uniquename de soluções não-sistema]
        self.tables = set()             # logical names das tabelas no escopo (preenchido pelo coletor de tabelas)

    def reason(self, name=None, managed=None, objectid=None, keyword_match=True):
        cfg = self.cfg
        n = (name or "").lower()
        if objectid and objectid.lower() in self.solution_objects:
            return "solution"
        if cfg.prefixes and any(n.startswith(p) for p in cfg.prefixes):
            return "prefix"
        if keyword_match and cfg.keywords and any(k in n for k in cfg.keywords):
            return "keyword"
        if cfg.include_unmanaged and managed is False:
            return "unmanaged"
        if cfg.scope_all:
            return "all"
        return None

    def solutions_of(self, objectid):
        return self.membership.get((objectid or "").lower(), [])
