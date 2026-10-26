"""Gera o vault Obsidian a partir de um snapshot `_raw/<run_id>/` (convenções kepano/obsidian-skills).

- Lê só os arquivos listados (e conferidos) no manifesto do snapshot; coletor sem arquivo = lacuna.
- Renderiza numa cópia de trabalho do vault, varre segredos (fail-closed) e só então troca a pasta
  publicada; saídas derivadas (findings.json, dependencies.json) vão para `_derived/<run_id>/`.

- Uma nota por componente, com properties tipadas (frontmatter) para alimentar Obsidian Bases.
- Wikilinks com caminho a partir da raiz do vault (`[[Pasta/Nota|alias]]`) — sem ambiguidade entre
  ambientes no mesmo vault.
- Tudo abaixo do marcador MANUAL é do analista e é preservado em re-execuções; idem para as
  properties em PRESERVE_PROPS (ex.: status/responsável de um achado).
"""

import os
import re
import shutil
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import yaml

from .. import dependencies as deps_mod
from .. import changes as changes_mod
from .. import findings as findings_mod
from .. import secrets
from .. import snapshot as snapshot_mod
from ..config import SYSTEM_SOLUTIONS
from .. import residuos, resumo
from . import agents, bases, campos, canvas, catalog, maps, navigation, structure

MANUAL = "%% dvinv:manual — o conteúdo abaixo desta linha é preservado nas próximas extrações %%"
PRESERVE_PROPS = {"status", "responsavel", "decisao", "prazo", "revisado", "tags_extra"}
BAD_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]]+')

CODE_IN_NOTE_MAX = 150_000  # código acima disso (ou de biblioteca de terceiros) fica só no snapshot
LIST_MAX = 300              # listas longas nas notas de seção: o resto vai para CSV/índice
SEV_CALLOUT = {"crítico": "danger", "alto": "warning", "médio": "caution", "baixo": "note", "info": "info"}
CATEGORY_FOLDER = {
    "Workflow": "Workflows", "Dialog": "Dialogs", "Business Rule": "Business Rules", "Action": "Actions",
    "Business Process Flow": "BPFs", "Cloud Flow": "Cloud Flows", "Desktop Flow": "Desktop Flows",
}


def safe(name, maxlen=110):
    s = BAD_CHARS.sub("-", str(name or "sem-nome")).strip().strip(".")
    return (s[:maxlen].rstrip() or "sem-nome")


def cell(v):
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "✅" if v else "❌"
    return str(v).replace("|", "\\|").replace("\n", " ")


def mdtable(headers, rows):
    if not rows:
        return "_nenhum_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def split_frontmatter(text):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            try:
                return yaml.safe_load(text[4:end]) or {}, text[end + 5:]
            except yaml.YAMLError:
                pass
    return {}, text


