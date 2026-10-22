import base64
import io
import re
import zipfile

from collections import Counter

from .. import secrets
from .. import snapshot
from ..util import day, field_refs, fv, url_host

WR_TYPES = {1: "HTML", 2: "CSS", 3: "JScript", 4: "XML", 5: "PNG", 6: "JPG", 7: "GIF", 8: "XAP",
            9: "XSL", 10: "ICO", 11: "SVG", 12: "RESX", 13: "TS"}
TEXT_TYPES = {1, 2, 3, 4, 9, 11, 12}
# Conteúdo baixado por padrão: só HTML e JS (onde estão lógica, campos e chamadas). CSS, XML, XSL, SVG e RESX
# costumam ser grandes e pouco úteis para a análise — ligue em deep.webresource_content_types se precisar.
DEFAULT_CONTENT_TYPES = (1, 3)
# Bibliotecas de terceiros reconhecidas pelo nome: conteúdo não baixado (não há regra de negócio nelas).
DEFAULT_SKIP_NAMES = (r"(jquery|bootstrap|xrmservicet\w*kit|moment(\.min)?\.js|lodash|underscore|"
                      r"knockout|angular|\breact(-dom)?\b|\bvue(\.min)?\.js|select2|datatables|chart(\.min)?\.js|\bd3(\.min)?\.js|"
                      r"font-?awesome|polyfill|sweetalert|toastr|kendo|telerik|signalr|crypto-?js|pdfmake|xlsx(\.full)?(\.min)?\.js)")
# Análise estática de JS — padrões de risco/legado (portado e generalizado).
JS_CHECKS = {
    "xrm_page": re.compile(r"\bXrm\.Page\b"),                          # API obsoleta desde a v9
    "form_context": re.compile(r"\bformContext\b|\bexecutionContext\b"),
    "eval": re.compile(r"\beval\s*\("),
    "jquery": re.compile(r"\$\(|\bjQuery\s*\("),
    "xrm_service_toolkit": re.compile(r"XrmServiceToolkit"),
    "raw_http": re.compile(r"\$\.ajax|XMLHttpRequest|\bfetch\s*\("),
    "odata_2011": re.compile(r"XRMServices/2011|OrganizationData\.svc", re.I),  # endpoint SOAP/OData 2011 removido
    "sync_xhr": re.compile(r"\.open\(\s*[\"'][A-Z]+[\"']\s*,[^,)]+,\s*false\s*\)"),
}
WEBAPI_RE = re.compile(r"Xrm\.WebApi(?:\.online|\.offline)?\.(\w+)")
GETATTR_RE = re.compile(r"getAttribute\(\s*[\"']([\w]+)[\"']")
CONSOLE_RE = re.compile(r"console\.(?:log|warn|error|debug)")
TODO_RE = re.compile(r"(?://|/\*)\s*(?:TODO|FIXME|HACK)", re.I)
URL_RE = re.compile(r"https?://[^\s\"'\)<>]+")
THIRD_PARTY_RE = re.compile(r"jquery|json2|moment|lodash|underscore|toolkit|polyfill|\.min\.js$|bootstrap|chart\.?js|select2", re.I)


def analyze_js(name, text):
    hosts = sorted({url_host(u) for u in URL_RE.findall(text) if url_host(u)})
    out = {k: bool(rx.search(text)) for k, rx in JS_CHECKS.items()}
    out.update({
        "lines": text.count("\n") + 1,
        "third_party": bool(THIRD_PARTY_RE.search(name)),
        "webapi_methods": sorted(set(WEBAPI_RE.findall(text))),
        "attributes_read": sorted({a.lower() for a in GETATTR_RE.findall(text)}),
        "console_calls": len(CONSOLE_RE.findall(text)),
        "todo_count": len(TODO_RE.findall(text)),
        "external_hosts": [h for h in hosts if not h.endswith((".dynamics.com", ".microsoft.com", ".w3.org"))],
    })
    return out


FUNC_RE = re.compile(
    r"(?:function\s+([A-Za-z_$][\w$]*)\s*\(|([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s+)?function\b|"
    r"([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s*)?\([^)]*\)\s*=>)")


