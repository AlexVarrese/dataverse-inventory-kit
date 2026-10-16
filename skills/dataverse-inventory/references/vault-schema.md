# Esquema do vault

Toda nota gerada tem `ambiente`, `tipo` e `tags` (`dataverse`, `dataverse/<tipo>`, `ambiente/<slug>`); as de
componente têm também `chave` (a mesma dos CSVs e do `dvinv onde-usa`) e `id` (GUID do Dataverse).
Desde a 0.6 **não há data de extração por nota** (re-extrair sem mudança no ambiente não altera os
arquivos e o diff do Git mostra só mudança real): a data e o `snapshot` ficam em `00 Índice`,
`01 Ambiente` e `AGENT-INDEX`. Links em properties são strings `"[[caminho|alias]]"`, como pede o Obsidian.

| `tipo` | Pasta | Properties principais |
|---|---|---|
| `indice` | `00 Índice.md` | `snapshot`, `extraido_em` |
| `indice-agente` | `AGENT-INDEX.md` | `snapshot` — entrada para agentes: mapa, comandos do índice, arquivos que não se lê inteiros |
| `secao` | `01 Ambiente`, `03 Integrações`, `04 Segurança`, `05 Uso de Campos`, `06 Armazenamento e Auditoria`, `07 Repositórios`, `08 Matriz de Dependências`, `09 Catálogo de Componentes` | `url`, `versao`, `snapshot` (run_id) e `perfil` em `01 Ambiente`; `componentes`, `tipos` no `09` |
| `tabela` | `Tabelas/` | `nome_logico`, `nome_exibicao`, `aliases`, `customizada`, `gerenciada`, `propriedade`, `tipo_tabela` (Standard/Virtual/Elastic/Activity), `registros`, `colunas_custom`, `colunas_total`, `relacionamentos`, `formularios`, `plugin_steps`, `processos`, `flows_que_tocam`, `achados`, `escopo`, `solucoes` |
| `plugin-assembly` | `Plugins/` | `versao`, `isolamento`, `origem`, `gerenciado`, `tipos`, `steps`, `modificado` |
| `plugin-step` | `Plugin Steps/` | `assembly` (link), `classe`, `handler`, `mensagem`, `tabela` (link), `estagio`, `modo`, `ordem`, `ativo`, `filtering`, `imagens`, `impersonando` |
| `processo` | `Processos/<Workflows\|Business Rules\|Actions\|BPFs\|Cloud Flows\|Desktop Flows>/` | `categoria`, `tabela` (link), `ativo`, `modo`, `gatilho`, `conectores`, `hosts_http`, `proprietario`, `proprietario_desativado`, `modificado`, `criado`, `modificado_por` |
| `webresource` | `Web Resources/` | `tipo_arquivo`, `tamanho_bytes`, `funcoes`, `formularios`, `segredos`, `modificado` |
| `customapi` | `Custom APIs/` | `binding`, `tabela` (link), `funcao`, `privada`, `plugin` |
| `app` | `Apps/` | `subtipo` (model-driven / canvas / agente Copilot Studio), `nome_unico` |
| `solucao` | `Soluções/` | `versao`, `gerenciada`, `publisher`, `prefixo`, `componentes` |
| `papel` | `Papéis/` | `gerenciado`, `tabelas_com_privilegio` |
| `sitemap` | `Site Maps/` | `de_app`, `apps` (links), `areas`, `subareas`, `tabelas`, `web_resources`, `urls_externas`, `gerenciado` |
| `sla` | `SLAs/` | `tabela` (link), `estado`, `versao_sla`, `padrao`, `itens`, `horario_comercial`, `permite_pausa`, `gerenciado` |
| `pcf` | `Componentes PCF/` | `namespace`, `construtor`, `versao`, `tipo_controle`, `gerenciado`, `formularios`, `campos`, `dominios_externos`, `web_api` |
| `business-unit` | `Business Units/` | `pai` (link), `filhas`, `equipes`, `usuarios_ativos`, `desativada` |
| `equipe` | `Equipes/` | `tipo_equipe`, `business_unit` (link), `grupo_entra`, `membros`, `papeis` (links), `administrador` |
| `usuario` | `Usuários/` (só extra `usuarios`, só ativos) | `upn`, `tipo_usuario`, `modo_acesso`, `licenca`, `business_unit` (link), `papeis`, `papeis_via_equipe`, `equipes` |
| `achado` | `Achados/` | `id_achado`, `severidade`, `titulo`, `metrica`, **`status`**, **`responsavel`** |
| `comparacao` | `Comparações/` | `ambiente_a`, `ambiente_b`, `data_a`, `data_b` |
| `mudancas` | `Mudanças desde a extração anterior.md` | `snapshot`, `snapshot_anterior`, `novos`, `removidos`, `alterados`, `achados_resolvidos`, `achados_novos` |

