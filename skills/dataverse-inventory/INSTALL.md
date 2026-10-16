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

**Publicar o vault como repositório de conhecimento (um por unidade de negócio):**

1. Crie o repositório no remoto da organização (ex. Azure DevOps do cliente) com o padrão de nome do programa
   (ex. `<programa>-<unidade>-knowledge`) e clone-o ao lado da pasta do cliente.
2. No `inventory.<amb>.yaml` de cada ambiente da unidade: `output.vault_root: ../<repositório>`,
   `output.vault_folder: Dataverse/<AMB>` e `output.repo: true`. Todos os ambientes da unidade (DEV/TEST/PRD)
   apontam para o mesmo repositório; as comparações (`diff`) ficam em `Dataverse/Comparações/`.
3. `dvinv all` (ou `render`). O kit mantém os blocos `dvinv` do `README.md`, `.gitignore`, `.gitattributes` e
   `AGENTS.md` (o texto do time fora deles é preservado) e mostra no fim `N novo(s), M alterado(s), K removido(s)`.
4. Revise o diff e faça o commit — **o dvinv não commita nem faz push**. Snapshot bruto, derivados e `.env` ficam em
   `out/`, fora do repositório.

**Validar no Windows (VDI) antes de usar em produção ou taguear uma versão:**

```powershell
cd <pasta-da-skill>\scripts
.\validar-windows.ps1                                         # testes offline (pipeline + P0)
.\validar-windows.ps1 -Config C:\kb\cliente\inventory.test.yaml   # + check, render 2x sem diff, consultas
.\validar-windows.ps1 -Config ... -Extrair                      # + extração completa (demora)
```

Pontos que o script confere: troca atômica da pasta do vault (no Windows falha se Explorer/editor/antivírus
segurar um arquivo — o kit tenta de novo e, persistindo, mantém o vault anterior e explica o que fechar), LF em
todos os arquivos, segunda renderização idêntica, consultas `--vault` e `core.longpaths` no repositório. Use pasta
de trabalho curta (ex. `C:\kb\…`): o render avisa se algum caminho do vault puder passar de 260 caracteres.

**Acompanhar uma extração longa:** cada `extract`/`render`/`all` grava o log com hora em
`out\<ambiente>\logs\dvinv-<data>-<comando>.log` (abra no VS Code, que atualiza sozinho, ou `type` no CMD). As
etapas em lote mostram `N/total (%) · decorrido · ~restante`, cada coletor termina com "na org · no escopo ·
processado" e o fim da extração traz a tabela-resumo. Em ambiente muito customizado: `parallel: 8`, confira a
tabela *De onde veio o escopo* em `01 Ambiente` e mova prefixos de terceiros para `scope.exclude_prefixes`.

Quem clona o repositório consulta sem instalar nada além do Python: `grep` nos CSVs ou
`python3 <skill>/scripts/dvinv.py onde-usa <alvo> --vault Dataverse/<AMB>` (sem `inventory.yaml` nem credenciais).

**Claude Code e GitHub Copilot no mesmo projeto (sem conflito):**

- **Uma cópia só da skill.** O GitHub Copilot (VS Code, CLI e cloud agent) lê skills de `.github/skills/`,
  `.claude/skills/` e `.agents/skills/` do projeto, e de `~/.copilot/skills/`, `~/.claude/skills/` e
  `~/.agents/skills/`. Instale em `.claude/skills/dataverse-inventory/` (ou em `~/.claude/skills/`) e os
  dois agentes usam o mesmo `SKILL.md` e as mesmas `references/*.md`. Duas cópias (ex. `.claude/` e
  `.github/`) acabam divergindo de versão; o `npx skills add` resolve criando links para uma cópia canônica.
- **Regras num lugar só, geradas pelo render.** Na raiz do vault (`output.vault_root`), o `render` mantém um
  bloco curto no `AGENTS.md` (lido pelo Copilot) entre `<!-- dvinv:inicio … -->` e `<!-- dvinv:fim -->`: ambientes
  inventariados, link do `AGENT-INDEX.md` e config de cada um, comandos do índice e as regras de execução.
  Texto fora do bloco é do time e nunca é alterado. O `CLAUDE.md` é criado com `@AGENTS.md` só se não existir;
  se já existir sem esse import, o render avisa e não mexe. Convenções do cliente (prefixos, quem autoriza
  extração) vão no `AGENTS.md`, fora do bloco — assim Claude e Copilot leem a mesma instrução.
