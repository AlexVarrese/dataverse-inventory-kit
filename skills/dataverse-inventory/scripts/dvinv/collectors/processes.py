"""Workflows clássicos, business rules, actions, BPFs, cloud flows e desktop flows (tabela workflow).

Lições aplicadas:
- type eq 1 (Definition) — o registro de Activation (type 2) duplicava a contagem (infla a contagem em ~25%).
- Cloud flows (category 5) só existem aqui se forem *solution-aware*; flows criados fora de solução
  ficam invisíveis para a Web API do Dataverse (lacuna declarada no manifest).
- Dono desativado em flow = automação que pode parar quando a conexão do usuário expirar.
"""

import json
import re
from collections import Counter
from urllib.parse import urlparse

from .. import secrets
from ..util import day, field_refs, fv

CATEGORY = {0: "Workflow", 1: "Dialog", 2: "Business Rule", 3: "Action", 4: "Business Process Flow",
            5: "Cloud Flow", 6: "Desktop Flow", 7: "AI Flow"}
# Ações Dataverse usam o entity set (contoso_projects); gatilhos usam o nome lógico (account).
ENTITY_RE = re.compile(r'"(?:entityName|subscriptionRequest/entityname)"\s*:\s*"([A-Za-z0-9_]+)"')


def classify_trigger(trigger):
    t = (trigger.get("type") or "").lower()
    kind = (trigger.get("kind") or "").lower()
    if t == "recurrence":
        return "Agendado"
    if t == "request" or kind in ("button", "powerappv2", "http", "teamsbot"):
        return "Instantâneo"
    return "Automatizado"


def describe_action(a):
    t = a.get("type", "?")
    inputs = a.get("inputs") or {}
    host = inputs.get("host") or {} if isinstance(inputs, dict) else {}
    params = inputs.get("parameters") or {} if isinstance(inputs, dict) else {}
    if t == "If":
        return "condição"
    if t == "Switch":
        return "switch"
    if t == "Foreach":
        return "loop (para cada)"
    if t == "Until":
        return "loop (até)"
    if t == "Scope":
        return "escopo"
    if t in ("OpenApiConnection", "ApiConnection", "OpenApiConnectionWebhook"):
        api = (host.get("apiId") or host.get("connectionName") or "").rsplit("/", 1)[-1].replace("shared_", "")
        op = host.get("operationId")
        ent = params.get("entityName")
        head = ".".join(x for x in (api, op) if x) or "conector"
        return head + (f" — {ent}" if ent else "")
    if t == "Http":
        uri = inputs.get("uri") if isinstance(inputs, dict) else None
        target = urlparse(uri).netloc if isinstance(uri, str) and uri.startswith("http") else "(URI dinâmica)"
        return " ".join(x for x in ("HTTP", inputs.get("method"), target) if x)
    if t == "Workflow":
        return "child flow"
    if t == "Response":
        return "resposta HTTP"
    if t == "Terminate":
        return f"terminar ({inputs.get('runStatus', '')})" if isinstance(inputs, dict) else "terminar"
    return t


