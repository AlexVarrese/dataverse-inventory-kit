"""CLI: python3 dvinv.py <comando> -c inventory.yaml

  check    valida config + autenticação (WhoAmI) sem coletar nada
  extract  coleta read-only → snapshot imutável <raw_dir>/<run_id>/ (*.json + manifest.json)
  render   gera/atualiza o vault Obsidian a partir do último snapshot íntegro (ou --snapshot)
  all      extract + render do snapshot recém-criado
  diff     compara dois snapshots (mesmo ambiente em datas diferentes, ou ambientes) → nota de comparação

Consultas ao índice SQLite do último render (_derived/<run_id>/inventario.sqlite) — para agentes de IA:
  buscar <texto>       encontra componentes (chave, tipo, escopo, nº de dependentes, nota)
  onde-usa <alvo>      quem depende diretamente do alvo (ex. account.esab_campo, account, nome de JS)
  impacto <alvo>       cadeia de quem depende do alvo (--profundidade, padrão 2)
  depende <alvo>       do que o alvo depende (--profundidade, padrão 1)

Perfis: --profile metadata_only desliga e trava toda coleta que lê conteúdo ou registros.
"""

import argparse
import os
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import config as config_mod
from . import secrets
from .auth import build_credential
from .client import Client
from .collectors import REGISTRY, Context, run
from .scope import Scope


LOCK_STALE_S = 6 * 3600


