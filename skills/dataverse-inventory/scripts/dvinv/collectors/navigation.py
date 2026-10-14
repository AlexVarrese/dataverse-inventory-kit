"""Site maps (navegação dos apps) e SLAs (acordos de nível de serviço).

Site maps
- `sitemaps` só devolve o site map clássico sem filtro: os de app (app designer) exigem
  `$filter=isappaware eq true` (ESAB-TEST: 1 sem filtro × 29 no catálogo). Lê os dois.
- `sitemapxml` é customização (como o formxml): Área → Grupo → SubÁrea, com destino = tabela
  (`Entity`), web resource (`$webresource:` ou `/WebResources/` em Url/Icon/VectorIcon), dashboard ou URL.
- Escopo: critério normal (prefixo/solução/não gerenciado) ou site map de app do escopo.

SLAs
- `slas` + `slaitems` (condições em FetchXML: aplicável quando / sucesso / pausa) + `msdyn_slakpis`
  (campo de KPI na tabela) + calendário de horário comercial (só o nome).
- Escopo: SLA em tabela do escopo ou critério normal.
"""

import re
import xml.etree.ElementTree as ET

from ..util import day, fv

WR_RE = re.compile(r"(?:\$webresource:|/WebResources/)([^\"'?&#\s]+)", re.I)
COND_ATTR_RE = re.compile(r"<condition\b[^>]*\battribute=\"([^\"]+)\"", re.I)
ENTITY_RE = re.compile(r"<(?:entity|link-entity)\b[^>]*\bname=\"([^\"]+)\"", re.I)
DASHBOARD_RE = re.compile(r"dashboard", re.I)


def _title(el, lcids=(1046, 1033)):
    titles = {t.get("LCID"): t.get("Title") for t in el.findall("./Titles/Title")}
    for l in lcids:
        if titles.get(str(l)):
            return titles[str(l)]
    return next((v for v in titles.values() if v), None) or el.get("Title") or el.get("ResourceId") or el.get("Id")


def parse_sitemap(xml):
    """Áreas/grupos/subáreas + destinos. Tolerante: XML inválido → estrutura vazia + erro."""
    if not xml:
        return {"areas": [], "error": None}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        return {"areas": [], "error": f"sitemapxml inválido: {e}"}
    areas = []
    for a in root.iter("Area"):
        groups = []
        for g in a.findall("Group"):
            subs = []
            for s in g.findall("SubArea"):
                url = s.get("Url") or ""
                wrs = sorted({m.group(1) for k in ("Url", "Icon", "VectorIcon") for m in WR_RE.finditer(s.get(k) or "")})
                ent = s.get("Entity")
                kind = ("tabela" if ent else "web resource" if WR_RE.search(url) else
                        "dashboard" if DASHBOARD_RE.search(url) else "página" if "pagetype=custom" in url.lower() else
                        "url" if url else "—")
                subs.append({"id": s.get("Id"), "title": _title(s), "entity": ent, "url": url or None, "kind": kind,
                             "webresources": wrs, "external": url.lower().startswith(("http://", "https://")),
                             "privileges": [f"{p.get('Privilege')} {p.get('Entity')}" for p in s.findall("Privilege")]})
            groups.append({"id": g.get("Id"), "title": _title(g), "subareas": subs})
        icons = sorted({m.group(1) for k in ("Icon", "VectorIcon") for m in WR_RE.finditer(a.get(k) or "")})
        areas.append({"id": a.get("Id"), "title": _title(a), "groups": groups, "icon_webresources": icons})
    return {"areas": areas, "error": None}


def collect_sitemaps(ctx):
    c, scope = ctx.client, ctx.scope
    sel = "sitemapid,sitemapname,sitemapnameunique,sitemapxml,ismanaged,isappaware,modifiedon"
    rows, seen = [], set()
    for flt in ("&$filter=isappaware eq true", "&$filter=isappaware eq false", ""):
        try:
            for r in c.get_all(f"sitemaps?$select={sel}{flt}"):
                if r["sitemapid"] not in seen:
                    seen.add(r["sitemapid"])
                    rows.append(r)
        except Exception as e:  # noqa: BLE001
            ctx.gap("sitemaps", f"sitemaps{flt or ' (sem filtro)'}", e)
    apps_by_sitemap = {}
    for a in (ctx.data.get("apps") or {}).get("modeldriven") or []:
        for comp in a.get("components") or []:
            if comp.get("type") == 62:
                apps_by_sitemap.setdefault((comp.get("objectid") or "").lower(), []).append(a)
    out = []
    for r in rows:
        sid = r["sitemapid"].lower()
        name = r.get("sitemapnameunique") or r.get("sitemapname") or "SiteMap (clássico)"
        apps = apps_by_sitemap.get(sid, [])
        reason = scope.reason(name, r.get("ismanaged"), sid) or (
            "site map de app do escopo" if any(a.get("scope_reason") for a in apps) else None)
        if not reason:
            continue
        parsed = parse_sitemap(r.get("sitemapxml"))
        if parsed["error"]:
            ctx.gap("sitemaps", f"estrutura do site map {name}", parsed["error"])
        subs = [s for a in parsed["areas"] for g in a["groups"] for s in g["subareas"]]
        out.append({
            "id": sid, "name": name, "display": r.get("sitemapname"), "managed": r.get("ismanaged"),
            "app_aware": r.get("isappaware"), "modified": day(r.get("modifiedon")), "scope_reason": reason,
            "solutions": scope.solutions_of(sid), "apps": [{"id": a["id"], "name": a["name"]} for a in apps],
            "areas": parsed["areas"],
            "tables": sorted({s["entity"] for s in subs if s["entity"]}),
            "webresources": sorted({w for s in subs for w in s["webresources"]} |
                                   {w for a in parsed["areas"] for w in a["icon_webresources"]}),
            "external_urls": sorted({s["url"] for s in subs if s["external"]}),
        })
    ctx.stats["sitemaps"] = {"org_total": len(rows), "scope": len(out),
                             "subareas": sum(len(g["subareas"]) for s in out for a in s["areas"] for g in a["groups"])}
    ctx.data["sitemaps"] = sorted(out, key=lambda x: x["name"].lower())


