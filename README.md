# Dataverse Inventory Kit

Skill + pacote Python (`dvinv`) para fazer o **inventário read-only de ambientes Microsoft Dataverse /
Dynamics 365 / Power Platform** e publicar o resultado como um **vault Obsidian**. O vault traz uma
nota por componente, Bases, um Canvas do ambiente, achados automáticos e comparação entre ambientes.

Segue a [especificação Agent Skills](https://agentskills.io/specification) e funciona com Hermes,
Claude Code, Codex, GitHub Copilot, Cursor e qualquer agente compatível. Para agentes sem suporte a
skills, há um prompt equivalente.

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
