---
name: revisao-performance
description: Revisão de performance de uma tabela, app ou do ambiente a partir do inventário e das boas práticas do Microsoft Learn
argument-hint: escopo, ex. tabela account, formulário Conta Principal ou "ambiente"
agent: agent
---

# Revisão de performance

## Objetivo

Apontar, pela configuração inventariada, os gargalos de performance mais prováveis e o que fazer primeiro. Para
arquiteto, desenvolvedor e sustentação.

## Entrada

- Escopo: ${input:escopo:tabela, formulário, app ou "ambiente"}
- Ambiente: ${input:ambiente:pasta do ambiente no vault, ex. Dataverse/HML}
- Sintoma (opcional): ${input:sintoma:ex. formulário de conta demora a abrir; salvar caso leva 10 s}

Escopo "ambiente": comece pelas 5 tabelas com mais automação síncrona e mais registros.

## Roteiro no vault

1. Leia o `AGENTS.md` e o `AGENT-INDEX.md` do ambiente. Anote a data do snapshot.
2. Delimite o escopo: nota da tabela (seções *Formulários*, *Automação*, *Uso das colunas customizadas*). Para o
   ambiente: `grep -l "modo: Síncrono" "Plugin Steps/"*.md`, contando por `tabela:`, e *Maiores tabelas* em
   `06 Armazenamento e Auditoria` (volume).
3. Servidor: steps síncronos por tabela e mensagem (`modo`, `estagio`, `ordem`, `filtering`), steps em
   Retrieve/RetrieveMultiple, steps repetidos (mesma classe, mensagem, tabela e estágio), workflows em tempo real.
   Com o extra `saude`: achado PLG-04 (taxa de erro por classe).
4. Formulário: bibliotecas e handlers por evento (*Eventos de formulário* na nota da tabela). Em cada web resource
   carregado no `OnLoad`: `tamanho_bytes`, análise estática (HTTP síncrono, OData v2, `Xrm.Page`) e o código, quando
   estiver na nota (`refreshRibbon`, `console`, `window.top`, `window.open`). Quantidade de campos e controles PCF.
5. Flows: na nota de cada flow com gatilho na tabela (`flows_que_tocam`), o gatilho e o número de ações. Gatilho do
   Dataverse sem colunas de filtro ou condição roda a cada alteração.
6. Dados: tabelas grandes com lógica síncrona; colunas sem dado que ainda estão em formulário (FLD-02); anexos e
   auditoria (STO-01, AUD-01).
7. Integrações: hosts chamados por JS ou plug-in (`03 Integrações`) dentro de evento síncrono.
8. Achados abertos no escopo (`Achados/`).

Não leia inteiros os CSVs nem a Matriz de Dependências: use grep ou os comandos `onde-usa`, `impacto`, `depende` e
`buscar` indicados no `AGENTS.md`.

## Checklist de boas práticas

Em [boas-praticas.md](boas-praticas.md): BP-PLG-01, BP-PLG-02, BP-PLG-03, BP-PLG-11 (servidor); BP-JS-02, BP-JS-04 a
BP-JS-08, BP-FRM-01, BP-FRM-02 (formulário); BP-FLW-02 (flows); BP-DAT-01, BP-DAT-02 (lógica e dados); BP-API-01
(integrações). Para medir, sugira solution checker, Monitor e Performance Insights (seção *Ferramentas
complementares* do catálogo).

## Formato da resposta

O padrão de `padrao-prompts.md`, com:

- **Resposta:** os 3 gargalos mais prováveis, em ordem.
- **Evidências:** coluna extra `Camada` (servidor, formulário, flow, dados, integração).
- **Recomendações:** coluna extra `Ganho esperado` (alto, médio, baixo). Alto = sai do caminho síncrono do usuário
  (abrir ou salvar) ou deixa de rodar a cada alteração.
- **Não verificado:** tempos reais. O inventário é estático; confirme com Monitor, Performance Insights ou o plugin
  trace log antes de priorizar.

## Limites

- Somente leitura: não altere o ambiente, não rode `extract`, `render` ou `all` e não use ferramentas que escrevem no
  Dataverse.
- Não exiba `.env`, tokens ou segredos: cite só o ID do achado (SEC-01, REPO-01).
- Ausência de dependência no vault não prova ausência de uso.
- A revisão é insumo; a decisão é do time.
