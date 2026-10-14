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
        "2. Impacto e rastreamento pelos CSVs da pasta do ambiente, não lendo notas: "
        "`grep \";<chave>;\" Dependências.csv`, `grep -i <nome> Componentes.csv` ou "
        "`python3 <skill>/scripts/dvinv.py onde-usa|impacto|depende|buscar <alvo> --vault <pasta>`. "
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
    try:
        os.replace(tmp, path)
    except PermissionError:  # Windows: arquivo aberto num editor — não derruba o render
        os.unlink(tmp)
        raise


GIT_BEGIN, GIT_END = "# dvinv:inicio — bloco gerado pelo dataverse-inventory-kit; escreva fora dele", "# dvinv:fim"

GITIGNORE = """# estado local do Obsidian (cada pessoa tem o seu)
.obsidian/workspace*.json
.obsidian/cache
.trash/
# cópias de trabalho do render (removidas no fim; sobram só se o processo for interrompido)
.dvinv-render-*/
.*.dvinv-old-*/
# nunca versionar credenciais
.env
.env.*"""

GITATTRIBUTES = """*.md text eol=lf
*.csv text eol=lf linguist-generated=true
*.base text eol=lf
*.canvas text eol=lf linguist-generated=true
*.xlsx binary"""


def _managed(path, begin, end, body, header=""):
    """Escreve/atualiza só o trecho entre begin/end; o resto do arquivo é do time. → aviso ou None."""
    blk = f"{begin}\n{body}\n{end}"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        i, j = text.find(begin), text.find(end)
        if i >= 0 and j > i:
            new = text[:i] + blk + text[j + len(end):]
        elif i >= 0 or j >= 0:
            return f"{path}: marcador dvinv incompleto — bloco não atualizado (corrija à mão)"
        else:
            new = text.rstrip() + "\n\n" + blk + "\n"
    else:
        new = (header + blk + "\n") if header else blk + "\n"
    if not path.exists() or new != path.read_text(encoding="utf-8"):
        _atomic_write(path, new)
    return None


def readme_block(root, envs):
    rows = [f"| {e['ambiente']} | {e.get('url') or '—'} | {e.get('snapshot') or '—'} | "
            f"[AGENT-INDEX]({e['path'].replace(' ', '%20')}) |" for e in envs]
    return "\n".join([
        f"## Inventário Dataverse ({len(envs)} ambiente(s))",
        "",
        "Vault Obsidian gerado pelo `dataverse-inventory-kit` (`dvinv`), **somente leitura** do ambiente. Cada pasta",
        "`Dataverse/<ambiente>/` traz uma nota por componente customizado, achados automáticos, Bases e o índice",
        "textual de impacto (`Componentes.csv`, `Dependências.csv`).",
        "",
        "| Ambiente | URL | Snapshot (UTC) | Entrada |",
        "|---|---|---|---|",
        *rows,
        "",
        "**Como consultar**",
        "",
        "- Obsidian: abra esta pasta como vault; comece por `Dataverse/<ambiente>/00 Índice.md`.",
        "- Onde um campo/componente é usado: `grep \";column:<tabela>.<coluna>;\" Dataverse/<ambiente>/Dependências.csv`",
        "  ou `python3 <skill>/scripts/dvinv.py onde-usa <tabela>.<coluna> --vault Dataverse/<ambiente>`.",
        "- Agentes (Copilot, Claude): seguem o `AGENTS.md` desta pasta.",
        "",
        "**Não está neste repositório**: snapshot bruto da extração (código completo de web resources, definições de",
        "flows), credenciais e o cache SQLite — ficam na máquina/pipeline que extraiu. Atualizações chegam por um novo",
        "`render`; revise o diff antes do commit (componentes novos, alterados e removidos do ambiente).",
    ])


def update_root(vault_root, repo=False):
    """Atualiza AGENTS.md (bloco) e cria CLAUDE.md se faltar; em modo repositório, também README.md,
    .gitignore e .gitattributes (só os blocos dvinv). Devolve avisos para o usuário."""
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
    try:
        return _update_files(root, envs, blk, agents, repo, warnings)
    except PermissionError as e:
        return warnings + [f"não foi possível atualizar {getattr(e, 'filename', '') or 'AGENTS.md/README'} (arquivo em "
                           "uso no Windows?) — feche o editor e rode o render de novo; o vault já foi publicado"]


def _update_files(root, envs, blk, agents, repo, warnings):
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
    if repo:
        title = f"# {root.resolve().name}\n\n"
        for path, begin, end, body, header in (
                (root / "README.md", BEGIN, END, readme_block(root, envs), title),
                (root / ".gitignore", GIT_BEGIN, GIT_END, GITIGNORE, ""),
                (root / ".gitattributes", GIT_BEGIN, GIT_END, GITATTRIBUTES, "")):
            w = _managed(path, begin, end, body, header)
            if w:
                warnings.append(w)
    claude = root / "CLAUDE.md"
    if not claude.exists():
        _atomic_write(claude, IMPORT + "\n")
    elif IMPORT not in claude.read_text(encoding="utf-8", errors="replace"):
        warnings.append(f"{claude} existe sem '{IMPORT}': acrescente essa linha para o Claude Code ler o bloco do inventário")
    return warnings
