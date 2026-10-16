# Changelog

## 0.6.0 — não lançado

Inventário de **100% dos componentes** e camada de consulta para agentes de IA (impacto de evolutivas e
rastreamento de problemas sem ler o vault inteiro). Construído sobre a 0.5.0 (ainda sem tag).

Removido
- Cache SQLite (`--sqlite`, `output.sqlite`, `inventario.sqlite`) e a planilha `Matriz de Dependências.xlsx`
  (`output.xlsx`): as consultas (`buscar`, `onde-usa`, `impacto`, `depende`) usam só o vault (`Componentes.csv` +
  `Dependências.csv`), que abre também no Excel. `output.sqlite`/`output.xlsx` num yaml antigo geram um aviso e são
  ignorados; a planilha deixada por versão anterior some do vault no próximo render. Lista de componentes sem
  dependente que passa do limite da nota 08 vai para `Sem dependentes.csv`.

Catálogo de 100% (coletor `catalog`, ligado por padrão)
- Lê todas as linhas de `solutioncomponent` (todas as soluções, inclusive as gerenciadas da Microsoft). No
  ESAB-TEST: 225 mil linhas, 121 mil componentes, 299 tipos.
- Nome de cada tipo: opção global `componenttype` + `solutioncomponentdefinitions` (tipos novos, ex.
  10249 → connectionreference) + tabela fixa para 69/80/172/181/183. Nome de cada componente: uma leitura
  id + nome por tipo na tabela de origem (metadados via EntityDefinitions/RelationshipDefinitions).
- Vault: `09 Catálogo de Componentes.md` (por tipo) e `Componentes.csv` (um por linha). Fora do escopo,
  componente não vira nota — fica no CSV e no índice.

Dependências da plataforma em lote (`platform_dependencies`, agora no núcleo)
- Lê a tabela `dependency` inteira em vez de `RetrieveDependentComponents` por componente (ESAB-TEST:
  844 mil arestas em ~3 min, sem throttling). Se a leitura em lote for negada, volta ao método antigo e
  declara a lacuna. Snapshot em formato compacto (nós + arestas).
- A matriz do vault mantém a semântica anterior (quem depende de componentes do escopo); o conjunto
  completo vai para o índice. Revela PCF usado fora de formulário (controle padrão de coluna/view).
- `RetrieveMissingDependencies` em paralelo (`parallel`, padrão 4): eram ~780 chamadas sequenciais no
  ESAB-TEST (6,5 min num dia normal; mais de 40 min com o servidor lento).

O vault é o produto (consulta sem binário, em qualquer clone do repositório)
- `Dependências.csv` vira índice textual: chave, GUID, tipo, nome e nota das duas pontas, arestas que tocam o
  escopo nos dois sentidos (sem as internas de metadados), ordem estável e LF. `Componentes.csv` ganha `Chave`.
- Uso de campos no grafo para todas as tabelas do escopo: evento de formulário, controle PCF, filtering
  attribute e imagem de plugin, campos de workflow/flow e `getAttribute` em JS → `onde-usa tabela.coluna`.
- `buscar`/`onde-usa`/`impacto`/`depende` leem os CSVs do vault (`consulta.py`); `--vault <pasta>` dispensa
  `inventory.yaml` e credenciais.
- `chave` e `id` (GUID) no frontmatter de toda nota de componente; `AGENT-INDEX` e `AGENTS.md` com receitas de grep.
- Notas órfãs de extrações anteriores: removidas sem curadoria; com curadoria, listadas em
  `Notas fora do inventário atual.md`. Aviso de arquivo > 50 MB.

Vault como repositório de conhecimento (`output.repo: true`)
- Um repositório por unidade de negócio (ex. `<programa>-<unidade>-knowledge`), com os ambientes em
  `Dataverse/<AMB>/`. O render mantém blocos `dvinv` no `README.md`, `.gitignore` e `.gitattributes` (texto do
  time preservado), desliga a planilha `.xlsx` por padrão e mostra o resumo do `git status`. Não commita.
- Render determinístico: listas de dependências, canvas e planilha em ordem estável, LF em tudo (código JS do
  Dataverse vem com CRLF) e data fixa nas entradas do `.xlsx`. Validado no ESAB-TEST: render → commit → render =
  "nenhuma mudança"; teste com duas sementes de hash diferentes exige arquivos idênticos.

Índice SQLite (desde então opcional — ver acima)
- Componentes (kit + catálogo), soluções, dependências (plataforma + inferidas), achados.
- Novos comandos só-leitura: `buscar`, `onde-usa`, `impacto` (CTE recursiva, `--profundidade`),
  `depende`. Saída compacta com o caminho da nota. Arestas internas de metadados (coluna → tabela) só
  com `--internas`. Texto do banco passa pela varredura de segredos antes de publicar (fail-closed).