Notas de componente que participam do grafo também recebem `dependencias` (quantos componentes ela usa)
e `dependentes` (quantos a usam), e uma seção **Dependências** com *Depende de* / *Usado por* agrupados
pela relação. ⚙︎ marca dependência registrada pela plataforma.

## Navegação e grafo por tipo (`Mapas/`)

O Graph view do Obsidian não tem visões salvas e a Obsidian CLI não abre o grafo com filtro (exige o app aberto e
não tem comandos de grafo). O vault traz o grafo por tipo em arquivos:

- `Mapas/00 Mapas por tipo.md` — índice dos mapas.
- `Mapas/<Tipo>.md` (`tipo: mapa`) — liga todos os componentes da pasta do tipo (Tabelas, Cloud Flows, Workflows,
  Business Rules, Actions, BPFs, Plugins, Plugin Steps, Web Resources, Custom APIs, PCF, Apps, Site Maps, SLAs,
  Papéis) com o resumo do que cada um usa (campos agrupados por tabela: "campos (7) → account"). O **grafo local**
  da nota (profundidade 2) mostra o tipo inteiro e seus vizinhos; a nota traz filtros prontos para o grafo
  global (`path:"Dataverse/<AMB>/Processos/Cloud Flows/"`, com/sem tabelas, sem soluções).
- `Mapas/<Tipo>.canvas` — JSON Canvas: componentes à esquerda, o que usam à direita, setas com a relação (até 200
  componentes, os de mais relacionamentos). Regenerado a cada render.
- `<vault_root>/.obsidian/graph.json` — criado **só se não existir**: um grupo de cor por tipo (`path:"/Tabelas/"`,
  `path:"/Processos/Cloud Flows/"`…, vale para todos os ambientes) e busca padrão sem soluções/achados/bases.
  Depois de criado, a configuração do grafo é de quem usa o vault.

## Raiz do vault: AGENTS.md e CLAUDE.md

Carregados automaticamente pelo Copilot (`AGENTS.md`) e pelo Claude Code (`CLAUDE.md` → `@AGENTS.md`). O
render reescreve só o bloco entre `<!-- dvinv:inicio … -->` e `<!-- dvinv:fim -->` (≤ 30 linhas: tabela de
ambientes com URL, `AGENT-INDEX.md`, config e snapshot, e 5 regras de consulta/execução). Os ambientes vêm do
frontmatter de cada `AGENT-INDEX.md` do vault. `CLAUDE.md` só é criado se não existir.

## Arquivos tabulares (fora das notas)

Listas que passariam de algumas centenas de linhas não ficam inteiras em nota (o editor do Obsidian
e a janela de contexto de um agente sofrem com notas de vários MB):

| Arquivo | Conteúdo |
|---|---|
| `Componentes.csv` | 100% dos componentes do ambiente (chave, tipo, nome, pai, GUID, escopo, soluções, nota) |
| `Dependências.csv` | **índice textual**: uma linha por aresta que toca o escopo, com chave, GUID, tipo, nome e nota das duas pontas, em ordem estável |
| `Sem dependentes.csv` | lista completa de componentes sem dependente (só quando passa do limite da nota 08) |
| `Dependências ausentes.csv` | RetrieveMissingDependencies por solução, com nomes do catálogo |
| `Equipes.csv` | todas as equipes estruturais; com mais de 1.000, só as que têm papel ou grupo do Entra viram nota |
| `Mudanças.csv` | tipo, situação (novo, removido, alterado, corrigido, nova ocorrência), componente e o que mudou, desde a extração anterior |

Web resource de biblioteca de terceiros ou com mais de 150 KB não tem o código na nota (fica no
snapshot, `_raw/<run_id>/webresources.json`); a análise estática e as funções continuam na nota.

## Mudanças desde a extração anterior

Gerada a cada render (`dvinv/changes.py`), comparando o snapshot renderizado com o snapshot **íntegro e completo**
imediatamente anterior da mesma pasta `_raw` (execução parcial — `--only` sem `--completar` — não serve de base):

