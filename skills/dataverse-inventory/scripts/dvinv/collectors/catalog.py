"""Catálogo de 100% dos componentes do ambiente (camada 1 do inventário).

Não depende do escopo: lê TODAS as linhas de `solutioncomponent` (todas as soluções, inclusive as
gerenciadas da Microsoft) e dá nome a cada tipo e, quando possível, a cada componente.

- Nome do tipo: `solutioncomponentdefinitions` (tipos novos, ex. 10249 → connectionreference), opção
  global `componenttype` (tipos clássicos) e uma tabela fixa para os poucos códigos que nenhuma das
  duas fontes rotula (ex. 69 Custom Control Resource, 80 Model-driven App — conferidos via
  `msdyn_solutioncomponentsummary`, que não serve para leitura em massa: pagina devagar e estoura a
  URL com HTTP 414).
- Nome do componente: uma leitura em massa por tipo, na tabela de origem (metadados via
  EntityDefinitions/RelationshipDefinitions/GlobalOptionSetDefinitions; os demais pela tabela da
  definição ou pela tabela fixa CLASSIC). Só `id` + nome — nunca conteúdo. Tipos sem fonte conhecida
  ficam sem nome (limitação declarada, o componente continua no catálogo).

Formato compacto (snapshot `catalog.json`, sem indentação):
  types:   {ct: {"name", "source", "table"}}
  members: [[ct, objectid, solutionid, rootcomponentbehavior], ...]   (uma linha por solução)
  names:   {objectid: [ct, nome, pai]}   (pai = tabela, para colunas/formulários/views/chaves)
"""

# Tipos clássicos: (entity set, id, nome, coluna-pai opcional). Conferido contra a referência de
# solutioncomponent (Microsoft Learn, 2026-09) e o ambiente de homologação.
CLASSIC = {
    16: ("privileges", "privilegeid", "name", None),
    20: ("roles", "roleid", "name", None),
    22: ("displaystrings", "displaystringid", "displaystringkey", None),
    26: ("savedqueries", "savedqueryid", "name", "returnedtypecode"),
    29: ("workflows", "workflowid", "name", "primaryentity"),
    31: ("reports", "reportid", "name", None),
    36: ("templates", "templateid", "title", None),
    37: ("contracttemplates", "contracttemplateid", "name", None),
    38: ("kbarticletemplates", "kbarticletemplateid", "title", None),
    39: ("mailmergetemplates", "mailmergetemplateid", "name", None),
    44: ("duplicaterules", "duplicateruleid", "name", "baseentityname"),
    46: ("entitymaps", "entitymapid", "sourceentityname", "targetentityname"),
    50: ("ribboncustomizations", "ribboncustomizationid", "entity", None),
    59: ("savedqueryvisualizations", "savedqueryvisualizationid", "name", "primaryentitytypecode"),
    60: ("systemforms", "formid", "name", "objecttypecode"),
    61: ("webresourceset", "webresourceid", "name", None),
    62: ("sitemaps", "sitemapid", "sitemapname", None),
    63: ("connectionroles", "connectionroleid", "name", None),
    65: ("hierarchyrules", "hierarchyruleid", "name", "primaryentitylogicalname"),
    66: ("customcontrols", "customcontrolid", "name", None),
    68: ("customcontroldefaultconfigs", "customcontroldefaultconfigid", "primaryentitytypecode", None),
    69: ("customcontrolresources", "customcontrolresourceid", "name", None),
    70: ("fieldsecurityprofiles", "fieldsecurityprofileid", "name", None),
    71: ("fieldpermissions", "fieldpermissionid", "attributelogicalname", "entityname"),
    80: ("appmodules", "appmoduleid", "name", None),
    90: ("plugintypes", "plugintypeid", "typename", None),
    91: ("pluginassemblies", "pluginassemblyid", "name", None),
    92: ("sdkmessageprocessingsteps", "sdkmessageprocessingstepid", "name", None),
    93: ("sdkmessageprocessingstepimages", "sdkmessageprocessingstepimageid", "name", None),
    95: ("serviceendpoints", "serviceendpointid", "name", None),
    150: ("routingrules", "routingruleid", "name", None),
    152: ("slas", "slaid", "name", "objecttypecode"),
    154: ("convertrules", "convertruleid", "name", None),
    161: ("mobileofflineprofiles", "mobileofflineprofileid", "name", None),
    165: ("similarityrules", "similarityruleid", "name", None),
    172: ("channelaccessprofiles", "channelaccessprofileid", "name", None),
    181: ("entitydataproviders", "entitydataproviderid", "name", None),
    201: ("sdkmessages", "sdkmessageid", "name", None),
    202: ("sdkmessagefilters", "sdkmessagefilterid", "primaryobjecttypecode", None),
    208: ("importmaps", "importmapid", "name", None),
    300: ("canvasapps", "canvasappid", "name", None),
    371: ("connectors", "connectorid", "name", None),
    372: ("connectors", "connectorid", "name", None),
    380: ("environmentvariabledefinitions", "environmentvariabledefinitionid", "schemaname", None),
    381: ("environmentvariablevalues", "environmentvariablevalueid", "schemaname", None),
    400: ("msdyn_aitemplates", "msdyn_aitemplateid", "msdyn_uniquename", None),
    401: ("msdyn_aimodels", "msdyn_aimodelid", "msdyn_name", None),
    402: ("msdyn_aiconfigurations", "msdyn_aiconfigurationid", "msdyn_name", None),
    430: ("entityanalyticsconfigs", "entityanalyticsconfigid", "parententitylogicalname", None),
    431: ("attributeimageconfigs", "attributeimageconfigid", "attributelogicalname", "parententitylogicalname"),
    432: ("entityimageconfigs", "entityimageconfigid", "parententitylogicalname", None),
}

