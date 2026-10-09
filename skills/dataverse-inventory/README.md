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
| Ambiente e ALM | versão, organização, auditoria, plugin trace, soluções e componentes por solução, publishers, variáveis de ambiente, referências de conexão, conectores customizados |
| Dados | tabelas custom e nativas customizadas, colunas (tipo, obrigatoriedade, auditoria), chaves alternativas, relacionamentos 1:N/N:N, option sets globais, contagem de registros |
| Interface | formulários (bibliotecas JS, handlers por evento/campo, controles PCF por coluna), views, web resources (com código-fonte redigido e funções), botões de ribbon\*, apps model-driven, canvas apps, agentes Copilot Studio |
| Componentes PCF | controles de código (`customcontrols`): manifesto (namespace, versão, tipo, propriedades, Web API, domínios externos), formulários e colunas onde são usados |
| Código e integração | plugin assemblies, classes, steps, imagens, binários\*, Custom APIs (parâmetros/respostas), service endpoints (webhook/Service Bus) |
| Automação | workflows clássicos, business rules, actions, BPFs, cloud flows (gatilho, conectores, hosts HTTP, tabelas tocadas), desktop flows, proprietário e status |
| Segurança | árvore de BUs (nota por BU), papéis (deduplicados pela BU raiz) e a quem estão atribuídos, privilégios por tabela\*, equipes com papéis (nota por equipe) e membros\*, perfis de segurança de campo, contagem de usuários por tipo e BU; lista nominal de usuários (nome, UPN, BU, licença, papéis, equipes)\* com `deep.users` |
| Saúde | plugin trace log: execuções, erros e tempo por classe\* |
| Uso de campos\* | preenchimento real por coluna, matriz de uso (forms ativos e inativos, eventos, views, processos, flows, plugins, JS, repo), candidatos a investigação de remoção e inconclusivos |
| Qualidade de código | JS: APIs obsoletas (Xrm.Page, SOAP 2011), eval, XHR síncrono, libs legadas, hosts fixos · flows: estrutura, condições, tratamento de erro |
| Armazenamento e auditoria\* | maiores tabelas, notas e anexos de e-mail (qtd, bytes, tipos), auditoria por tabela/ação e retenção efetiva |
| Dependências | matriz componente × componente: quem depende de cada tabela (forms, JS, plugins, business rules, workflows, BPFs, flows, Custom APIs, apps, papéis, tabelas filhas), tipo × tipo, mais dependidos, externos, órfãos; dependências registradas pela plataforma e ausentes por solução\*; exportação em Excel e CSV |
| Repositórios Git (`repos:`) | web resources e classes de plugin × código versionado, métodos só em produção (DLL decompilada), segredos no repo |

\* com `--deep` ou a chave correspondente em `deep:` no YAML.

## O que é gerado no vault

```
<vault>/Dataverse/<AMBIENTE>/
  00 Índice.md          números (escopo × org), achados, Bases embutidas
  01 Ambiente.md        versão, critério de escopo, LACUNAS declaradas, soluções, tempos
  03 Integrações.md     conectores, hosts HTTP, endpoints, Custom APIs, variáveis, conexões
  04 Segurança.md       árvore de BUs, papéis, equipes, perfis de campo
  05 Uso de Campos.md   preenchimento × uso, candidatos a investigação, inconclusivos (deep.field_usage)
  06 Armazenamento e Auditoria.md                                          (deep.storage)
  07 Repositórios.md    ambiente × Git: divergências, sem fonte, segredos  (repos:)
  08 Matriz de Dependências.md   impacto por tabela, tipo × tipo, órfãos, externos
  Matriz de Dependências.xlsx    matriz por tabela, arestas, tipo × tipo, componentes (fan-in/out)
  Dependências.csv               lista de arestas (A depende de B)
  Tabelas/  Plugins/  Plugin Steps/  Processos/<categoria>/  Web Resources/  Componentes PCF/
  Custom APIs/  Apps/  Soluções/  Papéis/  Business Units/  Equipes/  Usuários/ (deep.users)
  Achados/  Comparações/
  Bases/                Tabelas · Automações · Plugin Steps · Web Resources · Achados · Componentes ·
                        Componentes PCF · Segurança (BUs, equipes, usuários)
  Mapa do Ambiente.canvas
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
(ALM-01). Lista completa em [`references/findings.md`](references/findings.md).

## Uso rápido

```bash
SKILL=<pasta onde a skill foi instalada>     # ver INSTALL.md §2
mkdir -p inventario/CLIENTE && cd inventario/CLIENTE
cp $SKILL/scripts/inventory.example.yaml inventory.yaml     # ajustar url, prefixos, saída
python3 $SKILL/scripts/dvinv.py check   -c inventory.yaml   # testa a conexão
python3 $SKILL/scripts/dvinv.py all     -c inventory.yaml   # extrai + gera o vault
python3 $SKILL/scripts/dvinv.py extract -c inventory.yaml --deep
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
      bin/  decompiled/         DLLs/decompilados de plugin (deep.plugin_binaries)
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

Instalação, Service Principal e MCP: **[INSTALL.md](INSTALL.md)**.
Com agente (Hermes/Claude Code): basta pedir "faça o inventário do ambiente X". A skill
[`SKILL.md`](SKILL.md) conduz o processo. Para outros agentes, use [`PROMPT.md`](PROMPT.md).

## Garantias

- **Somente leitura**: o cliente HTTP só implementa GET.
- **Segredos redigidos** antes de qualquer gravação (raw, fontes decompiladas, notas, planilhas, mensagens
  de erro e lacunas). O achado informa onde está o segredo, nunca o valor. Varredura final fail-closed:
  se algum padrão de segredo aparecer numa saída, o snapshot/vault não é publicado.
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
  references/  collectors.md  vault-schema.md  findings.md
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
