"""Carrega inventory.yaml + .env e aplica defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Coletas pesadas (muitas chamadas ou payload grande). Defaults pensados para um
# primeiro levantamento completo sem estourar tempo em orgs grandes.
DEEP_DEFAULTS = {
    "record_counts": True,         # RetrieveTotalRecordCount (snapshot de 24h, barato)
    "webresource_content": True,   # baixa JS/HTML do escopo (scan de segredos, funções)
    "flow_definitions": True,      # clientdata dos cloud flows do escopo (gatilho, conectores, HTTP)
    "form_events": True,           # formxml dos formulários do escopo (bibliotecas, handlers)
    "role_privileges": False,      # RetrieveRolePrivilegesRole por papel do escopo
    "ribbons": False,              # RetrieveEntityRibbon por tabela do escopo
    "plugin_trace": False,         # plugintracelogs dos últimos N dias
    "team_members": False,         # contagem de membros por equipe (FetchXML aggregate)
    "plugin_binaries": False,      # .dll dos assemblies do escopo (para decompilar com ilspycmd)
    "process_definitions": False,  # xaml/clientdata de workflows, business rules e actions (campos referenciados)
    "field_usage": False,          # preenchimento real por campo + matriz de uso + candidatos a remoção
    "storage": False,              # anexos (annotation/activitymimeattachment), auditoria, maiores tabelas
    "platform_dependencies": False,          # RetrieveDependentComponents + RetrieveMissingDependencies
    "platform_dependencies_columns": False,  # idem para cada coluna custom (1 chamada por coluna — pesado)
}

# Perfis de coleta. `metadata_only` = só metadados de customização: desliga (e trava, mesmo com
# --deep) toda coleta que lê CONTEÚDO (código, definições, binários) ou REGISTROS de negócio/log.
# Ficam permitidos: record_counts (contagem agregada por tabela, sem ler registro), form_events
# (formxml = customização), ribbons, role_privileges e platform_dependencies (metadados).
PROFILES = {
    "padrao": set(),
    "metadata_only": {
        "webresource_content",   # código JS/HTML dos web resources
        "flow_definitions",      # clientdata dos cloud flows
        "process_definitions",   # xaml/clientdata de workflows, business rules, actions
        "plugin_binaries",       # .dll dos assemblies
        "field_usage",           # lê registros (preenchimento por coluna)
        "storage",               # agrega anexos/auditoria (lê tabelas de dados)
        "plugin_trace",          # logs de execução (podem conter dados de registros)
        "team_members",          # associações usuário × equipe (registros)
    },
}
PROFILE_ALIASES = {"padrão": "padrao", "default": "padrao", "metadata-only": "metadata_only"}

# Soluções que contêm "tudo" e não servem para dizer a que solução um componente pertence.
SYSTEM_SOLUTIONS = {"default", "active", "basic", "system", "activitypartysolution"}


@dataclass
class Config:
    name: str
    url: str
    prefixes: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    exclude_prefixes: list = field(default_factory=list)
    solutions: list = field(default_factory=list)
    include_unmanaged: bool = True
    scope_all: bool = False
    deep: dict = field(default_factory=lambda: dict(DEEP_DEFAULTS))
    plugin_trace_days: int = 7
    collectors: list = field(default_factory=list)  # vazio = todos
    parallel: int = 4  # chamadas simultâneas nas etapas de uma chamada por item
    raw_dir: Path = Path("out/_raw")
    vault_root: Path = Path("vault")
    vault_folder: str = "Dataverse"
    field_usage_tables: list = field(default_factory=list)  # vazio = todas as tabelas do escopo
    field_usage_max_records: int = 500_000
    repos: list = field(default_factory=list)               # [{"path": ..., "kind": webresources|plugins|any}]
    auth_method: str = "auto"  # auto | spn | mcp | azcli | devicecode
    devicecode_cache: bool = False  # cache persistente CRIPTOGRAFADO do device code (opt-in; nunca texto puro)
    profile: str = "padrao"         # padrao | metadata_only (ver PROFILES)
    derived_dir: Path = Path("out/_derived")
    env_file: Path | None = None
    tenant_id: str | None = None

    @property
    def profile_blocked(self):
        return sorted(PROFILES.get(self.profile, set()))

    @property
    def api(self):
        return self.url.rstrip("/") + "/api/data/v9.2"

    @property
    def env_slug(self):
        return "".join(c if c.isalnum() else "-" for c in self.name.lower()).strip("-")

    @property
    def vault_dir(self):
        return self.vault_root / self.vault_folder


def load_env_file(path):
    if not path or not Path(path).exists():
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load(path, overrides=None):
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = path.parent
    overrides = overrides or {}

    env = data.get("environment", {})
    scope = data.get("scope", {})
    out = data.get("output", {})
    auth = data.get("auth", {})

    def rel(p, default):
        p = Path(p if p else default).expanduser()
        return p if p.is_absolute() else (base / p).resolve()

    env_file = auth.get("env_file")
    env_file = rel(env_file, ".env") if env_file else None
    load_env_file(env_file)

    name = overrides.get("name") or env.get("name") or "Ambiente"
    url = overrides.get("url") or env.get("url") or os.environ.get("DATAVERSE_URL")
    if not url:
        raise SystemExit("environment.url (ou DATAVERSE_URL no .env) é obrigatório")

    prefixes = [p.lower() for p in scope.get("prefixes", [])]
    keywords = scope.get("keywords")
    if keywords is None:  # 'contoso_' -> 'contoso' para casar nomes de processos/assemblies
        keywords = [p.rstrip("_") for p in prefixes if p.rstrip("_")]

    deep = dict(DEEP_DEFAULTS)
    deep.update(data.get("deep", {}) or {})
    if overrides.get("deep_all"):
        deep = {k: True for k in deep}
    if deep.get("field_usage"):  # a matriz precisa dos campos citados em workflows/BRs e nos formulários
        deep["process_definitions"] = True
        deep["form_events"] = True
    profile = str(overrides.get("profile") or data.get("profile") or "padrao").strip().lower()
    profile = PROFILE_ALIASES.get(profile, profile)
    if profile not in PROFILES:
        raise SystemExit(f"profile '{profile}' desconhecido — use: {', '.join(PROFILES)}")
    for k in PROFILES[profile]:  # por último: nem deep.* do yaml, nem --deep, nem field_usage religam
        deep[k] = False
    fu = data.get("field_usage", {}) or {}
    repos = []
    for r in data.get("repos", []) or []:
        r = {"path": r} if isinstance(r, str) else dict(r)
        r["path"] = str(rel(r["path"], "."))
        r.setdefault("kind", "any")
        repos.append(r)

    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    cfg = Config(
        name=name,
        url=url.rstrip("/"),
        prefixes=prefixes,
        keywords=[k.lower() for k in keywords],
        exclude_prefixes=[p.lower() for p in scope.get("exclude_prefixes", []) or []],
        solutions=scope.get("solutions", []) or [],
        include_unmanaged=scope.get("include_unmanaged", True),
        scope_all=scope.get("all", False),
        deep=deep,
        plugin_trace_days=int(data.get("plugin_trace_days", 7)),
        field_usage_tables=[t.lower() for t in fu.get("tables", []) or []],
        field_usage_max_records=int(fu.get("max_records", 500_000)),
        repos=repos,
        collectors=overrides.get("collectors") or data.get("collectors") or [],
        parallel=int(data.get("parallel", 4)),
        raw_dir=rel(out.get("raw_dir"), f"out/{slug}/_raw"),
        derived_dir=rel(out.get("derived_dir"), str(Path(out.get("raw_dir") or f"out/{slug}/_raw").parent / "_derived")),
        vault_root=rel(out.get("vault_root"), "vault"),
        vault_folder=out.get("vault_folder", f"Dataverse/{name}"),
        auth_method=auth.get("method", "auto"),
        devicecode_cache=bool(auth.get("devicecode_cache", False)),
        profile=profile,
        env_file=env_file,
        tenant_id=auth.get("tenant_id") or os.environ.get("TENANT_ID"),
    )
    if not (cfg.prefixes or cfg.solutions or cfg.include_unmanaged or cfg.scope_all):
        raise SystemExit("Defina scope.prefixes, scope.solutions, scope.include_unmanaged ou scope.all")
    return cfg