# Códigos sem rótulo na opção global nem em solutioncomponentdefinitions (rótulo do maker portal).
EXTRA_LABELS = {69: "Custom Control Resource", 80: "Model-driven App", 172: "Channel Access Profile",
                181: "Virtual Entity Data Provider", 183: "Virtual Entity Data Source"}
METADATA_TYPES = {1, 2, 9, 10, 14}  # nomes vêm das APIs de metadados, não de tabela


def _label(opt):
    lab = opt.get("Label") or {}
    return ((lab.get("UserLocalizedLabel") or {}).get("Label")
            or next((x.get("Label") for x in lab.get("LocalizedLabels") or [] if x.get("Label")), None))


def _type_names(ctx):
    c = ctx.client
    types = {}
    try:
        opts = c.get("GlobalOptionSetDefinitions(Name='componenttype')").get("Options") or []
        for o in opts:
            if _label(o):
                types[o["Value"]] = {"name": _label(o), "source": "opção global componenttype", "table": None}
    except Exception as e:  # noqa: BLE001
        ctx.gap("catalog", "rótulos da opção global componenttype", e)
    defs = []
    try:
        defs = c.get_all("solutioncomponentdefinitions?$select=solutioncomponenttype,name,primaryentityname")
        for d in defs:
            ct = d.get("solutioncomponenttype")
            if ct is None:
                continue
            cur = types.setdefault(ct, {"name": None, "source": None, "table": None})
            cur["table"] = d.get("primaryentityname") or cur["table"]
            if not cur["name"]:
                cur["name"] = d.get("name") or d.get("primaryentityname")
                cur["source"] = "solutioncomponentdefinition"
    except Exception as e:  # noqa: BLE001
        ctx.gap("catalog", "solutioncomponentdefinitions (nomes dos tipos novos)", e)
    for ct, name in EXTRA_LABELS.items():
        cur = types.setdefault(ct, {"name": None, "source": None, "table": None})
        if not cur["name"]:
            cur.update(name=name, source="tabela fixa do dvinv")
    return types


def _metadata_names(ctx, names, present):
    """Tabelas, colunas, chaves, relacionamentos e option sets — todos, de uma vez (≈10 s numa org grande)."""
    c = ctx.client
    if present & {1, 2, 14}:
        try:
            ents = c.get_all("EntityDefinitions?$select=MetadataId,LogicalName"
                             "&$expand=Attributes($select=MetadataId,LogicalName),Keys($select=MetadataId,LogicalName)")
            for e in ents:
                t = e["LogicalName"]
                names[e["MetadataId"].lower()] = [1, t, None]
                for a in e.get("Attributes") or []:
                    names[a["MetadataId"].lower()] = [2, f"{t}.{a['LogicalName']}", t]
                for k in e.get("Keys") or []:
                    names[k["MetadataId"].lower()] = [14, f"{t}.{k['LogicalName']}", t]
        except Exception as e:  # noqa: BLE001
            ctx.gap("catalog", "nomes de tabelas/colunas/chaves (EntityDefinitions)", e)
    if 10 in present:
        try:
            for r in c.get_all("RelationshipDefinitions?$select=MetadataId,SchemaName"):
                names[r["MetadataId"].lower()] = [10, r["SchemaName"], None]
        except Exception as e:  # noqa: BLE001
            ctx.gap("catalog", "nomes de relacionamentos (RelationshipDefinitions)", e)
    if 9 in present:
        try:
            for o in c.get_all("GlobalOptionSetDefinitions?$select=MetadataId,Name"):
                names[o["MetadataId"].lower()] = [9, o["Name"], None]
        except Exception as e:  # noqa: BLE001
            ctx.gap("catalog", "nomes de option sets (GlobalOptionSetDefinitions)", e)


