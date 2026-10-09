"""CLI: python3 dvinv.py <comando> -c inventory.yaml

  check    valida config + autenticação (WhoAmI) sem coletar nada
  extract  coleta read-only → snapshot imutável <raw_dir>/<run_id>/ (*.json + manifest.json)
  render   gera/atualiza o vault Obsidian a partir do último snapshot íntegro (ou --snapshot)
  all      extract + render do snapshot recém-criado
  diff     compara dois snapshots (mesmo ambiente em datas diferentes, ou ambientes) → nota de comparação

Perfis: --profile metadata_only desliga e trava toda coleta que lê conteúdo ou registros.
"""

import argparse
import sys

from . import config as config_mod
from . import secrets
from .auth import build_credential
from .client import Client
from .collectors import REGISTRY, Context, run
from .scope import Scope


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
    print(f"[dvinv] derivados: {v.derived_dir}")
    print(f"[dvinv] abrir: {cfg.vault_dir / '00 Índice.md'}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dvinv", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "extract", "render", "all", "diff", "collectors"])
    ap.add_argument("-c", "--config", default="inventory.yaml")
    ap.add_argument("--only", help="lista de coletores separada por vírgula (ver 'collectors')")
    ap.add_argument("--deep", action="store_true", help="liga todas as coletas pesadas (respeita o perfil)")
    ap.add_argument("--profile", choices=sorted(config_mod.PROFILES), help="perfil de coleta (sobrepõe o yaml)")
    ap.add_argument("--snapshot", help="render: diretório do snapshot ou run_id (padrão: último íntegro)")
    ap.add_argument("--no-snapshot", action="store_true", help=argparse.SUPPRESS)  # obsoleto desde 0.5.0
    ap.add_argument("--a", help="diff: snapshot A (diretório, raiz _raw → último íntegro, ou run_id)")
    ap.add_argument("--b", help="diff: snapshot B")
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

    try:
        if args.command == "check":
            cmd_check(cfg)
        elif args.command == "extract":
            cmd_extract(cfg, cfg.collectors)
        elif args.command == "render":
            cmd_render(cfg, args.snapshot)
        elif args.command == "all":
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
