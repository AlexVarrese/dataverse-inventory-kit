"""Notas da estrutura de segurança e dos componentes PCF.

- Business Units/  uma nota por BU (pai, filhas, equipes, usuários ativos)
- Equipes/         equipes owner e de grupo Entra ID (equipes de acesso, criadas por registro, só contam)
- Usuários/        usuários ATIVOS — só com deep.users (dado pessoal; opt-in)
- Componentes PCF/ controles de código do escopo, com manifesto e onde são usados
"""

from collections import Counter, defaultdict

from ..dependencies import structural_teams

USER_LIST_MAX = 300  # nas notas de BU/equipe/papel: além disso, só a contagem (a Base lista todos)


def _h():
    from .obsidian import mdtable  # import tardio: obsidian importa este módulo
    return mdtable


def reserve(v, d):
    sec = d.get("security") or {}
    for b in sec.get("business_units") or []:
        v.reserve("bu", b["id"], "Business Units", b["name"])
    for t in structural_teams(sec):
        v.reserve("team", t["id"], "Equipes", t["name"])
    for u in d.get("users") or []:
        if u["active"]:
            v.reserve("user", u["id"], "Usuários", u["name"])
    for p in d.get("pcf") or []:
        v.reserve("pcf", p["name"], "Componentes PCF", p["name"])


def _indexes(d):
    sec = d.get("security") or {}
    users = [u for u in d.get("users") or [] if u["active"]]
    users_by_bu, users_by_team, users_by_role = defaultdict(list), defaultdict(list), defaultdict(list)
    for u in users:
        users_by_bu[(u.get("bu_id") or "").lower()].append(u)
        for t in u.get("teams") or []:
            users_by_team[t].append(u)
        for r in u.get("roles") or []:
            users_by_role[r].append(u)
    teams_by_bu, teams_by_role = defaultdict(list), defaultdict(list)
    for t in structural_teams(sec):  # equipes de acesso (por registro) ficam só na contagem
        teams_by_bu[(t.get("bu_id") or "").lower()].append(t)
        for r in t.get("roles") or []:
            teams_by_role[r].append(t)
    return users, users_by_bu, users_by_team, users_by_role, teams_by_bu, teams_by_role


def _user_list(v, us):
    shown = ", ".join(v.link("user", u["id"], u["name"]) for u in us[:USER_LIST_MAX])
    return shown + (f" … +{len(us) - USER_LIST_MAX}" if len(us) > USER_LIST_MAX else "")


def role_assignments(v, d, role_name):
    """Bloco 'Atribuído a' para a nota do papel."""
    mdtable = _h()
    users, _, _, users_by_role, _, teams_by_role = _indexes(d)
    teams = teams_by_role.get(role_name, [])
    b = ["## Atribuído a", ""]
    if not (d.get("security") or {}).get("teams") or all(t.get("roles") is None for t in d["security"]["teams"]):
        b += ["_Papéis das equipes não coletados._", ""]
    else:
        b += [f"**{len(teams)}** equipe(s)", "",
              mdtable(["Equipe", "Tipo", "BU"], [[v.link("team", t["id"], t["name"]), t.get("type"), t.get("bu")]
                                                for t in teams]) if teams else ""]
    if d.get("users") is None:
        b += ["> [!info] Usuários com o papel: ligue `deep.users` (lista nominal — dado pessoal).", ""]
    else:
        us = users_by_role.get(role_name, [])
        b += [f"**{len(us)}** usuário(s) ativo(s) com o papel atribuído diretamente", "", _user_list(v, us), ""]
    return "\n".join(b) + "\n"


