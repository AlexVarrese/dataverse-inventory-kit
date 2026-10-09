# Achados automáticos

Fonte: `scripts/dvinv/findings.py`, recalculados a cada `render` a partir do snapshot `_raw/<run_id>/`
e gravados em `_derived/<run_id>/findings.json` (fora do snapshot). Todo achado traz a métrica e a evidência. **Achados são candidatos**: valide
antes de afirmar.

| ID | Sev. | Regra | Como validar / falsos positivos |
|---|---|---|---|
| SEC-01 | crítico | padrão de segredo (`sig=`, `code=`, AccountKey, SharedAccessKey, Bearer/JWT, `password=`/`senha=`/`api_key=` literal) em JS/HTML, clientdata de flow, unsecure config de step, variável de ambiente ou URL de service endpoint | abrir a linha indicada; strings de exemplo/placeholder são falso positivo. Se for real: rotacionar na origem e mover para variável de ambiente *Secret* (Key Vault) ou secure config |
| UI-01 | alto | formulário ou botão de ribbon referencia web resource que não existe no ambiente | conferir o nome na solução de origem; pode ser recurso de outra região/variante nunca publicado |
| UI-02 | médio | handler de evento aponta para função não encontrada por análise estática da biblioteca | código minificado, funções geradas dinamicamente ou namespaces montados em runtime geram falso positivo |
| UI-03 | baixo | web resource JS do escopo sem referência em formulário (nem em ribbon, se coletado) | pode ser usado por outra biblioteca, HTML, PCF, command bar moderna ou chamado de outro JS |
| PLG-01 | médio | step síncrono de Update ativo sem filtering attributes | conferir no código quais colunas o plugin lê; às vezes é intencional (auditoria genérica) |
| PLG-02 | médio | step síncrono em Retrieve/RetrieveMultiple | medir com o trace; pode ser segurança por registro proposital |
| PLG-03 | baixo | step desativado | confirmar com o time se é desligamento temporário |
| PLG-04 | alto/crítico | classe com ≥ 5 execuções e taxa de erro ≥ 5% no trace (crítico se ≥ 50%) | ler `exceptiondetails` no ambiente; janela curta = amostra pequena |
| OPS-01 | info | plugin trace desligado | — |
| OPS-02 | médio | auditoria da organização desligada | — |
| PRC-01 | médio | mais de um BPF ativo na mesma tabela | podem ser por perfil/BU (intencional) |
| PRC-02 | alto | automação ativa com proprietário desativado | flows param quando a conexão do dono expira; transferir para conta de serviço/equipe |
| PRC-03 | baixo | workflow clássico síncrono (tempo real) | — |
| PRC-04 | info | workflows/dialogs clássicos ativos (legado) | candidatos a migração |
| PRC-05 | baixo | cloud flow desligado | obsoleto ou desligado por falha? |
| SEG-01 | alto | tabela custom sem privilégio de Read em nenhum dos papéis analisados (`deep.role_privileges`) | só cobre papéis do escopo; o acesso pode vir de papel gerenciado/ISV fora da análise |
| SEG-02 | médio | equipe com 1.000+ membros (`deep.team_members`) | equipes grandes com papel atribuído degradam o motor de autorização |
| ALM-01 | médio | variável de ambiente sem valor e sem default | — |
| ALM-02 | médio | referência de conexão sem conexão vinculada | — |
| ALM-03 | baixo | Custom API sem plugin de implementação | pode ser só uma mensagem para steps/flows |
| ALM-04 | baixo | processo não gerenciado fora de qualquer solução | customização direta no ambiente, não migra por ALM |

