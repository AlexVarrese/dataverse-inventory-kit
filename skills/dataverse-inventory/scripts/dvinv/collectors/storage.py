"""Armazenamento e auditoria (deep.storage).

- Maiores tabelas por nº de registros (RetrieveTotalRecordCount em todas as tabelas).
- Anexos: annotation (notas) e activitymimeattachment (anexos de e-mail) — quantidade por tabela,
  bytes, tipos de arquivo.
- Auditoria: volume por tabela e por ação, registro mais antigo (retenção efetiva).

Armadilhas tratadas: SUM de inteiros estoura int32 acima de ~2 GB por consulta → particiona por
ano e, se preciso, por mês; agregação nativa limitada → falha vira lacuna declarada. Os números são
de *Dataverse storage* lido pela Web API; o consumo oficial de capacidade (GB contratado × usado)
só existe no Power Platform Admin Center (API de capacidade exige papel de admin do tenant).
"""

from datetime import date

from .tables import record_counts

FIRST_YEAR = 2008


def _key(row, field, otc_map):
    v = row.get(field)
    if isinstance(v, int):
        return otc_map.get(v, str(v))
    return v or "(vazio)"


def _sum_bytes(ctx, entity_set, base_filter, label_):
    """Soma filesize particionando o período até não estourar."""
    c = ctx.client

    def q(extra):
        flt = " and ".join(x for x in (base_filter, extra) if x)
        f = f"filter({flt})/" if flt else ""
        return c.get_all(f"{entity_set}?$apply={f}aggregate(filesize with sum as total,$count as cnt)")[0]

    try:
        r = q(None)
        return r.get("total") or 0, r.get("cnt") or 0, "total"
    except Exception:  # noqa: BLE001
        pass
    total = cnt = 0
    failed = []
    for y in range(FIRST_YEAR, date.today().year + 1):
        try:
            r = q(f"createdon ge {y}-01-01 and createdon lt {y + 1}-01-01")
            total += r.get("total") or 0
            cnt += r.get("cnt") or 0
            continue
        except Exception:  # noqa: BLE001
            pass
        for m in range(1, 13):
            nxt = f"{y + 1}-01-01" if m == 12 else f"{y}-{m + 1:02d}-01"
            try:
                r = q(f"createdon ge {y}-{m:02d}-01 and createdon lt {nxt}")
                total += r.get("total") or 0
                cnt += r.get("cnt") or 0
            except Exception:  # noqa: BLE001
                failed.append(f"{y}-{m:02d}")
    if failed:
        ctx.gap("storage", f"bytes de {label_} em {len(failed)} mês(es)", ", ".join(failed[:12]))
    return total, cnt, "particionado"


