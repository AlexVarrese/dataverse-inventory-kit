"""Instruções carregadas automaticamente pelos agentes na raiz do vault (AGENTS.md / CLAUDE.md).

O `AGENT-INDEX.md` de cada ambiente fica numa subpasta e só é achado se alguém apontar para ele.
Claude Code lê `CLAUDE.md` e o GitHub Copilot lê `AGENTS.md` da raiz em toda sessão — então o render
mantém ali um bloco curto que diz onde está cada inventário, como consultá-lo e como (não) reprocessá-lo.

Regras:
- `AGENTS.md`: o kit só escreve entre os marcadores BEGIN/END. Fora deles o texto é do time e nunca é
  alterado. Sem o arquivo, cria um só com o bloco.
- `CLAUDE.md`: criado com `@AGENTS.md` apenas se não existir. Se existir sem esse import, não é tocado —
  o render só avisa.
- Bloco curto de propósito: é lido em toda sessão (custa tokens sempre). Detalhe fica no AGENT-INDEX.
"""

import os
import tempfile
from pathlib import Path

import yaml

from .. import secrets

BEGIN = "<!-- dvinv:inicio — bloco gerado pelo dataverse-inventory-kit a cada render; escreva FORA dele -->"
END = "<!-- dvinv:fim -->"
IMPORT = "@AGENTS.md"


def _frontmatter(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            try:
                return yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                return {}
    return {}


def environments(vault_root):
    """AGENT-INDEX.md de cada ambiente do vault (até 3 níveis: <vault_folder> padrão é Dataverse/<amb>)."""
    root = Path(vault_root)
    out = []
    for pattern in ("AGENT-INDEX.md", "*/AGENT-INDEX.md", "*/*/AGENT-INDEX.md", "*/*/*/AGENT-INDEX.md"):
        for p in sorted(root.glob(pattern)):
            if any(part.startswith(".") for part in p.relative_to(root).parts):
                continue  # cópias de trabalho do render (.dvinv-render-*), lixeira etc.
            fm = _frontmatter(p)
            out.append({"path": p.relative_to(root).as_posix(), "ambiente": fm.get("ambiente") or p.parent.name,
                        "url": fm.get("url"), "config": fm.get("config"), "snapshot": fm.get("snapshot")})
    return out


def block(envs):
    rows = [f"| {e['ambiente']} | {e.get('url') or '—'} | [{e['path']}]({e['path'].replace(' ', '%20')}) | "
            f"`{e.get('config') or '—'}` | {e.get('snapshot') or '—'} |" for e in envs]
    return "\n".join([
        BEGIN,
        "## Inventário Dataverse (dvinv)",
        "",
        "Este vault tem inventário **read-only** gerado pela skill `dataverse-inventory` (`dvinv`). "
        "`<skill>` = pasta da skill (ex. `.claude/skills/dataverse-inventory`).",
        "",
        "| Ambiente | URL | Entrada para agentes | Config | Snapshot |",
        "|---|---|---|---|---|",
        *rows,
        "",
        "1. Antes de responder sobre componentes, impacto ou problemas, abra o `AGENT-INDEX.md` do ambiente.",
        "2. Impacto e rastreamento pelo índice, não lendo notas: "
        "`python3 <skill>/scripts/dvinv.py onde-usa|impacto|depende|buscar <alvo> -c <config>`. "
        "Não leia inteiros a Matriz de Dependências nem os CSVs.",
        "3. Reprocessar (`extract`/`render`/`all`) só a pedido explícito do usuário, com o config da tabela. "
        "Não crie outro `inventory*.yaml` nem mude a URL de um existente. Uma execução por ambiente "
        "(trava `.dvinv.lock`): se travar, espere — não apague.",
        "4. Não edite acima do marcador `%% dvinv:manual %%` das notas (é regenerado); a curadoria vai abaixo dele.",
        "5. Nunca exiba nem copie `.env`, tokens ou segredos; ausência de dependência não prova ausência de uso.",
        END,
    ])


def _atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def update_root(vault_root):
    """Atualiza AGENTS.md (bloco) e cria CLAUDE.md se faltar. Devolve avisos para o usuário."""
    root = Path(vault_root)
    envs = environments(root)
    if not envs:
        return []
    blk = block(envs)
    hits = secrets.scan(blk)
    if hits:
        raise secrets.SecretLeakError("AGENTS.md (bloco dvinv)", [(str(root / "AGENTS.md"), h) for h in hits])
    warnings = []
    agents = root / "AGENTS.md"
    if agents.exists():
        text = agents.read_text(encoding="utf-8")
        i, j = text.find(BEGIN), text.find(END)
        if i >= 0 and j > i:
            new = text[:i] + blk + text[j + len(END):]
        elif i >= 0 or j >= 0:
            warnings.append(f"{agents}: marcador dvinv incompleto — bloco não atualizado (corrija à mão)")
            new = text
        else:
            new = text.rstrip() + "\n\n" + blk + "\n"
    else:
        new = "# Instruções para agentes\n\n" + blk + "\n"
    if not agents.exists() or new != agents.read_text(encoding="utf-8"):
        _atomic_write(agents, new)
    claude = root / "CLAUDE.md"
    if not claude.exists():
        _atomic_write(claude, IMPORT + "\n")
    elif IMPORT not in claude.read_text(encoding="utf-8", errors="replace"):
        warnings.append(f"{claude} existe sem '{IMPORT}': acrescente essa linha para o Claude Code ler o bloco do inventário")
    return warnings
