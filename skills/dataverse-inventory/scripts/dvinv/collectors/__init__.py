"""Registro e execução dos coletores.

Cada coletor recebe o Context, grava ctx.data[<nome>] (estrutura normalizada) e pode registrar
totais da org em ctx.stats[<nome>] (para mostrar 'X no escopo de Y na org').
Falha de um coletor (403, 400 de coluna inexistente, timeout) NÃO derruba o levantamento: vira um
gap registrado no manifest e nas notas — melhor um inventário honesto com lacunas declaradas do
que nenhum inventário.
"""

import time
import traceback
from datetime import datetime, timezone

from .. import secrets
from ..util import save_json
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

    def gap(self, collector, what, err):
        """Registra lacuna. A mensagem é redigida AQUI (antes de truncar): erros da API e tracebacks
        podem ecoar URLs com query string, headers ou trechos de configuração."""
        self.gaps.append({"collector": collector, "what": secrets.redact(str(what)),
                          "error": secrets.redact(str(err))[:400]})


def run(ctx, only=None):
    only = expand_only(only or [])
    timings = {}
    for name, fn in REGISTRY:
        if only and name not in only and name not in ("environment", "solutions", "tables"):
            continue  # environment/solutions/tables sempre rodam: definem o escopo dos demais
        t0 = time.time()
        print(f"[dvinv] {name} ...", flush=True)
        try:
            fn(ctx)
        except Exception as e:  # noqa: BLE001 — qualquer falha vira gap, não aborta
            ctx.gap(name, "coletor inteiro", f"{e}\n{traceback.format_exc(limit=3)}")
            print(f"[dvinv]   FALHOU: {secrets.redact(str(e))[:300]}", flush=True)
        timings[name] = round(time.time() - t0, 1)
        for key in [name] + [k for k in ctx.data if k.startswith(f"_{name[:-1]}")]:
            if key in ctx.data:
                save_json(ctx.cfg.raw_dir / f"{key}.json", secrets.redact_tree(ctx.data[key]))

    manifest = {
        "tool": "dvinv",
        "environment": ctx.cfg.name,
        "url": ctx.cfg.url,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": {
            "prefixes": ctx.cfg.prefixes, "keywords": ctx.cfg.keywords, "solutions": ctx.cfg.solutions,
            "include_unmanaged": ctx.cfg.include_unmanaged, "all": ctx.cfg.scope_all,
            "exclude_prefixes": ctx.cfg.exclude_prefixes,
        },
        "scope_breakdown": ctx.scope.breakdown(),
        "deep": ctx.cfg.deep,
        "stats": ctx.stats,
        "gaps": ctx.gaps,
        "timings_s": timings,
        "throttled_429": getattr(ctx.client, "throttled", 0),
        "queries": ctx.client.log,
    }
    manifest = secrets.redact_tree(manifest)  # lacunas e stats também (antes só as chamadas)
    save_json(ctx.cfg.raw_dir / "manifest.json", manifest)
    return manifest
