# Changelog

## 0.5.0 — não lançado

Correções de segurança e confiabilidade. **Muda a estrutura de `out/`** (ver README da skill, "Estrutura de saída").

Snapshots imutáveis (P0-01, P1-05)
- Cada `extract` grava numa área de staging e publica, por rename atômico, um snapshot
  `<raw_dir>/<run_id>/` (run_id em UTC, ex. `20261009T203200Z`). O `manifest.json` traz run_id, perfil,
  status de cada coletor (`ok`/`parcial`/`falhou`/`sem-dados`/`nao-executado`) e a lista de arquivos com sha256.
- Antes, coletor que falhava deixava o JSON da extração anterior no `_raw/` e o render o tratava como
  atual. Agora coletor que falha não tem arquivo (e seus dados parciais são descartados), vira lacuna,
  e o render lê só os arquivos listados no manifesto do snapshot escolhido, conferindo o sha256.
- `render` usa o último snapshot íntegro (ou `--snapshot <dir|run_id>`); `findings.json` e
  `dependencies.json` saem do snapshot e vão para `_derived/<run_id>/`. O `diff` recalcula as dependências
  a partir do snapshot e aceita diretório, raiz `_raw` (último íntegro) ou run_id.
- Vários snapshots no mesmo dia coexistem (antes a cópia `_raw-AAAA-MM-DD` era feita uma vez por dia).
  `--no-snapshot` virou opção obsoleta e ignorada. Pastas no formato antigo ainda são lidas por
  `render --snapshot`/`diff`, com aviso de que não há verificação de integridade.
- `01 Ambiente` ganhou a tabela "Status dos coletores", o run_id e o perfil de coleta.
- DLLs e fontes decompilados de plugin ficam dentro do snapshot (`bin/`, `decompiled/`); o decompilado é redigido.

Segredos (P0-02)
- Redação estruturada: parâmetros de query sensíveis (`api_key`, `access_token`, `client_secret`, `code`,
  `sig`, `token`, `pwd`, `subscription-key`…), headers (`Authorization`, `x-functions-key`,
  `Ocp-Apim-Subscription-Key`, `x-api-key`), credencial em URL (`https://user:senha@host`), atribuições
  JS/JSON/YAML/.env de nomes sensíveis com ou sem aspas, e valores de chaves sensíveis em estruturas JSON.
  Nomes que só contêm a palavra (`tokenize(`, `tokenizer`, `secret_hits`) não são afetados.
- Lacunas, mensagens de `DataverseError` e prints de falha são redigidos na origem; o manifesto inteiro
  passa por `redact_tree`. Hosts HTTP de flows/JS não carregam mais `user:senha@`.
- Varredura final fail-closed: snapshot (JSON, decompilados), vault (MD, Bases, Canvas, CSV, XLSX) e
  derivados são varridos antes da publicação; se algo casar, o comando falha com o tipo e a linha (sem o
  valor) e o vault/snapshot anterior fica intacto.

Uso de campos (P0-03)
- Novo bucket `inconclusivo`: zero preenchido numa amostra parcial, medição que falhou ou qualquer fonte
  da matriz (forms, views, processos/flows, plugins, web resources, repositórios) ausente, com falha,
  parcial ou desligada — os motivos ficam em `inconclusive_reasons`.
- `candidato-seguro` só com contagem completa e todas as fontes medidas, e o rótulo passou a ser
  "candidato a investigação de remoção — confirmar integrações externas" (nunca "seguro").
- Formulários inativos entram como uso fraco (`forms_inactive`, bucket `sem-dados-uso-fraco`, FLD-02).
- FLD-01/02/03 ajustados; novo FLD-04 (info) lista as colunas inconclusivas. Notas `05 Uso de Campos` e
  de tabela mostram a cobertura e as fontes que faltaram.

Rede e autenticação
- P1-02: o cliente só segue `@odata.nextLink` e só envia o Bearer para URLs `https` do mesmo host/porta
  de `environment.url`; o contrário vira erro (`UnsafeUrlError`) antes de pedir o token.
- P1-03: device code não usa mais cache de token em texto puro. Padrão: token só em memória; opt-in
  `auth.devicecode_cache: true` usa apenas o cofre criptografado do sistema.

