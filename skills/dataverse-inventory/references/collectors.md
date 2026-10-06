# Coletores — o que cada um lê e armadilhas já tratadas

Todos usam a Dataverse Web API v9.2 com `Prefer: odata.include-annotations="*"` (valores formatados
e tipo de lookup) e `odata.maxpagesize=5000`, seguindo `@odata.nextLink`. Falhas viram lacunas no
`manifest.json` em vez de abortar. Ordem de execução: `environment → solutions → tables` (definem o
escopo) e depois os demais.

| Coletor | Fontes (Web API) | Saída `_raw/` | Escopo |
|---|---|---|---|
| environment | `RetrieveVersion()`, `WhoAmI()`, `organizations` | environment.json | — |
| solutions | `solutions` (+publisher), `solutioncomponents` por solução | solutions.json | componentes só de soluções não gerenciadas + `scope.solutions` (Default/Active/Basic ignoradas) |
| tables | `EntityDefinitions` (+Attributes leves), depois `/Attributes` e `/Keys` por tabela, `RetrieveTotalRecordCount` | tables.json | prefixo, solução, custom não gerenciada, ou nativa com coluna customizada |
| relationships | `RelationshipDefinitions/…OneToMany…` e `…ManyToMany…` (2 chamadas para a org inteira) | relationships.json | toca tabela do escopo **e** (custom ou envolve tabela custom) |
| optionsets | `GlobalOptionSetDefinitions` | optionsets.json | custom + critério de escopo |
| webresources | `webresourceset` (todos os nomes) + `webresourceset(id)?$select=content` | webresources.json, _webresource_names.json | prefixo/solução/não gerenciado |
| forms | `systemforms` + `systemforms(id)?$select=formxml` | forms.json | forms de tabelas do escopo |
| views | `savedqueries` | views.json | views de tabelas do escopo |
| ribbons\* | `RetrieveEntityRibbon` (zip base64 → RibbonXml.xml) | ribbons.json | botões cujo comando chama biblioteca do escopo |
| apps | `appmodules`, `canvasapps`, `bots` | apps.json | critério de escopo |
| plugins | `pluginassemblies`, `plugintypes`, `sdkmessageprocessingsteps` (`customizationlevel eq 1`), `…stepimages`, `pluginassemblies(id)?$select=content`\* | plugins.json, bin/*.dll | assembly no escopo; step de classe no escopo ou webhook em tabela do escopo |
| customapis | `customapis` + `CustomAPIRequestParameters`/`CustomAPIResponseProperties` | customapis.json | critério de escopo |
| serviceendpoints | `serviceendpoints` | serviceendpoints.json | critério de escopo |
| processes | `workflows` (`type eq 1 or category eq 5`) + `workflows(id)?$select=clientdata` para flows | processes.json | nome/solução/não gerenciado, ou tabela do escopo + não gerenciado |
| alm | `environmentvariabledefinitions` (+valores), `connectionreferences`, `connectors` | alm.json | critério de escopo |
| security | `businessunits`, `roles`, `teams`, `fieldsecurityprofiles`, `systemusers`, `RetrieveRolePrivilegesRole`\*, FetchXML aggregate em `teammemberships`\* | security.json | papéis da BU raiz |
| health\* | `plugintracelogs` (todas e `exceptiondetails ne null`) | health.json | janela `plugin_trace_days` |
| repos (`repos:`) | arquivos dos clones locais + `git rev-parse`/`log -1`; `ilspycmd` nas DLLs (se instalado) | repos.json, decompiled/ | web resources e classes do escopo |
| field_usage\* | FetchXML `countcolumn` (≤ 50 mil registros) ou paginação `$select`; cruza forms, views (`savedqueries(id)` fetchxml/layoutxml), processos, steps, JS, repos | field_usage.json | colunas custom das tabelas do escopo ou `field_usage.tables` |
| storage\* | `RetrieveTotalRecordCount` (todas), `annotations`/`activitymimeattachments`/`audits` com `$apply` groupby/aggregate | storage.json | org inteira |

Além disso, com `deep.process_definitions` o coletor `processes` lê `workflows(id)?$select=xaml,clientdata`
de workflows/BRs/actions/BPFs (colunas citadas + segredos), e para cloud flows monta a árvore de ações
(`outline`), contagem por tipo, condições e tratamentos de erro (`runAfter` em Failed/TimedOut). O
coletor `webresources` faz análise estática de todo JS baixado (`js`).

\* só com `deep`.

## Armadilhas resolvidas no código

- **Workflows duplicados**: cada processo ativo tem um registro *Definition* (type 1) e um *Activation* (type 2). Filtrar `type eq 1`, senão a contagem infla.
- **Cloud flows fora de solução** não existem na tabela `workflow`: é uma lacuna permanente, declarada.
- **Papéis copiados por BU**: a tabela `roles` tem uma linha por papel × BU. Use só a BU raiz.
- **Privilégios usam SchemaName** (`prvReadcontoso_Project`): o casamento com tabelas é case-insensitive via SchemaName.
- **Limite de agregação** (~50 mil registros) em `$apply`/FetchXML aggregate: contagens grandes são paginadas ou viram lacuna.
- **`SUM` de inteiros** estoura int32 acima de ~2 GB (ex. `annotation.filesize`): se for medir storage, particione por período.
- **URL com espaço** no `$filter` quebra o `urllib`: tudo passa por `quote()` com os caracteres OData preservados.
- **Colunas que não existem em todas as versões/regiões** (ex. `canvasapps.canvasapptype`): `get_first_ok` tenta um `$select` mais enxuto.
- **429 / 5xx**: retry com `Retry-After` (service protection limits).
- **Flows referenciam tabelas por entity set** (`contoso_projects`) nas ações e por nome lógico no gatilho: os dois são mapeados para o nome lógico.
- **Secure config de steps** não é exposta pela Web API, por design. Só a unsecure é lida e varrida.
- **Preenchimento de colunas**: agregação nativa só até ~50 mil registros. Acima disso, pagina contando
  valores não nulos em lotes de 35 colunas (lookups via `_x_value`), até `field_usage.max_records`. Se parar
  antes do fim, o método fica "parcial" e os números valem só para os registros lidos.
- **Comparação com o repositório**: o conteúdo é normalizado (BOM, CRLF, espaços, linhas vazias e
  segredos redigidos dos dois lados) antes de comparar. Web resource sem arquivo de mesmo nome é
  casado pelo conteúdo mais parecido (Jaccard de linhas ≥ 50%).
- **Drift de plugin**: o corpo decompilado difere do fonte por ruído (nomes de variáveis locais,
  `string.Format`), então a comparação é por **assinatura de método**. Diferença de corpo exige leitura humana.

## Estender

Um coletor novo é uma função `collect_x(ctx)` que grava `ctx.data["x"]` (e opcionalmente
`ctx.stats["x"]`), registrada em `collectors/__init__.py::REGISTRY`. Use `ctx.scope.reason(...)`
para decidir o escopo e `ctx.gap(...)` para falhas parciais. Depois renderize a nova família em
`render/obsidian.py` e, se fizer sentido, crie uma regra em `findings.py` e uma Base em
`render/bases.py`. Acrescente a rota na fixture de `tests/test_pipeline.py`.

Candidatos naturais: command bar moderna (`appactions`), dashboards/charts (`savedqueryvisualizations`),
templates de e-mail, regras de duplicidade, SLAs, filas e roteamento (Customer Service), Power Pages,
dataflows, colunas de arquivo/imagem (file storage por coluna).
