# dataverse-inventory-kit — contexto para agentes

Kit que inventaria ambientes Microsoft Dataverse / Dynamics 365 (somente leitura) e publica o resultado como um
vault Obsidian. Pacote Python `dvinv` + skill `dataverse-inventory` (Claude Code, GitHub Copilot e outros agentes).
Este arquivo é o contexto de **desenvolvimento** do kit. O que o kit gera para quem consulta o vault (`AGENTS.md`,
`AGENT-INDEX.md`, `CLAUDE.md` do vault) é outra coisa: fica em `render/agents.py`.

## Mapa do repositório

```
.claude-plugin/                     marketplace + plugin (versão 0.6.0)
CHANGELOG.md                        seção "0.6.0 — não lançado" recebe toda mudança
skills/dataverse-inventory/
  SKILL.md  README.md  INSTALL.md  PROMPT.md     documentação de uso (README tem "Como usar")
  references/  collectors.md  findings.md  vault-schema.md  indice.md
  scripts/dvinv.py                  entrada da CLI
  scripts/inventory.example.yaml    yaml mínimo + ajustes avançados comentados no fim
  scripts/validar-windows.ps1       roteiro de validação no Windows (o uso real é CMD)
  scripts/dvinv/
    cli.py        comandos: check escopo extract render all diff collectors snapshots buscar onde-usa impacto depende
    config.py     DEEP_DEFAULTS (núcleo + extras), EXTRAS, PROFILES (metadata_only), carga do yaml
    client.py     cliente HTTP read-only (keep-alive, gzip, retry/429, parallel, get_many, get_batch, entity_definitions)
    scope.py      regra única de escopo + filtro de componentes da Microsoft
    collectors/   um coletor por família; REGISTRY (ordem), DEPENDS, FLAGS_BY_COLLECTOR, run(), plan_base()
    snapshot.py   Staging (gravação conferida + publicação atômica), DerivedStaging, leitura/verificação
    secrets.py    detecção e redação de segredos (ver "Segredos")
    dependencies.py  grafo componente × componente, chaves canônicas
    findings.py   achados automáticos (IDs em references/findings.md)
    residuos.py   uso e resíduos: última atividade, formulários fora de apps, candidatos a limpeza em ondas
    resumo.py     linha "N na org · M no escopo · processado" e tabela-resumo
    consulta.py   buscar/onde-usa/impacto/depende lendo só os CSVs do vault
    render/       obsidian.py (orquestra), catalog, maps, navigation, structure, agents, bases, canvas
  tests/test_pipeline.py            pipeline completo contra uma org simulada (FakeClient)
  tests/test_regressao_p0.py        regressões (unittest)
```

## Regras que não se quebram

1. **Somente leitura.** O cliente só faz GET. O único POST é o `$batch`, que só carrega GETs montados em
   `client.get_batch` (com `check_url` em cada um). Nunca adicionar escrita no Dataverse.
2. **Kit agnóstico de cliente.** Nenhum nome de cliente, prefixo, URL ou regra específica no código, nos testes,
   nas docs ou nos exemplos. Testes usam a org fictícia "Contoso". O repositório é público.
3. **Segredos nunca vão para disco em claro**, e **a extração nunca é descartada inteira por causa deles**. Não
   afrouxar padrões de `secrets.py` para resolver falso positivo: tratar na forma gravada (ver abaixo).
4. **O vault é o produto.** Consultas de impacto leem `Dependências.csv` e `Componentes.csv` do vault; nada de
   banco auxiliar (SQLite e xlsx foram removidos de propósito).
5. **Saída determinística.** LF sempre (também no Windows), ordem estável, sem data por nota.
   Re-extrair um ambiente sem mudança não pode gerar diff no vault.
6. **Roda em VDI Windows com CMD, sem admin e sem PowerShell.** Só Python + `requirements.txt`; caminhos longos e
   rename com novas tentativas já tratados. Mensagens de progresso visíveis: etapa silenciosa parece travada.
7. **Dado pessoal é opt-in** (extra `usuarios`) e o perfil `metadata_only` trava tudo que lê conteúdo ou registros,
   mesmo que o yaml peça.

## Pipeline

`extract` → snapshot imutável `out/<amb>/_raw/<run_id>/` (manifesto com status por coletor e sha256) →
`render` → vault `vault/Dataverse/<amb>/` + derivados `out/<amb>/_derived/<run_id>/`. `all` = os dois.