def collect_webresources(ctx):
    c, scope, cfg = ctx.client, ctx.scope, ctx.cfg
    allwr = c.get_all("webresourceset?$select=webresourceid,name,displayname,webresourcetype,ismanaged,modifiedon")
    # Nomes de TODOS os web resources da org: usado para achar referências quebradas em forms/ribbons.
    ctx.data["_webresource_names"] = sorted({w["name"].lower() for w in allwr})
    out = []
    for w in allwr:
        reason = scope.reason(w["name"], w.get("ismanaged"), w["webresourceid"], keyword_match=False)
        if not reason:
            continue
        t = w.get("webresourcetype")
        rec = {
            "id": w["webresourceid"], "name": w["name"], "display": w.get("displayname"),
            "type": WR_TYPES.get(t, str(t)), "managed": w.get("ismanaged"), "modified": day(w.get("modifiedon")),
            "scope_reason": reason, "solutions": scope.solutions_of(w["webresourceid"]),
            "modifiedon": w.get("modifiedon"), "content_source": None,
            "size": None, "functions": [], "secret_hits": [], "content": None, "js": None,
        }
        out.append((rec, t))
    content_types = {int(x) for x in (cfg.deep.get("webresource_content_types") or DEFAULT_CONTENT_TYPES)}
    skip = cfg.deep.get("webresource_skip_names", DEFAULT_SKIP_NAMES)
    if isinstance(skip, (list, tuple)):
        skip = "|".join(f"(?:{x})" for x in skip)
    skip_re = re.compile(skip or r"(?!x)x", re.I)  # [] ou "" desliga
    want, reused, skipped = [], 0, Counter()
    prev = {}
    if cfg.deep.get("webresource_content") and cfg.deep.get("webresource_reuse", True):
        prev_run, prev_list = snapshot.previous_file(cfg.raw_dir, "webresources")
        prev = {(r.get("id") or "").lower(): r for r in prev_list or [] if r.get("modifiedon") and r.get("content") is not None}
    for rec, t in out:
        if not cfg.deep.get("webresource_content") or t not in TEXT_TYPES:
            continue
        if t not in content_types:
            rec["content_source"] = f"não baixado: tipo {rec['type']} fora de deep.webresource_content_types"
            skipped["tipo"] += 1
        elif skip_re.search(rec["name"]):
            rec["content_source"] = "não baixado: biblioteca de terceiros pelo nome (deep.webresource_skip_names)"
            rec["js"] = {"third_party": True}
            skipped["terceiros"] += 1
        elif (p := prev.get(rec["id"].lower())) and p.get("modifiedon") == rec["modifiedon"]:
            # sem mudança no ambiente desde a extração anterior: reaproveita conteúdo (já redigido) e análise
            for k in ("size", "functions", "secret_hits", "content", "js"):
                rec[k] = p.get(k)
            rec["content_source"] = f"reaproveitado do snapshot {prev_run} (modifiedon igual)"
            reused += 1
        else:
            want.append((rec, t))
    contents = c.get_many("webresourceset", "webresourceid", [r["id"] for r, _t in want], "content",
                          batch=int(cfg.deep.get("webresource_batch") or 10), label="conteúdo de web resources") \
        if want else {}
    # Análise local do conteúdo (segredos, redação, funções, JS): ~2 s por MB. Com milhares de arquivos são
    # dezenas de minutos de CPU — antes sem nenhuma mensagem depois do download chegar a 100%.
    max_bytes = int(cfg.deep.get("webresource_max_bytes") or 1_000_000)
    import time as _time
    t0, last, total, done_bytes, big = _time.time(), _time.time(), len(want), 0, 0
    for n, (rec, t) in enumerate(want, 1):
        try:
            d = contents.get(rec["id"].lower())
            if d is None:
                raise RuntimeError("não retornado pela API")
            raw = base64.b64decode(d.get("content") or "")
            done_bytes += len(raw)
            text = raw.decode("utf-8-sig", "replace")
            rec["size"] = len(raw)
            rec["secret_hits"] = secrets.scan(text)
            if len(raw) > max_bytes:
                # grande demais (bundle/minificado): segredos verificados, mas conteúdo e análise não guardados
                rec["content_source"] = f"baixado; conteúdo não guardado (> {max_bytes // 1000} KB, deep.webresource_max_bytes)"
                rec["js"] = {"third_party": None, "lines": text.count("\n") + 1, "too_large": True} if t == 3 else None
                big += 1
            else:
                if t == 3:
                    names = {next(g for g in m.groups() if g) for m in FUNC_RE.finditer(text)}
                    rec["functions"] = sorted(names)
                    rec["js"] = analyze_js(rec["name"], text)
                # sem achado na varredura, a redação não muda nada: evita a segunda passada (mesmo resultado)
                rec["content"] = secrets.redact(text) if rec["secret_hits"] else text
                rec["content_source"] = "baixado"
        except Exception as e:  # noqa: BLE001
            ctx.gap("webresources", f"conteúdo de {rec['name']}", e)
        if total >= 20 and (_time.time() - last >= 30 or n == total):
            last = _time.time()
            el = last - t0
            print(f"[dvinv]   analisando conteúdo dos web resources: {n}/{total} ({100 * n // total}%) · "
                  f"{done_bytes / 1e6:.0f} MB · {int(el // 60)}m{int(el % 60):02d}s decorrido"
                  + (f" · ~{int(el / n * (total - n) // 60)}m restante" if n < total else ""), flush=True)
    if big:
        ctx.gap("webresources", f"conteúdo de {big} web resource(s) acima de {max_bytes // 1000} KB não guardado "
                "(segredos verificados)", "deep.webresource_max_bytes", kind="limitação")
    if skipped:
        ctx.gap("webresources", f"conteúdo não baixado de {sum(skipped.values())} web resource(s) "
                f"({', '.join(f'{k}: {n}' for k, n in sorted(skipped.items()))})",
                "configuração deep.webresource_content_types / deep.webresource_skip_names", kind="limitação")
    ctx.stats["webresources"] = {"org_total": len(allwr), "scope": len(out), "content_downloaded": len(want),
                                 "content_reused": reused, "content_skipped": sum(skipped.values())}
    ctx.data["webresources"] = sorted((rec for rec, _ in out), key=lambda x: x["name"])


