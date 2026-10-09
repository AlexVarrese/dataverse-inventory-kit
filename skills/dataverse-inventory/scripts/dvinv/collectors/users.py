"""Usuários do ambiente (deep.users — opt-in; bloqueado no perfil metadata_only).

Contém dado pessoal (nome e UPN): só ligue quando o levantamento de acesso fizer parte do escopo
acordado com o cliente, e trate o vault como confidencial. Sem deep.users, o coletor `security`
continua trazendo só contagens (ativos, aplicação, por BU).

Lê: systemusers (nome, UPN, BU, status, modo de acesso, licença, usuário de aplicação),
systemuserroles (papéis atribuídos diretamente) e teammembership (equipes). Papéis herdados via
equipe aparecem no usuário como `roles_via_team`.
"""

from ..util import day, fv

ACCESS_MODE = {0: "Leitura e gravação", 1: "Administrativo", 2: "Leitura", 3: "Usuário de suporte",
               4: "Não interativo", 5: "Administrador delegado"}


def user_kind(u):
    if u.get("applicationid"):
        return "aplicação"
    if u.get("accessmode") == 4:
        return "não interativo"
    if u.get("accessmode") in (1, 3, 5):
        return "administrativo/suporte"
    return "humano"


def collect_users(ctx):
    c, cfg = ctx.client, ctx.cfg
    if not cfg.deep.get("users"):
        return
    users = c.get_first_ok(
        "systemusers?$select=systemuserid,fullname,domainname,isdisabled,accessmode,applicationid,caltype,"
        "_businessunitid_value,createdon",
        "systemusers?$select=systemuserid,fullname,domainname,isdisabled,accessmode,applicationid,"
        "_businessunitid_value")
    role_name = {r["roleid"].lower(): r.get("name") for r in c.get_all("roles?$select=roleid,name")}
    sec = ctx.data.get("security") or {}
    teams = {t["id"].lower(): t for t in sec.get("teams") or []}

    direct = {}
    try:
        for a in c.get_all("systemuserrolescollection?$select=systemuserid,roleid"):
            direct.setdefault((a.get("systemuserid") or "").lower(), set()).add(
                role_name.get((a.get("roleid") or "").lower(), a.get("roleid")))
    except Exception as e:  # noqa: BLE001
        direct = None
        ctx.gap("users", "papéis atribuídos a usuários (systemuserroles)", e)
    member = {}
    try:
        for m in c.get_all("teammemberships?$select=teamid,systemuserid"):
            member.setdefault((m.get("systemuserid") or "").lower(), set()).add((m.get("teamid") or "").lower())
    except Exception as e:  # noqa: BLE001
        member = None
        ctx.gap("users", "equipes dos usuários (teammembership)", e)

    out = []
    for u in users:
        uid = u["systemuserid"].lower()
        tids = sorted((member or {}).get(uid, set()))
        via_team = sorted({r for t in tids for r in (teams.get(t) or {}).get("roles") or []})
        out.append({
            "id": u["systemuserid"], "name": u.get("fullname") or u.get("domainname") or u["systemuserid"],
            "upn": u.get("domainname"), "active": not u.get("isdisabled"), "kind": user_kind(u),
            "access_mode": fv(u, "accessmode") or ACCESS_MODE.get(u.get("accessmode"), u.get("accessmode")),
            "license": fv(u, "caltype") or u.get("caltype"),
            "bu": fv(u, "_businessunitid_value"), "bu_id": u.get("_businessunitid_value"),
            "application_id": u.get("applicationid"), "created": day(u.get("createdon")),
            "roles": sorted(r for r in (direct or {}).get(uid, set()) if r) if direct is not None else None,
            "teams": [teams[t]["name"] for t in tids if t in teams] if member is not None else None,
            "roles_via_team": via_team if member is not None else None,
        })
    out.sort(key=lambda x: (not x["active"], x["kind"], (x["name"] or "").lower()))
    ctx.stats["users"] = {
        "total": len(out), "active": sum(1 for x in out if x["active"]),
        "by_kind": {k: sum(1 for x in out if x["active"] and x["kind"] == k)
                    for k in ("humano", "aplicação", "não interativo", "administrativo/suporte")},
        "active_without_role": sum(1 for x in out if x["active"] and x["roles"] == [] and not x["roles_via_team"]),
    }
    ctx.data["users"] = out