class Vault:
    def __init__(self, vault_root, folder, env_name):
        self.root = Path(vault_root)
        self.folder = folder.strip("/")
        self.env = env_name
        self.paths = {}        # (kind, key) -> caminho relativo à raiz do vault, sem .md
        self.taken = set()
        self.written = []
        self.dep_counts = {}   # caminho da nota -> {"dependencias": n, "dependentes": n}
        self.guid_of = {}      # (kind, key) -> GUID do Dataverse (frontmatter `id`)
        self.today = date.today()

    # ---------- caminhos e links ----------
    def reserve(self, kind, key, subfolder, title):
        base = f"{self.folder}/{subfolder}/{safe(title)}" if subfolder else f"{self.folder}/{safe(title)}"
        rel, n = base, 2
        while rel.lower() in self.taken:
            rel = f"{base} ({n})"
            n += 1
        self.taken.add(rel.lower())
        self.paths[(kind, key)] = rel
        self.node_of = getattr(self, "node_of", {})
        self.node_of[rel] = (kind, key)
        return rel

    def link(self, kind, key, alias=None, in_table=False):
        rel = self.paths.get((kind, key))
        if not rel:
            return alias or (str(key) if key else "—")
        alias = alias or rel.rsplit("/", 1)[-1]
        return f"[[{rel}|{alias}]]"  # dentro de tabela, cell() escapa o pipe

    def link_path(self, rel, alias, in_table=False):
        return f"[[{self.folder}/{rel}|{alias}]]"

    # ---------- escrita ----------
    def write(self, rel, props, body, raw=False):
        path = self.root / (rel if raw else rel + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        body = body.replace("\r\n", "\n").replace("\r", "\n")  # LF sempre: código JS do Dataverse vem com CRLF
        if raw:
            path.write_text(body, encoding="utf-8", newline="\n")  # LF também no Windows
            self.written.append(path)
            return
        manual = f"{MANUAL}\n\n## Notas do analista\n\n"
        if path.exists():
            old_props, old_body = split_frontmatter(path.read_text(encoding="utf-8"))
            for k in PRESERVE_PROPS:
                if k in old_props:
                    props[k] = old_props[k]
            if MANUAL in old_body:
                manual = old_body[old_body.index(MANUAL):]
        # sem data de extração por nota: re-extrair sem mudança no ambiente não pode mudar 2 mil arquivos
        # (a data fica no 00 Índice, no 01 Ambiente e no manifesto do snapshot)
        # `chave` (a mesma dos CSVs e do `dvinv onde-usa`) e `id` (GUID do Dataverse): permitem achar a nota
        # a partir de um log, chamado ou trace com grep, sem ler o vault
        ident = {}
        node = getattr(self, "node_of", {}).get(rel)
        if node:
            ident["chave"] = f"{node[0]}:{node[1]}"
            if self.guid_of.get(node):
                ident["id"] = self.guid_of[node]
        props = {"ambiente": self.env, **ident, **props, **self.dep_counts.get(rel, {})}
        fm = yaml.safe_dump(props, allow_unicode=True, sort_keys=False, width=1000).strip()
        path.write_text(f"---\n{fm}\n---\n{body.rstrip()}\n\n{manual.rstrip()}\n", encoding="utf-8", newline="\n")
        self.written.append(path)

    def tags(self, kind):
        return ["dataverse", f"dataverse/{kind}", f"ambiente/{safe(self.env).lower().replace(' ', '-')}"]


def load_raw(path, root=None):
    """Carrega um snapshot (diretório, raiz de snapshots → último íntegro, ou formato antigo)."""
    snap = snapshot_mod.resolve(path, root)
    return snapshot_mod.load(snap)


def render(cfg, snapshot=None):
    """Renderiza o snapshot indicado (ou o último íntegro de cfg.raw_dir). Publica vault e derivados
    só se a varredura de segredos passar; caso contrário levanta SecretLeakError e nada muda."""
    started = time.monotonic()
    snap = snapshot_mod.resolve(snapshot or cfg.raw_dir, cfg.raw_dir)
    print(f"[render] Carregando snapshot {snap.name}...", flush=True)
    d = snapshot_mod.load(snap)
    print(f"[render] Snapshot validado/carregado em {time.monotonic() - started:.1f}s", flush=True)
    run_id = snapshot_mod.run_id_of(snap, d["manifest"])
    stage_root = Path(cfg.vault_root) / f".dvinv-render-{run_id}-{os.getpid()}"
    shutil.rmtree(stage_root, ignore_errors=True)
    stage_dir = stage_root / cfg.vault_folder
    final_dir = Path(cfg.vault_dir)
    derived = snapshot_mod.DerivedStaging(cfg.derived_dir, run_id)
    try:
        if final_dir.exists():
            copy_start = time.monotonic()
            print("[render] Copiando vault anterior para staging...", flush=True)
            shutil.copytree(final_dir, stage_dir)  # preserva edições do analista e notas antigas
            print(f"[render] Cópia concluída em {time.monotonic() - copy_start:.1f}s", flush=True)
        v = Vault(stage_root, cfg.vault_folder, cfg.name)
        v.snapshot, v.run_id = snap, run_id
        if getattr(cfg, "config_path", None):
            v.config_rel = os.path.relpath(cfg.config_path, Path(cfg.vault_root).resolve()).replace(os.sep, "/")
        print("[render] Calculando grafo, achados e notas...", flush=True)
        fnd, deps_json = _render_into(v, d)
        print(f"[render] Notas geradas: {len(v.written)} em {time.monotonic() - started:.1f}s", flush=True)
        print("[render] Gravando derivados...", flush=True)
        derived.write_json("findings", fnd)
        derived.write_json("dependencies", deps_json)
        # nota com trecho que só casa padrão de segredo na forma final (ex. entidades HTML decodificadas): limpa no
        # lugar; se não der, a nota vira um aviso e o arquivo de dados sai — o vault é publicado mesmo assim
        print(f"[render] Derivados gravados em {time.monotonic() - started:.1f}s; verificando segredos...", flush=True)
        unverified = list(v.written) + [p for p in derived.paths if p not in derived.verified]
        fixed, failed = secrets.sanitize_paths(unverified)
        for p in failed:
            if p.suffix == ".md":
                p.write_text("---\ntipo: aviso\n---\n\n> [!danger] Conteúdo removido na publicação: possível segredo "
                             "não redigido. Consulte o componente no ambiente.\n", encoding="utf-8", newline="\n")
            else:
                p.unlink(missing_ok=True)
        if fixed or failed:
            print(f"[dvinv] varredura do vault: {len(fixed)} arquivo(s) redigido(s) no lugar, {len(failed)} "
                  "substituído(s)/removido(s) (possível segredo)", flush=True)
        v.written = [p for p in v.written if Path(p).exists()]
        derived.paths = [p for p in derived.paths if Path(p).exists()]
        secrets.assert_clean(list(v.written) + [p for p in derived.paths if p not in derived.verified], f"render {run_id}")
        print(f"[render] Segurança verificada em {time.monotonic() - started:.1f}s; publicando vault...", flush=True)
        # publicação: troca a pasta do vault inteira (rename) e os derivados do run
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        old = None
        if final_dir.exists():
            old = final_dir.parent / f".{final_dir.name}.dvinv-old-{os.getpid()}"
            _rename_retry(final_dir, old)
        try:
            _rename_retry(stage_dir, final_dir)
        except OSError:
            if old:  # devolve o vault anterior
                _rename_retry(old, final_dir)
            raise
        if old:
            shutil.rmtree(old, ignore_errors=True)
        v.derived_dir = derived.publish()
        # AGENTS.md (bloco) + CLAUDE.md na raiz do vault; em modo repositório, README/.gitignore/.gitattributes
        v.agent_warnings = agents.update_root(cfg.vault_root, repo=getattr(cfg, "repo", False))
        v.graph_created = maps.graph_settings(Path(cfg.vault_root))  # cores por tipo no Graph view (se não existir)
    except BaseException:
        derived.discard()
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
    v.written = [final_dir / Path(w).relative_to(stage_dir) if str(w).startswith(str(stage_dir)) else w
                 for w in v.written]
    v.root = Path(cfg.vault_root)
    # vault publicado em Git: o GitHub alerta acima de 50 MB por arquivo e recusa acima de 100 MB
    files = [p for p in final_dir.rglob("*") if p.is_file()]
    v.size_warnings = [f"{p.relative_to(final_dir).as_posix()}: {p.stat().st_size / 1e6:.0f} MB"
                       for p in files if p.stat().st_size > GIT_FILE_WARN]
    # Windows: limite de 260 caracteres por caminho. O caminho relativo à raiz do vault + o prefixo de um clone
    # típico (C:\Users\<usuário>\source\repos\<repositório>\ ≈ 60) precisa caber.
    longest = max(files, key=lambda p: len(p.relative_to(cfg.vault_root).as_posix()), default=None)
    if longest is not None and len(longest.relative_to(cfg.vault_root).as_posix()) > WIN_REL_PATH_WARN:
        v.size_warnings.append(
            f"caminho longo para Windows ({len(longest.relative_to(cfg.vault_root).as_posix())} caracteres na raiz do "
            f"vault: {longest.relative_to(cfg.vault_root).as_posix()[:80]}…) — no Windows, use pasta curta (ex. C:\\kb) "
            "e `git config core.longpaths true`, ou ative LongPathsEnabled")
    return v


def _render_into(v, d):
    if not d.get("manifest"):
        raise SystemExit("Snapshot sem manifest.json — rode 'extract' antes.")
    phase_start = time.monotonic()
    fnd = findings_mod.compute(d)
    print(f"[render] Achados: {len(fnd)} ({time.monotonic() - phase_start:.1f}s)", flush=True)
    phase_start = time.monotonic()
    # notas de versão: compara com o snapshot anterior do mesmo ambiente (antes de escrever qualquer nota)
    chg = changes_mod.compare(v.snapshot, d, fnd) if getattr(v, "snapshot", None) else None

    print(f"[render] Comparação de snapshots: {time.monotonic() - phase_start:.1f}s", flush=True)
    tables = d.get("tables") or []
    plugins = d.get("plugins") or {"assemblies": [], "steps": []}
    procs = d.get("processes") or []
    wrs = d.get("webresources") or []
    forms = d.get("forms") or []
    views = d.get("views") or []
    rels = d.get("relationships") or []
    apis = d.get("customapis") or []
    apps = d.get("apps") or {}
    sols = d.get("solutions") or []
    sec = d.get("security") or {}
    alm = d.get("alm") or {}
    ribbons = d.get("ribbons") or []

    # ---- 1) reservar caminhos (para que todo link resolva, independente da ordem de escrita) ----
    for f in fnd:
        v.reserve("finding", f["id"], "Achados", f"{f['id']} {f['title']}"[:90])
    for t in tables:
        v.reserve("table", t["logical"], "Tabelas", t["logical"])
    for a in plugins["assemblies"]:
        v.reserve("assembly", a["name"], "Plugins", a["name"])
    for s in plugins["steps"]:
        v.reserve("step", s["id"], "Plugin Steps", s["name"] or s["id"])
    for p in procs:
        v.reserve("process", p["id"], f"Processos/{CATEGORY_FOLDER.get(p['category'], p['category'])}", p["name"])
    for w in wrs:
        v.reserve("webresource", w["name"], "Web Resources", w["name"].replace("/", "-"))
    for a in apis:
        v.reserve("customapi", a["unique"], "Custom APIs", a["unique"])
    for kind in ("modeldriven", "canvas", "bots"):
        for a in apps.get(kind) or []:
            v.reserve("app", a["id"], "Apps", a["name"])
    for s in sols:
        if s["uniquename"].lower() in SYSTEM_SOLUTIONS:
            continue
        if s["managed"] is False or s.get("in_scope"):
            v.reserve("solution", s["uniquename"], "Soluções", s["uniquename"])
    for r in sec.get("roles") or []:
        if r.get("scope_reason"):
            v.reserve("role", r["name"], "Papéis", r["name"])
    structure.reserve(v, d)  # BUs, equipes, usuários (extra usuarios) e componentes PCF
    navigation.reserve(v, d)  # site maps e SLAs
    for kind, section in (("envvar", "Variáveis de ambiente"), ("connref", "Referências de conexão"),
                          ("endpoint", "Service endpoints")):
        src = (alm.get("envvars") if kind == "envvar" else alm.get("connrefs") if kind == "connref"
               else d.get("serviceendpoints")) or []
        for x in src:
            v.paths[(kind, x["name"])] = f"{v.folder}/03 Integrações#{section}"

    def sol_links(names):
        return [v.link("solution", n, n) for n in names or [] if ("solution", n) in v.paths]

    finding_refs = defaultdict(list)
    for f in fnd:
        for kind, key in f["refs"]:
            finding_refs[(kind, key)].append(f)

    # ---- grafo de dependências (antes das notas: cada nota mostra 'depende de' / 'usado por') ----
    graph_start = time.monotonic()
    print("[render] Construindo grafo (a fase com maior volume de dependências)...", flush=True)
    graph = deps_mod.build(d)
    print(f"[render] Grafo: {len(graph.nodes):,} nós e {len(graph.edges):,} arestas em {time.monotonic() - graph_start:.1f}s", flush=True)
    v.graph = graph
    v.guid_of.update(getattr(graph, "guid_index", {}) or {})
    for s in sols:  # soluções não são nós do grafo, mas a nota leva o GUID
        v.guid_of[("solution", s["uniquename"])] = s["id"].lower()
    for n in list(v.paths):  # nós cuja chave já é o GUID (processos, steps, BUs, equipes, site maps, SLAs…)
        if n not in v.guid_of and re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", str(n[1]).lower()):
            v.guid_of[n] = str(n[1]).lower()
    graph_start = time.monotonic()
    dsum = deps_mod.summarize(graph, d)
    print(f"[render] Matriz de dependências: {time.monotonic() - graph_start:.1f}s", flush=True)
    graph_start = time.monotonic()
    deps_json = deps_mod.to_json(graph, dsum)
    print(f"[render] Exportação do grafo em memória: {time.monotonic() - graph_start:.1f}s", flush=True)
    for n in graph.nodes:
        if n in v.paths:
            v.dep_counts[v.paths[n]] = {"dependencias": dsum["fan_out"][n], "dependentes": dsum["fan_in"][n]}

    def node_link(n, in_table=False):
        node = graph.nodes[n]
        kind, key = n
        if n in v.paths:
            return v.link(kind, key, node["label"])
        if kind in ("form", "view", "column") and ("table", node.get("sub")) in v.paths:
            return f"{node['label']} ({v.link('table', node['sub'], node['sub'])})"
        return f"{node['label']}" + ("" if kind in ("process",) else f" _{deps_mod.KIND_LABEL.get(kind, kind).lower()}_")

    def deps_block(kind, key):
        n = (kind, key)
        if n not in graph.nodes:
            return ""
        out, inn = graph.out_of(n), graph.into(n)
        if not (out or inn):
            return "## Dependências\n\n_Nenhuma dependência encontrada (nem dependentes)._\n"
        lines = ["## Dependências", ""]
        for title, items in ((f"Depende de ({len(out)})", out), (f"Usado por ({len(inn)})", inn)):
            if not items:
                continue
            lines += [f"### {title}", ""]
            by_rel = defaultdict(list)
            for other, rel, e in items:
                by_rel[rel].append((other, e))
            for rel, lst in sorted(by_rel.items()):
                lst = sorted(lst, key=lambda oe: (str(graph.nodes[oe[0]]["label"]).lower(), str(oe[0])))  # estável
                shown = ", ".join(node_link(o) + (" ⚙︎" if "plataforma" in e["source"] else "") for o, e in lst[:150])
                more = f" … +{len(lst) - 150}" if len(lst) > 150 else ""
                lines.append(f"- **{rel}:** {shown}{more}")
            lines.append("")
        if any("plataforma" in e["source"] for _, _, e in out + inn):
            lines.append("⚙︎ = registrada pela plataforma (tabela dependency)\n")
        return "\n".join(lines) + "\n"

    def findings_block(kind, key):
        return deps_block(kind, key) + _findings_only(kind, key)

    def _findings_only(kind, key):
        fs = list({f["id"]: f for f in finding_refs.get((kind, key), [])}.values())
        if not fs:
            return ""
        lines = ["## Achados", ""]
        for f in fs:
            lines.append(f"- {v.link('finding', f['id'], f['id'] + ' — ' + f['title'])} · **{f['severity']}**")
        return "\n".join(lines) + "\n"

    # índices auxiliares
    steps_by_table = defaultdict(list)
    for s in plugins["steps"]:
        steps_by_table[s["entity"]].append(s)
    procs_by_table = defaultdict(list)
    flows_touching = defaultdict(list)
    for p in procs:
        if p.get("entity"):
            procs_by_table[p["entity"]].append(p)
        for t in p.get("tables") or []:
            if p.get("entity") != t:
                flows_touching[t].append(p)
    forms_by_table = defaultdict(list)
    for f in forms:
        forms_by_table[f["entity"]].append(f)
    views_by_table = defaultdict(list)
    for x in views:
        views_by_table[x["entity"]].append(x)
    rels_by_table = defaultdict(list)
    for r in rels:
        rels_by_table[r["from"]].append(r)
        if r["to"] != r["from"]:
            rels_by_table[r["to"]].append(r)
    ribbons_by_table = defaultdict(list)
    for r in ribbons:
        ribbons_by_table[r["entity"]].append(r)
    wr_usage = defaultdict(list)
    for f in forms:
        for lib in f.get("libraries") or []:
            wr_usage[lib.lower()].append(f)
    matrix = sec.get("privilege_matrix") or {}
    fu_by_table = {t["table"]: t for t in d.get("field_usage") or []}
    wr_repo, type_repo = defaultdict(list), defaultdict(list)
    for r in d.get("repos") or []:
        for w in r.get("webresources") or []:
            wr_repo[w["name"]].append((r["name"], w))
        for t in r.get("plugin_types") or []:
            type_repo[t["type"]].append((r["name"], t))
    apis_by_table = defaultdict(list)
    for a in apis:
        if a.get("bound_entity"):
            apis_by_table[a["bound_entity"]].append(a)

    # ---- 2) tabelas ----
    vtd = d.get("virtual_tables") or {}
    virtual_by_table = {x["logical"]: x for x in vtd.get("tables") or []}
    vprov = {x["id"].lower(): x for x in vtd.get("providers") or []}
    last_act = residuos.last_activity(d)
    fmatrix = campos.matrix(d, graph)        # uso de cada campo: quem usa e o que roda quando muda
    _trig, any_change = campos.triggers(d)
    for table_ix, t in enumerate(tables, 1):
        if table_ix == 1 or table_ix % 100 == 0:
            print(f"[render] Notas de tabelas: {table_ix}/{len(tables)} ({len(v.written)} arquivos escritos)", flush=True)
        ln = t["logical"]
        custom_cols = [c for c in t["columns"] if c["custom"]]
        st, pr, fl = steps_by_table.get(ln, []), procs_by_table.get(ln, []), flows_touching.get(ln, [])
        props = {
            "tipo": "tabela", "nome_logico": ln, "nome_exibicao": t.get("display"), "aliases": [t["display"]] if t.get("display") else [],
            "customizada": bool(t["custom"]), "gerenciada": t.get("managed"), "propriedade": t.get("ownership"),
            "registros": t.get("record_count"), "ultima_atividade": last_act.get(ln), "colunas_custom": len(custom_cols), "colunas_total": t.get("column_count"),
            "relacionamentos": len(rels_by_table.get(ln, [])), "formularios": len(forms_by_table.get(ln, [])),
            "plugin_steps": len(st), "processos": len(pr), "flows_que_tocam": len(fl),
            "achados": len(finding_refs.get(("table", ln), [])), "escopo": t.get("scope_reason"),
            "tipo_tabela": t.get("table_type"),
            "solucoes": sol_links(t.get("solutions")), "tags": v.tags("tabela"),
        }
        b = [f"# {t.get('display') or ln} (`{ln}`)", ""]
        b += ["> [!abstract] Resumo",
              f"> {'Tabela customizada' if t['custom'] else 'Tabela nativa customizada'} · propriedade **{t.get('ownership')}** · "
              f"{'**' + format(t['record_count'], ',').replace(',', '.') + '** registros' if t.get('record_count') is not None else 'registros não medidos'} · "
              f"{'última atividade **' + last_act[ln] + '** · ' if ln in last_act else ''}"
              f"**{len(custom_cols)}** colunas customizadas de {t.get('column_count')} · entity set `{t.get('entityset')}`", ""]
        vt = virtual_by_table.get(ln)
        if vt:
            prov = vprov.get(vt.get("provider_id") or "") or {}
            b += ["## Tabela virtual", "",
                  mdtable(["Item", "Valor"], [
                      ["Provedor de dados", vt.get("provider") or vt.get("provider_id")],
                      ["Fonte de dados", f"{vt.get('datasource') or '—'} (tabela `{vt.get('datasource_table') or '—'}`)"],
                      ["Nome externo", vt.get("external_name")], ["Coleção externa", vt.get("external_collection")]]),
                  mdtable(["Operação", "Classe de plugin", "Assembly"],
                          [[o["operation"], f"`{o.get('typename') or o['plugintype_id']}`",
                            v.link("assembly", o.get("assembly"), o.get("assembly"), in_table=True) if o.get("assembly") else "—"]
                           for o in prov.get("operations") or []]) if prov.get("operations") else ""]
        b += ["## Colunas customizadas", "",
              mdtable(["Coluna", "Nome de exibição", "Tipo", "Obrigatoriedade", "Auditoria", "Descrição"],
                      [[f"`{c['logical']}`", c.get("display"), c.get("type"), c.get("required"), c.get("audit"),
                        (c.get("description") or "")[:120]] for c in custom_cols])]
        b += field_usage_block(v, ln, fmatrix.get(ln) or [], any_change.get(ln) or {})
        if t.get("keys"):
            b += ["## Chaves alternativas", "", mdtable(["Chave", "Colunas"], [[k["name"], ", ".join(k["columns"] or [])] for k in t["keys"]])]
        fcols = [c_ for c_ in t["columns"] if c_.get("formula")]
        if fcols:
            def ref_link(ref):
                tb, _, cn = ref.partition(".")
                return f"`{cn}`" if tb == ln else f"{v.link('table', tb, tb, in_table=True)}.`{cn}`"
            b += ["## Colunas calculadas, rollup e fórmula", "",
                  "Mudar ou remover uma coluna usada aqui quebra o cálculo — confira antes de evoluir.", "",
                  mdtable(["Coluna", "Tipo", "Usa"], [[f"`{c_['logical']}`", c_["formula"]["kind"],
                          ", ".join(ref_link(r) for r in c_["formula"].get("refs") or []) or c_["formula"].get("error") or "—"]
                          for c_ in fcols])]
        maps_ = [m for m in d.get("mappings") or [] if ln in (m["source"], m["target"]) and m["fields"]]
        if maps_:
            b += ["## Mapeamentos entre tabelas", "",
                  "Ao criar um registro a partir de outro (ex. na subgrade do pai), a plataforma copia estes campos.", "",
                  mdtable(["Origem → destino", "Campos (customizados primeiro)", "Do sistema"],
                          [[f"{v.link('table', m['source'], m['source'], in_table=True)} → "
                            f"{v.link('table', m['target'], m['target'], in_table=True)}",
                            ", ".join(f"`{f['source']}`→`{f['target']}`" for f in m["fields"] if not f["system"])[:900] or "—",
                            sum(1 for f in m["fields"] if f["system"])] for m in maps_])]
        layers = (d.get("solution_layers") or {}).get(ln)
        if layers:
            b += ["## Camadas de solução", "",
                  "Da camada de cima (vale em tempo de execução) para a de baixo. `Active` = customização não gerenciada.", "",
                  mdtable(["Ordem", "Solução", "Publisher", "Sobrescrita em"],
                          [[x["order"], x["solution"], x["publisher"], x["overwritten"]] for x in layers])]
        rr = rels_by_table.get(ln, [])
        if rr:
            def other(r):
                o = r["to"] if r["from"] == ln else r["from"]
                return v.link("table", o, o, in_table=True)
            b += ["## Relacionamentos", "",
                  mdtable(["Tipo", "Relacionamento", "Com", "Direção", "Lookup / Intersect", "Delete em cascata"],
                          [[r["kind"], f"`{r['schema']}`", other(r),
                            ("pai" if r["from"] == ln else "filho") if r["kind"] == "1:N" else "N:N",
                            r.get("lookup") or r.get("intersect"), r.get("cascade_delete")] for r in rr])]
        ff = forms_by_table.get(ln, [])
        if ff:
            b += ["## Formulários", "",
                  mdtable(["Formulário", "Tipo", "Ativo", "Gerenciado", "Bibliotecas JS"],
                          [[f["name"], f["type"], f["active"], f["managed"],
                            ", ".join(v.link("webresource", lib, lib, in_table=True) for lib in f.get("libraries") or [])]
                           for f in ff])]
            hs = [(f, h) for f in ff for h in f.get("handlers") or []]
            if hs:
                b += ["### Eventos de formulário", "",
                      mdtable(["Formulário", "Evento", "Campo", "Biblioteca", "Função", "Ativo"],
                              [[f["name"], h["event"], h.get("field"), v.link("webresource", h.get("library"), h.get("library"), in_table=True),
                                f"`{h.get('function')}`", h.get("enabled")] for f, h in hs])]
        vv = views_by_table.get(ln, [])
        if vv:
            b += [f"> [!example]- Views ({len(vv)})", "> " + mdtable(["View", "Tipo", "Padrão", "Ativa", "Gerenciada"],
                  [[x["name"], x["querytype"], x["default"], x["active"], x["managed"]] for x in vv]).replace("\n", "\n> "), ""]
        if st or pr or fl or apis_by_table.get(ln):
            b += ["## Automação", ""]
        if st:
            b += ["### Plugin steps", "", mdtable(["Step", "Mensagem", "Estágio", "Modo", "Ordem", "Ativo", "Filtering"],
                  [[v.link("step", s["id"], s["name"], in_table=True), s["message"], s["stage"], s["mode"], s["rank"],
                    s["enabled"], s.get("filtering")] for s in sorted(st, key=lambda s: (s["message"] or "", str(s["stage"]), s["rank"] or 0))])]
        if pr:
            b += ["### Processos com esta tabela como primária", "",
                  mdtable(["Processo", "Categoria", "Ativo", "Gatilho / modo", "Proprietário"],
                          [[v.link("process", p["id"], p["name"], in_table=True), p["category"], p["active"],
                            p.get("trigger") or p.get("mode"), p.get("owner")] for p in pr])]
        if fl:
            b += ["### Flows que leem/gravam esta tabela", "",
                  "\n".join(f"- {v.link('process', p['id'], p['name'])} ({p['category']})" for p in fl), ""]
        pcf_here = [(p, x) for p in d.get("pcf") or [] for x in p.get("usage") or [] if x["entity"] == ln]
        if pcf_here:
            b += ["### Componentes PCF nos formulários", "",
                  mdtable(["Controle", "Formulário", "Coluna"],
                          [[v.link("pcf", p["name"], p["name"]), x["form"], x.get("field") or "(subgrid/dataset)"]
                           for p, x in pcf_here])]
        if apis_by_table.get(ln):
            b += ["### Custom APIs vinculadas", "", "\n".join(f"- {v.link('customapi', a['unique'], a['unique'])}" for a in apis_by_table[ln]), ""]
        if ribbons_by_table.get(ln):
            b += ["## Botões customizados (ribbon)", "",
                  mdtable(["Botão", "Rótulo", "Chamadas"],
                          [[f"`{r['button']}`", r.get("label"),
                            "; ".join(f"{c['library']} → {c['function']}" for c in r["calls"])] for r in ribbons_by_table[ln]])]
        if ln in fu_by_table:
            fu = fu_by_table[ln]
            from ..collectors.usage import BUCKETS
            cnt = Counter(f["bucket"] for f in fu["fields"])
            b += ["## Uso das colunas customizadas", "",
                  f"Registros considerados: **{fu.get('total')}** · método: {fu.get('method')}"
                  f"{'' if fu.get('repos_measured') else ' · sem repositório de código'}", "",
                  *(["> [!warning] Cobertura incompleta da matriz de uso — colunas sem dados e sem uso encontrado "
                     "ficam como **inconclusivo**", *[f"> - {m}" for m in fu.get("missing_sources") or []], ""]
                    if fu.get("missing_sources") else []),
                  " · ".join(f"**{cnt[k]}** {BUCKETS[k].lower()}" for k in BUCKETS if cnt.get(k)), "",
                  mdtable(["Coluna", "Preenchido", "%", "Forms", "Forms inativos", "Views", "PCF", "Processos/flows",
                           "Plugins", "JS", "Repo", "Classificação"],
                          [[f"`{f['logical']}`", f.get("populated"), f.get("pct"), len(f["forms"]) + len(f["form_events"]),
                            len(f.get("forms_inactive") or []), len(f["views"]), len(f.get("pcf") or []), len(f["processes"]),
                            len(f["plugin_steps"]), len(f["javascript"]), f["repo_files"], f["bucket"]]
                           for f in fu["fields"]])]
        if ln in matrix:
            acts = ["Create", "Read", "Write", "Delete", "Append", "AppendTo", "Assign", "Share"]
            b += ["## Privilégios (papéis analisados)", "",
                  mdtable(["Papel"] + acts, [[v.link("role", rn, rn, in_table=True)] + [p.get(a) for a in acts]
                                             for rn, p in sorted(matrix[ln].items())])]
        b.append(findings_block("table", ln))
        v.write(v.paths[("table", ln)], props, "\n".join(b))

    # ---- 3) plugins ----
    for a in plugins["assemblies"]:
        props = {"tipo": "plugin-assembly", "nome": a["name"], "versao": a.get("version"), "isolamento": a.get("isolation"),
                 "origem": a.get("source"), "gerenciado": a.get("managed"), "tipos": len(a["types"]),
                 "steps": sum(t["steps"] for t in a["types"]), "modificado": a.get("modified"),
                 "solucoes": sol_links(a.get("solutions")), "tags": v.tags("plugin-assembly")}
        steps = [s for s in plugins["steps"] if s.get("assembly") == a["name"]]
        b = [f"# {a['name']}", "", f"Versão **{a.get('version')}** · isolamento {a.get('isolation')} · origem {a.get('source')} · "
             f"public key token `{a.get('publickeytoken')}` · modificado em {a.get('modified')}", ""]
        if a.get("binary"):
            b += [f"> [!tip] Binário salvo em `{a['binary']}` — decompilar com `ilspycmd -p -o <dir> <dll>` "
                  "para comparar com o repositório (drift repo × produção).", ""]
        b += ["## Classes", "", mdtable(["Classe", "Workflow activity", "Steps"],
              [[f"`{t['typename']}`", t["workflow_activity"], t["steps"]] for t in a["types"]])]
        repo_rows = [(rn, t) for ty in a["types"] for rn, t in type_repo.get(ty["typename"], [])]
        if repo_rows:
            b += ["## Repositório × produção", "", mdtable(["Classe", "Repositório", "Status", "Arquivo(s)", "Métodos só em PRD", "Métodos só no repo"],
                  [[f"`{t['type']}`", rn, t["status"], ", ".join(t["repo_files"][:3]), ", ".join(t.get("methods_only_prd") or []),
                    ", ".join(t.get("methods_only_repo") or [])] for rn, t in repo_rows])]
        b += ["## Steps registrados", "", mdtable(["Step", "Tabela", "Mensagem", "Estágio", "Modo", "Ativo"],
              [[v.link("step", s["id"], s["name"], in_table=True), v.link("table", s["entity"], s["entity"], in_table=True),
                s["message"], s["stage"], s["mode"], s["enabled"]] for s in steps])]
        b.append(findings_block("assembly", a["name"]))
        v.write(v.paths[("assembly", a["name"])], props, "\n".join(b))

    for s in plugins["steps"]:
        props = {"tipo": "plugin-step", "nome": s["name"], "assembly": v.link("assembly", s.get("assembly"), s.get("assembly")) if s.get("assembly") else None,
                 "classe": s.get("type"), "handler": s.get("handler_kind"), "mensagem": s["message"],
                 "tabela": v.link("table", s["entity"], s["entity"]) if s.get("entity") else None,
                 "estagio": s["stage"], "modo": s["mode"], "ordem": s["rank"], "ativo": s["enabled"],
                 "filtering": s.get("filtering"), "imagens": len(s.get("images") or []), "gerenciado": s.get("managed"),
                 "impersonando": s.get("impersonating"), "modificado": s.get("modified"),
                 "solucoes": sol_links(s.get("solutions")), "tags": v.tags("plugin-step")}
        b = [f"# {s['name']}", "",
             f"**{s['message']}** em {v.link('table', s['entity'], s['entity']) if s.get('entity') else '(qualquer tabela)'} · "
             f"{s['stage']} · {s['mode']} · ordem {s['rank']} · {'ativo' if s['enabled'] else '**desativado**'}", "",
             f"- Handler: {'`' + str(s.get('type')) + '`' if s.get('type') else s.get('handler')} ({s.get('handler_kind')})",
             f"- Filtering attributes: {('`' + s['filtering'] + '`') if s.get('filtering') else '_nenhum (dispara para qualquer coluna)_'}",
             f"- Configuração unsecure: {'sim' if s.get('has_config') else 'não'}", ""]
        if s.get("images"):
            b += ["## Imagens", "", mdtable(["Nome", "Alias", "Tipo", "Atributos"],
                  [[i["name"], i["alias"], i["type"], i.get("attributes") or "(todas)"] for i in s["images"]])]
        b.append(findings_block("step", s["id"]))
        v.write(v.paths[("step", s["id"])], props, "\n".join(b))

    # ---- 4) processos ----
    for p in procs:
        props = {"tipo": "processo", "nome": p["name"], "categoria": p["category"],
                 "tabela": v.link("table", p["entity"], p["entity"]) if p.get("entity") else None,
                 "ativo": p["active"], "gerenciado": p.get("managed"), "modo": p.get("mode"), "gatilho": p.get("trigger"),
                 "conectores": p.get("connectors") or [], "hosts_http": p.get("http_hosts") or [],
                 "proprietario": p.get("owner"), "proprietario_desativado": p.get("owner_disabled"),
                 "modificado": p.get("modified"), "criado": p.get("created"), "modificado_por": p.get("modified_by"),
                 "solucoes": sol_links(p.get("solutions")), "tags": v.tags("processo")}
        b = [f"# {p['name']}", "", f"**{p['category']}** · {'ativo' if p['active'] else 'inativo'}"
             f"{' · ' + v.link('table', p['entity'], p['entity']) if p.get('entity') else ''} · proprietário {p.get('owner')}"
             f"{' ⚠️ desativado' if p.get('owner_disabled') else ''}", ""]
        if p.get("description"):
            b += [f"> {p['description']}", ""]
        trig = []
        if p.get("on_create"):
            trig.append("Create")
        if p.get("on_update"):
            trig.append(f"Update de `{p['on_update']}`")
        if p.get("on_delete"):
            trig.append("Delete")
        if p.get("on_demand"):
            trig.append("Sob demanda")
        if trig:
            b += [f"- Dispara em: {', '.join(trig)}"]
        if p.get("mode"):
            b += [f"- Modo: {p['mode']}"]
        if p.get("trigger"):
            b += [f"- Gatilho: {p['trigger']} — `{p.get('trigger_detail')}`",
                  f"- Ações: {p.get('actions')} · child flows: {p.get('child_flows')}",
                  f"- Conectores: {', '.join(p.get('connectors') or []) or '—'}",
                  f"- Hosts HTTP: {', '.join(p.get('http_hosts') or []) or '—'}"]
            if p.get("tables"):
                b += ["- Tabelas tocadas: " + ", ".join(v.link("table", t, t) for t in p["tables"])]
            if p.get("action_types"):
                b += [f"- Condições: {p.get('conditions')} · tratamentos de erro (runAfter Failed/TimedOut): "
                      f"{p.get('error_handlers')} · profundidade máx.: {p.get('max_depth')}",
                      "- Tipos de ação: " + ", ".join(f"{k} ({n})" for k, n in p["action_types"].items())]
        if p.get("field_refs"):
            b += ["", f"**Colunas citadas ({len(p['field_refs'])}):** " + ", ".join(f"`{f}`" for f in p["field_refs"])]
        if p.get("outline"):
            b += ["", f"> [!example]- Estrutura do flow ({p.get('actions')} ações)"] + [f"> {line}" for line in p["outline"]]
        b.append("")
        b.append(findings_block("process", p["id"]))
        v.write(v.paths[("process", p["id"])], props, "\n".join(b))

    # ---- 5) web resources ----
    fence = {"JScript": "js", "HTML": "html", "CSS": "css", "XML": "xml", "SVG": "xml", "XSL": "xml", "RESX": "xml", "TS": "ts"}
    for w in wrs:
        used = wr_usage.get(w["name"].lower(), [])
        props = {"tipo": "webresource", "nome": w["name"], "tipo_arquivo": w["type"], "gerenciado": w.get("managed"),
                 "tamanho_bytes": w.get("size"), "funcoes": len(w.get("functions") or []), "formularios": len(used),
                 "segredos": len(w.get("secret_hits") or []), "modificado": w.get("modified"),
                 "solucoes": sol_links(w.get("solutions")), "tags": v.tags("webresource")}
        b = [f"# {w['name']}", "", f"{w['type']} · {w.get('display') or ''} · modificado em {w.get('modified')}", ""]
        if w.get("secret_hits"):
            b += ["> [!danger] Possível segredo em texto claro",
                  "> " + "; ".join(f"linha {h['line']}: {h['kind']}" for h in w["secret_hits"]) +
                  " — valor redigido neste vault; **rotacionar na origem**.", ""]
        if used:
            b += ["## Usado em formulários", "", "\n".join(sorted({f"- {v.link('table', f['entity'], f['entity'])} — {f['name']}" for f in used})), ""]
        js = w.get("js")
        if js:
            flags = [("API obsoleta Xrm.Page", js.get("xrm_page")), ("eval()", js.get("eval")),
                     ("endpoint SOAP/OData 2011", js.get("odata_2011")), ("XHR síncrono", js.get("sync_xhr")),
                     ("XrmServiceToolkit", js.get("xrm_service_toolkit")), ("jQuery", js.get("jquery")),
                     ("HTTP direto (fetch/ajax/XHR)", js.get("raw_http")), ("formContext (moderno)", js.get("form_context"))]
            b += ["## Análise estática", "",
                  f"{js.get('lines')} linhas{' · **biblioteca de terceiros**' if js.get('third_party') else ''} · "
                  f"console.*: {js.get('console_calls')} · TODO/FIXME: {js.get('todo_count')}", "",
                  "- Usa: " + (", ".join(n for n, on in flags if on) or "—"),
                  f"- Xrm.WebApi: {', '.join(js.get('webapi_methods') or []) or '—'}",
                  f"- Hosts externos: {', '.join(js.get('external_hosts') or []) or '—'}",
                  f"- Colunas lidas (getAttribute): {', '.join(f'`{a}`' for a in js.get('attributes_read') or []) or '—'}", ""]
        for rn, rw in wr_repo.get(w["name"], []):
            callout = "success" if rw["status"] == "idêntico" else "warning"
            b += [f"> [!{callout}] Repositório {rn}: {rw['status']}",
                  f"> Arquivo: `{rw.get('file', '—')}` · casado por {rw.get('match', '—')}"
                  + (f" · similaridade {rw['similarity']:.0%}" if rw.get("similarity") is not None else "")]
            if rw.get("functions_only_env"):
                b += [f"> Funções só no ambiente: {', '.join(rw['functions_only_env'])}"]
            if rw.get("functions_only_repo"):
                b += [f"> Funções só no repo: {', '.join(rw['functions_only_repo'])}"]
            b.append("")
        if w.get("functions"):
            b += [f"## Funções ({len(w['functions'])})", "", ", ".join(f"`{x}`" for x in w["functions"]), ""]
        if w.get("content"):
            content = w["content"]
            if (js or {}).get("third_party") or len(content) > CODE_IN_NOTE_MAX:
                why = "biblioteca de terceiros" if (js or {}).get("third_party") else f"maior que {CODE_IN_NOTE_MAX // 1000} KB"
                b += ["## Código-fonte", "", f"_Não incluído na nota ({why})._ Código redigido no snapshot: "
                      f"`_raw/{getattr(v, 'run_id', '<run_id>')}/webresources.json` (busque por `\"name\": \"{w['name']}\"`).", ""]
            else:
                b += ["## Código-fonte (redigido)", "", f"```{fence.get(w['type'], '')}", content.replace("```", "``\u200b`"), "```", ""]
        b.append(findings_block("webresource", w["name"]))
        v.write(v.paths[("webresource", w["name"])], props, "\n".join(b))

    # ---- 6) custom APIs, apps, soluções, papéis ----
    for a in apis:
        props = {"tipo": "customapi", "nome": a["unique"], "binding": a.get("binding"),
                 "tabela": v.link("table", a["bound_entity"], a["bound_entity"]) if a.get("bound_entity") else None,
                 "funcao": a.get("is_function"), "privada": a.get("private"), "plugin": a.get("plugin_type"),
                 "gerenciado": a.get("managed"), "solucoes": sol_links(a.get("solutions")), "tags": v.tags("customapi")}
        b = [f"# {a['unique']}", "", a.get("description") or "", "",
             f"- Binding: {a.get('binding')} {('→ ' + v.link('table', a['bound_entity'], a['bound_entity'])) if a.get('bound_entity') else ''}",
             f"- {'Function (GET)' if a.get('is_function') else 'Action (POST)'} · privada: {a.get('private')} · steps permitidos: {a.get('steps_allowed')}",
             f"- Implementação: `{a.get('plugin_type') or '—'}`", "",
             "## Parâmetros de entrada", "", mdtable(["Nome", "Tipo", "Opcional"], [[p["name"], p["type"], p["optional"]] for p in a["request"]]),
             "## Propriedades de resposta", "", mdtable(["Nome", "Tipo"], [[p["name"], p["type"]] for p in a["response"]]),
             findings_block("customapi", a["unique"])]
        v.write(v.paths[("customapi", a["unique"])], props, "\n".join(b))

    kinds = {"modeldriven": "App model-driven", "canvas": "Canvas app / custom page", "bots": "Agente Copilot Studio"}
    for k, label_ in kinds.items():
        for a in apps.get(k) or []:
            props = {"tipo": "app", "subtipo": label_, "nome": a["name"], "nome_unico": a.get("unique"),
                     "gerenciado": a.get("managed"), "modificado": a.get("modified") or a.get("published"),
                     "solucoes": sol_links(a.get("solutions")), "tags": v.tags("app")}
            body = [f"# {a['name']}", "", f"{label_} · `{a.get('unique')}`", "", a.get("description") or ""]
            body.append(findings_block("app", a["id"]))
            v.write(v.paths[("app", a["id"])], props, "\n".join(body))

    for s in sols:
        if ("solution", s["uniquename"]) not in v.paths:
            continue
        cc = s.get("component_counts") or {}
        props = {"tipo": "solucao", "nome": s["uniquename"], "nome_exibicao": s.get("name"), "versao": s.get("version"),
                 "gerenciada": s.get("managed"), "publisher": s.get("publisher"), "prefixo": s.get("publisher_prefix"),
                 "componentes": s.get("component_total"), "instalada": s.get("installed"), "modificada": s.get("modified"),
                 "tags": v.tags("solucao")}
        b = [f"# {s.get('name')} (`{s['uniquename']}`)", "", f"v{s.get('version')} · publisher {s.get('publisher')} "
             f"(`{s.get('publisher_prefix')}`) · {'gerenciada' if s.get('managed') else 'não gerenciada'}", "",
             s.get("description") or "", "", "## Componentes por tipo", "", mdtable(["Tipo", "Qtd"], list(cc.items()))]
        v.write(v.paths[("solution", s["uniquename"])], props, "\n".join(b))

    for r in sec.get("roles") or []:
        if ("role", r["name"]) not in v.paths:
            continue
        mine = {t: p[r["name"]] for t, p in matrix.items() if r["name"] in p}
        props = {"tipo": "papel", "nome": r["name"], "gerenciado": r.get("managed"),
                 "tabelas_com_privilegio": len(mine) if matrix else None,
                 "solucoes": sol_links(r.get("solutions")), "tags": v.tags("papel")}
        acts = ["Create", "Read", "Write", "Delete", "Append", "AppendTo", "Assign", "Share"]
        b = [f"# {r['name']}", ""]
        if matrix:
            b += ["## Privilégios em tabelas do escopo", "",
                  mdtable(["Tabela"] + acts, [[v.link("table", t, t, in_table=True)] + [p.get(a) for a in acts] for t, p in sorted(mine.items())])]
        else:
            b += ["> [!info] Privilégios não coletados (`deep.role_privileges: false` no yaml ou perfil).", ""]
        b.append(structure.role_assignments(v, d, r["name"]))
        b.append(findings_block("role", r["name"]))
        v.write(v.paths[("role", r["name"])], props, "\n".join(b))
    structure.write_notes(v, d, findings_block, sol_links)
    navigation.write_notes(v, d, findings_block, sol_links)
    navigation.write_commands(v, d, findings_block, sol_links)

    # ---- 7) achados ----
    for f in fnd:
        props = {"tipo": "achado", "id_achado": f["id"], "severidade": f["severity"], "titulo": f["title"],
                 "metrica": f.get("metric"), "status": "aberto", "responsavel": None, "tags": v.tags("achado")}
        b = [f"# {f['id']} — {f['title']}", "", f"> [!{SEV_CALLOUT.get(f['severity'], 'note')}] Severidade: {f['severity']}",
             f"> {f['detail']}", ""]
        refs = [r for r in f["refs"] if tuple(r) in v.paths]
        if refs:
            uniq = list(dict.fromkeys((k, key) for k, key in refs))
            b += [f"## Componentes afetados ({len(uniq)})", "", "\n".join(f"- {v.link(k, key)}" for k, key in uniq[:500]), ""]
        if f.get("evidence"):
            b += ["## Evidências", "", "\n".join(f"- {e}" for e in f["evidence"][:500]), ""]
        b += ["Fonte: `_derived/<run_id>/findings.json` (regra em `dvinv/findings.py`). Status e responsável editáveis — preservados entre extrações."]
        v.write(v.paths[("finding", f["id"])], props, "\n".join(b))

    # ---- 8) notas de seção ----
    manifest = d["manifest"]
    write_integrations(v, d, procs, apis)
    write_field_usage(v, d)
    write_storage(v, d)
    write_repos(v, d)
    write_dependencies(v, d, graph, dsum, node_link)
    write_cleanup(v, d, graph, fnd, node_link)
    write_field_matrix_csv(v, d, fmatrix)
    write_field_matrix(v, fmatrix, any_change)
    write_solution_history(v, d)
    structure.write_security(v, d)
    write_environment(v, d, manifest)
    bases.write_all(v, fnd)
    canvas.write_map(v, d)
    has_catalog = catalog.write_catalog(v, d)
    v.maps = maps.write_maps(v, graph)  # Mapas/<Tipo>.md + .canvas: navegação e grafo por tipo
    write_changes(v, chg)
    write_index(v, d, manifest, fnd, has_catalog, chg)
    catalog.write_agent_index(v, d, has_catalog)  # lista as notas grandes já escritas
    prune_orphans(v)                               # por último: tudo que esta extração escreveu já está em v.written
    return fnd, deps_json


GIT_FILE_WARN = 50_000_000
WIN_REL_PATH_WARN = 190


def _rename_retry(src, dst, tries=6):
    """os.rename com novas tentativas: no Windows a troca da pasta falha (WinError 5/32) enquanto o antivírus,
    o Explorer ou um editor seguram um arquivo dela. Persistindo, explica o que fechar."""
    import time
    for i in range(tries):
        try:
            return os.rename(src, dst)
        except PermissionError:
            if i == tries - 1:
                raise SystemExit(f"[dvinv] não foi possível renomear {src} → {dst}: pasta em uso (feche Obsidian, "
                                 "Explorer, editores ou terminais abertos nela e rode o render de novo). O vault "
                                 "anterior foi mantido.") from None
            time.sleep(min(2 ** i, 10))
GENERATED_FILES = {"Componentes.csv", "Dependências.csv", "Dependências ausentes.csv", "Equipes.csv", "Sem dependentes.csv",
                   "Candidatos a limpeza.csv", "Uso dos campos.csv",
                   "Matriz de Dependências.xlsx"}  # a planilha saiu na 0.6: some do vault no próximo render
ORPHANS_NOTE = "Notas fora do inventário atual"


def _has_curation(text):
    """Algo escrito pelo analista abaixo do marcador (além do cabeçalho padrão)?"""
    if MANUAL not in text:
        return True  # sem marcador: não é nota gerada pelo dvinv — nunca apagar
    tail = text.split(MANUAL, 1)[1].replace("## Notas do analista", "")
    return bool(tail.strip())


def prune_orphans(v):
    """Remove da cópia de trabalho as notas geradas por extrações anteriores que não existem nesta (componente
    removido do ambiente ou fora do novo corte) e não têm curadoria. Com curadoria: mantém e lista em
    `Notas fora do inventário atual.md`. Num vault publicado em Git, nota velha engana o time e a IA."""
    base = v.root / v.folder
    written = {Path(p).resolve() for p in v.written}
    v.pruned, v.orphans_kept = [], []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.resolve() in written or any(x.startswith(".") for x in p.relative_to(base).parts):
            continue
        rel = p.relative_to(base).as_posix()
        if p.suffix == ".md":
            if p.stem == ORPHANS_NOTE:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if MANUAL not in text:
                continue  # nota do time (sem marcador do dvinv): não é nossa
            if _has_curation(text):
                v.orphans_kept.append(rel)
            else:
                p.unlink()
                v.pruned.append(rel)
        elif p.name in GENERATED_FILES and "/" not in rel:
            p.unlink()
            v.pruned.append(rel)
    note = f"{v.folder}/{ORPHANS_NOTE}"
    if v.orphans_kept:
        v.write(note, {"tipo": "secao", "notas": len(v.orphans_kept), "tags": v.tags("secao")}, "\n".join([
            f"# {ORPHANS_NOTE}", "",
            "Notas de extrações anteriores cujo componente **não aparece nesta extração** (removido do ambiente ou fora "
            "do escopo/corte atual), mantidas porque têm curadoria abaixo do marcador manual. Revise, mova a análise "
            "para onde fizer sentido e apague a nota.", "",
            "\n".join(f"- [[{v.folder}/{r[:-3]}|{r[:-3]}]]" for r in v.orphans_kept)]))
    else:
        (v.root / f"{note}.md").unlink(missing_ok=True)


def write_integrations(v, d, procs, apis):
    alm = d.get("alm") or {}
    flows = [p for p in procs if p["category"] == "Cloud Flow"]
    conn = Counter(c for p in flows for c in p.get("connectors") or [])
    hosts = defaultdict(list)
    for p in flows:
        for h in p.get("http_hosts") or []:
            hosts[h].append(p)
    b = ["# Integrações", "", "Mapa de tudo que entra ou sai do Dataverse: conectores usados por flows, chamadas HTTP, "
         "service endpoints, Custom APIs, variáveis de ambiente e referências de conexão.", ""]
    b += ["## Conectores usados por cloud flows", "", mdtable(["Conector", "Flows"], conn.most_common())]
    b += ["## Hosts HTTP chamados por flows", "", mdtable(["Host", "Flows"],
          [[h, ", ".join(v.link("process", p["id"], p["name"], in_table=True) for p in ps)] for h, ps in sorted(hosts.items())])]
    b += ["## Service endpoints", "", mdtable(["Nome", "Contrato", "Auth", "URL (redigida)"],
          [[e["name"], e.get("contract"), e.get("auth"), e.get("url")] for e in d.get("serviceendpoints") or []])]
    b += ["## Custom APIs", "", mdtable(["API", "Binding", "Tipo", "Plugin"],
          [[v.link("customapi", a["unique"], a["unique"], in_table=True), a.get("bound_entity") or a.get("binding"),
            "Function" if a.get("is_function") else "Action", a.get("plugin_type")] for a in apis])]
    b += ["## Variáveis de ambiente", "", mdtable(["Variável", "Tipo", "Valor atual (redigido)", "Default", "Tem valor"],
          [[f"`{e['name']}`", e.get("type"), (e.get("value") or "")[:80], (e.get("default") or "")[:80], e["has_value"]]
           for e in alm.get("envvars") or []])]
    b += ["## Referências de conexão", "", mdtable(["Referência", "Conector", "Conectada", "Ativa"],
          [[f"`{r['name']}`", r["connector"], r["connected"], r["active"]] for r in alm.get("connrefs") or []])]
    b += ["## Conectores customizados", "", mdtable(["Conector", "Tipo", "Gerenciado"],
          [[k["name"], k.get("kind"), k.get("managed")] for k in alm.get("connectors") or []])]
    vt = d.get("virtual_tables") or {}
    if vt.get("tables"):
        b += ["## Tabelas virtuais", "",
              "Dados lidos de fora do Dataverse em tempo de consulta (provedor de dados + fonte). Todas as da org; "
              "as do escopo têm nota com as classes de plugin do provedor.", "",
              mdtable(["Tabela", "Provedor", "Fonte de dados", "Nome externo", "Custom", "No escopo"],
                      [[v.link("table", t["logical"], t["logical"], in_table=True), t.get("provider") or t.get("provider_id"),
                        t.get("datasource") or t.get("datasource_table"), t.get("external_name"), t.get("custom"),
                        "sim" if t.get("in_scope") else ""] for t in vt["tables"]])]
        if vt.get("elastic"):
            b += [f"Tabelas **elásticas** na org: {len(vt['elastic'])} (contagem; armazenamento NoSQL do Dataverse).", ""]
    v.write(f"{v.folder}/03 Integrações", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))


