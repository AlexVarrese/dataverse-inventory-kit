# Dataverse Inventory Kit

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/github/v/tag/AlexVarrese/dataverse-inventory-kit?label=vers%C3%A3o)](https://github.com/AlexVarrese/dataverse-inventory-kit/tags)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-compat%C3%ADvel-7c3aed)](https://agentskills.io/specification)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)](skills/dataverse-inventory/scripts/requirements.txt)

Skill + pacote Python (`dvinv`) para fazer o **inventário read-only de ambientes Microsoft Dataverse /
Dynamics 365 / Power Platform** e publicar o resultado como um **vault Obsidian**. O vault traz uma
nota por componente, Bases, um Canvas do ambiente, achados automáticos e comparação entre ambientes.

Segue a [especificação Agent Skills](https://agentskills.io/specification) e funciona com Hermes,
Claude Code, Codex, GitHub Copilot, Cursor e qualquer agente compatível. Para agentes sem suporte a
skills, há um prompt equivalente.

## O que você recebe

- **Uma nota por componente**: tabelas, colunas, formulários, views, web resources, plugins, Custom APIs,
  workflows, business rules, BPFs, cloud flows, apps, agentes Copilot Studio, papéis, soluções, variáveis
  de ambiente e conexões, com properties para Obsidian Bases.
- **Matriz de dependências entre componentes**: quem depende de cada tabela (formulários, JS, plugins,
  processos, flows, Custom APIs, apps, papéis, tabelas filhas), seção *Depende de / Usado por* em cada
  nota e **`Matriz de Dependências.xlsx`** para entregar ao cliente. Opcionalmente inclui as dependências
  registradas pelo próprio Dataverse e as que faltam em cada solução.
- **Uso de campos**: preenchimento real × onde cada coluna é usada, com candidatos seguros a remoção.
- **Achados automáticos** (segredos expostos, JS quebrado ou obsoleto, plugins sem filtro, automações
  de dono desativado, drift entre ambiente e repositório Git, soluções com dependências faltando…).
- **Comparação** entre ambientes (TEST × PRD) ou entre datas.

## Instalação rápida

```bash
# 1. esta skill
npx skills add alexvarrese/dataverse-inventory-kit --skill dataverse-inventory -g
# 2. dependências recomendadas
npx skills add microsoft/Dataverse-skills -s "*" -g          # conexão, MCP, validação (dv-*)
npx skills add kepano/obsidian-skills -g --skill obsidian-markdown --skill obsidian-bases --skill json-canvas --skill obsidian-cli
# 3. Python
pip install -r <pasta-da-skill>/scripts/requirements.txt
```

Claude Code via marketplace: `/plugin marketplace add alexvarrese/dataverse-inventory-kit` e depois
`/plugin install dataverse-inventory@dataverse-inventory-kit`.

Passo a passo completo, incluindo **Service Principal** e **MCP** (os dois ficam ativos):
[`skills/dataverse-inventory/INSTALL.md`](skills/dataverse-inventory/INSTALL.md).

## Uso

Com o agente, na pasta do cliente: *"Conecte no Dataverse `https://<org>.crm.dynamics.com` e faça o
inventário do ambiente no Obsidian"*.

Direto pela CLI:

```bash
cp <pasta-da-skill>/scripts/inventory.example.yaml inventory.yaml     # url, prefixos, saída
python3 <pasta-da-skill>/scripts/dvinv.py check -c inventory.yaml
python3 <pasta-da-skill>/scripts/dvinv.py all   -c inventory.yaml
```

Documentação: [README da skill](skills/dataverse-inventory/README.md) ·
[INSTALL](skills/dataverse-inventory/INSTALL.md) ·
[SKILL.md](skills/dataverse-inventory/SKILL.md) ·
[PROMPT](skills/dataverse-inventory/PROMPT.md) ·
[coletores](skills/dataverse-inventory/references/collectors.md) ·
[esquema do vault](skills/dataverse-inventory/references/vault-schema.md) ·
[achados](skills/dataverse-inventory/references/findings.md)

## Dependências

| Componente | Origem | Obrigatório? |
|---|---|---|
| Python 3.10+ e `scripts/requirements.txt` | PyPI | sim |
| Dataverse Skills (`dv-connect`, `dv-security`, `dv-metadata`, `dv-query`, `dv-solution`) | [microsoft/Dataverse-skills](https://github.com/microsoft/Dataverse-skills) | recomendado (conexão/MCP/validação) |
| Obsidian Skills (`obsidian-markdown`, `obsidian-bases`, `json-canvas`, `obsidian-cli`) | [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) | recomendado (camada de análise) |
| Node.js 18+ e Dataverse CLI (`@microsoft/dataverse`) | npm | só para o caminho MCP |
| Obsidian 1.9+ (Bases e Canvas) | obsidian.md | para abrir o vault |

## Licença

[MIT](LICENSE).

## Segurança

- Só leitura: o cliente HTTP implementa apenas GET.
- Credenciais ficam no `.env` da pasta de cada cliente e nunca entram no pacote nem no vault.
- Segredos encontrados no ambiente são redigidos antes de qualquer gravação.
- O kit não contém dados de nenhum cliente. O teste usa uma organização fictícia (Contoso).

## Validar a instalação

```bash
python3 <pasta-da-skill>/tests/test_pipeline.py
# OK — 42 notas, 6 bases, 20 achados, …
```