@contextmanager
def run_lock(cfg):
    """Uma extração/render por ambiente de cada vez (dois agentes — ex. Claude e Copilot — na mesma pasta
    publicariam snapshots/vaults concorrentes). Trava = arquivo criado com O_EXCL; > 6 h é considerada
    abandonada. Sem checagem de PID: no Windows os.kill(pid, 0) encerraria o processo."""
    path = Path(cfg.raw_dir).parent / ".dvinv.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and time.time() - path.stat().st_mtime > LOCK_STALE_S:
            path.unlink()
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        info = path.read_text(encoding="utf-8", errors="replace").strip()
        sys.exit(f"[dvinv] outra execução em andamento para {cfg.name} ({info}). Aguarde terminar; se ela foi "
                 f"interrompida, apague {path}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"pid {os.getpid()} em {socket.gethostname()} desde {time.strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _client(cfg):
    cred, method = build_credential(cfg.auth_method, cfg.tenant_id, getattr(cfg, "devicecode_cache", False))
    print(f"[dvinv] autenticação: {method}", flush=True)
    return Client(cfg, cred)


def cmd_check(cfg):
    c = _client(cfg)
    who = c.get("WhoAmI()")
    ver = c.get("RetrieveVersion()").get("Version")
    print(f"OK — {cfg.url} versão {ver}, usuário {who.get('UserId')}")


def cmd_extract(cfg, only=None):
    if (cfg.raw_dir / "manifest.json").exists():
        print(f"[dvinv] aviso: {cfg.raw_dir} tem arquivos do formato antigo (<= 0.4); eles são ignorados — "
              "cada extração agora vira um snapshot em subpasta própria", file=sys.stderr)
    if cfg.profile != "padrao":
        print(f"[dvinv] perfil {cfg.profile}: bloqueado {', '.join(cfg.profile_blocked)}", flush=True)
    ctx = Context(cfg, _client(cfg), Scope(cfg))
    manifest = run(ctx, only)
    falhou = [k for k, s in manifest["collectors"].items() if s["status"] == "falhou"]
    print(f"[dvinv] extração concluída: {len(manifest['queries'])} chamadas, {len(manifest['gaps'])} lacuna(s)"
          f"{', coletor(es) com falha: ' + ', '.join(falhou) if falhou else ''} → {ctx.snapshot_dir}")
    for g in manifest["gaps"]:
        print(f"   lacuna [{g['collector']}] {g['what']}: {secrets.redact((g['error'].splitlines() or [''])[0])[:160]}")
    return ctx.snapshot_dir


def cmd_render(cfg, snapshot=None):
    from .render.obsidian import render
    v = render(cfg, snapshot)
    print(f"[dvinv] snapshot: {v.snapshot}")
    print(f"[dvinv] vault: {len(v.written)} arquivo(s) em {cfg.vault_dir}")
    print(f"[dvinv] derivados: {v.derived_dir} (índice para agentes: inventario.sqlite)")
    print(f"[dvinv] abrir: {cfg.vault_dir / '00 Índice.md'}")
    print(f"[dvinv] instruções para agentes: {cfg.vault_root / 'AGENTS.md'} (bloco dvinv) · CLAUDE.md")
    for w in getattr(v, "agent_warnings", None) or []:
        print(f"[dvinv] aviso: {w}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dvinv", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "extract", "render", "all", "diff", "collectors",
                                        "buscar", "onde-usa", "impacto", "depende"])
    ap.add_argument("termo", nargs="?", help="buscar/onde-usa/impacto/depende: componente (nome, tabela.coluna, chave ou GUID)")
    ap.add_argument("-c", "--config", default="inventory.yaml")
    ap.add_argument("--only", help="lista de coletores separada por vírgula (ver 'collectors')")
    ap.add_argument("--deep", action="store_true", help="liga todas as coletas pesadas (respeita o perfil)")
    ap.add_argument("--profile", choices=sorted(config_mod.PROFILES), help="perfil de coleta (sobrepõe o yaml)")
    ap.add_argument("--snapshot", help="render: diretório do snapshot ou run_id (padrão: último íntegro)")
    ap.add_argument("--no-snapshot", action="store_true", help=argparse.SUPPRESS)  # obsoleto desde 0.5.0
    ap.add_argument("--a", help="diff: snapshot A (diretório, raiz _raw → último íntegro, ou run_id)")
    ap.add_argument("--b", help="diff: snapshot B")
    ap.add_argument("--profundidade", type=int, help="impacto/depende: níveis (padrão 2 / 1)")
    ap.add_argument("--internas", action="store_true",
                    help="impacto/depende: inclui arestas internas de metadados (coluna → tabela etc.)")
    ap.add_argument("--limite", type=int, default=150, help="consultas: máximo de linhas listadas")
    ap.add_argument("--indice", help="consultas: caminho do inventario.sqlite ou run_id (padrão: o mais recente)")
    args = ap.parse_args(argv)

    if args.command == "collectors":
        print("\n".join(n for n, _ in REGISTRY))
        return 0
    if args.no_snapshot:
        print("[dvinv] --no-snapshot é obsoleto: cada extração já é um snapshot próprio (opção ignorada)",
              file=sys.stderr)

    overrides = {"deep_all": args.deep}
    if args.profile:
        overrides["profile"] = args.profile
    if args.only:
        overrides["collectors"] = [x.strip() for x in args.only.split(",") if x.strip()]
    cfg = config_mod.load(args.config, overrides)

    if args.command in ("buscar", "onde-usa", "impacto", "depende"):
        if not args.termo:
            sys.exit(f"{args.command} exige o termo (ex.: dvinv {args.command} account.esab_campo -c inventory.yaml)")
        from . import index as index_mod
        con = index_mod.connect(index_mod.locate(cfg, args.indice))
        if args.command == "buscar":
            print(index_mod.cmd_buscar(con, args.termo, args.limite))
        else:
            direction = "depende" if args.command == "depende" else "impacto"
            depth = args.profundidade or (2 if args.command == "impacto" else 1)
            print(index_mod.cmd_walk(con, args.termo, direction, depth, args.internas, args.limite))
        return 0

    try:
        if args.command == "check":
            cmd_check(cfg)
        elif args.command == "extract":
            with run_lock(cfg):
                cmd_extract(cfg, cfg.collectors)
        elif args.command == "render":
            with run_lock(cfg):
                cmd_render(cfg, args.snapshot)
        elif args.command == "all":
            with run_lock(cfg):
                snap = cmd_extract(cfg, cfg.collectors)
                cmd_render(cfg, snap)
        elif args.command == "diff":
            if not (args.a and args.b):
                sys.exit("diff exige --a <snapshot> --b <snapshot>")
            from .diff import compare
            print(f"[dvinv] {compare(args.a, args.b, cfg.vault_root, cfg.vault_folder.rsplit('/', 1)[0], cfg.raw_dir)}")
    except secrets.SecretLeakError as e:
        sys.exit(f"[dvinv] ERRO: {e}")
    return 0
