# Boas práticas Microsoft Learn × evidência no vault

Catálogo usado pelos prompts de impacto, diagnóstico, performance e arquitetura: cada prática tem a regra objetiva,
a página do Microsoft Learn de onde vem, onde a evidência aparece no vault e o achado automático que já a cobre.
Agentes: **cite o link da prática e a nota do vault** em toda recomendação; prática sem evidência no vault é
recomendação genérica e deve ser dita assim.

Legenda da coluna Achado:
- `ID` — já existe regra automática (ver `findings.md`);
- **lacuna (dado coletado)** — o vault já tem o dado; falta só a regra (candidata a novo achado);
- **lacuna (coletar)** — o kit ainda não coleta o dado; o agente só pode recomendar verificar.

Conferido em 19/10/2026. As páginas do Learn mudam: na dúvida, abra o link antes de afirmar.

## Plug-ins e workflows (servidor)

Fonte: [Best practices: plug-in and workflow development](https://learn.microsoft.com/power-apps/developer/data-platform/best-practices/business-logic/)

| ID | Prática | Onde ver no vault | Achado |
|---|---|---|---|
| BP-PLG-01 | Step de Update com **filtering attributes** (só as colunas que o plug-in usa) | nota do step: `filtering`, `mensagem` | PLG-01 |
| BP-PLG-02 | Evitar lógica **síncrona em Retrieve/RetrieveMultiple** | step: `mensagem`, `modo` | PLG-02 |
| BP-PLG-03 | Não **duplicar o registro** do mesmo step (mesma classe, mensagem, tabela e estágio) | `Plugin Steps/` agrupados por classe, mensagem, tabela, estágio | lacuna (dado coletado) |
| BP-PLG-04 | Manter a definição do assembly em **uma única solução** | nota do assembly: `solucoes` | lacuna (dado coletado) |
| BP-PLG-05 | Remover steps e customizações **desativados** | step: `ativo` | PLG-03 |
| BP-PLG-06 | Não usar `ExecuteMultipleRequest`/`ExecuteTransactionRequest` nem **paralelismo** dentro de plug-in | código (com `repos:` ou extra `binarios`) | lacuna (coletar: varredura do código) |
| BP-PLG-07 | Plug-in **sem estado** (sem campos de instância mutáveis) | código | lacuna (coletar) |
| BP-PLG-08 | Chamadas externas com **Timeout** definido e **KeepAlive = false** | código; hosts em service endpoints e no código | lacuna (coletar) |
| BP-PLG-09 | Erros com `InvalidPluginExecutionException` e rastreio com `ITracingService` | código; extra `saude` (trace) | PLG-04 (taxa de erro); resto lacuna (coletar) |
| BP-PLG-10 | Ler só as colunas necessárias (sem *all columns*) | código | lacuna (coletar) |
| BP-PLG-11 | Não registrar step em RetrieveMultiple de `annotation` nem no estágio do CRM 4.0 | step: `mensagem`, `tabela`, `estagio` | PLG-02 (parcial); resto lacuna (dado coletado) |

## Client scripting e formulários (model-driven)

Fontes: [Client scripting best practices](https://learn.microsoft.com/power-apps/developer/model-driven-apps/best-practices/business-logic/) ·
[Design forms for performance](https://learn.microsoft.com/power-apps/maker/model-driven-apps/design-performant-forms)

| ID | Prática | Onde ver no vault | Achado |
|---|---|---|---|
| BP-JS-01 | Não usar o endpoint **OData v2** / SOAP 2011 (usar Web API) | nota do web resource: análise de JS | JS-01 |
| BP-JS-02 | Requisições HTTP **assíncronas** (sem XHR síncrono) | análise de JS | JS-04 |
| BP-JS-03 | Não usar `Xrm.Page` (usar `formContext`) | análise de JS | JS-03 |
| BP-JS-04 | Evitar `window.top` | código do web resource | lacuna (dado coletado) |
| BP-JS-05 | Sem `console.log` em produção (memória e lentidão) | código do web resource | lacuna (dado coletado) |
| BP-JS-06 | Não abrir **novas janelas** (`window.open`, `openForm` com nova janela) | código do web resource | lacuna (dado coletado) |
| BP-JS-07 | Evitar `refreshRibbon` no `OnLoad` e em `EnableRule` | código do web resource; ribbons | lacuna (dado coletado) |
| BP-JS-08 | Carregar só o código necessário para cada formulário; biblioteca usada só em `OnChange`/`OnSave` não deve ir no `OnLoad` | nota do formulário: bibliotecas e handlers por evento | lacuna (dado coletado) |
| BP-JS-09 | `OnLoad`/`OnSave` assíncronos devolvendo `Promise`; cache de dados de configuração | código do web resource | revisão manual |
| BP-JS-10 | Remover dependências de bibliotecas removidas da plataforma (ex. jQuery 2.1.1 de `/_static/`, XrmServiceToolkit) | código do web resource | JS-05 (XrmServiceToolkit); jQuery lacuna (dado coletado) |
| BP-JS-11 | Não usar `eval()` nem equivalentes | análise de JS | JS-02 |
| BP-JS-12 | Sem `alert()` e sem script de depuração fora de DEV | código do web resource | lacuna (dado coletado) |
| BP-FRM-01 | **Aba padrão** leve: subgrids, quick views e timeline nas abas secundárias | XML do formulário | lacuna (coletar: contagem por aba) |
| BP-FRM-02 | Poucos controles por formulário; só os campos necessários | nota do formulário: `campos` | lacuna (dado coletado: contagem) |
| BP-FRM-03 | Trocar controles descontinuados (editable grid, controles legados) pelos atuais | XML do formulário (controles) | lacuna (coletar) |
| BP-UI-01 | Remover web resources e PCF sem uso | `Bases/Web Resources` (sem uso), PCF | UI-03, PCF-02 |
| BP-UI-02 | Formulário e botão não podem chamar biblioteca ou função inexistente | nota do formulário, ribbons | UI-01, UI-02 |

## Cloud flows (Power Automate)

Fontes: [Coding guidelines](https://learn.microsoft.com/power-automate/guidance/coding-guidelines/) ·
[Error handling](https://learn.microsoft.com/power-automate/guidance/coding-guidelines/error-handling) ·
[Optimize triggers](https://learn.microsoft.com/power-automate/guidance/coding-guidelines/optimize-power-automate-triggers)

| ID | Prática | Onde ver no vault | Achado |
|---|---|---|---|
| BP-FLW-01 | Tratamento de erro: escopos Try/Catch, *Run after* em falha, `Terminate`, log e notificação | nota do flow: estrutura e tratamento de erro | FLW-01 |
| BP-FLW-02 | **Condição de gatilho** / filtro OData e colunas de filtro no gatilho do Dataverse, para rodar só quando necessário | nota do flow: gatilho | lacuna (coletar: parâmetros do gatilho) |
| BP-FLW-03 | Controle de concorrência só quando necessário (é irreversível), de preferência num child flow | definição do flow | lacuna (coletar) |
| BP-FLW-04 | Política de retry **exponencial** em ações sujeitas a falha transitória | definição do flow | lacuna (coletar) |
| BP-FLW-05 | Usar o conector **Microsoft Dataverse** atual (o "legado" está descontinuado) | nota do flow: `conectores` | lacuna (dado coletado) |
| BP-FLW-06 | Dono ativo / conta de serviço; conexões vinculadas | flow: `proprietario_desativado`; `03 Integrações` | PRC-02, ALM-02 |
| BP-FLW-07 | Remover flows desligados que não serão reativados | flow: `ativo` | PRC-05 |

## Dados, consultas e limites de API

Fontes: [Service protection API limits](https://learn.microsoft.com/power-apps/developer/data-platform/api-limits) ·
[Well-Architected: optimize code](https://learn.microsoft.com/power-platform/well-architected/performance-efficiency/optimize-code)

| ID | Prática | Onde ver no vault | Achado |
|---|---|---|---|
| BP-API-01 | Integrações tratam **429** e respeitam `Retry-After`; lotes pequenos (começar com 10) e paralelismo controlado | `03 Integrações` (service endpoints, hosts HTTP, Custom APIs) | revisão manual |
| BP-API-02 | Limites por usuário e servidor: 6.000 requisições e 20 min de execução em 5 min; ~52 simultâneas | — (referência para desenhar evoluções) | referência |
| BP-DAT-01 | Preferir **coluna calculada, rollup ou Power Fx** a calcular e gravar por código | tabela: colunas `formula`; steps e flows que gravam a mesma coluna | lacuna (dado coletado) |
| BP-DAT-02 | Configurar antes de customizar; trabalho pesado de forma **assíncrona** | steps síncronos, workflows síncronos | PRC-03 (workflow síncrono) |
| BP-DAT-03 | Remover colunas sem dado e sem uso (após confirmar usos externos) | `05 Uso de Campos` | FLD-01 a FLD-04 |

## ALM e solução

Fonte: [Solution layers](https://learn.microsoft.com/power-platform/alm/solution-layers-alm)

| ID | Prática | Onde ver no vault | Achado |
|---|---|---|---|
| BP-ALM-01 | Fora de DEV, só **soluções gerenciadas**: customização não gerenciada (camada *Active*) por cima do deploy | extra `camadas` | ALM-07 |
| BP-ALM-02 | Customização sempre **dentro de solução** | processos e componentes não gerenciados fora de solução | ALM-04 |
| BP-ALM-03 | Evitar **patches** de solução (preferir upgrade) | soluções | lacuna (coletar: patch) |
| BP-ALM-04 | Solução declara o que exige: sem dependências ausentes | `Dependências ausentes.csv` | DEP-01 |
| BP-ALM-05 | Variáveis de ambiente com valor; referências de conexão vinculadas | `03 Integrações` | ALM-01, ALM-02 |
| BP-ALM-06 | Configuração por **variável de ambiente**, não URL fixa em código ou site map | análise de JS, site maps | JS-06, NAV-01 |

## Segurança

Fontes: [Security concepts](https://learn.microsoft.com/power-apps/developer/data-platform/security-concepts) ·
[Security in Dataverse (admin)](https://learn.microsoft.com/power-platform/admin/wp-security-cds)

| ID | Prática | Onde ver no vault | Achado |
|---|---|---|---|
| BP-SEG-01 | **Menor privilégio**: papéis customizados; privilégios se somam (vale o maior) | `04 Segurança`: matriz papel × tabela | lacuna (dado coletado); SEG-01 cobre o caso oposto (tabela sem Read) e SEG-05 papel sem uso |
| BP-SEG-02 | Usuário de aplicação sem System Administrator | extra `usuarios` | SEG-04 |
| BP-SEG-03 | Equipes por grupo do Entra para atribuição em escala; equipes enormes com papel pesam na autorização | `Equipes/` | SEG-02, SEG-03 |
| BP-SEG-04 | Segredo nunca em código, configuração de step ou variável de texto (usar variável Secret / Key Vault) | achado de segredos | SEC-01 |

## Descontinuações da plataforma

Fonte: [Important changes (deprecations)](https://learn.microsoft.com/power-platform/important-changes-coming)

| ID | Prática | Onde ver no vault | Achado |
|---|---|---|---|
| BP-DEP-01 | Migrar código do endpoint OData v2 e do SOAP para a Web API | análise de JS; código de plug-in | JS-01 |
| BP-DEP-02 | Workflows clássicos e diálogos: migrar para flows quando possível | processos | PRC-04 |
| BP-DEP-03 | Conector Dataverse legado → conector Microsoft Dataverse | flow: `conectores` | lacuna (dado coletado) |
| BP-DEP-04 | Controles e designers clássicos descontinuados (editable grid, controles legados) | XML do formulário | lacuna (coletar) |

## Solution checker × inventário

O [solution checker](https://learn.microsoft.com/power-apps/maker/data-platform/use-powerapps-checker) faz análise
estática de **soluções não gerenciadas**: plug-ins e custom workflow activities, web resources (JS/HTML), registro de
steps, flows (via Flow Checker) e Power Fx. O inventário lê o ambiente publicado, inclusive o que está em soluções
gerenciadas. Um complementa o outro:

| Pergunta | Solution checker | Inventário |
|---|---|---|
| O código tem padrão ruim (linha exata)? | sim, com linha e regra | parcial (análise de JS; plug-in só com `repos:`/`binarios`) |
| Onde o componente é usado e o que quebra se mudar? | não | sim (dependências, formulários, ribbons, flows) |
| Ambiente com soluções gerenciadas (HML/PRD)? | não analisa gerenciadas | sim, somente leitura |
| Segurança, uso de campos, saúde, camadas? | não | sim (núcleo + extras) |

Como rodar (sempre na solução **não gerenciada**, no ambiente de DEV ou a partir do zip exportado):

- **Portal:** make.powerapps.com → Soluções → `…` → *Solution checker* → *Run*; depois *Download results* (zip com Excel).
- **PowerShell / pipeline:** módulo [Microsoft.PowerApps.Checker.PowerShell](https://learn.microsoft.com/powershell/powerapps/get-started-powerapps-checker) ou `pac solution check`.
- **No editor, antes do deploy:** pacote ESLint [`@microsoft/eslint-plugin-power-apps`](https://www.npmjs.com/package/@microsoft/eslint-plugin-power-apps) (JS/TS).

Para o agente cruzar os dois: salve o relatório exportado no repositório do vault (ex. `checker/<solução>-<data>.xlsx`
ou convertido para CSV) e peça ao agente para ligar cada linha do relatório à nota do componente no vault.

Regras do checker que correspondem a práticas deste catálogo:

| Regra do checker | Severidade no checker | Prática | Achado do inventário |
|---|---|---|---|
| `meta-remove-dup-reg` | crítica | BP-PLG-03 | lacuna (dado coletado) |
| `meta-avoid-reg-no-attribute` | média | BP-PLG-01 | PLG-01 |
| `meta-avoid-reg-retrieve` | média | BP-PLG-02 | PLG-02 |
| `meta-avoid-retrievemultiple-annotation`, `meta-avoid-crm4-event` | alta / média | BP-PLG-11 | PLG-02 (parcial) |
| `meta-remove-inactive` | baixa | BP-PLG-05 | PLG-03 |
| `web-use-async` | crítica | BP-JS-02 | JS-04 |
| `web-avoid-crm2011-service-odata`, `web-avoid-crm2011-service-soap` | crítica | BP-JS-01, BP-DEP-01 | JS-01 |
| `web-avoid-2011-api`, `web-use-client-context` | alta / média | BP-JS-03 | JS-03 |
| `web-avoid-window-top` | alta | BP-JS-04 | lacuna (dado coletado) |
| `web-remove-console` | média | BP-JS-05 | lacuna (dado coletado) |
| `web-avoid-modals`, `web-use-navigation-api` | alta / média | BP-JS-06 | lacuna (dado coletado) |
| `web-avoid-ui-refreshribbon` | crítica | BP-JS-07 | lacuna (dado coletado) |
| `web-avoid-eval` | crítica | BP-JS-11 | JS-02 |
| `web-remove-alert`, `web-remove-debug-script` | média | BP-JS-12 | lacuna (dado coletado) |
| `web-use-relative-uri` | média | BP-ALM-06 | JS-06, NAV-01 |

Em ambiente gerenciado (managed environment) o checker pode **bloquear** a importação com violação crítica; as regras
críticas acima são as primeiras a corrigir.

## Ferramentas complementares (fora do inventário)

Para medir o que o inventário não mede em tempo de execução: Flow Checker, Monitor do Power Apps, Performance Insights
de apps model-driven e o Power CAT Toolkit. Os prompts podem sugerir rodá-los para confirmar uma recomendação.
