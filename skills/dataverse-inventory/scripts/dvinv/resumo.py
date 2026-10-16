"""Números de cada etapa da coleta: quantos existem na org, quantos no escopo e o que foi processado.

Usado no console/log (uma linha ao fim de cada coletor + tabela no fim da extração) e na nota `01 Ambiente`.
"""

ORG_KEYS = ("org_total", "org_definitions", "org_assemblies", "components")
SCOPE_KEYS = ("scope", "assemblies")
LABELS = {
    "unmanaged": "não gerenciadas", "rows": "linhas", "types": "tipos", "types_unnamed": "tipos sem nome",
    "components_named": "com nome", "names_total": "nomes lidos", "custom": "custom",
    "native_customized": "nativas customizadas", "columns_custom": "colunas custom", "providers": "provedores",
    "providers_used": "provedores em uso", "elastic_org_total": "elásticas na org", "org_1n": "1:N na org",
    "org_nn": "N:N na org", "used_in_forms": "usados em formulário", "modeldriven": "model-driven",
    "canvas": "canvas", "bots": "agentes", "org_types": "classes na org", "org_custom_steps": "steps na org",
    "steps": "steps no escopo", "envvars": "variáveis de ambiente", "connrefs": "referências de conexão",
    "connectors": "conectores", "by_category": "por categoria", "business_units": "business units",
    "roles_root_bu": "papéis (BU raiz)", "roles_scope": "papéis no escopo", "role_rows_all_bus": "papéis em todas as BUs",
    "teams": "equipes", "users_active": "usuários ativos", "tables": "tabelas", "fields": "campos",
    "safe_candidates": "candidatos a investigação", "inconclusive": "inconclusivos", "source": "fonte",
    "queried": "consultados um a um", "edges": "dependências", "nodes": "componentes envolvidos", "failed": "falhas",
    "missing": "ausentes nas soluções", "annotations": "tabelas com anexos", "email_attachments": "anexos de e-mail",
    "audit_records": "registros de auditoria", "content_downloaded": "conteúdo baixado",
    "content_reused": "conteúdo reaproveitado", "content_skipped": "conteúdo ignorado", "subareas": "subáreas",
    "items": "itens", "active": "ativos", "buttons": "botões que chamam JS do escopo", "files": "arquivos",
    "secret_hits": "possíveis segredos", "formula_columns": "colunas calculadas/rollup/fórmula",
    "custom_fields": "campos customizados", "failures": "com falha", "since": "desde", "javascript": "com JavaScript",
    "active_on_top": "com camada Active por cima", "microsoft_skipped": "tabelas da Microsoft puladas",
}
# mesmo nome de métrica com sentido diferente por coletor
LABELS_BY = {"plugins": {"types": "classes no escopo"}, "ribbons": {"scope": "tabelas consultadas"}}


def num(v):
    return f"{v:,}".replace(",", ".") if isinstance(v, int) and not isinstance(v, bool) else str(v)


def split(stats, name=None):
    """→ (na org, no escopo, detalhe) a partir do dict de stats de um coletor."""
    stats = dict(stats or {})
    labels = {**LABELS, **LABELS_BY.get(name, {})}
    org = next((stats.pop(k) for k in ORG_KEYS if k in stats), None)
    scope = next((stats.pop(k) for k in SCOPE_KEYS if k in stats), None)
    parts = []
    for k, v in stats.items():
        lab = labels.get(k, k)
        if isinstance(v, dict):
            inner = ", ".join(f"{a} {num(b)}" if not isinstance(b, dict) else
                              f"{a} ({', '.join(f'{labels.get(x, x)} {num(y)}' for x, y in b.items())})"
                              for a, b in v.items())
            if all(isinstance(b, (int, float)) and not isinstance(b, bool) for b in v.values()) and k not in labels:
                inner = ", ".join(f"{labels.get(a, a)} {num(b)}" for a, b in v.items())  # ex. repositório → métricas
            parts.append(f"{lab}: {inner}")
        else:
            parts.append(f"{lab} {num(v)}")
    return org, scope, " · ".join(parts)


def line(name, stats, seconds=None, status=None):
    org, scope, det = split(stats, name)
    if name == "ribbons" and scope is not None:  # "no escopo" de ribbons = tabelas consultadas
        det, scope = f"tabelas consultadas {num(scope)}" + (f" · {det}" if det else ""), None
    bits = [f"{num(org)} na org" if org is not None else None,
            f"{num(scope)} no escopo" if scope is not None else None, det or None,
            f"{seconds:.1f} s".replace(".", ",") if seconds is not None else None]
    flag = "" if status in (None, "ok") else f" [{status}]"
    return f"[dvinv]   ✓ {name}{flag}: " + " · ".join(b for b in bits if b)


def rows(manifest):
    """Linhas da tabela-resumo: etapa, na org, no escopo, processado/detalhe, status, tempo."""
    st, cols, tm = manifest.get("stats") or {}, manifest.get("collectors") or {}, manifest.get("timings_s") or {}
    out = []
    for name in list(cols) or list(st):
        org, scope, det = split(st.get(name), name)
        if name == "ribbons" and scope is not None:
            det, scope = f"tabelas consultadas {num(scope)}" + (f" · {det}" if det else ""), None
        c = cols.get(name) or {}
        out.append([name, num(org) if org is not None else "—", num(scope) if scope is not None else "—", det or "—",
                    c.get("status", "—") + (f" ({c['lacunas']} lacuna(s))" if c.get("lacunas") else "")
                    + (" ↺ herdado" if str(c.get("origem", "")).startswith("herdado") else ""),
                    f"{tm[name]:.1f} s".replace(".", ",") if name in tm else "—"])
    return out


def table_text(manifest):
    """Tabela em texto simples para o console/log."""
    head = ["Etapa", "Na org", "No escopo", "Processado / detalhe", "Status", "Tempo"]
    data = rows(manifest)
    w = [max(len(str(r[i])) for r in [head] + data) for i in range(len(head) - 3)]
    lines = ["[dvinv] resumo da extração:"]
    for r in [head] + data:
        lines.append("  " + "  ".join(str(r[i]).ljust(w[i]) for i in range(3)) + f"  {r[5]:>8}  {r[4]}  {r[3]}")
    return "\n".join(lines)
