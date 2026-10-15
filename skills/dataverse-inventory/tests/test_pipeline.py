"""Teste ponta a ponta offline: org fictícia 'Contoso' servida por um cliente HTTP falso.

Exercita o código real de paginação, coletores, achados, render (notas, Bases, Canvas) e diff.
Uso: python3 tests/test_pipeline.py
"""

import base64
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.parse
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import yaml  # noqa: E402

from dvinv import config as config_mod  # noqa: E402
from dvinv.client import Client, DataverseError  # noqa: E402
from dvinv.collectors import Context, run  # noqa: E402
from dvinv.diff import compare  # noqa: E402
from dvinv.render.obsidian import MANUAL, render  # noqa: E402
from dvinv.scope import Scope  # noqa: E402
from dvinv.snapshot import sha256_file  # noqa: E402

FV = "@OData.Community.Display.V1.FormattedValue"
LK = "@Microsoft.Dynamics.CRM.lookuplogicalname"
SECRET = "sv=2019&sig=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd"
G = lambda n: f"00000000-0000-0000-0000-{n:012d}"  # noqa: E731

JS_ACCOUNT = f"""var Contoso = Contoso || {{}};
Contoso.Account = {{
  onLoad: function (ctx) {{ fetch("https://prod-01.logic.azure.com/workflows/x?{SECRET}"); Xrm.Page.getAttribute("contoso_tier"); }},
  helper: function () {{ Xrm.WebApi.retrieveMultipleRecords("contoso_project", "?$top=1");
    Xrm.WebApi.online.execute({{ getMetadata: function () {{ return {{ operationName: "contoso_RecalcProject" }}; }} }}); }}
}};
function standalone() {{}}
"""
FORMXML = """<form><formLibraries><Library name="contoso_/js/account.js" libraryUniqueId="{1}"/>
<Library name="contoso_/js/missing.js" libraryUniqueId="{2}"/>
<Library name="$webresource:contoso_/js/account.js" libraryUniqueId="{3}"/></formLibraries>
<events><event name="onload" application="false" active="true"><Handlers>
<Handler functionName="Contoso.Account.onLoad" libraryName="contoso_/js/account.js" enabled="true"/></Handlers></event>
<event name="onchange" application="false" active="true" attribute="contoso_tier"><Handlers>
<Handler functionName="Contoso.Account.onTierChange" libraryName="contoso_/js/account.js" enabled="true"/></Handlers></event>
</events><tabs><tab><columns><column><sections><section><rows><row>
<cell><control id="name" datafieldname="name"/></cell><cell><control id="t" datafieldname="contoso_tier" uniqueid="{u-tier}"/></cell>
</row></rows></section></sections></column></columns></tab></tabs>
<controlDescriptions><controlDescription forControl="{u-tier}">
<customControl formFactor="0" name="MscrmControls.FieldControls.OptionSetControl"><parameters><value type="OptionSet">contoso_tier</value></parameters></customControl>
<customControl formFactor="2" name="contoso_Contoso.TierPicker"><parameters><value type="OptionSet">contoso_tier</value>
<amount type="Currency">contoso_revenue</amount><theme static="true" type="Enum">dark</theme></parameters></customControl>
</controlDescription></controlDescriptions></form>"""
PCF_MANIFEST = """<?xml version="1.0" encoding="utf-8"?><manifest><control namespace="Contoso" constructor="TierPicker"
 version="1.0.3" display-name-key="Tier" control-type="standard"><external-service-usage enabled="true">
<domain>api.rating.contoso.example</domain></external-service-usage>
<property name="value" of-type="OptionSet" usage="bound" required="true"/><property name="amount" of-type="Currency" usage="bound"/>
<property name="theme" of-type="Enum" usage="input"/><resources><code path="index.ts" order="1"/></resources>
<feature-usage><uses-feature name="WebAPI" required="true"/></feature-usage></control></manifest>"""
RIBBON = """<RibbonDefinitions><Button Id="contoso.account.Sync" Command="contoso.cmd.Sync" LabelText="Sincronizar ERP"/>
<Button Id="Mscrm.Save" Command="Mscrm.SavePrimary"/>
<CommandDefinition Id="contoso.cmd.Sync"><Actions><JavaScriptFunction Library="$webresource:contoso_/js/erp.js" FunctionName="sync"/></Actions></CommandDefinition>
<CommandDefinition Id="Mscrm.SavePrimary"><Actions><JavaScriptFunction Library="/_static/x.js" FunctionName="save"/></Actions></CommandDefinition>
</RibbonDefinitions>"""
FLOW = {"properties": {"connectionReferences": {
    "shared_commondataserviceforapps": {"api": {"name": "shared_commondataserviceforapps"}},
    "shared_sql": {"api": {"name": "shared_sql"}, "connection": {"connectionReferenceLogicalName": "contoso_sql"}}},
    "definition": {"triggers": {"Quando_conta_alterada": {"type": "OpenApiConnectionWebhook",
                   "inputs": {"host": {"operationId": "SubscribeWebhookTrigger"}, "parameters": {"subscriptionRequest/entityname": "account"}}}},
                   "actions": {"Lista": {"type": "OpenApiConnection", "inputs": {"parameters": {"entityName": "contoso_projects"}}},
                               "Cond": {"type": "If", "actions": {"Chama_ERP": {"type": "Http", "inputs": {"uri": "https://erp.contoso.com/api/orders?code=ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"}}},
                                        "else": {"actions": {"Filho": {"type": "Workflow", "inputs": {"host": {"workflowReferenceName": "00000000-0000-0000-0000-000000000052"}}}}}},
                               "Url": {"type": "Compose", "inputs": "@parameters('contoso_ErpUrl (contoso_ErpUrl)')"},
                               "Recalc": {"type": "OpenApiConnection", "inputs": {"host": {"operationId": "PerformUnboundAction"},
                                          "parameters": {"actionName": "contoso_RecalcProject"}}}}}}}


def zipped_ribbon():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("RibbonXml.xml", RIBBON)
    return base64.b64encode(buf.getvalue()).decode()


def attr(ln, custom, managed=True, typ="String"):
    return {"MetadataId": G(abs(hash(ln)) % 10**9), "LogicalName": ln, "SchemaName": ln.title(), "IsCustomAttribute": custom,
            "IsManaged": managed, "DisplayName": {"UserLocalizedLabel": {"Label": ln}}, "AttributeTypeName": {"Value": typ + "Type"},
            "RequiredLevel": {"Value": "None"}, "AttributeOf": None, "IsAuditEnabled": {"Value": True}, "Description": None}


ENTITIES = [
    {"MetadataId": G(1), "LogicalName": "account", "SchemaName": "Account", "EntitySetName": "accounts", "IsCustomEntity": False,
     "IsManaged": True, "OwnershipType": "UserOwned", "ObjectTypeCode": 1, "DisplayName": {"UserLocalizedLabel": {"Label": "Conta"}},
     "PrimaryIdAttribute": "accountid",
     "Attributes": [attr("name", False), attr("contoso_tier", True, False, "Picklist"), attr("contoso_legacy", True, False),
                    attr("contoso_revenue", True, False, "Money"), attr("contoso_weird", True, False)]},
    {"MetadataId": G(2), "LogicalName": "contoso_project", "SchemaName": "contoso_Project", "EntitySetName": "contoso_projects",
     "IsCustomEntity": True, "IsManaged": False, "OwnershipType": "UserOwned", "ObjectTypeCode": 10001,
     "PrimaryIdAttribute": "contoso_projectid",
     "DisplayName": {"UserLocalizedLabel": {"Label": "Projeto"}}, "Attributes": [attr("contoso_name", True, False), attr("contoso_accountid", True, False, "Lookup")]},
    {"MetadataId": G(4), "LogicalName": "contoso_virtual", "SchemaName": "contoso_Virtual", "EntitySetName": "contoso_virtuals",
     "IsCustomEntity": True, "IsManaged": False, "TableType": "Virtual", "Attributes": [attr("contoso_vname", True, False)],
     "DataProviderId": G(200), "DataSourceId": G(202), "ExternalName": "Projects", "ExternalCollectionName": "projects"},
    {"MetadataId": G(5), "LogicalName": "msdyn_odatav4ds", "SchemaName": "msdyn_odatav4ds", "EntitySetName": "msdyn_odatav4dses",
     "IsCustomEntity": False, "IsManaged": True, "PrimaryIdAttribute": "msdyn_odatav4dsid", "PrimaryNameAttribute": "msdyn_name",
     "Attributes": []},
    {"MetadataId": G(3), "LogicalName": "contact", "SchemaName": "Contact", "IsCustomEntity": False, "IsManaged": True,
     "OwnershipType": "UserOwned", "Attributes": [attr("fullname", False)]},
]


SITEMAP_XML = """<SiteMap><Area Id="vendas" Icon="/WebResources/contoso_/js/account.js"><Titles><Title LCID="1046" Title="Vendas"/>
<Title LCID="1033" Title="Sales"/></Titles><Group Id="g1"><Titles><Title LCID="1046" Title="Clientes"/></Titles>
<SubArea Id="s1" Entity="account"><Titles><Title LCID="1046" Title="Contas"/></Titles><Privilege Entity="account" Privilege="Read"/></SubArea>
<SubArea Id="s2" Url="https://erp.contoso.com/portal"><Titles><Title LCID="1046" Title="Portal ERP"/></Titles></SubArea>
<SubArea Id="s3" Url="/main.aspx?pagetype=dashboard"><Titles><Title LCID="1046" Title="Painel"/></Titles></SubArea>
<SubArea Id="s4" Url="https://contoso-dev.crm.dynamics.com/main.aspx?pagetype=entitylist&amp;etn=contoso_project"><Titles><Title LCID="1046" Title="Projetos (DEV)"/></Titles></SubArea>
</Group></Area></SiteMap>"""
DEP_BULK_FAIL = False  # True: a leitura em lote da tabela dependency devolve 403 (testa o fallback)
TIER_ID = lambda: G(abs(hash("contoso_tier")) % 10**9)  # noqa: E731  (id da coluna, como em attr())