- `AGENT-INDEX.md` gerado no vault: roteiro de leitura econômica, mapa de pastas e lista de arquivos
  grandes que não devem ser lidos inteiros. `references/indice.md` documenta comandos e esquema.

Tabelas virtuais (coletor `virtual_tables`)
- Todas as tabelas virtuais da org com provedor de dados, fonte (só id e nome do registro), nome
  externo e as classes de plugin do provedor por operação; seção na nota da tabela e em `03 Integrações`;
  no grafo, tabela → provedor → assembly/classe. Tabelas elásticas entram na contagem.

Site maps e SLAs (coletores `sitemaps` e `slas`)
- Site maps: lidos com `isappaware eq true` e `eq false` — sem filtro a API só devolve o clássico (ESAB-TEST:
  1 × 29). Nota por site map com Área → Grupo → SubÁrea, destino (tabela, web resource, dashboard, URL
  externa) e privilégios; no grafo, app → site map → tabela/web resource/host.
- SLAs: itens com aviso/falha, KPI (`msdyn_slakpis`), horário comercial e as colunas citadas no FetchXML de
  aplicável quando / sucesso / pausa; no grafo, SLA → tabela, SLA → coluna (por condição/KPI) e SLA →
  workflow de ação. `dvinv onde-usa tabela.coluna` passa a mostrar os SLAs que dependem da coluna.
- Bases **Site Maps** e **SLAs**; números no `00 Índice`.
- Achados novos: **NAV-01** (subárea com URL absoluta de org Dynamics — médio se for de outro ambiente;
  no ESAB-TEST, 2 site maps do TEST apontam para `esab-dev`) e **SLA-01** (SLA sem item ou tabela com mais
  de um SLA ativo; no ESAB-TEST, 3 SLAs ativos em `incident`).

Vault enxuto
- Sem `extraido_em` por nota: re-extrair sem mudança no ambiente não altera os arquivos (diff do Git
  limpo). A data fica em `00 Índice`, `01 Ambiente` e `AGENT-INDEX`.
- `08 Matriz`: "sem dependentes" limitado a 300 linhas; dependências ausentes resumidas por solução, lista
  completa em `Dependências ausentes.csv` (com nomes do catálogo). No ESAB-TEST a nota tinha 10,8 MB.
- `04 Segurança` e notas de BU: até 300 equipes listadas, todas em `Equipes.csv`. Com mais de 1.000
  equipes estruturais, só as que têm papel ou grupo do Entra viram nota.
- Web resource de terceiros ou acima de 150 KB não tem o código na nota (fica no snapshot).

Correção (falso positivo desde a 0.5.0)
- A varredura final de segredos abortava a publicação de qualquer snapshot com assembly de plugin
  assinado: `"publickeytoken": "<16 hex>"` casava com a regra de nome terminado em `token`. Agora a
  varredura aplica a mesma exceção que a redação já tinha (`NOT_SENSITIVE_KEYS`), **só** para o nome exato e
  o formato fixo do .NET (16 hexadecimais); outro valor nesse campo ou outro `…token` continua barrando.
  Achado na validação real (ESAB-TEST, 6 assemblies); a org fictícia dos testes usava token de 3 caracteres.

Validação real (ESAB-TEST, 2026-10-14)
- Extração completa em 29 min de coleta + 8 min de render (sem 429); 120.942 componentes em 299 tipos (109 mil
  com nome), 844 mil dependências da plataforma, 72 mil equipes. Markdown do vault: 41 MB → 10,5 MB; maior
  nota 230 KB (antes 10,8 MB). Consultas ao índice: < 1 s e 170 a 5.600 tokens por resposta.
- Ajustes saídos da validação: equipes fora do corte de notas não entram no grafo nem na nota do papel (só
  no índice); arestas internas de metadados ("Solution Internal") fora da matriz; GUID não duplicado no
  índice + VACUUM; Complex Control (sem RetrieveMultiple) fora do catálogo de nomes.

Núcleo e extras (uso em dois passos: `escopo`, depois `all`)
- O **núcleo** sempre roda e traz tudo o que foi customizado e se está em uso: entram por padrão privilégios por
  papel, ribbons, definições de processos e uso de campos (antes opt-in em `deep.*`). Sem níveis nem fases.
- **Extras** de diagnóstico, opt-in por `extras:` no yaml ou `--extras`: `saude` (plugin trace), `armazenamento`,
  `historico` (histórico de soluções, antes sempre ligado), `camadas`, `binarios`, `usuarios` (dado pessoal).
  Acrescentar a um inventário pronto: `--extras <...> --completar ultimo` refaz só as etapas dos extras.
- Saem `--deep` e `nivel:`/`--nivel` (yaml antigo com `nivel:` gera aviso e é ignorado). `deep:` fica como ajuste
  fino; se desligar parte do núcleo (bloco `deep:` copiado do exemplo antigo), o `dvinv` avisa.