def write_field_usage(v, d):
    fu = d.get("field_usage")
    if not fu:
        return
    from ..collectors.usage import BUCKETS
    total = Counter(f["bucket"] for t in fu for f in t["fields"])
    b = ["# Uso de campos", "",
         "Preenchimento real × onde cada coluna customizada é usada. Quem usa cada campo (formulário, evento, JS, "
         "plugin, business rule, workflow, BPF, flow) e o que roda quando ele muda: seção **Uso dos campos** de cada "
         f"tabela e [[{v.folder}/Uso dos campos.csv|Uso dos campos.csv]].", "",
         "> [!warning] Nenhuma classificação afirma que remover é seguro",
         "> **Candidato a investigação de remoção** = zero exato (contagem completa ou leitura de existência sem valor), "
         "nenhuma referência "
         "encontrada e todas as fontes da matriz medidas (forms, views, processos/flows, plugins, JS, repositório). "
         "Antes de remover, confirme integrações externas (ETL, Power BI, portais, APIs) e flows fora de solução.",
         "> **Inconclusivo** = zero preenchidos numa amostra parcial, medição que falhou, ou alguma fonte da matriz "
         "ausente/falha/não medida — resolver a lacuna antes de concluir.", "",
         mdtable(["Classificação", "Colunas"], [[BUCKETS[k], total.get(k, 0)] for k in BUCKETS]),
         "## Por tabela", "",
         mdtable(["Tabela", "Registros", "Método", "Cobertura"] + list(BUCKETS),
                 [[v.link("table", t["table"], t["table"]), t.get("total"), t.get("method"),
                   "completa" if t.get("coverage_complete") else "incompleta"] +
                  [sum(1 for f in t["fields"] if f["bucket"] == k) for k in BUCKETS] for t in fu])]
    safe = [(t, f) for t in fu for f in t["fields"] if f["bucket"] == "candidato-seguro"]
    b += ["## Candidatos a investigação de remoção", "",
          "Confirmar integrações externas antes de qualquer remoção.", "",
          mdtable(["Tabela", "Coluna", "Nome", "Tipo"],
                  [[v.link("table", t["table"], t["table"]), f"`{f['logical']}`", f.get("display"), f.get("type")]
                   for t, f in safe])]
    inc = [(t, f) for t in fu for f in t["fields"] if f["bucket"] == "inconclusivo"]
    if inc:
        b += ["## Inconclusivos", "", mdtable(["Tabela", "Coluna", "Preenchido", "Motivos"],
              [[v.link("table", t["table"], t["table"]), f"`{f['logical']}`", f.get("populated"),
                "; ".join(f.get("inconclusive_reasons") or [])] for t, f in inc])]
    weak = [(t, f) for t in fu for f in t["fields"] if f.get("forms_inactive")]
    if weak:
        b += ["## Uso fraco (só em formulário inativo)", "", mdtable(["Tabela", "Coluna", "Formulários inativos", "Classificação"],
              [[v.link("table", t["table"], t["table"]), f"`{f['logical']}`", ", ".join(f["forms_inactive"]), f["bucket"]]
               for t, f in weak])]
    v.write(f"{v.folder}/05 Uso de Campos", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))


