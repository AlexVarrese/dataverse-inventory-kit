"""Registro e execução dos coletores.

Cada coletor recebe o Context, grava ctx.data[<nome>] (estrutura normalizada) e pode registrar
totais da org em ctx.stats[<nome>] (para mostrar 'X no escopo de Y na org').
Falha de um coletor (403, 400 de coluna inexistente, timeout) NÃO derruba o levantamento: vira um
gap registrado no manifest e nas notas — melhor um inventário honesto com lacunas declaradas do
que nenhum inventário.

Tudo é gravado numa área de staging e publicado como snapshot imutável só no fim (ver snapshot.py);
o manifesto traz o status de cada coletor (ok, parcial, falhou, sem-dados, nao-executado).
"""

import time
import traceback
from datetime import datetime, timezone

from .. import secrets
from ..snapshot import COMPACT
from . import alm, apps, catalog, code, customization, navigation, environment, health, pcf, processes, repos, security, storage, tables, ui, usage, users
from . import dependencies as deps

# Ordem importa: solutions define o escopo por solução; tables define ctx.scope.tables,
# usado por forms/views/processos/plugins.
REGISTRY = [
    ("environment", environment.collect_environment),
    ("solutions", environment.collect_solutions),
    ("catalog", catalog.collect_catalog),        # 100% dos componentes (todas as soluções), com tipo e nome
    ("tables", tables.collect_tables),
    ("table_activity", usage.collect_table_activity),  # depois de tables: última atividade (resíduo × em uso)
    ("virtual_tables", tables.collect_virtual_tables),  # depois de tables: usa ctx.scope.tables
    ("relationships", tables.collect_relationships),
    ("mappings", customization.collect_mappings),          # mapeamentos campo a campo entre tabelas do escopo
    ("optionsets", tables.collect_optionsets),
    ("webresources", ui.collect_webresources),
    ("forms", ui.collect_forms),
    ("pcf", pcf.collect_pcf),                    # depois de forms: vínculos coluna → controle vêm do formxml
    ("views", ui.collect_views),
    ("ribbons", ui.collect_ribbons),
    ("apps", apps.collect_apps),
    ("sitemaps", navigation.collect_sitemaps),   # depois de apps: site map de app do escopo entra no escopo
    ("commands", customization.collect_commands),  # comandos modernos (command designer); usa ids dos web resources
    ("plugins", code.collect_plugins),
    ("customapis", code.collect_customapis),
    ("serviceendpoints", code.collect_serviceendpoints),
    ("alm", alm.collect_alm),                    # antes de processes: flows citam variáveis/conexões
    ("solution_history", customization.collect_solution_history),  # extra historico: importações/exportações e falhas
    ("processes", processes.collect_processes),
    ("slas", navigation.collect_slas),            # depois de tables (_tables_index) e processes
    ("security", security.collect_security),
    ("solution_layers", customization.collect_solution_layers),  # extra camadas: depois de tudo o que tem camada
    ("users", users.collect_users),              # depois de security: papéis das equipes
    ("repos", repos.collect_repos),              # depois de webresources/plugins/tables
    ("field_usage", usage.collect_field_usage),  # depois de tudo que cita campos (forms, views, processos, repos)
    ("platform_dependencies", deps.collect_platform_dependencies),  # depois de tudo que tem id
    ("storage", storage.collect_storage),
    ("health", health.collect_health),
]


# Coletores que usam dados de outros: --only inclui as dependências automaticamente.
DEPENDS = {
    "forms": ["webresources"], "ribbons": ["webresources"], "relationships": [],
    "pcf": ["forms"], "users": ["security"], "virtual_tables": [], "sitemaps": ["apps"], "slas": [], "commands": ["webresources", "apps"],
    "mappings": [], "solution_layers": [], "solution_history": [], "table_activity": ["tables"],
    "repos": ["webresources", "plugins"],
    "processes": ["alm", "customapis"],
    "field_usage": ["webresources", "forms", "views", "plugins", "processes", "repos"],
    "platform_dependencies": ["webresources", "forms", "views", "apps", "plugins", "customapis", "processes",
                              "alm", "security", "optionsets", "relationships", "pcf"],
}


def expand_only(only):
    todo, out = list(only), set()
    while todo:
        n = todo.pop()
        if n not in out:
            out.add(n)
            todo.extend(DEPENDS.get(n, []))
    return out