LIB_RE = re.compile(r'<Library\b[^>]*\bname="([^"]+)"', re.I)
EVENT_RE = re.compile(r"<event\b([^>]*)>(.*?)</event>", re.I | re.S)
HANDLER_RE = re.compile(r"<Handler\b([^>]*)/?>", re.I)
ATTR = lambda name: re.compile(rf'\b{name}="([^"]*)"', re.I)  # noqa: E731
A_NAME, A_ATTR, A_FUNC, A_LIB, A_ENABLED = ATTR("name"), ATTR("attribute"), ATTR("functionName"), ATTR("libraryName"), ATTR("enabled")
CONTROL_RE = re.compile(r'<control\b[^>]*\bdatafieldname="([^"]+)"', re.I)


def parse_formxml(xml):
    # o formxml às vezes grava a biblioteca como "$webresource:<nome>"
    libs = sorted({lib.split(":", 1)[1] if lib.lower().startswith("$webresource:") else lib for lib in LIB_RE.findall(xml)})
    handlers = []
    for m in EVENT_RE.finditer(xml):
        head, body = m.group(1), m.group(2)
        ev = (A_NAME.search(head) or [None, None])[1]
        field = (A_ATTR.search(head) or [None, None])[1]
        for h in HANDLER_RE.finditer(body):
            ha = h.group(1)
            fn = A_FUNC.search(ha)
            lib = A_LIB.search(ha)
            lib = lib.group(1) if lib else None
            if lib and lib.lower().startswith("$webresource:"):
                lib = lib.split(":", 1)[1]
            en = A_ENABLED.search(ha)
            if fn or lib:
                handlers.append({
                    "event": ev, "field": field.lower() if field else None,
                    "library": lib, "function": fn.group(1) if fn else None,
                    "enabled": (en.group(1).lower() != "false") if en else True,
                })
    fields = sorted({f.lower() for f in CONTROL_RE.findall(xml)})
    return libs, handlers, fields


# Controles de código (PCF) no formxml: <control uniqueid="{X}" datafieldname="campo"/> e
# <controlDescription forControl="{X}"><customControl name="prefixo_Namespace.Controle" formFactor="0">
#   <parameters><value type="...">campo</value><outro static="true">literal</outro></parameters>
BUILTIN_CONTROLS = ("mscrmcontrols.",)  # controles nativos da plataforma, não são PCF do cliente
CTRL_RE = re.compile(r"<control\b([^>]*)>", re.I)
CDESC_RE = re.compile(r'<controlDescription\b[^>]*\bforControl="([^"]+)"[^>]*>(.*?)</controlDescription>', re.I | re.S)
CUSTOM_RE = re.compile(r"<customControl\b([^>]*?)(?:/>|>(.*?)</customControl>)", re.I | re.S)
PARAMS_RE = re.compile(r"<parameters>(.*?)</parameters>", re.I | re.S)
PARAM_RE = re.compile(r"<([A-Za-z_][\w.]*)\b([^>]*)>([^<]*)</\1>")
IDENT_RE = re.compile(r"[A-Za-z_]\w*")
A_UNIQ, A_DFN, A_ID, A_FF = ATTR("uniqueid"), ATTR("datafieldname"), ATTR("id"), ATTR("formFactor")


