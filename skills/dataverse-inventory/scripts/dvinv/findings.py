"""Achados automáticos derivados do inventário.

Regra da casa: todo achado carrega numerador/denominador ou lista de evidências
rastreável a um arquivo de _raw/. Nada de score decorativo. Os achados são *candidatos* — o analista
confirma, muda status e anota na nota do achado (o render preserva essas edições).
"""

from collections import Counter, defaultdict

SEV_ORDER = {"crítico": 0, "alto": 1, "médio": 2, "baixo": 3, "info": 4}


def _f(fid, sev, title, detail, refs=None, evidence=None, metric=None):
    return {"id": fid, "severity": sev, "title": title, "detail": detail,
            "refs": refs or [], "evidence": evidence or [], "metric": metric}


def compute(d):
    out = []
    tables = d.get("tables") or []
    wrs = d.get("webresources") or []
    forms = d.get("forms") or []
    plugins = d.get("plugins") or {}
    steps = plugins.get("steps") or []
    procs = d.get("processes") or []
    alm = d.get("alm") or {}
    sec = d.get("security") or {}
    env = d.get("environment") or {}
    wr_names = set(d.get("_webresource_names") or [])

    # 1. Segredos em texto claro -------------------------------------------------------------
    hits = []
    for w in wrs:
        for h in w.get("secret_hits") or []:
            hits.append((("webresource", w["name"]), f"{w['name']} linha {h['line']}: {h['kind']}"))
    for p in procs:
        for h in p.get("secret_hits") or []:
            hits.append((("process", p["id"]), f"flow '{p['name']}': {h['kind']}"))
    for s in steps:
        for h in s.get("config_secret_hits") or []:
            hits.append((("step", s["id"]), f"step '{s['name']}' (unsecure config): {h['kind']}"))
    for e in alm.get("envvars") or []:
        for h in e.get("secret_hits") or []:
            hits.append((("envvar", e["name"]), f"variável '{e['name']}': {h['kind']}"))
    for e in d.get("serviceendpoints") or []:
        for h in e.get("secret_hits") or []:
            hits.append((("endpoint", e["name"]), f"service endpoint '{e['name']}': {h['kind']}"))
    if hits:
        out.append(_f("SEC-01", "crítico", f"{len(hits)} possível(is) segredo(s) em texto claro",
                      "Padrões de credencial (SAS `sig=`, function key `code=`, senha, bearer/JWT) encontrados em "
                      "conteúdo legível por qualquer usuário com leitura de customização. Valores já foram redigidos "
                      "no vault; o segredo continua válido na origem até ser rotacionado. Mover para variável de "
                      "ambiente do tipo Secret (Key Vault) ou secure config.",
                      refs=[r for r, _ in hits], evidence=[e for _, e in hits], metric=len(hits)))

    # 2. Referências quebradas para web resources --------------------------------------------
    broken = []
    if wr_names:
        for f in forms:
            for lib in f.get("libraries") or []:
                if lib.lower() not in wr_names:
                    broken.append((("table", f["entity"]), f"form '{f['name']}' ({f['entity']}) → biblioteca `{lib}`"))
        for r in d.get("ribbons") or []:
            for call in r.get("calls") or []:
                if call["library"].lower() not in wr_names:
                    broken.append((("table", r["entity"]),
                                   f"botão '{r.get('label') or r['button']}' ({r['entity']}) → `{call['library']}`"))
    if broken:
        out.append(_f("UI-01", "alto", f"{len(broken)} referência(s) a web resource inexistente",
                      "Formulário ou botão aponta para uma biblioteca que não existe no ambiente — erro de script "
                      "para o usuário ou botão sem ação. Provável recurso removido/renomeado ou variante nunca publicada.",
                      refs=[r for r, _ in broken], evidence=[e for _, e in broken], metric=len(broken)))

    # 3. Handlers apontando para função inexistente na biblioteca ------------------------------
    funcs = {w["name"].lower(): set(w.get("functions") or []) for w in wrs if w.get("functions")}
    missing_fn = []
    for f in forms:
        for h in f.get("handlers") or []:
            lib, fn = (h.get("library") or "").lower(), h.get("function") or ""
            if lib in funcs and fn:
                leaf = fn.split(".")[-1]
                if leaf not in funcs[lib]:
                    missing_fn.append((("table", f["entity"]), f"{f['entity']} / '{f['name']}' {h['event']}"
                                       f"{' ' + h['field'] if h.get('field') else ''} → `{fn}` em `{h['library']}`"))
    if missing_fn:
        out.append(_f("UI-02", "médio", f"{len(missing_fn)} handler(s) de formulário sem função correspondente",
                      "A função registrada no evento não foi encontrada por análise estática da biblioteca. Pode ser "
                      "falso positivo (função criada dinamicamente/minificada) — confirmar abrindo o JS.",
                      refs=[r for r, _ in missing_fn], evidence=[e for _, e in missing_fn], metric=len(missing_fn)))

    # 4. Web resources JS sem uso conhecido --------------------------------------------------
    if any(f.get("libraries") for f in forms):
        used = {lib.lower() for f in forms for lib in f.get("libraries") or []}
        used |= {c["library"].lower() for r in d.get("ribbons") or [] for c in r.get("calls") or []}
        orphans = [w for w in wrs if w["type"] == "JScript" and w["name"].lower() not in used]
        if orphans:
            out.append(_f("UI-03", "baixo", f"{len(orphans)} de {sum(1 for w in wrs if w['type'] == 'JScript')} "
                          "web resources JS sem referência em formulário" + (" ou ribbon" if d.get("ribbons") is not None else ""),
                          "Candidatos a código morto. Ainda podem ser usados por outra biblioteca, HTML, command bar "
                          "moderna (Power Fx) ou PCF — confirmar antes de remover." +
                          ("" if d.get("ribbons") is not None else " Ribbons não foram coletados (deep.ribbons=false)."),
                          refs=[("webresource", w["name"]) for w in orphans],
                          evidence=[w["name"] for w in orphans], metric=len(orphans)))

    # 5. Plugin steps: desempenho e estado ----------------------------------------------------
    plugin_steps = [s for s in steps if s.get("handler_kind") == "plugintype"]
    no_filter = [s for s in plugin_steps if s["message"] == "Update" and s["enabled"] and not s.get("filtering")
                 and s["mode"] == "Síncrono"]
    if no_filter:
        out.append(_f("PLG-01", "médio", f"{len(no_filter)} step(s) síncrono(s) de Update sem filtering attributes",
                      "Executam em qualquer alteração de qualquer coluna — custo em toda gravação e risco de loop. "
                      "Restringir aos campos realmente usados.",
                      refs=[("step", s["id"]) for s in no_filter],
                      evidence=[f"{s['entity']}: {s['name']}" for s in no_filter], metric=len(no_filter)))
    retrieve = [s for s in plugin_steps if s["message"] in ("Retrieve", "RetrieveMultiple") and s["enabled"]
                and s["mode"] == "Síncrono"]
    if retrieve:
        out.append(_f("PLG-02", "médio", f"{len(retrieve)} step(s) em Retrieve/RetrieveMultiple",
                      "Rodam em toda leitura (grids, lookups, API) — impacto direto na percepção de lentidão.",
                      refs=[("step", s["id"]) for s in retrieve],
                      evidence=[f"{s['entity']}: {s['name']}" for s in retrieve], metric=len(retrieve)))
    disabled = [s for s in steps if not s["enabled"]]
    if disabled:
        out.append(_f("PLG-03", "baixo", f"{len(disabled)} de {len(steps)} step(s) desativado(s)",
                      "Registro inativo: confirmar se é desligamento temporário ou resíduo a remover.",
                      refs=[("step", s["id"]) for s in disabled],
                      evidence=[f"{s['entity']}: {s['name']}" for s in disabled], metric=len(disabled)))

    # 6. Saúde de execução (trace log) --------------------------------------------------------
    health = d.get("health") or {}
    failing = [t for t in health.get("by_type") or [] if t["runs"] >= 5 and (t["error_rate"] or 0) >= 0.05]
    if failing:
        sev = "crítico" if any(t["error_rate"] >= 0.5 for t in failing) else "alto"
        out.append(_f("PLG-04", sev, f"{len(failing)} plugin(s) com taxa de erro ≥ 5% no trace log",
                      f"Janela desde {health.get('since')} (trace mais antigo: {health.get('oldest_trace')}).",
                      evidence=[f"{t['type']}: {t['errors']}/{t['runs']} ({t['error_rate']:.1%})" for t in failing],
                      metric=len(failing)))
    if env.get("plugin_trace_setting") in ("Off", "Desativado", 0):
        out.append(_f("OPS-01", "info", "Plugin trace log desativado no ambiente",
                      "Sem trace não há como medir falha de plugin em produção. Considerar 'Exception'."))
    if env.get("audit_enabled") is False:
        out.append(_f("OPS-02", "médio", "Auditoria desativada na organização",
                      "Sem trilha de alteração de dados/segurança."))

    # 7. Processos ---------------------------------------------------------------------------
    bpfs = defaultdict(list)
    for p in procs:
        if p["category"] == "Business Process Flow" and p["active"] and p.get("entity"):
            bpfs[p["entity"]].append(p)
    multi = {e: ps for e, ps in bpfs.items() if len(ps) > 1}
    if multi:
        out.append(_f("PRC-01", "médio", f"{len(multi)} tabela(s) com mais de um BPF ativo",
                      "BPFs concorrentes na mesma tabela — confirmar se é intencional (por perfil/BU) ou resíduo.",
                      refs=[("table", e) for e in multi],
                      evidence=[f"{e}: " + ", ".join(p["name"] for p in ps) for e, ps in multi.items()],
                      metric=len(multi)))
    orphan_owner = [p for p in procs if p["active"] and p.get("owner_disabled")]
    if orphan_owner:
        out.append(_f("PRC-02", "alto", f"{len(orphan_owner)} automação(ões) ativa(s) de proprietário desativado",
                      "Flows de usuário desligado param quando a conexão expira; workflows passam a rodar no contexto "
                      "de uma conta inativa. Transferir para conta de serviço/equipe.",
                      refs=[("process", p["id"]) for p in orphan_owner],
                      evidence=[f"{p['category']}: {p['name']} — {p['owner']}" for p in orphan_owner],
                      metric=len(orphan_owner)))
    realtime = [p for p in procs if p["category"] == "Workflow" and p["active"] and (p.get("mode") or "").startswith("Tempo")]
    if realtime:
        per = Counter(p["entity"] for p in realtime)
        out.append(_f("PRC-03", "baixo", f"{len(realtime)} workflow(s) clássico(s) síncrono(s)",
                      "Workflows em tempo real entram na transação de gravação. Por tabela: " +
                      ", ".join(f"{e} ({n})" for e, n in per.most_common(10)),
                      refs=[("process", p["id"]) for p in realtime], metric=len(realtime)))
    classic = [p for p in procs if p["category"] in ("Workflow", "Dialog") and p["active"]]
    if classic:
        out.append(_f("PRC-04", "info", f"{len(classic)} workflow(s)/dialog(s) clássico(s) ativos",
                      "Tecnologia legada — candidatos a migração para Power Automate / plugins / Power Fx.",
                      refs=[("process", p["id"]) for p in classic], metric=len(classic)))
    inactive_flows = [p for p in procs if p["category"] == "Cloud Flow" and not p["active"]]
    if inactive_flows:
        out.append(_f("PRC-05", "baixo", f"{len(inactive_flows)} cloud flow(s) desligado(s)",
                      "Confirmar se são obsoletos (remover) ou desligados por falha (investigar).",
                      refs=[("process", p["id"]) for p in inactive_flows], metric=len(inactive_flows)))

    # 8. Segurança ---------------------------------------------------------------------------
    matrix = sec.get("privilege_matrix")
    if matrix is not None:
        custom = [t for t in tables if t["custom"] and t.get("ownership") != "None"]
        uncovered = [t for t in custom if not any(
            "Read" in acts for acts in (matrix.get(t["logical"]) or {}).values())]
        if uncovered:
            out.append(_f("SEG-01", "alto", f"{len(uncovered)} de {len(custom)} tabela(s) custom sem privilégio de "
                          f"leitura nos {len(sec.get('privilege_roles_analyzed') or [])} papéis analisados",
                          "Somente administradores enxergam esses dados (ou o acesso vem de papel fora do escopo "
                          "analisado). Verificar se é intencional.",
                          refs=[("table", t["logical"]) for t in uncovered],
                          evidence=[f"{t['logical']} ({t.get('record_count') or '?'} registros)" for t in uncovered],
                          metric=len(uncovered)))
    big_teams = [t for t in sec.get("teams") or [] if (t.get("members") or 0) >= 1000]
    if big_teams:
        out.append(_f("SEG-02", "médio", f"{len(big_teams)} equipe(s) com 1.000+ membros",
                      "Equipes gigantes com papéis atribuídos degradam o motor de autorização (RetrieveMultiple). "
                      "Avaliar Modernized BU / Matrix Data Access.",
                      evidence=[f"{t['name']}: {t['members']}" for t in big_teams], metric=len(big_teams)))

    # 9. ALM ---------------------------------------------------------------------------------
    novalue = [e for e in alm.get("envvars") or [] if not e["has_value"] and not e["has_default"]]
    if novalue:
        out.append(_f("ALM-01", "médio", f"{len(novalue)} variável(is) de ambiente sem valor nem default",
                      "Flows/plugins que leem essas variáveis falham ou usam fallback silencioso.",
                      refs=[("envvar", e["name"]) for e in novalue], evidence=[e["name"] for e in novalue],
                      metric=len(novalue)))
    unbound = [r for r in alm.get("connrefs") or [] if not r["connected"]]
    if unbound:
        out.append(_f("ALM-02", "médio", f"{len(unbound)} referência(s) de conexão sem conexão vinculada",
                      "Flows que usam essas referências não ligam após import de solução.",
                      refs=[("connref", r["name"]) for r in unbound],
                      evidence=[f"{r['name']} ({r['connector']})" for r in unbound], metric=len(unbound)))
    apis_no_impl = [a for a in d.get("customapis") or [] if not a.get("plugin_type")]
    if apis_no_impl:
        out.append(_f("ALM-03", "baixo", f"{len(apis_no_impl)} Custom API(s) sem plugin de implementação",
                      "Sem plugin a API só serve como mensagem para steps/flows — confirmar se é o desenho.",
                      refs=[("customapi", a["unique"]) for a in apis_no_impl], metric=len(apis_no_impl)))
    no_solution = [p for p in procs if p["managed"] is False and not p.get("solutions")]
    if no_solution and d.get("solutions"):
        out.append(_f("ALM-04", "baixo", f"{len(no_solution)} processo(s) não gerenciado(s) fora de qualquer solução",
                      "Customizações feitas direto no ambiente e não empacotadas — não migram por ALM.",
                      refs=[("process", p["id"]) for p in no_solution], metric=len(no_solution)))

    # 10. Uso de campos ---------------------------------------------------------------------
    fu = d.get("field_usage") or []
    if fu:
        measured = [(t, f) for t in fu for f in t["fields"] if f["bucket"] != "nao-medido"]
        safe = [(t, f) for t, f in measured if f["bucket"] == "candidato-seguro"]
        partial = [t["table"] for t in fu if str(t.get("method", "")).startswith("parcial")]
        caveat = (" Medição parcial em: " + ", ".join(partial) + "." if partial else "") + (
            "" if all(t.get("repos_measured") for t in fu) else " Repositório de código não informado (`repos:`) — "
            "uso em código-fonte fora do Dataverse não foi verificado.")
        if safe:
            out.append(_f("FLD-01", "médio", f"{len(safe)} de {len(measured)} coluna(s) custom sem dados e sem uso encontrado",
                          "Nenhum registro preenchido e nenhuma referência em formulário, view, processo, flow, plugin, JS"
                          " ou repositório. Candidatas a remoção após confirmar integrações externas (ETL, relatórios"
                          " Power BI, portais)." + caveat,
                          refs=[("table", t["table"]) for t, _ in safe],
                          evidence=[f"{t['table']}.{f['logical']} ({f['type']})" for t, f in safe], metric=len(safe)))
        ui = [(t, f) for t, f in measured if f["bucket"] == "sem-dados-na-ui"]
        if ui:
            out.append(_f("FLD-02", "baixo", f"{len(ui)} coluna(s) sem dados mas ainda em formulário/view",
                          "Campo exibido que ninguém preenche: poluição de tela ou funcionalidade abandonada.",
                          refs=[("table", t["table"]) for t, _ in ui],
                          evidence=[f"{t['table']}.{f['logical']} — forms: {', '.join(f['forms'][:3]) or '—'}" for t, f in ui],
                          metric=len(ui)))
        logic = [(t, f) for t, f in measured if f["bucket"] == "sem-dados-com-logica"]
        if logic:
            out.append(_f("FLD-03", "baixo", f"{len(logic)} coluna(s) sem dados mas citadas em automação/código",
                          "Lógica que lê/escreve um campo sempre vazio — regra morta ou bug (a gravação nunca acontece).",
                          refs=[("table", t["table"]) for t, _ in logic],
                          evidence=[f"{t['table']}.{f['logical']}" for t, f in logic], metric=len(logic)))

    # 11. JavaScript -------------------------------------------------------------------------
    js = [w for w in wrs if w.get("js") and not w["js"].get("third_party")]
    for fid, sev, key, title, detail in (
            ("JS-01", "alto", "odata_2011", "usam endpoint SOAP/OData 2011 (removido)",
             "`XRMServices/2011` e `OrganizationData.svc` foram descontinuados — quebram sem aviso. Migrar para Xrm.WebApi."),
            ("JS-02", "alto", "eval", "usam eval()", "Risco de injeção e bloqueio por CSP."),
            ("JS-03", "médio", "xrm_page", "usam Xrm.Page (obsoleto)", "Substituir por formContext (executionContext.getFormContext())."),
            ("JS-04", "médio", "sync_xhr", "fazem XMLHttpRequest síncrono", "Trava a interface; navegadores estão removendo o suporte."),
            ("JS-05", "baixo", "xrm_service_toolkit", "dependem de XrmServiceToolkit", "Biblioteca legada sem manutenção.")):
        hit = [w for w in js if w["js"].get(key)]
        if hit:
            out.append(_f(fid, sev, f"{len(hit)} de {len(js)} web resource(s) JS {title}", detail,
                          refs=[("webresource", w["name"]) for w in hit], evidence=[w["name"] for w in hit], metric=len(hit)))
    ext = [w for w in js if w["js"].get("external_hosts")]
    if ext:
        out.append(_f("JS-06", "info", f"{len(ext)} web resource(s) JS chamam hosts externos fixos no código",
                      "Endpoint fixo no JS não muda entre ambientes (DEV/TEST/PRD) — preferir variável de ambiente.",
                      refs=[("webresource", w["name"]) for w in ext],
                      evidence=[f"{w['name']}: {', '.join(w['js']['external_hosts'])}" for w in ext], metric=len(ext)))

    # 12. Flows ------------------------------------------------------------------------------
    no_catch = [p for p in procs if p["category"] == "Cloud Flow" and p["active"] and (p.get("actions") or 0) >= 5
                and p.get("error_handlers") == 0]
    if no_catch:
        out.append(_f("FLW-01", "baixo", f"{len(no_catch)} cloud flow(s) ativo(s) sem tratamento de erro",
                      "Nenhuma ação configurada para rodar após Failed/TimedOut: falha fica só no histórico do flow, "
                      "sem log/notificação. Candidato — pode haver monitoramento externo.",
                      refs=[("process", p["id"]) for p in no_catch], metric=len(no_catch)))

    # 13. Armazenamento e auditoria -----------------------------------------------------------
    st = d.get("storage") or {}
    ema = st.get("email_attachments") or {}
    img = sum(m.get("bytes") or 0 for m in ema.get("by_mimetype") or [] if str(m.get("mimetype", "")).startswith("image/"))
    tot = sum(m.get("bytes") or 0 for m in ema.get("by_mimetype") or [])
    if tot and img / tot >= 0.3:
        out.append(_f("STO-01", "médio", f"Imagens são {img / tot:.0%} dos bytes de anexos de e-mail",
                      "Padrão típico de assinatura de e-mail anexada em cada mensagem rastreada. Maior alavanca de "
                      "redução de file storage: política de retenção/limpeza de anexos de imagem pequenos.",
                      evidence=[f"{m['mimetype']}: {m.get('count')} arquivos, {(m.get('bytes') or 0) / 1e9:.1f} GB"
                                for m in ema.get("by_mimetype")[:8]], metric=round(img / 1e9, 1)))
    aud = st.get("audit") or {}
    if aud.get("total"):
        top = aud.get("by_table") or []
        out.append(_f("AUD-01", "info", f"Auditoria: {aud['total']:,} registros desde {aud.get('oldest')}".replace(",", "."),
                      "Volume e retenção efetiva da auditoria. Comparar com a política de retenção acordada.",
                      evidence=[f"{r['table']}: {r['count']:,}".replace(",", ".") for r in top[:10]], metric=aud["total"]))

    # 14. Repositórios -----------------------------------------------------------------------
    for r in d.get("repos") or []:
        if r.get("secret_hits"):
            out.append(_f("REPO-01", "crítico", f"{len(r['secret_hits'])} possível(is) segredo(s) no repositório {r['name']}",
                          "Credencial versionada fica no histórico do Git mesmo depois de apagada — rotacionar e limpar o "
                          "histórico (git filter-repo).",
                          evidence=[f"{h['file']} linha {h['line']}: {h['kind']}" for h in r["secret_hits"]],
                          metric=len(r["secret_hits"])))
        div = [w for w in r.get("webresources") or [] if w["status"] == "divergente"]
        if div:
            out.append(_f("REPO-02", "alto", f"{len(div)} web resource(s) publicados diferentes do repositório {r['name']}",
                          "Alguém editou direto no ambiente ou o repo tem versão não publicada. Funções que existem só "
                          "de um lado indicam drift funcional, não só formatação.",
                          refs=[("webresource", w["name"]) for w in div],
                          evidence=[f"{w['name']} ↔ {w['file']} (similaridade {w['similarity']:.0%})"
                                    + (f"; só no ambiente: {', '.join(w['functions_only_env'][:5])}" if w['functions_only_env'] else "")
                                    + (f"; só no repo: {', '.join(w['functions_only_repo'][:5])}" if w['functions_only_repo'] else "")
                                    for w in div], metric=len(div)))
        miss = [w for w in r.get("webresources") or [] if w["status"] == "sem fonte no repo"]
        miss_t = [t for t in r.get("plugin_types") or [] if t["status"] == "sem fonte no repo"]
        if miss or miss_t:
            out.append(_f("REPO-03", "alto", f"{len(miss) + len(miss_t)} componente(s) publicado(s) sem fonte no repositório {r['name']}",
                          "Código em produção que não está versionado: impossível revisar, reconstruir ou corrigir com segurança.",
                          refs=[("webresource", w["name"]) for w in miss],
                          evidence=[w["name"] for w in miss] + [t["type"] for t in miss_t], metric=len(miss) + len(miss_t)))
        drift = [t for t in r.get("plugin_types") or [] if t.get("methods_only_prd") or t.get("methods_only_repo")]
        if drift:
            out.append(_f("REPO-04", "alto", f"{len(drift)} classe(s) de plugin com métodos diferentes entre produção e {r['name']}",
                          "Comparação da DLL decompilada com o código do repositório por assinatura de método (ignora "
                          "ruído de decompilador). Ler os dois arquivos antes de concluir.",
                          evidence=[f"{t['type']}: só PRD {t.get('methods_only_prd')}, só repo {t.get('methods_only_repo')}"
                                    for t in drift], metric=len(drift)))

    # 15. Dependências ---------------------------------------------------------------------
    from . import dependencies as deps_mod
    g = deps_mod.build(d)
    dsum = deps_mod.summarize(g, d)
    by_sol = defaultdict(list)
    for m in dsum["missing"]:
        by_sol[m["solution"]].append(m)
    if by_sol:
        # um achado só (ambientes de DEV/TEST têm centenas de soluções de patch); detalhe por solução no _raw
        ranked = sorted(by_sol.items(), key=lambda kv: -len(kv[1]))
        out.append(_f("DEP-01", "alto", f"{len(by_sol)} solução(ões) exigem componentes que não contêm",
                      "RetrieveMissingDependencies: a importação dessas soluções falha em ambiente que não tenha os "
                      "componentes exigidos. Adicione-os à solução ou garanta a ordem de instalação. Soluções de "
                      "patch/teste costumam aparecer aqui — priorize as que são unidades de deploy.",
                      refs=[("solution", sol) for sol, _ in ranked],
                      evidence=[f"{sol}: {len(ms)} componente(s) — "
                                + ", ".join(f"{t} ({n})" for t, n in Counter(
                                    m.get("required_type_label") or str(m.get("required_type")) for m in ms).most_common(4))
                                for sol, ms in ranked],
                      metric=len(by_sol)))
    hot = [r for r in dsum["per_table"] if r["total"]][:15]
    if hot:
        out.append(_f("DEP-02", "info", f"As {len(hot)} tabela(s) de maior impacto de mudança",
                      "Tabelas com mais componentes dependentes (formulários, JS, plugins, processos, flows, apps e, "
                      "com deep.platform_dependencies, o que a plataforma registra). Mudança de schema nelas exige "
                      "regressão ampla.",
                      refs=[("table", r["table"]) for r in hot],
                      evidence=[f"{r['table']}: {r['total']} dependentes" for r in hot], metric=len(hot)))
    unused = [n for n in dsum["unused"] if n[0] in ("envvar", "connref", "customapi")]
    if unused:
        out.append(_f("DEP-03", "baixo", f"{len(unused)} variável(is)/conexão(ões)/Custom API(s) sem uso encontrado",
                      "Nenhum flow, processo ou JS do escopo referencia esses componentes. Podem ser usados de fora "
                      "(integração, app externo) — confirmar antes de remover.",
                      refs=list(unused), evidence=[f"{deps_mod.KIND_LABEL[n[0]]}: {n[1]}" for n in unused],
                      metric=len(unused)))

    out.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["id"]))
    return out