- Componentes da Microsoft fora do escopo por padrão (`scope.exclude_microsoft: true`): prefixos dos publishers
  da Microsoft (`msdyn_`, `mscrm_`, `adx_`… — `scope.MICROSOFT_PREFIXES`) não entram nem por solução do escopo;
  ribbons de tabelas da Microsoft não são lidos. Tabela da Microsoft com coluna do cliente continua pelas colunas.
  Plugins do produto: assembly assinado com chave da Microsoft (`publickeytoken` 31bf3856ad364e35 e as do .NET) ou
  nome `Microsoft.`/`Adxstudio.` sai com classes e steps, inclusive steps não gerenciados registrados pelos apps.
  No ESAB-TEST: 409 de 437 assemblies da org são da Microsoft (todos com o mesmo token, muitos sem "Microsoft" no
  nome); os de cliente e de terceiros (assinados com chave própria) continuam pelas regras de escopo.
  Colunas: coluna criada por app da Microsoft (`msdyn_…` na conta) ou com prefixo de `exclude_prefixes` deixa de
  contar como customizada — não põe tabela nativa no escopo e não entra no uso de campos (que já ignorava colunas
  de sistema e virtuais).
  `escopo` e `01 Ambiente` mostram quantos componentes o filtro descartou por prefixo.
- Notas de versão automáticas: cada render gera `Mudanças desde a extração anterior.md` e `Mudanças.csv`,
  comparando com o snapshot completo anterior do ambiente — componentes novos, removidos e alterados (com o que
  mudou) e achados novos, resolvidos, que melhoraram ou pioraram, com as ocorrências corrigidas; resumo no
  `00 Índice`. Etapa que falhou ou rodou com opções diferentes (inclusive nas etapas de que depende) vira "não
  comparado"; campos voláteis e campos novos de uma versão do kit não contam como mudança. Validado no ESAB-TEST:
  sem falso positivo entre extrações com opções e versões diferentes do kit.
- Varredura de segredos sem perder a extração: o JSON gravado junta strings vizinhas (`"Authorization",\n
  "Content-Type"`) e isso casava o padrão de header de autenticação — falso positivo que só existe no arquivo, e a
  varredura final descartava horas de coleta. Agora cada arquivo é conferido ao ser gravado: o que só casa na forma
  gravada é redigido ali (`redact_serialized`, o valor vizinho vira «REDACTED»; chaves não mudam); se ainda sobrar,
  perde-se só aquela etapa (status `falhou`, lacuna declarada) e a extração segue. A varredura final cobre só o que
  não passou por essa conferência (fontes decompiladas, manifesto) — sem varrer de novo os ~140 MB de catálogo e
  dependências (ESAB-TEST: ~2 min a menos na publicação).
- `$batch` (OData) nas etapas de uma leitura por item — ribbons, privilégios por papel (antes em sequência),
  colunas e chaves por tabela, fórmulas, dependências por solução/componente: 20 GETs por requisição
  (`deep.batch_size`; 0 desliga), pacotes em paralelo. Recusado pelo proxy/serviço → uma chamada por item.
  Validado no ESAB-TEST (respostas idênticas às chamadas avulsas). Uso de campos: tabelas medidas em paralelo.
- `dvinv escopo`: pré-visualização (tabelas no escopo por motivo e prefixo + CSV) sem extrair — 58 s no ESAB-TEST.

Extração por partes e novos componentes
- `--completar <snapshot>` (ou `completar:` no yaml): roda o `--only` — ou, sem ele, só o que falhou/faltou — e
  herda os demais coletores do snapshot base (só ok/parcial, sha256 conferido, mesmo escopo/perfil/ambiente; senão
  recusa). Origem por coletor no manifesto e no `01 Ambiente`. `collectors:` no yaml = `--only`. Comando
  `snapshots` lista os snapshots e quantas etapas cada um tem íntegras. ESAB-TEST: completar a extração de ontem
  com 3 etapas novas levou 226 s (completa: 37 min).
- Colunas calculadas, rollup e fórmula: definição lida por coluna (tipo derivado do metadado) e colunas usadas
  no grafo (`onde-usa` mostra quem calcula a partir do campo). ESAB-TEST: 212 em 16 s.
- Mapeamentos entre tabelas (`entitymaps`/`attributemaps`): campo de destino depende do de origem.
- Comandos modernos (`appactions`): nota por comando, botão → tabela/JS/app no grafo.
- Histórico de soluções (`msdyn_solutionhistories`): `10 Histórico de Soluções` + CSV; achado ALM-06 (falhas nos
  últimos 90 dias). Camadas de solução por tabela (`deep.solution_layers`, desligado): achado ALM-07 (camada
  `Active` por cima das gerenciadas).