- **Coletores** rodam na ordem do `REGISTRY`; `environment` e `solutions` sempre rodam; `tables` define o escopo
  dos demais. Falha de coletor vira status `falhou` + lacuna (`ctx.gap`), nunca aborta a extração. Status:
  `ok`, `parcial` (com lacunas), `falhou`, `sem-dados` (desligado/nada a coletar), `nao-executado` (fora do `--only`).
- **`--completar <run_id|pasta|ultimo>`** herda coletores `ok`/`parcial` do snapshot base (sha256 conferido,
  mesmo escopo, perfil e URL; senão recusa) e roda o que falhou/faltou. Coletor cuja opção `deep.*` mudou
  (`FLAGS_BY_COLLECTOR`) é refeito, em cascata pelos `DEPENDS`. Origem registrada por coletor no manifesto.
- **Núcleo + extras** (`config.py`): o núcleo sempre roda e traz tudo o que é customizado e se está em uso
  (conteúdo de web resources, definições de flows/processos, eventos de formulário, privilégios, ribbons, catálogo
  de 100%, dependências da plataforma, contagem de registros, uso de campos). Extras opt-in (`extras:` /
  `--extras`): `saude`, `armazenamento`, `historico`, `camadas`, `binarios`, `usuarios`. `deep:` no yaml é ajuste
  fino; se desligar parte do núcleo, o `dvinv` avisa. Não existem mais níveis nem `--deep`/`--nivel`.
- **Notas de versão** (`changes.py`): cada render compara com o snapshot completo anterior e gera
  `Mudanças desde a extração anterior` + `Mudanças.csv`. Nada pode virar "resolvido" ou "alterado" por falha de
  etapa, opção diferente ou atualização do kit: esses casos são "não comparado" ou ignorados.
- **Escopo** (`scope.py`): prefixo, palavra-chave, solução, não gerenciado ou `all`; `exclude_prefixes`.
  `exclude_microsoft` (padrão ligado): prefixos de publishers da Microsoft fora do escopo mesmo dentro de solução
  do escopo; plugins da Microsoft identificados pela **chave de assinatura** (`publickeytoken`) ou nome
  `Microsoft.`/`Adxstudio.` — nome sozinho não serve (há assemblies de cliente chamados `MS.Dynamics.…`); colunas
  `msdyn_…` e de `exclude_prefixes` não contam como customizadas (não trazem tabela nativa ao escopo nem entram no
  uso de campos).

## Desempenho (onde o tempo vai)

- Listas: páginas de 5.000 via `get_all`/`iter_all` (sequenciais por natureza).
- Conteúdo grande (JS, formxml, xaml): `get_many`, lotes de 5–25 ids por GET, em paralelo. Não empacotar em
  `$batch`: resposta grande derruba a conexão em proxy corporativo.
- `EntityDefinitions` com expand: em blocos de 40 tabelas (`entity_definitions`); a resposta única derrubava a
  conexão.
- Leituras de um item (ribbons, privilégios por papel, colunas/chaves por tabela, fórmulas, dependências por
  solução, nomes do catálogo por tipo): `get_batch`, 20 GETs por `$batch` (`deep.batch_size`, 0 desliga), pacotes
  em paralelo; recusado pelo proxy → uma chamada por item. Ganho grande com rede lenta, pequeno perto do datacenter.
- Última atividade por tabela (`table_activity`): 2 GETs de uma linha por tabela (`$orderby … desc&$top=1`). **Sem
  `$batch`**: ordenar tabela grande pode levar minutos no serviço e um pacote de 20 estourava o tempo do proxy/VPN
  (~2 min, RemoteDisconnected, VPN caindo com as novas tentativas). Cada GET com 30 s e nenhuma nova tentativa
  (`get(quick=…)`), 2 conexões, parada da etapa após 3 cortes seguidos, teto `deep.table_activity_max_records`.
- Uso de campos: agregação FetchXML até 50 mil registros; acima disso lê os registros página a página (é o custo
  dominante). Tabelas medidas em paralelo.
- Referência (ambiente de teste com ~121 mil componentes e 844 mil dependências): extração completa ~37 min,
  catálogo 85 s com `$batch` (147–311 s antes), render ~6,5 min, varredura de segredos ~1 min por 70 MB de JSON.

## Segredos (secrets.py + snapshot.py)

Camadas, nesta ordem:
1. `redact` / `scan`: padrões textuais. Consideram também a forma com **entidades HTML decodificadas** até
   estabilizar (`&quot;apiKey&quot;`, `&amp;quot;`) — HTML de web resource costuma vir assim.
2. `redact_tree`: cada string + valor inteiro de chave com nome sensível.
3. `redact_serialized`: o que só casa na forma **gravada** (strings vizinhas no JSON, ex. `"Authorization",\n
   "Content-Type"` casava o padrão de header). Redige o valor vizinho; chaves nunca mudam.