def write_storage(v, d):
    st = d.get("storage")
    if not st:
        return
    gb = lambda x: f"{(x or 0) / 1e9:,.2f} GB".replace(",", "X").replace(".", ",").replace("X", ".")  # noqa: E731
    b = ["# Armazenamento e auditoria", "",
         "> [!info] Números lidos pela Web API (contagens e somas de `filesize`). O consumo oficial de capacidade "
         "(Database/File/Log contratado × usado) só existe no Power Platform Admin Center.", "",
         "## Maiores tabelas (registros)", "",
         mdtable(["Tabela", "Registros"], [[v.link("table", r["table"], r["table"]), r["records"]] for r in st.get("largest_tables") or []])]
    for key in ("annotations", "email_attachments"):
        sec = st.get(key) or {}
        b += [f"## {sec.get('label', key)}", "", f"**{sec.get('count')}** arquivos · **{gb(sec.get('bytes'))}** "
              f"({sec.get('bytes_method', '—')})", "",
              *(["> [!note] Quantidade por tabela cobre só as tabelas do escopo (o agrupamento na org inteira "
                 "excedeu o tempo de SQL do servidor).", ""] if sec.get("by_table_scope_only") else []),
              mdtable(["Tabela", "Quantidade", "Bytes"], [[v.link("table", r["table"], r["table"]), r.get("count"),
                                                         gb(r["bytes"]) if r.get("bytes") is not None else "—"]
                                                        for r in (sec.get("by_table") or [])[:25]]),
              mdtable(["Tipo de arquivo", "Quantidade", "Bytes"], [[m["mimetype"], m.get("count"), gb(m.get("bytes"))]
                                                                 for m in sec.get("by_mimetype") or []])]
    aud = st.get("audit") or {}
    b += ["## Auditoria", "", f"**{aud.get('total')}** registros · mais antigo: **{aud.get('oldest')}**", "",
          mdtable(["Tabela", "Registros"], [[v.link("table", r["table"], r["table"]), r["count"]] for r in (aud.get("by_table") or [])[:30]]),
          mdtable(["Ação", "Registros"], [[r["action"], r["count"]] for r in aud.get("by_action") or []])]
    v.write(f"{v.folder}/06 Armazenamento e Auditoria", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))