def _entity_sources(ctx, types, present):
    """ct → (entity set, id, nome, pai) para os tipos novos, a partir da tabela da definição."""
    wanted = {types[ct]["table"]: ct for ct in present
              if ct not in CLASSIC and ct not in METADATA_TYPES and (types.get(ct) or {}).get("table")}
    if not wanted:
        return {}
    out = {}
    try:
        for e in ctx.client.get_all("EntityDefinitions?$select=LogicalName,EntitySetName,PrimaryIdAttribute,"
                                    "PrimaryNameAttribute"):
            ct = wanted.get(e["LogicalName"])
            if ct is not None and e.get("EntitySetName") and e.get("PrimaryIdAttribute"):
                out[ct] = (e["EntitySetName"], e["PrimaryIdAttribute"], e.get("PrimaryNameAttribute"), None)
    except Exception as e:  # noqa: BLE001
        ctx.gap("catalog", "tabelas de origem dos tipos novos (EntityDefinitions)", e)
    return out


def _step(msg):
    print(f"[dvinv]   catálogo: {msg}", flush=True)


def collect_catalog(ctx):
    import time
    c, cfg = ctx.client, ctx.cfg
    if not cfg.deep.get("catalog", True):
        return
    t0 = time.time()
    _step("1/4 nomes dos tipos de componente")
    types = _type_names(ctx)
    _step(f"1/4 {len(types)} tipos conhecidos · {time.time() - t0:.0f}s")
    _step("2/4 lendo todos os componentes de todas as soluções (solutioncomponent, páginas de 5.000)")
    t1 = time.time()

    members, present, ids = [], set(), {}
    for r in c.iter_all("solutioncomponents?$select=componenttype,objectid,_solutionid_value,rootcomponentbehavior"):
        ct, oid = r.get("componenttype"), (r.get("objectid") or "").lower()
        if ct is None or not oid:
            continue
        present.add(ct)
        ids.setdefault(oid, ct)
        members.append([ct, oid, (r.get("_solutionid_value") or "").lower(), r.get("rootcomponentbehavior")])

    _step(f"2/4 {len(members):,} linhas · {len(ids):,} componentes · {len(present)} tipos · {time.time() - t1:.0f}s"
          .replace(",", "."))
    names = {}
    if cfg.deep.get("catalog_names", True):
        t2 = time.time()
        _step("3/4 nomes de tabelas, colunas, chaves, relacionamentos e option sets (respostas grandes, sem paginação)")
        _metadata_names(ctx, names, present)
        _step(f"3/4 {len(names):,} nomes de metadados · {time.time() - t2:.0f}s".replace(",", "."))
        sources = {ct: CLASSIC[ct] for ct in present if ct in CLASSIC}
        sources.update(_entity_sources(ctx, types, present))
        cap = int(cfg.deep.get("catalog_max_rows_per_type") or 200_000)
        total = len(sources)
        _step(f"4/4 nomes dos demais componentes: {total} tipos, uma leitura por tipo (em paralelo)")

        def fetch(item):
            _ct, (es, idf, namef, parentf) = item
            return c.get_all(f"{es}?$select={','.join(x for x in (idf, namef, parentf) if x)}", max_rows=cap)

        for (ct, (es, idf, namef, parentf)), got, err in c.parallel(fetch, sorted(sources.items()),
                                                                     label="catálogo 4/4 nomes por tipo"):
            if err is not None:
                ctx.gap("catalog", f"nomes do tipo {ct} ({(types.get(ct) or {}).get('name') or es})", err)
                continue
            if len(got) >= cap:
                ctx.gap("catalog", f"nomes do tipo {ct} ({es})", f"truncado em {cap} linhas "
                        "(deep.catalog_max_rows_per_type)", kind="limitação")
            for g in got:
                gid = (g.get(idf) or "").lower()
                if gid:
                    names.setdefault(gid, [ct, g.get(namef) if namef else None, g.get(parentf) if parentf else None])
        sem_fonte = sorted(ct for ct in present if ct not in sources and ct not in METADATA_TYPES)
        if sem_fonte:
            ctx.gap("catalog", f"{len(sem_fonte)} tipo(s) sem fonte de nome conhecida — componentes listados sem nome",
                    ", ".join(str(x) for x in sem_fonte[:40]), kind="limitação")

    for ct in present:
        types.setdefault(ct, {"name": None, "source": None, "table": None})
    distinct = len(ids)
    named = sum(1 for oid in ids if (names.get(oid) or [None, None])[1])
    ctx.stats["catalog"] = {"components": distinct, "rows": len(members), "types": len(present),
                            "types_unnamed": sum(1 for ct in present if not types[ct]["name"]),
                            "components_named": named, "names_total": len(names)}
    ctx.data["catalog"] = {"types": {str(k): v for k, v in sorted(types.items())}, "members": members, "names": names}