class Context:
    def __init__(self, cfg, client, scope):
        self.cfg = cfg
        self.client = client
        self.scope = scope
        self.data = {}
        self.stats = {}
        self.gaps = []
        self.status = {}        # coletor -> {"status": ok|parcial|falhou|nao-executado|sem-dados, ...}
        self.out_dir = None     # staging da extração em andamento (binários, decompilados)
        self.snapshot_dir = None
        self.run_id = None

    def gap(self, collector, what, err, kind="falha"):
        """Registra lacuna. A mensagem é redigida AQUI (antes de truncar): erros da API e tracebacks
        podem ecoar URLs com query string, headers ou trechos de configuração.

        kind: 'falha' (algo deveria ter sido lido e não foi), 'limitação' (limite conhecido da fonte,
        não depende desta execução) ou 'nao-executado' (coletor fora do --only)."""
        self.gaps.append({"collector": collector, "what": secrets.redact(str(what)),
                          "error": secrets.redact(str(err))[:400], "kind": kind})


def _status(ctx, name, failed, gaps_before):
    if failed is not None:
        return {"status": "falhou", "motivo": secrets.redact(str(failed)).splitlines()[0][:200] if str(failed) else
                type(failed).__name__}
    falhas = sum(1 for g in ctx.gaps[gaps_before:] if g["collector"] == name and g.get("kind", "falha") == "falha")
    if name not in ctx.data:
        return {"status": "sem-dados", "motivo": "desligado (deep.*/perfil) ou nada a coletar", "lacunas": falhas}
    return {"status": "parcial" if falhas else "ok", "lacunas": falhas}


# Opções (deep.*) que mudam o resultado de cada coletor: ao completar um snapshot, coletor com opção diferente da
# usado no base é refeito (ex. extra novo, ou deep.* ajustado no yaml).
FLAGS_BY_COLLECTOR = {
    "catalog": ("catalog", "catalog_names"), "tables": ("record_counts",),
    "webresources": ("webresource_content", "webresource_content_types", "webresource_skip_names",
                     "webresource_max_bytes"),
    "forms": ("form_events", "field_usage"), "views": ("field_usage",), "ribbons": ("ribbons",),
    "plugins": ("plugin_binaries",), "processes": ("flow_definitions", "process_definitions"),
    "security": ("role_privileges", "team_members"), "users": ("users",), "field_usage": ("field_usage",),
    "table_activity": ("table_activity", "table_activity_max_records"),
    "platform_dependencies": ("platform_dependencies", "platform_dependencies_columns"), "storage": ("storage",),
    "health": ("plugin_trace",), "solution_layers": ("solution_layers", "solution_layers_max", "solution_layers_columns"),
    "solution_history": ("solution_history", "solution_history_max"),
}
ALWAYS = ("environment", "solutions")  # baratos e com efeito no escopo (pertencimento a soluções): sempre rodam


def _scope_signature(cfg):
    return {"prefixes": cfg.prefixes, "keywords": cfg.keywords, "solutions": cfg.solutions,
            "include_unmanaged": cfg.include_unmanaged, "all": cfg.scope_all, "exclude_prefixes": cfg.exclude_prefixes,
            "exclude_microsoft": getattr(cfg, "exclude_microsoft", True)}