def dep(dt, did, rt, rid, kind):
    return {"dependentcomponenttype": dt, "dependentcomponentobjectid": did, "requiredcomponenttype": rt,
            "requiredcomponentobjectid": rid, "dependencytype": kind}


def route(path):
    p = urllib.parse.unquote(path)
    # --- catálogo (100%) e dependências em lote ---
    if p.startswith("GlobalOptionSetDefinitions(Name='componenttype')"):
        return {"Options": [{"Value": v, "Label": {"UserLocalizedLabel": {"Label": lbl}}} for v, lbl in
                            ((1, "Entity"), (2, "Attribute"), (26, "Saved Query"), (29, "Workflow"), (62, "Site Map"),
                             (66, "Custom Control"))]}
    if p.startswith("solutioncomponentdefinitions"):
        return [{"solutioncomponenttype": 10249, "name": "connectionreference", "primaryentityname": "connectionreference"}]
    if p.startswith("solutioncomponents?$select=componenttype,objectid,_solutionid_value,rootcomponentbehavior"):
        assert "$filter" not in p
        rows = [(1, G(1), G(10)), (1, G(2), G(10)), (1, G(2), G(11)), (2, TIER_ID(), G(10)), (29, G(50), G(11)),
                (29, G(50), G(10)), (26, G(70), G(10)), (26, G(300), G(10)), (62, G(86), G(10)), (10249, G(140), G(11)),
                (171, G(301), G(10)), (66, G(190), G(11))]
        return [{"componenttype": ct, "objectid": oid, "_solutionid_value": sid, "rootcomponentbehavior": 0}
                for ct, oid, sid in rows]
    if p.startswith("dependencies?$select="):
        if DEP_BULK_FAIL:
            raise DataverseError(403, p, "sem privilégio prvReadDependency")
        return [dep(26, G(70), 1, G(1), 2), dep(62, G(86), 1, G(1), 2), dep(2, TIER_ID(), 1, G(1), 1),
                dep(26, G(300), 1, G(3), 2),              # view de contact → contact: fora do escopo (só no índice)
                dep(26, G(70), 66, G(190), 2)]            # PCF como controle de view (fora de formulário)
    if p.startswith("RelationshipDefinitions?$select=MetadataId,SchemaName"):
        return [{"MetadataId": G(20), "SchemaName": "contoso_account_project"}]
    if p.startswith("sitemaps?"):
        if "isappaware eq true" not in p and "isappaware eq false" not in p and "sitemapxml" in p:
            return [{"sitemapid": G(87), "sitemapname": None, "ismanaged": True, "isappaware": False,
                     "sitemapxml": "<SiteMap><Area Id='Clássica'/></SiteMap>"}]  # só o clássico sem filtro
        if "isappaware eq false" in p:
            return [{"sitemapid": G(87), "sitemapname": None, "ismanaged": True, "isappaware": False,
                     "sitemapxml": "<SiteMap><Area Id='Clássica'/></SiteMap>"}]
        if "isappaware eq true" in p:
            return [{"sitemapid": G(86), "sitemapname": "Contoso Sitemap", "sitemapnameunique": "contoso_Vendas",
                     "ismanaged": False, "isappaware": True, "sitemapxml": SITEMAP_XML}]
        return [{"sitemapid": G(86), "sitemapname": "Contoso Sitemap"}]  # catálogo (só id + nome)
    if p.startswith("slas?"):
        return [{"slaid": G(210), "name": "Contoso SLA Projeto", "description": "Resolver em 8h úteis",
                 "objecttypecode": 10001, "primaryentityotc": 10001, "statecode": 1, "statecode" + FV: "Active",
                 "slaversion": 100000001, "slaversion" + FV: "Version UC", "isdefault": True, "ismanaged": False,
                 "_businesshoursid_value": G(212)},
                {"slaid": G(211), "name": "SLA nativo", "objecttypecode": 2, "ismanaged": True, "statecode": 1}]
    if p.startswith("slaitems?"):
        return [{"slaitemid": G(214), "name": "Resolver projeto", "_slaid_value": G(210), "sequencenumber": 1,
                 "applicablewhenxml": '<fetch><entity name="contoso_project"><filter><condition attribute="contoso_name" '
                                      'operator="not-null"/></filter><link-entity name="account"/></entity></fetch>',
                 "successconditionsxml": '<fetch><entity name="contoso_project"><filter><condition attribute="statuscode" '
                                         'operator="eq" value="2"/></filter></entity></fetch>',
                 "failureafter": 480, "warnafter": 240, "_msdyn_slakpiid_value": G(213), "_workflowid_value": G(52),
                 "_businesshoursid_value": G(212)}]
    if p.startswith("msdyn_slakpis?"):
        return [{"msdyn_slakpiid": G(213), "msdyn_name": "Resolver projeto", "msdyn_entityname": "contoso_project",
                 "msdyn_kpifield": "contoso_resolvebykpiid", "msdyn_applicablefromfield": "createdon"}]
    if p.startswith("calendars"):
        return [{"calendarid": G(212), "name": "Horário Brasil"}]
    if p.startswith("EntityDefinitions?") and "TableType eq 'Virtual'" in p:
        return [e for e in ENTITIES if e.get("TableType") == "Virtual"]
    if p.startswith("entitydataproviders"):
        return [{"entitydataproviderid": G(200), "name": "Contoso OData Provider", "datasourcelogicalname": "msdyn_odatav4ds",
                 "retrieveplugin": G(101), "retrievemultipleplugin": G(101), "ismanaged": False},
                {"entitydataproviderid": G(201), "name": "Não usado", "datasourcelogicalname": None}]
    if p.startswith("msdyn_odatav4dses?"):
        return [{"msdyn_odatav4dsid": G(202), "msdyn_name": "ERP OData"}]
    # --- uso de campos ---
    if p.startswith("accounts?fetchXml="):
        assert "contoso_revenue" not in p, "coluna Money não pode ir para countcolumn"
        if "contoso_weird" in p:
            raise DataverseError(400, p, '{"error":{"message":"The property provided was of type System.Int32"}}')
        n = p.count('aggregate="countcolumn"')
        return [dict({"total": 1000}, **{f"c{j}": 0 for j in range(n)})]
    if p.startswith("accounts?$select="):
        return [{"accountid": G(600 + i), "contoso_revenue": {"Value": 10} if i < 2 else None, "contoso_weird": None}
                for i in range(4)]
    if p.startswith("contoso_projects?fetchXml="):
        raise DataverseError(400, p, "AggregateQueryRecordLimit exceeded")
    if p.startswith("contoso_projects?$select="):
        return [{"contoso_projectid": G(500 + i), "contoso_name": f"P{i}" if i < 3 else None,
                 "_contoso_accountid_value": G(1)} for i in range(5)]
    m = re.match(r"savedqueries\((.+?)\)", p)
    if m:
        return {"fetchxml": '<fetch><entity name="contoso_project"><attribute name="contoso_name"/></entity></fetch>',
                "layoutxml": '<grid><row><cell name="contoso_name"/></row></grid>'}
    # --- armazenamento e auditoria ---
    if p.startswith("annotations?$apply=groupby((objecttypecode)"):
        raise DataverseError(500, p, '{"error":{"message":"Sql error: Generic SQL error. Sql Number: 8003"}}')
    if p.startswith("annotations?$apply=filter(objecttypecode eq") and "aggregate($count as cnt)" in p and "filesize" not in p:
        return [{"cnt": 40 if "'account'" in p else 2}]
    if p.startswith("activitymimeattachments?$apply=groupby((objecttypecode)"):
        return [{"objecttypecode": "email", "cnt": 900}]
    if "groupby((mimetype)" in p:
        return [{"mimetype": "image/png", "cnt": 800, "total": 8_000_000_000}, {"mimetype": "application/pdf", "cnt": 100, "total": 2_000_000_000}]
    if p.startswith("annotations?$apply=filter("):
        if "objecttypecode eq 'account'" in p and "createdon" not in p:
            raise DataverseError(400, p, "arithmetic overflow")  # força partição por ano
        return [{"total": 1000, "cnt": 1}]
    if p.startswith("activitymimeattachments?$apply=aggregate"):
        return [{"total": 10_000_000_000, "cnt": 900}]
    if p.startswith("audits?$apply=groupby((objecttypecode)"):
        return [{"objecttypecode": 1, "cnt": 5_000_000}, {"objecttypecode": 10001, "cnt": 20_000}]
    if p.startswith("audits?$apply=groupby((action)"):
        return [{"action": 2, "action" + FV: "Update", "cnt": 4_000_000}]
    if p.startswith("audits?$select=createdon"):
        return {"value": [{"createdon": "2022-07-19T00:00:00Z"}]}
    if p.startswith("RetrieveVersion"):
        return {"Version": "9.2.25091.00000"}
    if p.startswith("WhoAmI"):
        return {"UserId": G(900), "OrganizationId": G(901)}
    if p.startswith("organizations"):
        return [{"name": "contoso", "isauditenabled": False, "plugintracelogsetting": 0, "plugintracelogsetting" + FV: "Off",
                 "languagecode": 1046, "createdon": "2021-01-01T00:00:00Z"}]
    if p.startswith("solutions"):
        return [{"solutionid": G(10), "uniquename": "Default", "friendlyname": "Default", "ismanaged": False, "publisherid": {}},
                {"solutionid": G(11), "uniquename": "ContosoCore", "friendlyname": "Contoso Core", "version": "1.2.0.0",
                 "ismanaged": False, "publisherid": {"friendlyname": "Contoso", "customizationprefix": "contoso"}}]
    if p.startswith("solutioncomponents"):
        assert G(11) in p, "não deveria ler componentes da Default"
        return [{"componenttype": 1, "componenttype" + FV: "Entity", "objectid": G(2), "_solutionid_value": G(11)},
                {"componenttype": 29, "componenttype" + FV: "Workflow", "objectid": G(50), "_solutionid_value": G(11)}]
    if p.startswith("EntityDefinitions?"):
        return ENTITIES
    m = re.match(r"EntityDefinitions\(LogicalName='(\w+)'\)/(Attributes|Keys)", p)
    if m:
        ent = next(e for e in ENTITIES if e["LogicalName"] == m.group(1))
        return ent["Attributes"] if m.group(2) == "Attributes" else []
    if p.startswith("RetrieveTotalRecordCount"):
        names = json.loads(p.split("@p1=", 1)[1])
        if "contoso_virtual" in names:
            raise DataverseError(400, p, '{"error":{"message":"Entity contoso_virtual is a virtual entity, which is not supported"}}')
        if "contact" in names:
            raise DataverseError(400, p, '{"error":{"message":"contagem indisponível"}}')
        return {"EntityRecordCountCollection": {"Keys": names, "Values": [1000 * (i + 1) for i in range(len(names))]}}
    if "OneToManyRelationshipMetadata" in p:
        return [{"MetadataId": G(20), "SchemaName": "contoso_account_project", "ReferencedEntity": "account",
                 "ReferencingEntity": "contoso_project", "ReferencingAttribute": "contoso_accountid", "IsCustomRelationship": True,
                 "IsManaged": False, "CascadeConfiguration": {"Delete": "RemoveLink"}},
                {"MetadataId": G(21), "SchemaName": "account_contacts", "ReferencedEntity": "account", "ReferencingEntity": "contact",
                 "IsCustomRelationship": False, "IsManaged": True}]
    if "ManyToManyRelationshipMetadata" in p:
        return []
    if p.startswith("GlobalOptionSetDefinitions"):
        return [{"MetadataId": G(30), "Name": "contoso_status", "IsCustomOptionSet": True, "IsManaged": False},
                {"MetadataId": G(31), "Name": "budgetstatus", "IsCustomOptionSet": False, "IsManaged": True}]
    # --- consultas em lote (get_many): $filter=<chave> eq id1 or <chave> eq id2 … ---
    if "$filter=" in p and " eq " in p and any(p.startswith(x) for x in ("webresourceset?", "systemforms?", "workflows?",
                                                                         "savedqueries?", "customcontrols?")):
        ids = re.findall(r"(?:webresourceid|formid|workflowid|savedqueryid|customcontrolid) eq ([0-9a-f-]{36})", p)
        if ids:
            key = p.split("?")[0]
            res = []
            for i in ids:
                one = route(f"{key}({i})?$select=" + p.split("$select=")[1].split("&")[0])
                k = {"webresourceset": "webresourceid", "systemforms": "formid", "workflows": "workflowid",
                     "savedqueries": "savedqueryid", "customcontrols": "customcontrolid"}[key]
                res.append(dict(one, **{k: i}))
            return res
    m = re.match(r"customcontrols\((.+?)\)", p)
    if m:
        return {"manifest": PCF_MANIFEST if m.group(1) == G(190) else
                '<manifest><control namespace="Contoso" constructor="Unused" version="0.1.0"/></manifest>'}
    if p.startswith("customcontrols"):
        return [{"customcontrolid": G(190), "name": "contoso_Contoso.TierPicker", "version": "1.0.3", "ismanaged": False,
                 "compatibledatatypes": "OptionSet"},
                {"customcontrolid": G(191), "name": "contoso_Contoso.Unused", "version": "0.1.0", "ismanaged": False},
                {"customcontrolid": G(192), "name": "MscrmControls.Grid.ReadOnlyGrid", "ismanaged": True}]
    if p.startswith("teamrolescollection"):
        return [{"teamid": G(170), "roleid": G(161)}]
    if p.startswith("systemuserrolescollection"):
        return [{"systemuserid": G(181), "roleid": G(162)}, {"systemuserid": G(182), "roleid": G(160)}]
    if p.startswith("teammemberships?$select=teamid,systemuserid"):
        return [{"teamid": G(170), "systemuserid": G(182)}, {"teamid": G(170), "systemuserid": G(183)}]
    m = re.match(r"webresourceset\((.+?)\)", p)
    if m:
        content = {G(40): JS_ACCOUNT, G(41): "function nobodyCallsMe(){}"}[m.group(1)]
        return {"content": base64.b64encode(content.encode()).decode()}
    if p.startswith("webresourceset"):
        return [{"webresourceid": G(40), "name": "contoso_/js/account.js", "webresourcetype": 3, "ismanaged": False,
                 "modifiedon": "2026-01-02T03:04:05Z"},
                {"webresourceid": G(41), "name": "contoso_/js/unused.js", "webresourcetype": 3, "ismanaged": False,
                 "modifiedon": "2026-01-02T03:04:06Z"},
                {"webresourceid": G(42), "name": "msdyn_/x.js", "webresourcetype": 3, "ismanaged": True}]
    m = re.match(r"systemforms\((.+?)\)", p)
    if m:
        return {"formxml": FORMXML}
    if p.startswith("systemforms"):
        return [{"formid": G(60), "name": "Conta Principal", "type": 2, "type" + FV: "Main", "objecttypecode": "account",
                 "formactivationstate": 1, "ismanaged": False},
                {"formid": G(61), "name": "Contato", "type": 2, "objecttypecode": "contact", "formactivationstate": 1, "ismanaged": True}]
    if p.startswith("savedqueries"):
        return [{"savedqueryid": G(70), "name": "Projetos ativos", "returnedtypecode": "contoso_project", "querytype": 0,
                 "querytype" + FV: "Main", "isdefault": True, "statecode": 0, "ismanaged": False}]
    if p.startswith("RetrieveEntityRibbon"):
        return {"CompressedEntityXml": zipped_ribbon()}
    if p.startswith("appmodules"):
        return [{"appmoduleid": G(80), "appmoduleidunique": G(85), "name": "Contoso Vendas", "uniquename": "contoso_Vendas", "ismanaged": False}]
    if p.startswith("appmodulecomponents"):
        return [{"componenttype": 1, "objectid": G(1), "_appmoduleidunique_value": G(85)},
                {"componenttype": 62, "objectid": G(86), "_appmoduleidunique_value": G(85)}]
    if p.startswith("RetrieveDependentComponents"):
        if G(1) in p and "@p2=1" in p:   # quem depende da tabela account (registrado pela plataforma)
            return {"value": [
                {"dependentcomponentobjectid": G(70), "dependentcomponenttype": 26, "dependentcomponenttype" + FV: "Saved Query",
                 "requiredcomponentobjectid": G(1), "requiredcomponenttype": 1, "dependencytype" + FV: "Published"},
                {"dependentcomponentobjectid": G(86), "dependentcomponenttype": 62, "dependentcomponenttype" + FV: "Site Map",
                 "requiredcomponentobjectid": G(1), "requiredcomponenttype": 1, "dependencytype" + FV: "Published"}]}
        return {"value": []}
    if p.startswith("RetrieveMissingDependencies"):
        return {"value": [{"dependentcomponentobjectid": G(2), "dependentcomponenttype": 1, "dependentcomponenttype" + FV: "Entity",
                           "requiredcomponentobjectid": G(998), "requiredcomponenttype": 2, "requiredcomponenttype" + FV: "Attribute",
                           "dependencytype" + FV: "Published"}]}
    if p.startswith("canvasapps"):
        raise DataverseError(403, p, "sem privilégio prvReadCanvasApp")
    if p.startswith("bots"):
        return [{"botid": G(81), "name": "Agente Contoso", "schemaname": "contoso_agent", "statecode": 0, "ismanaged": False}]
    if p.startswith("pluginassemblies"):
        return [{"pluginassemblyid": G(90), "name": "Contoso.Plugins", "version": "1.0.0.0", "isolationmode": 2, "sourcetype": 0,
                 "ismanaged": False, "publickeytoken": "5ab6c1d2e3f40718"},
                {"pluginassemblyid": G(91), "name": "Microsoft.Crm.ObjectModel", "ismanaged": True}]
    if p.startswith("plugintypes"):
        return [{"plugintypeid": G(100), "typename": "Contoso.Plugins.AccountPre", "_pluginassemblyid_value": G(90)},
                {"plugintypeid": G(101), "typename": "Contoso.Plugins.ProjectRetrieve", "_pluginassemblyid_value": G(90),
                 "assemblyname": "Contoso.Plugins"},
                {"plugintypeid": G(102), "typename": "Microsoft.Something", "_pluginassemblyid_value": G(91)}]
    if p.startswith("sdkmessageprocessingsteps"):
        base = {"ismanaged": False, "statecode": 0, "rank": 1, "_eventhandler_value" + LK: "plugintype"}
        return [dict(base, sdkmessageprocessingstepid=G(110), name="AccountPre: Update of account", stage=20, mode=0,
                     _plugintypeid_value=G(100), sdkmessageid={"name": "Update"}, sdkmessagefilterid={"primaryobjecttypecode": "account"},
                     configuration="password='SuperSecret123'"),
                dict(base, sdkmessageprocessingstepid=G(111), name="ProjectRetrieve: RetrieveMultiple", stage=40, mode=0,
                     _plugintypeid_value=G(101), sdkmessageid={"name": "RetrieveMultiple"},
                     sdkmessagefilterid={"primaryobjecttypecode": "contoso_project"}),
                dict(base, sdkmessageprocessingstepid=G(113), name="AccountPre: Create of account (desligado)", stage=20, mode=0,
                     _plugintypeid_value=G(100), sdkmessageid={"name": "Create"},
                     sdkmessagefilterid={"primaryobjecttypecode": "account"}, statecode=1),
                dict(base, sdkmessageprocessingstepid=G(114), name="Customização em plugin Microsoft", stage=40, mode=1,
                     _plugintypeid_value=G(102), sdkmessageid={"name": "Create"}, sdkmessagefilterid={"primaryobjecttypecode": "contact"}),
                dict(base, sdkmessageprocessingstepid=G(112), name="MS internal", stage=40, mode=1, ismanaged=True,
                     _plugintypeid_value=G(102), sdkmessageid={"name": "Create"}, sdkmessagefilterid={"primaryobjecttypecode": "contact"})]
    if p.startswith("sdkmessageprocessingstepimages"):
        return [{"name": "pre", "entityalias": "pre", "imagetype": 0, "attributes": "contoso_tier",
                 "_sdkmessageprocessingstepid_value": G(110)}]
    if p.startswith("customapis"):
        return [{"customapiid": G(120), "uniquename": "contoso_RecalcProject", "bindingtype": 1, "bindingtype" + FV: "Entity",
                 "boundentitylogicalname": "contoso_project", "isfunction": False, "ismanaged": False,
                 "_plugintypeid_value": None, "CustomAPIRequestParameters": [{"uniquename": "Force", "type": 0, "type" + FV: "Boolean", "isoptional": True}],
                 "CustomAPIResponseProperties": []}]
    if p.startswith("serviceendpoints"):
        return []
    m = re.match(r"workflows\((.+?)\)", p)
    if m and m.group(1) == G(50):
        return {"clientdata": json.dumps(FLOW), "xaml": None}
    if m and "xaml" in p:
        return {"xaml": '<Activity><SetEntityProperty Attribute="contoso_tier" Entity="account"/></Activity>', "clientdata": None}
    if m:
        return {"clientdata": json.dumps(FLOW)}
    if p.startswith("workflows"):
        own = lambda dis: {"fullname": "Fulano", "isdisabled": dis}  # noqa: E731
        base = {"ismanaged": False, "statecode": 1, "type": 1, "_ownerid_value" + FV: "Fulano", "_ownerid_value" + LK: "systemuser"}
        return [dict(base, workflowid=G(50), name="Contoso - Sync conta → ERP", category=5, primaryentity="none", owninguser=own(True)),
                dict(base, workflowid=G(51), name="Validar tier", category=2, primaryentity="account", owninguser=own(False)),
                dict(base, workflowid=G(52), name="Atualiza projeto", category=0, mode=1, primaryentity="contoso_project", owninguser=own(False)),
                dict(base, workflowid=G(53), name="Processo de Venda A", category=4, primaryentity="account", owninguser=own(False)),
                dict(base, workflowid=G(54), name="Processo de Venda B", category=4, primaryentity="account", owninguser=own(False)),
                dict(base, workflowid=G(55), name="Sistema gerenciado", category=0, primaryentity="contact", ismanaged=True, owninguser=own(False))]
    if p.startswith("environmentvariabledefinitions"):
        return [{"environmentvariabledefinitionid": G(130), "schemaname": "contoso_ErpUrl", "type": 100000000,
                 "defaultvalue": None, "ismanaged": False, "environmentvariabledefinition_environmentvariablevalue": []},
                {"environmentvariabledefinitionid": G(131), "schemaname": "contoso_ErpKey", "type": 100000005,
                 "defaultvalue": "kv-ref", "ismanaged": False, "environmentvariabledefinition_environmentvariablevalue": [{"value": "x"}]}]
    if p.startswith("connectionreferences"):
        return [{"connectionreferenceid": G(140), "connectionreferencelogicalname": "contoso_sql", "connectorid": "/providers/x/apis/shared_sql",
                 "connectionid": None, "ismanaged": False, "statecode": 0}]
    if p.startswith("connectors"):
        return []
    if p.startswith("businessunits"):
        return [{"businessunitid": G(150), "name": "contoso", "_parentbusinessunitid_value": None},
                {"businessunitid": G(151), "name": "Brasil", "_parentbusinessunitid_value": G(150), "_parentbusinessunitid_value" + FV: "contoso"}]
    if p.startswith("roles"):
        return [{"roleid": G(160), "name": "Contoso Vendedor", "ismanaged": False, "_businessunitid_value": G(150)},
                {"roleid": G(161), "name": "Contoso Vendedor", "ismanaged": False, "_businessunitid_value": G(151)},
                {"roleid": G(162), "name": "System Administrator", "ismanaged": True, "_businessunitid_value": G(150)}]
    if p.startswith("RetrieveRolePrivilegesRole"):
        return {"RolePrivileges": [{"PrivilegeName": "prvReadAccount", "Depth": "Global"},
                                   {"PrivilegeName": "prvWriteAccount", "Depth": "Local"}]}
    if p.startswith("teammemberships?fetchXml"):
        raise DataverseError(400, p, '{"error":{"code":"0x8004e023","message":"The maximum record limit of 50000 is exceeded"}}')
    if p.startswith("teammemberships?$select=teamid"):
        return [{"teamid": G(170)}] * 1500
    if p.startswith("teams"):
        return [{"teamid": G(170), "name": "Todos Vendas", "teamtype": 0, "_businessunitid_value": G(150),
                 "_businessunitid_value" + FV: "contoso"},
                {"teamid": G(171), "name": "Equipe Sem Papel", "teamtype": 0, "_businessunitid_value": G(151),
                 "_businessunitid_value" + FV: "Brasil"},
                {"teamid": G(172), "name": "{acesso-registro}", "teamtype": 1, "_businessunitid_value": G(151)}]
    if p.startswith("fieldsecurityprofiles"):
        return []
    if p.startswith("systemusers"):
        return [{"systemuserid": G(180 + i), "isdisabled": i == 0, "accessmode": 0, "applicationid": G(9) if i == 1 else None,
                 "fullname": "App Integração Contoso" if i == 1 else f"Usuário Contoso {i}",
                 "domainname": f"usuario{i}@contoso.example", "caltype": 0, "caltype" + FV: "Professional",
                 "_businessunitid_value": G(151) if i >= 3 else G(150),
                 "_businessunitid_value" + FV: "Brasil" if i >= 3 else "contoso"} for i in range(5)]
    if p.startswith("plugintracelogs"):
        if "exceptiondetails" in p:
            return [{"typename": "Contoso.Plugins.AccountPre"}] * 6
        return [{"typename": "Contoso.Plugins.AccountPre", "performanceexecutionduration": 30, "createdon": "2026-10-01T00:00:00Z"}] * 10
    raise AssertionError(f"rota não mapeada: {p}")


