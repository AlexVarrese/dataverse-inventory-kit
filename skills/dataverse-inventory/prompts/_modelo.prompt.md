---
name: modelo
description: Modelo dos prompts do dataverse-inventory-kit — copie com outro nome e preencha os <…>
argument-hint: <o que informar, ex. tabela.coluna, nome do componente ou mensagem de erro>
agent: agent
---

# <Título do prompt>

## Objetivo

<Uma frase: a pergunta que este prompt responde e para quem (desenvolvedor, sustentação, arquiteto, PO).>

## Entrada

- Alvo: ${input:alvo:<exemplo>}
- Ambiente: ${input:ambiente:pasta do ambiente no vault, ex. Dataverse/HML}

Se o alvo faltar, ou houver mais de um ambiente no `AGENTS.md` e nenhum for informado, pergunte antes de seguir.

## Roteiro no vault

1. Leia o `AGENTS.md` e o `AGENT-INDEX.md` do ambiente. Anote a data do snapshot.
2. Localize o alvo: `grep -i "<alvo>" Componentes.csv` → chave e nota.
3. <Passos específicos deste prompt: Dependências.csv, notas, Achados/, Mudanças.csv.>

Não leia inteiros os CSVs nem a Matriz de Dependências: use grep ou os comandos `onde-usa`, `impacto`, `depende` e `buscar` indicados no `AGENTS.md`.

## Checklist de boas práticas

Para cada componente envolvido, confira em [boas-praticas.md](boas-praticas.md) as práticas `<BP-… da área>` e os
achados abertos ligados a ele (`Achados/`, property `status`). Tema fora do catálogo: consulte o Microsoft Learn
(Learn MCP Server ou a página, se habilitados) e cite a URL; sem acesso, escreva "não verificado no Learn".

## Formato da resposta

```markdown
**Resposta:** <1 a 3 frases> · Confiança: alta | média | baixa
**Base:** <ambiente>, snapshot <data>

### Evidências
| Componente (chave) | Nota | O que mostra | Origem (vault, Learn, inferência) |
|---|---|---|---|

### Recomendações
| # | Recomendação | Prática (BP-… + link) | Esforço (P, M, G) | Risco se não fizer |
|---|---|---|---|---|

### Não verificado
- <lacuna> → <quem valida e como>
```

## Limites

- Somente leitura: não altere o ambiente, não rode `extract`, `render` ou `all` e não use ferramentas que escrevem no
  Dataverse.
- Não exiba `.env`, tokens ou segredos: cite só o ID do achado (SEC-01, REPO-01).
- Ausência de dependência no vault não prova ausência de uso (integração externa, código fora do repositório).
- A resposta é insumo; a decisão é do time.