- por tipo (tabelas, colunas customizadas, relacionamentos, option sets, formulários, views, web resources, PCF,
  assemblies, steps, processos, Custom APIs, service endpoints, variáveis, conexões, apps, site maps, comandos,
  SLAs, mapeamentos, papéis): novos, removidos e alterados, com os campos que mudaram;
- achados: novo, resolvido, melhorou ou piorou (número de ocorrências), mudou, igual ou **não comparável**, e as
  ocorrências corrigidas e novas (achados `info` não listam ocorrências);
- resumo no `00 Índice` e tudo em `Mudanças.csv`.

Regras para não acusar mudança que não houve: ignora campos voláteis (contagem de registros, origem do conteúdo,
análises derivadas, quem modificou); compara só campos que as duas extrações gravam (atualização do kit não vira
mudança); tipo e achado cuja etapa falhou, ficou desligada ou rodou com opções `deep.*` diferentes — inclusive nas
etapas de que depende — saem como "não comparado". A data de modificação conta: deploy que regrava sem mudar conteúdo
aparece como "data de modificação". Primeira extração: a nota diz que não há com o que comparar.

## Vault em repositório Git

- Texto com LF e ordem estável: o diff entre extrações mostra só o que mudou no ambiente.
- O render avisa quando algum arquivo passa de 50 MB (o GitHub alerta nesse ponto e recusa acima de 100 MB).
- Só texto: sem planilha nem banco binário (a planilha xlsx de versões anteriores é removida no render).
- Snapshot bruto e derivados ficam em `out/`, fora do vault — não vão para o repositório.
- `output.repo: true` (repositório de conhecimento): o render mantém blocos `dvinv` no `README.md` (ambientes,
  snapshot, como consultar, o que não está no repositório), no `.gitignore` (estado local do Obsidian, cópias de
  trabalho, `.env`) e no `.gitattributes` (LF; CSVs marcados como gerados), e imprime o resumo do `git status`.
  Não faz commit.

## Preservação entre extrações

- Corpo: tudo a partir da linha `%% dvinv:manual — … %%` é mantido.
- Properties mantidas: `status`, `responsavel`, `decisao`, `prazo`, `revisado`, `tags_extra`.
- O resto é regenerado. O render escreve numa cópia de trabalho (`<vault_root>/.dvinv-render-*`) e
  troca a pasta `<vault_folder>` só no fim, depois da varredura de segredos; se falhar, o vault anterior
  fica intacto. Edições feitas no vault *durante* o render se perdem (a cópia é tirada no início).
- Notas de componentes que não aparecem na extração atual: **removidas** se não têm nada abaixo do marcador
  manual; **mantidas** e listadas em `Notas fora do inventário atual.md` se têm curadoria. Notas sem o
  marcador do dvinv (escritas pelo time) nunca são tocadas.

## Bases geradas (`Bases/`)

Todas filtram por `file.inFolder("<vault_folder>")` + `tipo`, então cada ambiente tem as suas.

- **Tabelas**: Todas (soma de registros/colunas) · Customizadas · Mais automatizadas (fórmula `automacao`) · Com achados
- **Automações**: Por categoria · Cloud flows · Dono desativado · Parados há mais tempo (fórmula `dias_sem_alteracao`)
- **Plugin Steps**: Por tabela · Síncronos · Por assembly
- **Web Resources**: Todos por tipo · JS sem uso em formulário
- **Achados**: Por severidade · Abertos
- **Componentes**: assemblies, Custom APIs, apps, soluções, papéis e PCF agrupados por tipo
- **Componentes PCF**: Todos · Sem uso em formulário
- **Site Maps**: Todos (apps, subáreas, tabelas, URLs externas)
- **SLAs**: Por tabela · Não ativos
- **Segurança**: Business units · Equipes (por BU) · Usuários por BU · Usuários de aplicação

Para uma visão transversal a vários ambientes, crie uma Base sem o filtro de pasta e agrupe por
`ambiente` (skill `obsidian-bases`).

## Canvas

`Mapa do Ambiente.canvas` tem quatro grupos: Apps e agentes · Tabelas mais automatizadas (top 25) ·
Plugins · Integrações (conectores, hosts HTTP, service endpoints). As arestas são assembly → tabela
(nº de steps) e tabela → integração (flows). Os nós de arquivo abrem a nota. Edite à vontade (skill
`json-canvas`), mas saiba que o arquivo é **regenerado** a cada `render`: para um canvas autoral, salve com outro nome.