class FakeClient(Client):
    """Substitui só a camada HTTP; paginação real via @odata.nextLink (páginas de 2)."""

    def _request(self, url, page_size=5000):
        path = url.replace(self.base + "/", "")
        page = 0
        if "&__page=" in path:
            path, page = path.split("&__page=")
            page = int(page)
        res = route(path)
        if isinstance(res, list):
            chunk = res[page * 2:(page + 1) * 2]
            out = {"value": chunk}
            if (page + 1) * 2 < len(res):
                out["@odata.nextLink"] = f"{self.base}/{path}&__page={page + 1}"
            return out
        return res


def check_vault(vault_dir, vault_root):
    md = list(vault_dir.rglob("*.md"))
    assert md, "nenhuma nota gerada"
    names = {str(p.relative_to(vault_root))[:-3] for p in md} | {str(p.relative_to(vault_root)) for p in vault_dir.rglob("*.*")}
    broken = []
    for p in md:
        text = p.read_text(encoding="utf-8")
        assert text.startswith("---\n"), p
        fm = yaml.safe_load(text[4:text.index("\n---\n", 4)])
        assert fm.get("tipo") and fm.get("ambiente") == "CONTOSO-PRD", (p, fm)
        assert "\\\\|" not in text, f"pipe escapado em dobro em {p}"
        for line in text.splitlines():
            if line.startswith("- [[") and "Achados/" in line:
                assert text.count(line) == 1, f"achado duplicado em {p}: {line}"
        for link in re.findall(r"!?\[\[([^\]]+)\]\]", text):
            target = link.split("|")[0].split("\\")[0].split("#")[0]
            if target and target not in names:
                broken.append((p.name, target))
        assert "SuperSecret123" not in text and "AbCdEfGhIjKlMnOp" not in text and "ABCDEFGHIJKLMNOPQRST" not in text \
            and "Sup3rS3cretValue" not in text and "Ab$cd3fgh1" not in text, f"segredo vazou em {p}"
    assert not broken, f"wikilinks quebrados: {broken[:10]}"
    for b in vault_dir.rglob("*.base"):
        data = yaml.safe_load(b.read_text(encoding="utf-8"))
        assert data["views"], b
        for view in data["views"]:
            for col in view.get("order", []):
                if col.startswith("formula."):
                    assert col[8:] in data.get("formulas", {}), (b, col)
    cv = json.loads((vault_dir / "Mapa do Ambiente.canvas").read_text(encoding="utf-8"))
    ids = [n["id"] for n in cv["nodes"]]
    assert len(ids) == len(set(ids))
    assert all(e["fromNode"] in ids and e["toNode"] in ids for e in cv["edges"])
    for n in cv["nodes"]:
        if n["type"] == "file":
            assert (vault_root / n["file"]).exists(), n["file"]
    assert not (vault_dir / "Soluções" / "Default.md").exists(), "solução de sistema virou nota"
    return md


