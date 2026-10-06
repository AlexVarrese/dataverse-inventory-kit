from ..config import SYSTEM_SOLUTIONS
from ..util import day, fv


def collect_environment(ctx):
    c = ctx.client
    env = {"name": ctx.cfg.name, "url": ctx.cfg.url}
    try:
        env["version"] = c.get("RetrieveVersion()").get("Version")
    except Exception as e:  # noqa: BLE001
        ctx.gap("environment", "RetrieveVersion", e)
    try:
        who = c.get("WhoAmI()")
        env["caller_userid"] = who.get("UserId")
        env["organizationid"] = who.get("OrganizationId")
    except Exception as e:  # noqa: BLE001
        ctx.gap("environment", "WhoAmI", e)
    try:
        org = c.get_first_ok(
            "organizations?$select=name,isauditenabled,plugintracelogsetting,languagecode,createdon",
            "organizations?$select=name,createdon",
        )[0]
        env.update({
            "org_name": org.get("name"),
            "audit_enabled": org.get("isauditenabled"),
            "plugin_trace_setting": fv(org, "plugintracelogsetting") or org.get("plugintracelogsetting"),
            "base_language": org.get("languagecode"),
            "created": day(org.get("createdon")),
        })
    except Exception as e:  # noqa: BLE001
        ctx.gap("environment", "organizations", e)
    ctx.data["environment"] = env


def collect_solutions(ctx):
    c, scope, cfg = ctx.client, ctx.scope, ctx.cfg
    sols = c.get_all(
        "solutions?$select=solutionid,uniquename,friendlyname,version,ismanaged,installedon,modifiedon,description"
        "&$expand=publisherid($select=uniquename,friendlyname,customizationprefix)"
        "&$filter=isvisible eq true"
    )
    wanted = {s.lower() for s in cfg.solutions}
    out = []
    for s in sols:
        pub = s.get("publisherid") or {}
        uname = s["uniquename"]
        rec = {
            "id": s["solutionid"],
            "uniquename": uname,
            "name": s.get("friendlyname"),
            "version": s.get("version"),
            "managed": s.get("ismanaged"),
            "installed": day(s.get("installedon")),
            "modified": day(s.get("modifiedon")),
            "publisher": pub.get("friendlyname"),
            "publisher_prefix": pub.get("customizationprefix"),
            "description": s.get("description"),
            "in_scope": uname.lower() in wanted or bool(
                cfg.prefixes and (pub.get("customizationprefix") or "").lower() + "_" in cfg.prefixes),
            "component_counts": None,
        }
        out.append(rec)

    # Componentes: só para soluções do escopo e soluções não gerenciadas (a Default contém tudo e
    # soluções gerenciadas grandes estouram tempo sem agregar informação de pertencimento útil).
    for rec in out:
        uname = rec["uniquename"].lower()
        if uname in SYSTEM_SOLUTIONS:
            continue
        if not (uname in wanted or rec["managed"] is False):
            continue
        try:
            comps = c.get_all(
                f"solutioncomponents?$select=componenttype,objectid,rootcomponentbehavior"
                f"&$filter=_solutionid_value eq {rec['id']}"
            )
        except Exception as e:  # noqa: BLE001
            ctx.gap("solutions", f"componentes de {rec['uniquename']}", e)
            continue
        counts = {}
        for comp in comps:
            t = fv(comp, "componenttype") or str(comp.get("componenttype"))
            counts[t] = counts.get(t, 0) + 1
            oid = (comp.get("objectid") or "").lower()
            scope.membership.setdefault(oid, []).append(rec["uniquename"])
            if uname in wanted:
                scope.solution_objects.add(oid)
        rec["component_counts"] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
        rec["component_total"] = len(comps)

    missing = wanted - {r["uniquename"].lower() for r in out}
    for m in missing:
        ctx.gap("solutions", f"solução '{m}' configurada não existe neste ambiente", "não encontrada")

    ctx.stats["solutions"] = {"org_total": len(out), "unmanaged": sum(1 for r in out if r["managed"] is False)}
    ctx.data["solutions"] = out