def write_notes(v, d, findings_block, sol_links):
    mdtable = _h()
    sec = d.get("security") or {}
    bus = sec.get("business_units") or []
    users, users_by_bu, users_by_team, _, teams_by_bu, _ = _indexes(d)
    counts_by_bu = {k.lower(): n for k, n in ((sec.get("users") or {}).get("active_by_bu") or {}).items()}
    children = defaultdict(list)
    for b_ in bus:
        children[(b_.get("parent_id") or "").lower()].append(b_)

    # ---- business units ----
    for b_ in bus:
        bid = b_["id"].lower()
        ts, us = teams_by_bu.get(bid, []), users_by_bu.get(bid, [])
        kids = sorted(children.get(bid, []), key=lambda x: x["name"])
        props = {"tipo": "business-unit", "nome": b_["name"], "desativada": bool(b_.get("disabled")),
                 "pai": v.link("bu", b_["parent_id"], b_.get("parent")) if b_.get("parent_id") else None,
                 "filhas": len(kids), "equipes": len(ts),
                 "usuarios_ativos": counts_by_bu.get(bid, len(us) if users else None), "tags": v.tags("business-unit")}
        body = [f"# {b_['name']}", "",
                f"{'BU raiz' if not b_.get('parent_id') else 'Filha de ' + v.link('bu', b_['parent_id'], b_.get('parent'))}"
                f"{' · **desativada**' if b_.get('disabled') else ''}", ""]
        if kids:
            body += ["## BUs filhas", "", "\n".join(f"- {v.link('bu', k['id'], k['name'])}" for k in kids), ""]
        if ts:
            body += ["## Equipes", "", mdtable(["Equipe", "Tipo", "Papéis", "Membros"],
                     [[v.link("team", t["id"], t["name"]), t.get("type"), ", ".join(
                         v.link("role", r, r) for r in t.get("roles") or []), t.get("members")] for t in ts])]
        if users:
            body += [f"## Usuários ativos ({len(us)})", "", _user_list(v, us), ""]
        body.append(findings_block("bu", b_["id"]))
        v.write(v.paths[("bu", b_["id"])], props, "\n".join(body))

    # ---- equipes ----
    for t in structural_teams(sec):
        us = users_by_team.get(t["name"], [])
        props = {"tipo": "equipe", "nome": t["name"], "tipo_equipe": t.get("type"),
                 "business_unit": v.link("bu", t.get("bu_id"), t.get("bu")) if t.get("bu_id") else t.get("bu"),
                 "grupo_entra": t.get("aad_group"), "membros": t.get("members") if t.get("members") is not None
                 else (len(us) if users else None),
                 "papeis": [v.link("role", r, r) for r in t.get("roles") or []] if t.get("roles") is not None else None,
                 "administrador": t.get("admin"), "padrao_da_bu": t.get("default"), "tags": v.tags("equipe")}
        body = [f"# {t['name']}", "", f"{t.get('type')} · BU {props['business_unit']}"
                f"{' · grupo do Entra ID (' + str(t.get('membership')) + ')' if t.get('aad_group') else ''}", "",
                "## Papéis", ""]
        if t.get("roles") is None:
            body += ["_Não coletado (ver lacunas)._", ""]
        else:
            body += ["\n".join(f"- {v.link('role', r, r)}" for r in t["roles"]) or "_nenhum_", ""]
        if users:
            body += [f"## Membros ativos ({len(us)})", "", _user_list(v, us), ""]
        body.append(findings_block("team", t["id"]))
        v.write(v.paths[("team", t["id"])], props, "\n".join(body))

    # ---- usuários (deep.users) ----
    for u in users:
        props = {"tipo": "usuario", "nome": u["name"], "upn": u.get("upn"), "ativo": u["active"],
                 "tipo_usuario": u["kind"], "modo_acesso": u.get("access_mode"), "licenca": u.get("license"),
                 "business_unit": v.link("bu", u.get("bu_id"), u.get("bu")) if u.get("bu_id") else u.get("bu"),
                 "papeis": [v.link("role", r, r) for r in u.get("roles") or []],
                 "papeis_via_equipe": [v.link("role", r, r) for r in u.get("roles_via_team") or []],
                 "equipes": len(u.get("teams") or []), "criado": u.get("created"), "tags": v.tags("usuario")}
        team_ids = {t["name"]: t["id"] for t in sec.get("teams") or []}
        body = [f"# {u['name']}", "", f"`{u.get('upn')}` · {u['kind']} · {u.get('access_mode')} · BU {props['business_unit']}", "",
                "## Papéis (atribuição direta)", "",
                "\n".join(f"- {v.link('role', r, r)}" for r in u.get("roles") or []) or "_nenhum_", "",
                "## Equipes", "",
                "\n".join(f"- {v.link('team', team_ids.get(t), t)}" for t in u.get("teams") or []) or "_nenhuma_", ""]
        if u.get("roles_via_team"):
            body += ["## Papéis herdados das equipes", "", "\n".join(f"- {v.link('role', r, r)}" for r in u["roles_via_team"]), ""]
        body.append(findings_block("user", u["id"]))
        v.write(v.paths[("user", u["id"])], props, "\n".join(body))

    # ---- componentes PCF ----
    for p in d.get("pcf") or []:
        m = p.get("manifest") or {}
        forms_ = sorted({(x["entity"], x["form"]) for x in p.get("usage") or []})
        fields = sorted({f"{x['entity']}.{x['field']}" for x in p.get("usage") or [] if x.get("field")})
        props = {"tipo": "pcf", "nome": p["name"], "namespace": m.get("namespace"), "construtor": m.get("constructor"),
                 "versao": p.get("version") or m.get("manifest_version"), "tipo_controle": m.get("control_type"),
                 "gerenciado": p.get("managed"), "formularios": len(forms_), "campos": fields,
                 "dominios_externos": m.get("external_domains") or [], "web_api": "WebAPI" in (m.get("features") or []),
                 "escopo": p.get("scope_reason"), "solucoes": sol_links(p.get("solutions")),
                 "modificado": p.get("modified"), "tags": v.tags("pcf")}
        body = [f"# {p['name']}", "",
                f"`{m.get('namespace')}.{m.get('constructor')}` · {m.get('control_type') or '?'} · v{props['versao']} · "
                f"{'gerenciado' if p.get('managed') else 'não gerenciado'} · tipos compatíveis: {p.get('data_types') or '—'}", ""]
        if not p.get("manifest"):
            body += ["> [!warning] Manifesto não lido — propriedades e domínios externos desconhecidos.", ""]
        if m.get("external_domains"):
            body += ["> [!warning] Domínios externos declarados (`external-service-usage`)"] + \
                    [f"> - `{x}`" for x in m["external_domains"]] + [""]
        if m.get("properties") or m.get("datasets"):
            body += ["## Propriedades do manifesto", "", mdtable(["Propriedade", "Tipo", "Uso", "Obrigatória"],
                     [[x["name"], x.get("type"), x.get("usage"), x.get("required")] for x in m.get("properties") or []]
                     + [[x, "data-set", "dataset", None] for x in m.get("datasets") or []])]
        if m.get("features"):
            body += [f"Recursos da plataforma usados: {', '.join(f'`{x}`' for x in m['features'])}", ""]
        body += ["## Onde é usado (formulários ativos)", "",
                 mdtable(["Tabela", "Formulário", "Coluna", "Outras colunas (parâmetros)", "Form factor"],
                         [[v.link("table", x["entity"], x["entity"]), x["form"], x.get("field") or "(subgrid/dataset)",
                           ", ".join(x.get("bound") or []), ", ".join(x.get("form_factors") or [])]
                          for x in p.get("usage") or []]),
                 "> [!info] PCF configurado como controle padrão da coluna/tabela ou em view não aparece aqui "
                 "(não exposto pela Web API).", ""]
        body.append(findings_block("pcf", p["name"]))
        v.write(v.paths[("pcf", p["name"])], props, "\n".join(body))


