import json
import re
from pathlib import Path


def label(obj):
    """Extrai texto de um Label do metadata (UserLocalizedLabel → primeiro LocalizedLabel)."""
    if not obj:
        return None
    if isinstance(obj, str):
        return obj
    ul = obj.get("UserLocalizedLabel")
    if ul and ul.get("Label"):
        return ul["Label"]
    for ll in obj.get("LocalizedLabels") or []:
        if ll.get("Label"):
            return ll["Label"]
    return None


def fv(rec, fieldname):
    """Valor formatado (annotation) de um campo, se o servidor devolveu."""
    return rec.get(f"{fieldname}@OData.Community.Display.V1.FormattedValue")


def enum_value(obj):
    """Metadata devolve alguns enums como {"Value": "..."}; normaliza para o valor simples."""
    return obj.get("Value") if isinstance(obj, dict) else obj


def url_host(url):
    """Host de uma URL sem credencial embutida (`https://user:senha@host:443/x` → `host:443`)."""
    from urllib.parse import urlsplit
    try:
        netloc = urlsplit(url).netloc
    except ValueError:
        return ""
    return netloc.rsplit("@", 1)[-1]


def day(ts):
    return (ts or "")[:10] or None


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


def field_refs(text, known):
    """Nomes de coluna conhecidos (logical names, minúsculos) citados num texto qualquer.

    Busca por token inteiro: 'contoso_tier' não casa dentro de 'contoso_tier_old'.
    """
    if not text or not known:
        return []
    return sorted({t for t in (m.lower() for m in _TOKEN_RE.findall(text)) if t in known})
