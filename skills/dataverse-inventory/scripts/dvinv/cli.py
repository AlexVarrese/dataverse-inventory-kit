"""CLI: python3 dvinv.py <comando> -c inventory.yaml

  check    valida config + autenticação (WhoAmI) sem coletar nada
  extract  coleta read-only → <raw_dir>/*.json + manifest.json
  render   gera/atualiza o vault Obsidian a partir de <raw_dir>
  all      extract + render
  diff     compara dois raw_dir (snapshots ou ambientes) → nota de comparação
"""

import argparse
import shutil
import sys
from datetime import date

from . import config as config_mod
from .auth import build_credential
from .client import Client
from .collectors import REGISTRY, Context, run
from .scope import Scope


def _client(cfg):
    cred, method = build_credential(cfg.auth_method, cfg.tenant_id)
    print(f"[dvinv] autenticação: {method}", flush=True)
    return Client(cfg, cred)


def cmd_check(cfg):
    c = _client(cfg)
    who = c.get("WhoAmI()")
    ver = c.get("RetrieveVersion()").get("Version")
    print(f"OK — {cfg.url} versão {ver}, usuário {who.get('UserId')}")


def cmd_extract(cfg, snapshot):
    if cfg.raw_dir.exists() and snapshot:
        dst = cfg.raw_dir.parent / f"_raw-{date.today().isoformat()}"
        if not dst.exists():
            shutil.copytree(cfg.raw_dir, dst)
            print(f"[dvinv] snapshot anterior copiado para {dst}")
    ctx = Context(cfg, _client(cfg), Scope(cfg))
    manifest = run(ctx, cfg.collectors)
    print(f"[dvinv] extração concluída: {len(manifest['queries'])} chamadas, {len(manifest['gaps'])} lacuna(s) → {cfg.raw_dir}")
    for g in manifest["gaps"]:
        print(f"   lacuna [{g['collector']}] {g['what']}: {g['error'].splitlines()[0][:160]}")


def cmd_render(cfg):
    from .render.obsidian import render
    v = render(cfg)
    print(f"[dvinv] vault: {len(v.written)} arquivo(s) em {cfg.vault_dir}")
    print(f"[dvinv] abrir: {cfg.vault_dir / '00 Índice.md'}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dvinv", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "extract", "render", "all", "diff", "collectors"])
    ap.add_argument("-c", "--config", default="inventory.yaml")
    ap.add_argument("--only", help="lista de coletores separada por vírgula (ver 'collectors')")
    ap.add_argument("--deep", action="store_true", help="liga todas as coletas pesadas")
    ap.add_argument("--no-snapshot", action="store_true", help="não preserva o _raw anterior antes de extrair")
    ap.add_argument("--a", help="diff: raw_dir A")
    ap.add_argument("--b", help="diff: raw_dir B")
    args = ap.parse_args(argv)

    if args.command == "collectors":
        print("\n".join(n for n, _ in REGISTRY))
        return 0

    overrides = {"deep_all": args.deep}
    if args.only:
        overrides["collectors"] = [x.strip() for x in args.only.split(",") if x.strip()]
    cfg = config_mod.load(args.config, overrides)

    if args.command == "check":
        cmd_check(cfg)
    elif args.command == "extract":
        cmd_extract(cfg, not args.no_snapshot)
    elif args.command == "render":
        cmd_render(cfg)
    elif args.command == "all":
        cmd_extract(cfg, not args.no_snapshot)
        cmd_render(cfg)
    elif args.command == "diff":
        if not (args.a and args.b):
            sys.exit("diff exige --a <raw_dir> --b <raw_dir>")
        from .diff import compare
        print(f"[dvinv] {compare(args.a, args.b, cfg.vault_root, cfg.vault_folder.rsplit('/', 1)[0])}")
    return 0