def parse_form_pcf(xml):
    """PCF usados no formulário → [{control, field, control_id, form_factors, bound}] (sem controles nativos).

    `field` é a coluna do controle (None em subgrid/dataset); `bound` são colunas passadas como
    parâmetro não estático (o PCF lê/grava esses campos além do principal)."""
    ctrl = {}
    for m in CTRL_RE.finditer(xml or ""):
        a = m.group(1)
        u = A_UNIQ.search(a)
        if u:
            f, i = A_DFN.search(a), A_ID.search(a)
            ctrl[u.group(1).lower()] = (i.group(1) if i else None, f.group(1).lower() if f else None)
    found = {}
    for m in CDESC_RE.finditer(xml or ""):
        cid, field = ctrl.get(m.group(1).lower(), (None, None))
        for c in CUSTOM_RE.finditer(m.group(2)):
            nm = A_NAME.search(c.group(1))
            name = nm.group(1) if nm else None
            if not name or name.lower().startswith(BUILTIN_CONTROLS):
                continue
            bound = set()
            for pm in PARAMS_RE.finditer(c.group(2) or ""):
                for prm in PARAM_RE.finditer(pm.group(1)):
                    val = prm.group(3).strip()
                    if 'static="true"' not in prm.group(2).lower() and IDENT_RE.fullmatch(val):
                        bound.add(val.lower())
            rec = found.setdefault((name, field, cid), {"control": name, "field": field, "control_id": cid,
                                                         "form_factors": [], "bound": set()})
            ff = A_FF.search(c.group(1))
            if ff and ff.group(1) not in rec["form_factors"]:
                rec["form_factors"].append(ff.group(1))
            rec["bound"] |= bound - ({field} if field else set())
    return [dict(r, bound=sorted(r["bound"]), form_factors=sorted(r["form_factors"])) for r in found.values()]


def collect_forms(ctx):
    c, scope, cfg = ctx.client, ctx.scope, ctx.cfg
    # systemform não tem modifiedon (a data é publishedon); fallback sem data para versões que diferirem.
    forms = c.get_first_ok(
        "systemforms?$select=formid,name,type,objecttypecode,formactivationstate,ismanaged,isdefault,publishedon",
        "systemforms?$select=formid,name,type,objecttypecode,formactivationstate,ismanaged,isdefault")
    out = []
    for f in forms:
        ent = f.get("objecttypecode")
        if ent not in scope.tables and not scope.reason(f.get("name"), f.get("ismanaged"), f["formid"], keyword_match=False):
            continue
        rec = {
            "id": f["formid"], "name": f.get("name"), "entity": ent,
            "type": fv(f, "type") or str(f.get("type")), "type_code": f.get("type"),
            "active": f.get("formactivationstate") == 1,
            "managed": f.get("ismanaged"), "default": f.get("isdefault"), "modified": day(f.get("publishedon")),
            "solutions": scope.solutions_of(f["formid"]), "libraries": [], "handlers": [], "fields": [],
            "pcf": [],
        }
        out.append(rec)
    # formxml de forms com tabela (dashboards não têm eventos de campo), em lotes. Forms inativos só
    # entram com deep.field_usage: contam como uso "fraco" de coluna (não como biblioteca/handler ativo).
    if cfg.deep.get("form_events"):
        need = [r for r in out if r["entity"] and r["entity"] != "none" and (r["active"] or cfg.deep.get("field_usage"))]
        xmls = c.get_many("systemforms", "formid", [r["id"] for r in need], "formxml", label="formxml dos formulários")
        for rec in need:
            d = xmls.get(rec["id"].lower())
            if d is None:
                ctx.gap("forms", f"formxml de {rec['entity']}/{rec['name']}", "não retornado pela API")
                continue
            libs, handlers, fields = parse_formxml(d.get("formxml") or "")
            rec["fields"], rec["xml_read"] = fields, True
            if rec["active"]:
                rec["libraries"], rec["handlers"] = libs, handlers
                rec["pcf"] = parse_form_pcf(d.get("formxml") or "")
    ctx.stats["forms"] = {"org_total": len(forms), "scope": len(out)}
    ctx.data["forms"] = sorted(out, key=lambda x: (x["entity"] or "", x["name"] or ""))