def parse_flow(clientdata):
    info = {"trigger": None, "trigger_detail": None, "connectors": [], "tables": [], "http_hosts": [],
            "child_flows": 0, "actions": 0, "outline": [], "action_types": {}, "conditions": 0,
            "error_handlers": 0, "max_depth": 0}
    try:
        cd = json.loads(clientdata)
    except Exception:  # noqa: BLE001
        return info
    props = cd.get("properties", cd)
    definition = props.get("definition") or {}
    triggers = definition.get("triggers") or {}
    if triggers:
        name, trig = next(iter(triggers.items()))
        info["trigger"] = classify_trigger(trig)
        op = ((trig.get("inputs") or {}).get("host") or {}).get("operationId")
        info["trigger_detail"] = f"{name} ({op})" if op else name
    refs = props.get("connectionReferences") or {}
    apis = set()
    info["connrefs"] = sorted({(r.get("connection") or {}).get("connectionReferenceLogicalName")
                               for r in refs.values() if (r.get("connection") or {}).get("connectionReferenceLogicalName")})
    info["child_flow_ids"] = []
    for r in refs.values():
        api = r.get("api", {}).get("name") or r.get("apiName") or ""
        if not api and isinstance(r.get("id"), str):
            api = r["id"].rsplit("/", 1)[-1]
        if api:
            apis.add(api.replace("shared_", ""))
    info["connectors"] = sorted(apis)
    info["tables"] = sorted(set(ENTITY_RE.findall(clientdata)))

    hosts = set()
    types = Counter()

    def walk(actions, depth=0):
        for name, a in (actions or {}).items():
            info["actions"] += 1
            info["max_depth"] = max(info["max_depth"], depth)
            types[a.get("type", "?")] += 1
            if a.get("type") in ("If", "Switch"):
                info["conditions"] += 1
            # "catch" do Power Automate: ação que roda depois de Failed/TimedOut de outra
            if any(set(v or []) & {"Failed", "TimedOut"} for v in (a.get("runAfter") or {}).values()):
                info["error_handlers"] += 1
            if len(info["outline"]) < 400:
                info["outline"].append(f"{'  ' * depth}- {name.replace('_', ' ')} [{describe_action(a)}]")
            if a.get("type") == "Http":
                uri = (a.get("inputs") or {}).get("uri") or ""
                if isinstance(uri, str) and uri.startswith("http"):
                    hosts.add(urlparse(uri).netloc)
                else:
                    hosts.add("(URI dinâmica)")
            if a.get("type") == "Workflow":
                info["child_flows"] += 1
                wid = (((a.get("inputs") or {}).get("host") or {}).get("workflowReferenceName"))
                if wid:
                    info["child_flow_ids"].append(wid.lower())
            walk(a.get("actions"), depth + 1)
            if (a.get("else") or {}).get("actions"):
                if len(info["outline"]) < 400:
                    info["outline"].append(f"{'  ' * (depth + 1)}- (senão)")
                walk(a["else"]["actions"], depth + 2)
            for cname, case in (a.get("cases") or {}).items():
                walk(case.get("actions"), depth + 1)
            walk((a.get("default") or {}).get("actions"), depth + 1)
    walk(definition.get("actions"))
    info["http_hosts"] = sorted(hosts)
    info["action_types"] = dict(types.most_common())
    return info


