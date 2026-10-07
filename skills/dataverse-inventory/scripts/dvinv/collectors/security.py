"""Business units, papéis, equipes, perfis de segurança de campo e usuários.

Lições aplicadas:
- Papéis são copiados para cada BU (N nomes × M BUs linhas na tabela roles) → só a cópia da BU raiz.
- RetrieveRolePrivilegesRole devolve nomes de privilégio com a SchemaName (prvReadcontoso_Foo),
  não a LogicalName → casar sem diferenciar maiúsculas.
- Agregações nativas param em ~50.000 registros → falha vira lacuna declarada, não número inventado.
"""

import re

from ..util import fv

PRV_RE = re.compile(r"^prv(Create|Read|Write|Delete|Append|AppendTo|Assign|Share)(.+)$")


def collect_security(ctx):
    c, scope, cfg = ctx.client, ctx.scope, ctx.cfg
    out = {"business_units": [], "roles": [], "teams": [], "field_security_profiles": [], "users": {},
           "privilege_matrix": None}

    bus = c.get_all("businessunits?$select=businessunitid,name,isdisabled,_parentbusinessunitid_value")
    root = next((b for b in bus if not b.get("_parentbusinessunitid_value")), None)
    out["business_units"] = [{"id": b["businessunitid"], "name": b["name"], "disabled": b.get("isdisabled"),
                              "parent": fv(b, "_parentbusinessunitid_value"),
                              "parent_id": b.get("_parentbusinessunitid_value")} for b in bus]

    roles_all = c.get_all("roles?$select=roleid,name,ismanaged,_businessunitid_value,_parentrootroleid_value")
    root_roles = [r for r in roles_all if root and r.get("_businessunitid_value") == root["businessunitid"]]
    for r in root_roles:
        reason = scope.reason(r["name"], r.get("ismanaged"), r["roleid"])
        out["roles"].append({"id": r["roleid"], "name": r["name"], "managed": r.get("ismanaged"),
                             "scope_reason": reason, "solutions": scope.solutions_of(r["roleid"])})
    out["roles"].sort(key=lambda r: r["name"].lower())

    teams = c.get_all("teams?$select=teamid,name,teamtype,isdefault,azureactivedirectoryobjectid,membershiptype,"
                      "_businessunitid_value")
    out["teams"] = [{"id": t["teamid"], "name": t["name"], "type": fv(t, "teamtype") or t.get("teamtype"),
                     "default": t.get("isdefault"), "bu": fv(t, "_businessunitid_value"),
                     "aad_group": bool(t.get("azureactivedirectoryobjectid")),
                     "membership": fv(t, "membershiptype"), "members": None} for t in teams]

    try:
        fsps = c.get_all("fieldsecurityprofiles?$select=fieldsecurityprofileid,name,ismanaged,description")
        out["field_security_profiles"] = [{"id": f["fieldsecurityprofileid"], "name": f["name"],
                                           "managed": f.get("ismanaged")} for f in fsps]
    except Exception as e:  # noqa: BLE001
        ctx.gap("security", "perfis de segurança de campo", e)

    try:
        users = c.get_all("systemusers?$select=systemuserid,isdisabled,accessmode,applicationid")
        out["users"] = {
            "total": len(users),
            "active": sum(1 for u in users if not u.get("isdisabled")),
            "active_human": sum(1 for u in users if not u.get("isdisabled") and not u.get("applicationid")
                                and u.get("accessmode") not in (1, 3, 4, 5)),
            "application": sum(1 for u in users if u.get("applicationid")),
            "disabled": sum(1 for u in users if u.get("isdisabled")),
        }
    except Exception as e:  # noqa: BLE001
        ctx.gap("security", "usuários", e)

    if cfg.deep.get("team_members"):
        fetch = ("<fetch aggregate='true'><entity name='teammembership'>"
                 "<attribute name='teamid' groupby='true' alias='t'/>"
                 "<attribute name='systemuserid' aggregate='count' alias='n'/></entity></fetch>")
        counts = None
        try:
            rows = c.get_all(f"teammemberships?fetchXml={fetch}")
            counts = {r.get("t"): r.get("n") for r in rows}
        except Exception:  # noqa: BLE001 — acima de 50 mil vínculos a agregação recusa: pagina só o teamid
            try:
                counts = {}
                for r in c.get_all("teammemberships?$select=teamid"):
                    counts[r.get("teamid")] = counts.get(r.get("teamid"), 0) + 1
            except Exception as e:  # noqa: BLE001
                ctx.gap("security", "membros por equipe", e)
        if counts is not None:
            for t in out["teams"]:
                t["members"] = counts.get(t["id"], 0)

    if cfg.deep.get("role_privileges"):
        schema_to_logical = {t["schema"].lower(): t["logical"] for t in ctx.data.get("tables", []) if t.get("schema")}
        matrix = {}
        for r in out["roles"]:
            if not r["scope_reason"]:
                continue
            try:
                privs = c.get(f"RetrieveRolePrivilegesRole(RoleId={r['id']})").get("RolePrivileges", [])
            except Exception as e:  # noqa: BLE001
                ctx.gap("security", f"privilégios do papel {r['name']}", e)
                continue
            for p in privs:
                m = PRV_RE.match(p.get("PrivilegeName", ""))
                if not m:
                    continue
                action, schema = m.groups()
                logical = schema_to_logical.get(schema.lower())
                if logical:
                    matrix.setdefault(logical, {}).setdefault(r["name"], {})[action] = p.get("Depth")
        out["privilege_matrix"] = matrix
        out["privilege_roles_analyzed"] = [r["name"] for r in out["roles"] if r["scope_reason"]]

    ctx.stats["security"] = {
        "business_units": len(out["business_units"]), "roles_root_bu": len(out["roles"]),
        "roles_scope": sum(1 for r in out["roles"] if r["scope_reason"]), "role_rows_all_bus": len(roles_all),
        "teams": len(out["teams"]), "users_active": out["users"].get("active"),
    }
    ctx.data["security"] = out
