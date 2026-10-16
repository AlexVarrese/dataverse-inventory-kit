"""Detecção e redação de segredos em conteúdo extraído.

Motivação: é recorrente achar SAS tokens de Logic App em web resources JS, function keys de
Azure Function em plugins e senhas de conta de serviço em configuração.
Nada que este pacote grava em disco (raw JSON, fontes, notas, planilhas) pode carregar esses valores.

Três camadas:

1. `redact(text)` — padrões textuais: parâmetros de query sensíveis, headers de autenticação,
   credencial embutida em URL (`https://user:senha@host`), atribuições JS/JSON/YAML/.env de nomes
   sensíveis (com ou sem aspas), connection strings, SAS, JWT, Bearer.
2. `redact_tree(obj)` — redação estruturada: além de redigir cada string, redige o valor inteiro de
   chaves de dicionário com nome sensível (`{"x-functions-key": "..."}` não tem contexto na string).
3. `redact_serialized(obj)` — passada sobre a forma GRAVADA: no JSON, strings vizinhas ficam lado a lado
   (`"Authorization",\n "Content-Type"`) e podem casar um padrão que nenhuma casa sozinha (aqui, o de
   `setRequestHeader("Authorization", valor)`). Redige o valor vizinho; chaves nunca mudam.
4. `sanitize_paths(paths)` — antes de publicar, limpa no lugar o que não passou pela gravação conferida (fontes
   decompiladas, notas); quem chama remove o arquivo que não der para limpar e declara a lacuna.
5. `scan_paths(paths)` / `assert_clean(paths)` — varredura final (fail-closed) do que sobrou; se algum padrão
   ainda casar com valor não redigido, a publicação é abortada com `SecretLeakError`.

Toda detecção considera também a forma com entidades HTML decodificadas (`&quot;apiKey&quot;`).

Na dúvida, redige a mais: falso positivo custa um «REDACTED» numa nota; falso negativo vaza credencial.
"""

import html
import json
import re
import zipfile
from pathlib import Path

REDACTED = "«REDACTED»"

# Nomes de parâmetro de query que carregam segredo (comparação sem caixa).
_QP = (r"api[_-]?key|apikey|key|code|sig|signature|token|access[_-]?token|id[_-]?token|refresh[_-]?token|"
       r"auth[_-]?token|client[_-]?secret|clientsecret|secret|password|passwd|pwd|"
       r"subscription[_-]?key|ocp-apim-subscription-key|x-api-key|apim?[_-]?key|sas|sastoken|sharedaccesssignature")
# Headers de autenticação.
_HDR = (r"authorization|proxy-authorization|x-functions-key|ocp-apim-subscription-key|x-api-key|api-key|"
        r"x-ms-client-secret|x-auth-token")
# Nomes de variável/propriedade sensíveis: o identificador precisa TERMINAR com um destes
# (assim `tokenize(`, `tokenizer`, `tokenType`, `secret_hits` não casam).
_NAME = (r"password|passwd|pwd|senha|secret|api[_-]?key|apikey|access[_-]?key|account[_-]?key|private[_-]?key|"
         r"subscription[_-]?key|functions?[_-]?key|auth[_-]?key|token")
# Valores que são claramente placeholders de template (não são segredo).
_NOT_PLACEHOLDER = r"(?!\$\{|\{\{|\$\()"

