---
name: impacto-evolutiva
description: Parecer de impacto de uma evolutiva a partir do inventário — o que afeta, o que ajustar e testar, e o risco
argument-hint: história ou mudança, ex. "tornar account.contoso_limite obrigatória"
agent: agent
---

# Impacto de evolutiva

## Objetivo

Dizer, antes do desenvolvimento, o que uma evolutiva afeta no ambiente, o que precisa ser ajustado e testado e qual o
risco. Para desenvolvedor, arquiteto e PO.

## Entrada

- Evolutiva: ${input:evolutiva:história ou mudança, ex. tornar account.contoso_limite obrigatória}
- Ambiente: ${input:ambiente:pasta do ambiente no vault, ex. Dataverse/HML}

Se faltar a evolutiva, ou houver mais de um ambiente no `AGENTS.md` e nenhum for informado, pergunte. Se a história
não nomeia componentes, proponha os candidatos encontrados em `Componentes.csv` e peça confirmação antes do parecer.

## Roteiro no vault

1. Leia o `AGENTS.md` e o `AGENT-INDEX.md` do ambiente. Anote a data do snapshot.
2. Quebre a evolutiva em mudanças: criar, alterar ou remover × componente (tabela, coluna, formulário, view, plug-in
   step, flow, workflow, regra de negócio, JS, ribbon, site map, PCF, Custom API, variável de ambiente, papel).
3. Para cada componente que já existe: `buscar` (chave e nota), `onde-usa` (dependentes diretos = impacto direto) e
   `impacto --profundidade 2` (segundo nível = impacto indireto).
4. Na nota de cada componente afetado, leia o que importa para a mudança:
    - tabela: `plugin_steps`, `processos`, `flows_que_tocam`, `formularios`, `achados`;
    - plug-in step: `modo`, `estagio`, `filtering`, `imagens` (a coluna mudada está no filtering ou numa imagem?);
    - flow: gatilho, conectores, hosts HTTP;
    - web resource: funções e campos usados;
    - coluna: `05 Uso de Campos` (tem dado? quem lê ou grava?).
5. Se a mudança toca dado integrado, veja `03 Integrações` (Custom APIs, service endpoints, hosts). Se cria tabela ou
   coluna, ou muda acesso, veja `04 Segurança` (papéis com privilégio na tabela).
6. ALM: coluna `Soluções` do `Componentes.csv` (em que solução a mudança entra) e achados ALM-07 e DEP-01 nos
   componentes afetados.
7. `grep "<chave>" Mudanças.csv` (existe a partir da segunda extração): o componente mudou na última extração?
   Pode haver outra entrega em curso.
8. Achados com `status` aberto ou confirmado nos componentes afetados (`Achados/`).

Não leia inteiros os CSVs nem a Matriz de Dependências: use grep ou os comandos `onde-usa`, `impacto`, `depende` e
`buscar` indicados no `AGENTS.md`.

## Checklist de boas práticas

Em [boas-praticas.md](boas-praticas.md), conforme o que a evolutiva mexe:

| Mexe em | Práticas |
|---|---|
| coluna lida por step de Update | BP-PLG-01 |
| lógica nova | BP-DAT-01 (fórmula ou calculada antes de código), BP-DAT-02 (assíncrono), BP-PLG-02 |
| flow | BP-FLW-01, BP-FLW-02, BP-FLW-05 |
| JS | BP-JS-01 a BP-JS-12 |
| formulário | BP-FRM-01, BP-FRM-02, BP-JS-08 |
| configuração por ambiente | BP-ALM-05, BP-ALM-06 |
| deploy | BP-ALM-01 a BP-ALM-04 |
| acesso | BP-SEG-01 |

## Formato da resposta

O padrão de `padrao-prompts.md`, com:

- **Resposta:** impacto baixo, médio ou alto, e por quê.
  - Alto: afeta diretamente algo síncrono (step síncrono, workflow em tempo real, JS de `OnLoad`/`OnSave`), tem mais
    de 10 dependentes diretos, toca integração externa ou remove coluna com dado.
  - Médio: há dependentes diretos, nenhum síncrono.
  - Baixo: nenhum dependente além de formulário e view.
- **Evidências:** uma linha por componente afetado, com as colunas extras `Mudança` (qual item da evolutiva) e
  `Impacto` (direto ou indireto).
- **Recomendações:** ajustes, um teste de regressão por componente afetado diretamente, ALM (soluções e ordem de
  deploy) e práticas, com a coluna extra `Tipo` (ajuste, teste, ALM, prática).
- **Não verificado:** o que o inventário não enxerga — integração externa, código fora do repositório, relatórios,
  Power BI, canvas apps sem dependência registrada.

## Limites

- Somente leitura: não altere o ambiente, não rode `extract`, `render` ou `all` e não use ferramentas que escrevem no
  Dataverse.
- Não exiba `.env`, tokens ou segredos: cite só o ID do achado (SEC-01, REPO-01).
- Ausência de dependência no vault não prova ausência de uso.
- O parecer é insumo; a decisão é do time.
