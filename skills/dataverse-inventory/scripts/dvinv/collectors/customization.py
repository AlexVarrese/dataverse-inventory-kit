"""Comandos modernos, mapeamentos entre tabelas, histórico de soluções e camadas de solução.

- commands: `appactions` (command designer) — botão, local (formulário/grade/subgrade), tabela, app, JavaScript
  (web resource + função) ou Power Fx (biblioteca de componente). Substituem o ribbon clássico.
- mappings: `entitymaps` + `attributemaps` — ao criar um registro a partir de outro (ex. contato a partir da conta),
  a plataforma copia campos: coluna de destino depende da coluna de origem.
- solution_history (extra `historico`): `msdyn_solutionhistories` — importações/exportações/upgrades com resultado e mensagem de erro.
- solution_layers (extra `camadas`): `msdyn_componentlayers` dos componentes gerenciados do escopo — tabelas,
  colunas, relacionamentos, option sets, formulários, views, web resources, plugin steps, processos, papéis, apps e
  site maps. Quem sobrescreve o quê (camada "Active" = customização não gerenciada por cima das gerenciadas) e, na
  camada Active, as propriedades que ela altera e o valor imposto (aba "Atualizado" de "Ver camadas da solução" no
  maker portal). Tabela virtual que exige uma consulta por componente (0,3–2 s cada, em $batch).
"""

import json

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
    if not ctx.cfg.deep.get("solution_history"):
        return
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


# Tipos lidos, na ordem de prioridade do teto (deep.solution_layers_max): colunas por último, por serem muitas.
# Nome = msdyn_solutioncomponentname, conferido num ambiente real (2026-10): relacionamento é 'EntityRelationship';
# sem o id do componente a tabela virtual volta vazia.
LAYER_TYPES = ("Entity", "SystemForm", "SavedQuery", "Role", "WebResource", "SdkMessageProcessingStep", "Workflow",
               "AppModule", "SiteMap", "OptionSet", "EntityRelationship", "Attribute")
# Mudam a cada importação ou publicação sem mudar comportamento: não contam como propriedade alterada.
LAYER_NOISE = {"modifiedon", "modifiedby", "modifiedonbehalfby", "createdon", "createdby", "createdonbehalfby",
               "overwritetime", "versionnumber", "componentstate", "solutionid", "supportingsolutionid",
               "introducedversion", "publishedon", "componentidunique"}
# Conteúdo (código, xml de definição): só o nome da propriedade alterada; o valor fica no maker portal.
LAYER_CONTENT = {"content", "clientdata", "xaml", "formxml", "formjson", "fetchxml", "layoutxml", "layoutjson",
                 "offlinesqlquery", "sitemapxml", "sitemapxmlmanaged", "descriptor", "dependencyxml", "configuration",
                 "secureconfig", "contentjson", "uidata", "inputparameters", "triggeronupdateattributelist"}
LAYER_VALUE_MAX = 300
LAYER_SELECT = "msdyn_order,msdyn_solutionname,msdyn_publishername,msdyn_overwritetime"


def layer_targets(ctx):
    """Componentes gerenciados do escopo (só eles têm mais de uma camada), na ordem de LAYER_TYPES.

    → [{type, kind, id, name, table, key}]; `key` = chave da nota no vault, quando o tipo tem nota própria."""
    d, native_cols = ctx.data, ctx.cfg.deep.get("solution_layers_columns")
    out = {}

    def add(typ, kind, rec, name, table=None, key=None):
        if rec.get("managed") is True and rec.get("id"):
            oid = str(rec["id"]).lower()
            out.setdefault((typ, oid), {"type": typ, "kind": kind, "id": oid, "name": name, "table": table, "key": key})

    for t in d.get("tables") or []:
        add("Entity", "table", t, t["logical"], t["logical"], t["logical"])
        for col in t.get("columns") or []:
            if col.get("custom") or native_cols:
                add("Attribute", "column", col, f"{t['logical']}.{col['logical']}", t["logical"])
    for f in d.get("forms") or []:
        add("SystemForm", "form", f, f"{f.get('entity')} / {f.get('name')}", f.get("entity"))
    for x in d.get("views") or []:
        add("SavedQuery", "view", x, f"{x.get('entity')} / {x.get('name')}", x.get("entity"))
    for r in d.get("relationships") or []:
        add("EntityRelationship", "relationship", r, r["schema"], r.get("from"))
    for o in d.get("optionsets") or []:
        add("OptionSet", "optionset", o, o["name"])
    for w in d.get("webresources") or []:
        add("WebResource", "webresource", w, w["name"], None, w["name"])
    for s in (d.get("plugins") or {}).get("steps") or []:
        add("SdkMessageProcessingStep", "step", s, s.get("name") or s["id"], s.get("entity"), s["id"])
    for p in d.get("processes") or []:
        add("Workflow", "process", p, p.get("name") or p["id"], p.get("entity"), p["id"])
    for r in (d.get("security") or {}).get("roles") or []:
        if r.get("scope_reason"):
            add("Role", "role", r, r["name"], None, r["name"])
    for a in (d.get("apps") or {}).get("modeldriven") or []:
        add("AppModule", "app", a, a.get("name") or a["id"], None, a["id"])
    for s in d.get("sitemaps") or []:
        add("SiteMap", "sitemap", s, s.get("name") or s["id"], None, s["id"])
    rank = {t: i for i, t in enumerate(LAYER_TYPES)}
    return sorted(out.values(), key=lambda x: (rank[x["type"]], str(x["name"]).lower(), x["id"]))


