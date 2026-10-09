"""Componentes de código do Power Apps (PCF): `customcontrols` + manifesto + onde são usados.

- Lista todos os controles da org; entram no escopo os do critério normal (prefixo, palavra-chave,
  solução, não gerenciado) e os de terceiros usados em formulário de tabela do escopo.
- O manifesto (XML de customização, não código) dá namespace, versão, tipo (standard/virtual),
  propriedades, uso de Web API e domínios externos declarados (`external-service-usage`).
- Uso: vínculos coluna → controle lidos do formxml pelo coletor `forms` (só formulários ativos).
  PCF configurado como controle padrão da coluna/tabela, em view ou fora de formulário NÃO aparece
  aqui (a Web API não expõe essas customizações de forma consultável) — lacuna declarada.
"""

import re
from collections import defaultdict

from ..util import day

MANIFEST_CONTROL_RE = re.compile(r"<control\b([^>]*)>", re.I)
PROPERTY_RE = re.compile(r"<property\b([^>]*?)/?>", re.I)
DATASET_RE = re.compile(r"<data-set\b([^>]*?)/?>", re.I)
DOMAIN_RE = re.compile(r"<domain>\s*([^<\s]+)\s*</domain>", re.I)
FEATURE_RE = re.compile(r"<uses-feature\b[^>]*\bname=\"([^\"]+)\"", re.I)
EXT_USAGE_RE = re.compile(r"<external-service-usage\b[^>]*\benabled=\"(true|false)\"", re.I)
CODE_RE = re.compile(r"<code\b[^>]*\bpath=\"([^\"]+)\"", re.I)


def _attr(text, name):
    m = re.search(rf'\b{name}="([^"]*)"', text or "", re.I)
    return m.group(1) if m else None


def parse_manifest(xml):
    """Resumo do ControlManifest.Input.xml gravado em customcontrol.manifest."""
    if not xml:
        return {}
    head = MANIFEST_CONTROL_RE.search(xml)
    h = head.group(1) if head else ""
    props = [{"name": _attr(p, "name"), "type": _attr(p, "of-type") or _attr(p, "of-type-group"),
              "usage": _attr(p, "usage"), "required": (_attr(p, "required") or "").lower() == "true"}
             for p in (m.group(1) for m in PROPERTY_RE.finditer(xml))]
    ext = EXT_USAGE_RE.search(xml)
    return {
        "namespace": _attr(h, "namespace"), "constructor": _attr(h, "constructor"),
        "manifest_version": _attr(h, "version"), "control_type": _attr(h, "control-type") or "standard",
        "properties": props, "datasets": [_attr(m.group(1), "name") for m in DATASET_RE.finditer(xml)],
        "external_service_usage": bool(ext and ext.group(1).lower() == "true"),
        "external_domains": sorted({d.lower() for d in DOMAIN_RE.findall(xml)}),
        "features": sorted(set(FEATURE_RE.findall(xml))),
        "code": CODE_RE.findall(xml),
    }


def collect_pcf(ctx):
    c, scope = ctx.client, ctx.scope
    rows = c.get_first_ok(
        "customcontrols?$select=customcontrolid,name,compatibledatatypes,version,ismanaged,modifiedon",
        "customcontrols?$select=customcontrolid,name,ismanaged")
    usage = defaultdict(list)
    for f in ctx.data.get("forms") or []:
        for p in f.get("pcf") or []:
            usage[p["control"].lower()].append({"form": f["name"], "form_id": f["id"], "entity": f["entity"],
                                                "field": p.get("field"), "bound": p.get("bound") or [],
                                                "form_factors": p.get("form_factors") or []})
    out = []
    for r in rows:
        name = r.get("name") or ""
        used = usage.get(name.lower(), [])
        reason = scope.reason(name, r.get("ismanaged"), r["customcontrolid"]) or (
            "usado em formulário do escopo" if any(u["entity"] in scope.tables for u in used) else None)
        if not reason:
            continue
        out.append({
            "id": r["customcontrolid"], "name": name, "version": r.get("version"), "managed": r.get("ismanaged"),
            "data_types": r.get("compatibledatatypes"), "modified": day(r.get("modifiedon")),
            "scope_reason": reason, "solutions": scope.solutions_of(r["customcontrolid"]),
            "usage": used, "manifest": None,
        })
    names = {x.lower() for x in usage} - {(r.get("name") or "").lower() for r in rows}
    if names:  # usado no formxml mas não listado (ex. sem privilégio de leitura): registra, não inventa
        ctx.gap("pcf", f"{len(names)} controle(s) citados em formulário sem registro em customcontrols",
                ", ".join(sorted(names)[:10]))
    if out:
        try:
            mans = c.get_many("customcontrols", "customcontrolid", [x["id"] for x in out], "manifest", batch=10)
            for x in out:
                m = mans.get(x["id"].lower())
                if m is None:
                    ctx.gap("pcf", f"manifesto de {x['name']}", "não retornado pela API")
                    continue
                x["manifest"] = parse_manifest(m.get("manifest") or "")
        except Exception as e:  # noqa: BLE001
            ctx.gap("pcf", "manifestos dos controles", e)
    ctx.gap("pcf", "PCF fora de formulário (controle padrão da coluna/tabela, views)",
            "não exposto de forma consultável pela Web API — só vínculos em formulários ativos são listados",
            kind="limitação")
    ctx.stats["pcf"] = {"org_total": len(rows), "scope": len(out), "used_in_forms": sum(1 for x in out if x["usage"])}
    ctx.data["pcf"] = sorted(out, key=lambda x: x["name"].lower())