def check_dependencies(cfg, derived):
    dep = json.loads((derived / "dependencies.json").read_text())
    edges = {(e["from_label"], e["relation"], e["to_label"]) for e in dep["edges"]}
    flow = "Contoso - Sync conta → ERP"
    must = {
        ("AccountPre: Update of account", "registrado em Update", "account"),
        ("AccountPre: Update of account", "executa código de", "Contoso.Plugins"),
        (flow, "usa conexão", "contoso_sql"), (flow, "lê variável", "contoso_ErpUrl"),
        (flow, "chama", "contoso_RecalcProject"), (flow, "chama child flow", "Atualiza projeto"),
        (flow, "lê/grava", "contoso_project"), (flow, "chama HTTP", "erp.contoso.com"), (flow, "usa conector", "sql"),
        ("account / Conta Principal", "carrega biblioteca", "contoso_/js/account.js"),
        ("account / Conta Principal", "formulário de", "account"),
        ("contoso_/js/account.js", "chama (JS)", "contoso_RecalcProject"),
        ("contoso_/js/account.js", "consulta/grava (JS)", "contoso_project"),
        ("contoso_RecalcProject", "vinculada a", "contoso_project"),
        ("Contoso Vendas", "inclui tabela", "account"),
        ("Contoso Vendedor", "concede acesso a", "account"),
        ("contoso_project", "lookup contoso_accountid", "account"),
        ("account", "botão Sincronizar ERP", "contoso_/js/erp.js"),
        ("contoso_project / Projetos ativos", "plataforma (Published)", "account"),
    }
    assert must <= edges, f"arestas faltando: {must - edges}"
    # 'Contoso.Account' (variável JS, sem aspas) não pode virar dependência da tabela account
    assert ("contoso_/js/account.js", "consulta/grava (JS)", "account") not in edges
    assert any(n["kind"] == "sitemap" and n["label"] == "contoso_Vendas" for n in dep["nodes"])
    acc = next(r for r in dep["per_table"] if r["table"] == "account")
    assert acc["form"] == 1 and acc["step"] == 2 and acc["app"] == 1 and acc["role"] == 1 and acc["child_table"] == 1, acc
    # Excel abre: XML válido e as planilhas esperadas
    import xml.dom.minidom as minidom
    z = zipfile.ZipFile(cfg.vault_dir / "Matriz de Dependências.xlsx")
    for n in z.namelist():
        minidom.parseString(z.read(n))
    wb = z.read("xl/workbook.xml").decode()
    for sheet in ("Matriz por tabela", "Arestas", "Tipo x tipo", "Componentes", "Ausentes na solução"):
        assert f'name="{sheet}"' in wb, sheet
    note = (cfg.vault_dir / "Tabelas" / "account.md").read_text()
    assert "### Usado por" in note and "dependentes:" in note