| SEG-03 | baixo | equipe proprietária (não padrão da BU) sem nenhum papel | pode ser equipe usada só para compartilhamento — confirmar |
| SEG-04 | alto | usuário de aplicação ativo com System Administrator (direto ou via equipe) | exige `deep.users`; aplicar menor privilégio |
| SEG-05 | baixo | papel do escopo sem usuário ativo nem equipe | exige `deep.users`; pode ser atribuído por processo externo |
| PCF-01 | info | componente PCF declara domínios externos (`external-service-usage`) | confirmar finalidade, dono do serviço e licenciamento premium |
| PCF-02 | baixo | PCF não gerenciado sem uso em formulário ativo | pode ser controle padrão de coluna/tabela ou de view (não visível pela Web API) |
| FLD-01 | médio | coluna custom com zero registros (contagem **completa**) e nenhuma referência (form ativo ou inativo, view, processo, flow, plugin, JS, repo), com **todas** essas fontes medidas — candidata a **investigação** de remoção | nunca afirma que remover é seguro: confirmar integrações externas (ETL, Power BI, portais, APIs), flows fora de solução e código não versionado |
| FLD-02 | baixo | coluna sem dados que ainda aparece em formulário/view, ou só em formulário inativo (uso fraco) | pode ser campo recém-criado (verifique a data de criação); limpe o formulário inativo antes |
| FLD-03 | baixo | coluna sem dados (contagem completa) citada em automação/código | regra morta ou gravação que nunca acontece (bug) |
| FLD-04 | info | coluna com uso **inconclusivo**: zero na amostra parcial, medição que falhou, ou fonte da matriz de uso ausente/falha/não medida | resolver a lacuna (motivos na evidência) antes de qualquer conclusão |
| JS-01 | alto | JS usa `XRMServices/2011` ou `OrganizationData.svc` (removidos) | confirmar se o trecho é alcançável |
| JS-02 | alto | JS usa `eval()` | — |
| JS-03 | médio | JS usa `Xrm.Page` (obsoleto) | bibliotecas de terceiros são excluídas pelo nome |
| JS-04 | médio | XMLHttpRequest síncrono | — |
| JS-05 | baixo | dependência de XrmServiceToolkit | — |
| JS-06 | info | hosts externos fixos no JS | — |
| FLW-01 | baixo | cloud flow ativo com 5+ ações e nenhum tratamento de erro | pode haver monitoramento externo (alerta do admin center, CoE) |
| STO-01 | médio | imagens ≥ 30% dos bytes de anexos de e-mail | padrão de assinatura de e-mail; confirmar tamanho médio por arquivo |
| AUD-01 | info | volume e retenção efetiva da auditoria | comparar com a política acordada; queda brusca entre extrações = exclusão em massa (use `diff`) |
| REPO-01 | crítico | segredo em arquivo versionado | valor fica no histórico do Git; rotacionar e limpar o histórico |
| REPO-02 | alto | web resource publicado diferente do repo | funções só de um lado = drift funcional; só formatação → similaridade alta e nenhuma função exclusiva |
| REPO-03 | alto | web resource/classe publicada sem fonte no repo | pode estar noutro repositório não informado em `repos:` |
| REPO-04 | alto | métodos só em PRD ou só no repo (DLL decompilada) | ler os dois arquivos; compiladores geram métodos auxiliares (`<>c`), ignorados pela heurística |

| DEP-01 | alto | solução exige componentes que não estão nela (RetrieveMissingDependencies, `deep.platform_dependencies`) | o componente pode vir de outra solução instalada antes — documentar a ordem de instalação |
| DEP-02 | info | tabela com 20+ componentes dependentes | indicador de impacto de mudança, não problema |
| DEP-03 | baixo | variável de ambiente, referência de conexão ou Custom API sem uso encontrado no escopo | uso por integração externa, app fora do Dataverse ou flow fora de solução não é visível |

## Ciclo de vida na nota do achado

`status: aberto` → `confirmado` | `falso-positivo` | `resolvido`, com `responsavel` e anotações
abaixo do marcador manual. Esses campos sobrevivem a novas extrações. Quando a condição desaparece
do ambiente, o achado deixa de ser gerado, mas a nota antiga continua no vault: registre o
fechamento nela.

## Achados que só o analista encontra

As regras cobrem o que é detectável por metadata e comparação estática. Ficam para a análise
humana/agente, com o vault como mapa: diferenças de **corpo** de método entre DLL decompilada e repo
(o pacote só aponta métodos que existem de um lado só); contrato
de integração (payload real × modelo do código); tipos de coluna assumidos pelo código × schema real
(Money/Decimal/Double/Integer/Boolean); retenção e volume de auditoria/anexos; lógica de negócio
espalhada entre JS, business rule, plugin e flow para o mesmo campo.