def collect_views(ctx):
    c, scope = ctx.client, ctx.scope
    views = c.get_all(
        "savedqueries?$select=savedqueryid,name,returnedtypecode,querytype,isdefault,statecode,ismanaged,modifiedon")
    out = []
    for v in views:
        ent = v.get("returnedtypecode")
        if ent not in scope.tables:
            continue
        out.append({
            "id": v["savedqueryid"], "name": v.get("name"), "entity": ent,
            "querytype": fv(v, "querytype") or str(v.get("querytype")), "default": v.get("isdefault"),
            "active": v.get("statecode") == 0, "managed": v.get("ismanaged"), "modified": day(v.get("modifiedon")),
        })
    if ctx.cfg.deep.get("field_usage"):
        # em lotes: orgs grandes têm milhares de views (uma chamada por view levava 15+ min)
        xmls = c.get_many("savedqueries", "savedqueryid", [x["id"] for x in out], "fetchxml,layoutxml", batch=20,
                          label="definição das views")
        for x in out:
            d = xmls.get(x["id"].lower())
            if d is None:
                ctx.gap("views", f"fetchxml da view {x['name']}", "não retornado pela API")
                continue
            xml = (d.get("fetchxml") or "") + (d.get("layoutxml") or "")
            x["columns"] = sorted({m.lower() for m in re.findall(r'\bname="([A-Za-z0-9_]+)"', xml)})
    ctx.stats["views"] = {"org_total": len(views), "scope": len(out)}
    ctx.data["views"] = sorted(out, key=lambda x: (x["entity"], x["name"] or ""))


BUTTON_RE = re.compile(r"<Button\b([^>]*)/?>", re.I)
CMD_RE = re.compile(r'<CommandDefinition\b[^>]*\bId="([^"]+)"[^>]*>(.*?)</CommandDefinition>', re.I | re.S)
JSF_RE = re.compile(r"<JavaScriptFunction\b([^>]*)>", re.I)
A_ID, A_COMMAND, A_LABEL, A_LIBRARY, A_FNAME = ATTR("Id"), ATTR("Command"), ATTR("LabelText"), ATTR("Library"), ATTR("FunctionName")


def collect_ribbons(ctx):
    """Botões clássicos (RibbonDiffXml) cujo comando chama um web resource. Só com deep.ribbons."""
    c, scope, cfg = ctx.client, ctx.scope, ctx.cfg
    if not cfg.deep.get("ribbons"):
        return
    out = []

    def unzip(d):
        raw = base64.b64decode(d["CompressedEntityXml"])
        return zipfile.ZipFile(io.BytesIO(raw)).read("RibbonXml.xml").decode("utf-8")

    ents = sorted(t for t in scope.tables if not scope.is_microsoft(t))  # tabela da Microsoft: ribbon do produto
    for ent, d, err in c.get_batch(ents, lambda ent: (
            f"RetrieveEntityRibbon(EntityName=@p1,RibbonLocationFilter=@p2)?@p1='{ent}'"
            f"&@p2=Microsoft.Dynamics.CRM.RibbonLocationFilters'All'"), label="ribbons", size=10):
        try:
            xml = unzip(d) if err is None else None
        except Exception as e:  # noqa: BLE001 — resposta sem o zip esperado
            err = e
        if err:
            ctx.gap("ribbons", f"ribbon de {ent}", err)
            continue
        commands = {}
        for m in CMD_RE.finditer(xml):
            fns = []
            for j in JSF_RE.finditer(m.group(2)):
                lib = (A_LIBRARY.search(j.group(1)) or [None, None])[1] or ""
                fn = (A_FNAME.search(j.group(1)) or [None, None])[1]
                if lib.startswith("$webresource:"):
                    fns.append({"library": lib.split(":", 1)[1], "function": fn})
            if fns:
                commands[m.group(1)] = fns
        for b in BUTTON_RE.finditer(xml):
            attrs = b.group(1)
            cmd = (A_COMMAND.search(attrs) or [None, None])[1]
            if cmd not in commands:
                continue
            calls = commands[cmd]
            libs = [x["library"].lower() for x in calls]
            # Só botões que chamam bibliotecas do cliente (nativos Microsoft ficam de fora).
            if not any(scope.reason(lib, None, None) for lib in libs):
                continue
            out.append({
                "entity": ent, "button": (A_ID.search(attrs) or [None, None])[1],
                "label": (A_LABEL.search(attrs) or [None, None])[1], "command": cmd, "calls": calls,
            })
    ctx.stats["ribbons"] = {"scope": len(ents), "microsoft_skipped": len(scope.tables) - len(ents), "buttons": len(out)}
    ctx.data["ribbons"] = out
