import base64

from .. import secrets
from ..util import day, fv

STAGES = {10: "PreValidation", 20: "PreOperation", 30: "MainOperation", 40: "PostOperation", 50: "PostOperation (legado)"}
MODES = {0: "Síncrono", 1: "Assíncrono"}
ISOLATION = {1: "None", 2: "Sandbox", 3: "External"}
SOURCE = {0: "Database", 1: "Disk", 2: "Normal", 3: "AzureWebApp", 4: "FileStore"}
IMAGE = {0: "PreImage", 1: "PostImage", 2: "Both"}


def collect_plugins(ctx):
    c, scope, cfg = ctx.client, ctx.scope, ctx.cfg

    asms = c.get_all(
        "pluginassemblies?$select=pluginassemblyid,name,version,culture,publickeytoken,isolationmode,"
        "sourcetype,ismanaged,modifiedon,createdon")
    types = c.get_all(
        "plugintypes?$select=plugintypeid,name,typename,friendlyname,isworkflowactivity,workflowactivitygroupname,"
        "_pluginassemblyid_value")
    # customizationlevel eq 1 = steps registrados por clientes/ISVs (exclui os internos da plataforma).
    steps = c.get_all(
        "sdkmessageprocessingsteps?$select=sdkmessageprocessingstepid,name,description,stage,mode,rank,statecode,"
        "filteringattributes,configuration,ismanaged,supporteddeployment,asyncautodelete,_plugintypeid_value,"
        "_eventhandler_value,_impersonatinguserid_value,modifiedon"
        "&$expand=sdkmessageid($select=name),sdkmessagefilterid($select=primaryobjecttypecode)"
        "&$filter=customizationlevel eq 1")
    images = c.get_all(
        "sdkmessageprocessingstepimages?$select=name,entityalias,imagetype,attributes,"
        "_sdkmessageprocessingstepid_value&$filter=customizationlevel eq 1")

    asm_scope = {}
    for a in asms:
        r = scope.reason(a["name"], a.get("ismanaged"), a["pluginassemblyid"])
        if r:
            asm_scope[a["pluginassemblyid"]] = r
    type_by_id = {t["plugintypeid"]: t for t in types}
    scoped_types = {t["plugintypeid"] for t in types
                    if t.get("_pluginassemblyid_value") in asm_scope
                    or scope.reason(t.get("typename"), None, t["plugintypeid"])}
    asm_by_id = {a["pluginassemblyid"]: a for a in asms}

    imgs_by_step = {}
    for im in images:
        imgs_by_step.setdefault(im.get("_sdkmessageprocessingstepid_value"), []).append({
            "name": im.get("name"), "alias": im.get("entityalias"),
            "type": IMAGE.get(im.get("imagetype"), im.get("imagetype")),
            "attributes": im.get("attributes"),
        })

    out_steps = []
    for s in steps:
        tid = s.get("_plugintypeid_value")
        entity = (s.get("sdkmessagefilterid") or {}).get("primaryobjecttypecode")
        handler_kind = s.get("_eventhandler_value@Microsoft.Dynamics.CRM.lookuplogicalname")
        in_scope = (tid in scoped_types
                    or scope.reason(s.get("name"), s.get("ismanaged"), s["sdkmessageprocessingstepid"], keyword_match=False)
                    or (handler_kind == "serviceendpoint" and entity in scope.tables))
        if not in_scope:
            continue
        t = type_by_id.get(tid) or {}
        asm = asm_by_id.get(t.get("_pluginassemblyid_value")) or {}
        config = s.get("configuration")
        out_steps.append({
            "id": s["sdkmessageprocessingstepid"], "name": s.get("name"),
            "type": t.get("typename"), "assembly": asm.get("name"),
            "handler_kind": handler_kind or "plugintype",
            "handler": fv(s, "_eventhandler_value"),
            "message": (s.get("sdkmessageid") or {}).get("name"), "entity": entity if entity != "none" else None,
            "stage": STAGES.get(s.get("stage"), s.get("stage")), "mode": MODES.get(s.get("mode"), s.get("mode")),
            "rank": s.get("rank"), "enabled": s.get("statecode") == 0,
            "filtering": s.get("filteringattributes"), "managed": s.get("ismanaged"),
            "impersonating": fv(s, "_impersonatinguserid_value"),
            "has_config": bool(config), "config_secret_hits": secrets.scan(config),
            "async_autodelete": s.get("asyncautodelete"), "modified": day(s.get("modifiedon")),
            "images": imgs_by_step.get(s["sdkmessageprocessingstepid"], []),
            "solutions": scope.solutions_of(s["sdkmessageprocessingstepid"]),
        })

    out_asms = []
    for aid, reason in asm_scope.items():
        a = asm_by_id[aid]
        rec = {
            "id": aid, "name": a["name"], "version": a.get("version"), "culture": a.get("culture"),
            "publickeytoken": a.get("publickeytoken"),
            "isolation": ISOLATION.get(a.get("isolationmode"), a.get("isolationmode")),
            "source": SOURCE.get(a.get("sourcetype"), a.get("sourcetype")),
            "managed": a.get("ismanaged"), "modified": day(a.get("modifiedon")), "created": day(a.get("createdon")),
            "scope_reason": reason, "solutions": scope.solutions_of(aid),
            "types": sorted([
                {"id": t["plugintypeid"], "typename": t.get("typename"), "friendly": t.get("friendlyname"),
                 "workflow_activity": t.get("isworkflowactivity"), "group": t.get("workflowactivitygroupname"),
                 "steps": sum(1 for s in out_steps if s["type"] == t.get("typename"))}
                for t in types if t.get("_pluginassemblyid_value") == aid], key=lambda x: x["typename"] or ""),
            "binary": None,
        }
        if cfg.deep.get("plugin_binaries"):
            try:
                d = c.get(f"pluginassemblies({aid})?$select=content")
                if d.get("content"):
                    rel = f"bin/{a['name']}.dll"  # relativo ao snapshot (o staging muda de nome ao publicar)
                    path = (ctx.out_dir or cfg.raw_dir) / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(base64.b64decode(d["content"]))
                    rec["binary"] = rel
            except Exception as e:  # noqa: BLE001
                ctx.gap("plugins", f"binário de {a['name']}", e)
        out_asms.append(rec)

    ctx.stats["plugins"] = {
        "org_assemblies": len(asms), "assemblies": len(out_asms),
        "org_types": len(types), "types": sum(len(a["types"]) for a in out_asms),
        "org_custom_steps": len(steps), "steps": len(out_steps),
    }
    ctx.data["plugins"] = {"assemblies": sorted(out_asms, key=lambda x: x["name"]),
                           "steps": sorted(out_steps, key=lambda x: (x["entity"] or "", x["message"] or "", x["name"] or ""))}


