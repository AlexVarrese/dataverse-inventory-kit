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
<cell><control id="name" datafieldname="name"/></cell><cell><control id="t" datafieldname="contoso_tier"/></cell>
</row></rows></section></sections></column></columns></tab></tabs></form>"""
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
     "IsCustomEntity": True, "IsManaged": False, "TableType": "Virtual", "Attributes": [attr("contoso_vname", True, False)]},
    {"MetadataId": G(3), "LogicalName": "contact", "SchemaName": "Contact", "IsCustomEntity": False, "IsManaged": True,
     "OwnershipType": "UserOwned", "Attributes": [attr("fullname", False)]},
]


def route(path):
    p = urllib.parse.unquote(path)
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
    if "$filter=" in p and " eq " in p and any(p.startswith(x) for x in ("webresourceset?", "systemforms?", "workflows?", "savedqueries?")):
        ids = re.findall(r"(?:webresourceid|formid|workflowid|savedqueryid) eq ([0-9a-f-]{36})", p)
        if ids:
            key = p.split("?")[0]
            res = []
            for i in ids:
                one = route(f"{key}({i})?$select=" + p.split("$select=")[1].split("&")[0])
                k = {"webresourceset": "webresourceid", "systemforms": "formid", "workflows": "workflowid",
                     "savedqueries": "savedqueryid"}[key]
                res.append(dict(one, **{k: i}))
            return res
    m = re.match(r"webresourceset\((.+?)\)", p)
    if m:
        content = {G(40): JS_ACCOUNT, G(41): "function nobodyCallsMe(){}"}[m.group(1)]
        return {"content": base64.b64encode(content.encode()).decode()}
    if p.startswith("webresourceset"):
        return [{"webresourceid": G(40), "name": "contoso_/js/account.js", "webresourcetype": 3, "ismanaged": False},
                {"webresourceid": G(41), "name": "contoso_/js/unused.js", "webresourcetype": 3, "ismanaged": False},
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
                 "ismanaged": False, "publickeytoken": "abc"},
                {"pluginassemblyid": G(91), "name": "Microsoft.Crm.ObjectModel", "ismanaged": True}]
    if p.startswith("plugintypes"):
        return [{"plugintypeid": G(100), "typename": "Contoso.Plugins.AccountPre", "_pluginassemblyid_value": G(90)},
                {"plugintypeid": G(101), "typename": "Contoso.Plugins.ProjectRetrieve", "_pluginassemblyid_value": G(90)},
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
        return [{"teamid": G(170), "name": "Todos Vendas", "teamtype": 0, "_businessunitid_value": G(150)}]
    if p.startswith("fieldsecurityprofiles"):
        return []
    if p.startswith("systemusers"):
        return [{"systemuserid": G(180 + i), "isdisabled": i == 0, "accessmode": 0, "applicationid": G(9) if i == 1 else None} for i in range(5)]
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
    assert any(n["kind"] == "platform" and n["sub"] == "Site Map" for n in dep["nodes"])
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
        cfg = config_mod.load(tmp / "inventory.yaml", {"deep_all": True})
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
        expected = {"DEP-01", "DEP-03", "FLW-01", "FLD-01", "FLD-03", "JS-03", "JS-06", "STO-01", "AUD-01", "REPO-01", "REPO-02", "REPO-03", "SEC-01", "UI-01", "UI-02", "UI-03", "PLG-01", "PLG-02", "PLG-03", "PLG-04", "OPS-01", "OPS-02",
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
        for sec in ("05 Uso de Campos", "06 Armazenamento e Auditoria", "07 Repositórios", "08 Matriz de Dependências"):
            assert (cfg.vault_dir / f"{sec}.md").exists(), sec
        flow = json.loads((snap / "processes.json").read_text())
        assert next(p for p in flow if p["category"] == "Cloud Flow")["tables"] == ["account", "contoso_project"]
        cv = json.loads((cfg.vault_dir / "Mapa do Ambiente.canvas").read_text())
        assert any(e["label"] == "flow HTTP" for e in cv["edges"]), "aresta tabela→HTTP ausente no canvas"

        # re-render preserva edição do analista
        achado = cfg.vault_dir / "Achados"
        note = next(achado.glob("SEC-01*.md"))
        txt = note.read_text().replace("status: aberto", "status: em análise") + "\nRotacionado em 06/10.\n"
        note.write_text(txt)
        render(cfg)
        txt2 = note.read_text()
        assert "status: em análise" in txt2 and "Rotacionado em 06/10." in txt2 and txt2.count(MANUAL) == 1

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

        print(f"OK — {len(md)} notas, {len(list(cfg.vault_dir.rglob('*.base')))} bases, "
              f"{len(fnd)} achados, {len(manifest['queries'])} chamadas simuladas, {len(manifest['gaps'])} lacunas")
        print(f"vault de exemplo em {cfg.vault_dir}")
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