def write_repos(v, d):
    repos = d.get("repos")
    if not repos:
        return
    b = ["# Repositórios × ambiente", ""]
    for r in repos:
        wrs = r.get("webresources") or []
        tys = r.get("plugin_types") or []
        git = (f"branch **{r['branch']}** · último commit `{r.get('commit')}`" if r.get("branch")
               else "não é um clone Git (sem branch/commit)")
        b += [f"## {r['name']}", "", f"`{r['path']}` · {git} · {r['files']} arquivos de texto lidos", ""]
        if wrs:
            cnt = Counter(w["status"] for w in wrs)
            b += ["### Web resources", "", " · ".join(f"**{n}** {k}" for k, n in cnt.items()), "",
                  mdtable(["Web resource", "Status", "Arquivo", "Similaridade", "Só no ambiente", "Só no repo"],
                          [[v.link("webresource", w["name"], w["name"]), w["status"], w.get("file"),
                            f"{w['similarity']:.0%}" if w.get("similarity") is not None else None,
                            ", ".join(w.get("functions_only_env") or []), ", ".join(w.get("functions_only_repo") or [])] for w in wrs])]
        if tys:
            cnt = Counter(t["status"] for t in tys)
            b += ["### Classes de plugin", "", " · ".join(f"**{n}** {k}" for k, n in cnt.items()), "",
                  mdtable(["Classe", "Assembly", "Status", "Arquivo(s)", "Métodos só em PRD", "Métodos só no repo"],
                          [[f"`{t['type']}`", v.link("assembly", t["assembly"], t["assembly"]), t["status"],
                            ", ".join(t["repo_files"][:3]), ", ".join(t.get("methods_only_prd") or []),
                            ", ".join(t.get("methods_only_repo") or [])] for t in tys])]
        if r.get("secret_hits"):
            b += ["> [!danger] Possíveis segredos versionados (valores não reproduzidos aqui)"] + \
                 [f"> - `{h['file']}` linha {h['line']}: {h['kind']}" for h in r["secret_hits"]] + [""]
    v.write(f"{v.folder}/07 Repositórios", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))


