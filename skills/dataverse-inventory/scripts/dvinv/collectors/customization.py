"""Comandos modernos, mapeamentos entre tabelas, histórico de soluções e camadas de solução.

- commands: `appactions` (command designer) — botão, local (formulário/grade/subgrade), tabela, app, JavaScript
  (web resource + função) ou Power Fx (biblioteca de componente). Substituem o ribbon clássico.
- mappings: `entitymaps` + `attributemaps` — ao criar um registro a partir de outro (ex. contato a partir da conta),
  a plataforma copia campos: coluna de destino depende da coluna de origem.
- solution_history: `msdyn_solutionhistories` — importações/exportações/upgrades com resultado e mensagem de erro.
- solution_layers (deep, desligado por padrão): `msdyn_componentlayers` por tabela do escopo — quem sobrescreve o
  quê (camada "Active" = customização não gerenciada por cima das gerenciadas). Tabela virtual que exige uma
  consulta por componente (~3 s cada): ligue só quando for diagnosticar ALM.
"""

from ..util import day

FV = "@OData.Community.Display.V1.FormattedValue"


def collect_commands(ctx):
    c, scope = ctx.client, ctx.scope
    rows = c.get_all("appactions?$select=appactionid,name,uniquename,buttonlabeltext,location,context,contextvalue,"
                     "_contextentity_value,onclickeventtype,_onclickeventjavascriptwebresourceid_value,"
                     "onclickeventjavascriptfunctionname,onclickeventjavascriptparameters,onclickeventformulacomponentlibrary,"
                     "onclickeventformulafunctionname,visibilitytype,visibilityformulafunctionname,_appmoduleid_value,"
                     "hidden,isdisabled,ismanaged,modifiedon")
    wr_by_id = {(w.get("id") or "").lower(): w["name"] for w in ctx.data.get("webresources") or []}
    out = []
    for r in rows:
        aid = r["appactionid"].lower()
        table = r.get("contextvalue") if str(r.get("context" + FV) or "").lower() in ("entity", "table", "tabela") else None
        reason = scope.reason(r.get("uniquename") or r.get("name"), r.get("ismanaged"), aid) or (
            "comando em tabela do escopo" if table in scope.tables and r.get("ismanaged") is False else None)
        if not reason:
            continue
        wid = (r.get("_onclickeventjavascriptwebresourceid_value") or "").lower()
        out.append({
            "id": aid, "name": r.get("uniquename") or r.get("name"), "label": r.get("buttonlabeltext") or r.get("name"),
            "location": r.get("location" + FV) or r.get("location"), "table": table,
            "app_id": (r.get("_appmoduleid_value") or "").lower() or None,
            "action": r.get("onclickeventtype" + FV) or r.get("onclickeventtype"),
            "webresource": wr_by_id.get(wid) or r.get("_onclickeventjavascriptwebresourceid_value" + FV) or (wid or None),
            "function": r.get("onclickeventjavascriptfunctionname"),
            "parameters": r.get("onclickeventjavascriptparameters"),
            "powerfx": " / ".join(x for x in (r.get("onclickeventformulacomponentlibrary"),
                                               r.get("onclickeventformulafunctionname")) if x) or None,
            "visibility": r.get("visibilitytype" + FV) or r.get("visibilitytype"),
            "visibility_formula": r.get("visibilityformulafunctionname"),
            "hidden": r.get("hidden"), "disabled": r.get("isdisabled"), "managed": r.get("ismanaged"),
            "modified": day(r.get("modifiedon")), "scope_reason": reason, "solutions": scope.solutions_of(aid),
        })
    ctx.stats["commands"] = {"org_total": len(rows), "scope": len(out),
                             "javascript": sum(1 for x in out if x["webresource"])}
    ctx.data["commands"] = sorted(out, key=lambda x: (x["table"] or "", x["label"] or ""))


