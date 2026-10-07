"""Arquivos .base (Obsidian Bases) — visões tipo banco de dados sobre as notas geradas.

Filtro por pasta + property `tipo`, para que vários ambientes convivam no mesmo vault.
"""

import yaml


def _base(v, tipo, formulas=None, properties=None, views=None):
    data = {"filters": {"and": [f'file.inFolder("{v.folder}")', f'tipo == "{tipo}"']}}
    if formulas:
        data["formulas"] = formulas
    if properties:
        data["properties"] = {k: {"displayName": n} for k, n in properties.items()}
    data["views"] = views
    return data


def write_all(v, findings):
    files = {
        "Tabelas": _base(v, "tabela",
            properties={"nome_exibicao": "Nome", "customizada": "Custom", "registros": "Registros",
                        "colunas_custom": "Colunas custom", "plugin_steps": "Steps", "processos": "Processos",
                        "flows_que_tocam": "Flows", "achados": "Achados", "dependentes": "Dependentes",
                        "dependencias": "Depende de"},
            formulas={"automacao": "plugin_steps + processos + flows_que_tocam"},
            views=[
                {"type": "table", "name": "Todas", "order": ["file.name", "nome_exibicao", "customizada", "registros",
                 "colunas_custom", "plugin_steps", "processos", "flows_que_tocam", "formula.automacao", "dependentes",
                 "achados"],
                 "summaries": {"registros": "Sum", "colunas_custom": "Sum"}},
                {"type": "table", "name": "Customizadas", "filters": "customizada == true",
                 "order": ["file.name", "nome_exibicao", "registros", "colunas_custom", "formula.automacao"]},
                {"type": "table", "name": "Mais automatizadas", "filters": "formula.automacao > 0",
                 "order": ["file.name", "plugin_steps", "processos", "flows_que_tocam", "formula.automacao"]},
                {"type": "table", "name": "Com achados", "filters": "achados > 0", "order": ["file.name", "achados"]},
                {"type": "table", "name": "Alto impacto (5+ dependentes)", "filters": "dependentes >= 5",
                 "order": ["file.name", "nome_exibicao", "dependentes", "dependencias", "registros"]},
            ]),
        "Automações": _base(v, "processo",
            properties={"categoria": "Categoria", "tabela": "Tabela", "ativo": "Ativo", "proprietario": "Proprietário",
                        "proprietario_desativado": "Dono desativado", "gatilho": "Gatilho", "modificado": "Modificado"},
            formulas={"dias_sem_alteracao": 'if(modificado, (today() - date(modificado)).days, "")'},
            views=[
                {"type": "table", "name": "Por categoria", "groupBy": {"property": "categoria", "direction": "ASC"},
                 "order": ["file.name", "tabela", "ativo", "gatilho", "proprietario", "modificado"]},
                {"type": "table", "name": "Cloud flows", "filters": 'categoria == "Cloud Flow"',
                 "order": ["file.name", "ativo", "gatilho", "conectores", "hosts_http", "proprietario", "modificado"]},
                {"type": "table", "name": "Dono desativado", "filters": "proprietario_desativado == true",
                 "order": ["file.name", "categoria", "proprietario", "ativo"]},
                {"type": "table", "name": "Parados há mais tempo", "filters": "ativo == true",
                 "order": ["file.name", "categoria", "modificado", "formula.dias_sem_alteracao"]},
            ]),
        "Plugin Steps": _base(v, "plugin-step",
            properties={"mensagem": "Mensagem", "tabela": "Tabela", "estagio": "Estágio", "modo": "Modo",
                        "ordem": "Ordem", "ativo": "Ativo", "filtering": "Filtering"},
            views=[
                {"type": "table", "name": "Por tabela", "groupBy": {"property": "tabela", "direction": "ASC"},
                 "order": ["file.name", "mensagem", "estagio", "modo", "ordem", "ativo", "filtering", "classe"]},
                {"type": "table", "name": "Síncronos", "filters": 'modo == "Síncrono"',
                 "order": ["file.name", "tabela", "mensagem", "estagio", "filtering"]},
                {"type": "table", "name": "Por assembly", "groupBy": {"property": "assembly", "direction": "ASC"},
                 "order": ["file.name", "classe", "tabela", "mensagem"]},
            ]),
        "Web Resources": _base(v, "webresource",
            views=[
                {"type": "table", "name": "Todos", "groupBy": {"property": "tipo_arquivo", "direction": "ASC"},
                 "order": ["file.name", "funcoes", "formularios", "tamanho_bytes", "segredos", "modificado"]},
                {"type": "table", "name": "JS sem uso em formulário", "filters": {"and": ['tipo_arquivo == "JScript"', "formularios == 0"]},
                 "order": ["file.name", "funcoes", "modificado"]},
            ]),
        "Achados": _base(v, "achado",
            properties={"id_achado": "ID", "severidade": "Severidade", "titulo": "Título", "metrica": "Qtd",
                        "status": "Status", "responsavel": "Responsável"},
            views=[
                {"type": "table", "name": "Por severidade", "groupBy": {"property": "severidade", "direction": "ASC"},
                 "order": ["id_achado", "titulo", "metrica", "status", "responsavel"]},
                {"type": "table", "name": "Abertos", "filters": 'status == "aberto"',
                 "order": ["id_achado", "severidade", "titulo", "responsavel"]},
            ]),
        "Componentes": {
            "filters": {"and": [f'file.inFolder("{v.folder}")', {"or": [
                'tipo == "plugin-assembly"', 'tipo == "customapi"', 'tipo == "app"', 'tipo == "solucao"', 'tipo == "papel"']}]},
            "views": [{"type": "table", "name": "Por tipo", "groupBy": {"property": "tipo", "direction": "ASC"},
                       "order": ["file.name", "subtipo", "versao", "gerenciado", "gerenciada", "dependentes",
                                 "dependencias", "modificado"]}],
        },
    }
    for name, data in files.items():
        v.write(f"{v.folder}/Bases/{name}.base", None,
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000), raw=True)
