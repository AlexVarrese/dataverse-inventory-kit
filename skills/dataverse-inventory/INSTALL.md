# Instalação e conexão

O pacote suporta **dois caminhos de conexão, ambos ativos ao mesmo tempo**:

| | **A. Service Principal (SPN)** | **B. MCP (login do Dataverse CLI)** |
|---|---|---|
| Identidade | Application User no Dataverse | O usuário que fez `dataverse auth create` |
| Interativo? | Não. Serve para agendamento e CI | Login uma vez; depois renova em silêncio |
| Conditional Access que bloqueia login interativo | Não afeta | Pode bloquear (device code/MCP) |
| Usado por | Extrator Python (`auth.method: spn`) | Agente (ferramentas MCP) **e** extrator (`auth.method: mcp`) |
| Melhor para | Extração completa e repetível | Exploração e validação pontual pelo agente; extração sem app registration |

Com `auth.method: auto` (padrão), o extrator tenta **SPN → MCP → Azure CLI → device code**. Ter os
dois configurados é o recomendado: o extrator usa o SPN, e o agente usa o MCP para responder perguntas
e validar achados.

---

## 1. Pré-requisitos

> Atalho: se as Dataverse Skills estiverem instaladas, peça ao agente **"Connect to Dataverse"** na
> pasta do cliente. A `dv-connect` cobre as seções 1 e 3B (ferramentas, login, MCP, `.env`), e a
> `dv-security` cria o Application User da 3A.

| Item | Versão | Para quê |
|---|---|---|
| Python | 3.10+ | extrator `dvinv` |
| Node.js + npm | 18+ | Dataverse CLI e servidor MCP (`@microsoft/dataverse`) |
| Obsidian desktop | 1.9+ (Bases é core plugin) | abrir o vault |
| Permissão no ambiente | System Administrator ou Environment Admin | criar Application User e liberar o cliente MCP (uma vez) |

Dependências Python (instaladas na seção 2): `azure-identity`, `msal`, `msal-extensions`, `PyYAML`.

## 2. Instalar as skills