def collect_storage(ctx):
    c, cfg = ctx.client, ctx.cfg
    if not cfg.deep.get("storage"):
        return
    idx = ctx.data.get("_tables_index") or []
    otc_map = {t["otc"]: t["logical"] for t in idx if t.get("otc") is not None}
    out = {"largest_tables": [], "annotations": {}, "email_attachments": {}, "audit": {}}

    # 1. maiores tabelas
    names = [t["logical"] for t in idx if not t.get("intersect") and t.get("table_type") != "Virtual"]
    counts = record_counts(ctx, names, "storage")
    out["largest_tables"] = [{"table": k, "records": v} for k, v in sorted(counts.items(), key=lambda kv: -(kv[1] or 0))[:40]]

    # 2. anexos
    for key, es, label_ in (("annotations", "annotations", "notas (annotation)"),
                            ("email_attachments", "activitymimeattachments", "anexos de e-mail (activitymimeattachment)")):
        sec = {"label": label_, "by_table": [], "by_mimetype": [], "bytes": None, "count": None}
        try:
            rows = c.get_all(f"{es}?$apply=groupby((objecttypecode),aggregate($count as cnt))")
            sec["by_table"] = sorted([{"table": _key(r, "objecttypecode", otc_map), "count": r.get("cnt")} for r in rows],
                                     key=lambda x: -(x["count"] or 0))
        except Exception:  # noqa: BLE001 — groupby na org inteira estoura o tempo de SQL em tabelas grandes
            # plano B: contagem filtrada por tabela do escopo, em paralelo
            def count_for(tab):
                return c.get_all(f"{es}?$apply=filter(objecttypecode eq '{tab}')/aggregate($count as cnt)")[0].get("cnt")
            res = c.parallel(count_for, sorted(ctx.scope.tables), label=f"{label_} por tabela")
            fails = [tab for tab, _, err in res if err]
            sec["by_table"] = sorted([{"table": tab, "count": n} for tab, n, err in res if not err and n],
                                     key=lambda x: -(x["count"] or 0))
            sec["by_table_scope_only"] = True
            if fails:
                ctx.gap("storage", f"{label_}: contagem em {len(fails)} tabela(s)", ", ".join(fails[:10]))
        try:
            rows = c.get_all(f"{es}?$apply=groupby((mimetype),aggregate($count as cnt,filesize with sum as total))")
            sec["by_mimetype"] = sorted([{"mimetype": r.get("mimetype") or "(vazio)", "count": r.get("cnt"),
                                          "bytes": r.get("total")} for r in rows], key=lambda x: -(x["bytes"] or 0))[:30]
        except Exception as e:  # noqa: BLE001
            ctx.gap("storage", f"{label_} por tipo de arquivo (provável estouro de soma)", e)
        try:
            # activitymimeattachment não tem createdon filtrável em todas as versões: sem partição, só total.
            if es == "annotations":
                sec["bytes"], sec["count"], sec["bytes_method"] = _sum_bytes(ctx, es, "isdocument eq true", label_)
            else:
                r = c.get_all(f"{es}?$apply=aggregate(filesize with sum as total,$count as cnt)")[0]
                sec["bytes"], sec["count"], sec["bytes_method"] = r.get("total"), r.get("cnt"), "total"
        except Exception as e:  # noqa: BLE001
            ctx.gap("storage", f"bytes totais de {label_}", e)
        if sec["by_table"] and es == "annotations":
            for row in sec["by_table"][:15]:
                try:
                    b, _, _ = _sum_bytes(ctx, es, f"objecttypecode eq '{row['table']}' and isdocument eq true", f"notas de {row['table']}")
                    row["bytes"] = b
                except Exception as e:  # noqa: BLE001
                    ctx.gap("storage", f"bytes de notas em {row['table']}", e)
        out[key] = sec

    # 3. auditoria
    aud = {"by_table": [], "by_action": [], "oldest": None, "total": None}
    try:
        rows = c.get_all("audits?$apply=groupby((objecttypecode),aggregate(auditid with countdistinct as cnt))")
        aud["by_table"] = sorted([{"table": _key(r, "objecttypecode", otc_map), "count": r.get("cnt")} for r in rows],
                                 key=lambda x: -(x["count"] or 0))
        aud["total"] = sum(r["count"] or 0 for r in aud["by_table"])
    except Exception as e:  # noqa: BLE001
        ctx.gap("storage", "auditoria por tabela", e)
    try:
        rows = c.get_all("audits?$apply=groupby((action),aggregate(auditid with countdistinct as cnt))")
        aud["by_action"] = sorted([{"action": r.get("action@OData.Community.Display.V1.FormattedValue") or r.get("action"),
                                    "count": r.get("cnt")} for r in rows], key=lambda x: -(x["count"] or 0))
    except Exception as e:  # noqa: BLE001
        ctx.gap("storage", "auditoria por ação", e)
    try:
        first = c.get("audits?$select=createdon&$orderby=createdon asc&$top=1").get("value") or []
        aud["oldest"] = (first[0].get("createdon") or "")[:10] if first else None
    except Exception as e:  # noqa: BLE001
        ctx.gap("storage", "registro de auditoria mais antigo", e)
    out["audit"] = aud
    ctx.stats["storage"] = {"annotations": out["annotations"].get("count"), "email_attachments": out["email_attachments"].get("count"),
                            "audit_records": aud.get("total")}
    ctx.data["storage"] = out