- **Uma execução por ambiente.** `extract`/`render`/`all` criam a trava `out/<ambiente>/.dvinv.lock`: se o
  Claude estiver extraindo e o Copilot pedir outra extração do mesmo ambiente, a segunda falha com aviso
  em vez de publicar snapshots/vaults concorrentes. As consultas (`buscar`, `onde-usa`, `impacto`,
  `depende`) só leem e podem rodar em paralelo.
- **Mesma entrada para os dois.** O vault gerado tem `AGENT-INDEX.md`, `Componentes.csv` e `Dependências.csv`:
  qualquer agente — até sem a skill — segue o mesmo roteiro (grep nos CSVs; `references/indice.md`).

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
`publisher_prefix` das soluções não gerenciadas em `out/<AMBIENTE>/_raw/<run_id>/solutions.json`.

## 5. Executar

```bash
D=$SKILL/scripts/dvinv.py
python3 $D check   -c inventory.yaml          # conexão
python3 $D escopo  -c inventory.yaml          # quantas tabelas/soluções entram, sem extrair
python3 $D extract -c inventory.yaml          # núcleo: tudo o que é customizado + uso
python3 $D extract -c inventory.yaml --extras historico,saude   # + diagnóstico (lista abaixo)
python3 $D extract -c inventory.yaml --completar ultimo         # retoma: refaz só o que falhou
python3 $D render  -c inventory.yaml          # (re)gera o vault — edições do analista são preservadas
python3 $D all     -c inventory.yaml          # extract + render
python3 $D diff    -c inventory.prd.yaml --a out/CLIENTE-PRD/_raw --b out/CLIENTE-TEST/_raw
python3 $D render  -c inventory.yaml --snapshot 20261009T203200Z   # renderiza um snapshot específico
python3 $D all     -c inventory.yaml --profile metadata_only       # só metadados (ver abaixo)
```

Cada `extract` cria um snapshot imutável em `<raw_dir>/<run_id>/` (run_id = data/hora UTC) com
`manifest.json` (status de cada coletor, arquivos com sha256, lacunas). A extração grava numa pasta
`.staging-*` e só publica no fim; se for interrompida, nada fica com cara de snapshot. O `render` usa o
último snapshot íntegro e grava achados/dependências em `_derived/<run_id>/` (fora do snapshot). Vários
snapshots no mesmo dia coexistem; apague os antigos quando quiser. A opção `--no-snapshot` das versões
anteriores é ignorada.

**Perfil `metadata_only`** (`profile: metadata_only` no YAML ou `--profile metadata_only`): desliga e trava
— mesmo que o núcleo, um extra ou `deep.*: true` peça — tudo que lê conteúdo ou registros: `webresource_content`,
`flow_definitions`, `process_definitions`, `plugin_binaries`, `field_usage`, `storage`, `plugin_trace`,
`team_members` e `users`. Continuam permitidos metadados de customização (`form_events`, `ribbons`,
`role_privileges`, `platform_dependencies`) e `record_counts`: é uma contagem agregada por tabela
(`RetrieveTotalRecordCount`), que não lê nenhum registro — desligue com `deep.record_counts: false` se
nem o volume puder ser coletado. O perfil aparece no manifesto e em `01 Ambiente.md`. Observação: o
coletor `security` continua contando usuários ativos (`systemusers`, só flags de status).

O extra `usuarios` traz a lista nominal de usuários (nome, UPN, BU, licença, papéis diretos e via equipe,
equipes) e uma nota por usuário ativo em `Usuários/`. É **dado pessoal**: ligue só quando o levantamento de
acessos estiver no escopo acordado e trate o vault como confidencial. Sem ele, BUs, equipes (com papéis) e
PCF continuam no vault, e os usuários aparecem só como contagem.

**Núcleo e extras.** O núcleo sempre roda: tudo o que foi customizado (código, definições, eventos de formulário,
privilégios por papel, ribbons, catálogo de 100%, dependências registradas pela plataforma) e se está em uso
(contagem de registros, **uso de campos**). Os extras são diagnóstico, opt-in por `extras:` no YAML ou
`--extras`: `saude` (plugin trace), `armazenamento` (maiores tabelas, anexos, auditoria), `historico`
(importações/exportações de soluções), `camadas` (camadas de solução, ~3 s por tabela), `binarios` (DLLs de
plugin), `usuarios` (lista nominal e membros de equipe — dado pessoal). As chaves `deep:` ficam para ajuste fino
(ver o fim do `inventory.example.yaml`); se o YAML desligar parte do núcleo, o `dvinv` avisa.