4. `Staging.write_json` confere cada arquivo ao gravar; se ainda sobrar, levanta e **perde só aquela etapa**.
5. Publicação/render: `sanitize_paths` limpa no lugar o que não passou pela conferência (fontes decompiladas,
   notas). Nota que não der para limpar vira aviso; arquivo de dados sai como lacuna. `assert_clean` final só
   sobre o que não foi conferido (não varrer 140 MB duas vezes).
6. Achado `SEC-01` aponta onde está cada segredo (arquivo, linha, tipo) **sem o valor**. Nunca criar opção para
   publicar segredo em claro: o vault vai para Git e para agentes de IA.

## Testes

```
cd skills/dataverse-inventory
python3 tests/test_pipeline.py        # termina com "OK — N notas, N bases, N achados, ..."
python3 tests/test_regressao_p0.py    # unittest
```
- `FakeClient` (test_pipeline.py) troca só a camada HTTP: `_request` roteia por caminho (páginas de 2 para exercitar
  `@odata.nextLink`); `_batch_post` simula o `$batch` (e `refuse_batch` simula proxy que recusa).
- Toda mudança de comportamento ganha teste. Quando der, validar também contra um ambiente real de teste
  (somente leitura), comparando números com a execução anterior (tempo, contagens, lacunas) antes de publicar.
- Testes antigos que verificam comportamento intencionalmente mudado são reescritos para o novo comportamento,
  nunca apagados sem substituto.

## Convenções

- Código, mensagens, comentários e docs em português. Mensagens de console com prefixo `[dvinv]`.
- Estatística de coletor em `ctx.stats[nome]` com `org_total`/`scope` + métricas; rótulo novo em `resumo.LABELS`.
- Lacuna: `ctx.gap(coletor, o_que, erro, kind="falha"|"limitação"|"nao-executado")`.
- Opção nova: padrão em `DEEP_DEFAULTS` (ou campo do `Config`), entrada em `FLAGS_BY_COLLECTOR` se muda o
  resultado do coletor, linha comentada no `inventory.example.yaml`, tabela no README.
- Toda mudança atualiza: `CHANGELOG.md` (0.6.0), README da skill ("Como usar"), `SKILL.md`, `INSTALL.md` e
  `references/*.md` quando afetados.
- Commits no estilo `tipo(área): resumo` em português, corpo com bullets. Autor: Alex Giovani Varrese
  <alex.varrese@outlook.com>, **sem trailer de co-autoria**. Commit e push só quando o usuário pedir.

## Decisões já tomadas (não reabrir sem motivo novo)

- Níveis rápido/padrão/diagnóstico e execução em fases: removidos; complexidade sem ganho real (a fase seguinte
  refazia as etapas pesadas). Uso em dois passos: `escopo`, depois `all`; `--completar ultimo` para retomar.
- SQLite e xlsx: removidos; o vault em CSV + Markdown basta e é versionável.
- Vault como repositório de conhecimento por unidade de negócio: nome e local vêm de `output.vault_root` e
  `output.repo: true` (blocos gerenciados em README/.gitignore/.gitattributes). Nada disso é fixo no código.
- Segredo detectado não bloqueia a entrega: é redigido e vira achado; só a parte que não der para limpar sai.
- Prompts padrão: fonte em `prompts/` (+ `references/boas-praticas.md`), copiados para `.github/prompts/` do vault
  a cada render. Só arquivo com a marca `dvinv:prompt <nome do próprio arquivo>` é regravado ou removido; `_modelo`
  não é copiado. Padrão de prompt e de resposta em `references/padrao-prompts.md`.

## Ideias avaliadas, ainda não feitas

- Uso de campos em tabela grande por agregação em faixas de `createdon` (cada faixa < 50 mil) + `$batch`: troca a
  leitura de todos os registros por dezenas de consultas que só devolvem números. Validar contagens campo a campo.
- Linhas de progresso no render (hoje fica alguns minutos sem mensagem).
- Agrupar ocorrências do mesmo segredo no `SEC-01` (id por execução, sem relação com o valor).
- Histórico de execução de cloud flows (lacuna real para diagnóstico de erro).
- Visão consolidada entre unidades de negócio (juntar `Componentes.csv` de vários vaults).
- Prompts de code review e de racionalização (o que remover ou reaproveitar), no padrão de `prompts/`.
- Regras novas para as lacunas priorizadas em `references/boas-praticas.md`.
- Lista `pular:` no yaml para coletores leves (só se houver necessidade real).
