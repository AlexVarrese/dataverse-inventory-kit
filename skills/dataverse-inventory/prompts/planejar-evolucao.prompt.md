---
name: planejar-evolucao
description: Plano para um requisito novo reaproveitando o que já existe no ambiente, seguindo as boas práticas do Microsoft Learn
argument-hint: requisito, ex. "aprovar limite de crédito acima de 100 mil"
agent: agent
---

# Planejar evolução

## Objetivo

Antes de construir, descobrir o que já existe no ambiente que atende ou pode ser estendido para o requisito, e propor
o caminho com menos código novo. Para consultor funcional, PO e arquiteto.

## Entrada

- Requisito: ${input:requisito:o que o negócio precisa}
- Ambiente: ${input:ambiente:pasta do ambiente no vault, ex. Dataverse/HML}

## Roteiro no vault

1. Leia o `AGENTS.md` e o `AGENT-INDEX.md` do ambiente. Anote a data do snapshot.
2. Tire do requisito os termos de negócio (entidades, campos, eventos, sistemas externos) e procure cada um com
   `buscar` (nome técnico e de exibição): tabelas, colunas, Custom APIs, flows, workflows, regras de negócio, web
   resources, PCF, variáveis de ambiente, referências de conexão.
3. Para cada candidato, leia a nota: o que faz (estrutura do flow, mensagem do step, parâmetros da Custom API,
   funções do JS), se está ativo, dono, solução e achados abertos.
4. Classifique cada candidato: reaproveitar como está, estender ou não serve.
5. Para o que for novo, prefira nesta ordem: configuração (coluna calculada, rollup ou fórmula, regra de negócio,
   view), flow, código. Use o prefixo do publisher da solução de destino (`prefixo:` em `Soluções/`) e variável de
   ambiente para tudo que muda entre ambientes.
6. Riscos do caminho escolhido: tabela que vai receber lógica já tem muitos steps síncronos? Componente a estender
   mudou na última extração (`Mudanças.csv`, a partir da segunda extração)? Papéis que precisarão de privilégio
   (`04 Segurança`)?

Não leia inteiros os CSVs nem a Matriz de Dependências: use grep ou os comandos `onde-usa`, `impacto`, `depende` e
`buscar` indicados no `AGENTS.md`.

## Checklist de boas práticas

Em [boas-praticas.md](boas-praticas.md): BP-DAT-01 e BP-DAT-02 (configurar antes de customizar, assíncrono quando
possível); BP-PLG-01, BP-PLG-02 (se houver plug-in); BP-FLW-01, BP-FLW-02, BP-FLW-05 (se houver flow); BP-ALM-01 a
BP-ALM-06; BP-SEG-01.

## Formato da resposta

O padrão de `padrao-prompts.md`, com:

- **Resposta:** o caminho recomendado (reaproveitar, estender ou criar) e por quê.
- **Evidências:** uma linha por candidato encontrado, com a coluna extra `Reaproveitável` (sim, com ajuste, não).
- **Recomendações:** as etapas do plano em ordem, com a coluna extra `Etapa` (desenho, construção, ALM, teste). A
  última recomendação é rodar `/impacto-evolutiva` sobre o desenho escolhido.
- **Não verificado:** regras de negócio que só o time conhece, integrações fora do Dataverse, licenças.

## Limites

- Somente leitura: não altere o ambiente, não rode `extract`, `render` ou `all` e não use ferramentas que escrevem no
  Dataverse.
- Não exiba `.env`, tokens ou segredos: cite só o ID do achado (SEC-01, REPO-01).
- Ausência de um componente no vault não prova que ele não exista: veja a data do snapshot e o escopo da extração.
- O plano é proposta; a decisão é do time.