São três conjuntos: **esta skill**, as **Dataverse Skills** da Microsoft (conexão, MCP, validação) e
as **Obsidian Skills** do kepano (escrita no vault). O kit está em
[github.com/alexvarrese/dataverse-inventory-kit](https://github.com/alexvarrese/dataverse-inventory-kit); para instalar de uma cópia local (zip), troque
`alexvarrese/dataverse-inventory-kit` pelo caminho da pasta descompactada.

**Qualquer agente compatível com Agent Skills (Hermes, Claude Code, Codex, Copilot, Cursor…), via `npx skills`:**

```bash
npx skills add alexvarrese/dataverse-inventory-kit --skill dataverse-inventory -g                # esta skill
npx skills add microsoft/Dataverse-skills -s "*" -g                  # dv-connect, dv-metadata, dv-query…
npx skills add kepano/obsidian-skills -g \
  --skill obsidian-markdown --skill obsidian-bases --skill json-canvas --skill obsidian-cli
```

Para restringir a um agente, acrescente `--agent hermes-agent` (ou `claude-code`, `codex`…); para
copiar em vez de criar link, `--copy`.

**Claude Code (marketplace de plugins):**

```
/plugin marketplace add alexvarrese/dataverse-inventory-kit
/plugin install dataverse-inventory@dataverse-inventory-kit
/plugin install dataverse@claude-plugins-official
/plugin marketplace add kepano/obsidian-skills
/plugin install obsidian@obsidian-skills
```

**Manual:** copie `skills/dataverse-inventory/` para a pasta de skills do agente:
`~/.claude/skills/` (Claude Code), `~/.hermes/skills/` (Hermes), `~/.codex/skills/` (Codex) ou
`.agents/skills/` do projeto.

**Agentes sem suporte a skills:** use o [`PROMPT.md`](PROMPT.md).

Depois de instalar, defina `SKILL` como a pasta onde a skill ficou (usada nos comandos abaixo):

```bash
export SKILL=~/.claude/skills/dataverse-inventory        # ajuste para o seu agente
pip install -r $SKILL/scripts/requirements.txt
python3 $SKILL/tests/test_pipeline.py                    # teste offline: deve terminar em "OK — …"
```

## 3. Pasta do cliente

```bash
mkdir -p inventario/CLIENTE && cd inventario/CLIENTE
cp $SKILL/scripts/inventory.example.yaml inventory.yaml
printf '.env\nout/\n' > .gitignore
```

Um `inventory.yaml` por ambiente (`inventory.prd.yaml`, `inventory.test.yaml`), todos podendo
apontar para o mesmo vault com `vault_folder` diferente.

---

## 3A. Service Principal

**No Microsoft Entra ID** (portal.azure.com → App registrations):

1. **New registration** → nome `dvinv-inventario-<cliente>` → single tenant → Register.
2. Anote **Application (client) ID** e **Directory (tenant) ID**.
3. **Certificates & secrets → New client secret**. Copie o *Value* (aparece uma vez só) e defina uma validade curta.
4. Não é preciso adicionar API permissions: o acesso ao Dataverse é dado pelo Application User abaixo.

**No Power Platform Admin Center** (admin.powerplatform.microsoft.com):

5. Environments → *ambiente* → **Settings → Users + permissions → Application users → New app user**.
6. Selecione o app, a Business Unit raiz e um **papel de segurança somente leitura** (recomendado):
   - copie o papel *Basic User* para `dvinv - Leitura de Customização` e dê **Read em nível
     Organization** nas abas *Customization* e *Core Records* para: Entity, Field, Relationship,
     System Form, View, Web Resource, Plug-in Assembly, Plug-in Type, Sdk Message Processing Step
     (+ Image), Custom API (+ Request Parameter/Response Property), Service Endpoint, Process,
     Solution, Publisher, Model-driven App, Canvas App, Bot, Environment Variable Definition/Value,
     Connection Reference, Connector, Security Role, Business Unit, User, Team, Field Security
     Profile, Plug-in Trace Log, Organization;
   - na prática, muitos times usam *System Administrator* para o levantamento e removem o usuário depois.
     Nesse caso, registre essa decisão.
7. Crie o `.env` na pasta do cliente (**nunca versionar**):

```dotenv
TENANT_ID=00000000-0000-0000-0000-000000000000
CLIENT_ID=00000000-0000-0000-0000-000000000000
CLIENT_SECRET=<valor do secret>
DATAVERSE_URL=https://<org>.crm.dynamics.com
```

8. No `inventory.yaml`: `auth.method: spn` (ou `auto`) e `auth.env_file: .env`.
9. Teste: `python3 $SKILL/scripts/dvinv.py check -c inventory.yaml`
   → `OK — https://<org>.crm.dynamics.com versão 9.2…`

> O mesmo SPN pode atender DEV/TEST/PRD: crie o Application User em cada ambiente e mude só
> `environment.url` em cada `inventory.*.yaml`.

---

## 3B. MCP (servidor MCP do Dataverse + mesmo login no extrator)

1. **Dataverse CLI** (só se ainda não houver; ele é também o proxy MCP):

   ```bash
   npm list -g @microsoft/dataverse || npm install -g @microsoft/dataverse@latest
   ```

2. **Login** (grava o cache MSAL compartilhado pelo CLI, pelo MCP e pelo `dvinv`):

   ```bash
   dataverse auth create --environment https://<org>.crm.dynamics.com             # com navegador
   dataverse auth create --environment https://<org>.crm.dynamics.com --deviceCode  # SSH/headless
   ```

3. **Liberar o cliente MCP no ambiente** (uma vez por ambiente; exige System Admin no ambiente):

   ```bash
   dataverse mcp allow 0c412cc3-0dd6-449b-987f-05b053db9457
   ```

   Alternativa: PPAC → ambiente → Settings → Product → Features → **MCP Server** → On → *Allowed clients* → adicionar o ID acima.
   Se o tenant exigir consentimento de administrador (uma vez por tenant, Global Admin):
   `https://login.microsoftonline.com/<TENANT_ID>/adminconsent?client_id=0c412cc3-0dd6-449b-987f-05b053db9457`

4. **Validar o endpoint:**

   ```bash
   npx -y @microsoft/dataverse@latest mcp https://<org>.crm.dynamics.com --validate
   ```

5. **Registrar o servidor MCP no agente** (sempre transporte *stdio* via proxy `npx`, nunca URL direta):

   - **Hermes**: no `config.yaml` do Hermes (`$HERMES_HOME/config.yaml`, padrão `~/.hermes/config.yaml`),
     na chave `mcp_servers:`, preservando os servidores existentes:

     ```yaml
     mcp_servers:
       dataverse-cliente-prd:
         command: npx
         args: ["-y", "@microsoft/dataverse@latest", "mcp", "https://<org>.crm.dynamics.com"]
     ```

     Depois reinicie o Hermes (ou use `hermes mcp`).
   - **Claude Code**:

     ```bash
     claude mcp add --scope project dataverse-cliente-prd -t stdio -- \
       npx -y @microsoft/dataverse@latest mcp "https://<org>.crm.dynamics.com"
     ```

   - **Cursor / VS Code / Codex**: entrada `command: npx` com os mesmos `args`
     (ver `dv-connect/references/mcp-configuration.md`).

6. **Extrator usando o login do MCP:** `auth.method: mcp` no `inventory.yaml` (ou `auto` sem
   `CLIENT_SECRET` no `.env`). Teste com `dvinv.py check -c inventory.yaml` → `autenticação: mcp (…)`.

   > **Linux headless:** o cache do Dataverse CLI fica no keyring (libsecret). Sem
   > `pygobject` + `gnome-keyring` + sessão D-Bus, o modo `mcp` não abre o cache e o `auto` cai para
   > o próximo método. Em servidores e containers, prefira SPN (3A) para o extrator e mantenha o MCP
   > para o agente.

---

## 3C. Ambos ativos (recomendado)

```yaml
# inventory.yaml
auth:
  method: auto        # SPN quando houver CLIENT_SECRET; senão, o login do MCP
  env_file: .env
```

- **Extração completa** → SPN (estável, sem prompt, sem depender de sessão do usuário).
- **Agente** → servidor MCP para perguntas ad hoc ("quantos registros em X?", "esse campo existe em
  TEST?") e para **validar achados** antes de confirmá-los no vault.
- Para forçar um caminho numa execução, troque `method:` para `spn` ou `mcp`.

---

## 4. Configurar o escopo

```yaml
scope:
  prefixes: [contoso_]        # prefixo(s) do publisher
  solutions: [ContosoCore]    # opcional: inventariar soluções inteiras
  include_unmanaged: true     # pega customização feita direto no ambiente
```

Não sabe o prefixo? Rode `dvinv.py extract -c inventory.yaml --only solutions` e veja
`publisher_prefix` das soluções não gerenciadas em `out/<AMBIENTE>/_raw/solutions.json`.

## 5. Executar

```bash
D=$SKILL/scripts/dvinv.py
python3 $D check   -c inventory.yaml          # conexão
python3 $D extract -c inventory.yaml          # coleta padrão
python3 $D extract -c inventory.yaml --deep   # liga todas as coletas pesadas (lista abaixo)
python3 $D render  -c inventory.yaml          # (re)gera o vault — edições do analista são preservadas
python3 $D all     -c inventory.yaml          # extract + render
python3 $D diff    -c inventory.prd.yaml --a out/CLIENTE-PRD/_raw --b out/CLIENTE-TEST/_raw
```

O `--deep` liga: privilégios por papel, ribbons, plugin trace, membros de equipe, DLLs de plugin,
definições de processos, **uso de campos**, **armazenamento/auditoria** e **dependências registradas
pela plataforma** (inclusive por coluna, uma chamada por coluna customizada). Para ligar só parte
delas, use as chaves de `deep:` no `inventory.yaml` em vez do `--deep`.

Tempo típico: de minutos (org pequena) até 30–60 min com `--deep` em orgs com centenas de tabelas e
milhares de processos. O cliente respeita `Retry-After` em caso de 429 (service protection limits).

## 6. Abrir no Obsidian

1. *Open folder as vault* → a pasta `output.vault_root` (ou use um vault existente e aponte `vault_root` para ele).
2. Confirme que os core plugins **Bases** e **Canvas** estão ligados.
3. Comece por `Dataverse/<AMBIENTE>/00 Índice.md`.
4. **Matriz de dependências**: `08 Matriz de Dependências.md` (impacto por tabela, tipo × tipo, mais
   dependidos, externos, órfãos) e a seção *Dependências* de cada nota. Para o cliente, entregue
   `Matriz de Dependências.xlsx` (mesma pasta), que abre no Excel com filtros. A matriz sai sempre;
   `deep.platform_dependencies: true` acrescenta as dependências registradas pelo Dataverse e as
   dependências ausentes de cada solução.

## 7. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `HTTP 401` no `check` | secret expirado/errado, tenant errado | gerar novo secret e conferir `TENANT_ID` |
| `HTTP 403` em tudo | Application User inexistente ou sem papel | 3A passos 5–6 |
| `HTTP 403` só em um coletor (ex. canvasapps) | papel sem Read naquela tabela | vira lacuna em `01 Ambiente.md`; ampliar o papel se precisar |
| device code bloqueado / AADSTS53003 | Conditional Access | usar SPN (3A) |
| `mcp indisponível … libsecret` | keyring ausente (Linux headless) | SPN para o extrator; MCP segue para o agente |
| MCP `403` após `mcp allow` | consentimento do tenant pendente | URL de admin consent (3B passo 3) |
| poucos cloud flows | flows fora de solução não aparecem na Web API | Power Platform admin / `pac admin list` / PAC CLI |
| `AggregateQueryRecordLimit` em membros de equipe | > 50 mil linhas de teammembership | lacuna declarada; medir por amostragem |

## 8. Testar a instalação sem ambiente

```bash
python3 $SKILL/tests/test_pipeline.py --keep
```

Roda o pipeline inteiro contra uma org fictícia (sem rede) e deixa um vault de exemplo em `/tmp` para abrir no Obsidian.
