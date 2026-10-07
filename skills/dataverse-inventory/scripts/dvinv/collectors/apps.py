from ..util import day, fv


def collect_apps(ctx):
    c, scope = ctx.client, ctx.scope
    out = {"modeldriven": [], "canvas": [], "bots": []}

    try:
        apps = c.get_all("appmodules?$select=appmoduleid,appmoduleidunique,name,uniquename,ismanaged,modifiedon,description")
        for a in apps:
            reason = scope.reason(a.get("uniquename"), a.get("ismanaged"), a.get("appmoduleid"))
            if reason:
                out["modeldriven"].append({
                    "id": a["appmoduleid"], "name": a.get("name"), "unique": a.get("uniquename"),
                    "managed": a.get("ismanaged"), "modified": day(a.get("modifiedon")),
                    "description": a.get("description"), "scope_reason": reason,
                    "solutions": scope.solutions_of(a["appmoduleid"]),
                })
        # Componentes de cada app (tabelas, forms, views, sitemap…): base da dependência app → tabela.
        uniq = {a.get("appmoduleidunique"): a["appmoduleid"] for a in apps if a.get("appmoduleidunique")}
        if out["modeldriven"]:
            try:
                comps = c.get_all("appmodulecomponents?$select=componenttype,objectid,_appmoduleidunique_value")
                by_app = {}
                for comp in comps:
                    aid = uniq.get(comp.get("_appmoduleidunique_value"))
                    if aid:
                        by_app.setdefault(aid, []).append({"type": comp.get("componenttype"),
                                                           "objectid": (comp.get("objectid") or "").lower()})
                for a in out["modeldriven"]:
                    a["components"] = by_app.get(a["id"], [])
            except Exception as e:  # noqa: BLE001
                ctx.gap("apps", "componentes dos apps model-driven", e)
    except Exception as e:  # noqa: BLE001
        ctx.gap("apps", "appmodules (model-driven)", e)

    try:
        canv = c.get_first_ok(
            "canvasapps?$select=canvasappid,name,displayname,ismanaged,canvasapptype,lastpublishtime",
            "canvasapps?$select=canvasappid,name,displayname,ismanaged",
        )
        for a in canv:
            reason = scope.reason(a.get("name"), a.get("ismanaged"), a.get("canvasappid"))
            if reason:
                out["canvas"].append({
                    "id": a["canvasappid"], "name": a.get("displayname") or a.get("name"), "unique": a.get("name"),
                    "managed": a.get("ismanaged"), "kind": fv(a, "canvasapptype") or a.get("canvasapptype"),
                    "published": day(a.get("lastpublishtime")), "scope_reason": reason,
                    "solutions": scope.solutions_of(a["canvasappid"]),
                })
    except Exception as e:  # noqa: BLE001
        ctx.gap("apps", "canvasapps (apenas apps em solução aparecem no Dataverse)", e)

    try:
        bots = c.get_all("bots?$select=botid,name,schemaname,statecode,ismanaged,modifiedon")
        for b in bots:
            reason = scope.reason(b.get("schemaname"), b.get("ismanaged"), b.get("botid"))
            if reason:
                out["bots"].append({
                    "id": b["botid"], "name": b.get("name"), "unique": b.get("schemaname"),
                    "managed": b.get("ismanaged"), "active": b.get("statecode") == 0,
                    "modified": day(b.get("modifiedon")), "scope_reason": reason,
                    "solutions": scope.solutions_of(b["botid"]),
                })
    except Exception as e:  # noqa: BLE001
        ctx.gap("apps", "bots (Copilot Studio)", e)

    ctx.stats["apps"] = {k: len(v) for k, v in out.items()}
    ctx.data["apps"] = out
