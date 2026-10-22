"""Uso e resíduos: atividade das tabelas, formulários fora de apps e candidatos a limpeza com o impacto da exclusão.

Tudo aqui é sinal para INVESTIGAÇÃO, não autorização de exclusão. O Dataverse não mostra integrações externas,
relatórios (Power BI), portais, uso de formulário por URL nem código fora do `repos:`. Base para preparar a migração
e a limpeza dos ambientes: o que está parado, o que ninguém referencia e em que ordem dá para remover.
"""

from collections import defaultdict
from datetime import date, timedelta

from . import dependencies as deps_mod

# systemform.type de formulários de registro: principal, exibição rápida, criação rápida, cartão, principal interativo
FORM_RECORD_TYPES = {2, 6, 7, 11, 12}

# Achados cujos componentes entram como candidatos a limpeza (o motivo vai para a nota)
CLEANUP_FROM_FINDINGS = {
    "UI-03": "JS sem uso em formulário (nem em ribbon, se coletado)",
    "PCF-02": "PCF não gerenciado sem uso em formulário",
    "DEP-03": "variável, conexão ou Custom API sem uso encontrado",
    "PLG-03": "step de plugin desativado",
    "PRC-05": "cloud flow desligado",
}

STATUS = ("bloqueado pela plataforma", "ajustar formulário/view antes", "revisar referências",
          "sem dependente no inventário")


def reference_day(d):
    """Data da extração: referência de 'há quantos dias' (render feito outro dia não muda o resultado)."""
    ts = ((d.get("manifest") or {}).get("extracted_at") or "")[:10]
    try:
        return date.fromisoformat(ts)
    except ValueError:
        return None


def last_activity(d):
    """→ {tabela: data mais recente entre o último registro criado e o último alterado} (só tabelas medidas)."""
    out = {}
    for ln, a in ((d.get("table_activity") or {}).get("tables") or {}).items():
        days = [x for x in (a.get("last_created"), a.get("last_modified")) if x]
        if days:
            out[ln] = max(days)
    return out


def stale_tables(d):
    """Tabelas customizadas com registros e nenhum registro criado/alterado há mais de `inactive_days` → [(t, data)]."""
    act, ref = d.get("table_activity") or {}, reference_day(d)
    if not act.get("tables") or ref is None:
        return []
    limit = (ref - timedelta(days=int(act.get("inactive_days") or 365))).isoformat()
    last = last_activity(d)
    return [(t, last[t["logical"]]) for t in d.get("tables") or []
            if t.get("custom") and t.get("record_count") and t["logical"] in last and last[t["logical"]] < limit]


def empty_tables(d):
    """Tabelas customizadas (não virtuais) com contagem zero."""
    return [t for t in d.get("tables") or []
            if t.get("custom") and t.get("record_count") == 0 and t.get("table_type") != "Virtual"]


def record_forms(d):
    """Formulários de registro das tabelas do escopo (sem painéis). Snapshot antigo, sem `type_code`: todos com tabela."""
    scope = {t["logical"] for t in d.get("tables") or []}
    return [f for f in d.get("forms") or [] if f.get("entity") in scope
            and (f.get("type_code") is None or f.get("type_code") in FORM_RECORD_TYPES)]


def form_apps(d):
    """→ {id do formulário: [apps em que está disponível]}, ou None se a disponibilidade não foi coletada.

    Disponível num app model-driven quando o app inclui a tabela e não escolhe formulários dela (todos valem) ou
    escolhe este. Sinal indireto: o Dataverse não registra quem abre cada formulário."""
    avail = (d.get("apps") or {}).get("availability")
    if avail is None:
        return None
    table_ids = {(t.get("id") or "").lower(): t["logical"] for t in d.get("tables") or [] if t.get("id")}
    forms = record_forms(d)
    by_entity = defaultdict(set)
    for f in forms:
        by_entity[f["entity"]].add(f["id"].lower())
    out = {f["id"].lower(): set() for f in forms}
    for a in avail:
        chosen_all = set(a.get("forms") or [])
        for tid in a.get("tables") or []:
            ln = table_ids.get(tid)
            if not ln:
                continue
            chosen = by_entity[ln] & chosen_all
            for fid in chosen or by_entity[ln]:
                out[fid].add(a.get("name") or a.get("unique") or a["id"])
    return {k: sorted(v) for k, v in out.items()}