def collect_customapis(ctx):
    c, scope = ctx.client, ctx.scope
    apis = c.get_first_ok(
        "customapis?$select=customapiid,uniquename,name,displayname,description,bindingtype,boundentitylogicalname,"
        "isfunction,isprivate,allowedcustomprocessingsteptype,ismanaged,_plugintypeid_value"
        "&$expand=CustomAPIRequestParameters($select=uniquename,type,isoptional),"
        "CustomAPIResponseProperties($select=uniquename,type)",
        "customapis?$select=customapiid,uniquename,name,bindingtype,boundentitylogicalname,isfunction,ismanaged,"
        "_plugintypeid_value",
    )
    out = []
    for a in apis:
        reason = scope.reason(a["uniquename"], a.get("ismanaged"), a["customapiid"])
        if not reason:
            continue
        out.append({
            "id": a["customapiid"], "unique": a["uniquename"], "name": a.get("displayname") or a.get("name"),
            "description": a.get("description"),
            "binding": fv(a, "bindingtype") or a.get("bindingtype"), "bound_entity": a.get("boundentitylogicalname"),
            "is_function": a.get("isfunction"), "private": a.get("isprivate"),
            "steps_allowed": fv(a, "allowedcustomprocessingsteptype"),
            "plugin_type": fv(a, "_plugintypeid_value"), "managed": a.get("ismanaged"), "scope_reason": reason,
            "request": [{"name": p["uniquename"], "type": fv(p, "type") or p.get("type"), "optional": p.get("isoptional")}
                        for p in a.get("CustomAPIRequestParameters") or []],
            "response": [{"name": p["uniquename"], "type": fv(p, "type") or p.get("type")}
                         for p in a.get("CustomAPIResponseProperties") or []],
            "solutions": scope.solutions_of(a["customapiid"]),
        })
    ctx.stats["customapis"] = {"org_total": len(apis), "scope": len(out)}
    ctx.data["customapis"] = sorted(out, key=lambda x: x["unique"])


def collect_serviceendpoints(ctx):
    """Webhooks / Azure Service Bus / Event Hub registrados — integrações saindo do Dataverse."""
    c, scope = ctx.client, ctx.scope
    eps = c.get_first_ok(
        "serviceendpoints?$select=serviceendpointid,name,contract,url,namespaceaddress,path,authtype,ismanaged,messageformat",
        "serviceendpoints?$select=serviceendpointid,name,contract,url,ismanaged",
    )
    out = []
    for e in eps:
        reason = scope.reason(e.get("name"), e.get("ismanaged"), e["serviceendpointid"])
        if not reason:
            continue
        out.append({
            "id": e["serviceendpointid"], "name": e.get("name"),
            "contract": fv(e, "contract") or e.get("contract"), "auth": fv(e, "authtype"),
            "url": secrets.redact(e.get("url") or e.get("namespaceaddress")), "path": e.get("path"),
            "managed": e.get("ismanaged"), "scope_reason": reason,
            "secret_hits": secrets.scan(e.get("url")),
        })
    ctx.stats["serviceendpoints"] = {"org_total": len(eps), "scope": len(out)}
    ctx.data["serviceendpoints"] = out
