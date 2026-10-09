"""Credenciais Azure AD para a Dataverse Web API — dois caminhos principais, ambos ativos:

  spn  Service Principal (Application User no Dataverse). .env: TENANT_ID, CLIENT_ID, CLIENT_SECRET.
       Não-interativo; é o caminho que funciona mesmo quando Conditional Access bloqueia login
       interativo/device code. Recomendado para extrações completas e agendadas.

  mcp  Reusa o login do Dataverse CLI / servidor MCP (`dataverse auth create --environment <url>`).
       O proxy MCP `npx @microsoft/dataverse mcp <url>` e este pacote leem o MESMO cache MSAL
       (app 0c412cc3-…): um login serve o MCP do agente e o extrator Python, com as permissões
       do usuário logado.

Fallbacks: azcli (az login) e devicecode. Em `auto` a ordem é spn → mcp → azcli → devicecode.

devicecode: por padrão o token fica só em memória (novo login a cada execução). Com
`auth.devicecode_cache: true` o token é persistido no cache CRIPTOGRAFADO do sistema (DPAPI no
Windows, Keychain no macOS, libsecret no Linux). Não existe fallback para arquivo em texto puro: se o
cofre do sistema não estiver disponível, a autenticação falha com mensagem clara.
"""

import os
import shutil
import sys
import time
from pathlib import Path

# App registration pública do Dataverse CLI — usada pelo proxy MCP stdio e pelas skills dv-*.
DATAVERSE_CLI_CLIENT_ID = "0c412cc3-0dd6-449b-987f-05b053db9457"


class _AccessToken:
    def __init__(self, token, expires_on):
        self.token, self.expires_on = token, expires_on


class McpSharedCacheCredential:
    """Lê o cache MSAL do Dataverse CLI (o mesmo do servidor MCP) e renova o token em silêncio."""

    def __init__(self, tenant_id):
        try:
            self._open(tenant_id)
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001 — keyring ausente, cache corrompido, lib faltando
            hint = (" (Linux: o cache do Dataverse CLI usa libsecret — instale `pygobject` + gnome-keyring, "
                    "ou use Service Principal em hosts headless)") if sys.platform.startswith("linux") else ""
            raise RuntimeError(f"cache do Dataverse CLI indisponível: {type(e).__name__}: {e}{hint}") from None

    def _open(self, tenant_id):
        try:
            import msal
            from msal_extensions import PersistedTokenCache
        except ImportError:
            raise RuntimeError("msal/msal-extensions não instalados (pip install -r scripts/requirements.txt)")
        if sys.platform == "win32":
            from msal_extensions import FilePersistenceWithDataProtection
            path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Microsoft" / "DataverseCli" / "tokencache_msalv3.dat"
            if not path.exists():
                raise RuntimeError(f"cache do Dataverse CLI não encontrado em {path}")
            persistence = FilePersistenceWithDataProtection(str(path))
        elif sys.platform == "darwin":
            from msal_extensions import KeychainPersistence
            persistence = KeychainPersistence(str(Path.home() / ".dataverse_cli_msal_cache"),
                                              "dataverse_cli_service", "dataverse_cli_account")
        else:
            from msal_extensions import LibsecretPersistence
            persistence = LibsecretPersistence(str(Path.home() / ".dataverse_cli_msal_cache"),
                                               schema_name="com.microsoft.dataversecli",
                                               attributes={"Version": "1", "ProductGroup": "DataverseCli"})
        authority = f"https://login.microsoftonline.com/{tenant_id or 'organizations'}"
        self.app = msal.PublicClientApplication(DATAVERSE_CLI_CLIENT_ID, authority=authority,
                                                token_cache=PersistedTokenCache(persistence))
        self.accounts = self.app.get_accounts()
        if not self.accounts:
            raise RuntimeError("cache do Dataverse CLI vazio — rode `dataverse auth create --environment <url>`")

    def get_token(self, *scopes, **_):
        res = self.app.acquire_token_silent(list(scopes), account=self.accounts[0])
        if not res or "access_token" not in res:
            raise RuntimeError("token do cache MCP expirou — rode de novo `dataverse auth create --environment <url>`")
        return _AccessToken(res["access_token"], int(time.time()) + int(res.get("expires_in", 3600)))


def build_credential(method, tenant_id, devicecode_cache=False):
    try:
        from azure.identity import (AzureCliCredential, ClientSecretCredential,
                                    DeviceCodeCredential, TokenCachePersistenceOptions)
    except ImportError:
        raise SystemExit("azure-identity não instalado: pip install -r scripts/requirements.txt")

    client_id = os.environ.get("CLIENT_ID")
    secret = os.environ.get("CLIENT_SECRET")

    if method in ("auto", "spn") and client_id and secret:
        if not tenant_id:
            raise SystemExit("TENANT_ID é obrigatório para Service Principal")
        return ClientSecretCredential(tenant_id, client_id, secret), "spn (Service Principal)"
    if method == "spn":
        raise SystemExit("auth.method=spn mas CLIENT_ID/CLIENT_SECRET ausentes no .env — ver INSTALL.md §3A")

    if method in ("auto", "mcp"):
        try:
            return McpSharedCacheCredential(tenant_id), "mcp (cache do Dataverse CLI / servidor MCP)"
        except RuntimeError as e:
            if method == "mcp":
                raise SystemExit(f"auth.method=mcp: {e} — ver INSTALL.md §3B")
            print(f"[dvinv] mcp indisponível, tentando próximo método: {e}", file=sys.stderr)

    if method in ("auto", "azcli") and shutil.which("az"):
        return AzureCliCredential(tenant_id=tenant_id), "azcli"
    if method == "azcli":
        raise SystemExit("auth.method=azcli mas o Azure CLI (az) não está no PATH")

    def prompt(uri, code, _exp):
        print(f"\nPara autenticar, abra {uri} e informe o código: {code}\n", file=sys.stderr, flush=True)

    return DeviceCodeCredential(**devicecode_kwargs(tenant_id, prompt, devicecode_cache, TokenCachePersistenceOptions)), \
        "devicecode" + (" (cache criptografado)" if devicecode_cache else " (sem cache persistente)")


def devicecode_kwargs(tenant_id, prompt, persist, options_cls):
    """Parâmetros do DeviceCodeCredential. Cache persistente só criptografado e só por opt-in."""
    kw = {"tenant_id": tenant_id or "organizations", "client_id": DATAVERSE_CLI_CLIENT_ID, "prompt_callback": prompt}
    if persist:
        kw["cache_persistence_options"] = options_cls(name="dvinv", allow_unencrypted_storage=False)
    return kw
