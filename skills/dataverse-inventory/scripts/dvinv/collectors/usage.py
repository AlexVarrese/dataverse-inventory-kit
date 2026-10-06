"""Uso real de colunas customizadas (deep.field_usage).

1. Preenchimento: quantos registros têm valor em cada coluna. Primeiro tenta agregação nativa
   (FetchXML countcolumn, rápida); acima do limite de ~50 mil registros da agregação, pagina os
   registros contando valores não nulos (exato até field_usage.max_records; acima disso marca
   'parcial' — os números passam a valer só para os registros lidos).
2. Matriz de uso: para cada coluna, onde ela aparece — formulários (controle e eventos), views,
   business rules/workflows/actions/BPFs (XAML/clientdata), cloud flows, plugin steps (filtering
   attributes e imagens), JavaScript e arquivos de repositório.
3. Classificação (bucket) que separa 'candidato seguro a remoção' de 'sem dados mas ainda na UI'.
"""

from collections import defaultdict
from urllib.parse import quote

SKIP_TYPES = {"Virtual", "File", "Image", "EntityName", "ManagedProperty", "CalendarRules", "PartyList", "Uniqueidentifier"}
LOOKUPS = {"Lookup", "Customer", "Owner"}
AGG_LIMIT = 50_000
BATCH_AGG, BATCH_PAGE = 20, 35

BUCKETS = {
    "candidato-seguro": "Sem dados e sem nenhum uso encontrado — candidato a remoção",
    "sem-dados-na-ui": "Sem dados, mas ainda aparece em formulário/view",
    "sem-dados-com-logica": "Sem dados, mas citado em automação/código — remover exige limpar a lógica",
    "dados-sem-uso-conhecido": "Tem dados, mas nenhum uso encontrado (integração/importação?)",
    "em-uso": "Em uso",
    "nao-medido": "Preenchimento não medido",
}


def _select_name(col):
    return f"_{col['logical']}_value" if col.get("type") in LOOKUPS else col["logical"]


def measure_fill(ctx, t, cols):
    c, cfg = ctx.client, ctx.cfg
    es, pk = t.get("entityset"), t.get("primary_id")
    counts, total, method = {}, None, None
    if not (es and pk):
        return counts, total, "sem entity set"
    rc = t.get("record_count")
    if rc is None or rc <= AGG_LIMIT:
        try:
            for i in range(0, len(cols), BATCH_AGG):
                batch = cols[i:i + BATCH_AGG]
                attrs = f'<attribute name="{pk}" alias="total" aggregate="count"/>' + "".join(
                    f'<attribute name="{col["logical"]}" alias="c{j}" aggregate="countcolumn"/>' for j, col in enumerate(batch))
                fetch = f'<fetch aggregate="true"><entity name="{t["logical"]}">{attrs}</entity></fetch>'
                row = (c.get_all(f"{es}?fetchXml={quote(fetch)}") or [{}])[0]
                total = row.get("total", 0)
                for j, col in enumerate(batch):
                    counts[col["logical"]] = row.get(f"c{j}", 0)
            return counts, total, "agregação"
        except Exception as e:  # noqa: BLE001 — AggregateQueryRecordLimit e afins: cai para paginação
            ctx.gap("field_usage", f"agregação em {t['logical']} (usando paginação)", e)
            counts = {}
    method = "paginação"
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
            method = f"parcial (primeiros {seen:,} registros)".replace(",", ".")
        c.log.append({"path": f"{es}?$select=<{len(batch)} colunas>", "status": 200, "rows": seen})
        counts.update(local)
        total = seen
    return counts, total, method


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

        use = defaultdict(lambda: defaultdict(set))
        for f in forms:
            if f["entity"] != ln:
                continue
            for fld in f.get("fields") or []:
                if f.get("active"):
                    use[fld]["forms"].add(f["name"])
            for h in f.get("handlers") or []:
                if h.get("field"):
                    use[h["field"]]["form_events"].add(f"{f['name']}:{h['event']}")
        for v in views:
            if v["entity"] == ln:
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
            logic = bool(u.get("form_events") or u.get("processes") or u.get("plugin_steps") or u.get("javascript")
                         or repo_hits.get(fld))
            if pop is None:
                bucket = "nao-medido"
            elif pop == 0:
                bucket = "sem-dados-com-logica" if logic else "sem-dados-na-ui" if ui else "candidato-seguro"
            else:
                bucket = "em-uso" if (ui or logic) else "dados-sem-uso-conhecido"
            rows.append({
                "logical": fld, "display": col.get("display"), "type": col.get("type"),
                "populated": pop, "pct": round(100 * pop / total, 2) if pop is not None and total else None,
                **{k: sorted(u.get(k, [])) for k in ("forms", "form_events", "views", "processes", "plugin_steps", "javascript")},
                "repo_files": repo_hits.get(fld, 0), "bucket": bucket,
            })
        rows.sort(key=lambda r: (list(BUCKETS).index(r["bucket"]), r["logical"]))
        out.append({"table": ln, "total": total, "method": method, "fields": rows,
                    "views_measured": any(v.get("columns") is not None for v in views if v["entity"] == ln),
                    "repos_measured": bool(repos)})
    ctx.stats["field_usage"] = {"tables": len(out), "fields": sum(len(t["fields"]) for t in out),
                                "safe_candidates": sum(1 for t in out for f in t["fields"] if f["bucket"] == "candidato-seguro")}
    ctx.data["field_usage"] = out
