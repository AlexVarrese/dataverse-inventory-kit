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


def field_tokens(text):
    """Tokens de um texto, normalizados uma vez para cruzar com várias tabelas.

    Mantém a mesma expressão regular e normalização de field_refs().
    """
    return {m.lower() for m in _TOKEN_RE.findall(text)} if text else set()


# Camadas de solução (extra `camadas`): rótulo de cada tipo de msdyn_componentlayer e leitura compatível com o formato
# até a 0.6, que só tinha tabelas ({tabela: [camadas]}).
LAYER_TYPE_LABEL = {"Entity": "Tabela", "SystemForm": "Formulário", "SavedQuery": "View", "Role": "Papel",
                    "WebResource": "Web resource", "SdkMessageProcessingStep": "Plugin step", "Workflow": "Processo",
                    "AppModule": "App", "SiteMap": "Site map", "OptionSet": "Option set",
                    "EntityRelationship": "Relacionamento", "Attribute": "Coluna"}


def layer_components(d):
    """→ lista de componentes com camadas ({type, kind, id, name, table, key, layers}); camadas de cima para baixo."""
    sl = d.get("solution_layers") or {}
    if isinstance(sl.get("components"), list):
        return sl["components"]
    return [{"type": "Entity", "kind": "table", "id": None, "name": t, "table": t, "key": t, "layers": ls}
            for t, ls in sorted(sl.items()) if isinstance(ls, list)]


def active_layer(comp):
    """→ (camada Active por cima de uma gerenciada?, propriedades alteradas nela — None quando não lidas)."""
    ls = comp.get("layers") or []
    if len(ls) < 2 or ls[0].get("solution") != "Active":
        return False, None
    return True, ls[0].get("changed")