Perfil `metadata_only`
- `profile: metadata_only` no YAML ou `--profile metadata_only` desliga e trava (mesmo com `--deep`)
  `webresource_content`, `flow_definitions`, `process_definitions`, `plugin_binaries`, `field_usage`,
  `storage`, `plugin_trace` e `team_members`. `record_counts` continua permitido (contagem agregada, não lê
  registros). O perfil vai para o manifesto e para `01 Ambiente`.

Inventário: PCF, business units, equipes e usuários
- Novo coletor `pcf`: `customcontrols` + manifesto (namespace, versão, tipo, propriedades, Web API, domínios
  externos) e vínculos coluna → controle lidos do formxml (`controlDescriptions`). Nota por controle em
  `Componentes PCF/`, seção nos formulários da tabela, Base própria, arestas formulário → PCF e PCF → host
  externo. Colunas passadas como parâmetro do PCF contam como uso na matriz de uso de campos.
- `security` passa a ler os papéis das equipes (`teamrolescollection`) e a contar usuários ativos por BU.
  Notas por business unit (`Business Units/`) e por equipe owner/Entra ID (`Equipes/`); papéis ganham a
  seção "Atribuído a"; `04 Segurança` com links, contagens e papéis; arestas BU → BU pai, equipe → BU e
  equipe → papel. Equipes de acesso (por registro) ficam só na contagem.
- Novo coletor opt-in `users` (`deep.users`, dado pessoal, bloqueado em `metadata_only`): nome, UPN, BU,
  status, modo de acesso, licença, papéis diretos e via equipe, equipes. Nota por usuário ativo em
  `Usuários/` e Base **Segurança**.
- Novos achados: SEG-03 (equipe proprietária sem papel), SEG-04 (usuário de aplicação com System
  Administrator), SEG-05 (papel do escopo sem atribuição), PCF-01 (domínios externos), PCF-02 (PCF sem uso).

Testes
- `tests/test_regressao_p0.py` (unittest, offline): falha de coletor entre dois runs, snapshot adulterado,
  colisão de run_id, interrupção no meio, vetores de segredo em todas as saídas e no console, varredura
  bloqueando render/snapshot, amostra parcial, forms com falha, sem repositório, formulário inativo,
  nextLink para outro host, cache de token e perfil `metadata_only`.

## 0.4.0 — 2026-10-07

Validado contra um ambiente real de TEST (159 tabelas, 4 mil colunas custom, 900 processos):
extração completa caiu de **mais de 2 h para 22 min**, e o ambiente revelou bugs que a fixture não tinha.

Desempenho
- Conexões HTTPS reaproveitadas (keep-alive) e respostas gzip: 2,5× por chamada (medido a ~200 ms do datacenter).
- Até `parallel` (padrão 4) chamadas simultâneas em colunas por tabela, ribbons e dependências da plataforma.
- Busca em lote (`$filter=id eq … or …`) para componentes de soluções, conteúdo de web resources, formxml,
  fetchxml de views e definições de processos/flows. Views: 28 min → 33 s.
- Progresso no log das etapas longas; aviso explícito e contagem (`throttled_429`) quando o serviço pede espera.

Robustez (bugs encontrados no ambiente real)
- `systemform` não tem `modifiedon` (usa `publishedon`) — o coletor de formulários falhava inteiro.
- Tabela virtual derrubava o lote de `RetrieveTotalRecordCount`: virtuais são puladas e lote com erro é
  dividido até isolar a tabela; as falhas viram uma lacuna consolidada.
- `countcolumn` falha em colunas Money: elas (e qualquer coluna que falhe) vão para paginação, o resto segue
  por agregação.
- Membros de equipe acima de 50 mil vínculos: plano B por paginação de `teammemberships`.
- `groupby` de notas na org inteira estoura o tempo de SQL: plano B por tabela do escopo.
- Step em assembly fora do escopo quebrava a matriz de dependências.
- Bibliotecas `$webresource:<nome>` no formxml geravam falso "web resource inexistente".
- Senha de connection string com `$` no meio não era detectada.
- `git` recusava clones de outro dono (safe.directory) — branch/commit saíam vazios.
- Mensagens de erro da API agora aparecem nas lacunas (antes ficavam truncadas atrás da URL).