# (nome, regex). O grupo 'v' é o valor a redigir; o restante do match é mantido como contexto.
PATTERNS = [
    ("SAS signature (sig=)", re.compile(r"(?<![A-Za-z])sig=(?P<v>[A-Za-z0-9%/+=]{16,})")),
    ("Parâmetro de URL sensível", re.compile(
        rf"(?i)(?<=[?&;])(?:{_QP})=(?P<v>[^&#;\s\"'<>`]+)")),
    ("Credencial em URL (user:senha@)", re.compile(
        r"(?i)\b[a-z][a-z0-9+.\-]*://(?P<v>[^/\s@\"'<>?#`]+)@")),
    ("Storage AccountKey", re.compile(r"AccountKey=(?P<v>[A-Za-z0-9+/=]{20,})")),
    ("SharedAccessKey", re.compile(r"SharedAccessKey=(?P<v>[A-Za-z0-9+/=]{20,})")),
    ("Header de autenticação", re.compile(
        rf"(?i)[\"'](?:{_HDR})[\"']\s*[:,]\s*[\"'](?!(?:Bearer|Basic)\s*[\"'])(?P<v>[^\"'\r\n]{{3,}})[\"']")),
    ("Header de autenticação", re.compile(
        rf"(?im)(?:^|(?<=[\"'`]))[ \t]*(?:{_HDR})[ \t]*:[ \t]*(?!(?:Bearer|Basic)[ \t]*(?:[\"'`]|$))"
        rf"(?P<v>[^\s\"'`][^\"'`\r\n]{{2,}})")),
    # header citado no meio de texto (mensagem de erro, log): "... (x-functions-key: abc123)"
    ("Header de autenticação", re.compile(
        rf"(?i)(?<![A-Za-z0-9_-])(?:{_HDR})[ \t]*:[ \t]*(?!(?:Bearer|Basic|Digest)[ \t]*(?:[\"'`]|$))"
        r"(?:(?:Bearer|Basic|Digest|SharedAccessSignature)[ \t]+)?(?P<v>[^\s\"'`,;)}\]]{3,})")),
    ("Bearer token", re.compile(
        r"[Bb]earer\s+(?P<v>(?=[A-Za-z0-9\-_.=~+/]*[0-9._~+/=-])[A-Za-z0-9\-_.=~+/]{16,})")),
    ("JWT", re.compile(r"(?P<v>eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")),
    # .NET: <add key="Password" value="..."/> (app.config / web.config)
    ("Senha em appSettings (.NET)", re.compile(
        r"(?i)key=[\"'][\w.:-]*(?:password|passwd|pwd|senha|secret|apikey|api_key|clientsecret|token)[\"']\s+"
        r"value=[\"'](?P<v>[^\"']{4,})[\"']")),
    # connection string: ...;Password=xxx; — ignora só valores que COMEÇAM como placeholder ({0}, $(Var));
    # senha real pode ter $ ou { no meio
    ("Senha em connection string", re.compile(r"(?i)(?:^|[;\"'\s])(?:password|pwd)=(?!\$\(|\{)(?P<v>[^;\"'\s]{4,})")),
    # JS/JSON/C#/XML: token = '...', "apiKey": "...", password="..."
    ("Segredo atribuído (literal)", re.compile(
        rf"(?i)(?<![A-Za-z0-9_$])[A-Za-z0-9_$.\-]*?(?:{_NAME})[\"']?\s*(?::|=>|=(?!=))\s*{_NOT_PLACEHOLDER}"
        r"[\"'`](?P<v>[^\"'`\r\n]{4,})[\"'`]")),
    # YAML/.env/properties sem aspas: password: abc123 / CLIENT_SECRET=abc123 (valor até o fim da linha)
    ("Segredo atribuído (YAML/.env)", re.compile(
        rf"(?im)^[ \t]*(?:-[ \t]+)?(?:export[ \t]+)?[A-Za-z0-9_.\-]*?(?:{_NAME})[ \t]*[:=][ \t]*{_NOT_PLACEHOLDER}"
        r"(?P<v>[^\s\"'`#;,(){}\[\]<>|][^\s\"'`#;,(){}\[\]<>|]{3,})[ \t]*(?:#.*)?$")),
]

# Chaves de dicionário cujo valor (string) é redigido inteiro em redact_tree.
SENSITIVE_KEY = re.compile(
    rf"(?i)^(?:[A-Za-z0-9_.$\-]*?(?:{_NAME}|client[_-]?secret)|{_HDR}|sig|signature|sas|sastoken)$")
NOT_SENSITIVE_KEYS = {"publickeytoken", "public_key_token"}  # token público de assinatura .NET, não é segredo
# Mesma exceção na varredura textual, e só no formato fixo do .NET: o nome exato + 16 hexadecimais
# (`"publickeytoken": "31bf3856ad364e35"`). Qualquer outro valor nesse campo, ou outro `…token`, continua
# sendo segredo. Sem isso, todo assembly de plugin assinado abortava a publicação do snapshot.
_PUBLIC_KEY_TOKEN = re.compile(r"(?i)[\"']?public_?key_?token[\"']?\s*(?::|=>|=)\s*[\"'`][0-9a-f]{16}[\"'`]")


def _matches(text):
    """[(início_v, fim_v, tipo)] sem sobreposição, ignorando valores já redigidos."""
    spans = []
    for name, rx in PATTERNS:
        for m in rx.finditer(text):
            v = m.group("v")
            if not v or REDACTED in v or _PUBLIC_KEY_TOKEN.fullmatch(m.group(0)):
                continue
            spans.append((m.start("v"), m.end("v"), name))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out, last_end = [], -1
    for s, e, name in spans:
        if s < last_end:  # sobrepõe um trecho já contado (dois padrões, mesmo segredo)
            continue
        out.append((s, e, name))
        last_end = e
    return out