def candidates(d, g, fnd):
    """Candidatos a limpeza com o impacto de excluir cada um, em ondas de remoção → lista de dicts.

    - bloqueios: quem depende do componente segundo a plataforma (tabela `dependency`, o que impede a exclusão);
    - referências: o que o kit encontrou em JS, flows, plugins, processos e apps (inferido);
    - onda: um candidato usado por outro candidato vai numa onda depois dele (remove-se primeiro quem usa).
    Dependente que também é candidato não bloqueia: sai na onda anterior."""
    items = {}

    def add(n, rule, reason, evidence="", extra_hard=None):
        items.setdefault(n, {"rule": rule, "reason": reason, "evidence": evidence, "extra_hard": extra_hard or []})

    for t, last in stale_tables(d):
        add(("table", t["logical"]), "TAB-01", "sem registro criado ou alterado recentemente",
            f"{t['record_count']:,} registros · última atividade {last}".replace(",", "."))
    for t in empty_tables(d):
        add(("table", t["logical"]), "TAB-02", "tabela customizada sem registros", "0 registros")
    fa = form_apps(d)
    for f in record_forms(d):
        n = ("form", f["id"])
        managed = "gerenciado" if f.get("managed") else "não gerenciado"
        if not f.get("active"):
            add(n, "FRM-02", "formulário inativo", managed)
        elif fa is not None and not fa.get(f["id"].lower()):
            add(n, "FRM-01", "formulário ativo fora de qualquer app model-driven", managed)
    for tu in d.get("field_usage") or []:
        for col in tu.get("fields") or []:
            n = ("column", f"{tu['table']}.{col['logical']}")
            if col["bucket"] == "candidato-seguro":
                add(n, "FLD-01", "coluna sem dados e sem uso encontrado", "0 preenchidos (contagem completa)")
            elif col["bucket"] in ("sem-dados-na-ui", "sem-dados-uso-fraco"):
                where = [f"formulário {x}" for x in (col.get("forms") or []) + (col.get("forms_inactive") or [])]
                where += [f"view {x}" for x in col.get("views") or []]
                add(n, "FLD-02", "coluna sem dados que ainda aparece em formulário/view", "0 preenchidos", where)
    for f in fnd:
        if f["id"] in CLEANUP_FROM_FINDINGS:
            for ref in f.get("refs") or []:
                add(tuple(ref), f["id"], CLEANUP_FROM_FINDINGS[f["id"]])

    def dependents(n):
        if n not in g.nodes:
            return []
        return [(src, rel, e) for src, rel, e in g.into(n)]

    wave, visiting = {}, set()

    def wave_of(n):
        if n in wave:
            return wave[n]
        if n in visiting:  # ciclo entre candidatos: mesma onda
            return 1
        visiting.add(n)
        deps = [src for src, _r, _e in dependents(n) if src in items and src != n]
        wave[n] = 1 + max((wave_of(s) for s in deps), default=0)
        visiting.discard(n)
        return wave[n]

    def label(n):
        node = g.nodes.get(n)
        if node:
            return node["label"]
        if n[0] == "form":
            f = next((x for x in d.get("forms") or [] if x["id"] == n[1]), {})
            return f"{f.get('entity')} / {f.get('name')}"
        return str(n[1])

    rows = []
    for n, it in items.items():
        hard, soft = [], []
        for src, rel, e in dependents(n):
            if src in items:
                continue
            txt = f"{label(src)} ({deps_mod.KIND_LABEL.get(src[0], src[0]).lower()}, {rel})"
            (hard if "plataforma" in (e.get("source") or "") else soft).append(txt)
        if hard:
            status = STATUS[0]
        elif it["extra_hard"]:
            status = STATUS[1]
        elif soft:
            status = STATUS[2]
        else:
            status = STATUS[3]
        rows.append({"wave": wave_of(n), "status": status, "node": n, "kind": n[0],
                     "key": deps_mod.canonical_key(n), "label": label(n), "rule": it["rule"], "reason": it["reason"],
                     "evidence": it["evidence"], "hard": sorted(set(hard)) + list(it["extra_hard"]),
                     "soft": sorted(set(soft))})
    rows.sort(key=lambda r: (r["wave"], STATUS.index(r["status"]), r["kind"], r["label"].lower(), r["key"]))
    return rows