def collect_mappings(ctx):
    c, scope = ctx.client, ctx.scope
    maps = c.get_all("entitymaps?$select=entitymapid,sourceentityname,targetentityname,ismanaged")
    keep = {m["entitymapid"].lower(): m for m in maps
            if m.get("sourceentityname") in scope.tables or m.get("targetentityname") in scope.tables}
    fields = {}
    if keep:
        for a in c.get_all("attributemaps?$select=_entitymapid_value,sourceattributename,targetattributename,issystem,ismanaged"):
            mid = (a.get("_entitymapid_value") or "").lower()
            if mid in keep:
                fields.setdefault(mid, []).append({"source": a.get("sourceattributename"), "target": a.get("targetattributename"),
                                                   "system": a.get("issystem"), "managed": a.get("ismanaged")})
    out = [{"id": mid, "source": m["sourceentityname"], "target": m["targetentityname"], "managed": m.get("ismanaged"),
            "fields": sorted(fields.get(mid, []), key=lambda f: (f["system"] is True, f["target"] or ""))}
           for mid, m in keep.items()]
    ctx.stats["mappings"] = {"org_total": len(maps), "scope": len(out),
                             "fields": sum(len(m["fields"]) for m in out),
                             "custom_fields": sum(1 for m in out for f in m["fields"] if not f["system"])}
    ctx.data["mappings"] = sorted(out, key=lambda m: (m["source"], m["target"]))


def collect_solution_history(ctx):
    from .. import secrets
    c = ctx.client
    rows = c.get_all("msdyn_solutionhistories?$select=msdyn_name,msdyn_operation,msdyn_suboperation,msdyn_result,"
                     "msdyn_status,msdyn_starttime,msdyn_endtime,msdyn_totaltime,msdyn_solutionversion,msdyn_ismanaged,"
                     "msdyn_ispatch,msdyn_isoverwritecustomizations,msdyn_publishername,msdyn_errorcode,msdyn_exceptionmessage"
                     "&$orderby=msdyn_starttime desc", max_rows=int(ctx.cfg.deep.get("solution_history_max") or 5000))
    out = [{
        "solution": r.get("msdyn_name"), "operation": r.get("msdyn_operation" + FV) or r.get("msdyn_operation"),
        "suboperation": r.get("msdyn_suboperation" + FV), "ok": r.get("msdyn_result"),
        "status": r.get("msdyn_status" + FV), "start": r.get("msdyn_starttime"), "seconds": r.get("msdyn_totaltime"),
        "version": r.get("msdyn_solutionversion"), "managed": r.get("msdyn_ismanaged"), "patch": r.get("msdyn_ispatch"),
        "overwrite": r.get("msdyn_isoverwritecustomizations"), "publisher": r.get("msdyn_publishername"),
        "error_code": r.get("msdyn_errorcode") or None,
        "error": secrets.redact((r.get("msdyn_exceptionmessage") or "")[:600]) or None,
    } for r in rows]
    ctx.stats["solution_history"] = {"org_total": len(out), "failures": sum(1 for x in out if x["ok"] is False),
                                     "since": (out[-1]["start"] or "")[:10] if out else None}
    ctx.data["solution_history"] = out


def collect_solution_layers(ctx):
    c, cfg = ctx.client, ctx.cfg
    if not cfg.deep.get("solution_layers"):
        return
    tables = [t for t in ctx.data.get("tables") or [] if t.get("id")]
    cap = int(cfg.deep.get("solution_layers_max") or 500)
    if len(tables) > cap:
        ctx.gap("solution_layers", f"camadas lidas para {cap} de {len(tables)} tabelas (deep.solution_layers_max)",
                "consulta por componente (~3 s cada)", kind="limitação")
        tables = sorted(tables, key=lambda t: (not t.get("custom"), t["logical"]))[:cap]

    def fetch(t):
        return c.get_all(f"msdyn_componentlayers?$filter=(msdyn_componentid eq '{t['id']}' and "
                         "msdyn_solutioncomponentname eq 'Entity')&$select=msdyn_order,msdyn_solutionname,"
                         "msdyn_publishername,msdyn_overwritetime")

    out = {}
    for t, rows, err in c.parallel(fetch, tables, label="camadas de solução por tabela"):
        if err is not None:
            ctx.gap("solution_layers", f"camadas de {t['logical']}", err)
            continue
        out[t["logical"]] = [{"order": r.get("msdyn_order"), "solution": r.get("msdyn_solutionname"),
                              "publisher": r.get("msdyn_publishername"), "overwritten": day(r.get("msdyn_overwritetime"))}
                             for r in sorted(rows, key=lambda r: -(r.get("msdyn_order") or 0))]
    ctx.stats["solution_layers"] = {"scope": len(out),
                                    "active_on_top": sum(1 for ls in out.values() if ls and ls[0]["solution"] == "Active")}
    ctx.data["solution_layers"] = out