def _fetch_fields(xml):
    """Colunas citadas em condições de FetchXML (aplicável quando / sucesso / pausa)."""
    return sorted(set(COND_ATTR_RE.findall(xml or "")))


def collect_slas(ctx):
    c, scope = ctx.client, ctx.scope
    slas = c.get_all("slas?$select=slaid,name,description,objecttypecode,primaryentityotc,applicablefrom,slatype,"
                     "slaversion,isdefault,statecode,ismanaged,allowpauseresume,_businesshoursid_value,"
                     "_workflowid_value,modifiedon")
    items = []
    try:
        items = c.get_all("slaitems?$select=slaitemid,name,_slaid_value,sequencenumber,applicablewhenxml,"
                          "successconditionsxml,msdyn_pauseconfigurationxml,failureafter,warnafter,relatedfield,"
                          "allowpauseresume,applicableentity,_msdyn_slakpiid_value,actionflowuniquename,"
                          "_workflowid_value,_businesshoursid_value")
    except Exception as e:  # noqa: BLE001
        ctx.gap("slas", "itens de SLA (slaitems)", e)
    kpis = {}
    try:
        for k in c.get_all("msdyn_slakpis?$select=msdyn_slakpiid,msdyn_name,msdyn_entityname,msdyn_kpifield,"
                           "msdyn_applicablefromfield,statecode"):
            kpis[k["msdyn_slakpiid"].lower()] = k
    except Exception as e:  # noqa: BLE001
        ctx.gap("slas", "KPIs de SLA (msdyn_slakpis — só SLAs da Interface Unificada)", e, kind="limitação")
    cal_ids = {(x.get("_businesshoursid_value") or "").lower() for x in slas + items} - {""}
    cals = {}
    if cal_ids:
        try:
            cals = c.get_many("calendars", "calendarid", sorted(cal_ids), "name")
        except Exception as e:  # noqa: BLE001
            ctx.gap("slas", "calendários de horário comercial", e)
    otc = {x.get("otc"): x["logical"] for x in ctx.data.get("_tables_index") or [] if x.get("otc") is not None}
    by_sla = {}
    for it in items:
        by_sla.setdefault((it.get("_slaid_value") or "").lower(), []).append(it)

    out = []
    for s in slas:
        sid = s["slaid"].lower()
        table = otc.get(s.get("objecttypecode")) or otc.get(s.get("primaryentityotc"))
        reason = ("tabela do escopo" if table in scope.tables else None) or scope.reason(s.get("name"), s.get("ismanaged"), sid)
        if not reason:
            continue
        its = []
        for it in sorted(by_sla.get(sid, []), key=lambda x: x.get("sequencenumber") or 0):
            k = kpis.get((it.get("_msdyn_slakpiid_value") or "").lower()) or {}
            its.append({
                "id": it["slaitemid"], "name": it.get("name"), "sequence": it.get("sequencenumber"),
                "entity": it.get("applicableentity") or table,
                "failure_min": it.get("failureafter"), "warning_min": it.get("warnafter"),
                "kpi": k.get("msdyn_name"), "kpi_field": k.get("msdyn_kpifield") or it.get("relatedfield"),
                "applicable_from": k.get("msdyn_applicablefromfield"),
                "applicable_when_fields": _fetch_fields(it.get("applicablewhenxml")),
                "success_fields": _fetch_fields(it.get("successconditionsxml")),
                "pause_fields": _fetch_fields(it.get("msdyn_pauseconfigurationxml")),
                "linked_entities": sorted(set(ENTITY_RE.findall((it.get("applicablewhenxml") or "") +
                                                                (it.get("successconditionsxml") or ""))) - {table}),
                "action_flow": it.get("actionflowuniquename"),
                "workflow_id": (it.get("_workflowid_value") or "").lower() or None,
                "business_hours": (cals.get((it.get("_businesshoursid_value") or "").lower()) or {}).get("name"),
                "allow_pause": it.get("allowpauseresume"),
            })
        out.append({
            "id": sid, "name": s.get("name"), "description": s.get("description"), "table": table,
            "type": fv(s, "slatype") or s.get("slatype"), "version": fv(s, "slaversion") or s.get("slaversion"),
            "state": fv(s, "statecode") or s.get("statecode"), "default": s.get("isdefault"),
            "managed": s.get("ismanaged"), "applicable_from": s.get("applicablefrom"),
            "allow_pause": s.get("allowpauseresume"),
            "business_hours": (cals.get((s.get("_businesshoursid_value") or "").lower()) or {}).get("name"),
            "workflow_id": (s.get("_workflowid_value") or "").lower() or None,
            "modified": day(s.get("modifiedon")), "scope_reason": reason,
            "solutions": scope.solutions_of(sid), "items": its,
        })
    ctx.stats["slas"] = {"org_total": len(slas), "scope": len(out), "items": sum(len(x["items"]) for x in out),
                         "active": sum(1 for x in out if str(x["state"]).lower() in ("active", "ativo", "1"))}
    ctx.data["slas"] = sorted(out, key=lambda x: (x["table"] or "", x["name"] or ""))