def check_security_pcf(cfg, snap, fnd):
    """PCF, BUs, equipes e usuários na estrutura do vault."""
    pcf = {p["name"]: p for p in json.loads((snap / "pcf.json").read_text())}
    assert set(pcf) == {"contoso_Contoso.TierPicker", "contoso_Contoso.Unused"}, set(pcf)  # nativo fora
    tp = pcf["contoso_Contoso.TierPicker"]
    assert {(u["form"], u["field"]) for u in tp["usage"]} == {("Conta Principal", "contoso_tier")}  # contact fora do escopo
    assert tp["usage"][0]["bound"] == ["contoso_revenue"], tp["usage"]  # parâmetro estático (theme) não conta
    assert tp["manifest"]["external_domains"] == ["api.rating.contoso.example"] and "WebAPI" in tp["manifest"]["features"]
    assert fnd["PCF-02"]["evidence"] == ["contoso_Contoso.Unused"], fnd["PCF-02"]
    fu = {t["table"]: {f["logical"]: f for f in t["fields"]} for t in json.loads((snap / "field_usage.json").read_text())}
    assert fu["account"]["contoso_revenue"]["pcf"] == ["Conta Principal: contoso_Contoso.TierPicker"]
    assert fu["account"]["contoso_revenue"]["bucket"] == "em-uso", fu["account"]["contoso_revenue"]
    sec = json.loads((snap / "security.json").read_text())
    teams = {t["name"]: t for t in sec["teams"]}
    assert teams["Todos Vendas"]["roles"] == ["Contoso Vendedor"] and teams["Equipe Sem Papel"]["roles"] == []
    users = {u["name"]: u for u in json.loads((snap / "users.json").read_text())}
    app = users["App Integração Contoso"]
    assert app["kind"] == "aplicação" and app["roles"] == ["System Administrator"], app
    assert users["Usuário Contoso 2"]["teams"] == ["Todos Vendas"] and users["Usuário Contoso 2"]["roles_via_team"] == ["Contoso Vendedor"]
    assert fnd["SEG-03"]["evidence"] == ["Equipe Sem Papel (BU Brasil)"] and fnd["SEG-04"]["metric"] == 1
    vd = cfg.vault_dir
    for rel in ("Componentes PCF/contoso_Contoso.TierPicker.md", "Equipes/Todos Vendas.md", "Equipes/Equipe Sem Papel.md",
                "Business Units/Brasil.md", "Business Units/contoso.md", "Usuários/App Integração Contoso.md",
                "Bases/Segurança.base", "Bases/Componentes PCF.base"):
        assert (vd / rel).exists(), rel
    assert not (vd / "Equipes" / "{acesso-registro}.md").exists(), "equipe de acesso virou nota"
    assert not (vd / "Usuários" / "Usuário Contoso 0.md").exists(), "usuário desativado virou nota"
    bu = (vd / "Business Units" / "Brasil.md").read_text()
    assert "Equipe Sem Papel" in bu and "Usuário Contoso 3" in bu and "Filha de" in bu
    role = (vd / "Papéis" / "Contoso Vendedor.md").read_text()
    assert "## Atribuído a" in role and "Todos Vendas" in role and "Usuário Contoso 2" not in role.split("## Atribuído a")[0]
    dep = json.loads((cfg.derived_dir / json.loads((snap / "manifest.json").read_text())["run_id"] / "dependencies.json").read_text())
    edges = {(e["from_label"], e["relation"], e["to_label"]) for e in dep["edges"]}
    must = {("account / Conta Principal", "usa controle PCF", "contoso_Contoso.TierPicker"),
            ("contoso_Contoso.TierPicker", "chama HTTP (PCF)", "api.rating.contoso.example"),
            ("Todos Vendas", "pertence a", "contoso"), ("Brasil", "filha de", "contoso"),
            ("Todos Vendas", "recebe papel", "Contoso Vendedor")}
    assert must <= edges, must - edges


