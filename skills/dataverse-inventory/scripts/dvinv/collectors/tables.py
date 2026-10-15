import json
import re

from ..util import enum_value, label


def record_counts(ctx, names, collector):
    """RetrieveTotalRecordCount em lotes de 50; se um lote falhar, divide ao meio até isolar a tabela."""
    c, counts, failed = ctx.client, {}, []

    def run(chunk):
        try:
            d = c.get(f"RetrieveTotalRecordCount(EntityNames=@p1)?@p1={json.dumps(chunk)}")
            coll = d.get("EntityRecordCountCollection") or {}
            counts.update(dict(zip(coll.get("Keys", []), coll.get("Values", []))))
        except Exception as ex:  # noqa: BLE001
            if len(chunk) == 1:
                failed.append((chunk[0], getattr(ex, "message", str(ex))))
            else:
                run(chunk[:len(chunk) // 2])
                run(chunk[len(chunk) // 2:])

    for i in range(0, len(names), 50):
        run(names[i:i + 50])
    if failed:
        # uma lacuna só: tabelas de sistema virtuais/sem leitura não interessam uma a uma
        why = {}
        for name, msg in failed:
            k = ("virtual" if "virtual entity" in msg else "não legível" if "not valid for read" in msg
                 else "sem privilégio" if "CheckPrivilege" in msg else "outro erro")
            why.setdefault(k, []).append(name)
        ctx.gap(collector, f"contagem de registros indisponível em {len(failed)} tabela(s)",
                "; ".join(f"{k}: {', '.join(v[:12])}{' …' if len(v) > 12 else ''}" for k, v in why.items()))
    return counts


def collect_tables(ctx):
    c, cfg, scope = ctx.client, ctx.cfg, ctx.scope

    # Fase 1 (leve): todas as tabelas + só nome/flag-custom de cada coluna, para descobrir
    # tabelas nativas que receberam colunas customizadas.
    ents = c.entity_definitions(
        "MetadataId,LogicalName,SchemaName,EntitySetName,DisplayName,"
        "IsCustomEntity,IsManaged,OwnershipType,ObjectTypeCode,IsActivity,IsIntersect,TableType,PrimaryIdAttribute",
        "Attributes($select=LogicalName,IsCustomAttribute,IsManaged)", label="tabelas e colunas (fase 1)")

    # Índice leve de TODAS as tabelas (objecttypecode → nome): usado por storage/auditoria.
    ctx.data["_tables_index"] = [{"logical": e["LogicalName"], "otc": e.get("ObjectTypeCode"),
                                  "display": label(e.get("DisplayName")), "custom": e.get("IsCustomEntity"),
                                  "intersect": e.get("IsIntersect"), "table_type": e.get("TableType")} for e in ents]

    def custom_attrs(e):
        attrs = e.get("Attributes") or []
        if cfg.prefixes:
            return [a for a in attrs if any(a["LogicalName"].startswith(p) for p in cfg.prefixes)]
        return [a for a in attrs if a.get("IsCustomAttribute") and a.get("IsManaged") is False]

    selected = []
    for e in ents:
        if e.get("IsIntersect"):
            continue
        ln = e["LogicalName"]
        reason = scope.reason(ln, e.get("IsManaged"), e.get("MetadataId"), keyword_match=False)
        if reason == "unmanaged" and not e.get("IsCustomEntity"):
            reason = None  # nativa "unmanaged" sozinha não diz nada; vale pelas colunas abaixo
        ca = custom_attrs(e)
        if not reason and ca:
            reason = "custom-columns"
        if reason:
            selected.append((e, reason, len(ca)))

    # Fase 2 em paralelo: colunas (labels, tipo, obrigatoriedade) e chaves de cada tabela do escopo.
    def details(ln):
        attrs = c.get_all(
            f"EntityDefinitions(LogicalName='{ln}')/Attributes?$select=MetadataId,LogicalName,SchemaName,"
            "DisplayName,Description,AttributeType,AttributeTypeName,RequiredLevel,IsCustomAttribute,"
            "IsManaged,AttributeOf,IsAuditEnabled,SourceType")
        try:
            keys = c.get_all(f"EntityDefinitions(LogicalName='{ln}')/Keys?$select=LogicalName,KeyAttributes")
        except Exception as ex:  # noqa: BLE001
            ctx.gap("tables", f"chaves alternativas de {ln}", ex)
            keys = []
        return attrs, keys

    fetched = {it: (res, err) for it, res, err in
               c.parallel(details, [e["LogicalName"] for e, _, _ in selected], label="colunas das tabelas")}
    tables = []
    for e, reason, n_custom in selected:
        ln = e["LogicalName"]
        rec = {
            "id": e.get("MetadataId"),
            "logical": ln,
            "schema": e.get("SchemaName"),
            "entityset": e.get("EntitySetName"),
            "primary_id": e.get("PrimaryIdAttribute"),
            "display": label(e.get("DisplayName")),
            "custom": e.get("IsCustomEntity"),
            "managed": e.get("IsManaged"),
            "ownership": enum_value(e.get("OwnershipType")),
            "otc": e.get("ObjectTypeCode"),
            "activity": e.get("IsActivity"),
            "table_type": e.get("TableType"),
            "scope_reason": reason,
            "custom_column_count": n_custom,
            "column_count": len(e.get("Attributes") or []),
            "solutions": scope.solutions_of(e.get("MetadataId")),
            "columns": [],
            "keys": [],
            "record_count": None,
        }
        res, err = fetched[ln]
        try:
            if err:
                raise err
            attrs, keys = res
            for a in attrs:
                if a.get("AttributeOf"):  # colunas virtuais derivadas (ex. *name de lookup)
                    continue
                custom = bool(a.get("IsCustomAttribute"))
                if cfg.prefixes:
                    custom = custom and any(a["LogicalName"].startswith(p) for p in cfg.prefixes) or (
                        custom and a.get("IsManaged") is False)
                rec["columns"].append({
                    "id": a.get("MetadataId"),
                    "logical": a["LogicalName"],
                    "schema": a.get("SchemaName"),
                    "display": label(a.get("DisplayName")),
                    "description": label(a.get("Description")),
                    "type": (enum_value(a.get("AttributeTypeName")) or a.get("AttributeType") or "").removesuffix("Type") or None,
                    "required": enum_value(a.get("RequiredLevel")),
                    "custom": custom,
                    "managed": a.get("IsManaged"),
                    "audit": enum_value(a.get("IsAuditEnabled")),
                    "source_type": a.get("SourceType"),
                    "type_name": enum_value(a.get("AttributeTypeName")),
                    "solutions": scope.solutions_of(a.get("MetadataId")),
                })
            rec["columns"].sort(key=lambda x: (not x["custom"], x["logical"]))
            rec["keys"] = [{"name": k["LogicalName"], "columns": k.get("KeyAttributes")} for k in keys]
        except Exception as ex:  # noqa: BLE001
            ctx.gap("tables", f"colunas de {ln}", ex)
        tables.append(rec)

    if cfg.deep.get("record_counts") and tables:
        # tabelas virtuais não suportam RetrieveTotalRecordCount — e uma só derruba o lote inteiro
        names = [t["logical"] for t in tables if t.get("table_type") != "Virtual"]
        counts = record_counts(ctx, names, "tables")
        for t in tables:
            t["record_count"] = counts.get(t["logical"])

    tables.sort(key=lambda t: t["logical"])
    scope.tables = {t["logical"] for t in tables}
    ctx.stats["tables"] = {
        "org_total": len(ents),
        "scope": len(tables),
        "custom": sum(1 for t in tables if t["custom"]),
        "native_customized": sum(1 for t in tables if not t["custom"]),
        "columns_custom": sum(sum(1 for col in t["columns"] if col["custom"]) for t in tables),
    }
    try:
        collect_formulas(ctx, tables)
    except Exception as e:  # noqa: BLE001 — definição das fórmulas é complemento: falha vira lacuna
        ctx.gap("tables", "definição das colunas calculadas/rollup/fórmula", e)
    ctx.data["tables"] = tables


SOURCE_KIND = {1: "calculada", 2: "rollup", 3: "fórmula (Power Fx)"}
XAML_ATTR_RE = re.compile(r'Attribute="(\w+)"[^>]*?EntityName="(\w+)"')


def collect_formulas(ctx, tables):
    """Definição das colunas calculadas, rollup e de fórmula (Power Fx) e as colunas que cada uma usa.

    A definição (`FormulaDefinition`) só existe nos tipos derivados do metadado (Decimal, Integer, DateTime…):
    uma leitura por coluna, com o tipo certo, em paralelo. Calculada/rollup = XAML (pares Attribute/EntityName);
    fórmula = Power Fx (nomes de coluna da própria tabela)."""
    from ..util import field_refs
    c = ctx.client
    todo = [(t, col) for t in tables for col in t.get("columns") or [] if col.get("source_type") in SOURCE_KIND]
    if not todo:
        return

    def fetch(item):
        t, col = item
        cast = (col.get("type_name") or "").removesuffix("Type") + "AttributeMetadata"
        return c.get(f"EntityDefinitions(LogicalName='{t['logical']}')/Attributes(LogicalName='{col['logical']}')/"
                     f"Microsoft.Dynamics.CRM.{cast}?$select=FormulaDefinition").get("FormulaDefinition")

    for (t, col), fd, err in c.parallel(fetch, todo, label="definição das colunas calculadas/rollup/fórmula"):
        kind = SOURCE_KIND[col["source_type"]]
        if err is not None or not fd:
            col["formula"] = {"kind": kind, "refs": [], "error": "definição não lida" if err else "sem definição"}
            if err is not None:
                ctx.gap("tables", f"definição da coluna {kind} {t['logical']}.{col['logical']}", err)
            continue
        if col["source_type"] in (1, 2):
            refs = {f"{e}.{a}" for a, e in XAML_ATTR_RE.findall(fd)}
        else:
            refs = {f"{t['logical']}.{a}" for a in field_refs(fd, {x["logical"] for x in t.get("columns") or []})}
        refs.discard(f"{t['logical']}.{col['logical']}")
        col["formula"] = {"kind": kind, "refs": sorted(refs)}
    ctx.stats.setdefault("tables", {})["formula_columns"] = len(todo)


def collect_relationships(ctx):
    c, scope = ctx.client, ctx.scope
    custom_tables = {t["logical"] for t in ctx.data.get("tables", []) if t["custom"]}
    out = []
    one_n = c.get_all(
        "RelationshipDefinitions/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata?$select=MetadataId,"
        "SchemaName,ReferencedEntity,ReferencedAttribute,ReferencingEntity,ReferencingAttribute,"
        "IsCustomRelationship,IsManaged,CascadeConfiguration"
    )
    for r in one_n:
        a, b = r.get("ReferencedEntity"), r.get("ReferencingEntity")
        touches = a in scope.tables or b in scope.tables
        if not touches or not (r.get("IsCustomRelationship") or a in custom_tables or b in custom_tables):
            continue
        cascade = r.get("CascadeConfiguration") or {}
        out.append({
            "id": r.get("MetadataId"), "schema": r["SchemaName"], "kind": "1:N",
            "from": a, "to": b, "lookup": r.get("ReferencingAttribute"),
            "custom": r.get("IsCustomRelationship"), "managed": r.get("IsManaged"),
            "cascade_delete": cascade.get("Delete"), "cascade_assign": cascade.get("Assign"),
        })
    n_n = c.get_all(
        "RelationshipDefinitions/Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata?$select=MetadataId,"
        "SchemaName,Entity1LogicalName,Entity2LogicalName,IntersectEntityName,IsCustomRelationship,IsManaged"
    )
    for r in n_n:
        a, b = r.get("Entity1LogicalName"), r.get("Entity2LogicalName")
        touches = a in scope.tables or b in scope.tables
        if not touches or not (r.get("IsCustomRelationship") or a in custom_tables or b in custom_tables):
            continue
        out.append({
            "id": r.get("MetadataId"), "schema": r["SchemaName"], "kind": "N:N",
            "from": a, "to": b, "intersect": r.get("IntersectEntityName"),
            "custom": r.get("IsCustomRelationship"), "managed": r.get("IsManaged"),
        })
    ctx.stats["relationships"] = {"org_1n": len(one_n), "org_nn": len(n_n), "scope": len(out)}
    ctx.data["relationships"] = out


def collect_optionsets(ctx):
    c, scope = ctx.client, ctx.scope
    sets = c.get_all("GlobalOptionSetDefinitions?$select=MetadataId,Name,DisplayName,IsCustomOptionSet,IsManaged,OptionSetType")
    out = []
    for s in sets:
        reason = scope.reason(s["Name"], s.get("IsManaged"), s.get("MetadataId"))
        if not reason or not s.get("IsCustomOptionSet"):
            continue
        out.append({
            "id": s.get("MetadataId"), "name": s["Name"], "display": label(s.get("DisplayName")),
            "managed": s.get("IsManaged"), "type": s.get("OptionSetType"), "scope_reason": reason,
        })
    ctx.stats["optionsets"] = {"org_total": len(sets), "scope": len(out)}
    ctx.data["optionsets"] = sorted(out, key=lambda x: x["name"])


# Operações do provedor de dados virtual → coluna de plugin em entitydataprovider.
PROVIDER_OPS = [("Retrieve", "retrieveplugin"), ("RetrieveMultiple", "retrievemultipleplugin"),
                ("Create", "createplugin"), ("Update", "updateplugin"), ("Delete", "deleteplugin")]


def collect_virtual_tables(ctx):
    """Tabelas virtuais da org inteira (são poucas) + provedor de dados, plugins do provedor e fonte de dados.

    Da fonte de dados (registro da tabela `datasourcelogicalname` do provedor) lê só id e nome: a
    configuração pode ter URL e cabeçalhos de autenticação. Tabelas elásticas entram só na contagem.
    """
    c, scope = ctx.client, ctx.scope
    ents = c.get_all("EntityDefinitions?$select=MetadataId,LogicalName,DisplayName,DataProviderId,DataSourceId,"
                     "ExternalName,ExternalCollectionName,IsCustomEntity,IsManaged&$filter=TableType eq 'Virtual'")
    providers = []
    try:
        sel = ",".join(["entitydataproviderid", "name", "datasourcelogicalname", "ismanaged"] + [f for _, f in PROVIDER_OPS])
        providers = c.get_all(f"entitydataproviders?$select={sel}")
    except Exception as e:  # noqa: BLE001
        ctx.gap("virtual_tables", "provedores de dados (entitydataproviders)", e)
    by_id = {(p.get("entitydataproviderid") or "").lower(): p for p in providers}

    plugin_ids = {(p.get(f) or "").lower() for p in providers for _, f in PROVIDER_OPS if p.get(f)}
    ptypes = {}
    if plugin_ids:
        try:
            ptypes = c.get_many("plugintypes", "plugintypeid", sorted(plugin_ids), "typename,assemblyname")
        except Exception as e:  # noqa: BLE001
            ctx.gap("virtual_tables", "classes de plugin dos provedores", e)

    used_ds = {(by_id.get((e.get("DataProviderId") or "").lower()) or {}).get("datasourcelogicalname")
               for e in ents} - {None}
    ds_names = {}
    if used_ds:
        meta = {}
        try:
            for m in c.get_all("EntityDefinitions?$select=LogicalName,EntitySetName,PrimaryIdAttribute,PrimaryNameAttribute"):
                meta[m["LogicalName"]] = m
        except Exception as e:  # noqa: BLE001
            ctx.gap("virtual_tables", "metadados das tabelas de fonte de dados", e)
        for ds in sorted(used_ds):
            m = meta.get(ds)
            if not m or not m.get("EntitySetName"):
                continue
            sel = ",".join(x for x in (m["PrimaryIdAttribute"], m.get("PrimaryNameAttribute")) if x)
            try:
                for r in c.get_all(f"{m['EntitySetName']}?$select={sel}", max_rows=5000):
                    ds_names[(r.get(m["PrimaryIdAttribute"]) or "").lower()] = {
                        "table": ds, "name": r.get(m.get("PrimaryNameAttribute")) if m.get("PrimaryNameAttribute") else None}
            except Exception as e:  # noqa: BLE001
                ctx.gap("virtual_tables", f"registros da fonte de dados {ds} (só id e nome)", e)

    def provider_rec(p):
        ops = []
        for op, f in PROVIDER_OPS:
            pid = (p.get(f) or "").lower()
            if pid:
                t = ptypes.get(pid) or {}
                ops.append({"operation": op, "plugintype_id": pid, "typename": t.get("typename"),
                            "assembly": t.get("assemblyname")})
        return {"id": p["entitydataproviderid"], "name": p.get("name"), "datasource_table": p.get("datasourcelogicalname"),
                "managed": p.get("ismanaged"), "operations": ops}

    tables = []
    for e in sorted(ents, key=lambda x: x["LogicalName"]):
        pid = (e.get("DataProviderId") or "").lower()
        p = by_id.get(pid)
        ds = ds_names.get((e.get("DataSourceId") or "").lower()) or {}
        tables.append({
            "id": e["MetadataId"], "logical": e["LogicalName"], "display": label(e.get("DisplayName")),
            "custom": e.get("IsCustomEntity"), "managed": e.get("IsManaged"),
            "external_name": e.get("ExternalName"), "external_collection": e.get("ExternalCollectionName"),
            "provider_id": pid or None, "provider": p.get("name") if p else None,
            "datasource_id": (e.get("DataSourceId") or "").lower() or None,
            "datasource_table": (p or {}).get("datasourcelogicalname"), "datasource": ds.get("name"),
            "in_scope": e["LogicalName"] in scope.tables or bool(scope.reason(e["LogicalName"], e.get("IsManaged"),
                                                                              e["MetadataId"])),
        })
    used_p = {t["provider_id"] for t in tables}
    elastic = [x["logical"] for x in ctx.data.get("_tables_index") or [] if x.get("table_type") == "Elastic"]
    ctx.stats["virtual_tables"] = {"org_total": len(tables), "scope": sum(1 for t in tables if t["in_scope"]),
                                   "providers": len(providers), "providers_used": len(used_p - {None}),
                                   "elastic_org_total": len(elastic)}
    ctx.data["virtual_tables"] = {"tables": tables,
                                  "providers": [provider_rec(p) for p in providers
                                                if (p.get("entitydataproviderid") or "").lower() in used_p],
                                  "elastic": sorted(elastic)}