def write_security(v, d):
    mdtable = _h()
    sec = d.get("security") or {}
    bus = sec.get("business_units") or []
    users, users_by_bu, _, users_by_role, teams_by_bu, teams_by_role = _indexes(d)
    counts_by_bu = {k.lower(): n for k, n in ((sec.get("users") or {}).get("active_by_bu") or {}).items()}
    children = defaultdict(list)
    for b_ in bus:
        children[(b_.get("parent_id") or "").lower()].append(b_)

    def tree(pid, depth):
        lines = []
        for b_ in sorted(children.get(pid, []), key=lambda x: x["name"]):
            bid = b_["id"].lower()
            n = counts_by_bu.get(bid)
            extra = [f"{len(teams_by_bu.get(bid, []))} equipe(s)"] + ([f"{n} usuário(s) ativo(s)"] if n is not None else [])
            lines.append("  " * depth + f"- {v.link('bu', b_['id'], b_['name'])}"
                         f"{' (desativada)' if b_.get('disabled') else ''} — {', '.join(extra)}")
            if depth < 12:
                lines += tree(bid, depth + 1)
        return lines

    u = sec.get("users") or {}
    roles = sec.get("roles") or []
    teams = sec.get("teams") or []
    access_teams = [t for t in teams if t.get("type_code") == 1]
    b = ["# Segurança", "",
         f"**{len(bus)}** business units · **{len(roles)}** papéis (cópia da BU raiz) · **{len(teams)}** equipes "
         f"({len(access_teams)} de acesso) · **{u.get('active', '?')}** usuários ativos ({u.get('active_human', '?')} "
         f"humanos, {u.get('application', '?')} de aplicação)", ""]
    b += ["## Árvore de business units", ""] + tree("", 0) + [""]
    teams_known = any(t.get("roles") is not None for t in teams)
    b += ["## Papéis", "", mdtable(
        ["Papel", "Gerenciado", "No escopo", "Equipes"] + (["Usuários (direto)"] if users else []),
        [[v.link("role", r["name"], r["name"], in_table=True), r.get("managed"), r.get("scope_reason"),
          len(teams_by_role.get(r["name"], [])) if teams_known else None]
         + ([len(users_by_role.get(r["name"], []))] if users else []) for r in roles])]
    b += ["## Equipes", "",
          *([f"> [!info] {len(access_teams)} equipe(s) de acesso (criadas por registro via access team template) "
             "ficam fora da lista e das notas.", ""] if access_teams else []),
          mdtable(["Equipe", "Tipo", "BU", "Grupo Entra ID", "Membros", "Papéis"],
                  [[v.link("team", t["id"], t["name"], in_table=True), t.get("type"),
                    v.link("bu", t.get("bu_id"), t.get("bu"), in_table=True) if t.get("bu_id") else t.get("bu"),
                    t.get("aad_group"), t.get("members"),
                    ", ".join(t.get("roles") or []) if t.get("roles") is not None else None]
                   for t in structural_teams(sec)])]
    if users:
        kinds = Counter(x["kind"] for x in users)
        lic = Counter(x.get("license") or "—" for x in users)
        b += ["## Usuários ativos", "",
              f"Lista nominal em `Usuários/` e na Base **Segurança**. Sem papel direto nem via equipe: "
              f"**{sum(1 for x in users if not x.get('roles') and not x.get('roles_via_team'))}**.", "",
              mdtable(["Tipo", "Ativos"], kinds.most_common()),
              mdtable(["Licença (caltype)", "Ativos"], lic.most_common()),
              mdtable(["Usuário de aplicação", "Application ID", "BU", "Papéis"],
                      [[v.link("user", x["id"], x["name"], in_table=True), x.get("application_id"), x.get("bu"),
                        ", ".join((x.get("roles") or []) + (x.get("roles_via_team") or []))]
                       for x in users if x["kind"] == "aplicação"])]
    else:
        b += ["> [!info] Lista nominal de usuários não coletada — ligue `deep.users` (dado pessoal; bloqueado no "
              "perfil `metadata_only`).", ""]
    b += ["## Perfis de segurança de campo", "", mdtable(["Perfil", "Gerenciado"],
          [[f["name"], f.get("managed")] for f in sec.get("field_security_profiles") or []])]
    v.write(f"{v.folder}/04 Segurança", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))