def _decoded(text):
    """Forma com entidades HTML decodificadas, se diferente (HTML de web resource: `&quot;apiKey&quot;: …`)."""
    if "&" not in text:
        return None
    un = unescape_all(text)
    return un if un != text else None


def scan(text):
    """Lista de {kind, line} sem o valor (um item por segredo, mesmo que vários padrões casem). Considera também a
    forma com entidades HTML decodificadas — vale a que acusar mais."""
    if not text or not isinstance(text, str):
        return []
    hits = [{"kind": name, "line": text.count("\n", 0, s) + 1} for s, _e, name in _matches(text)]
    un = _decoded(text)
    if un is not None:
        enc = [{"kind": name, "line": un.count("\n", 0, s) + 1} for s, _e, name in _matches(un)]
        if len(enc) > len(hits):
            return enc
    return hits


def redact(text):
    """Redige os padrões; se ainda sobrar segredo escondido em entidades HTML, devolve a forma decodificada, redigida
    (só nesse caso o texto muda além do «REDACTED»)."""
    if not text or not isinstance(text, str):
        return text
    text = _redact_plain(text)
    un = _decoded(text)
    if un is not None and _matches(un):
        text = _redact_plain(un)
    return text


def _redact_plain(text):
    # repete até estabilizar: um valor redigido pode expor outro padrão (ex. header com URL dentro)
    for _ in range(4):
        spans = _matches(text)
        if not spans:
            break
        parts, pos = [], 0
        for s, e, _name in spans:
            parts += [text[pos:s], REDACTED]
            pos = e
        parts.append(text[pos:])
        text = "".join(parts)
    return text


def is_sensitive_key(key):
    return isinstance(key, str) and key.lower() not in NOT_SENSITIVE_KEYS and bool(SENSITIVE_KEY.match(key))


