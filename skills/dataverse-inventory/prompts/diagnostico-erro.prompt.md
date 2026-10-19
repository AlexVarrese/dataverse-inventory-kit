---
name: diagnostico-erro
description: Triagem de um erro em produção ou homologação a partir do inventário — causa mais provável, evidências e o que verificar
argument-hint: mensagem de erro, GUID ou trecho do log; onde aconteceu (salvar, abrir, flow, integração)
agent: agent
---

# Diagnóstico de erro

## Objetivo

Chegar à causa mais provável de um erro usando os componentes inventariados, e dizer o que verificar no ambiente para
confirmar. Para sustentação e desenvolvedor.

## Entrada

- Erro: ${input:erro:mensagem, código, GUID ou trecho do log}
- Contexto: ${input:contexto:onde e quando — ex. ao salvar account, flow X, integração Y, desde o deploy de 15/10}
- Ambiente: ${input:ambiente:pasta do ambiente no vault, ex. Dataverse/HML}

Se não houver mensagem nem identificador, pergunte. Sem o contexto, siga e marque a confiança como baixa.

## Roteiro no vault

1. Leia o `AGENTS.md` e o `AGENT-INDEX.md` do ambiente. Anote a data do snapshot: componente criado depois dela não
   está no vault.
2. Tire do erro os identificadores e localize cada um:
    - GUID: `grep -rl "id: <guid>"` nas notas e a coluna `Id` do `Componentes.csv`;
    - nome de classe de plug-in, web resource, flow, Custom API ou variável de ambiente: `buscar`;
    - função JS: `grep -rl "<função>" Tabelas/ Comandos/ "Web Resources/"` (o handler completo aparece nos eventos
      de formulário e nos comandos; o nome curto, na nota do web resource);
    - código de erro (`0x8004…`): procure na página de [códigos de erro do Dataverse](https://learn.microsoft.com/power-apps/developer/data-platform/reference/web-service-error-codes).
3. Monte o que roda no ponto do erro. Ao salvar ou abrir uma tabela, use as seções *Eventos de formulário* e
   *Automação* da nota da tabela e as notas dos steps:
    - no formulário: bibliotecas e handlers de `OnLoad`/`OnSave`/`OnChange`, regras de negócio;
    - no servidor, síncrono: steps por `estagio` (PreValidation → PreOperation → PostOperation) e `ordem`, e workflows
      em tempo real. Exceção em qualquer etapa síncrona desfaz a transação inteira;
    - depois, assíncrono: steps assíncronos e cloud flows com gatilho na tabela.
4. Siga a triagem abaixo pelo sintoma.
5. `grep "<chave>" Mudanças.csv` para os suspeitos (existe a partir da segunda extração): componente que mudou
   pouco antes do erro é o primeiro suspeito.
6. Achados com `status` aberto ou confirmado nos suspeitos (`Achados/`).

| Sintoma | Onde olhar no vault | Achados que explicam |
|---|---|---|
| erro de script no formulário (nome de arquivo ou função) | nota do web resource; *Eventos de formulário* na nota da tabela | UI-01, UI-02, JS-01, JS-03, JS-04 |
| mensagem de regra de negócio ou de plug-in ao salvar | steps síncronos da tabela e mensagem; classe do plug-in | PLG-04 (extra `saude`), PLG-01 |
| falta de privilégio | `04 Segurança` (papéis × tabela), equipes, usuário (extra `usuarios`) | SEG-01, SEG-03 |
| flow falhou ou desligou | nota do flow: dono, conectores, conexões, variáveis | PRC-02, ALM-01, ALM-02 |
| integração, timeout ou 429 | `03 Integrações` (hosts, service endpoints, Custom APIs) | JS-06, ALM-03 |
| começou após um deploy | `Mudanças.csv`, `10 Histórico de Soluções`, camadas | ALM-06, ALM-07, DEP-01 |
| link ou menu leva a outro ambiente | nota do site map | NAV-01 |

Não leia inteiros os CSVs nem a Matriz de Dependências: use grep ou os comandos `onde-usa`, `impacto`, `depende` e
`buscar` indicados no `AGENTS.md`.

## Checklist de boas práticas

Para a causa provável, cite em [boas-praticas.md](boas-praticas.md) a prática que evitaria o erro (ex. BP-PLG-09
tratamento de exceção e trace, BP-FLW-01 tratamento de erro no flow, BP-JS-02 HTTP assíncrono, BP-API-01 tratar 429).

## Formato da resposta

O padrão de `padrao-prompts.md`, com:

- **Resposta:** a causa mais provável e a confiança.
- **Evidências:** uma linha por hipótese, da mais para a menos provável, com a coluna extra `Hipótese`.
- **Recomendações:** coluna extra `Tipo`:
  - *verificar*: o que conferir no ambiente para confirmar (plugin trace log, histórico de execução do flow, Monitor
    do Power Apps, ferramentas do navegador), sempre só leitura;
  - *corrigir*: a correção proposta;
  - *prevenir*: a prática que evita a repetição.
- **Não verificado:** o que o vault não mostra (dados do registro, payload da integração, log em tempo real).

## Limites

- Somente leitura: não altere o ambiente, não rode `extract`, `render` ou `all`, não reprocesse nem reative nada e
  não use ferramentas que escrevem no Dataverse.
- Não exiba `.env`, tokens, segredos nem dados pessoais do log; cite só o ID do achado (SEC-01, REPO-01).
- Ausência de dependência no vault não prova ausência de uso.
- O diagnóstico é hipótese até ser confirmado no ambiente; a decisão é do time.
