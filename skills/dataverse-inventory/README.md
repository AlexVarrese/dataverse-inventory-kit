# dataverse-inventory (`dvinv`)

Pacote + skill para **inventariar ambientes Microsoft Dataverse / Dynamics 365 / Power Platform** e
publicar o resultado como um **vault Obsidian** navegável, seguindo as convenções de
[kepano/obsidian-skills](https://github.com/kepano/obsidian-skills): Obsidian Flavored Markdown,
Bases e JSON Canvas.

É genérico: tudo que é específico de um cliente fica num `inventory.yaml` e num `.env`, na pasta do cliente.

```
Dataverse Web API (GET)  ──►  _raw/<run_id>/*.json + manifest  ──►  vault Obsidian
  SPN ou login do MCP          snapshot imutável, redigido,          notas · Bases · Canvas · achados
                               sha256 por arquivo                    (+ _derived/<run_id>/)
```

## O que é coletado

| Família | Componentes |
|---|---|
| Ambiente e ALM | versão, organização, auditoria, plugin trace\*, soluções e componentes por solução, publishers, variáveis de ambiente, referências de conexão, conectores customizados |
| Dados | tabelas custom e nativas customizadas, colunas (tipo, obrigatoriedade, auditoria), chaves alternativas, relacionamentos 1:N/N:N, option sets globais, contagem de registros |
| Interface | formulários (bibliotecas JS, handlers por evento/campo, controles PCF por coluna), views, web resources (com código-fonte redigido e funções), botões de ribbon, apps model-driven, canvas apps, agentes Copilot Studio |
| Componentes PCF | controles de código (`customcontrols`): manifesto (namespace, versão, tipo, propriedades, Web API, domínios externos), formulários e colunas onde são usados |
| Código e integração | plugin assemblies, classes, steps, imagens, binários\*, Custom APIs (parâmetros/respostas), service endpoints (webhook/Service Bus) |
| Automação | workflows clássicos, business rules, actions, BPFs, cloud flows (gatilho, conectores, hosts HTTP, tabelas tocadas), desktop flows, proprietário e status |
| Segurança | árvore de BUs (nota por BU), papéis (deduplicados pela BU raiz) e a quem estão atribuídos, privilégios por tabela, equipes com papéis (nota por equipe) e membros\*, perfis de segurança de campo, contagem de usuários por tipo e BU; lista nominal de usuários (nome, UPN, BU, licença, papéis, equipes)\* com o extra `usuarios` |
| Saúde | plugin trace log: execuções, erros e tempo por classe\* |
| Uso de campos | preenchimento real por coluna, matriz de uso (forms ativos e inativos, eventos, views, processos, flows, plugins, JS, repo), candidatos a investigação de remoção e inconclusivos |
| Qualidade de código | JS: APIs obsoletas (Xrm.Page, SOAP 2011), eval, XHR síncrono, libs legadas, hosts fixos · flows: estrutura, condições, tratamento de erro |
| Armazenamento e auditoria\* | maiores tabelas, notas e anexos de e-mail (qtd, bytes, tipos), auditoria por tabela/ação e retenção efetiva |
| Dependências | matriz componente × componente: quem depende de cada tabela (forms, JS, plugins, business rules, workflows, BPFs, flows, Custom APIs, apps, papéis, tabelas filhas), tipo × tipo, mais dependidos, externos, órfãos; dependências registradas pela plataforma e ausentes por solução; exportação em CSV |
| Repositórios Git (`repos:`) | web resources e classes de plugin × código versionado, métodos só em produção (DLL decompilada), segredos no repo |

\* extras (fora do núcleo): `extras:` no YAML ou `--extras` — ver *Como usar*. Todo o resto é o núcleo e sempre roda.

## O que é gerado no vault

```
<vault_root>/
  AGENTS.md  CLAUDE.md          instruções para agentes (bloco dvinv + texto do time)
  README.md  .gitignore  .gitattributes   blocos dvinv quando output.repo: true
  Dataverse/<AMBIENTE>/
    AGENT-INDEX.md        entrada para agentes: mapa, receitas de busca, o que não ler inteiro
    00 Índice.md          números (escopo × org), achados, Bases embutidas
    01 Ambiente.md        versão, critério e origem do escopo, status por etapa, LACUNAS, soluções, tempos
    03 Integrações.md     conectores, hosts HTTP, endpoints, Custom APIs, variáveis, conexões
    04 Segurança.md       árvore de BUs, papéis, matriz papel × tabela, equipes, perfis de campo
    05 Uso de Campos.md   preenchimento × uso, candidatos a investigação, inconclusivos
    06 Armazenamento e Auditoria.md                                (extra armazenamento)
    07 Repositórios.md    ambiente × Git: divergências, sem fonte, segredos (repos:)
    08 Matriz de Dependências.md   impacto por tabela, tipo × tipo, mais dependidos, órfãos, externos
    09 Catálogo de Componentes.md  100% dos componentes das soluções, por tipo
    10 Histórico de Soluções.md    importações e falhas (extra historico)
    Componentes.csv       um componente por linha (chave, tipo, nome, GUID, escopo, nota)
    Dependências.csv      uma linha por relação "A depende de B" — abre no Excel
    Dependências ausentes.csv  Sem dependentes.csv  Equipes.csv
    Tabelas/  Plugins/  Plugin Steps/  Processos/<categoria>/  Web Resources/  Componentes PCF/
    Custom APIs/  Apps/  Site Maps/  SLAs/  Comandos/  Soluções/  Papéis/  Business Units/  Equipes/
    Usuários/ (extra usuarios)  Achados/  Comparações/
    Mapas/                um hub + um canvas por tipo de componente
    Bases/                Tabelas · Automações · Plugin Steps · Web Resources · Achados · Componentes ·
                          Componentes PCF · Segurança · Site Maps · SLAs
    Mapa do Ambiente.canvas
    Notas fora do inventário atual.md   notas com curadoria de componentes que deixaram de existir
    Mudanças desde a extração anterior.md  Mudanças.csv   notas de versão: o que entrou, saiu e mudou
```

Cada nota tem properties tipadas (`tipo`, `ambiente`, `tabela`, `ativo`, `modo`…) que alimentam as
Bases. Os links usam o caminho completo, então vários ambientes convivem no mesmo vault. Abaixo do
marcador `%% dvinv:manual … %%` fica o espaço do analista, preservado entre extrações.

## Achados automáticos

São regras derivadas de levantamentos reais, cada uma com contagem e evidência apontando para o snapshot (`_raw/<run_id>/`).
Exemplos: segredo em texto claro (SEC-01); biblioteca JS inexistente referenciada em formulário ou
botão (UI-01); handler sem função (UI-02); step de Update sem filtering attributes (PLG-01); plugin com
taxa de erro ≥ 5% (PLG-04); BPFs concorrentes (PRC-01); automação de dono desativado (PRC-02); tabela
custom sem privilégio de leitura (SEG-01); equipe gigante (SEG-02); variável de ambiente sem valor
(ALM-01). Lista completa em [`references/findings.md`](references/findings.md); a prática do Microsoft Learn que
cada regra verifica, em [`references/boas-praticas.md`](references/boas-praticas.md).

## Prompts para o time

A cada render, `prompts/*.prompt.md` e o catálogo de boas práticas são copiados para `.github/prompts/` da raiz do
vault, e o `AGENTS.md` cita os comandos. Não precisa extrair de novo para recebê-los.

| Comando | Quando usar |
|---|---|
| `/impacto-evolutiva` | antes de desenvolver: o que a mudança afeta, o que ajustar e testar |
| `/diagnostico-erro` | erro em HML ou PRD: causa provável e o que verificar |
| `/revisao-performance` | lentidão ou revisão periódica: gargalos prováveis e ganho esperado |
| `/melhorias-arquitetura` | planejamento: melhorias priorizadas por risco e esforço |
| `/planejar-evolucao` | requisito novo: o que reaproveitar antes de construir |

Para gravar ou atualizar só os prompts, sem render: `dvinv prompts --vault <raiz do vault>`. O render e esse comando
mostram quantos prompts estão em `.github/prompts/` e qual cópia da skill está em uso (`skill em uso:`); se aparecer
um caminho antigo, é essa cópia que precisa ser atualizada.

No Copilot (VS Code), abra a pasta raiz do vault e digite o comando no Chat em modo Agent. Em outros agentes, peça
para seguir o arquivo. Toda resposta segue [`references/padrao-prompts.md`](references/padrao-prompts.md). Prompt do
time: copie `prompts/_modelo.prompt.md` para `.github/prompts/` com outro nome; o render não mexe nele.

## Uso rápido

```bash
SKILL=<pasta onde a skill foi instalada>     # ver INSTALL.md §2
mkdir -p inventario/CLIENTE && cd inventario/CLIENTE
cp $SKILL/scripts/inventory.example.yaml inventory.yaml     # ajustar url, prefixos, saída
python3 $SKILL/scripts/dvinv.py check   -c inventory.yaml   # testa a conexão
python3 $SKILL/scripts/dvinv.py escopo  -c inventory.yaml   # confere quantas tabelas entram (sem extrair)
python3 $SKILL/scripts/dvinv.py all     -c inventory.yaml   # extrai + gera o vault
python3 $SKILL/scripts/dvinv.py diff    -c inventory.yaml --a out/PRD/_raw --b out/TEST/_raw
python3 $SKILL/scripts/dvinv.py all     -c inventory.yaml --profile metadata_only   # só metadados
```

## Estrutura de saída

```
out/<AMBIENTE>/
  _raw/                         raiz dos snapshots (output.raw_dir)
    20261009T203200Z/           um snapshot por extração (run_id em UTC); vários no mesmo dia coexistem
      manifest.json             run_id, perfil, status por coletor, arquivos + sha256, lacunas, chamadas
      tables.json  forms.json … um arquivo por coletor que terminou (coletor que falhou não tem arquivo)
      bin/  decompiled/         DLLs/decompilados de plugin (extra binarios)
    .staging-<run_id>-*/        extração em andamento — ignorada; removida se a extração falhar
  _derived/                     saídas do render por snapshot (output.derived_dir)
    20261009T203200Z/findings.json  dependencies.json
```

- `extract` grava em `.staging-*` e só publica (rename atômico) no fim, depois de varrer segredos.
- `render` usa o último snapshot **íntegro** (sha256 confere) ou `--snapshot <dir|run_id>`; lê só os
  arquivos listados no manifesto. Renderiza numa cópia de trabalho e troca a pasta do vault só se a
  varredura de segredos passar.
- `diff --a/--b` aceita diretório de snapshot, raiz `_raw` (último íntegro) ou run_id; recalcula as
  dependências a partir do snapshot.
- Pastas do formato antigo (≤ 0.4, JSON solto em `_raw/` e `_raw-AAAA-MM-DD/`) ainda podem ser lidas
  por `render --snapshot`/`diff`, com aviso: não têm verificação de integridade.

## Como usar (resumo prático)

Comandos a partir da pasta do cliente (onde está o `inventory.yaml`). No Windows sem PowerShell, use o CMD e o
Python do venv (ex. `C:\temp\dvinv-venv\Scripts\python.exe`); `<skill>` = pasta da skill.

**Dois passos:**

| Passo | Comando | O que faz |
|---|---|---|
| 1. Conferir o escopo | `python <skill>/scripts/dvinv.py escopo -c inventory.yaml` | ~1 min, sem extrair: quantas tabelas/soluções entram e por quê (CSV em `out/<amb>/escopo-*.csv`). Escopo largo demais = extração lenta: ajuste `scope:` antes |
| 2. Inventariar | `python <skill>/scripts/dvinv.py all -c inventory.yaml` | extrai o **núcleo** e gera o vault |

O **núcleo** sempre roda e traz tudo o que foi customizado e se está em uso: tabelas, colunas, fórmulas, chaves,
relacionamentos, option sets, mapeamentos, formulários (eventos e PCF), views, web resources (com código), ribbons,
comandos, apps, site maps, processos (com definição), plugins, Custom APIs, service endpoints, SLAs, variáveis e
conexões, papéis (com privilégios), catálogo de 100% dos componentes das soluções, dependências da plataforma,
contagem de registros e preenchimento real dos campos.

**Se cair no meio** (proxy, tempo limite): o mesmo comando com `--completar ultimo` refaz só o que falhou e herda o
resto. Um vault por ambiente, sempre do último snapshot — não há passo de consolidação.

**Extras** (diagnóstico, desligados): `--extras historico,saude` ou `extras: [historico, saude]` no yaml. Para
acrescentar a um inventário pronto sem refazer o núcleo: `all -c inventory.yaml --extras <...> --completar ultimo`.

| Extra | O que traz |
|---|---|
| `saude` | plugin trace dos últimos `plugin_trace_days` dias: execuções, erros, tempo |
| `armazenamento` | maiores tabelas, anexos, auditoria (lê tabelas de dados) |
| `historico` | importações/exportações/upgrades de soluções e falhas |
| `camadas` | camadas de solução por tabela (~3 s por tabela) |
| `binarios` | DLLs dos plugins (para decompilar) |
| `usuarios` | lista nominal de usuários e membros de equipe — **dado pessoal**: só com autorização do cliente |

**Componentes da Microsoft** (`msdyn_`, `mscrm_`, `adx_`… — lista em `scripts/dvinv/scope.py`) ficam fora do
escopo por padrão, mesmo dentro de uma solução do escopo, e o ribbon das tabelas deles não é lido. Plugins do
produto saem pela chave de assinatura do assembly (a mesma em qualquer ambiente) ou pelo nome `Microsoft.`/
`Adxstudio.`, com classes e steps. Colunas criadas pelos apps da Microsoft (`msdyn_…` na conta) e as de
`exclude_prefixes` não contam como customizadas: não trazem a tabela para o escopo nem entram no uso de campos. Tabela da
Microsoft com coluna do cliente continua no inventário pelas colunas. Para incluir: `scope.exclude_microsoft: false`.

O perfil `metadata_only` (`profile:` no yaml) continua valendo por cima de tudo: trava a leitura de conteúdo e
registros (código, definições, uso de campos, extras de dados), mesmo que o núcleo ou um extra peça.

**Outros comandos:**

| Objetivo | Comando |
|---|---|
| Testar acesso | `... check -c inventory.yaml` |
| Só gerar o vault de novo | `... render -c inventory.yaml` |
| Ver os snapshots e o que cada um tem | `... snapshots -c inventory.yaml` |
| Rodar só algumas etapas | `... extract -c inventory.yaml --only webresources,forms --completar ultimo` |
| Consultar impacto (sem config, em qualquer clone do vault) | `... onde-usa account.campo --vault Dataverse/<AMB>` · `impacto` · `depende` · `buscar` |
| Comparar ambientes/datas | `... diff -c inventory.yaml --a <snapshot> --b <snapshot>` |

**Semana seguinte (versionamento):** rode o mesmo `all`. Cada execução vira um snapshot novo (os anteriores ficam
em `_raw/`) e o vault é regenerado no lugar, preservando o que o time escreveu e o status dos achados:

- **`Mudanças desde a extração anterior`** (e `Mudanças.csv`): compara com o snapshot completo anterior do mesmo
  ambiente — componentes novos, removidos e alterados (dizendo o que mudou: código, colunas, filtro, versão,
  soluções, data de modificação…) e achados novos, resolvidos, que melhoraram ou pioraram, com as ocorrências
  corrigidas. O `00 Índice` traz o resumo. Tipo cuja etapa falhou ou rodou com opções diferentes aparece como
  "não comparado", para nada virar "resolvido" por engano; campos que só uma versão do kit grava não contam.
- **Git:** a saída é estável, então o `git diff` do repositório mostra só o que mudou no ambiente; o histórico de
  commits é o versionamento semana a semana.
- **`diff`:** para comparar quaisquer dois snapshots (outras datas ou HML × PRD), com nota em `Comparações/`.

**Acompanhar:** o log com hora e cada chamada à API fica em `out/<ambiente>/logs/dvinv-<data>-<comando>.log`.
Cada etapa termina com "N na org · M no escopo · processado"; etapas em lote mostram `N/total · ~restante`;
tempo limite e nova tentativa aparecem na tela; o fim da extração traz a tabela-resumo (também no `01 Ambiente`).

**Ajuste fino (avançado):** `parallel: 3` (2–4 em VDI/proxy), e as chaves `deep:` comentadas no fim do
`inventory.example.yaml` (tipos de web resource baixados, tetos de linhas, `batch_size` etc.).

**Rede lenta:** as etapas de uma leitura por item (ribbons, privilégios por papel, colunas e chaves por tabela,
fórmulas, dependências por solução) vão em pacotes `$batch` de 20 leituras por requisição — poucas idas e voltas
pelo proxy. Se o proxy ou o serviço recusarem o `$batch`, o kit avisa e segue uma chamada por item. Num host
perto do datacenter o ganho é pequeno (o serviço executa as leituras do pacote em sequência); `deep.batch_size: 0`
desliga.

Instalação, Service Principal e MCP: **[INSTALL.md](INSTALL.md)**.
Com agente (Hermes/Claude Code): basta pedir "faça o inventário do ambiente X". A skill
[`SKILL.md`](SKILL.md) conduz o processo. Para outros agentes, use [`PROMPT.md`](PROMPT.md).

## Garantias

- **Somente leitura**: o cliente HTTP só implementa GET.
- **Segredos redigidos** antes de qualquer gravação (raw, fontes decompiladas, notas, mensagens de erro e
  lacunas), também quando escritos com entidades HTML. O achado informa onde está o segredo, nunca o valor.
  Cada arquivo é conferido ao ser gravado; o que só aparece na forma gravada é redigido ali. Se algo não puder
  ser limpo, perde-se só aquela etapa ou arquivo (lacuna declarada; nota vira aviso) — a extração e o vault
  nunca são descartados inteiros, e nenhum valor chega ao disco em claro.
- **Snapshots imutáveis**: cada extração tem seu `run_id`; nada de outra extração é reaproveitado.
- **Lacunas declaradas**: o que falhou ou não é visível pela API vai para o manifest (status por coletor)
  e para `01 Ambiente.md`.
- **Token só para o ambiente configurado**: `@odata.nextLink` e URLs absolutas precisam ser https e do
  mesmo host; senão a chamada é recusada antes de anexar o Bearer.
- **Perfil `metadata_only`**: trava em desligado toda coleta que lê conteúdo ou registros (ver INSTALL §5).
- **Rastreável**: `manifest.json` lista cada chamada feita (caminho, status, linhas, ms).
- **Testado offline**: `python3 tests/test_pipeline.py` e `python3 tests/test_regressao_p0.py` (org fictícia, sem rede).

## Estrutura do pacote

```
dataverse-inventory/
  SKILL.md  README.md  INSTALL.md  PROMPT.md
  references/  collectors.md  vault-schema.md  findings.md  indice.md  boas-praticas.md  padrao-prompts.md
  prompts/     _modelo.prompt.md e os cinco prompts copiados para .github/prompts/ do vault
  scripts/
    dvinv.py                   launcher da CLI
    inventory.example.yaml     configuração modelo
    requirements.txt
    dvinv/
      auth.py                  SPN · MCP (cache do Dataverse CLI) · Azure CLI · device code
      client.py                GET-only, paginação, retry 429/5xx, log de chamadas
      config.py  scope.py  secrets.py  snapshot.py  findings.py  diff.py  cli.py
      collectors/              environment, tables, ui, apps, code, processes, alm, security,
                               repos, usage, storage, health
      render/                  obsidian (notas), bases (.base), canvas (.canvas)
  tests/test_pipeline.py  tests/test_regressao_p0.py
```
