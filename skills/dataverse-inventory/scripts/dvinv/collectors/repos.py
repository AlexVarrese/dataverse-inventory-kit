"""Comparação ambiente × repositório Git (clones locais listados em `repos:` no inventory.yaml).

Responde às perguntas que mais geram retrabalho em projetos herdados:
- O web resource publicado é o mesmo que está no Git? Que funções existem só de um lado?
- Toda classe de plugin registrada tem fonte no repositório? (com DLL decompilada: que métodos
  existem só em produção ou só no repo — drift real, não ruído de decompilador)
- O repositório tem segredo em texto claro (app.config, appsettings, JS)?
- Em que arquivos de código cada coluna customizada é citada (insumo da matriz de uso de campos)?

Só lê arquivos locais e roda `git rev-parse`/`git log -1` (read-only). Nada é clonado/alterado.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from .. import secrets
from ..util import field_refs
from .ui import FUNC_RE

TEXT_EXT = {".cs", ".js", ".ts", ".html", ".htm", ".xml", ".config", ".json", ".css", ".resx", ".svg",
            ".xaml", ".ps1", ".py", ".fetchxml", ".txt", ".yml", ".yaml"}
SKIP_DIRS = {".git", "bin", "obj", "node_modules", "packages", ".vs", "dist", "TestResults"}
MAX_FILE = 2_000_000
METHOD_RE = re.compile(r"(?:public|private|protected|internal)\s+(?:static\s+|virtual\s+|override\s+|async\s+)*"
                       r"[\w<>\[\],\s\.?]+?\s+(\w+)\s*\(")


def norm(text):
    """Normaliza para comparação: sem BOM, CRLF, espaços de borda e linhas vazias; segredos redigidos."""
    text = secrets.redact(text.lstrip("﻿"))
    return "\n".join(l.strip() for l in text.replace("\r\n", "\n").split("\n") if l.strip())


def jaccard(a, b):
    sa, sb = set(a.split("\n")), set(b.split("\n"))
    return len(sa & sb) / len(sa | sb) if sa | sb else 1.0


def git_info(path):
    def run(*args):
        try:
            # safe.directory: clones de outro usuário (ex. volume montado) são recusados sem isso; só leitura
            return subprocess.run(["git", "-c", f"safe.directory={path}", "-C", str(path), *args],
                                  capture_output=True, text=True,
                                  timeout=20).stdout.strip() or None
        except Exception:  # noqa: BLE001
            return None
    return {"branch": run("rev-parse", "--abbrev-ref", "HEAD"), "commit": run("log", "-1", "--format=%h %cs %s")}


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in TEXT_EXT:
                try:
                    if p.stat().st_size <= MAX_FILE:
                        yield p, p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue


def wr_keys(name):
    """Possíveis nomes de arquivo para um web resource: 'contoso_/js/account.js' → account.js, account…"""
    leaf = name.rsplit("/", 1)[-1].lower()
    keys = {leaf, Path(leaf).stem}
    for p in ("_js", "_css", "_html"):
        if leaf.endswith(p):
            keys.add(leaf[: -len(p)] + "." + p[1:])
    if "_" in leaf:
        keys.add(leaf.split("_", 1)[1])  # sem o prefixo do publisher
    return keys


def methods(text, cls):
    """Métodos declarados dentro de 'class <cls>' (heurística por chaves balanceadas)."""
    m = re.search(rf"\bclass\s+{re.escape(cls)}\b[^{{]*{{", text)
    if not m:
        return set()
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    body = text[m.end():i]
    return {n for n in METHOD_RE.findall(body) if n != cls and n not in ("if", "for", "while", "switch", "catch", "using")}


def decompile(ctx, dll):
    tool = shutil.which("ilspycmd")
    if not tool:
        return None
    out = ctx.cfg.raw_dir / "decompiled" / Path(dll).stem
    if not out.exists():
        out.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([tool, "-p", "-o", str(out), str(dll)], capture_output=True, timeout=600, check=True)
        except Exception as e:  # noqa: BLE001
            ctx.gap("repos", f"decompilar {Path(dll).name}", e)
            return None
    return out


def collect_repos(ctx):
    cfg = ctx.cfg
    if not cfg.repos:
        return
    known_fields = {c["logical"] for t in ctx.data.get("tables", []) for c in t.get("columns", []) if c.get("custom")}
    wrs = ctx.data.get("webresources") or []
    plugins = ctx.data.get("plugins") or {}
    out = []
    for r in cfg.repos:
        root = Path(r["path"])
        if not root.exists():
            ctx.gap("repos", f"repositório {root}", "caminho não existe")
            continue
        kind = r.get("kind", "any")
        rec = {"path": str(root), "name": r.get("name") or root.name, "kind": kind, **git_info(root),
               "files": 0, "webresources": [], "plugin_types": [], "secret_hits": [], "field_hits": {}}
        by_name, js_files, cs_files = {}, [], []
        for p, text in iter_files(root):
            rel = str(p.relative_to(root))
            rec["files"] += 1
            for h in secrets.scan(text):
                rec["secret_hits"].append({"file": rel, "line": h["line"], "kind": h["kind"]})
            for f in field_refs(text, known_fields):
                rec["field_hits"].setdefault(f, []).append(rel)
            by_name.setdefault(p.name.lower(), []).append((rel, text))
            by_name.setdefault(p.stem.lower(), []).append((rel, text))
            if p.suffix.lower() in (".js", ".ts", ".html", ".htm", ".css"):
                js_files.append((rel, text))
            if p.suffix.lower() == ".cs":
                cs_files.append((rel, text))

        if kind in ("webresources", "any"):
            normed = None
            for w in wrs:
                if not w.get("content"):
                    continue
                env = norm(w["content"])
                cands = [c for k in wr_keys(w["name"]) for c in by_name.get(k, [])]
                how = "nome"
                if not cands:  # sem arquivo de mesmo nome: procura pelo conteúdo mais parecido
                    if normed is None:
                        normed = [(rel, norm(t)) for rel, t in js_files]
                    scored = sorted(((jaccard(env, n), rel, n) for rel, n in normed), reverse=True)[:1]
                    if scored and scored[0][0] >= 0.5:
                        cands, how = [(scored[0][1], None)], "conteúdo"
                        best = scored[0]
                if not cands:
                    rec["webresources"].append({"name": w["name"], "status": "sem fonte no repo"})
                    continue
                if how == "nome":
                    best = max(((jaccard(env, norm(t)), rel, norm(t)) for rel, t in cands))
                sim, rel, repo_n = best
                f_env = {next(g for g in m.groups() if g) for m in FUNC_RE.finditer(env)}
                f_repo = {next(g for g in m.groups() if g) for m in FUNC_RE.finditer(repo_n)}
                rec["webresources"].append({
                    "name": w["name"], "file": rel, "match": how, "similarity": round(sim, 3),
                    "status": "idêntico" if env == repo_n else "divergente",
                    "functions_only_env": sorted(f_env - f_repo), "functions_only_repo": sorted(f_repo - f_env),
                })

        if kind in ("plugins", "any"):
            for a in plugins.get("assemblies") or []:
                dec_dir = decompile(ctx, a["binary"]) if a.get("binary") else None
                dec_files = list(iter_files(dec_dir)) if dec_dir else []
                for t in a.get("types") or []:
                    cls = (t["typename"] or "").rsplit(".", 1)[-1]
                    hits = [(rel, text) for rel, text in cs_files if re.search(rf"\bclass\s+{re.escape(cls)}\b", text)]
                    item = {"assembly": a["name"], "type": t["typename"], "repo_files": [h[0] for h in hits],
                            "status": "com fonte" if hits else "sem fonte no repo"}
                    if dec_files and hits:
                        dec = [(str(p), x) for p, x in dec_files if re.search(rf"\bclass\s+{re.escape(cls)}\b", x)]
                        if dec:
                            m_prd = methods(dec[0][1], cls)
                            m_repo = set().union(*(methods(x, cls) for _, x in hits))
                            item.update(decompiled_file=dec[0][0], methods_only_prd=sorted(m_prd - m_repo),
                                        methods_only_repo=sorted(m_repo - m_prd))
                    rec["plugin_types"].append(item)
        rec["field_hits"] = {f: {"count": len(v), "files": v[:10]} for f, v in rec["field_hits"].items()}
        out.append(rec)
    ctx.stats["repos"] = {r["name"]: {"files": r["files"], "secret_hits": len(r["secret_hits"])} for r in out}
    ctx.data["repos"] = out
