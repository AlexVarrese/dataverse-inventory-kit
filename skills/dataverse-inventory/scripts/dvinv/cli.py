"""CLI: python3 dvinv.py <comando> -c inventory.yaml

  check    valida config + autenticação (WhoAmI) sem coletar nada
  extract  coleta read-only → snapshot imutável <raw_dir>/<run_id>/ (*.json + manifest.json)
  render   gera/atualiza o vault Obsidian a partir do último snapshot íntegro (ou --snapshot)
  all      extract + render do snapshot recém-criado
  diff     compara dois snapshots (mesmo ambiente em datas diferentes, ou ambientes) → nota de comparação

Consultas ao vault (Componentes.csv + Dependências.csv da pasta do ambiente) — funcionam em qualquer clone
do repositório do vault com --vault <pasta>, sem inventory.yaml nem credenciais:
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


class _Tee:
    """Repete stdout/stderr num arquivo de log, com hora em cada linha — para acompanhar extrações longas
    (`type`/`tail` no arquivo) e guardar o histórico da execução. Só o que já é impresso (mensagens redigidas)."""

    def __init__(self, stream, fh):
        self.stream, self.fh, self._bol = stream, fh, True

    def write(self, text):
        self.stream.write(text)
        for part in text.splitlines(keepends=True):
            if self._bol:
                self.fh.write(time.strftime("%H:%M:%S "))
            self.fh.write(part)
            self._bol = part.endswith("\n")
        self.fh.flush()
        return len(text)

    def flush(self):
        self.stream.flush()
        self.fh.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def start_log(cfg, command):
    logs = Path(cfg.raw_dir).parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"dvinv-{time.strftime('%Y%m%d-%H%M%S')}-{command}.log"
    fh = open(path, "a", encoding="utf-8", newline="\n")  # noqa: SIM115 — fica aberto até o fim do processo
    sys.stdout, sys.stderr = _Tee(sys.stdout, fh), _Tee(sys.stderr, fh)
    from . import client as client_mod

    def trace(text):  # uma linha por chamada HTTP, só no arquivo (o console fica legível)
        fh.write(time.strftime("%H:%M:%S ") + "        http " + secrets.redact(text) + "\n")
        fh.flush()
    client_mod.TRACE = trace
    print(f"[dvinv] log desta execução: {path} (inclui cada chamada à API, com tempo e tamanho)", flush=True)
    return path


def git_summary(root):
    """Resumo só-leitura do que o render mudou no repositório do vault (git status). Não faz commit."""
    import subprocess
    root = Path(root)
    try:
        out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all", "--", "."],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"git indisponível ({e.__class__.__name__}) — revise as mudanças antes do commit"
    if out.returncode != 0:
        return "pasta ainda não é um repositório Git — `git init` (ou clone do remoto) antes do primeiro commit"
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    if not lines:
        return "nenhuma mudança desde o último commit"
    novos = sum(1 for ln in lines if ln.startswith("??") or ln[:2].strip() == "A")
    removidos = sum(1 for ln in lines if "D" in ln[:2])
    alterados = len(lines) - novos - removidos
    return (f"{novos} novo(s), {alterados} alterado(s), {removidos} removido(s) — revise com `git -C {root} diff "
            f"--stat` e faça o commit (o dvinv não commita)")


def _client(cfg):
    cred, method = build_credential(cfg.auth_method, cfg.tenant_id, getattr(cfg, "devicecode_cache", False))
    print(f"[dvinv] autenticação: {method}", flush=True)
    return Client(cfg, cred)


def cmd_check(cfg):
    c = _client(cfg)
    who = c.get("WhoAmI()")
    ver = c.get("RetrieveVersion()").get("Version")
    print(f"OK — {cfg.url} versão {ver}, usuário {who.get('UserId')}")


def cmd_escopo(cfg):
    """Pré-visualização do escopo: o que entraria na extração, sem extrair (minutos, não horas)."""
    import csv
    from collections import Counter
    from .collectors import environment, tables
    ctx = Context(cfg, _client(cfg), Scope(cfg))
    ctx.preview = True
    print("[dvinv] lendo soluções e tabelas (sem detalhar colunas)…", flush=True)
    environment.collect_solutions(ctx)
    tables.collect_tables(ctx)
    sel, org = ctx.data.get("_preview_tables") or [], ctx.data.get("_preview_org_tables")
    sols = [s_ for s_ in ctx.data.get("solutions") or [] if s_.get("in_scope")]
    print(f"\nEscopo com a configuração atual: {len(sel)} de {org} tabelas · {len(sols)} solução(ões) no escopo")
    print("\nPor motivo:")
    for r, n in Counter(t["reason"] for t in sel).most_common():
        print(f"  {n:6}  {r}")
    pref = Counter((t["logical"].split("_", 1)[0] + "_") if "_" in t["logical"] else "(sem prefixo)" for t in sel)
    print("\nPor prefixo (as 15 maiores):")
    for p_, n in pref.most_common(15):
        print(f"  {n:6}  {p_}")
    out = Path(cfg.raw_dir).parent / f"escopo-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Tabela", "Motivo", "Colunas custom", "Custom", "Gerenciada"])
        w.writerows([[t["logical"], t["reason"], t["custom_columns"], t["custom"], t["managed"]] for t in sel])
    ms = ctx.scope.excluded_summary()
    if ms:
        print(f"\nDescartados por prefixo da Microsoft (scope.exclude_microsoft): {sum(ms.values())} componente(s) — "
              + ", ".join(f"{p} {n}" for p, n in list(ms.items())[:10]))
    print(f"\nLista completa: {out}")
    print("Motivos: prefix/keyword/solution = critério do yaml · custom-columns = tabela nativa com coluna "
          "customizada · unmanaged = componente não gerenciado. Muitas tabelas por 'custom-columns'/'unmanaged' "
          "costumam vir de prefixos de terceiros: use scope.exclude_prefixes ou restrinja scope.prefixes/solutions.")


def cmd_extract(cfg, only=None, completar=None):
    if (cfg.raw_dir / "manifest.json").exists():
        print(f"[dvinv] aviso: {cfg.raw_dir} tem arquivos do formato antigo (<= 0.4); eles são ignorados — "
              "cada extração agora vira um snapshot em subpasta própria", file=sys.stderr)
    if cfg.profile != "padrao":
        print(f"[dvinv] perfil {cfg.profile}: bloqueado {', '.join(cfg.profile_blocked)}", flush=True)
    base = None
    if completar:
        from . import snapshot
        base = snapshot.resolve(cfg.raw_dir if completar in ("ultimo", "último", "latest") else completar, cfg.raw_dir)
    ctx = Context(cfg, _client(cfg), Scope(cfg))
    manifest = run(ctx, only, base=base)
    from .resumo import table_text
    print(table_text(manifest), flush=True)
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
    print(f"[dvinv] instruções para agentes: {cfg.vault_root / 'AGENTS.md'} (bloco dvinv) · CLAUDE.md")
    if getattr(v, "pruned", None):
        print(f"[dvinv] {len(v.pruned)} nota(s)/arquivo(s) de extrações anteriores removidos (sem curadoria)")
    if getattr(v, "orphans_kept", None):
        print(f"[dvinv] {len(v.orphans_kept)} nota(s) antigas com curadoria mantidas — ver 'Notas fora do inventário atual.md'")
    if getattr(cfg, "repo", False):
        print(f"[dvinv] repositório: {git_summary(cfg.vault_root)}")
    for w in getattr(v, "size_warnings", None) or []:
        print(f"[dvinv] aviso: arquivo grande para Git (> 50 MB): {w}")
    for w in getattr(v, "agent_warnings", None) or []:
        print(f"[dvinv] aviso: {w}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dvinv", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "escopo", "extract", "render", "all", "diff", "collectors", "snapshots",
                                        "buscar", "onde-usa", "impacto", "depende"])
    ap.add_argument("termo", nargs="?", help="buscar/onde-usa/impacto/depende: componente (nome, tabela.coluna, chave ou GUID)")
    ap.add_argument("-c", "--config", default="inventory.yaml")
    ap.add_argument("--only", help="lista de coletores separada por vírgula (ver 'collectors')")
    ap.add_argument("--extras", help="liga coletas de diagnóstico além do núcleo, separadas por vírgula: "
                    + ", ".join(config_mod.EXTRAS) + " (sobrepõe extras: do yaml)")
    ap.add_argument("--profile", choices=sorted(config_mod.PROFILES), help="perfil de coleta (sobrepõe o yaml)")
    ap.add_argument("--snapshot", help="render: diretório do snapshot ou run_id (padrão: último íntegro)")
    ap.add_argument("--no-snapshot", action="store_true", help=argparse.SUPPRESS)  # obsoleto desde 0.5.0
    ap.add_argument("--a", help="diff: snapshot A (diretório, raiz _raw → último íntegro, ou run_id)")
    ap.add_argument("--b", help="diff: snapshot B")
    ap.add_argument("--profundidade", type=int, help="impacto/depende: níveis (padrão 2 / 1)")
    ap.add_argument("--limite", type=int, default=150, help="consultas: máximo de linhas listadas")
    ap.add_argument("--completar", metavar="SNAPSHOT",
                    help="extract/all: completa um snapshot anterior (run_id, pasta ou 'ultimo') — roda o --only (ou, "
                         "sem --only, só o que falhou/faltou) e herda os demais coletores, declarando a origem")
    ap.add_argument("--vault", help="consultas: pasta do ambiente no vault (ex. vault/Dataverse/PRD); dispensa -c")
    args = ap.parse_args(argv)

    if args.command == "collectors":
        print("\n".join(n for n, _ in REGISTRY))
        return 0
    if args.command == "snapshots":
        from . import snapshot
        cfg = config_mod.load(args.config)
        total = len(REGISTRY)
        print(f"Snapshots em {cfg.raw_dir} (mais recente primeiro) — use o run_id em --completar ou render --snapshot:")
        for snap in snapshot.list_snapshots(cfg.raw_dir):
            m = snapshot.read_manifest(snap) or {}
            cols = m.get("collectors") or {}
            ok = [n for n, st in cols.items() if st.get("status") in ("ok", "parcial")]
            bad = [n for n, st in cols.items() if st.get("status") == "falhou"]
            print(f"  {snap.name}  {m.get('extracted_at', '?')}  {len(ok):>2}/{total} etapas íntegras"
                  + (f"  · falharam: {', '.join(bad)}" if bad else "")
                  + (f"  · completou {m['base']}" if m.get("base") else "")
                  + (f"  · --only {','.join(m['only'])}" if m.get("only") else ""))
        return 0
    if args.no_snapshot:
        print("[dvinv] --no-snapshot é obsoleto: cada extração já é um snapshot próprio (opção ignorada)",
              file=sys.stderr)

    if args.command in ("buscar", "onde-usa", "impacto", "depende"):
        if not args.termo:
            sys.exit(f"{args.command} exige o termo (ex.: dvinv {args.command} account.esab_campo --vault <pasta>)")
        from . import consulta
        vdir = args.vault or config_mod.load(args.config).vault_dir
        inv = consulta.Inventario(vdir)
        if args.command == "buscar":
            print(consulta.cmd_buscar(inv, args.termo, args.limite))
        else:
            direction = "depende" if args.command == "depende" else "impacto"
            depth = args.profundidade or (2 if args.command == "impacto" else 1)
            print(consulta.cmd_walk(inv, args.termo, direction, depth, args.limite))
        return 0

    overrides = {"extras": args.extras}
    if args.profile:
        overrides["profile"] = args.profile
    if args.only:
        overrides["collectors"] = [x.strip() for x in args.only.split(",") if x.strip()]
    cfg = config_mod.load(args.config, overrides)


    if args.command in ("extract", "render", "all"):
        start_log(cfg, args.command)
    try:
        if args.command == "check":
            cmd_check(cfg)
        elif args.command == "escopo":
            cmd_escopo(cfg)
        elif args.command == "extract":
            with run_lock(cfg):
                cmd_extract(cfg, cfg.collectors, args.completar or cfg.completar)
        elif args.command == "render":
            with run_lock(cfg):
                cmd_render(cfg, args.snapshot)
        elif args.command == "all":
            with run_lock(cfg):
                snap = cmd_extract(cfg, cfg.collectors, args.completar or cfg.completar)
                cmd_render(cfg, snap)
        elif args.command == "diff":
            if not (args.a and args.b):
                sys.exit("diff exige --a <snapshot> --b <snapshot>")
            from .diff import compare
            print(f"[dvinv] {compare(args.a, args.b, cfg.vault_root, cfg.vault_folder.rsplit('/', 1)[0], cfg.raw_dir)}")
    except secrets.SecretLeakError as e:
        sys.exit(f"[dvinv] ERRO: {e}")
    return 0
