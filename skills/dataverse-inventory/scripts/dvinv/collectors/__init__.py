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
    ("processes", processes.collect_processes),
    ("alm", alm.collect_alm),
    ("security", security.collect_security),
    ("repos", repos.collect_repos),              # depois de webresources/plugins/tables
    ("field_usage", usage.collect_field_usage),  # depois de tudo que cita campos (forms, views, processos, repos)
    ("storage", storage.collect_storage),
    ("health", health.collect_health),
]


# Coletores que usam dados de outros: --only inclui as dependências automaticamente.
DEPENDS = {
    "forms": ["webresources"], "ribbons": ["webresources"], "relationships": [],
    "repos": ["webresources", "plugins"],
    "field_usage": ["webresources", "forms", "views", "plugins", "processes", "repos"],
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
        self.gaps.append({"collector": collector, "what": what, "error": str(err)[:400]})


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
            print(f"[dvinv]   FALHOU: {e}", flush=True)
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
        },
        "deep": ctx.cfg.deep,
        "stats": ctx.stats,
        "gaps": ctx.gaps,
        "timings_s": timings,
        "queries": secrets.redact_tree(ctx.client.log),
    }
    save_json(ctx.cfg.raw_dir / "manifest.json", manifest)
    return manifest
