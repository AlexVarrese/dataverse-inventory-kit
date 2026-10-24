# Coletores — o que cada um lê e armadilhas já tratadas

Todos usam a Dataverse Web API v9.2 com `Prefer: odata.include-annotations="*"` (valores formatados
e tipo de lookup) e `odata.maxpagesize=5000`, seguindo `@odata.nextLink`. Falhas viram lacunas no
`manifest.json` em vez de abortar, e cada coletor recebe um status (`ok`, `parcial` = terminou com
lacunas, `falhou`, `sem-dados` = desligado/nada a coletar, `nao-executado` = fora do `--only`). Coletor
que falha não grava arquivo e seus dados parciais são descartados (não alimentam os coletores seguintes). Ordem de execução: `environment → solutions → tables` (definem o
escopo) e depois os demais.

| Coletor | Fontes (Web API) | Saída `_raw/<run_id>/` | Escopo |
|---|---|---|---|
| environment | `RetrieveVersion()`, `WhoAmI()`, `organizations` | environment.json | — |
| solutions | `solutions` (+publisher), `solutioncomponents` por solução | solutions.json | componentes só de soluções não gerenciadas + `scope.solutions` (Default/Active/Basic ignoradas) |
| catalog | `GlobalOptionSetDefinitions(Name='componenttype')`, `solutioncomponentdefinitions`, `solutioncomponents` (todas as linhas, página a página), e por tipo uma leitura id + nome na tabela de origem (`EntityDefinitions` com Attributes/Keys, `RelationshipDefinitions`, `GlobalOptionSetDefinitions`, `savedqueries`, `systemforms`, `sitemaps`, `customcontrols`, `customcontrolresources`, …) | catalog.json (compacto) | **org inteira** — 100% dos componentes, inclusive soluções gerenciadas; tipos sem fonte de nome ficam sem nome (lacuna `limitação`). `msdyn_solutioncomponentsummary` não é usado: pagina devagar e estoura a URL (HTTP 414) |
| tables | `EntityDefinitions` (+Attributes leves), depois `/Attributes` e `/Keys` por tabela, `RetrieveTotalRecordCount` | tables.json | prefixo, solução, custom não gerenciada, ou nativa com coluna customizada |
| table_activity | por tabela: `<entityset>?$select=createdon&$orderby=createdon desc&$top=1` e o mesmo com `modifiedon` (só a coluna de data; **sem `$batch`**, 30 s por leitura, sem nova tentativa, 2 conexões, para após 3 cortes seguidos; acima de `deep.table_activity_max_records` não consulta) | table_activity.json (`last_created`, `last_modified`, `inactive_days`) | tabelas do escopo com contagem ≠ 0, não virtuais, que têm a coluna; bloqueado em `metadata_only` |
| virtual_tables | `EntityDefinitions?$filter=TableType eq 'Virtual'` (provedor, fonte, nome externo), `entitydataproviders` (plugins por operação), `plugintypes`, registros da tabela de fonte de dados (**só id e nome** — a configuração pode ter URL e credencial) | virtual_tables.json | org inteira (são poucas); `in_scope` marca as do escopo. Elásticas: só contagem |
| relationships | `RelationshipDefinitions/…OneToMany…` e `…ManyToMany…` (2 chamadas para a org inteira) | relationships.json | toca tabela do escopo **e** (custom ou envolve tabela custom) |
| optionsets | `GlobalOptionSetDefinitions` | optionsets.json | custom + critério de escopo |
| webresources | `webresourceset` (todos os nomes) + `webresourceset(id)?$select=content` | webresources.json, _webresource_names.json | prefixo/solução/não gerenciado |
| forms | `systemforms` + `systemforms(id)?$select=formxml` (ativos; inativos também com `deep.field_usage`, só para colunas) | forms.json | forms de tabelas do escopo |
| views | `savedqueries` | views.json | views de tabelas do escopo |
| pcf | `customcontrols` + `customcontrols(id)?$select=manifest`; vínculos coluna → controle vêm do formxml (coletor forms) | pcf.json | critério de escopo, ou de terceiros usado em formulário de tabela do escopo; controles nativos (`MscrmControls.*`) fora |
| ribbons | `RetrieveEntityRibbon` (zip base64 → RibbonXml.xml), em pacotes `$batch` | ribbons.json | botões cujo comando chama biblioteca do escopo; tabelas da Microsoft (`exclude_microsoft`) não são lidas |
| apps | `appmodules`, `appmodulecomponents` (todos os apps), `canvasapps`, `bots` | apps.json (`availability`: tabelas e formulários de cada app model-driven, inclusive gerenciados) | critério de escopo; `availability` cobre todos os apps (formulário fora de apps, FRM-01) |
| sitemaps | `sitemaps` com `$filter=isappaware eq true` **e** `eq false` (sem filtro a API só devolve o clássico) + `sitemapxml` (Área → Grupo → SubÁrea: tabela, web resource em Url/Icon, dashboard, URL externa, privilégios) | sitemaps.json | critério de escopo ou site map de app do escopo (via `appmodulecomponents`, tipo 62) |
| slas | `slas`, `slaitems` (FetchXML de aplicável quando / sucesso / pausa → colunas citadas), `msdyn_slakpis` (campo de KPI, aplicável a partir de), `calendars` (só o nome do horário comercial) | slas.json | SLA de tabela do escopo ou critério de escopo |
| plugins | `pluginassemblies`, `plugintypes`, `sdkmessageprocessingsteps` (`customizationlevel eq 1`), `…stepimages`, `pluginassemblies(id)?$select=content`\* | plugins.json, bin/*.dll | assembly no escopo; step de classe no escopo ou webhook em tabela do escopo |
| customapis | `customapis` + `CustomAPIRequestParameters`/`CustomAPIResponseProperties` | customapis.json | critério de escopo |
| serviceendpoints | `serviceendpoints` | serviceendpoints.json | critério de escopo |
| processes | `workflows` (`type eq 1 or category eq 5`) + `workflows(id)?$select=clientdata` para flows (gatilho Dataverse: `trigger_table`, `trigger_fields` = colunas de filtro) | processes.json | nome/solução/não gerenciado, ou tabela do escopo + não gerenciado |
| alm | `environmentvariabledefinitions` (+valores), `connectionreferences`, `connectors` | alm.json | critério de escopo |
| security | `businessunits`, `roles`, `teams`, `teamrolescollection` (papéis das equipes), `fieldsecurityprofiles`, `systemusers` (só contagens: tipo, status, BU), `RetrieveRolePrivilegesRole`, FetchXML aggregate em `teammemberships`\* | security.json | papéis da BU raiz |
| users\* | `systemusers` (nome, UPN, BU, status, modo de acesso, licença, application id), `systemuserrolescollection`, `teammemberships` | users.json | org inteira — **dado pessoal**, só com extra `usuarios` (bloqueado em `metadata_only`) |
| health\* | `plugintracelogs` (todas e `exceptiondetails ne null`) | health.json | janela `plugin_trace_days` |
| repos (`repos:`) | arquivos dos clones locais + `git rev-parse`/`log -1`; `ilspycmd` nas DLLs (se instalado) | repos.json, decompiled/ | web resources e classes do escopo |
| field_usage | sondagem `$select=<id>&$top=1` (tabela ilegível sai aqui; vazia = zero exato; virtual não é lida); FetchXML `countcolumn` (≤ 50 mil registros) ou amostra paginada `$select` + existência `$filter=<coluna> ne null&$top=1` nas colunas com zero; cruza forms, views (`savedqueries(id)` fetchxml/layoutxml), processos, steps, JS, repos | field_usage.json | colunas custom das tabelas do escopo ou `field_usage.tables` |
| platform_dependencies | tabela `dependency` inteira (`dependencies?$select=…`, página a página — ESAB-TEST: 844 mil linhas em 3 min, sem 429); se negada, `RetrieveDependentComponents` por componente do escopo (comportamento até a 0.5). `RetrieveMissingDependencies` por solução não gerenciada/do escopo | platform_dependencies.json (compacto: nós + arestas) | org inteira no snapshot; a matriz do vault usa só as arestas cujo exigido está no escopo |
| storage\* | `RetrieveTotalRecordCount` (todas), `annotations`/`activitymimeattachments`/`audits` com `$apply` groupby/aggregate | storage.json | org inteira |

Além disso, o coletor `processes` lê `workflows(id)?$select=xaml,clientdata`
de workflows/BRs/actions/BPFs (colunas citadas + segredos), e para cloud flows monta a árvore de ações
(`outline`), contagem por tipo, condições e tratamentos de erro (`runAfter` em Failed/TimedOut). O
coletor `webresources` faz análise estática de todo JS baixado (`js`).

\* só com um extra (`extras:` no yaml ou `--extras`): `health` = `saude`, `storage` = `armazenamento`, `users` e membros de equipe = `usuarios`, DLLs de `plugins` = `binarios`, `solution_history` = `historico`, `solution_layers` = `camadas`. O resto é o núcleo e sempre roda (o perfil `metadata_only` ainda trava leitura de conteúdo e registros).

## Matriz de dependências (render)

Montada em `dvinv/dependencies.py` a partir do snapshot (não exige coleta extra); o render grava
`_derived/<run_id>/dependencies.json` e o `diff` recalcula a partir do snapshot. Relações inferidas:
step → tabela/assembly/service endpoint · processo → tabela primária · flow → tabelas, referências de
conexão, variáveis de ambiente, Custom APIs, child flows, conectores e hosts HTTP · formulário → tabela e
bibliotecas JS · botão de ribbon → JS · JS → tabelas e Custom APIs citadas **entre aspas** e hosts externos
· Custom API → tabela e assembly · app → tabelas (`appmodulecomponents`) · papel → tabelas (privilégios) · tabela filha → tabela pai (lookup). Com `platform_dependencies` (núcleo), soma as arestas da
plataforma, resolvendo os ids para componentes conhecidos quando possível.

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
- **PCF**: só os vínculos em formulários ativos são visíveis (`controlDescriptions` do formxml). PCF definido
  como controle padrão da coluna/tabela ou em view não é exposto de forma consultável pela Web API — lacuna
  declarada (`kind=limitação`). Parâmetros não estáticos do controle contam como uso da coluna na matriz.
- **Equipes de acesso** (teamtype 1) são criadas por registro via access team template e podem ser milhares:
  entram só na contagem, sem nota nem nó no grafo.
- **Secure config de steps** não é exposta pela Web API, por design. Só a unsecure é lida e varrida.
- **Preenchimento de colunas**: primeiro uma leitura de uma linha por tabela — tabela que não responde
  (provedor virtual quebrado, plugin com erro no RetrieveMultiple) vira uma lacuna só, sem as dezenas de chamadas
  dos lotes divididos; tabela vazia sai com zero exato; tabela virtual não é medida (uma lacuna `limitação`).
  Agregação nativa só até ~50 mil registros (lote que falha é dividido; coluna citada no erro vai direto para
  paginação; após 8 lotes com erro, a tabela inteira pagina). Acima disso, pagina uma **amostra** contando valores
  não nulos em lotes de 35 colunas (lookups via `_x_value`), até `field_usage.max_records` (padrão 50 mil; antes
  500 mil — horas em base grande), com progresso a cada 30 s. Amostra parcial: método "parcial", e cada coluna com
  zero na amostra ganha uma leitura de existência (`$filter=<coluna> ne null&$top=1`, 60 s, sem nova tentativa,
  para após 3 cortes seguidos): nenhuma linha = zero exato (`zero_confirmed`), uma linha = tem dado fora da amostra
  (`found_beyond_sample`); sem resposta = continua inconclusiva. O percentual vale só para a amostra.
- **Classificação do uso de campos**: `candidato-seguro` (rótulo: *candidato a investigação de remoção*)
  exige zero exato (contagem completa ou existência sem valor) e todas as fontes da matriz medidas — forms (formxml de todos os forms da
  tabela), views (colunas de todas as views da tabela), processos (`process_definitions` e
  `flow_definitions`), plugins, web resources (`webresource_content`) e repositório (`repos:`). Zero
  preenchido numa amostra parcial, medição que falhou ou fonte ausente/falha/parcial/desligada →
  `inconclusivo`, com os motivos em `inconclusive_reasons`. Coluna que só aparece em formulário inativo →
  `sem-dados-uso-fraco` (listada em `forms_inactive`).
- **Comparação com o repositório**: o conteúdo é normalizado (BOM, CRLF, espaços, linhas vazias e
  segredos redigidos dos dois lados) antes de comparar. Web resource sem arquivo de mesmo nome é
  casado pelo conteúdo mais parecido (Jaccard de linhas ≥ 50%).
- **Drift de plugin**: o corpo decompilado difere do fonte por ruído (nomes de variáveis locais,
  `string.Format`), então a comparação é por **assinatura de método**. Diferença de corpo exige leitura humana.

## Estender

Um coletor novo é uma função `collect_x(ctx)` que grava `ctx.data["x"]` (e opcionalmente
`ctx.stats["x"]`), registrada em `collectors/__init__.py::REGISTRY`. Use `ctx.scope.reason(...)`
para decidir o escopo e `ctx.gap(...)` para falhas parciais (`kind="limitação"` para limites
permanentes da fonte, que não deixam o coletor `parcial`). Arquivos extras (binários) vão em
`ctx.out_dir`, nunca em `cfg.raw_dir`. Não grave nada fora do `ctx.data`: o `run` publica o snapshot. Depois renderize a nova família em
`render/obsidian.py` e, se fizer sentido, crie uma regra em `findings.py` e uma Base em
`render/bases.py`. Acrescente a rota na fixture de `tests/test_pipeline.py`.

Candidatos naturais: command bar moderna (`appactions`), dashboards/charts (`savedqueryvisualizations`),
templates de e-mail, regras de duplicidade, SLAs, filas e roteamento (Customer Service), Power Pages,
dataflows, colunas de arquivo/imagem (file storage por coluna).
