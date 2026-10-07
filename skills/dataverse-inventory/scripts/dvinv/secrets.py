"""Detecção e redação de segredos em conteúdo extraído.

Motivação: é recorrente achar SAS tokens de Logic App em web resources JS, function keys de
Azure Function em plugins e senhas de conta de serviço em configuração.
Nada que este pacote grava em disco (raw JSON, fontes, notas) pode carregar esses valores.
"""

import re

REDACTED = "«REDACTED»"

# (nome, regex). O grupo 'v' é o valor a redigir; o restante do match é mantido como contexto.
PATTERNS = [
    ("SAS signature (sig=)", re.compile(r"(?<![A-Za-z])sig=(?P<v>[A-Za-z0-9%/+=]{16,})")),
    ("Azure Function key (code=)", re.compile(r"[?&]code=(?P<v>[A-Za-z0-9_\-/+=%]{20,})")),
    ("Storage AccountKey", re.compile(r"AccountKey=(?P<v>[A-Za-z0-9+/=]{20,})")),
    ("SharedAccessKey", re.compile(r"SharedAccessKey=(?P<v>[A-Za-z0-9+/=]{20,})")),
    ("Bearer token", re.compile(r"[Bb]earer\s+(?P<v>[A-Za-z0-9\-_\.=]{30,})")),
    ("JWT", re.compile(r"(?P<v>eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")),
    # .NET: <add key="Password" value="..."/> (app.config / web.config)
    ("Senha em appSettings (.NET)", re.compile(
        r"(?i)key=[\"'][\w.:-]*(?:password|passwd|pwd|senha|secret|apikey|api_key|clientsecret)[\"']\s+"
        r"value=[\"'](?P<v>[^\"']{6,})[\"']")),
    # connection string: ...;Password=xxx; — ignora só valores que COMEÇAM como placeholder ({0}, $(Var));
    # senha real pode ter $ ou { no meio
    ("Senha em connection string", re.compile(r"(?i)(?:^|[;\"'\s])(?:password|pwd)=(?!\$\(|\{)(?P<v>[^;\"'\s]{6,})")),
    ("Senha/segredo literal", re.compile(
        r"(?i)(?:password|passwd|pwd|senha|client_?secret|api_?key|x-api-key|secret)"
        r"[\"']?\s*[:=]\s*[\"'](?P<v>[^\"'\s]{6,})[\"']")),
]


def scan(text):
    """Lista de (tipo, posição) sem o valor."""
    if not text or not isinstance(text, str):
        return []
    hits = []
    for name, rx in PATTERNS:
        for m in rx.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append({"kind": name, "line": line})
    return hits


def redact(text):
    if not text or not isinstance(text, str):
        return text
    for _, rx in PATTERNS:
        text = rx.sub(lambda m: m.group(0).replace(m.group("v"), REDACTED), text)
    return text


def redact_tree(obj):
    """Redige recursivamente todas as strings de uma estrutura JSON."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_tree(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_tree(v) for k, v in obj.items()}
    return obj
