# Esquema do vault

Toda nota gerada tem `ambiente`, `tipo`, `tags` (`dataverse`, `dataverse/<tipo>`, `ambiente/<slug>`)
e `extraido_em` (Date). Links em properties são strings `"[[caminho|alias]]"`, como pede o Obsidian.

| `tipo` | Pasta | Properties principais |
|---|---|---|
| `indice` | `00 Índice.md` | — |
| `secao` | `01 Ambiente`, `03 Integrações`, `04 Segurança`, `05 Uso de Campos`, `06 Armazenamento e Auditoria`, `07 Repositórios`, `08 Matriz de Dependências` | `url`, `versao` |
| `tabela` | `Tabelas/` | `nome_logico`, `nome_exibicao`, `aliases`, `customizada`, `gerenciada`, `propriedade`, `registros`, `colunas_custom`, `colunas_total`, `relacionamentos`, `formularios`, `plugin_steps`, `processos`, `flows_que_tocam`, `achados`, `escopo`, `solucoes` |
| `plugin-assembly` | `Plugins/` | `versao`, `isolamento`, `origem`, `gerenciado`, `tipos`, `steps`, `modificado` |
| `plugin-step` | `Plugin Steps/` | `assembly` (link), `classe`, `handler`, `mensagem`, `tabela` (link), `estagio`, `modo`, `ordem`, `ativo`, `filtering`, `imagens`, `impersonando` |
| `processo` | `Processos/<Workflows\|Business Rules\|Actions\|BPFs\|Cloud Flows\|Desktop Flows>/` | `categoria`, `tabela` (link), `ativo`, `modo`, `gatilho`, `conectores`, `hosts_http`, `proprietario`, `proprietario_desativado`, `modificado`, `criado`, `modificado_por` |
| `webresource` | `Web Resources/` | `tipo_arquivo`, `tamanho_bytes`, `funcoes`, `formularios`, `segredos`, `modificado` |
| `customapi` | `Custom APIs/` | `binding`, `tabela` (link), `funcao`, `privada`, `plugin` |
| `app` | `Apps/` | `subtipo` (model-driven / canvas / agente Copilot Studio), `nome_unico` |
| `solucao` | `Soluções/` | `versao`, `gerenciada`, `publisher`, `prefixo`, `componentes` |
| `papel` | `Papéis/` | `gerenciado`, `tabelas_com_privilegio` |
| `achado` | `Achados/` | `id_achado`, `severidade`, `titulo`, `metrica`, **`status`**, **`responsavel`** |
| `comparacao` | `Comparações/` | `ambiente_a`, `ambiente_b`, `data_a`, `data_b` |

Notas de componente que participam do grafo também recebem `dependencias` (quantos componentes ela usa)
e `dependentes` (quantos a usam), e uma seção **Dependências** com *Depende de* / *Usado por* agrupados
pela relação. ⚙︎ marca dependência registrada pela plataforma.

## Preservação entre extrações

- Corpo: tudo a partir da linha `%% dvinv:manual — … %%` é mantido.
- Properties mantidas: `status`, `responsavel`, `decisao`, `prazo`, `revisado`, `tags_extra`.
- O resto é regenerado. Componentes que sumiram do ambiente **não** são apagados do vault: use o
  `diff` para identificá-los e arquive manualmente.

## Bases geradas (`Bases/`)

Todas filtram por `file.inFolder("<vault_folder>")` + `tipo`, então cada ambiente tem as suas.

- **Tabelas**: Todas (soma de registros/colunas) · Customizadas · Mais automatizadas (fórmula `automacao`) · Com achados
- **Automações**: Por categoria · Cloud flows · Dono desativado · Parados há mais tempo (fórmula `dias_sem_alteracao`)
- **Plugin Steps**: Por tabela · Síncronos · Por assembly
- **Web Resources**: Todos por tipo · JS sem uso em formulário
- **Achados**: Por severidade · Abertos
- **Componentes**: assemblies, Custom APIs, apps, soluções e papéis agrupados por tipo

Para uma visão transversal a vários ambientes, crie uma Base sem o filtro de pasta e agrupe por
`ambiente` (skill `obsidian-bases`).

## Canvas

`Mapa do Ambiente.canvas` tem quatro grupos: Apps e agentes · Tabelas mais automatizadas (top 25) ·
Plugins · Integrações (conectores, hosts HTTP, service endpoints). As arestas são assembly → tabela
(nº de steps) e tabela → integração (flows). Os nós de arquivo abrem a nota. Edite à vontade (skill
`json-canvas`), mas saiba que o arquivo é **regenerado** a cada `render`: para um canvas autoral, salve com outro nome.
