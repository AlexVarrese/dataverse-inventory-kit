---
name: dataverse-inventory
description: Inventaria ambientes Dataverse/D365 num vault Obsidian.
version: 0.4.0
author: Alex Giovani Varrese (alexvarrese)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Dataverse, Dynamics 365, Power Platform, Inventário, Obsidian, Governança]
    related_skills: [dv-connect, dv-security, dv-metadata, dv-query, dv-solution, obsidian-markdown, obsidian-bases, json-canvas]
---

# Inventário de ambiente Dataverse → Obsidian

Levantamento **read-only** e repetível de tudo que um cliente customizou num ambiente Dataverse /
Dynamics 365 / Power Platform: tabelas, colunas, relacionamentos, formulários, views, web resources,
ribbons, plugins (assemblies, classes, steps, imagens), Custom APIs, service endpoints, workflows,
business rules, actions, BPFs, cloud/desktop flows, apps model-driven, canvas, agentes Copilot
Studio, variáveis de ambiente, referências de conexão, conectores, soluções, segurança (BUs, papéis,
privilégios, equipes, usuários) e saúde de execução (plugin trace). O resultado é um vault Obsidian
com uma nota por componente, Bases, um Canvas do ambiente e achados automáticos rastreáveis.

## Quando usar

- "Inventário / levantamento / assessment / documentar o ambiente / mapear customizações" de um CRM
  Dynamics 365 ou Dataverse.
- Gap TEST × PRD antes de go-live, ou "o que mudou desde o último levantamento" (comando `diff`).
- Preparar refactor, migração, upgrade, auditoria de segurança ou de dívida técnica.
- Não usar para **alterar** o ambiente: este pacote só faz GET. Mudanças → skills `dv-*`.

## Dependências (skills de terceiros)

