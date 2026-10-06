"""Saúde de execução: plugin trace log dos últimos N dias (deep.plugin_trace).

Só tem dado se plugintracelogsetting estiver em Exception/All; retenção padrão é curta (24h).
Paginado manualmente (agregação nativa para em 50k registros).
"""

from datetime import datetime, timedelta, timezone


def collect_health(ctx):
    c, cfg = ctx.client, ctx.cfg
    if not cfg.deep.get("plugin_trace"):
        return
    since = (datetime.now(timezone.utc) - timedelta(days=cfg.plugin_trace_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = c.get_all(f"plugintracelogs?$select=typename,messagename,primaryentity,performanceexecutionduration,createdon"
                      f"&$filter=createdon ge {since}")
    errors = c.get_all(f"plugintracelogs?$select=typename,createdon&$filter=createdon ge {since} and exceptiondetails ne null")
    by_type = {}
    for r in total:
        t = by_type.setdefault(r.get("typename") or "?", {"type": r.get("typename"), "runs": 0, "errors": 0,
                                                          "max_ms": 0, "sum_ms": 0})
        t["runs"] += 1
        ms = r.get("performanceexecutionduration") or 0
        t["sum_ms"] += ms
        t["max_ms"] = max(t["max_ms"], ms)
    for r in errors:
        by_type.setdefault(r.get("typename") or "?", {"type": r.get("typename"), "runs": 0, "errors": 0,
                                                      "max_ms": 0, "sum_ms": 0})["errors"] += 1
    rows = []
    for t in by_type.values():
        t["avg_ms"] = round(t["sum_ms"] / t["runs"]) if t["runs"] else None
        t["error_rate"] = round(t["errors"] / t["runs"], 4) if t["runs"] else None
        del t["sum_ms"]
        rows.append(t)
    rows.sort(key=lambda x: (-(x["errors"] or 0), -(x["runs"] or 0)))
    oldest = min((r["createdon"] for r in total), default=None)
    ctx.data["health"] = {"since": since, "oldest_trace": oldest, "traces": len(total), "errors": len(errors),
                          "by_type": rows}
