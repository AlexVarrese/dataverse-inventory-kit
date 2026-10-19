# Consultas de impacto sobre o vault

O **vault é o produto**: publicado num repositório, o time consulta pelo Obsidian, pelo Copilot, pelo
Claude ou por grep — tudo texto, com diff legível entre extrações. Perguntas **estruturais** (impacto de
uma evolutiva, onde um campo é usado, do que um componente depende, se algo existe) são respondidas
pelos CSVs da pasta do ambiente, sem ler notas:

| Arquivo | Uma linha por… | Colunas principais |
|---|---|---|
| `Dependências.csv` | aresta que toca o escopo (origem **depende de** destino) | `Origem`, `Origem id`, `Origem tipo`, `Origem nome`, `Relação`, `Fonte`, `Destino`, `Destino id`, `Destino tipo`, `Destino nome`, `Nota origem`, `Nota destino`, `Origem no escopo`, `Destino no escopo` |
| `Componentes.csv` | componente do ambiente (100%, todas as soluções) | `Chave`, `Tipo`, `Código do tipo`, `Nome`, `Pai`, `Id` (GUID), `No escopo`, `Só em soluções gerenciadas`, `Soluções`, `Nota` |
| `Equipes.csv` | equipe estrutural | BU, grupo do Entra, membros, papéis, id |

## Chave

A mesma nos CSVs, no frontmatter (`chave:`) e nos comandos: `<tipo>:<nome>` para o que tem nota
(`table:account`, `column:account.contoso_x`, `webresource:contoso_/js/x.js`, `process:<guid>`, `sla:<guid>`…);
fora do escopo, `table:`/`column:` pelo nome lógico e o resto pelo GUID. Toda nota tem também `id:` (GUID do
Dataverse): um GUID tirado de log, plugin trace ou chamado leva à nota com `grep -rl "id: <guid>"`.

## Comandos

| Pergunta | grep (qualquer agente/terminal) | dvinv |
|---|---|---|
| Existe? Qual a chave? | `grep -i "contoso_x" Componentes.csv` | `dvinv.py buscar contoso_x --vault <pasta>` |
| Quem depende de X? | `grep ";column:account.contoso_x;" Dependências.csv` | `dvinv.py onde-usa account.contoso_x --vault <pasta>` |
| Impacto em cadeia | repetir o grep para cada origem | `dvinv.py impacto account.contoso_x --profundidade 2 --vault <pasta>` |
| Do que X depende? | `grep "^webresource:contoso_/js/x.js;" Dependências.csv` | `dvinv.py depende contoso_/js/x.js --vault <pasta>` |

`<pasta>` = pasta do ambiente no vault (ex. `Dataverse/PRD`). `--vault` dispensa `inventory.yaml` e
credenciais: funciona em qualquer clone do repositório. Com `-c inventory.yaml`, usa o vault do config.
`<alvo>` aceita chave, GUID, `tabela.coluna`, nome lógico de tabela ou nome do componente; se for ambíguo, o
comando lista as chaves candidatas. Opções: `--profundidade N`, `--limite N` (padrão 150; escopo primeiro).

## O que entra nas arestas

- **plataforma** — tabela `dependency` do Dataverse (o que ela usa para bloquear exclusão): formulário/view/
  business rule → coluna, sitemap → tabela, PCF como controle de coluna/view… Entram as que tocam o escopo, nos
  dois sentidos; ficam de fora as internas de metadados (coluna → tabela) e nativo → nativo.
- **inferida** — o que o kit deduz e a plataforma não registra: uso de campos (evento de formulário, controle
  PCF, filtering attribute e imagem de plugin, campos de workflow/flow, `getAttribute` em JS), JS chamando
  Custom API/tabela/host, flow lendo variável/conexão/tabela, step registrado em mensagem, papel com privilégio,
  tabela virtual → provedor → plugin, site map → tabela, SLA → coluna de condição/KPI.

Ausência de aresta **não prova** ausência de uso: integrações externas, Power BI, apps fora do Dataverse e
código não coletado não aparecem. Trate "sem dependentes" como candidato a investigação.

## Conjunto completo da plataforma

O vault traz só as arestas que tocam o escopo. **Todas** as dependências registradas pelo Dataverse
(inclusive nativo → nativo) ficam no snapshot, `_raw/<run_id>/platform_dependencies.json` (formato compacto:
nós + arestas), na máquina de quem extraiu — fora do vault e do Git.