- **Dataverse Skills** — [microsoft/Dataverse-skills](https://github.com/microsoft/Dataverse-skills) (`dv-*`):
  conexão, MCP e validação. Recomendada; sem ela o pacote funciona com `.env` manual (`INSTALL.md` §3A).
- **Obsidian Skills** — [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills): sintaxe
  correta de notas, Bases e Canvas na camada de análise.

| Etapa | Skill |
|---|---|
| Ferramentas, login, servidor MCP, `.env` | `dv-connect` |
| Application User do Service Principal + papel de leitura | `dv-security` |
| Extração, vault, achados, diff | **esta skill** (`dvinv`) |
| Validar achado de schema (coluna, relacionamento, form, view) | `dv-metadata` (ou MCP) |
| Contagens e uso de dados (volume, campo preenchido?) | `dv-query` |
| Exportar/descompactar solução para ler XML completo | `dv-solution` |
| Escrever análise, Bases e diagramas no vault | `obsidian-markdown`, `obsidian-bases`, `json-canvas` |

As skills `dv-*` podem **alterar** o ambiente; no contexto de inventário use-as só para leitura.

## Arquivos

- `scripts/dvinv.py` — CLI (`check`, `extract`, `render`, `all`, `diff`, `collectors`).
- `scripts/inventory.example.yaml` — configuração por cliente/ambiente.
- `README.md` (visão geral) e `INSTALL.md` (instalação, Service Principal e MCP).
- `references/collectors.md` — o que cada coletor lê e as armadilhas da Web API já resolvidas.
- `references/vault-schema.md` — estrutura do vault e properties de cada tipo de nota.
- `references/findings.md` — regras dos achados automáticos e como validá-los.
- `PROMPT.md` — prompt equivalente para agentes sem suporte a skills.

## Procedimento

1. **Pasta do cliente.** Trabalhe numa pasta própria por cliente (ex.: `inventario/<CLIENTE>/`). Copie
   `scripts/inventory.example.yaml` para lá como `inventory.yaml`. Um arquivo por ambiente
   (`inventory.prd.yaml`, `inventory.test.yaml`). Nunca reaproveite `.env`, prefixos ou saídas de
   outro cliente.
2. **Conexão.** Se `dv-connect` estiver disponível, use-a **na pasta do cliente**: ela instala o
   Dataverse CLI, faz o login, registra o MCP e grava o `.env` com as mesmas variáveis que o `dvinv`
   lê (`DATAVERSE_URL`, `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`). Para criar o Application User do
   SPN, use `dv-security`. Os dois caminhos ficam ativos (detalhes em `INSTALL.md`):
   - **Service Principal**: `.env` com `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`. Preferido para a
     extração completa: não-interativo e imune ao bloqueio de login interativo por Conditional Access.
   - **MCP**: `dataverse auth create --environment <url>` + servidor MCP `@microsoft/dataverse`
     registrado no agente. O extrator reusa o mesmo login (`auth.method: mcp`), e você usa as
     ferramentas MCP para consultas pontuais de validação.
   - `auth.method: auto` tenta SPN → MCP → Azure CLI → device code.
   - **Nunca** exiba, copie para o vault nem faça commit do conteúdo do `.env`.
3. **Validar acesso:** `python3 <skill>/scripts/dvinv.py check -c inventory.yaml`.
4. **Descobrir o escopo** se o usuário não souber o prefixo do publisher: rode
   `extract --only solutions` e leia `publisher_prefix` das soluções não gerenciadas em
   `<raw_dir>/<run_id>/solutions.json`. Confirme com o usuário prefixos e soluções antes da extração completa.
   Depois da 1ª extração, confira em `01 Ambiente.md` a tabela **De onde veio o escopo** (motivo ×
   prefixo): em ambientes de DEV/TEST, `include_unmanaged` costuma trazer milhares de bibliotecas de
   terceiros — mova o prefixo para `scope.exclude_prefixes`, e prefixos do próprio cliente (ex. de outra
   operação regional) para `scope.prefixes`.
5. **Extrair:** `python3 <skill>/scripts/dvinv.py extract -c inventory.yaml`. `--deep` liga tudo:
   privilégios por papel, ribbons, plugin trace, membros de equipe, DLLs de plugin, definições de
   processos, **uso de campos** (preenchimento real + matriz + candidatos a investigação de remoção) e **armazenamento/
   auditoria**. Se o cliente tiver repositórios Git, liste os clones locais em `repos:` — o pacote compara
   web resources e classes de plugin publicados com o código, procura segredos versionados e usa o
   código como evidência de uso de campo. Com `ilspycmd` no PATH e `plugin_binaries`, compara métodos
   da DLL de produção com o repo. Coletores
   que falham (403, coluna inexistente) viram **lacunas declaradas** no manifest — não aborte;
   reporte-as. Cada extração vira um snapshot imutável `<raw_dir>/<run_id>/` (manifesto com status por
   coletor e sha256 por arquivo); coletor que falhou não tem arquivo e nunca herda dado de outra extração.
   BUs, equipes (com papéis) e componentes PCF entram sempre; a lista nominal de usuários só com
   `deep.users` (dado pessoal — confirme com o usuário antes de ligar).
   Se o cliente só autoriza leitura de metadados, use `--profile metadata_only` (ou `profile: metadata_only`
   no YAML): conteúdo de web resources/flows/processos, binários, uso de campos, storage, plugin trace e
   membros de equipe ficam desligados mesmo com `--deep`.
   A **matriz de dependências** (componente × componente) é gerada sempre a partir do que foi
   coletado; `deep.platform_dependencies` acrescenta o que o próprio Dataverse registra
   (RetrieveDependentComponents) e as dependências ausentes de cada solução (RetrieveMissingDependencies).
6. **Renderizar:** `python3 <skill>/scripts/dvinv.py render -c inventory.yaml` (último snapshot íntegro;
   `--snapshot <run_id>` para outro). Achados e dependências vão para `_derived/<run_id>/`. Re-renderizar é
   seguro: o conteúdo abaixo do marcador `%% dvinv:manual … %%` e as properties `status`,
   `responsavel`, `decisao`, `prazo` e `revisado` são preservados.
7. **Camada de análise (o seu trabalho, não do script).**
   - Leia `00 Índice.md`, `01 Ambiente.md` (lacunas!) e cada nota em `Achados/`.
   - Para análise de impacto ("o que quebra se eu mudar X?"), use `08 Matriz de Dependências.md`, a seção
     **Dependências** (depende de / usado por) de cada nota e `Matriz de Dependências.xlsx` para o cliente.
   - Valide cada achado antes de afirmá-lo: abra o JS, consulte via MCP, `dv-metadata` (schema) ou
     `dv-query` (dados), use `dv-solution` para exportar a solução quando precisar do XML, e cruze com o
     repositório de código quando houver. Vários achados são **candidatos** (ver `references/findings.md`).
   - Escreva conclusões **abaixo do marcador manual** das notas, usando a skill
     `obsidian-markdown` (wikilinks, callouts, properties). Para visões novas, use `obsidian-bases`;
     para diagramas, `json-canvas`.
   - Atualize `status` do achado (`aberto` → `confirmado`/`falso-positivo`/`resolvido`).
8. **Comparar ambientes/snapshots:** `dvinv.py diff -c inventory.yaml --a out/PRD/_raw --b out/TEST/_raw`
   (raiz → último snapshot íntegro de cada lado) ou `--a <run_id> --b <run_id>` para duas datas do mesmo
   ambiente. Snapshots antigos ficam em `_raw/<run_id>/`; apague os que não precisar mais.
9. **Reportar ao usuário:** números do índice com a fonte (`_raw/<run_id>/*.json`), achados críticos e altos
   já validados, lacunas e próximos passos. Sem números digitados à mão nem score decorativo.

## Regras

- Read-only por design: o cliente HTTP só tem GET. Não use este pacote como pretexto para escrever no ambiente.
- Segredos encontrados (SAS `sig=`, `code=`/`api_key=`/`access_token=` em URL, headers
  `x-functions-key`/`Ocp-Apim-Subscription-Key`/`Authorization`, `user:senha@host`, senhas, tokens, JWT)
  são **redigidos** antes de qualquer gravação, inclusive em lacunas e mensagens de erro. Se a varredura
  final achar algo, o comando falha e nada é publicado: reporte o erro, não contorne. Relate onde estão,
  nunca o valor, e recomende rotação.
- Uso de campos: `candidato-seguro` significa **candidato a investigação de remoção**, nunca "pode
  remover". `inconclusivo` = amostra parcial ou fonte da matriz ausente/falha — não recomende remoção.
- Os números valem para o escopo configurado; deixe isso explícito. Cloud flows fora de solução não
  aparecem na Web API do Dataverse (lacuna permanente; use o Power Platform admin/PAC CLI).
- Escrever num vault existente do cliente (com git): peça autorização, mostre o diff e não faça commit sem pedido.

## Verificação

- `python3 <skill>/tests/test_pipeline.py` roda o pipeline inteiro contra uma org fictícia (sem rede)
  e valida frontmatter, wikilinks, Bases, Canvas, redação de segredos e preservação de edições.
- `python3 <skill>/tests/test_regressao_p0.py` cobre snapshots isolados, redação/varredura de segredos,
  classificação de uso de campos, validação de nextLink, cache de token e o perfil `metadata_only`.
- Após uma extração real, confira em `01 Ambiente.md` a tabela "Escopo × organização" e as lacunas.
