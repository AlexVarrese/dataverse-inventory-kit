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
from . import alm, apps, code, environment, health, processes, repos, security, storage, tables, ui, usage
from . import dependencies as deps

# Ordem importa: solutions define o escopo por solução; tables define ctx.scope.tables,
# usado por forms/views/processos/plugins.
REGISTRY = [
    ("environment", environment.collect_environment),
    ("solutions", environment.collect_solutions),
    ("tables", tables.collect_tables),
    ("relationships", tables.collect_relationships),
    ("optionsets", tables.collect_optionsets),
    ("webresources", ui.collect_webresources),
    ("forms", ui.collect_forms),
    ("views", ui.collect_views),
    ("ribbons", ui.collect_ribbons),
    ("apps", apps.collect_apps),
    ("plugins", code.collect_plugins),
    ("customapis", code.collect_customapis),
    ("serviceendpoints", code.collect_serviceendpoints),
    ("alm", alm.collect_alm),                    # antes de processes: flows citam variáveis/conexões
    ("processes", processes.collect_processes),
    ("security", security.collect_security),
    ("repos", repos.collect_repos),              # depois de webresources/plugins/tables
    ("field_usage", usage.collect_field_usage),  # depois de tudo que cita campos (forms, views, processos, repos)
    ("platform_dependencies", deps.collect_platform_dependencies),  # depois de tudo que tem id
    ("storage", storage.collect_storage),
    ("health", health.collect_health),
]


# Coletores que usam dados de outros: --only inclui as dependências automaticamente.
DEPENDS = {
    "forms": ["webresources"], "ribbons": ["webresources"], "relationships": [],
    "repos": ["webresources", "plugins"],
    "processes": ["alm", "customapis"],
    "field_usage": ["webresources", "forms", "views", "plugins", "processes", "repos"],
    "platform_dependencies": ["webresources", "forms", "views", "apps", "plugins", "customapis", "processes",
                              "alm", "security", "optionsets", "relationships"],
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


def run(ctx, only=None):
    """Executa os coletores numa área de staging e publica um snapshot imutável (ver snapshot.py).

    Retorna o manifesto publicado; ctx.snapshot_dir aponta para o diretório do snapshot.
    """
    from .. import snapshot

    requested = list(only or [])
    only = expand_only(requested)
    timings = {}
    stg = snapshot.Staging(ctx.cfg.raw_dir)
    ctx.out_dir, ctx.run_id = stg.dir, stg.run_id
    started = datetime.now(timezone.utc)
    try:
        for name, fn in REGISTRY:
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
            st["arquivos"] = []
            for key in keys:
                if key in ctx.data:
                    stg.write_json(key, ctx.data[key])
                    st["arquivos"].append(f"{key}.json")
            ctx.status[name] = st

        manifest = {
            "tool": "dvinv",
            "environment": ctx.cfg.name,
            "url": ctx.cfg.url,
            "started_at": started.isoformat(timespec="seconds"),
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "only": requested,
            "scope": {
                "prefixes": ctx.cfg.prefixes, "keywords": ctx.cfg.keywords, "solutions": ctx.cfg.solutions,
                "include_unmanaged": ctx.cfg.include_unmanaged, "all": ctx.cfg.scope_all,
                "exclude_prefixes": ctx.cfg.exclude_prefixes,
            },
            "scope_breakdown": ctx.scope.breakdown(),
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