Desempenho: o kit reaproveita conexões HTTPS (keep-alive), pede respostas comprimidas (gzip), busca em lote
(até 25 ids por consulta), empacota leituras de um item em `$batch` (20 por requisição; `deep.batch_size`) e
faz até `parallel` (padrão 4) chamadas simultâneas nas etapas de uma chamada por item. O log mostra o
progresso dessas etapas e avisa se o serviço pedir espera (429). Referência medida: ambiente com 159
tabelas, 4 mil colunas custom, 1,8 mil views, 900 processos e 1,5 mil consultas de dependência — **22 min**
com todas as coletas ligadas, a partir de um host a ~200 ms do datacenter.

Tempo típico: de minutos (org pequena) até 30–60 min em orgs com centenas de tabelas e
milhares de processos. O cliente respeita `Retry-After` em caso de 429 (service protection limits).

## 6. Abrir no Obsidian

1. *Open folder as vault* → a pasta `output.vault_root` (ou use um vault existente e aponte `vault_root` para ele).
2. Confirme que os core plugins **Bases** e **Canvas** estão ligados.
3. Comece por `Dataverse/<AMBIENTE>/00 Índice.md`.
4. **Matriz de dependências**: `08 Matriz de Dependências.md` (impacto por tabela, tipo × tipo, mais
   dependidos, externos, órfãos) e a seção *Dependências* de cada nota. Para o cliente, entregue
   `Dependências.csv` (mesma pasta; abre no Excel, separador `;`). A matriz inclui as dependências
   registradas pelo Dataverse e as dependências ausentes de cada solução (núcleo; `deep.platform_dependencies:
   false` deixa só as que o kit deduz).

## 7. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `HTTP 401` no `check` | secret expirado/errado, tenant errado | gerar novo secret e conferir `TENANT_ID` |
| `HTTP 403` em tudo | Application User inexistente ou sem papel | 3A passos 5–6 |
| `HTTP 403` só em um coletor (ex. canvasapps) | papel sem Read naquela tabela | vira lacuna em `01 Ambiente.md`; ampliar o papel se precisar |
| device code bloqueado / AADSTS53003 | Conditional Access | usar SPN (3A) |
| device code pede login a cada execução | o token fica só em memória por padrão | `auth.devicecode_cache: true` (cache criptografado do sistema; sem cofre disponível, falha — não há fallback em texto puro) |
| etapa `falhou` com `possível segredo não redigido` | um trecho casou padrão de segredo e não pôde ser redigido na gravação | só aquela etapa ficou sem arquivo (lacuna); o resto foi publicado. Informe o tipo e a linha da mensagem (o valor não é exibido) |
| `varredura do vault: N arquivo(s) redigido(s) no lugar` | nota com trecho que só casa padrão na forma final (ex. entidades HTML) | nada a fazer: o trecho foi redigido; se aparecer "substituído(s)", a nota virou aviso — confira a parte manual dela, que é varrida junto |
| `URL recusada (host … difere …)` | `@odata.nextLink` ou URL absoluta fora do ambiente configurado | o token não foi enviado; confira `environment.url` (precisa ser `https://`) |
| `mcp indisponível … libsecret` | keyring ausente (Linux headless) | SPN para o extrator; MCP segue para o agente |
| MCP `403` após `mcp allow` | consentimento do tenant pendente | URL de admin consent (3B passo 3) |
| poucos cloud flows | flows fora de solução não aparecem na Web API | Power Platform admin / `pac admin list` / PAC CLI |
| `AggregateQueryRecordLimit` em membros de equipe | > 50 mil linhas de teammembership | o kit pagina `teammemberships` automaticamente |
| milhares de web resources/notas que não são do cliente | `include_unmanaged` trouxe bibliotecas de terceiros | `scope.exclude_prefixes` (ver "De onde veio o escopo" em `01 Ambiente`) |
| extração lenta | latência alta até o datacenter | aumente `parallel` (ex. 6–8); observe se aparecem avisos de 429 no log |

## 8. Testar a instalação sem ambiente

```bash
python3 $SKILL/tests/test_pipeline.py --keep
python3 $SKILL/tests/test_regressao_p0.py
```

Roda o pipeline inteiro contra uma org fictícia (sem rede) e deixa um vault de exemplo em `/tmp` para abrir no Obsidian.