def write_dependencies(v, d, g, dsum, node_link):
    kinds = deps_mod.KIND_LABEL
    n_inf = sum(1 for e in g.edges.values() if "inferida" in e["source"])
    n_plat = sum(1 for e in g.edges.values() if "plataforma" in e["source"])
    plat_on = bool(d.get("platform_dependencies"))
    b = ["# Matriz de dependências", "",
         "**A → B** significa *A depende de B*: se B mudar ou for removido, A é afetado. Use a matriz por tabela "
         "para dimensionar impacto de mudança e as listas de órfãos para limpeza.", "",
         f"**{len(g.nodes)}** componentes · **{len(g.edges)}** dependências ({n_inf} inferidas pelo kit, "
         f"{n_plat} registradas pela plataforma) · exportação: "
         + f"[[{v.folder}/Dependências.csv|CSV]] (uma linha por aresta, com chave e GUID das duas pontas)", ""]
    if not plat_on:
        b += ["> [!info] Dependências da plataforma não coletadas",
              "> Ligue `deep.platform_dependencies` para incluir o que o Dataverse registra (ex. view → coluna, "
              "sitemap → tabela) e as dependências ausentes de cada solução.", ""]
    cols = deps_mod.IMPACT_COLS
    b += ["## Impacto por tabela (quem depende de cada tabela)", "",
          mdtable(["Tabela"] + [c for _, c in cols] + ["Total", "Depende de"],
                  [[v.link("table", r["table"], r["table"])] + [r[k] or "" for k, _ in cols] + [r["total"], r["depends_on"]]
                   for r in dsum["per_table"]])]
    tt = dsum["type_x_type"]
    src_k = sorted({a for a, _ in tt}, key=lambda k: list(kinds).index(k) if k in kinds else 99)
    dst_k = sorted({b_ for _, b_ in tt}, key=lambda k: list(kinds).index(k) if k in kinds else 99)
    b += ["## Tipo × tipo", "", "Linhas dependem das colunas.", "",
          mdtable(["Depende ↓ / de →"] + [kinds.get(k, k) for k in dst_k],
                  [[kinds.get(a, a)] + [tt.get((a, c), "") for c in dst_k] for a in src_k])]
    pst = ((d.get("manifest") or {}).get("stats") or {}).get("platform_dependencies") or {}
    if pst.get("edges"):
        b += [f"> [!info] Plataforma: {pst['edges']} dependências lidas ({pst.get('source')}); aqui entram só as que "
              "apontam para componentes do escopo. O conjunto completo fica no snapshot (`_raw/<run_id>/platform_dependencies.json`).", ""]
    top_in = [n for n, c in dsum["fan_in"].most_common(25) if c]
    top_out = [n for n, c in dsum["fan_out"].most_common(25) if c]
    b += ["## Mais dependidos (maior impacto de mudança)", "",
          mdtable(["Componente", "Tipo", "Dependentes"], [[node_link(n), kinds.get(n[0], n[0]), dsum["fan_in"][n]] for n in top_in]),
          "## Que mais dependem de outros (mais frágeis)", "",
          mdtable(["Componente", "Tipo", "Dependências"], [[node_link(n), kinds.get(n[0], n[0]), dsum["fan_out"][n]] for n in top_out])]
    if dsum["external"]:
        b += ["## Dependências externas", "",
              mdtable(["Destino", "Tipo", "Usado por"],
                      [[g.nodes[n]["label"], kinds.get(n[0], n[0]), ", ".join(node_link(s) for s, _, _ in g.into(n)[:20])]
                       for n, _ in dsum["external"]])]
    if dsum["unused"]:
        unused = sorted(dsum["unused"])
        b += ["## Sem nenhum dependente encontrado", "",
              "Candidatos a limpeza — confirme chamadas de fora do Dataverse (integrações, apps externos, Power BI).", "",
              mdtable(["Componente", "Tipo"], [[node_link(n), kinds.get(n[0], n[0])] for n in unused[:LIST_MAX]])]
        if len(unused) > LIST_MAX:
            b += [f"… +{len(unused) - LIST_MAX} — lista completa em [[{v.folder}/Sem dependentes.csv|Sem dependentes.csv]].", ""]
    if dsum["missing"]:
        cat_names = ((d.get("catalog") or {}).get("names") or {})

        def nm(oid, lbl):
            n = (cat_names.get(oid) or [None, None])[1]
            return f"{n} ({lbl})" if n else f"{lbl} {oid}"

        by_sol = Counter(m["solution"] for m in dsum["missing"])
        b += ["## Dependências ausentes nas soluções", "",
              "Componentes exigidos que **não estão na solução** — a importação falha em ambiente que não os tenha. "
              f"Lista completa ({len(dsum['missing'])}) em [[{v.folder}/Dependências ausentes.csv|Dependências ausentes.csv]].", "",
              mdtable(["Solução", "Dependências ausentes"],
                      [[v.link("solution", s_, s_, in_table=True), n] for s_, n in by_sol.most_common(LIST_MAX)])]
        catalog.write_csv(v, "Dependências ausentes.csv",
                          [["Solução", "Componente que exige", "Tipo", "Id", "Componente exigido", "Tipo", "Id"]] +
                          [[m["solution"], nm(m["dependent_id"], m.get("dependent_type_label") or m.get("dependent_type")),
                            m.get("dependent_type_label") or m.get("dependent_type"), m["dependent_id"],
                            nm(m["required_id"], m.get("required_type_label") or m.get("required_type")),
                            m.get("required_type_label") or m.get("required_type"), m["required_id"]]
                           for m in dsum["missing"]])
    v.write(f"{v.folder}/08 Matriz de Dependências", {"tipo": "secao", "tags": v.tags("secao")}, "\n".join(b))

    if len(dsum["unused"]) > LIST_MAX:
        catalog.write_csv(v, "Sem dependentes.csv", [["Componente", "Tipo"]] + sorted(
            [[g.nodes[n]["label"], kinds.get(n[0], n[0])] for n in dsum["unused"]], key=lambda x: (str(x[1]), str(x[0]))))
    # Dependências.csv = índice textual do vault (grep/Copilot/Claude/`dvinv onde-usa` leem daqui): chave, GUID,
    # tipo, nome e nota das duas pontas; ordem estável para o diff do Git mostrar só mudança real
    cat_names = ((d.get("catalog") or {}).get("names")) or {}

    def info(n):
        nd = g.nodes[n]
        rel = v.paths.get(n)
        return [deps_mod.canonical_key(n, cat_names), deps_mod.guid_of(g, n) or "",
                nd.get("sub") if n[0] == "platform" else kinds.get(n[0], n[0]), nd["label"],
                (rel.split("#")[0] + ".md") if rel else "", "" if n[0] == "platform" else "sim"]

    rows = []
    for (s, t, r), e in g.edges.items():
        a, b_ = info(s), info(t)
        rows.append([a[0], a[1], a[2], a[3], r, e["source"], b_[0], b_[1], b_[2], b_[3], a[4], b_[4], a[5], b_[5]])
    rows.sort(key=lambda x: (x[0], x[4], x[6]))
    catalog.write_csv(v, "Dependências.csv",
                      [["Origem", "Origem id", "Origem tipo", "Origem nome", "Relação", "Fonte", "Destino", "Destino id",
                        "Destino tipo", "Destino nome", "Nota origem", "Nota destino", "Origem no escopo",
                        "Destino no escopo"]] + rows)


FIELD_MATRIX_MAX = 400
LOGIC_CATS = ("events", "js", "plugins", "businessrule", "workflow", "bpf", "action", "flow")


def _names(names, keep=2):
    if not names:
        return "—"
    if len(names) <= keep:
        return "; ".join(names)
    return f"{len(names)}: " + "; ".join(names[:keep]) + " …"


def field_usage_block(v, ln, rows, any_change):
    """Seções `Uso dos campos` e `Gatilhos` da nota da tabela."""
    if not rows and not any_change:
        return []
    labels = dict(campos.CATS)
    b = ["## Uso dos campos", "",
         "Quem usa cada coluna (nativa ou customizada). Formulários e views em quantidade; lógica (eventos, JS, "
         "plugins, business rules, workflows, BPFs, actions, flows) pelo nome. Nomes completos em "
         f"[[{v.folder}/Uso dos campos.csv|Uso dos campos.csv]]; uma coluna: `dvinv onde-usa {ln}.<coluna>`.", ""]
    if rows:
        shown = rows[:FIELD_MATRIX_MAX]
        b += [mdtable(["Coluna", labels["forms"], labels["views"]] + [labels[k] for k in LOGIC_CATS] + ["Outros"],
                      [[f"`{r['column']}`" + ("" if r["custom"] else " _(nativa)_"), len(r["forms"]) or "—",
                        len(r["views"]) or "—"] + [_names(r[k]) for k in LOGIC_CATS] +
                       [_names(r["pcf"] + r["formula"] + r["other"])] for r in shown])]
        if len(rows) > len(shown):
            b += [f"… +{len(rows) - len(shown)} colunas — lista completa no CSV.", ""]
    trig = [r for r in rows if any(r[k] for k, _ in campos.TRIGGERS)]
    if trig or any_change:
        b += ["## Gatilhos — o que roda quando o campo muda", "",
              "Exato para plugins (filtering attributes de steps de Update), workflows (campos do gatilho), cloud "
              "flows (colunas de filtro) e onChange de formulário; **provável** para business rules (campos citados "
              "na regra — a plataforma não expõe a lista de gatilho delas).", ""]
        if any_change:
            b += ["> [!warning] Rodam em qualquer alteração da tabela, seja qual for o campo",
                  *[f"> - {'Plugin' if k == 'plugins' else 'Cloud flow'}: " + ", ".join(sorted(names))
                    for k, names in sorted(any_change.items())], ""]
        if trig:
            b += [mdtable(["Coluna"] + [lbl for _, lbl in campos.TRIGGERS],
                          [[f"`{r['column']}`"] + [_names(r[k], keep=3) for k, _ in campos.TRIGGERS]
                           for r in trig[:FIELD_MATRIX_MAX]])]
    return b


def write_field_matrix_csv(v, d, fmatrix):
    """`Uso dos campos.csv`: uma linha por coluna usada, com os nomes por tipo de componente e os gatilhos."""
    head = (["Tabela", "Coluna", "Nome de exibição", "Customizada", "Componentes de lógica"]
            + [lbl for _, lbl in campos.CATS] + [lbl for _, lbl in campos.TRIGGERS])
    rows = [[ln, r["column"], r.get("display"), "sim" if r["custom"] else "não", campos.logic_count(r)]
            + [" | ".join(r[k]) for k, _ in campos.CATS] + [" | ".join(r[k]) for k, _ in campos.TRIGGERS]
            for ln in sorted(fmatrix) for r in fmatrix[ln]]
    catalog.write_csv(v, "Uso dos campos.csv", [head] + rows)


FIELD_TOP = 50
TRIGGER_TOP = 100


def write_field_matrix(v, fmatrix, any_change):
    """`12 Matriz de Uso dos Campos`: panorama do ambiente — campos com mais lógica, o que roda em qualquer alteração
    e gatilhos por campo. O detalhe fica na nota de cada tabela e em `Uso dos campos.csv`."""
    labels = dict(campos.CATS)
    rows = [(ln, r) for ln in sorted(fmatrix) for r in fmatrix[ln]]
    tables = sorted(set(fmatrix) | set(any_change))

    def tlink(ln):
        return v.link("table", ln, ln, in_table=True) if ("table", ln) in v.paths else ln

    b = ["# Matriz de uso dos campos", "",
         "Quem usa cada campo — formulários, eventos onChange, JavaScript, plugins, business rules, workflows, BPFs, "
         "actions, cloud flows, views — e o que roda quando ele muda. Responde \"se eu alterar o campo X, o que "
         "quebra ou dispara?\" antes de uma evolutiva, e mostra onde a mudança é mais arriscada.", "",
         "> [!info] Como ler",
         "> **Lógica** = eventos onChange, JS, plugins, business rules, workflows, BPFs, actions e flows que usam o campo "
         "(formulários e views ficam de fora da conta). Gatilhos **exatos**: filtering attributes de steps de Update, "
         "campos do gatilho de workflows, colunas de filtro de flows e onChange de formulário; business rules são "
         "**prováveis** (campos citados na regra). Ausência de uso aqui não prova que o campo está livre: integrações "
         "externas, relatórios e código fora do `repos:` não aparecem.", "",
         f"Detalhe por tabela: seção *Uso dos campos* de cada nota. Tudo, um campo por linha: "
         f"[[{v.folder}/Uso dos campos.csv|Uso dos campos.csv]]. Um campo: `dvinv onde-usa <tabela>.<coluna>`.", ""]
    if not tables:
        b += ["_Nenhum uso de campo encontrado nesta extração._", ""]
    else:
        b += ["## Resumo por tabela", "",
              mdtable(["Tabela", "Colunas usadas", "Com lógica", "Com gatilho", "Plugins em qualquer alteração",
                       "Flows em qualquer alteração"],
                      [[tlink(ln), len(fmatrix.get(ln, [])),
                        sum(1 for r in fmatrix.get(ln, []) if campos.logic_count(r)),
                        sum(1 for r in fmatrix.get(ln, []) if any(r[k] for k, _ in campos.TRIGGERS)),
                        len((any_change.get(ln) or {}).get("plugins") or ()) or "—",
                        len((any_change.get(ln) or {}).get("flows") or ()) or "—"] for ln in tables])]
        hot = sorted((x for x in rows if campos.logic_count(x[1])), key=lambda x: (-campos.logic_count(x[1]), x[0],
                                                                                   x[1]["column"]))[:FIELD_TOP]
        if hot:
            n_logic = sum(1 for _, r in rows if campos.logic_count(r))
            b += [f"## Campos com mais lógica ({len(hot)} de {n_logic})", "",
                  "Mudar tipo, tamanho, obrigatoriedade ou remover estes campos exige revisar e testar tudo o que aparece "
                  "na linha.", "",
                  mdtable(["Tabela", "Coluna", "Lógica"] + [labels[k] for k in LOGIC_CATS],
                          [[tlink(ln), f"`{r['column']}`" + ("" if r["custom"] else " _(nativa)_"), campos.logic_count(r)]
                           + [_names(r[k]) for k in LOGIC_CATS] for ln, r in hot])]
        if any_change:
            b += ["## Rodam em qualquer alteração da tabela", "",
                  "Step de Update sem filtering attributes ou flow sem coluna de filtro: dispara seja qual for o campo "
                  "alterado — custo em toda gravação e efeito colateral em qualquer evolutiva da tabela (PLG-01).", "",
                  mdtable(["Tabela", "Plugins", "Cloud flows"],
                          [[tlink(ln), "; ".join(sorted((any_change[ln] or {}).get("plugins") or ())) or "—",
                            "; ".join(sorted((any_change[ln] or {}).get("flows") or ())) or "—"]
                           for ln in sorted(any_change)])]
        trig = sorted((x for x in rows if any(x[1][k] for k, _ in campos.TRIGGERS)),
                      key=lambda x: (-sum(len(x[1][k]) for k, _ in campos.TRIGGERS), x[0], x[1]["column"]))
        if trig:
            b += [f"## Gatilhos por campo ({min(len(trig), TRIGGER_TOP)} de {len(trig)})", "",
                  mdtable(["Tabela", "Coluna"] + [lbl for _, lbl in campos.TRIGGERS],
                          [[tlink(ln), f"`{r['column']}`"] + [_names(r[k], keep=3) for k, _ in campos.TRIGGERS]
                           for ln, r in trig[:TRIGGER_TOP]])]
    v.write(f"{v.folder}/12 Matriz de Uso dos Campos",
            {"tipo": "secao", "colunas_usadas": len(rows),
             "colunas_com_logica": sum(1 for _, r in rows if campos.logic_count(r)), "tags": v.tags("secao")},
            "\n".join(b))


