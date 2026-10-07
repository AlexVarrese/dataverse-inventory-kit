import json

from ..util import enum_value, label


def collect_tables(ctx):
    c, cfg, scope = ctx.client, ctx.cfg, ctx.scope

    # Fase 1 (leve): todas as tabelas + só nome/flag-custom de cada coluna, para descobrir
    # tabelas nativas que receberam colunas customizadas.
    ents = c.get_all(
        "EntityDefinitions?$select=MetadataId,LogicalName,SchemaName,EntitySetName,DisplayName,"
        "IsCustomEntity,IsManaged,OwnershipType,ObjectTypeCode,IsActivity,IsIntersect,TableType,PrimaryIdAttribute"
        "&$expand=Attributes($select=LogicalName,IsCustomAttribute,IsManaged)"
    )

    # Índice leve de TODAS as tabelas (objecttypecode → nome): usado por storage/auditoria.
    ctx.data["_tables_index"] = [{"logical": e["LogicalName"], "otc": e.get("ObjectTypeCode"),
                                  "display": label(e.get("DisplayName")), "custom": e.get("IsCustomEntity"),
                                  "intersect": e.get("IsIntersect")} for e in ents]

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
        # Fase 2: detalhes de colunas (labels, tipo, obrigatoriedade) só para tabelas do escopo.
        try:
            attrs = c.get_all(
                f"EntityDefinitions(LogicalName='{ln}')/Attributes?$select=MetadataId,LogicalName,SchemaName,"
                "DisplayName,Description,AttributeType,AttributeTypeName,RequiredLevel,IsCustomAttribute,"
                "IsManaged,AttributeOf,IsAuditEnabled"
            )
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
                    "solutions": scope.solutions_of(a.get("MetadataId")),
                })
            rec["columns"].sort(key=lambda x: (not x["custom"], x["logical"]))
        except Exception as ex:  # noqa: BLE001
            ctx.gap("tables", f"colunas de {ln}", ex)
        try:
            keys = c.get_all(f"EntityDefinitions(LogicalName='{ln}')/Keys?$select=LogicalName,KeyAttributes")
            rec["keys"] = [{"name": k["LogicalName"], "columns": k.get("KeyAttributes")} for k in keys]
        except Exception as ex:  # noqa: BLE001
            ctx.gap("tables", f"chaves alternativas de {ln}", ex)
        tables.append(rec)

    if cfg.deep.get("record_counts") and tables:
        names = [t["logical"] for t in tables]
        counts = {}
        for i in range(0, len(names), 50):
            chunk = names[i:i + 50]
            try:
                d = c.get(f"RetrieveTotalRecordCount(EntityNames=@p1)?@p1={json.dumps(chunk)}")
                coll = d.get("EntityRecordCountCollection") or {}
                counts.update(dict(zip(coll.get("Keys", []), coll.get("Values", []))))
            except Exception as ex:  # noqa: BLE001
                ctx.gap("tables", f"RetrieveTotalRecordCount lote {i // 50}", ex)
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
    ctx.data["tables"] = tables


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
