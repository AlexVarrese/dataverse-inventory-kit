# Padrão de prompt e de resposta

Todo prompt do kit (impacto, diagnóstico, performance, arquitetura, evolução) segue a mesma estrutura e devolve
a resposta no mesmo formato, para o time comparar respostas e revisar sempre do mesmo jeito.
Modelo pronto para copiar: `prompts/_modelo.prompt.md` (o `render` não o copia para o vault).

## Onde ficam

| Onde | O quê |
|---|---|
| `prompts/*.prompt.md` (kit) | fonte dos prompts padrão |
| `.github/prompts/*.prompt.md` (repositório do vault) | cópia gravada pelo `render` no modo repositório; o Copilot oferece cada um como `/nome` no chat |
| `.github/prompts/boas-praticas.md` | cópia do catálogo, para os prompts citarem por link relativo |
| `AGENTS.md` (bloco dvinv) | aponta para os prompts; Devin e demais agentes leem esse arquivo automaticamente |

O time pode criar os próprios prompts na mesma pasta com outro nome; o `render` só reescreve os do kit.

## Estrutura do prompt

| Seção | Conteúdo | Regra |
|---|---|---|
| Frontmatter | `name`, `description`, `argument-hint`, `agent: agent` | `name` = comando no chat (`/impacto-evolutiva`) |
| Objetivo | a pergunta que o prompt responde e para quem | uma frase |
| Entrada | alvo e ambiente via `${input:…}` | faltou dado → o agente pergunta antes de seguir |
| Roteiro no vault | passos numerados: `AGENT-INDEX.md` → CSVs → notas → achados | nunca ler CSVs ou a Matriz de Dependências inteiros |
| Checklist de práticas | IDs `BP-…` da área + achados abertos do componente | tema fora do catálogo → Learn, com URL |
| Formato da resposta | o padrão abaixo | sem seções extras |
| Limites | somente leitura, sem segredos, decisão humana | iguais em todos os prompts |

`${input:nome:exemplo}` só é interpretado pelo Copilot. Em outro agente o usuário escreve a entrada na mensagem,
por exemplo: "siga `.github/prompts/impacto-evolutiva.prompt.md` para `account.contoso_limite`".

## Padrão de resposta

```markdown
**Resposta:** <1 a 3 frases com a conclusão> · Confiança: alta | média | baixa
**Base:** <ambiente>, snapshot <data do AGENT-INDEX>

### Evidências
| Componente (chave) | Nota | O que mostra | Origem |
|---|---|---|---|

### Recomendações
| # | Recomendação | Prática | Esforço | Risco se não fizer |
|---|---|---|---|---|

### Não verificado
- <o que o vault não cobre> → <quem valida e como>
```

- **Origem** de cada afirmação: `vault` (lido numa nota ou CSV), `Learn` (página citada), `inferência` (dedução do
  agente). Inferência nunca sustenta sozinha uma recomendação de prioridade alta.
- **Prática** = `BP-…` com o link do Learn; recomendação sem prática é dita "genérica".
- **Esforço**: P (horas), M (dias), G (sprint ou mais). **Confiança** cai para média se houver item em *Não verificado*
  que mude a conclusão, e para baixa se o alvo não foi encontrado no vault.
- Componente sempre pela chave e com link para a nota; nada de nome solto.

## Busca externa (Microsoft Learn)

Ordem: (1) `boas-praticas.md`; (2) se o tema não estiver lá, o
[Microsoft Learn MCP Server](https://learn.microsoft.com/training/support/mcp) (`https://learn.microsoft.com/api/mcp`,
público, sem autenticação) ou a leitura direta da página em learn.microsoft.com; (3) sem acesso, escrever
"não verificado no Learn". Só vale como `Learn` a página aberta e citada; resultado de busca não é fonte.
Use o MCP só se a governança tiver aprovado a ferramenta no ambiente do time.

## Aprovação do CoE

O modelo está aprovado quando:

- [ ] a estrutura e o formato de resposta foram revisados pelo CoE;
- [ ] um caso real respondido no padrão foi conferido por um owner da Avenida;
- [ ] os limites (somente leitura, segredos, decisão humana) foram aceitos pela governança;
- [ ] o uso do Learn MCP Server foi aprovado ou descartado.
