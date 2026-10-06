# Changelog

## 0.2.0 — 2026-10-06

- **Uso de campos** (`deep.field_usage`): preenchimento real por coluna (agregação nativa, com
  paginação quando passa de 50 mil registros), matriz de uso (formulários, eventos, views, processos,
  flows, plugins, JS, repositório) e classificação em candidato seguro a remoção / sem dados na UI /
  sem dados com lógica / dados sem uso conhecido / em uso. Achados FLD-01..03.
- **Análise estática de JS**: Xrm.Page obsoleto, eval, endpoints SOAP/OData 2011, XHR síncrono,
  XrmServiceToolkit, jQuery, chamadas Xrm.WebApi, hosts externos e colunas lidas. Achados JS-01..06.
- **Estrutura de cloud flows**: árvore de ações, tipos, condições, tratamento de erro (runAfter
  Failed/TimedOut), colunas citadas. Achado FLW-01.
- **Definições de processos** (`deep.process_definitions`): colunas citadas no XAML/clientdata de
  workflows, business rules, actions e BPFs; varredura de segredos.
- **Armazenamento e auditoria** (`deep.storage`): maiores tabelas, notas e anexos de e-mail (quantidade,
  bytes com partição contra estouro de int32, tipos de arquivo), auditoria por tabela/ação e registro
  mais antigo. Achados STO-01, AUD-01.
- **Repositórios Git** (`repos:`): web resources idênticos/divergentes/sem fonte (com funções só de um
  lado), classes de plugin com/sem fonte, diff de métodos DLL decompilada × repo (com `ilspycmd`),
  segredos versionados, referências a colunas no código. Achados REPO-01..04.
- Segredos: novos padrões para `appSettings` .NET (`key="…Password" value="…"`) e connection strings.
- `--only` inclui automaticamente os coletores de que o escolhido depende.
- Notas novas: `05 Uso de Campos`, `06 Armazenamento e Auditoria`, `07 Repositórios`.

## 0.1.0 — 2026-10-06 (primeira versão)

Primeira versão.

- 17 coletores read-only: ambiente, soluções, tabelas/colunas/chaves, relacionamentos, option sets, web resources (com código), formulários (bibliotecas e eventos), views, ribbons, apps (model-driven, canvas, Copilot Studio), plugins (assemblies, classes, steps, imagens, DLLs), Custom APIs, service endpoints, processos (workflows, business rules, actions, BPFs, cloud/desktop flows), ALM (variáveis de ambiente, referências de conexão, conectores), segurança (BUs, papéis, privilégios, equipes, usuários) e saúde (plugin trace).
- Autenticação: Service Principal, MCP (cache do Dataverse CLI), Azure CLI e device code (`auto`).
- Vault Obsidian: notas com properties, 6 Bases, Canvas do ambiente, 21 regras de achados, preservação das edições do analista entre extrações.
- `diff` entre snapshots e ambientes.
- Redação de segredos em todas as saídas.
- Teste offline ponta a ponta (`tests/test_pipeline.py`).