def plan_base(ctx, base_dir, requested):
    """Lê o snapshot base de `--completar` e decide o que roda e o que é herdado.

    Herda só coletor que terminou ok/parcial no base, com os arquivos conferidos pelo sha256. Recusa base com outro
    escopo ou perfil (misturaria inventários diferentes). Sem --only: roda só o que falhou ou faltou no base.
    → (base_manifest, conjunto que roda)."""
    from .. import snapshot
    m = snapshot.read_manifest(base_dir)
    if not m or m.get("schema") != snapshot.SCHEMA:
        raise SystemExit(f"--completar: {base_dir} não é um snapshot desta versão (manifesto ausente ou antigo)")
    ok, probs = snapshot.verify(base_dir)
    if not ok:
        raise SystemExit(f"--completar: snapshot {base_dir} não está íntegro: {'; '.join(probs[:3])}")
    if (m.get("scope") or {}) != _scope_signature(ctx.cfg) or m.get("profile", "padrao") != ctx.cfg.profile:
        raise SystemExit("--completar: o snapshot base tem outro escopo ou perfil de coleta — herdar dados dele "
                         "misturaria inventários diferentes. Rode a extração completa (sem --completar).")
    if m.get("url") and m["url"].rstrip("/") != ctx.cfg.url.rstrip("/"):
        raise SystemExit(f"--completar: o snapshot base é de outro ambiente ({m['url']})")
    good = {n for n, st in (m.get("collectors") or {}).items() if st.get("status") in ("ok", "parcial")}
    names = [n for n, _ in REGISTRY]
    base_deep, deep = m.get("deep") or {}, ctx.cfg.deep
    changed = {n for n, flags in FLAGS_BY_COLLECTOR.items() if any(base_deep.get(f) != deep.get(f) for f in flags)}
    good -= changed  # opção mudou (ex. extra novo): não herda — refaz
    run_set = set(requested) if requested else {n for n in names if n not in good}
    if changed and not requested:
        print(f"[dvinv] opções diferentes do snapshot base em: {', '.join(n for n in names if n in changed)} — "
              "essas etapas são refeitas", flush=True)
    if not requested:  # quem usa o dado de uma etapa refeita também é refeito (ex. forms → pcf, field_usage)
        grew = True
        while grew:
            grew = False
            for n, deps in DEPENDS.items():
                if n not in run_set and any(dep in run_set for dep in deps):
                    run_set.add(n)
                    grew = True
    todo = list(run_set)
    while todo:  # dependência que o base não tem íntegra precisa rodar junto
        for dep in DEPENDS.get(todo.pop(), []):
            if dep not in good and dep not in run_set:
                run_set.add(dep)
                todo.append(dep)
    return m, run_set | set(ALWAYS)


def _inherit(ctx, stg, name, base_dir, base_m):
    """Copia para o staging os arquivos do coletor no base e carrega os dados (para os coletores seguintes)."""
    import json
    import shutil
    st_b = (base_m.get("collectors") or {}).get(name) or {}
    files = list(st_b.get("arquivos") or [])
    if name == "plugins":  # binários e decompilados dos assemblies vão junto
        files += [f for f in (base_m.get("files") or {}) if f.startswith(("bin/", "decompiled/"))]
    for rel in files:
        src, dst = base_dir / rel, stg.dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if rel.endswith(".json") and "/" not in rel:
            ctx.data[rel[:-5]] = json.loads(src.read_text(encoding="utf-8"))
    if name in (base_m.get("stats") or {}):
        ctx.stats[name] = base_m["stats"][name]
    for g in base_m.get("gaps") or []:  # lacunas do coletor herdado continuam valendo
        if g.get("collector") == name and g.get("kind") != "nao-executado":
            ctx.gaps.append(dict(g, what=f"{g['what']} (herdado de {base_dir.name})"))
    if name == "tables":  # escopo das tabelas reconstruído a partir do base
        ctx.scope.tables.update(t["logical"] for t in ctx.data.get("tables") or [])
    return {"status": st_b.get("status"), "lacunas": st_b.get("lacunas", 0), "arquivos": files,
            "origem": f"herdado de {base_dir.name}"}