CLEANUP_LIST_MAX = 300


def write_cleanup(v, d, g, fnd, node_link):
    """`11 Candidatos a Limpeza` + `Candidatos a limpeza.csv`: o que parece resíduo e o impacto de excluir cada um."""
    rows = residuos.candidates(d, g, fnd)
    env = v.env
    when = ((d.get("manifest") or {}).get("extracted_at") or "")[:10]
    status_count = Counter((r["kind"], r["status"]) for r in rows)
    kinds = sorted({r["kind"] for r in rows}, key=lambda k: deps_mod.KIND_LABEL.get(k, k))

    def comp(r):
        n = r["node"]
        if n in g.nodes:
            return node_link(n)
        if n in v.paths:
            return v.link(n[0], n[1], r["label"])
        table = r["label"].split(" / ")[0] if n[0] == "form" else r["label"].split(".")[0] if n[0] == "column" else None
        if table and ("table", table) in v.paths:  # coluna/formulário sem nó no grafo: aponta a nota da tabela
            return f"{r['label']} ({v.link('table', table, table)})"
        return r["label"]

    b = ["# Candidatos a limpeza", "",
         "> [!warning] Candidatos para investigação, não autorização de exclusão",
         f"> Lista montada a partir do inventário de **{env}** extraído em **{when or '—'}**. O Dataverse não mostra "
         "integrações externas, relatórios (Power BI), portais, uso de formulário por URL nem código fora do `repos:`. "
         "Cada item precisa do aceite do owner antes de qualquer remoção, e a remoção segue a esteira de deploy — "
         "nunca direto em produção.", "",
         "## Como ler", "",
         "- **Onda**: ordem sugerida. Um item da onda 2 é usado por um candidato da onda 1 — remova (ou ajuste) a "
         "onda 1 primeiro.",
         "- **Situação**: *bloqueado pela plataforma* (o Dataverse registra dependentes e impede a exclusão até "
         "tratá-los) · *ajustar formulário/view antes* (coluna ainda exibida) · *revisar referências* (o kit achou uso "
         "em JS, flow, plugin, processo ou app) · *sem dependente no inventário*.",
         "- Dependente que também é candidato não bloqueia: sai numa onda anterior.", "",
         f"Lista completa em [[{v.folder}/Candidatos a limpeza.csv|Candidatos a limpeza.csv]] (abre no Excel).", ""]
    if not rows:
        b += ["_Nenhum candidato encontrado com os dados desta extração._", ""]
    else:
        b += [f"## Resumo ({len(rows)} candidatos)", "",
              mdtable(["Tipo"] + list(residuos.STATUS) + ["Total"],
                      [[deps_mod.KIND_LABEL.get(k, k)] + [status_count.get((k, st), 0) for st in residuos.STATUS]
                       + [sum(status_count.get((k, st), 0) for st in residuos.STATUS)] for k in kinds]),
              f"## Candidatos ({min(len(rows), CLEANUP_LIST_MAX)} de {len(rows)})", "",
              mdtable(["Onda", "Situação", "Componente", "Regra", "Motivo", "Evidência", "Bloqueios (plataforma)",
                       "Referências (inventário)"],
                      [[r["wave"], r["status"], comp(r), v.link("finding", r["rule"], r["rule"], in_table=True),
                        r["reason"], r["evidence"], "; ".join(r["hard"][:4]) + (" …" if len(r["hard"]) > 4 else ""),
                        "; ".join(r["soft"][:4]) + (" …" if len(r["soft"]) > 4 else "")]
                       for r in rows[:CLEANUP_LIST_MAX]])]
    b += ["## Não verificado", "",
          "- Integrações externas, ETL, relatórios e portais que leem ou gravam os componentes.",
          "- Uso real de formulários (quem abre): exige telemetria, como o Application Insights.",
          f"- Outros ambientes: esta lista é de **{env}**. O que existe só em produção, ou foi alterado lá, precisa da "
          "extração daquele ambiente (`dvinv diff` compara os dois).",
          "- Contagem de registros: instantâneo de até 24 h; tabela criada há pouco pode aparecer como vazia.",
          "- Código-fonte fora dos repositórios informados em `repos:`.", ""]
    v.write(f"{v.folder}/11 Candidatos a Limpeza", {"tipo": "secao", "candidatos": len(rows),
                                                    "tags": v.tags("secao")}, "\n".join(b))
    catalog.write_csv(v, "Candidatos a limpeza.csv",
                      [["Onda", "Situação", "Tipo", "Chave", "Nome", "Regra", "Motivo", "Evidência",
                        "Bloqueios (plataforma)", "Referências (inventário)"]] +
                      [[r["wave"], r["status"], deps_mod.KIND_LABEL.get(r["kind"], r["kind"]), r["key"], r["label"],
                        r["rule"], r["reason"], r["evidence"], " | ".join(r["hard"]), " | ".join(r["soft"])]
                       for r in rows])


def write_solution_history(v, d):
    hist = d.get("solution_history") or []
    if not hist:
        return
    fails = [h for h in hist if h.get("ok") is False]
    last = {}
    for h in hist:  # já vem do mais recente para o mais antigo
        last.setdefault(h["solution"], h)
    b = ["# Histórico de soluções", "",
         f"**{len(hist)}** operações (importação, exportação, upgrade…) desde {(hist[-1].get('start') or '')[:10]} · "
         f"**{len(fails)}** com falha · lista completa em [[{v.folder}/Histórico de soluções.csv|Histórico de soluções.csv]].", "",
         "## Falhas mais recentes", "",
         mdtable(["Quando", "Solução", "Operação", "Versão", "Erro"],
                 [[(h.get("start") or "")[:16].replace("T", " "), h["solution"], h.get("operation"), h.get("version"),
                   (h.get("error") or h.get("error_code") or "")[:240]] for h in fails[:100]]),
         "## Última operação por solução", "",
         mdtable(["Solução", "Quando", "Operação", "Versão", "Gerenciada", "Resultado"],
                 [[(v.link("solution", s_, s_, in_table=True) if ("solution", s_) in v.paths else s_),
                   (h.get("start") or "")[:10], h.get("operation"), h.get("version"), h.get("managed"),
                   "ok" if h.get("ok") else "falha"] for s_, h in list(last.items())[:300]])]
    v.write(f"{v.folder}/10 Histórico de Soluções", {"tipo": "secao", "operacoes": len(hist), "falhas": len(fails),
                                                     "tags": v.tags("secao")}, "\n".join(b))
    catalog.write_csv(v, "Histórico de soluções.csv", [["Início", "Solução", "Operação", "Suboperação", "Resultado",
                                                        "Versão", "Gerenciada", "Patch", "Publisher", "Segundos", "Código", "Erro"]] +
                      [[h.get("start"), h["solution"], h.get("operation"), h.get("suboperation"),
                        "ok" if h.get("ok") else "falha", h.get("version"), h.get("managed"), h.get("patch"),
                        h.get("publisher"), h.get("seconds"), h.get("error_code"), h.get("error")] for h in hist])


def write_environment(v, d, manifest):
    env = d.get("environment") or {}
    b = ["# Ambiente", "", mdtable(["Item", "Valor"], [
        ["URL", env.get("url")], ["Organização", env.get("org_name")], ["Versão", env.get("version")],
        ["Organization ID", env.get("organizationid")], ["Criada em", env.get("created")],
        ["Auditoria", env.get("audit_enabled")], ["Plugin trace", env.get("plugin_trace_setting")],
        ["Extraído em (UTC)", manifest.get("extracted_at")], ["Chamadas à API", len(manifest.get("queries") or [])],
        ["Snapshot (run_id)", manifest.get("run_id") or "formato antigo"],
        ["Completou o snapshot", manifest.get("base") or "—"],
        ["Perfil de coleta", manifest.get("profile") or "padrao"],
        ["Bloqueado pelo perfil", ", ".join(manifest.get("profile_blocked") or []) or "—"],
    ])]
    sc = manifest.get("scope") or {}
    b += ["## Critério de escopo", "", f"- Prefixos: {', '.join(f'`{p}`' for p in sc.get('prefixes') or []) or '—'}",
          f"- Palavras-chave: {', '.join(f'`{k}`' for k in sc.get('keywords') or []) or '—'}",
          f"- Soluções: {', '.join(sc.get('solutions') or []) or '—'}",
          f"- Inclui não gerenciados: {sc.get('include_unmanaged')} · tudo: {sc.get('all')}",
          f"- Prefixos excluídos: {', '.join(f'`{p}`' for p in sc.get('exclude_prefixes') or []) or '—'}",
          f"- Prefixos da Microsoft descartados: {'sim' if sc.get('exclude_microsoft', True) else 'não'}"
          + (" — " + ", ".join(f"`{p}` {n}" for p, n in (manifest.get("scope_excluded_microsoft") or {}).items())
             if manifest.get("scope_excluded_microsoft") else ""), ""]
    bd = manifest.get("scope_breakdown") or {}
    if bd:
        b += ["### De onde veio o escopo (componentes únicos por motivo × prefixo)", "",
              "> [!tip] Volume inesperado? Prefixos de terceiros que entraram por *unmanaged* podem ir para "
              "`scope.exclude_prefixes`; prefixos do próprio cliente, para `scope.prefixes`.", "",
              mdtable(["Motivo", "Prefixo", "Componentes"],
                      [[r, p, n] for r, ps in sorted(bd.items()) for p, n in list(ps.items())[:15]])]
    b += ["",
          "## Componentes: organização × escopo × processados", "",
          "Por etapa da coleta: quantos existem no ambiente, quantos entraram no escopo e o que foi processado "
          "(conteúdo baixado/reaproveitado/ignorado, falhas, tempo).", "",
          mdtable(["Etapa", "Na org", "No escopo", "Processado / detalhe", "Status", "Tempo"], resumo.rows(manifest))]
    cols = manifest.get("collectors") or {}
    if cols:
        b += ["## Status dos coletores", "",
              "> [!info] Cada extração é um snapshot imutável; coletor que falhou ou não rodou não tem dado neste vault "
              "(nunca é preenchido com arquivo de outra extração).", "",
              mdtable(["Coletor", "Status", "Origem", "Lacunas", "Arquivos", "Motivo"],
                      [[k, st.get("status"), st.get("origem") or "—", st.get("lacunas"),
                        ", ".join(a for a in st.get("arquivos") or [] if "/" not in a), st.get("motivo")]
                       for k, st in cols.items()])]
    elif manifest.get("legacy"):
        b += ["> [!warning] Snapshot no formato antigo (sem manifesto de integridade) — rode `extract` de novo.", ""]
    gaps = manifest.get("gaps") or []
    b += ["## Lacunas declaradas", "",
          "> [!warning] O que **não** foi coletado nesta extração\n> Números deste vault só valem para o que foi efetivamente lido.", "",
          mdtable(["Coletor", "O quê", "Motivo"], [[g["collector"], g["what"], (g["error"].splitlines() or [""])[0][:200]] for g in gaps])]
    b += ["## Soluções", "", mdtable(["Solução", "Versão", "Gerenciada", "Publisher", "Componentes"],
          [[v.link("solution", s["uniquename"], s["uniquename"], in_table=True), s.get("version"), s.get("managed"),
            s.get("publisher"), s.get("component_total")] for s in d.get("solutions") or []])]
    b += ["## Coleta", "", "**Extras:** " + (", ".join(f"`{x}`" for x in manifest.get("extras") or []) or
                                     "nenhum (só o núcleo: tudo o que é customizado e o uso)"), "",
          mdtable(["Opção (deep)", "Ligada"], list((manifest.get("deep") or {}).items())),
          "## Tempo por coletor (s)", "", mdtable(["Coletor", "s"], list((manifest.get("timings_s") or {}).items()))]
    v.write(f"{v.folder}/01 Ambiente", {"tipo": "secao", "url": env.get("url"), "versao": env.get("version"),
                                        "snapshot": manifest.get("run_id"), "perfil": manifest.get("profile") or "padrao",
                                        "extras": manifest.get("extras") or [], "tags": v.tags("secao")}, "\n".join(b))