def check_catalog_index(cfg, snap, derived):
    """0.6: catálogo de 100%, dependências em lote, índice SQLite, tabelas virtuais e notas enxutas."""
    from dvinv import index as index_mod
    cat = json.loads((snap / "catalog.json").read_text())
    assert "\n" not in (snap / "catalog.json").read_text().strip(), "catalog.json deveria ser compacto"
    assert cat["types"]["10249"]["name"] == "connectionreference" and cat["types"]["62"]["name"] == "Site Map"
    assert cat["types"]["171"]["name"] is None, cat["types"]["171"]          # sem rótulo em nenhuma fonte
    assert cat["names"][G(86)][1] == "Contoso Sitemap" and cat["names"][TIER_ID()][1] == "account.contoso_tier"
    man = json.loads((snap / "manifest.json").read_text())
    assert man["stats"]["catalog"]["components"] == 10 and man["stats"]["catalog"]["types_unnamed"] == 1, man["stats"]["catalog"]
    assert any("sem fonte de nome" in g["what"] for g in man["gaps"])  # tipo 171 e 10249 sem tabela de origem no fake
    pd = json.loads((snap / "platform_dependencies.json").read_text())
    assert pd["format"] == "compacto/1" and len(pd["edges"]) == 5 and pd["source"].startswith("tabela dependency")

    dep = json.loads((derived / "dependencies.json").read_text())
    edges = {(e["from_label"], e["relation"], e["to_label"]) for e in dep["edges"]}
    assert ("contoso_Vendas", "plataforma (Published)", "account") in edges, "site map do escopo resolvido pelo id"
    assert not any(e["to_label"] == "contact" for e in dep["edges"] if "plataforma" in e["source"]), "aresta fora do escopo na matriz"
    assert ("projeto / Projetos ativos" not in str(edges))
    assert ("contoso_project / Projetos ativos", "plataforma (Published)", "contoso_Contoso.TierPicker") in edges, \
        "PCF usado fora de formulário (view) deveria aparecer pela tabela dependency"
    assert ("contoso_virtual", "dados via provedor virtual", "Contoso OData Provider") in edges
    assert ("Contoso OData Provider", "executa Retrieve", "Contoso.Plugins") in edges

    # cache SQLite (opcional, `--sqlite`): conjunto completo, inclusive nativo → nativo e internas
    db = derived / "inventario.sqlite"
    assert db.exists()
    con = index_mod.connect(db)
    assert con.execute("SELECT count(*) FROM dependencias WHERE fonte = 'plataforma'").fetchone()[0] == 5
    assert con.execute("SELECT count(*) FROM dependencias WHERE interna = 1").fetchone()[0] == 1

    # consultas sobre o próprio vault (CSV) — o que o time usa num clone do repositório
    from dvinv import consulta
    inv = consulta.Inventario(cfg.vault_dir)
    imp = consulta.cmd_walk(inv, "account", "impacto", 1, 50)
    assert "contoso_Vendas" in imp and "Projetos ativos" in imp and "Tabelas/account.md" in imp, imp
    assert "account.contoso_tier" not in imp, "aresta interna (coluna → tabela) não vai para o vault"
    assert G(300) not in (cfg.vault_dir / "Dependências.csv").read_text(), "nativo → nativo não vai para o vault"
    assert "contoso_Vendas" in consulta.cmd_buscar(inv, "vendas")
    assert "Contoso Sitemap" in consulta.cmd_buscar(inv, G(86)) or "contoso_Vendas" in consulta.cmd_buscar(inv, G(86))
    two = consulta.cmd_walk(inv, "contoso_Contoso.TierPicker", "impacto", 2, 50)
    assert "Projetos ativos" in two, two
    assert "é ambíguo" in consulta.cmd_walk(inv, "Venda", "impacto", 1, 50)
    dep_txt = (cfg.vault_dir / "Dependências.csv").read_text(encoding="utf-8-sig")
    assert dep_txt.splitlines()[0].startswith("Origem;Origem id;Origem tipo;Origem nome;Relação;Fonte;Destino;Destino id")
    assert "\r\n" not in dep_txt and any(l.startswith(f"view:{G(70)};") and ";table:account;" in l for l in dep_txt.splitlines())
    # a mesma consulta pela CLI, sem inventory.yaml (clone do repositório do vault)
    import contextlib
    from dvinv import cli as cli_mod
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli_mod.main(["onde-usa", "account.contoso_tier", "--vault", str(cfg.vault_dir)])
    got = buf.getvalue()
    for must in ("Validar tier", "AccountPre: Update of account", "contoso_/js/account.js", "Conta Principal",
                 "evento onchange", "controle PCF"):
        assert must in got, (must, got)  # business rule, filtering attribute, JS, evento e PCF no formulário
    # frontmatter com chave e GUID: grep por um id de log/chamado acha a nota
    acc_fm = (cfg.vault_dir / "Tabelas" / "account.md").read_text()
    assert "chave: table:account" in acc_fm and f"id: {G(1)}" in acc_fm, acc_fm[:400]
    assert f"id: {G(50)}" in next((cfg.vault_dir / "Processos").rglob("Contoso - Sync*.md")).read_text()

    vd = cfg.vault_dir
    for rel in ("09 Catálogo de Componentes.md", "Componentes.csv", "AGENT-INDEX.md", "Dependências ausentes.csv", "Equipes.csv"):
        assert (vd / rel).exists(), rel
    csv_rows = (vd / "Componentes.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(csv_rows) == 11 and any("Contoso Sitemap" in r for r in csv_rows), csv_rows
    assert csv_rows[0].startswith("Chave;Tipo;") and any(r.startswith("table:account;") for r in csv_rows), csv_rows[:3]
    acc = (vd / "Tabelas" / "account.md").read_text()
    assert "extraido_em" not in acc, "data por nota volta a sujar o diff do Git"
    assert "extraido_em" in (vd / "00 Índice.md").read_text()
    vt = (vd / "Tabelas" / "contoso_virtual.md").read_text()
    assert "## Tabela virtual" in vt and "Contoso OData Provider" in vt and "ERP OData" in vt, vt
    assert "## Tabelas virtuais" in (vd / "03 Integrações.md").read_text()
    ai = (vd / "AGENT-INDEX.md").read_text()
    assert "dvinv.py impacto" in ai and "--vault" in ai and "Dependências.csv" in ai


def check_navigation_sla(cfg, snap, derived):
    """Site maps (inclusive os de app, que exigem isappaware eq true) e SLAs com itens, KPI e condições."""
    sm = json.loads((snap / "sitemaps.json").read_text())
    assert [x["name"] for x in sm] == ["contoso_Vendas"], sm  # clássico gerenciado fora do escopo
    s0 = sm[0]
    assert s0["apps"] == [{"id": G(80), "name": "Contoso Vendas"}] and s0["tables"] == ["account"], s0
    assert s0["webresources"] == ["contoso_/js/account.js"], s0["webresources"]
    assert s0["external_urls"] == ["https://contoso-dev.crm.dynamics.com/main.aspx?pagetype=entitylist&etn=contoso_project",
                                   "https://erp.contoso.com/portal"], s0["external_urls"]
    kinds = [x["kind"] for a in s0["areas"] for g in a["groups"] for x in g["subareas"]]
    assert kinds == ["tabela", "url", "dashboard", "url"] and s0["areas"][0]["title"] == "Vendas", kinds
    fnd = {f["id"]: f for f in json.loads((derived / "findings.json").read_text())}
    assert fnd["NAV-01"]["severity"] == "médio" and fnd["NAV-01"]["evidence"] == ["contoso_Vendas: contoso-dev.crm.dynamics.com"], fnd["NAV-01"]
    assert "SLA-01" not in fnd  # um SLA ativo com item
    sla = json.loads((snap / "slas.json").read_text())
    assert [x["name"] for x in sla] == ["Contoso SLA Projeto"], sla
    it = sla[0]["items"][0]
    assert sla[0]["table"] == "contoso_project" and sla[0]["business_hours"] == "Horário Brasil"
    assert it["kpi_field"] == "contoso_resolvebykpiid" and it["applicable_when_fields"] == ["contoso_name"], it
    assert it["success_fields"] == ["statuscode"] and it["linked_entities"] == ["account"], it

    dep = json.loads((derived / "dependencies.json").read_text())
    edges = {(e["from_label"], e["relation"], e["to_label"]) for e in dep["edges"]}
    must = {("Contoso Vendas", "usa site map", "contoso_Vendas"), ("contoso_Vendas", "navega para", "account"),
            ("contoso_Vendas", "usa web resource", "contoso_/js/account.js"),
            ("contoso_Vendas", "abre URL externa", "erp.contoso.com"),
            ("Contoso SLA Projeto", "SLA de", "contoso_project"),
            ("Contoso SLA Projeto", "condição: aplicável quando", "contoso_project.contoso_name"),
            ("Contoso SLA Projeto", "KPI", "contoso_project.contoso_resolvebykpiid"),
            ("Contoso SLA Projeto", "dispara ação", "Atualiza projeto")}
    assert must <= edges, must - edges
    from dvinv import consulta
    inv = consulta.Inventario(cfg.vault_dir)
    out = consulta.cmd_walk(inv, "contoso_project.contoso_name", "impacto", 1, 50)
    assert "Contoso SLA Projeto" in out and "SLAs/Contoso SLA Projeto.md" in out, out
    assert "contoso_Vendas" in consulta.cmd_walk(inv, "account", "impacto", 1, 80)
    vd = cfg.vault_dir
    smn = (vd / "Site Maps" / "contoso_Vendas.md").read_text()
    assert "## Navegação" in smn and "Portal ERP" in smn and "URLs externas" in smn and "Contoso Vendas" in smn, smn
    sln = (vd / "SLAs" / "Contoso SLA Projeto.md").read_text()
    assert "## Itens (KPIs)" in sln and "480 min (8h00)" in sln and "contoso_resolvebykpiid" in sln, sln
    for b in ("Site Maps.base", "SLAs.base"):
        assert (vd / "Bases" / b).exists(), b


def check_maps(cfg):
    """Navegação e grafo por tipo: hub .md, canvas e cores do Graph view."""
    vd = cfg.vault_dir
    hub = (vd / "Mapas" / "Cloud Flows.md").read_text()
    assert 'path:\"Dataverse/CONTOSO-PRD/Processos/Cloud Flows/\"' in hub, hub[:600]
    assert "Contoso - Sync conta → ERP" in hub and "usa conexão" in hub, hub
    idx = (vd / "Mapas" / "00 Mapas por tipo.md").read_text()
    for name in ("Tabelas", "Cloud Flows", "Business Rules", "Web Resources", "Site Maps", "SLAs"):
        assert f"Mapas/{name}\\|{name}" in idx, (name, idx)  # pipe escapado dentro da tabela
    cv = json.loads((vd / "Mapas" / "Cloud Flows.canvas").read_text())
    files = [n for n in cv["nodes"] if n["type"] == "file"]
    assert files and all((cfg.vault_root / n["file"]).exists() for n in files), files
    assert cv["edges"] and all(e["fromNode"] in {n["id"] for n in cv["nodes"]} for e in cv["edges"])
    gj = json.loads((cfg.vault_root / ".obsidian" / "graph.json").read_text())
    assert any(gr["query"] == 'path:"/Processos/Cloud Flows/"' for gr in gj["colorGroups"]), gj["colorGroups"][:3]
    assert "Mapas por tipo" in (vd / "00 Índice.md").read_text()


def check_method_drift():
    """REPO-04 sem ilspycmd: heurística de métodos por classe (decompilado × repo)."""
    from dvinv.collectors.repos import methods
    prd = ("namespace X { public class AtivarQuote : PluginBase { public AtivarQuote() {} "
           "public override void PreUpdate(LocalContext c) { if (x) { y(); } } "
           "private static DateTime GetDeliveryTimeDate(Entity e) { return DateTime.Now; } } "
           "public class Outra { public void Nada() {} } }")
    repo = ("namespace X { public class AtivarQuote : PluginBase { public override void PreUpdate(LocalContext c) { } "
            "public void MetodoAntigo(string a, int b) { } } }")
    m_prd, m_repo = methods(prd, "AtivarQuote"), methods(repo, "AtivarQuote")
    assert m_prd - m_repo == {"GetDeliveryTimeDate"}, m_prd
    assert m_repo - m_prd == {"MetodoAntigo"}, m_repo
    assert "Nada" not in m_prd  # não vaza para a classe seguinte


def main():
    check_method_drift()
    tmp = Path(tempfile.mkdtemp(prefix="dvinv-test-"))
    try:
        repo = tmp / "repo"
        (repo / "JavaScript").mkdir(parents=True)
        (repo / "Plugins").mkdir()
        (repo / "JavaScript" / "account.js").write_text(JS_ACCOUNT.replace("helper: function () {", "outra: function () {"))
        (repo / "JavaScript" / "unused.js").write_text("function nobodyCallsMe(){}\r\n")
        (repo / "Plugins" / "AccountPre.cs").write_text(
            "namespace Contoso.Plugins { public class AccountPre : IPlugin { public void Execute(IServiceProvider s) {"
            " var x = entity[\"contoso_name\"]; } } }")
        (repo / "app.config").write_text('<add key="Password" value="Sup3rS3cretValue"/>\n<setting password="Sup3rS3cretValue"/>\n'
                                         '<add name="PROD" connectionString="Url=https://x; Username=u; Password=Ab$cd3fgh1; authtype=Office365"/>\n'
                                         '<add name="CI" connectionString="Password=$(DbPwd);"/>')
        (tmp / "inventory.yaml").write_text(yaml.safe_dump({
            "repos": [{"path": "repo", "kind": "any", "name": "contoso-crm"}],
            "environment": {"name": "CONTOSO-PRD", "url": "https://contoso.crm.dynamics.com"},
            "scope": {"prefixes": ["contoso_"], "solutions": ["ContosoCore", "NaoExiste"]},
            "output": {"raw_dir": "out/_raw", "vault_root": "vault", "vault_folder": "Dataverse/CONTOSO-PRD"},
        }))
        cfg = config_mod.load(tmp / "inventory.yaml", {"deep_all": True, "sqlite": True})
        cfg.deep["plugin_binaries"] = False
        ctx = Context(cfg, FakeClient(cfg, credential=None), Scope(cfg))
        ctx.client._bearer = lambda: "fake"
        manifest = run(ctx)

        bd = manifest["scope_breakdown"]
        assert bd["prefix"]["contoso_"] >= 5 and "msdyn_" not in str(bd), bd
        st = manifest["stats"]
        assert st["tables"]["scope"] == 3 and st["tables"]["custom"] == 2, st["tables"]
        gaps_txt = json.dumps(manifest["gaps"], ensure_ascii=False)
        assert "contoso_virtual" not in gaps_txt, "tabela virtual não deveria ir para RetrieveTotalRecordCount"
        assert "contagem de registros indisponível em 1 tabela(s)" in gaps_txt and "outro erro: contact" in gaps_txt, gaps_txt
        assert "lote" not in gaps_txt  # lote dividido até isolar a tabela
        assert st["webresources"]["scope"] == 2
        assert st["plugins"]["steps"] == 4 and st["plugins"]["assemblies"] == 1, st["plugins"]
        assert st["processes"]["scope"] == 5, st["processes"]  # gerenciado de 'contact' fica fora
        assert st["security"]["roles_root_bu"] == 2, st["security"]  # dedupe por BU raiz
        gaps = " ".join(g["what"] for g in manifest["gaps"])
        assert "canvasapps" in gaps and "naoexiste" in gaps, gaps
        snap = ctx.snapshot_dir
        assert snap.parent == cfg.raw_dir and snap.name == manifest["run_id"], (snap, manifest["run_id"])
        assert not list(cfg.raw_dir.glob(".staging-*")), "staging não foi publicado/limpo"
        assert set(manifest["files"]) >= {"tables.json", "field_usage.json", "processes.json"}, manifest["files"]
        assert manifest["collectors"]["apps"]["status"] == "parcial" and manifest["collectors"]["forms"]["status"] == "ok"
        raw_text = " ".join(p.read_text() for p in snap.glob("*.json"))
        assert "SuperSecret123" not in raw_text and "AbCdEfGhIjKlMnOp" not in raw_text, "segredo no _raw"

        v = render(cfg)
        assert v.snapshot == snap and v.derived_dir == cfg.derived_dir / manifest["run_id"], (v.snapshot, v.derived_dir)
        assert not (snap / "findings.json").exists() and not (snap / "dependencies.json").exists(), "derivado no snapshot"
        fnd = {f["id"]: f for f in json.loads((v.derived_dir / "findings.json").read_text())}
        assert sum(1 for f in fnd.values() if f["id"] == "DEP-01") == 1
        assert not any("$webresource:" in e for f in fnd.values() for e in f.get("evidence") or []), "prefixo não removido"
        expected = {"PCF-01", "PCF-02", "SEG-03", "SEG-04", "DEP-01", "DEP-03", "FLW-01", "FLD-01", "FLD-03", "JS-03", "JS-06", "STO-01", "AUD-01", "REPO-01", "REPO-02", "REPO-03", "SEC-01", "UI-01", "UI-02", "UI-03", "PLG-01", "PLG-02", "PLG-03", "PLG-04", "OPS-01", "OPS-02",
                    "PRC-01", "PRC-02", "PRC-03", "SEG-02", "ALM-01", "ALM-02", "ALM-03"}
        assert expected <= set(fnd), f"faltam achados: {expected - set(fnd)}"
        assert fnd["SEC-01"]["metric"] == 3, fnd["SEC-01"]  # JS (sig=) + flow (code=) + config do step
        md = check_vault(cfg.vault_dir, cfg.vault_root)
        fu = {t["table"]: {f["logical"]: f for f in t["fields"]} for t in json.loads((snap / "field_usage.json").read_text())}
        assert fu["account"]["contoso_legacy"]["bucket"] == "candidato-seguro", fu["account"]["contoso_legacy"]
        assert fu["account"]["contoso_revenue"]["populated"] == 2 and fu["account"]["contoso_weird"]["populated"] == 0
        fu_acc = next(t for t in json.loads((snap / "field_usage.json").read_text()) if t["table"] == "account")
        assert fu_acc["method"] == "agregação + paginação (2 colunas)", fu_acc["method"]
        assert fu["account"]["contoso_tier"]["bucket"] == "sem-dados-com-logica", fu["account"]["contoso_tier"]
        assert fu["contoso_project"]["contoso_name"]["populated"] == 3 and fu["contoso_project"]["contoso_name"]["bucket"] == "em-uso"
        assert fu["contoso_project"]["contoso_name"]["repo_files"] == 1
        repo_raw = json.loads((snap / "repos.json").read_text())[0]
        wr = {w["name"]: w for w in repo_raw["webresources"]}
        assert wr["contoso_/js/unused.js"]["status"] == "idêntico", wr  # CRLF/BOM não contam
        assert wr["contoso_/js/account.js"]["functions_only_env"] == ["helper"], wr["contoso_/js/account.js"]
        assert sum(1 for h in repo_raw["secret_hits"] if h["file"] == "app.config") == 3, repo_raw["secret_hits"]  # 2 + connection string com $ no meio; $(DbPwd) não
        ty = {t["type"]: t["status"] for t in repo_raw["plugin_types"]}
        assert ty == {"Contoso.Plugins.AccountPre": "com fonte", "Contoso.Plugins.ProjectRetrieve": "sem fonte no repo"}, ty
        st = json.loads((snap / "storage.json").read_text())
        acc = next(r for r in st["annotations"]["by_table"] if r["table"] == "account")
        assert acc["bytes"] == 1000 * (date.today().year - 2008 + 1), acc  # partição por ano após estouro
        assert st["audit"]["by_table"][0]["table"] == "account" and st["audit"]["oldest"] == "2022-07-19"
        check_dependencies(cfg, v.derived_dir)
        check_security_pcf(cfg, snap, fnd)
        check_catalog_index(cfg, snap, v.derived_dir)
        check_navigation_sla(cfg, snap, v.derived_dir)
        check_maps(cfg)
        (cfg.vault_root / ".obsidian" / "graph.json").write_text('{"search": "minha busca"}')  # agora é do usuário
        for sec in ("05 Uso de Campos", "06 Armazenamento e Auditoria", "07 Repositórios", "08 Matriz de Dependências"):
            assert (cfg.vault_dir / f"{sec}.md").exists(), sec
        flow = json.loads((snap / "processes.json").read_text())
        assert next(p for p in flow if p["category"] == "Cloud Flow")["tables"] == ["account", "contoso_project"]
        cv = json.loads((cfg.vault_dir / "Mapa do Ambiente.canvas").read_text())
        assert any(e["label"] == "flow HTTP" for e in cv["edges"]), "aresta tabela→HTTP ausente no canvas"

        # render determinístico: dois processos com sementes de hash diferentes geram arquivos idênticos
        # (vault publicado em Git — re-render sem mudança no ambiente não pode gerar diff)
        import hashlib
        import os
        import subprocess

        def vault_digest():
            return {p.relative_to(cfg.vault_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(cfg.vault_root.rglob("*")) if p.is_file()}
        code = ("import sys; sys.path.insert(0, %r); from dvinv import config; from dvinv.render.obsidian import render; "
                "render(config.load(%r))") % (str(ROOT / "scripts"), str(tmp / "inventory.yaml"))
        digests = []
        for seed in ("1", "2"):
            subprocess.run([sys.executable, "-c", code], check=True, capture_output=True,
                           env=dict(os.environ, PYTHONHASHSEED=seed))
            digests.append(vault_digest())
        diff = sorted(k for k in set(digests[0]) | set(digests[1]) if digests[0].get(k) != digests[1].get(k))
        assert not diff, f"render não determinístico: {diff[:10]}"

        # AGENTS.md (bloco dvinv) + CLAUDE.md na raiz do vault
        from dvinv.render.agents import BEGIN, END
        ag, cl = cfg.vault_root / "AGENTS.md", cfg.vault_root / "CLAUDE.md"
        assert cl.read_text() == "@AGENTS.md\n", cl.read_text()
        agt = ag.read_text()
        assert agt.count(BEGIN) == 1 and agt.count(END) == 1 and "CONTOSO-PRD" in agt and "AGENT-INDEX.md" in agt, agt
        assert "inventory.yaml" in agt and "https://contoso.crm.dynamics.com" in agt, agt
        assert len(agt.split(BEGIN)[1].splitlines()) <= 30, "bloco lido em toda sessão: manter curto"
        ag.write_text("# Projeto Contoso\n\nRegra do time: não citar o cliente.\n\n" + agt + "\nRodapé do time.\n")
        cl.write_text("# Claude\nInstruções próprias.\n")

        # notas de extrações anteriores: sem curadoria somem; com curadoria ficam e são listadas; nota do time fica
        (cfg.vault_dir / "Tabelas" / "old_sem_curadoria.md").write_text(f"---\ntipo: tabela\n---\n# x\n\n{MANUAL}\n\n## Notas do analista\n")
        (cfg.vault_dir / "Tabelas" / "old_com_curadoria.md").write_text(f"---\ntipo: tabela\n---\n# y\n\n{MANUAL}\n\n## Notas do analista\n\nDecisão: manter.\n")
        (cfg.vault_dir / "Tabelas" / "nota do time.md").write_text("# Nossa análise\n")

        # re-render preserva edição do analista
        achado = cfg.vault_dir / "Achados"
        note = next(achado.glob("SEC-01*.md"))
        txt = note.read_text().replace("status: aberto", "status: em análise") + "\nRotacionado em 06/10.\n"
        note.write_text(txt)
        cfg.xlsx = False
        cfg.repo = True  # modo repositório de conhecimento (um por unidade/avenida)
        (cfg.vault_root / "README.md").write_text("# Base de conhecimento\n\nTexto do time.\n")
        v2 = render(cfg)
        rd = (cfg.vault_root / "README.md").read_text()
        assert rd.startswith("# Base de conhecimento") and "Texto do time." in rd and "CONTOSO-PRD" in rd, rd
        assert "Não está neste repositório" in rd and rd.count("dvinv:inicio") == 1
        gi, ga = (cfg.vault_root / ".gitignore").read_text(), (cfg.vault_root / ".gitattributes").read_text()
        assert ".obsidian/workspace*.json" in gi and ".env" in gi and "*.csv text eol=lf" in ga, (gi, ga)
        from dvinv.cli import git_summary
        assert "não é um repositório" in git_summary(cfg.vault_root) or "mudança" in git_summary(cfg.vault_root)
        assert not (cfg.vault_dir / "Tabelas" / "old_sem_curadoria.md").exists() and "Tabelas/old_sem_curadoria.md" in v2.pruned
        assert (cfg.vault_dir / "Tabelas" / "old_com_curadoria.md").exists() and v2.orphans_kept == ["Tabelas/old_com_curadoria.md"]
        assert "old_com_curadoria" in (cfg.vault_dir / "Notas fora do inventário atual.md").read_text()
        assert (cfg.vault_dir / "Tabelas" / "nota do time.md").exists(), "nota sem marcador do dvinv não pode ser apagada"
        assert not (cfg.vault_dir / "Matriz de Dependências.xlsx").exists(), "xlsx desligado: planilha antiga removida"
        assert json.loads((cfg.vault_root / ".obsidian" / "graph.json").read_text()) == {"search": "minha busca"}
        assert "Matriz de Dependências.xlsx" not in (cfg.vault_dir / "00 Índice.md").read_text()
        assert any("CLAUDE.md" in w and "@AGENTS.md" in w for w in v2.agent_warnings), v2.agent_warnings
        txt2 = note.read_text()
        assert "status: em análise" in txt2 and "Rotacionado em 06/10." in txt2 and txt2.count(MANUAL) == 1
        agt2 = ag.read_text()
        assert agt2.startswith("# Projeto Contoso") and "Regra do time" in agt2 and agt2.rstrip().endswith("Rodapé do time.")
        assert agt2.count(BEGIN) == 1 and "CONTOSO-PRD" in agt2, agt2
        assert cl.read_text() == "# Claude\nInstruções próprias.\n", "CLAUDE.md existente não pode ser alterado"

        # diff contra uma cópia alterada (outro snapshot: manifesto com o sha256 novo)
        b = tmp / "out" / "_raw_b" / "20990101T000000Z"
        shutil.copytree(snap, b)
        t = json.loads((b / "tables.json").read_text())
        t[0]["columns"].append({"logical": "contoso_new", "custom": True})
        (b / "tables.json").write_text(json.dumps(t))
        mb = json.loads((b / "manifest.json").read_text())
        mb["files"]["tables.json"] = sha256_file(b / "tables.json")
        mb["run_id"] = b.name
        (b / "manifest.json").write_text(json.dumps(mb))
        out = compare(cfg.raw_dir, b.parent, cfg.vault_root, "Dataverse")  # raiz → último íntegro de cada lado
        assert "contoso_new" in out.read_text()
        dep_row = re.search(r"^\| Dependências \| (\d+) \|", out.read_text(), re.M)
        assert dep_row and int(dep_row.group(1)) > 0, "diff sem dependências recalculadas a partir do snapshot"
        assert not list(cfg.vault_root.glob(".dvinv-render-*")), "cópia de trabalho do render não foi removida"

        # web resources: conteúdo reaproveitado do snapshot anterior quando o modifiedon não mudou (sem download)
        calls_before = []
        ctx3 = Context(cfg, FakeClient(cfg, credential=None), Scope(cfg))
        ctx3.client._bearer = lambda: "fake"
        m3 = run(ctx3, ["webresources"])
        w3 = m3["stats"]["webresources"]
        assert w3["content_reused"] == 2 and w3["content_downloaded"] == 0, w3
        assert not any("webresourceset(" in q["path"] or "webresourceid eq" in q["path"] for q in m3["queries"]), \
            "conteúdo inalterado não deveria ser baixado de novo"
        wr3 = {w["name"]: w for w in json.loads((ctx3.snapshot_dir / "webresources.json").read_text())}
        assert wr3["contoso_/js/account.js"]["content_source"].startswith("reaproveitado do snapshot")
        assert wr3["contoso_/js/account.js"]["secret_hits"], "achado de segredo precisa vir junto do reaproveitamento"
        # biblioteca de terceiros pelo nome: sem download, declarado como limitação
        cfg.deep["webresource_skip_names"], cfg.deep["webresource_reuse"] = ["account\\.js$"], False
        ctx4 = Context(cfg, FakeClient(cfg, credential=None), Scope(cfg))
        ctx4.client._bearer = lambda: "fake"
        m4 = run(ctx4, ["webresources"])
        assert m4["stats"]["webresources"]["content_skipped"] == 1 and m4["stats"]["webresources"]["content_downloaded"] == 1
        assert any("conteúdo não baixado" in g["what"] and g["kind"] == "limitação" for g in m4["gaps"])
        del cfg.deep["webresource_skip_names"]
        # acima do limite de tamanho: segredos verificados, conteúdo não guardado (lacuna declarada)
        cfg.deep["webresource_max_bytes"] = 50
        ctx5 = Context(cfg, FakeClient(cfg, credential=None), Scope(cfg))
        ctx5.client._bearer = lambda: "fake"
        m5 = run(ctx5, ["webresources"])
        wr5 = {w["name"]: w for w in json.loads((ctx5.snapshot_dir / "webresources.json").read_text())}
        acc = wr5["contoso_/js/account.js"]
        assert acc["content"] is None and acc["secret_hits"] and "não guardado" in acc["content_source"], acc
        assert any("acima de 0 KB não guardado" in g["what"] for g in m5["gaps"]), m5["gaps"]
        del cfg.deep["webresource_max_bytes"]
        cfg.deep["webresource_reuse"] = True

        # leitura em lote negada → fallback por componente do escopo, com lacuna declarada
        global DEP_BULK_FAIL
        DEP_BULK_FAIL = True
        ctx2 = Context(cfg, FakeClient(cfg, credential=None), Scope(cfg))
        ctx2.client._bearer = lambda: "fake"
        m2 = run(ctx2, ["platform_dependencies"])
        DEP_BULK_FAIL = False
        assert m2["stats"]["platform_dependencies"]["source"].startswith("RetrieveDependentComponents"), m2["stats"]
        assert m2["stats"]["platform_dependencies"]["edges"] == 2 and any("lote" in g["what"] for g in m2["gaps"])

        print(f"OK — {len(md)} notas, {len(list(cfg.vault_dir.rglob('*.base')))} bases, "
              f"{len(fnd)} achados, {len(manifest['queries'])} chamadas simuladas, {len(manifest['gaps'])} lacunas")
        print(f"vault de exemplo em {cfg.vault_dir}")
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