Escopo e relatório
- `scope.exclude_prefixes` e tabela "De onde veio o escopo" (motivo × prefixo) em `01 Ambiente`.
- DEP-01 consolidado num único achado; DEP-02 lista as 15 tabelas de maior impacto.
- `diff` do mesmo ambiente rotula os lados com data e hora.

## 0.3.0 — 2026-10-07

- **Matriz de dependências entre componentes**: grafo "A depende de B" com relações inferidas (steps,
  processos, flows → conexões/variáveis/Custom APIs/child flows/hosts, formulários e botões → JS,
  JS → tabelas/Custom APIs, Custom APIs, apps → tabelas, papéis → tabelas, lookups entre tabelas).
  Nota `08 Matriz de Dependências` (impacto por tabela, tipo × tipo, mais dependidos, externos, órfãos),
  seção **Dependências** (depende de / usado por) em cada nota, properties `dependencias`/`dependentes`,
  visão "Alto impacto" na Base de tabelas.
- Exportação **`Matriz de Dependências.xlsx`** (sem dependência externa) e `Dependências.csv`.
- `deep.platform_dependencies`: dependências registradas pelo Dataverse (RetrieveDependentComponents) e
  dependências ausentes por solução (RetrieveMissingDependencies). Achados DEP-01..03.
- Apps model-driven passam a registrar seus componentes (`appmodulecomponents`).
- `diff` compara também as dependências entre snapshots/ambientes.

## 0.2.0 — 2026-10-06

- **Uso de campos** (`deep.field_usage`): preenchimento real por coluna (agregação nativa, com
  paginação quando passa de 50 mil registros), matriz de uso (formulários, eventos, views, processos,
  flows, plugins, JS, repositório) e classificação em candidato seguro a remoção / sem dados na UI /
  sem dados com lógica / dados sem uso conhecido / em uso. Achados FLD-01..03.
- **Análise estática de JS**: Xrm.Page obsoleto, eval, endpoints SOAP/OData 2011, XHR síncrono,
  XrmServiceToolkit, jQuery, chamadas Xrm.WebApi, hosts externos e colunas lidas. Achados JS-01..06.
- **Estrutura de cloud flows**: árvore de ações, tipos, condições, tratamento de erro (runAfter
  Failed/TimedOut), colunas citadas. Achado FLW-01.
- **Definições de processos** (`deep.process_definitions`): colunas citadas no XAML/clientdata de
  workflows, business rules, actions e BPFs; varredura de segredos.
- **Armazenamento e auditoria** (`deep.storage`): maiores tabelas, notas e anexos de e-mail (quantidade,
  bytes com partição contra estouro de int32, tipos de arquivo), auditoria por tabela/ação e registro
  mais antigo. Achados STO-01, AUD-01.
- **Repositórios Git** (`repos:`): web resources idênticos/divergentes/sem fonte (com funções só de um
  lado), classes de plugin com/sem fonte, diff de métodos DLL decompilada × repo (com `ilspycmd`),
  segredos versionados, referências a colunas no código. Achados REPO-01..04.
- Segredos: novos padrões para `appSettings` .NET (`key="…Password" value="…"`) e connection strings.
- `--only` inclui automaticamente os coletores de que o escolhido depende.
- Notas novas: `05 Uso de Campos`, `06 Armazenamento e Auditoria`, `07 Repositórios`.

## 0.1.0 — 2026-10-06 (primeira versão)

Primeira versão.

- 17 coletores read-only: ambiente, soluções, tabelas/colunas/chaves, relacionamentos, option sets, web resources (com código), formulários (bibliotecas e eventos), views, ribbons, apps (model-driven, canvas, Copilot Studio), plugins (assemblies, classes, steps, imagens, DLLs), Custom APIs, service endpoints, processos (workflows, business rules, actions, BPFs, cloud/desktop flows), ALM (variáveis de ambiente, referências de conexão, conectores), segurança (BUs, papéis, privilégios, equipes, usuários) e saúde (plugin trace).
- Autenticação: Service Principal, MCP (cache do Dataverse CLI), Azure CLI e device code (`auto`).
- Vault Obsidian: notas com properties, 6 Bases, Canvas do ambiente, 21 regras de achados, preservação das edições do analista entre extrações.
- `diff` entre snapshots e ambientes.
- Redação de segredos em todas as saídas.
- Teste offline ponta a ponta (`tests/test_pipeline.py`).