def _layer_changes(raw):
    """msdyn_changes (JSON) → {propriedade: valor}, sem as que só mudam data/versão. None se ilegível."""
    try:
        attrs = (json.loads(raw) or {}).get("Attributes") or []
    except (ValueError, TypeError, AttributeError):
        return None
    return {a["Key"]: a.get("Value") for a in attrs
            if isinstance(a, dict) and a.get("Key") and a["Key"].lower() not in LAYER_NOISE}


def _short(value):
    from .. import secrets
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = secrets.redact(text)
    return text if len(text) <= LAYER_VALUE_MAX else text[:LAYER_VALUE_MAX] + " …"


def collect_solution_layers(ctx):
    c, cfg = ctx.client, ctx.cfg
    if not cfg.deep.get("solution_layers"):
        return
    todo = layer_targets(ctx)
    total = len(todo)
    cap = int(cfg.deep.get("solution_layers_max") or 3000)
    if total > cap:
        left = sorted({x["type"] for x in todo[cap:]}, key=LAYER_TYPES.index)
        ctx.gap("solution_layers", f"camadas lidas para {cap} de {total} componentes gerenciados "
                "(deep.solution_layers_max)", f"ficaram de fora componentes de: {', '.join(left)}", kind="limitação")
        todo = todo[:cap]

    def path(x, only_active=False):
        flt = " and msdyn_solutionname eq 'Active'" if only_active else ""
        return (f"msdyn_componentlayers?$filter=(msdyn_componentid eq '{x['id']}' and "
                f"msdyn_solutioncomponentname eq '{x['type']}'{flt})&$select={LAYER_SELECT}"
                f"{',msdyn_changes' if only_active else ''}")

    failed = 0

    def fail(x, err):
        nonlocal failed
        failed += 1
        if failed <= 20:
            ctx.gap("solution_layers", f"camadas de {x['type']} {x['name']}", err)

    # Etapa que não rodou nesta extração nem foi herdada: os componentes dela ficam sem camadas (declarado). Não
    # puxamos essas etapas junto (DEPENDS): --only solution_layers continua leve.
    sources = {"forms": "SystemForm", "views": "SavedQuery", "relationships": "EntityRelationship",
               "optionsets": "OptionSet", "webresources": "WebResource", "plugins": "SdkMessageProcessingStep",
               "processes": "Workflow", "security": "Role", "apps": "AppModule", "sitemaps": "SiteMap"}
    missing = [t for n, t in sources.items() if (ctx.status.get(n) or {}).get("status") in ("nao-executado", "falhou")]
    if missing:
        ctx.gap("solution_layers", f"camadas não lidas para: {', '.join(missing)}",
                "a etapa desses componentes não rodou nesta extração nem foi herdada", kind="limitação")

    # 1) a pilha de cada componente (leve). 2) onde a camada de cima é Active, só ela com as propriedades alteradas.
    # A tabela virtual só respeita `eq` no filtro (msdyn_order gt / ne são ignorados): pedir as alterações de todas
    # as camadas traria a de base inteira (formxml, todas as propriedades) para cada componente.
    comps = []
    for x, rows, err in c.get_batch(todo, path, pages=True, label="camadas de solução"):
        if err is not None:
            fail(x, err)
            continue
        comps.append(dict(x, layers=[
            {"order": r.get("msdyn_order"), "solution": r.get("msdyn_solutionname"),
             "publisher": r.get("msdyn_publishername"),
             "overwritten": None if str(r.get("msdyn_overwritetime") or "").startswith("1900") else
             day(r.get("msdyn_overwritetime"))}
            for r in sorted(rows, key=lambda r: -(r.get("msdyn_order") or 0))]))
    multi = [x for x in comps if len(x["layers"]) > 1]
    on_top = [x for x in multi if x["layers"][0]["solution"] == "Active"]
    for x, rows, err in c.get_batch(on_top, lambda x: path(x, only_active=True), pages=True,
                                    label="propriedades alteradas na camada Active"):
        if err is not None:
            fail(x, err)
            continue
        ch = _layer_changes((rows or [{}])[0].get("msdyn_changes")) if rows else None
        if ch is None:
            continue
        top = x["layers"][0]
        top["changed"] = sorted(ch, key=str.lower)
        top["values"] = {k: _short(v) for k, v in sorted(ch.items(), key=lambda kv: kv[0].lower())
                         if k.lower() not in LAYER_CONTENT}
    if failed > 20:
        ctx.gap("solution_layers", f"camadas de mais {failed - 20} componente(s)", "ver o log da extração")

    chs = [x["layers"][0].get("changed") for x in on_top]
    ctx.stats["solution_layers"] = {
        "scope": total, "queried": len(todo), "multi_layer": len(multi), "active_on_top": len(on_top),
        "active_changed": sum(1 for ch in chs if ch), "active_empty": sum(1 for ch in chs if ch == []),
        "failed": failed}
    ctx.data["solution_layers"] = {"format": "componentes/2", "components": comps}
