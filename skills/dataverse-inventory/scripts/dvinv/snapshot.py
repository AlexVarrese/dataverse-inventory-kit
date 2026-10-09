"""Snapshots imutáveis da extração bruta.

Estrutura (``raw_dir`` = raiz dos snapshots, padrão ``out/<ambiente>/_raw``)::

    _raw/
      20261009T203200Z/          snapshot publicado (um por extração; vários no mesmo dia coexistem)
        manifest.json            run_id, status por coletor, arquivos + sha256, lacunas
        tables.json ...          um arquivo por coletor que terminou (ok/parcial)
        bin/ decompiled/         binários de plugin (deep.plugin_binaries), quando houver
      .staging-<run_id>-xxxx/    extração em andamento (ignorada; removida se a extração falhar)
    _derived/
      20261009T203200Z/          saídas do render para aquele snapshot (findings.json, dependencies.json)

Regras:
- a extração grava tudo em ``.staging-*`` e só no fim publica com ``os.rename`` (atômico no mesmo
  sistema de arquivos) para ``_raw/<run_id>``; nada é escrito depois disso;
- o render/diff só leem os arquivos listados no manifesto do snapshot escolhido, conferindo o sha256;
  coletor que falhou ou não rodou simplesmente não tem arquivo — vira lacuna, nunca dado de outro run;
- saídas derivadas nunca são gravadas dentro do snapshot.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import secrets

SCHEMA = "dvinv-snapshot/1"
MANIFEST = "manifest.json"
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z(?:-\d+)?$")
DERIVED_NAMES = {"findings", "dependencies"}  # o formato antigo gravava estes dentro do _raw


class SnapshotError(SystemExit):
    pass


def new_run_id(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dump(data):
    return json.dumps(data, indent=1, ensure_ascii=False)


class Staging:
    """Diretório temporário de uma extração. `publish()` é o único caminho para virar snapshot."""

    def __init__(self, root, run_id=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        base = run_id or new_run_id()
        rid, n = base, 2
        while (self.root / rid).exists():
            rid, n = f"{base}-{n}", n + 1
        self.run_id = rid
        self.dir = Path(tempfile.mkdtemp(prefix=f".staging-{rid}-", dir=self.root))
        self.published = None

    def write_json(self, name, data):
        """Grava <name>.json já redigido (redact_tree)."""
        path = self.dir / f"{name}.json"
        path.write_text(_dump(secrets.redact_tree(data)), encoding="utf-8")
        return path

    def publish(self, manifest):
        """Fecha o manifesto (lista + sha256), varre segredos (fail-closed) e publica por rename."""
        files = {}
        for p in sorted(self.dir.rglob("*")):
            if p.is_file() and p.name != MANIFEST:
                files[p.relative_to(self.dir).as_posix()] = sha256_file(p)
        manifest = secrets.redact_tree(dict(manifest, schema=SCHEMA, run_id=self.run_id, files=files))
        (self.dir / MANIFEST).write_text(_dump(manifest), encoding="utf-8")
        try:
            secrets.assert_clean([self.dir], f"snapshot {self.run_id}")
        except secrets.SecretLeakError:
            self.discard()
            raise
        final = self.root / self.run_id
        os.rename(self.dir, final)  # atômico; falha se o destino já existir com conteúdo
        self.published = final
        return final, manifest

    def discard(self):
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------------------------------------
# leitura

def read_manifest(snap):
    p = Path(snap) / MANIFEST
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def verify(snap):
    """(ok, problemas). Confere schema, presença e sha256 de cada arquivo listado."""
    snap = Path(snap)
    m = read_manifest(snap)
    if not m or m.get("schema") != SCHEMA:
        return False, ["manifest.json ausente, ilegível ou de outro formato"]
    probs = []
    for rel, digest in (m.get("files") or {}).items():
        p = snap / rel
        if not p.is_file():
            probs.append(f"{rel}: ausente")
        elif sha256_file(p) != digest:
            probs.append(f"{rel}: sha256 não confere")
    return not probs, probs


def list_snapshots(root):
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name)), key=lambda p: p.name,
                  reverse=True)


def latest(root, warn=True):
    for p in list_snapshots(root):
        ok, probs = verify(p)
        if ok:
            return p
        if warn:
            print(f"[dvinv] snapshot {p.name} ignorado (íntegro? não): {'; '.join(probs[:3])}", file=sys.stderr)
    return None


def is_legacy(path):
    m = read_manifest(path)
    return bool(m) and m.get("schema") != SCHEMA


def resolve(path, root=None):
    """Aceita: diretório de snapshot, raiz de snapshots (→ último íntegro), run_id (relativo a `root`)
    ou diretório no formato antigo (arquivos soltos + manifest.json, sem verificação de integridade)."""
    p = Path(path)
    if not p.exists() and root is not None and RUN_ID_RE.match(str(path)):
        p = Path(root) / str(path)
    m = read_manifest(p)
    if m and m.get("schema") == SCHEMA:
        return p
    snap = latest(p)
    if snap:
        return snap
    if m:  # formato antigo (<= 0.4): só quando não há snapshot novo
        return p
    raise SnapshotError(f"Nenhum snapshot íntegro em {p} — rode 'extract' antes.")


def load(snap):
    """dict nome → conteúdo, só com os arquivos listados no manifesto (e conferidos)."""
    snap = Path(snap)
    m = read_manifest(snap)
    if m is None:
        raise SnapshotError(f"{snap} não tem manifest.json")
    if m.get("schema") == SCHEMA:
        ok, probs = verify(snap)
        if not ok:
            raise SnapshotError(f"Snapshot {snap} não está íntegro: {'; '.join(probs[:5])}")
        d = {"manifest": m}
        for rel in m.get("files") or {}:
            if "/" not in rel and rel.endswith(".json"):
                d[rel[:-5]] = json.loads((snap / rel).read_text(encoding="utf-8"))
        return d
    print(f"[dvinv] AVISO: {snap} está no formato antigo (sem manifesto de integridade); arquivos de outra "
          "extração podem estar misturados. Rode 'extract' de novo para ter um snapshot verificável.", file=sys.stderr)
    d = {}
    for p in snap.glob("*.json"):
        if p.stem not in DERIVED_NAMES:
            d[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    d["manifest"] = dict(d.get("manifest") or {}, legacy=True)
    return d


def run_id_of(snap, manifest):
    return manifest.get("run_id") or f"legado-{Path(snap).name}"


# ---------------------------------------------------------------------------------------------
# saídas derivadas (fora do snapshot)

class DerivedStaging:
    """findings.json / dependencies.json de um run, publicados juntos por rename."""

    def __init__(self, derived_root, run_id):
        self.root = Path(derived_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.dir = Path(tempfile.mkdtemp(prefix=f".staging-{run_id}-", dir=self.root))
        self.paths = []

    def write_json(self, name, data):
        p = self.dir / f"{name}.json"
        p.write_text(_dump(data), encoding="utf-8")
        self.paths.append(p)
        return p

    def publish(self):
        final = self.root / self.run_id
        old = None
        if final.exists():
            old = self.root / f".old-{self.run_id}-{os.getpid()}"
            os.rename(final, old)
        os.rename(self.dir, final)
        if old:
            shutil.rmtree(old, ignore_errors=True)
        return final

    def discard(self):
        shutil.rmtree(self.dir, ignore_errors=True)
