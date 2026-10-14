# Índice SQLite e consultas para agentes

O vault é para leitura humana e curadoria. Para perguntas **estruturais** (impacto de uma evolutiva,
onde um campo é usado, do que um componente depende, se algo existe no ambiente), consulte o índice:
uma consulta devolve poucas linhas com o caminho da nota, em vez de abrir notas grandes.

## Onde fica

`<derived_dir>/<run_id>/inventario.sqlite` (padrão `out/<ambiente>/_derived/<run_id>/`), gerado pelo
`render`. Fica **fora** do vault: é binário, regenerável a partir do snapshot e não deve ir para o Git.
Tem a mesma sensibilidade do vault. Antes de publicar, todo texto do banco passa pela varredura de
segredos (fail-closed).

## Comandos

Todos leem o índice mais recente (ou `--indice <arquivo|run_id>`), só leitura:

| Comando | Uso | Exemplo |
|---|---|---|
| `buscar <texto>` | achar o componente e a chave | `dvinv.py buscar sitemap -c inventory.yaml` |
| `onde-usa <alvo>` | quem depende **diretamente** do alvo | `dvinv.py onde-usa account.esab_segmento -c inventory.yaml` |
| `impacto <alvo>` | cadeia de quem depende (padrão 2 níveis) | `dvinv.py impacto esab_/js/account.js --profundidade 3 -c inventory.yaml` |
| `depende <alvo>` | do que o alvo precisa | `dvinv.py depende "Contoso - Sync conta → ERP" -c inventory.yaml` |

`<alvo>` aceita: chave (`table:account`, `column:account.x`, `webresource:...`), GUID, `tabela.coluna`,
nome lógico da tabela ou nome do componente. Se for ambíguo, o comando lista as chaves candidatas.

Opções: `--profundidade N`, `--limite N` (padrão 150 linhas; os do escopo vêm primeiro),
`--internas` (inclui arestas internas de metadados, ex. coluna → tabela, *Solution Internal*).

## Saída (texto compacto)

```
# Impacto — account [Tabela] ·escopo (table:account)
nota: Dataverse/ESAB-TEST/Tabelas/account.md
57 componente(s) — quem depende (profundidade 1; sem arestas internas de metadados)

nível | tipo | qtde
1 | System Form | 38
...
nível | componente | relação | nota
1 | Conta Principal [Formulário] ·escopo | formulário de | —
1 | esab_/js/account.js [Web resource] ·escopo | consulta/grava (JS) | Dataverse/ESAB-TEST/Web Resources/esab_-js-account.js.md
```

Depois, abra só as notas listadas. Componentes **sem nota** estão fora do escopo (catálogo de 100%) —
o nome e o tipo vêm do catálogo; para detalhe use o MCP do Dataverse ou `dv-metadata`.

## Esquema (para SQL direto)

| Tabela | Colunas |
|---|---|
| `componentes` | `id`, `chave`, `guid`, `ct` (componenttype), `tipo`, `nome`, `pai`, `no_escopo`, `nota` |
| `dependencias` | `origem` **depende de** `destino` (ids de `componentes`), `relacao`, `fonte` (`plataforma`/`inferida`), `interna`, `detalhe` |
| `solucoes` / `componente_solucao` | pertencimento a soluções (todas, inclusive gerenciadas) |
| `tipos` | `ct` → nome do tipo e fonte do rótulo |
| `achados` | achados automáticos por componente |
| `meta` | ambiente, url, run_id, data da extração |

Exemplo — colunas customizadas de `account` sem nenhum dependente (candidatas a investigar):

```sql
SELECT c.nome FROM componentes c
WHERE c.chave LIKE 'column:account.%' AND c.no_escopo = 1
  AND NOT EXISTS (SELECT 1 FROM dependencias d WHERE d.destino = c.id AND d.interna = 0);
```

## Origem das arestas

- **plataforma** — tabela `dependency` do Dataverse, lida inteira (centenas de milhares de linhas): o
  que a plataforma usa para bloquear exclusão (view → coluna, formulário → web resource, sitemap → tabela,
  PCF como controle de coluna/view…).
- **inferida** — o que o kit deduz e a plataforma não registra: JS chamando Custom API/tabela/host,
  flow lendo variável/conexão/tabela, step registrado em mensagem/tabela, papel com privilégio na tabela,
  tabela virtual → provedor → classe de plugin.

Ausência de aresta **não prova** ausência de uso: integrações externas, Power BI, apps fora do
Dataverse e código não coletado não aparecem. Trate "sem dependentes" como candidato a investigação.