- README da skill com o guia "Como usar".

Desempenho e acompanhamento em ambientes muito customizados
- Leituras em lote (`get_many`) agora em paralelo (`parallel`, padrão 4): conteúdo de web resources, formxml,
  views, definições de processos e manifestos PCF. Antes eram lotes em sequência — num cliente, só o conteúdo
  de web resources passou de 2 h.
- Web resources: conteúdo reaproveitado do snapshot anterior quando o `modifiedon` não mudou (com análise e
  achados de segredo); só HTML e JS baixados por padrão (`deep.webresource_content_types`); bibliotecas de
  terceiros reconhecidas pelo nome não são baixadas (`deep.webresource_skip_names`, `[]` desliga) — lacuna
  declarada. ESAB-TEST: 1ª rodada 133 baixados em 3 s; 2ª rodada 0 baixados, 133 reaproveitados.
- Progresso com tempo decorrido e estimativa do restante (a cada 10% ou 30 s) em toda etapa por lotes.
- Log de cada `extract`/`render`/`all` com hora por linha em `out/<ambiente>/logs/dvinv-<data>-<comando>.log`.
- O log registra **cada chamada à API** (caminho, status, tamanho, tempo); o console avisa tempo limite e nova
  tentativa (antes eram silenciosos e a etapa parecia travada); leituras paginadas longas mostram linhas/páginas
  lidas a cada 30 s.
- Web resources: a análise local do conteúdo (segredos, redação, funções, JS — ~2 s por MB) mostra progresso
  com MB processados e estimativa; sem achado na varredura, a redação não roda de novo (mesmo resultado, metade
  do tempo); acima de `deep.webresource_max_bytes` (1 MB) os segredos são verificados mas conteúdo e análise não
  são guardados (lacuna declarada). Num cliente com ~10 mil web resources a etapa ficou 20+ min sem mensagem
  depois do download em 100%.
- Metadados das tabelas em lotes de 40 (`EntityDefinitions` com `$filter` por MetadataId + `$expand`), em
  paralelo, com nova tentativa tabela a tabela: a API não pagina metadados, e a resposta única com todas as
  colunas da org caía no meio (`ConnectionAbortError`) num cliente grande — derrubando o coletor `tables`, que
  define o escopo dos demais. Vale para a fase 1 de `tables` e para o catálogo 3/4. ESAB-TEST: 63 lotes em 11 s.
- Catálogo: subetapas 1/4–4/4 com contagens e tempo; nomes por tipo lidos em paralelo (ESAB-TEST: 287 tipos,
  de ~15 min estimados em sequência para 48 s; coletor de 518 s para 147 s).
- Ao fim de cada coletor, uma linha "N na org · M no escopo · processado…"; no fim da extração, tabela-resumo
  (etapa, na org, no escopo, processado/detalhe, status, tempo) — a mesma tabela vai para `01 Ambiente`.

Navegação e grafo por tipo
- `Mapas/<Tipo>.md` (hub com todos os componentes do tipo e o que usam; filtros prontos para o Graph view; grafo
  local mostra o tipo inteiro) e `Mapas/<Tipo>.canvas` (mapa visual fixo). Campos agrupados por tabela.
- `.obsidian/graph.json` com cores por tipo, criado só se não existir. A Obsidian CLI não tem comandos de grafo
  (e exige o app aberto) — por isso a visualização vem em arquivos do vault.

Windows
- Arquivos do vault e da comparação gravados sempre com LF (no Windows o Python gravaria CRLF e o diff contra um
  vault gerado no Linux mostraria todos os arquivos).
- Troca da pasta do vault com novas tentativas quando o Windows a mantém em uso (antivírus, Explorer, editor);
  persistindo, mensagem clara e vault anterior intacto. `AGENTS.md`/`README.md` abertos num editor viram aviso.
- Aviso de caminho longo (limite de 260 caracteres) e `scripts/validar-windows.ps1` (testes offline, check, render
  duas vezes sem diff, CRLF, consultas `--vault`, `core.longpaths`). Testes P0 simulam os dois comportamentos.

Execução por mais de um agente
- `AGENTS.md` na raiz do vault com bloco gerado (`<!-- dvinv:inicio -->`…`<!-- dvinv:fim -->`): ambientes,
  `AGENT-INDEX.md`, config, comandos do índice e regras de execução; texto fora do bloco preservado.
  `CLAUDE.md` criado com `@AGENTS.md` só se não existir (senão, aviso). Mesma instrução para Claude e Copilot.
- Trava `out/<ambiente>/.dvinv.lock` em `extract`/`render`/`all` (Claude Code e Copilot no mesmo
  projeto não publicam snapshots concorrentes). `INSTALL.md` explica a instalação única da skill para os dois.

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