def collect_processes(ctx):
    c, scope, cfg = ctx.client, ctx.scope, ctx.cfg
    select = ("workflowid,name,uniquename,category,type,statecode,statuscode,mode,scope,primaryentity,ismanaged,"
              "createdon,modifiedon,triggeroncreate,triggerondelete,triggeronupdateattributelist,ondemand,"
              "subprocess,_ownerid_value,_modifiedby_value,description")
    rows = c.get_all(
        f"workflows?$select={select}&$expand=owninguser($select=fullname,domainname,isdisabled)"
        "&$filter=type eq 1 or category eq 5")
    set_to_logical = {t.get("entityset"): t["logical"] for t in ctx.data.get("tables", []) if t.get("entityset")}
    known_fields = {c["logical"] for t in ctx.data.get("tables", []) for c in t.get("columns", []) if c.get("custom")}
    # nomes citáveis em definições: variáveis de ambiente e Custom APIs (para a matriz de dependências)
    alm = ctx.data.get("alm") or {}
    envvar_names = {e["name"].lower(): e["name"] for e in alm.get("envvars") or []}
    api_names = {a["unique"].lower(): a["unique"] for a in ctx.data.get("customapis") or []}

    def named_refs(text):
        toks = set(field_refs(text, set(envvar_names) | set(api_names)))
        return (sorted(envvar_names[t] for t in toks if t in envvar_names),
                sorted(api_names[t] for t in toks if t in api_names))
    # Definições (clientdata/xaml) em lotes — uma chamada por processo custava minutos em orgs grandes.
    defs = {}
    if cfg.deep.get("flow_definitions") or cfg.deep.get("process_definitions"):
        want = []
        for w in rows:
            cat = w.get("category")
            ent_ = w.get("primaryentity")
            r_ = scope.reason(w.get("name"), w.get("ismanaged"), w["workflowid"]) or (
                ent_ in scope.tables and w.get("ismanaged") is False)
            if r_ and ((cat == 5 and cfg.deep.get("flow_definitions")) or
                       (cat in (0, 2, 3, 4) and cfg.deep.get("process_definitions"))):
                want.append(w["workflowid"])
        defs = c.get_many("workflows", "workflowid", want, "clientdata,xaml", batch=5)
    out = []
    for w in rows:
        ent = w.get("primaryentity")
        ent = None if ent in (None, "none") else ent
        reason = scope.reason(w.get("name"), w.get("ismanaged"), w["workflowid"])
        if not reason and ent in scope.tables and w.get("ismanaged") is False:
            reason = "table"
        if not reason:
            continue
        cat = w.get("category")
        ou = w.get("owninguser") or {}
        owner_type = w.get("_ownerid_value@Microsoft.Dynamics.CRM.lookuplogicalname")
        rec = {
            "id": w["workflowid"], "name": w.get("name"), "unique": w.get("uniquename"),
            "category": CATEGORY.get(cat, fv(w, "category") or str(cat)),
            "entity": ent, "state": fv(w, "statecode") or w.get("statecode"),
            "active": w.get("statecode") == 1, "managed": w.get("ismanaged"),
            "mode": ("Tempo real (síncrono)" if w.get("mode") == 1 else "Segundo plano") if cat == 0 else None,
            "on_create": w.get("triggeroncreate"), "on_delete": w.get("triggerondelete"),
            "on_update": w.get("triggeronupdateattributelist"), "on_demand": w.get("ondemand"),
            "child": w.get("subprocess"),
            "created": day(w.get("createdon")), "modified": day(w.get("modifiedon")),
            "owner": fv(w, "_ownerid_value") or ou.get("fullname"),
            "owner_kind": "Equipe" if owner_type == "team" else "Usuário",
            "owner_disabled": bool(ou.get("isdisabled")),
            "modified_by": fv(w, "_modifiedby_value"),
            "description": w.get("description"), "scope_reason": reason,
            "solutions": scope.solutions_of(w["workflowid"]),
        }
        if cat == 5 and cfg.deep.get("flow_definitions"):
            try:
                d = defs.get(w["workflowid"].lower())
                if d is None:
                    raise RuntimeError("não retornado pela API")
                cdata = d.get("clientdata") or ""
                rec.update(parse_flow(cdata))
                rec["tables"] = sorted({set_to_logical.get(t, t) for t in rec["tables"]})
                rec["field_refs"] = field_refs(cdata, known_fields)
                rec["envvar_refs"], rec["customapi_refs"] = named_refs(cdata)
                rec["secret_hits"] = secrets.scan(cdata)
            except Exception as e:  # noqa: BLE001
                ctx.gap("processes", f"definição do flow {w.get('name')}", e)
        if cat in (0, 2, 3, 4) and cfg.deep.get("process_definitions"):
            # XAML (workflow/action/BPF) e clientdata (business rule) citam os campos que o processo lê/grava.
            try:
                d = defs.get(w["workflowid"].lower())
                if d is None:
                    raise RuntimeError("não retornado pela API")
                text = (d.get("xaml") or "") + (d.get("clientdata") or "")
                rec["field_refs"] = field_refs(text, known_fields)
                rec["secret_hits"] = secrets.scan(text)
                rec["envvar_refs"], rec["customapi_refs"] = named_refs(text)
            except Exception as e:  # noqa: BLE001
                ctx.gap("processes", f"definição de {w.get('name')}", e)
        if w.get("triggeronupdateattributelist"):
            rec["field_refs"] = sorted(set(rec.get("field_refs") or []) |
                                       {f.strip().lower() for f in w["triggeronupdateattributelist"].split(",")})
        out.append(rec)

    by_cat = {}
    for r in out:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    ctx.stats["processes"] = {"org_definitions": len(rows), "scope": len(out), "by_category": by_cat}
    ctx.gap("processes", "cloud flows fora de solução",
            "flows não solution-aware não aparecem na tabela workflow — inventariar via Power Platform admin/PAC CLI")
    ctx.data["processes"] = sorted(out, key=lambda x: (x["category"], x["entity"] or "", x["name"] or ""))