def redact_tree(obj):
    """Redige recursivamente uma estrutura JSON: strings por padrão e valores de chaves sensíveis."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_tree(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, str) and v and is_sensitive_key(k):
                out[k] = REDACTED
            else:
                out[k] = redact_tree(v)
        return out
    return obj


def _dirty(x):
    return bool(scan(json.dumps(x, indent=1, ensure_ascii=False))
                or scan(json.dumps(x, ensure_ascii=False, separators=(",", ":"))))


def redact_serialized(obj):
    """Redige o que só casa um padrão depois de serializado (vizinhos numa lista ou num dicionário, escape do JSON).

    Só desce nos contêineres cuja forma gravada casa algum padrão; em cada par vizinho que casa, redige o valor de
    baixo e, se não bastar, o de cima. Chaves não mudam (a estrutura continua a mesma). → (obj, quantos redigidos)."""
    n = 0

    def fix_pair(get, set_, slots):
        nonlocal n
        for j in reversed(slots):
            if not _dirty(get()):
                return
            v = set_(j, None)
            if isinstance(v, str) and v != REDACTED:
                set_(j, REDACTED)
                n += 1

    def fix(o):
        nonlocal n
        if isinstance(o, str):
            if not _dirty(o):
                return o
            n += 1
            try:  # o escape do JSON expôs o padrão: redige a forma gravada e volta ao texto
                back = json.loads(redact(json.dumps(o, ensure_ascii=False)))
                return back if isinstance(back, str) and not _dirty(back) else REDACTED
            except ValueError:
                return REDACTED
        if not isinstance(o, (list, dict)) or not _dirty(o):
            return o
        if isinstance(o, list):
            o = [fix(x) for x in o]
            for i in range(len(o) - 1):
                def set_(j, v, i=i):
                    if v is not None:
                        o[i + j] = v
                    return o[i + j]
                fix_pair(lambda i=i: o[i:i + 2], set_, [0, 1])
            return o
        o = {k: fix(v) for k, v in o.items()}
        keys = list(o)
        for a, b in zip(keys, keys[1:]):
            def set_(j, v, ab=(a, b)):
                if v is not None:
                    o[ab[j]] = v
                return o[ab[j]]
            fix_pair(lambda a=a, b=b: {a: o[a], b: o[b]}, set_, [0, 1])
        return o

    return fix(obj), n


# ---------------------------------------------------------------------------------------------
# Varredura final (fail-closed)

class SecretLeakError(RuntimeError):
    """Algum arquivo gerado ainda contém um valor que casa com padrão de segredo."""

    def __init__(self, label, hits):
        self.hits = hits
        lines = [f"  {p} linha {h['line']}: {h['kind']}" for p, h in hits[:20]]
        more = f"\n  … +{len(hits) - 20}" if len(hits) > 20 else ""
        super().__init__(f"{label}: possível segredo não redigido em {len(hits)} ponto(s) — publicação abortada "
                         f"(valores não exibidos):\n" + "\n".join(lines) + more)


UNESCAPE_SUFFIXES = {".xml", ".html", ".md", ".cs", ".csv"}  # varridos também com as entidades decodificadas
TEXT_SUFFIXES = {".json", ".md", ".csv", ".base", ".canvas", ".txt", ".yaml", ".yml", ".cs", ".js", ".xml", ".html"}
ZIP_SUFFIXES = {".xlsx", ".zip"}


def _strings(obj, out):
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, list):
        for x in obj:
            _strings(x, out)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.append(k)
            _strings(v, out)


def unescape_all(text):
    """Decodifica entidades HTML até estabilizar: `&amp;quot;` (dupla codificação) também vira `"`."""
    for _ in range(4):
        un = html.unescape(text)
        if un == text:
            break
        text = un
    return text


def _texts(path):
    path = Path(path)
    suf = path.suffix.lower()
    if suf in ZIP_SUFFIXES:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.lower().endswith((".xml", ".rels", ".txt", ".json")):
                    yield f"{path}!{n}", html.unescape(z.read(n).decode("utf-8", "replace"))
    elif suf in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        yield str(path), text
        if suf in (".json", ".canvas"):  # no texto do JSON as aspas vêm escapadas (password=\"x\") e o padrão não casa
            try:
                vals = []
                _strings(json.loads(text), vals)
                yield f"{path} (valores decodificados)", "\n".join(vals)
            except ValueError:
                pass
        if suf in UNESCAPE_SUFFIXES:
            un = unescape_all(text)
            if un != text:
                yield f"{path} (entidades decodificadas)", un


def scan_paths(paths):
    """[(arquivo, hit)] para todos os arquivos de texto/zip; diretórios são percorridos."""
    hits = []
    for p in paths:
        p = Path(p)
        files = sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]
        for f in files:
            if not f.exists():
                continue
            for label, text in _texts(f):
                hits += [(label, h) for h in scan(text)]
    return hits


def _file_hits(path):
    return [h for _label, text in _texts(path) for h in scan(text)]


def sanitize_file(path):
    """Último recurso antes de abortar uma publicação: limpa o arquivo no lugar → True se ficou limpo.

    JSON/canvas: redact_tree + redact_serialized (estrutura preservada). Texto (nota, CSV, fonte): redige o texto e,
    se ainda casar (ex. só na forma com entidades HTML decodificadas, `&quot;apiKey&quot;: …`), troca a linha
    acusada por «REDACTED». Arquivo binário ou zip: não mexe (a varredura final decide)."""
    path = Path(path)
    if path.suffix.lower() not in TEXT_SUFFIXES or not path.exists() or not _file_hits(path):
        return not (path.exists() and _file_hits(path))
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in (".json", ".canvas"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if data is not None:
            data, _n = redact_serialized(redact_tree(data))
            path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
            return not _file_hits(path)
    text = redact(text)
    path.write_text(text, encoding="utf-8", newline="\n")
    if _file_hits(path) and path.suffix.lower() in UNESCAPE_SUFFIXES:
        # o padrão só aparece com as entidades decodificadas (`&quot;apiKey&quot;: …`): grava a forma decodificada,
        # redigida — a numeração de linhas passa a ser a mesma da varredura (`&#10;` não desalinha mais)
        text = redact(unescape_all(text))
    for _ in range(3):
        path.write_text(text, encoding="utf-8", newline="\n")
        hits = _file_hits(path)
        if not hits:
            return True
        lines = text.split("\n")
        for h in hits:
            if 0 < h["line"] <= len(lines):
                lines[h["line"] - 1] = f"{REDACTED} (linha removida na publicação: possível segredo)"
        text = "\n".join(lines)
    path.write_text(text, encoding="utf-8", newline="\n")
    return not _file_hits(path)


def sanitize_paths(paths):
    """sanitize_file em cada arquivo com achado → (limpos, que não deu para limpar). Nunca levanta."""
    fixed, failed = [], []
    for p in paths:
        p = Path(p)
        try:
            if p.exists() and p.suffix.lower() in TEXT_SUFFIXES and _file_hits(p):
                (fixed if sanitize_file(p) else failed).append(p)
        except Exception:  # noqa: BLE001 — erro ao limpar = não limpo (quem chama remove o arquivo)
            failed.append(p)
    return fixed, failed


def assert_clean(paths, label):
    hits = scan_paths(paths)
    if hits:
        raise SecretLeakError(label, hits)