CHANGES_LIST_MAX = 200  # itens por lista na nota de mudanças; a lista completa vai para Mudanças.csv


def write_changes(v, chg):
    """`Mudanças desde a extração anterior` + `Mudanças.csv`: notas de versão do ambiente (ver changes.py)."""
    title = f"{v.folder}/Mudanças desde a extração anterior"
    csv_rows = [["Tipo", "Situação", "Componente", "O que mudou"]]
    head = ["# Mudanças desde a extração anterior", ""]
    if not chg or chg.get("error"):
        why = (f"A extração anterior (`{chg['prev']}`) não pôde ser lida: {chg['error']}" if chg else
               "Primeira extração deste ambiente nesta pasta: ainda não há com o que comparar. A partir da próxima, "
               "esta nota mostra o que entrou, saiu e mudou no ambiente e quais achados foram resolvidos.")
        v.write(title, {"tipo": "mudancas", "tags": v.tags("mudancas")}, "\n".join(head + [why, ""]))
        catalog.write_csv(v, "Mudanças.csv", csv_rows)
        return
    new, rem, alt, res, nov = changes_mod.summary(chg)
    lk = lambda name, link: v.link(link[0], link[1], name, in_table=True) if link else name  # noqa: E731
    b = head + [f"Snapshot `{chg['prev']}` ({chg['prev_at']}) → `{chg['cur']}` ({chg['cur_at']}): **{new}** novo(s), "
                f"**{rem}** removido(s), **{alt}** alterado(s) · achados: **{res}** resolvido(s), **{nov}** novo(s).", ""]
    if chg["scope_changed"]:
        b += ["> [!warning] O critério de escopo mudou entre as duas extrações: parte das diferenças pode vir da "
              "configuração, não do ambiente.", ""]
    skipped = [r["label"] for r in chg["types"] if not r["comparable"]]
    if skipped:
        b += [f"> [!info] Não comparados (etapa falhou, ficou desligada ou rodou com opções diferentes numa das extrações): {', '.join(skipped)}.", ""]
    moved = [r for r in chg["types"] if r["comparable"] and (r["new"] or r["removed"] or r["changed"])]
    b += ["## Resumo por tipo", "", mdtable(["Tipo", "Novos", "Removidos", "Alterados"],
          [[r["label"], len(r["new"]), len(r["removed"]), len(r["changed"])] for r in moved])]
    fch = [f for f in chg["findings"] if f["status"] != "igual"]
    b += ["## Achados", "", mdtable(["Achado", "Severidade", "Antes", "Agora", "Situação"],
          [[f"{f['id']} {f['title']}" if f["status"] == "resolvido" else
            v.link("finding", f["id"], f"{f['id']} {f['title']}", in_table=True),
            f["severity"], f["before"], f["after"], f["status"]] for f in fch])]
    for label, field in (("Ocorrências corrigidas", "fixed"), ("Ocorrências novas", "added")):
        occ = [(f["id"], e) for f in chg["findings"] for e in f[field]]
        if occ:
            b += [f"### {label} ({len(occ)})", "", mdtable(["Achado", "Ocorrência"], [list(o) for o in occ[:CHANGES_LIST_MAX]])]
            if len(occ) > CHANGES_LIST_MAX:
                b += [f"… e mais {len(occ) - CHANGES_LIST_MAX}: ver `Mudanças.csv`.", ""]
        csv_rows += [["Achado", "corrigido" if field == "fixed" else "nova ocorrência", fid, e] for fid, e in occ]
    for r in moved:
        b += [f"## {r['label']}", ""]
        for label, items in (("Novos", r["new"]), ("Removidos", r["removed"])):
            if items:
                b += [f"**{label} ({len(items)}):** " + ", ".join(lk(n, link) for n, link in items[:CHANGES_LIST_MAX])
                      + (f" … e mais {len(items) - CHANGES_LIST_MAX} (ver `Mudanças.csv`)" if len(items) > CHANGES_LIST_MAX
                         else ""), ""]
        if r["changed"]:
            b += [f"**Alterados ({len(r['changed'])}):**", "", mdtable(["Componente", "O que mudou"],
                  [[lk(n, link), ", ".join(what)] for n, link, what in r["changed"][:CHANGES_LIST_MAX]])]
            if len(r["changed"]) > CHANGES_LIST_MAX:
                b += [f"… e mais {len(r['changed']) - CHANGES_LIST_MAX}: ver `Mudanças.csv`.", ""]
        csv_rows += [[r["label"], "novo", n, ""] for n, _l in r["new"]]
        csv_rows += [[r["label"], "removido", n, ""] for n, _l in r["removed"]]
        csv_rows += [[r["label"], "alterado", n, ", ".join(what)] for n, _l, what in r["changed"]]
    if not moved and not fch:
        b += ["Nenhuma mudança nos componentes do escopo nem nos achados.", ""]
    v.write(title, {"tipo": "mudancas", "snapshot": chg["cur"], "snapshot_anterior": chg["prev"], "novos": new,
                    "removidos": rem, "alterados": alt, "achados_resolvidos": res, "achados_novos": nov,
                    "tags": v.tags("mudancas")}, "\n".join(b))
    catalog.write_csv(v, "Mudanças.csv", csv_rows)


def write_index(v, d, manifest, fnd, has_catalog=False, chg=None):
    st = manifest.get("stats") or {}
    procs = d.get("processes") or []
    plugins = d.get("plugins") or {}
    apps = d.get("apps") or {}
    by_cat = Counter(p["category"] for p in procs)
    sev = Counter(f["severity"] for f in fnd)
    rows = [
        ["Tabelas", f"{st.get('tables', {}).get('scope', 0)} ({st.get('tables', {}).get('custom', 0)} custom + "
                    f"{st.get('tables', {}).get('native_customized', 0)} nativas customizadas)", st.get("tables", {}).get("org_total")],
        ["Colunas customizadas", st.get("tables", {}).get("columns_custom"), None],
        ["Relacionamentos", st.get("relationships", {}).get("scope"), None],
        ["Formulários / views", f"{st.get('forms', {}).get('scope')} / {st.get('views', {}).get('scope')}", None],
        ["Web resources", st.get("webresources", {}).get("scope"), st.get("webresources", {}).get("org_total")],
        ["Plugin assemblies / classes / steps", f"{len(plugins.get('assemblies') or [])} / "
         f"{sum(len(a['types']) for a in plugins.get('assemblies') or [])} / {len(plugins.get('steps') or [])}",
         st.get("plugins", {}).get("org_assemblies")],
        ["Custom APIs", st.get("customapis", {}).get("scope"), st.get("customapis", {}).get("org_total")],
    ] + [[f"Processos — {k}", n, None] for k, n in sorted(by_cat.items())] + [
        ["Apps model-driven / canvas / agentes", f"{len(apps.get('modeldriven') or [])} / {len(apps.get('canvas') or [])} / "
         f"{len(apps.get('bots') or [])}", None],
        ["Papéis no escopo (BU raiz)", st.get("security", {}).get("roles_scope"), st.get("security", {}).get("roles_root_bu")],
        ["Usuários ativos", st.get("security", {}).get("users_active"), None],
        ["Business units / equipes", f"{st.get('security', {}).get('business_units')} / {st.get('security', {}).get('teams')}",
         None],
        ["Componentes PCF", st.get("pcf", {}).get("scope"), st.get("pcf", {}).get("org_total")],
        ["Site maps (subáreas)", f"{st.get('sitemaps', {}).get('scope', '—')} ({st.get('sitemaps', {}).get('subareas', '—')})",
         st.get("sitemaps", {}).get("org_total")],
        ["SLAs (itens)", f"{st.get('slas', {}).get('scope', '—')} ({st.get('slas', {}).get('items', '—')})",
         st.get("slas", {}).get("org_total")],
        ["Tabelas virtuais / provedores de dados", f"{st.get('virtual_tables', {}).get('scope', '—')} / "
         f"{st.get('virtual_tables', {}).get('providers_used', '—')}", st.get("virtual_tables", {}).get("org_total")],
        ["Tabelas elásticas", None, st.get("virtual_tables", {}).get("elastic_org_total")],
        ["Catálogo (todos os componentes, todas as soluções)", f"{st.get('catalog', {}).get('types', '—')} tipos",
         st.get("catalog", {}).get("components")],
        ["Dependências registradas pela plataforma", None, st.get("platform_dependencies", {}).get("edges")],
    ]
    env = d.get("environment") or {}
    b = [f"# Inventário Dataverse — {v.env}", "",
         f"`{env.get('url')}` · versão {env.get('version')} · extraído em {manifest.get('extracted_at')} · "
         f"{len(manifest.get('gaps') or [])} lacuna(s) declarada(s) em {v.link_path('01 Ambiente', 'Ambiente')}", "",
         "> [!summary] Achados automáticos",
         "> " + " · ".join(f"**{n}** {s}" for s, n in sorted(sev.items(), key=lambda x: findings_mod.SEV_ORDER.get(x[0], 9))) +
         f" — ver {v.link_path('Bases/Achados.base', 'Achados')}" if fnd else "> Nenhum achado automático.", "",
         *(["> [!info] Desde a extração anterior ({}): {} novo(s) · {} removido(s) · {} alterado(s) · achados: {} "
            "resolvido(s), {} novo(s) — ver {}".format(
                (chg.get("prev_at") or chg["prev"])[:10], *changes_mod.summary(chg),
                v.link_path("Mudanças desde a extração anterior", "Mudanças desde a extração anterior")), ""]
           if chg and not chg.get("error") else []),
         "## Números", "", mdtable(["Componente", "No escopo", "Total na org"], rows),
         "## Navegação", "",
         f"- {v.link_path('01 Ambiente', 'Ambiente, escopo e lacunas')}",
         f"- {v.link_path('03 Integrações', 'Integrações')}",
         f"- {v.link_path('04 Segurança', 'Segurança')}",
         *([f"- {v.link_path('10 Histórico de Soluções', 'Histórico de soluções (importações e falhas)')}"]
           if d.get("solution_history") else []),
         *([f"- {v.link_path('05 Uso de Campos', 'Uso de campos')}"] if d.get("field_usage") else []),
         *([f"- {v.link_path('06 Armazenamento e Auditoria', 'Armazenamento e auditoria')}"] if d.get("storage") else []),
         *([f"- {v.link_path('07 Repositórios', 'Repositórios × ambiente')}"] if d.get("repos") else []),
         f"- {v.link_path('08 Matriz de Dependências', 'Matriz de dependências')}",
         f"- {v.link_path('11 Candidatos a Limpeza', 'Candidatos a limpeza (resíduos e impacto da exclusão)')}",
         f"- {v.link_path('12 Matriz de Uso dos Campos', 'Matriz de uso dos campos (quem usa e o que dispara)')}",
         *([f"- {v.link_path('09 Catálogo de Componentes', 'Catálogo de componentes (100%)')}"] if has_catalog else []),
         f"- {v.link_path('Mapa do Ambiente.canvas', 'Mapa do ambiente (canvas)')}",
         *([f"- {v.link_path('Mapas/00 Mapas por tipo', 'Mapas por tipo de componente (grafo por tipo)')}"]
           if getattr(v, "maps", None) else []),
         f"- {v.link_path('AGENT-INDEX', 'Índice para agentes de IA')}", "",
         "## Achados", "", f"![[{v.folder}/Bases/Achados.base]]", "",
         "## Tabelas", "", f"![[{v.folder}/Bases/Tabelas.base]]", "",
         "## Automação", "", f"![[{v.folder}/Bases/Automações.base]]", ""]
    v.write(f"{v.folder}/00 Índice", {"tipo": "indice", "snapshot": manifest.get("run_id"),
                                      "extraido_em": manifest.get("extracted_at"), "tags": v.tags("indice")}, "\n".join(b))
