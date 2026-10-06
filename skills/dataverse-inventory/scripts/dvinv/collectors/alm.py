from .. import secrets
from ..util import day, fv

SECRET_TYPE = 100000005  # environmentvariabledefinition.type = Secret (Key Vault)


def collect_alm(ctx):
    c, scope = ctx.client, ctx.scope
    out = {"envvars": [], "connrefs": [], "connectors": []}

    try:
        defs = c.get_all(
            "environmentvariabledefinitions?$select=environmentvariabledefinitionid,schemaname,displayname,type,"
            "defaultvalue,ismanaged,isrequired,description"
            "&$expand=environmentvariabledefinition_environmentvariablevalue($select=value)")
        for d in defs:
            reason = scope.reason(d["schemaname"], d.get("ismanaged"), d["environmentvariabledefinitionid"])
            if not reason:
                continue
            vals = d.get("environmentvariabledefinition_environmentvariablevalue") or []
            is_secret = d.get("type") == SECRET_TYPE
            value = None if is_secret else (vals[0].get("value") if vals else None)
            default = None if is_secret else d.get("defaultvalue")
            out["envvars"].append({
                "id": d["environmentvariabledefinitionid"], "name": d["schemaname"], "display": d.get("displayname"),
                "type": fv(d, "type") or d.get("type"), "managed": d.get("ismanaged"), "required": d.get("isrequired"),
                "has_value": bool(vals), "has_default": bool(d.get("defaultvalue")),
                "value": secrets.redact(value), "default": secrets.redact(default),
                "secret_hits": secrets.scan(value) + secrets.scan(default),
                "description": d.get("description"), "scope_reason": reason,
                "solutions": scope.solutions_of(d["environmentvariabledefinitionid"]),
            })
    except Exception as e:  # noqa: BLE001
        ctx.gap("alm", "variáveis de ambiente", e)

    try:
        refs = c.get_all(
            "connectionreferences?$select=connectionreferenceid,connectionreferencelogicalname,"
            "connectionreferencedisplayname,connectorid,connectionid,ismanaged,statecode,modifiedon")
        for r in refs:
            reason = scope.reason(r["connectionreferencelogicalname"], r.get("ismanaged"), r["connectionreferenceid"])
            if not reason:
                continue
            out["connrefs"].append({
                "id": r["connectionreferenceid"], "name": r["connectionreferencelogicalname"],
                "display": r.get("connectionreferencedisplayname"),
                "connector": (r.get("connectorid") or "").rsplit("/", 1)[-1].replace("shared_", ""),
                "connected": bool(r.get("connectionid")), "managed": r.get("ismanaged"),
                "active": r.get("statecode") == 0, "modified": day(r.get("modifiedon")), "scope_reason": reason,
                "solutions": scope.solutions_of(r["connectionreferenceid"]),
            })
    except Exception as e:  # noqa: BLE001
        ctx.gap("alm", "referências de conexão", e)

    try:
        cons = c.get_first_ok("connectors?$select=connectorid,name,displayname,connectortype,ismanaged,description",
                              "connectors?$select=connectorid,name,displayname,ismanaged")
        for k in cons:
            reason = scope.reason(k.get("name"), k.get("ismanaged"), k["connectorid"])
            if not reason:
                continue
            out["connectors"].append({
                "id": k["connectorid"], "name": k.get("displayname") or k.get("name"), "unique": k.get("name"),
                "kind": fv(k, "connectortype"), "managed": k.get("ismanaged"), "description": k.get("description"),
                "scope_reason": reason, "solutions": scope.solutions_of(k["connectorid"]),
            })
    except Exception as e:  # noqa: BLE001
        ctx.gap("alm", "conectores customizados", e)

    ctx.stats["alm"] = {k: len(v) for k, v in out.items()}
    ctx.data["alm"] = out
