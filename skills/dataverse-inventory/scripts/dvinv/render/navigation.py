"""Notas de site maps (`Site Maps/`) e SLAs (`SLAs/`)."""


def _h():
    from .obsidian import mdtable  # import tardio: obsidian importa este módulo
    return mdtable


def reserve(v, d):
    for s in d.get("sitemaps") or []:
        v.reserve("sitemap", s["id"], "Site Maps", s["name"])
    for s in d.get("slas") or []:
        v.reserve("sla", s["id"], "SLAs", s["name"] or s["id"])


def _minutes(m):
    if m is None:
        return "—"
    h, mm = divmod(int(m), 60)
    return f"{m} min ({h}h{mm:02d})" if h else f"{m} min"


def write_notes(v, d, findings_block, sol_links):
    mdtable = _h()
    procs = {p["id"]: p for p in d.get("processes") or []}

    def dest(sub):
        if sub.get("entity"):
            return v.link("table", sub["entity"], sub["entity"], in_table=True)
        if sub.get("webresources") and sub["kind"] == "web resource":
            return ", ".join(v.link("webresource", w, w, in_table=True) for w in sub["webresources"])
        return f"`{sub['url']}`" if sub.get("url") else "—"

    for s in d.get("sitemaps") or []:
        subs = [(a, g, x) for a in s["areas"] for g in a["groups"] for x in g["subareas"]]
        props = {"tipo": "sitemap", "nome": s["name"], "de_app": s.get("app_aware"), "gerenciado": s.get("managed"),
                 "apps": [v.link("app", a["id"], a["name"]) for a in s.get("apps") or []],
                 "areas": len(s["areas"]), "subareas": len(subs), "tabelas": len(s.get("tables") or []),
                 "web_resources": len(s.get("webresources") or []), "urls_externas": len(s.get("external_urls") or []),
                 "modificado": s.get("modified"), "escopo": s.get("scope_reason"),
                 "solucoes": sol_links(s.get("solutions")), "tags": v.tags("sitemap")}
        b = [f"# {s['display'] or s['name']} (`{s['name']}`)", "",
             f"Site map {'de app' if s.get('app_aware') else 'clássico'} · **{len(s['areas'])}** áreas · "
             f"**{len(subs)}** subáreas · {len(s.get('tables') or [])} tabelas", ""]
        if s.get("apps"):
            b += ["**Apps:** " + ", ".join(v.link("app", a["id"], a["name"]) for a in s["apps"]), ""]
        if s.get("external_urls"):
            b += ["> [!warning] URLs externas na navegação", "> " + " · ".join(f"`{u}`" for u in s["external_urls"]), ""]
        b += ["## Navegação", "", mdtable(["Área", "Grupo", "Subárea", "Tipo", "Destino", "Privilégios"],
              [[a["title"], g["title"], x["title"], x["kind"], dest(x), ", ".join(x.get("privileges") or []) or ""]
               for a, g, x in subs])]
        if s.get("webresources"):
            b += ["## Web resources (páginas e ícones)", "",
                  ", ".join(v.link("webresource", w, w) for w in s["webresources"]), ""]
        b.append(findings_block("sitemap", s["id"]))
        v.write(v.paths[("sitemap", s["id"])], props, "\n".join(b))

    for s in d.get("slas") or []:
        its = s.get("items") or []
        props = {"tipo": "sla", "nome": s["name"], "tabela": v.link("table", s["table"], s["table"]) if s.get("table") else None,
                 "estado": s.get("state"), "versao_sla": s.get("version"), "padrao": s.get("default"),
                 "gerenciado": s.get("managed"), "itens": len(its), "horario_comercial": s.get("business_hours"),
                 "permite_pausa": s.get("allow_pause"), "modificado": s.get("modified"), "escopo": s.get("scope_reason"),
                 "solucoes": sol_links(s.get("solutions")), "tags": v.tags("sla")}
        b = [f"# {s['name']}", "", (s.get("description") or "").strip(), "",
             f"SLA ({s.get('version') or s.get('type') or '—'}) da tabela "
             f"{v.link('table', s['table'], s['table']) if s.get('table') else '—'} · estado **{s.get('state')}** · "
             f"padrão: {s.get('default')} · horário comercial: {s.get('business_hours') or '—'}", ""]
        if its:
            b += ["## Itens (KPIs)", "", mdtable(
                ["#", "Item", "KPI / campo", "Aviso", "Falha", "Horário", "Ação"],
                [[it.get("sequence"), it.get("name"), f"{it.get('kpi') or '—'} (`{it.get('kpi_field') or '—'}`)",
                  _minutes(it.get("warning_min")), _minutes(it.get("failure_min")), it.get("business_hours") or "",
                  (v.link("process", it["workflow_id"], procs[it["workflow_id"]]["name"], in_table=True)
                   if it.get("workflow_id") in procs else it.get("action_flow") or it.get("workflow_id") or "")]
                 for it in its])]
            b += ["## Campos usados nas condições", "", mdtable(
                ["Item", "Aplicável quando", "Sucesso", "Pausa", "Outras tabelas"],
                [[it.get("name"), ", ".join(f"`{c}`" for c in it.get("applicable_when_fields") or []),
                  ", ".join(f"`{c}`" for c in it.get("success_fields") or []),
                  ", ".join(f"`{c}`" for c in it.get("pause_fields") or []),
                  ", ".join(it.get("linked_entities") or [])] for it in its])]
        else:
            b += ["> [!info] SLA sem itens.", ""]
        b.append(findings_block("sla", s["id"]))
        v.write(v.paths[("sla", s["id"])], props, "\n".join(b))


def bases(v, _base):
    return {
        "Site Maps": _base(v, "sitemap",
            properties={"de_app": "De app", "apps": "Apps", "subareas": "Subáreas", "tabelas": "Tabelas",
                        "urls_externas": "URLs externas"},
            views=[{"type": "table", "name": "Todos", "order": ["file.name", "de_app", "apps", "areas", "subareas",
                                                                 "tabelas", "web_resources", "urls_externas", "gerenciado"]}]),
        "SLAs": _base(v, "sla",
            properties={"tabela": "Tabela", "estado": "Estado", "itens": "Itens", "horario_comercial": "Horário"},
            views=[{"type": "table", "name": "Por tabela", "groupBy": {"property": "tabela", "direction": "ASC"},
                    "order": ["file.name", "estado", "versao_sla", "padrao", "itens", "horario_comercial", "gerenciado"]},
                   {"type": "table", "name": "Não ativos", "filters": 'estado != "Active" && estado != "Ativo"',
                    "order": ["file.name", "tabela", "estado", "modificado"]}]),
    }
