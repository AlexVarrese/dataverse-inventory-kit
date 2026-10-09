"""Uso real de colunas customizadas (deep.field_usage).

1. Preenchimento: quantos registros têm valor em cada coluna. Primeiro tenta agregação nativa
   (FetchXML countcolumn, rápida); acima do limite de ~50 mil registros da agregação, pagina os
   registros contando valores não nulos (exato até field_usage.max_records; acima disso marca
   'parcial' — os números passam a valer só para os registros lidos).
2. Matriz de uso: para cada coluna, onde ela aparece — formulários (controle e eventos), views,
   business rules/workflows/actions/BPFs (XAML/clientdata), cloud flows, plugin steps (filtering
   attributes e imagens), JavaScript e arquivos de repositório.
3. Classificação (bucket). `candidato-seguro` (rótulo: candidato a INVESTIGAÇÃO de remoção) só sai
   com contagem completa (não parcial, não falhou) E todas as fontes da matriz de uso medidas
   (forms, views, processos, plugins, web resources, repositórios). Qualquer lacuna nessas fontes,
   ou zero preenchido numa amostra parcial, vira `inconclusivo` com os motivos registrados.
   Nenhuma classificação afirma que remover é seguro: integrações externas não são visíveis daqui.
"""

from collections import defaultdict
from urllib.parse import quote

SKIP_TYPES = {"Virtual", "File", "Image", "EntityName", "ManagedProperty", "CalendarRules", "PartyList", "Uniqueidentifier"}
LOOKUPS = {"Lookup", "Customer", "Owner"}
AGG_LIMIT = 50_000
BATCH_AGG, BATCH_PAGE = 20, 35

BUCKETS = {
    "candidato-seguro": "Sem dados e sem uso encontrado (cobertura completa) — candidato a investigação de remoção; "
                        "confirmar integrações externas",
    "inconclusivo": "Sem dados na amostra ou cobertura incompleta — investigar",
    "sem-dados-uso-fraco": "Sem dados; só aparece em formulário inativo",
    "sem-dados-na-ui": "Sem dados, mas ainda aparece em formulário/view",
    "sem-dados-com-logica": "Sem dados, mas citado em automação/código — remover exige limpar a lógica",
    "dados-sem-uso-conhecido": "Tem dados, mas nenhum uso encontrado (integração/importação?)",
    "em-uso": "Em uso",
    "nao-medido": "Preenchimento não medido",
}


def _select_name(col):
    return f"_{col['logical']}_value" if col.get("type") in LOOKUPS else col["logical"]


# countcolumn em coluna Money falha no Dataverse ("expected Microsoft.Xrm.Sdk.Money") — conta por paginação.
NO_AGG_TYPES = {"Money"}


def _page_counts(ctx, t, cols):
    """Conta valores não nulos paginando os registros (exato até field_usage.max_records)."""
    c, cfg = ctx.client, ctx.cfg
    es, pk = t["entityset"], t["primary_id"]
    counts, total, partial = {}, None, False
    for i in range(0, len(cols), BATCH_PAGE):
        batch = cols[i:i + BATCH_PAGE]
        sel = ",".join([pk] + [_select_name(col) for col in batch])
        url, seen = c._url(f"{es}?$select={sel}"), 0
        local = {col["logical"]: 0 for col in batch}
        while url and seen < cfg.field_usage_max_records:
            data = c._request(url)
            rows = data.get("value", [])
            seen += len(rows)
            for col in batch:
                k = _select_name(col)
                local[col["logical"]] += sum(1 for r in rows if r.get(k) not in (None, "", []))
            url = data.get("@odata.nextLink")
            if url:
                c.check_url(url)  # nextLink vem da resposta: só segue para a base configurada
        partial = partial or bool(url)
        c.log.append({"path": f"{es}?$select=<{len(batch)} colunas>", "status": 200, "rows": seen})
        counts.update(local)
        total = seen
    return counts, total, partial