def run(ctx, only=None, base=None):
    """Executa os coletores numa área de staging e publica um snapshot imutável (ver snapshot.py).

    `base` (diretório de snapshot, de `--completar`): coletores fora do que roda são herdados do base, se lá
    terminaram ok/parcial — declarado por coletor no manifesto (`origem`). Retorna o manifesto publicado;
    ctx.snapshot_dir aponta para o diretório do snapshot.
    """
    from pathlib import Path

    from .. import snapshot

    requested = list(only or [])
    base_m = None
    if base is not None:
        base = Path(base)
        base_m, only = plan_base(ctx, base, requested)
        print(f"[dvinv] completando o snapshot {base.name}: roda {', '.join(n for n, _ in REGISTRY if n in only)}; "
              "o resto é herdado de lá (ok/parcial)", flush=True)
    else:
        only = expand_only(requested)
    timings = {}
    stg = snapshot.Staging(ctx.cfg.raw_dir)
    ctx.out_dir, ctx.run_id = stg.dir, stg.run_id
    started = datetime.now(timezone.utc)
    try:
        for name, fn in REGISTRY:
            if base_m is not None and name not in only:
                if ((base_m.get("collectors") or {}).get(name) or {}).get("status") in ("ok", "parcial"):
                    ctx.status[name] = _inherit(ctx, stg, name, base, base_m)
                    print(f"[dvinv]   ↺ {name}: herdado de {base.name}", flush=True)
                else:
                    ctx.status[name] = {"status": "nao-executado", "motivo": f"fora do --only e sem dado íntegro em {base.name}"}
                    ctx.gap(name, "coletor não executado nem herdado", f"falhou ou faltou em {base.name}",
                            kind="nao-executado")
                continue
            if only and name not in only and name not in ("environment", "solutions", "tables"):
                # environment/solutions/tables sempre rodam: definem o escopo dos demais
                ctx.status[name] = {"status": "nao-executado", "motivo": "fora do --only"}
                ctx.gap(name, "coletor não executado nesta extração", "fora do --only", kind="nao-executado")
                continue
            t0 = time.time()
            print(f"[dvinv] {name} ...", flush=True)
            gaps_before, failed = len(ctx.gaps), None
            try:
                fn(ctx)
            except Exception as e:  # noqa: BLE001 — qualquer falha vira gap, não aborta
                failed = e
                ctx.gap(name, "coletor inteiro", f"{e}\n{traceback.format_exc(limit=3)}")
                print(f"[dvinv]   FALHOU: {secrets.redact(str(e))[:300]}", flush=True)
            timings[name] = round(time.time() - t0, 1)
            keys = [name] + [k for k in ctx.data if k.startswith(f"_{name[:-1]}")]
            if failed is not None:
                # dado parcial de coletor que falhou não é publicado nem usado pelos seguintes
                for key in keys:
                    ctx.data.pop(key, None)
                ctx.stats.pop(name, None)
            st = _status(ctx, name, failed, gaps_before)
            from ..resumo import line
            print(line(name, ctx.stats.get(name), timings[name], st["status"]), flush=True)
            st["origem"] = "coletado nesta execução"
            st["arquivos"] = []
            try:
                for key in keys:
                    if key in ctx.data:
                        if key in COMPACT:  # dezenas de MB: a gravação + varredura de segredos leva ~1 min
                            print(f"[dvinv]   gravando {key}.json e conferindo segredos…", flush=True)
                        if stg.write_json(key, ctx.data[key]):
                            print(f"[dvinv]   {key}.json: valores vizinhos que só casam padrão de segredo depois de "
                                  "gravados foram redigidos", flush=True)
                        st["arquivos"].append(f"{key}.json")
            except secrets.SecretLeakError as e:  # perde só esta etapa, não a extração
                for f in st["arquivos"] + [f"{k}.json" for k in keys]:
                    (stg.dir / f).unlink(missing_ok=True)
                for key in keys:
                    ctx.data.pop(key, None)
                ctx.stats.pop(name, None)
                ctx.gap(name, "dados não gravados: possível segredo não redigido", str(e).splitlines()[0])
                print(f"[dvinv]   FALHOU ao gravar: {str(e).splitlines()[0]} — etapa descartada, extração segue",
                      flush=True)
                st = {"status": "falhou", "motivo": "possível segredo não redigido nos dados gravados",
                      "origem": "coletado nesta execução", "arquivos": []}
            ctx.status[name] = st

        manifest = {
            "tool": "dvinv",
            "environment": ctx.cfg.name,
            "url": ctx.cfg.url,
            "started_at": started.isoformat(timespec="seconds"),
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "profile": getattr(ctx.cfg, "profile", "padrao"),
            "profile_blocked": list(getattr(ctx.cfg, "profile_blocked", [])),
            "only": requested,
            "base": base.name if base_m is not None else None,
            "extras": list(getattr(ctx.cfg, "extras", []) or []),
            "scope": {
                "prefixes": ctx.cfg.prefixes, "keywords": ctx.cfg.keywords, "solutions": ctx.cfg.solutions,
                "include_unmanaged": ctx.cfg.include_unmanaged, "all": ctx.cfg.scope_all,
                "exclude_prefixes": ctx.cfg.exclude_prefixes,
                "exclude_microsoft": getattr(ctx.cfg, "exclude_microsoft", True),
            },
            "scope_breakdown": ctx.scope.breakdown(),
            "scope_excluded_microsoft": ctx.scope.excluded_summary(),
            "deep": ctx.cfg.deep,
            "collectors": ctx.status,
            "stats": ctx.stats,
            "gaps": ctx.gaps,
            "timings_s": timings,
            "throttled_429": getattr(ctx.client, "throttled", 0),
            "queries": ctx.client.log,
        }
        final, manifest = stg.publish(manifest)
    except BaseException:
        stg.discard()  # nada parcial fica com cara de snapshot
        raise
    ctx.snapshot_dir = final
    ctx.out_dir = final
    return manifest
