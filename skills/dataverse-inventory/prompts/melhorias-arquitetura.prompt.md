---
name: melhorias-arquitetura
description: Melhorias de arquitetura priorizadas por risco e esforço, a partir do inventário e das boas práticas do Microsoft Learn
argument-hint: escopo, ex. "ambiente", solução ContosoCore ou tabela account
agent: agent
---

# Melhorias de arquitetura

## Objetivo

Listar as melhorias de arquitetura que mais reduzem risco, priorizadas por risco e esforço, para o arquiteto e o CoE
planejarem as próximas evoluções.

## Entrada

- Escopo: ${input:escopo:"ambiente", uma solução ou uma tabela}
- Ambiente: ${input:ambiente:pasta do ambiente no vault, ex. Dataverse/HML}

## Roteiro no vault

1. Leia o `AGENTS.md` e o `AGENT-INDEX.md` do ambiente. Anote a data do snapshot.
2. Achados com `status` aberto ou confirmado no escopo (`Achados/`), agrupados pelos temas abaixo.
3. Acoplamento: seção *Impacto por tabela* da `08 Matriz de Dependências` (só essa seção) e `dependentes:` no
   frontmatter das notas; achado DEP-02. Tabela central com muita lógica síncrona é o ponto de maior risco.
4. Lógica espalhada: a mesma coluna lida ou gravada por JS, regra de negócio, plug-in e flow
   (`onde-usa <tabela.coluna>`, `05 Uso de Campos`). Candidata a consolidar num lugar só.
5. Código onde a configuração resolve: plug-in ou JS que calcula valor (coluna calculada, rollup ou fórmula),
   workflows clássicos (PRC-04), JS legado (JS-01, JS-03, JS-05).
6. ALM: notas de `Soluções/` (gerenciada, prefixo, componentes), achados ALM-*, DEP-01, URLs fixas (JS-06, NAV-01),
   repositórios (REPO-*).
7. Segurança: `04 Segurança` e achados SEG-* e SEC-01.
8. Integrações: `03 Integrações` (hosts, Custom APIs, service endpoints, conectores) e tratamento de erro dos flows
   (FLW-01).
9. Descontinuações: práticas BP-DEP-* que aparecem no escopo.

Não leia inteiros os CSVs nem a Matriz de Dependências: use grep ou os comandos `onde-usa`, `impacto`, `depende` e
`buscar` indicados no `AGENTS.md`.

## Checklist de boas práticas

Em [boas-praticas.md](boas-praticas.md), todas as áreas; use a seção *Lacunas priorizadas* para o que o kit ainda não
detecta sozinho. Princípio do Well-Architected: configurar antes de customizar (BP-DAT-01, BP-DAT-02).

## Formato da resposta

O padrão de `padrao-prompts.md`, com:

- **Resposta:** as 5 melhorias de maior valor, em ordem.
- **Evidências:** coluna extra `Tema` (acoplamento, lógica espalhada, código × configuração, ALM, segurança,
  integração, descontinuação).
- **Recomendações:** coluna extra `Quadrante`:
  - *fazer já*: risco alto, esforço P ou M;
  - *planejar*: risco alto, esforço G;
  - *oportunidade*: risco baixo, esforço P;
  - *backlog*: o resto.
  Risco alto = pode parar o negócio, expor dado ou quebrar no próximo deploy ou atualização da plataforma.
- **Não verificado:** o que pede conversa com o time (motivo de uma escolha, integração fora do Dataverse, volume
  real de uso).

## Limites

- Somente leitura: não altere o ambiente, não rode `extract`, `render` ou `all` e não use ferramentas que escrevem no
  Dataverse.
- Não exiba `.env`, tokens ou segredos: cite só o ID do achado (SEC-01, REPO-01).
- Ausência de dependência no vault não prova ausência de uso.
- A priorização é proposta; a decisão é do time e do CoE.