def measure_fill(ctx, t, cols):
    c = ctx.client
    es, pk = t.get("entityset"), t.get("primary_id")
    if not (es and pk):
        return {}, None, "sem entity set"
    rc = t.get("record_count")
    counts, total, to_page = {}, None, []

    def agg(batch):
        attrs = f'<attribute name="{pk}" alias="total" aggregate="count"/>' + "".join(
            f'<attribute name="{col["logical"]}" alias="c{j}" aggregate="countcolumn"/>' for j, col in enumerate(batch))
        fetch = f'<fetch aggregate="true"><entity name="{t["logical"]}">{attrs}</entity></fetch>'
        row = (c.get_all(f"{es}?fetchXml={quote(fetch)}") or [{}])[0]
        return row.get("total", 0), {col["logical"]: row.get(f"c{j}", 0) for j, col in enumerate(batch)}

    def agg_safe(batch):
        """Agrega; se o lote falhar por causa de alguma coluna, divide até isolá-la (ela vai para paginação)."""
        nonlocal total
        try:
            tot, got = agg(batch)
            total = tot
            counts.update(got)
        except Exception as e:  # noqa: BLE001
            if "50000" in str(e) or "0x8004e023" in str(e).lower():
                raise  # limite de registros da agregação: a tabela inteira vai para paginação
            if len(batch) == 1:
                to_page.append(batch[0])
            else:
                agg_safe(batch[:len(batch) // 2])
                agg_safe(batch[len(batch) // 2:])

    if rc is None or rc <= AGG_LIMIT:
        aggregable = [col for col in cols if col.get("type") not in NO_AGG_TYPES]
        to_page += [col for col in cols if col.get("type") in NO_AGG_TYPES]
        try:
            for i in range(0, len(aggregable), BATCH_AGG):
                agg_safe(aggregable[i:i + BATCH_AGG])
        except Exception as e:  # noqa: BLE001
            ctx.gap("field_usage", f"agregação em {t['logical']} (usando paginação)", e)
            counts, to_page = {}, list(cols)
    else:
        to_page = list(cols)
    if not to_page:
        return counts, total, "agregação"
    paged, ptotal, partial = _page_counts(ctx, t, to_page)
    counts.update(paged)
    total = ptotal if total is None else total
    if partial:
        method = f"parcial (primeiros {ptotal:,} registros)".replace(",", ".")
    elif len(to_page) == len(cols):
        method = "paginação"
    else:
        method = f"agregação + paginação ({len(to_page)} colunas)"
    return counts, total, method


# Fontes da matriz de uso: coletor -> o que precisa estar ligado para a fonte valer como "medida".
USAGE_SOURCES = {
    "forms": ("form_events",),
    "views": (),
    "processes": ("process_definitions", "flow_definitions"),
    "plugins": (),
    "webresources": ("webresource_content",),
    "repos": (),
}


def source_coverage(ctx):
    """Fontes da matriz que NÃO estão completas nesta extração → [motivo] (vazio = todas medidas)."""
    cfg, missing = ctx.cfg, []
    for src, needs in USAGE_SOURCES.items():
        st = (getattr(ctx, "status", {}) or {}).get(src)
        if st is None:
            missing.append(f"{src}: status desconhecido")
            continue
        if st["status"] in ("falhou", "nao-executado"):
            missing.append(f"{src}: {st['status']}")
            continue
        if st["status"] == "parcial":
            missing.append(f"{src}: parcial ({st.get('lacunas', '?')} lacuna(s))")
        off = [n for n in needs if not cfg.deep.get(n)]
        if off:
            missing.append(f"{src}: deep.{', deep.'.join(off)} desligado")
        if src == "repos" and not cfg.repos:
            missing.append("repos: nenhum repositório configurado (repos:)")
        elif st["status"] == "sem-dados" and src != "repos":
            missing.append(f"{src}: sem dados")
    return missing


def collect_field_usage(ctx):
    cfg = ctx.cfg
    if not cfg.deep.get("field_usage"):
        return
    tables = ctx.data.get("tables") or []
    if cfg.field_usage_tables:
        tables = [t for t in tables if t["logical"] in cfg.field_usage_tables]

    forms = ctx.data.get("forms") or []
    views = ctx.data.get("views") or []
    procs = ctx.data.get("processes") or []
    steps = (ctx.data.get("plugins") or {}).get("steps") or []
    wrs = ctx.data.get("webresources") or []
    repos = ctx.data.get("repos") or []
    global_missing = source_coverage(ctx)

    from ..util import field_refs
    out = []
    for t in tables:
        ln = t["logical"]
        cols = [col for col in t.get("columns") or [] if col.get("custom") and col.get("type") not in SKIP_TYPES]
        if not cols:
            continue
        known = {col["logical"] for col in cols}
        try:
            counts, total, method = measure_fill(ctx, t, cols)
        except Exception as e:  # noqa: BLE001
            ctx.gap("field_usage", f"preenchimento de {ln}", e)
            counts, total, method = {}, None, "falhou"
        partial = str(method).startswith("parcial")

        # cobertura desta tabela: fontes globais + formulários/views da própria tabela lidos por inteiro
        t_forms = [f for f in forms if f["entity"] == ln]
        t_views = [v for v in views if v["entity"] == ln]
        forms_measured = all(f.get("xml_read") for f in t_forms)
        views_measured = all(v.get("columns") is not None for v in t_views)
        missing = list(global_missing)
        if not forms_measured:
            missing.append(f"forms: formxml não lido em {sum(1 for f in t_forms if not f.get('xml_read'))} formulário(s)")
        if not views_measured:
            missing.append(f"views: colunas não lidas em {sum(1 for v in t_views if v.get('columns') is None)} view(s)")

        use = defaultdict(lambda: defaultdict(set))
        for f in t_forms:
            for fld in f.get("fields") or []:
                use[fld]["forms" if f.get("active") else "forms_inactive"].add(f["name"])
            for h in f.get("handlers") or []:
                if h.get("field"):
                    use[h["field"]]["form_events"].add(f"{f['name']}:{h['event']}")
        for v in t_views:
            for fld in v.get("columns") or []:
                use[fld]["views"].add(v["name"])
        for p in procs:
            if p.get("entity") == ln or ln in (p.get("tables") or []):
                for fld in p.get("field_refs") or []:
                    use[fld]["processes"].add(f"{p['category']}: {p['name']}")
        for s in steps:
            if s.get("entity") == ln:
                attrs = set((s.get("filtering") or "").split(","))
                for im in s.get("images") or []:
                    attrs |= set((im.get("attributes") or "").split(","))
                for fld in {a.strip().lower() for a in attrs if a.strip()}:
                    use[fld]["plugin_steps"].add(s["name"])
        for w in wrs:
            for fld in field_refs(w.get("content") or "", known):
                use[fld]["javascript"].add(w["name"])
        repo_hits = {}
        for r in repos:
            for fld, h in (r.get("field_hits") or {}).items():
                if fld in known:
                    repo_hits[fld] = repo_hits.get(fld, 0) + h["count"]

        rows = []
        for col in cols:
            fld = col["logical"]
            u = use.get(fld, {})
            pop = counts.get(fld)
            ui = bool(u.get("forms") or u.get("views"))
            weak = bool(u.get("forms_inactive"))
            logic = bool(u.get("form_events") or u.get("processes") or u.get("plugin_steps") or u.get("javascript")
                         or repo_hits.get(fld))
            reasons = []
            if pop is None:
                bucket = "inconclusivo" if method == "falhou" else "nao-medido"
                if method == "falhou":
                    reasons.append("preenchimento: medição falhou")
            elif pop > 0:  # há dado: a amostra (mesmo parcial) já prova preenchimento
                bucket = "em-uso" if (ui or logic) else "dados-sem-uso-conhecido"
            elif partial:
                bucket = "inconclusivo"
                reasons.append(f"preenchimento: zero na amostra {method} — registros não lidos podem ter valor")
            elif logic:
                bucket = "sem-dados-com-logica"
            elif ui:
                bucket = "sem-dados-na-ui"
            elif missing:
                bucket = "inconclusivo"
                reasons += missing
            elif weak:
                bucket = "sem-dados-uso-fraco"
            else:
                bucket = "candidato-seguro"
            rows.append({
                "logical": fld, "display": col.get("display"), "type": col.get("type"),
                "populated": pop, "pct": round(100 * pop / total, 2) if pop is not None and total else None,
                **{k: sorted(u.get(k, [])) for k in ("forms", "forms_inactive", "form_events", "views", "processes",
                                                     "plugin_steps", "javascript")},
                "repo_files": repo_hits.get(fld, 0), "bucket": bucket, "inconclusive_reasons": reasons,
            })
        rows.sort(key=lambda r: (list(BUCKETS).index(r["bucket"]), r["logical"]))
        out.append({"table": ln, "total": total, "method": method, "fields": rows,
                    "count_complete": method not in ("falhou", "sem entity set") and not partial,
                    "coverage_complete": not missing, "missing_sources": missing,
                    "forms_measured": forms_measured, "views_measured": views_measured,
                    "repos_measured": bool(repos)})
    ctx.stats["field_usage"] = {"tables": len(out), "fields": sum(len(t["fields"]) for t in out),
                                "safe_candidates": sum(1 for t in out for f in t["fields"] if f["bucket"] == "candidato-seguro"),
                                "inconclusive": sum(1 for t in out for f in t["fields"] if f["bucket"] == "inconclusivo")}
    ctx.data["field_usage"] = out
