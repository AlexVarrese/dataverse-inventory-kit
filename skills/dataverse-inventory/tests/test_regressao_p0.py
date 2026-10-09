"""Regressões dos defeitos P0/P1 (0.5.0). Offline, só stdlib + o fixture fictício de test_pipeline.

Uso: python3 tests/test_regressao_p0.py   (ou python3 -m unittest tests/test_regressao_p0.py)
"""

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

import yaml  # noqa: E402

import test_pipeline as tp  # noqa: E402
from dvinv import auth as auth_mod  # noqa: E402
from dvinv import config as config_mod  # noqa: E402
from dvinv import findings as findings_mod  # noqa: E402
from dvinv import secrets  # noqa: E402
from dvinv import snapshot as snapshot_mod  # noqa: E402
from dvinv.client import Client, DataverseError, UnsafeUrlError  # noqa: E402
from dvinv.collectors import Context, run  # noqa: E402
from dvinv.collectors import usage as usage_mod  # noqa: E402
from dvinv.render import obsidian  # noqa: E402
from dvinv.scope import Scope  # noqa: E402

G = tp.G

# ------------------------------------------------------------------------------------------------
# Vetores de segredo (valores fictícios). Cada valor carrega o marcador FAKE para a busca final.
SECRET_VECTORS = {
    "query api_key": "https://api.contoso.example/v1/orders?api_key=FAKEapikey0001&page=2",
    "query access_token": "https://app.contoso.example/cb?state=x&access_token=FAKEaccess0002",
    "query client_secret": "https://login.contoso.example/t?client_id=abc&client_secret=FAKEclisec0003",
    "header x-functions-key (linha)": "x-functions-key: FAKEfunckey0004",
    "header x-functions-key (JSON)": '{"x-functions-key": "FAKEfunckey0005"}',
    "header Ocp-Apim (JS)": 'xhr.setRequestHeader("Ocp-Apim-Subscription-Key", "FAKEapim0006");',
    "header Ocp-Apim (linha)": "Ocp-Apim-Subscription-Key: FAKEapim0007",
    "userinfo em URL": "https://svcuser:FAKEpass0008@erp.contoso.example/api",
    "var token": "var token = 'FAKEtoken0009';",
    "YAML password": "password: FAKEyaml0010",
    "JSON apiKey": '{"apiKey": "FAKEjson0011"}',
    "query pwd": "https://x.contoso.example/?user=a&pwd=FAKEpwd0012",
    "query code curto": "https://fn.contoso.example/api/x?code=FAKEc13",
    "Authorization Basic": 'headers: {"Authorization": "Basic RkFLRWJhc2ljMDAxNA=="}',
    "Authorization Bearer curto": '"Authorization": "Bearer FAKEbear15"',
    ".env": "CLIENT_SECRET=FAKEenv0016",
    "curl -H x-api-key": 'curl -H "x-api-key: FAKEcurl0017" https://x.contoso.example',
    "query refresh_token": "https://x.contoso.example/?refresh_token=FAKErefresh18",
    "query subscription-key": "https://x.contoso.example/?subscription-key=FAKEsub0019",
    "template literal accessToken": "const accessToken = `FAKEtpl0020`;",
    "query sig": "https://x.blob.core.windows.net/c?sv=2020&sig=FAKEsig0021",
}
SECRET_RE = re.compile(r"FAKE[A-Za-z]*\d+|RkFLRWJhc2lj")
NOT_SECRETS = [
    "var t = tokenize(input);", "var tokenizer = new Tokenizer();", "if (token === undefined) {}",
    "$filter=statuscode eq 1", "fetch(url, {credentials: 'include'})", '"Authorization": "Bearer " + token',
    "Basic authentication is used", "Bearer token required", "var url = base + '?code=' + code;",
    "token = getToken();", '"secret_hits": []', "xhr.setRequestHeader('Authorization: Bearer ' + t);",
]


def all_text_under(root):
    """Texto de todos os arquivos (xlsx/zip descompactados) — para procurar vazamento."""
    out = []
    for p in Path(root).rglob("*"):
        if not p.is_file():
            continue
        if p.suffix in (".xlsx", ".zip"):
            with zipfile.ZipFile(p) as z:
                out += [(f"{p}!{n}", z.read(n).decode("utf-8", "replace")) for n in z.namelist()]
        elif p.suffix != ".dll":
            out.append((str(p), p.read_text(encoding="utf-8", errors="replace")))
    return out


@contextlib.contextmanager
def patched_route(fn):
    """fn(path) → resultado, ou None para cair no fixture padrão."""
    orig = tp.route

    def wrapper(path):
        res = fn(urllib_unquote(path))
        return orig(path) if res is None else res
    tp.route = wrapper
    try:
        yield
    finally:
        tp.route = orig


def urllib_unquote(p):
    import urllib.parse
    return urllib.parse.unquote(p)


class Env:
    """Diretório temporário com inventory.yaml + repositório fictício (igual ao test_pipeline)."""

    def __init__(self, repos=True, extra=None):
        self.tmp = Path(tempfile.mkdtemp(prefix="dvinv-reg-"))
        repo = self.tmp / "repo"
        (repo / "JavaScript").mkdir(parents=True)
        (repo / "Plugins").mkdir()
        (repo / "JavaScript" / "unused.js").write_text("function nobodyCallsMe(){}\n")
        (repo / "Plugins" / "AccountPre.cs").write_text(
            "namespace Contoso.Plugins { public class AccountPre : IPlugin { public void Execute(IServiceProvider s) {"
            " var x = entity[\"contoso_name\"]; } } }")
        data = {
            "environment": {"name": "CONTOSO-PRD", "url": "https://contoso.crm.dynamics.com"},
            "scope": {"prefixes": ["contoso_"], "solutions": ["ContosoCore"]},
            "output": {"raw_dir": "out/_raw", "vault_root": "vault", "vault_folder": "Dataverse/CONTOSO-PRD"},
        }
        if repos:
            data["repos"] = [{"path": "repo", "kind": "any", "name": "contoso-crm"}]
        data.update(extra or {})
        (self.tmp / "inventory.yaml").write_text(yaml.safe_dump(data))

    def cfg(self, overrides=None):
        cfg = config_mod.load(self.tmp / "inventory.yaml", dict({"deep_all": True}, **(overrides or {})))
        cfg.deep["plugin_binaries"] = False
        return cfg

    def extract(self, cfg, only=None):
        ctx = Context(cfg, tp.FakeClient(cfg, credential=None), Scope(cfg))
        ctx.client._bearer = lambda: "fake"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            manifest = run(ctx, only)
        return ctx, manifest, buf.getvalue()

    def render(self, cfg, snapshot=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            v = obsidian.render(cfg, snapshot)
        return v, buf.getvalue()

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def fu_fields(snap):
    return {t["table"]: {f["logical"]: f for f in t["fields"]}
            for t in json.loads((Path(snap) / "field_usage.json").read_text())}


# ================================================================================================
class TestP001SnapshotIsolado(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_coletor_que_falha_nao_reaproveita_json_do_run_anterior(self):
        cfg = self.env.cfg()
        ctx1, m1, _ = self.env.extract(cfg)
        snap1 = ctx1.snapshot_dir
        self.assertTrue((snap1 / "views.json").exists())
        v1, _ = self.env.render(cfg)
        note = cfg.vault_dir / "Tabelas" / "contoso_project.md"
        self.assertIn("Projetos ativos", note.read_text())

        def fail_views(p):
            if p.startswith("savedqueries"):
                raise DataverseError(500, p, '{"error":{"message":"falha simulada em views"}}')
        with patched_route(fail_views):
            ctx2, m2, out2 = self.env.extract(cfg)
        snap2 = ctx2.snapshot_dir
        self.assertNotEqual(snap1, snap2)
        self.assertTrue(snap1.exists() and snap2.exists(), "snapshots do mesmo dia devem coexistir")
        self.assertEqual(m2["collectors"]["views"]["status"], "falhou")
        self.assertEqual(m2["collectors"]["views"]["arquivos"], [])
        self.assertNotIn("views.json", m2["files"])
        self.assertFalse((snap2 / "views.json").exists())
        self.assertTrue(any(g["collector"] == "views" and g["what"] == "coletor inteiro" for g in m2["gaps"]))

        d2 = snapshot_mod.load(snapshot_mod.resolve(cfg.raw_dir))
        self.assertEqual(d2["manifest"]["run_id"], m2["run_id"], "render deve usar o último snapshot íntegro")
        self.assertNotIn("views", d2, "dado de views do 1º run vazou para o 2º")

        v2, _ = self.env.render(cfg)
        self.assertEqual(v2.snapshot, snap2)
        self.assertNotIn("Projetos ativos", note.read_text(), "nota renderizada com view do run anterior")
        amb = (cfg.vault_dir / "01 Ambiente.md").read_text()
        self.assertRegex(amb, r"\| views \| falhou \|")
        self.assertIn("falha simulada em views", amb)
        # views é fonte da matriz de uso: sem ela nada pode ser candidato
        fu = fu_fields(snap2)
        self.assertEqual(fu["account"]["contoso_legacy"]["bucket"], "inconclusivo")
        self.assertTrue(any("views: falhou" in r for r in fu["account"]["contoso_legacy"]["inconclusive_reasons"]))
        # derivados ficam fora do snapshot, um diretório por run
        self.assertFalse((snap2 / "findings.json").exists())
        self.assertTrue((cfg.derived_dir / m1["run_id"] / "findings.json").exists())
        self.assertTrue((cfg.derived_dir / m2["run_id"] / "findings.json").exists())
        ok, probs = snapshot_mod.verify(snap1)
        self.assertTrue(ok, probs)  # o render não escreve no snapshot

    def test_snapshot_adulterado_e_ignorado(self):
        cfg = self.env.cfg()
        ctx1, m1, _ = self.env.extract(cfg)
        ctx2, m2, _ = self.env.extract(cfg)
        (ctx2.snapshot_dir / "tables.json").write_text("[]")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.assertEqual(snapshot_mod.resolve(cfg.raw_dir), ctx1.snapshot_dir)
        self.assertIn("sha256", buf.getvalue())
        with self.assertRaises(SystemExit):
            snapshot_mod.load(ctx2.snapshot_dir)

    def test_mesmo_segundo_nao_colide_e_staging_some_em_falha(self):
        root = self.env.tmp / "snaps"
        a = snapshot_mod.Staging(root, run_id="20261009T203200Z")
        a.write_json("x", {"v": 1})
        pa, _ = a.publish({})
        b = snapshot_mod.Staging(root, run_id="20261009T203200Z")
        b.write_json("x", {"v": 2})
        pb, _ = b.publish({})
        self.assertEqual((pa.name, pb.name), ("20261009T203200Z", "20261009T203200Z-2"))
        # interrupção no meio da extração: nenhum snapshot novo, nenhum staging sobrando
        cfg = self.env.cfg()
        before = set(p.name for p in snapshot_mod.list_snapshots(cfg.raw_dir))

        def boom(p):
            if p.startswith("pluginassemblies"):
                raise KeyboardInterrupt
        with patched_route(boom), self.assertRaises(KeyboardInterrupt):
            self.env.extract(cfg)
        self.assertEqual(set(p.name for p in snapshot_mod.list_snapshots(cfg.raw_dir)), before)
        self.assertEqual(list(cfg.raw_dir.glob(".staging-*")) if cfg.raw_dir.exists() else [], [])

    def test_only_registra_nao_executado(self):
        cfg = self.env.cfg()
        ctx, m, _ = self.env.extract(cfg, ["views"])
        self.assertEqual(m["collectors"]["plugins"]["status"], "nao-executado")
        self.assertTrue(any(g["collector"] == "plugins" and g["kind"] == "nao-executado" for g in m["gaps"]))
        self.assertFalse((ctx.snapshot_dir / "plugins.json").exists())


# ================================================================================================
class TestP002Segredos(unittest.TestCase):
    def test_vetores_redigidos_e_scanner_limpo(self):
        for name, text in SECRET_VECTORS.items():
            red = secrets.redact(text)
            self.assertIsNone(SECRET_RE.search(red), f"{name}: {red}")
            self.assertTrue(secrets.scan(text), f"{name}: scan não detectou")
            self.assertEqual(secrets.scan(red), [], f"{name}: scanner acusa texto já redigido")
            self.assertEqual(secrets.redact(red), red, f"{name}: redação não é idempotente")
        for text in NOT_SECRETS:
            self.assertEqual(secrets.redact(text), text, f"falso positivo: {text}")

    def test_redacao_estruturada_por_chave(self):
        t = secrets.redact_tree({"headers": {"x-functions-key": "abc", "Ocp-Apim-Subscription-Key": "def",
                                             "Content-Type": "application/json"},
                                 "publickeytoken": "31bf3856ad364e35", "secret_hits": [], "clientSecret": "zz"})
        self.assertEqual(t["headers"]["x-functions-key"], secrets.REDACTED)
        self.assertEqual(t["headers"]["Ocp-Apim-Subscription-Key"], secrets.REDACTED)
        self.assertEqual(t["headers"]["Content-Type"], "application/json")
        self.assertEqual(t["publickeytoken"], "31bf3856ad364e35")
        self.assertEqual(t["clientSecret"], secrets.REDACTED)

    def test_nenhum_vetor_em_nenhuma_saida_nem_no_console(self):
        env = Env()
        try:
            js = tp.JS_ACCOUNT + "\n" + "\n".join(SECRET_VECTORS.values()) + "\n"
            flow = json.loads(json.dumps(tp.FLOW))
            acts = flow["properties"]["definition"]["actions"]
            acts["Chama_API"] = {"type": "Http", "inputs": {
                "uri": "https://svcuser:FAKEpass0008@erp.contoso.example/api?api_key=FAKEapikey0001",
                "headers": {"x-functions-key": "FAKEfunckey0005", "Ocp-Apim-Subscription-Key": "FAKEapim0007"}}}
            err_body = json.dumps({"error": {"message": "Forbidden em https://x.contoso.example/?access_token="
                                             "FAKEaccess0002&client_secret=FAKEclisec0003 (x-functions-key: FAKEfunckey0004)"}})

            def inject(p):
                if re.match(r"webresourceset\(" + re.escape(G(40)), p):
                    import base64
                    return {"content": base64.b64encode(js.encode()).decode()}
                if re.match(r"workflows\(" + re.escape(G(50)), p):
                    return {"clientdata": json.dumps(flow), "xaml": None}
                if p.startswith("canvasapps"):
                    raise DataverseError(403, p, err_body)
                if p.startswith("environmentvariabledefinitions"):
                    return [{"environmentvariabledefinitionid": G(130), "schemaname": "contoso_ErpUrl", "type": 100000000,
                             "defaultvalue": "https://erp.contoso.example/?code=FAKEc13", "ismanaged": False,
                             "environmentvariabledefinition_environmentvariablevalue": [
                                 {"value": "https://svcuser:FAKEpass0008@erp.contoso.example/"}]}]
                if p.startswith("serviceendpoints"):
                    raise DataverseError(400, p, "falhou ao ler https://x.contoso.example/?pwd=FAKEpwd0012")
            cfg = env.cfg()
            with patched_route(inject):
                ctx, manifest, console = env.extract(cfg)
                v, console2 = env.render(cfg)
            leaks = [(f, m.group(0)) for f, text in all_text_under(env.tmp / "out") + all_text_under(env.tmp / "vault")
                     for m in SECRET_RE.finditer(text)]
            self.assertEqual(leaks, [], f"segredo em arquivo de saída: {leaks[:5]}")
            self.assertIsNone(SECRET_RE.search(console + console2), "segredo no console")
            self.assertIsNone(SECRET_RE.search(json.dumps(manifest["gaps"], ensure_ascii=False)), "segredo nas lacunas")
            gaps = {g["collector"]: g for g in manifest["gaps"]}
            self.assertIn(secrets.REDACTED, gaps["apps"]["error"])  # a lacuna existe, só sem o valor
            self.assertEqual(manifest["collectors"]["serviceendpoints"]["status"], "falhou")
            # o achado de segredo continua apontando o web resource (sem o valor)
            fnd = {f["id"]: f for f in json.loads((v.derived_dir / "findings.json").read_text())}
            self.assertIn("SEC-01", fnd)
            self.assertEqual(secrets.scan_paths([env.tmp / "out", env.tmp / "vault"]), [])
        finally:
            env.close()

    def test_scanner_final_bloqueia_render_e_preserva_vault(self):
        env = Env()
        try:
            cfg = env.cfg()
            ctx, m, _ = env.extract(cfg)
            env.render(cfg)
            idx = cfg.vault_dir / "00 Índice.md"
            before = idx.read_text()
            orig = findings_mod.compute

            def leaky(d):  # simula um caminho de código que esqueceu de redigir
                out = orig(d)
                out[0] = dict(out[0], detail='config: password="FAKEleak0099"')
                return out
            obsidian.findings_mod.compute = leaky
            try:
                with self.assertRaises(secrets.SecretLeakError) as cm:
                    env.render(cfg)
            finally:
                obsidian.findings_mod.compute = orig
            self.assertNotIn("FAKEleak0099", str(cm.exception), "a mensagem de erro não pode repetir o valor")
            self.assertEqual(idx.read_text(), before, "vault publicado foi alterado mesmo com vazamento")
            self.assertFalse(any("FAKEleak0099" in t for _, t in all_text_under(env.tmp / "vault")))
            self.assertFalse(any("FAKEleak0099" in t for _, t in all_text_under(cfg.derived_dir)))
            self.assertEqual(list(cfg.vault_root.glob(".dvinv-render-*")), [])
        finally:
            env.close()

    def test_scanner_final_bloqueia_snapshot(self):
        env = Env()
        try:
            cfg = env.cfg()
            orig = snapshot_mod.secrets.redact_tree
            snapshot_mod.secrets.redact_tree = lambda x: x  # redação "quebrada"
            try:
                def inject(p):  # nome de solução não é redigido na origem — só no redact_tree (desligado aqui)
                    if p.startswith("solutions"):
                        return [{"solutionid": G(11), "uniquename": "ContosoCore", "version": "1.0.0.0",
                                 "friendlyname": "Contoso https://x.contoso.example/?api_key=FAKEapikey0001",
                                 "ismanaged": False, "publisherid": {"customizationprefix": "contoso"}}]
                with patched_route(inject), self.assertRaises(secrets.SecretLeakError):
                    env.extract(cfg)
            finally:
                snapshot_mod.secrets.redact_tree = orig
            self.assertEqual(snapshot_mod.list_snapshots(cfg.raw_dir), [])
            self.assertEqual(list(cfg.raw_dir.glob(".staging-*")), [])
        finally:
            env.close()


# ================================================================================================
class TestP003UsoDeCampos(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_amostra_parcial_nunca_e_candidato(self):
        cfg = self.env.cfg()
        cfg.field_usage_max_records = 2   # fake pagina de 2 em 2: lê só a 1ª página

        def late_fill(p):  # nulo nos primeiros registros, preenchido depois
            if p.startswith("contoso_projects?$select="):
                return [{"contoso_projectid": G(500 + i), "contoso_name": f"P{i}",
                         "_contoso_accountid_value": G(1) if i >= 3 else None} for i in range(6)]
        with patched_route(late_fill):
            ctx, m, _ = self.env.extract(cfg)
        fu = fu_fields(ctx.snapshot_dir)
        tab = next(t for t in json.loads((ctx.snapshot_dir / "field_usage.json").read_text())
                   if t["table"] == "contoso_project")
        self.assertTrue(tab["method"].startswith("parcial"), tab["method"])
        self.assertFalse(tab["count_complete"])
        f = fu["contoso_project"]["contoso_accountid"]
        self.assertEqual(f["populated"], 0)
        self.assertEqual(f["bucket"], "inconclusivo")
        self.assertTrue(any("amostra" in r for r in f["inconclusive_reasons"]), f["inconclusive_reasons"])
        self.assertFalse(any(x["bucket"] == "candidato-seguro" for x in fu["contoso_project"].values()))
        v, _ = self.env.render(cfg)
        fnd = {x["id"]: x for x in json.loads((v.derived_dir / "findings.json").read_text())}
        self.assertIn("FLD-04", fnd)
        self.assertTrue(any("contoso_project.contoso_accountid" in e for e in fnd["FLD-04"]["evidence"]))
        self.assertFalse(any("contoso_accountid" in e for e in fnd.get("FLD-01", {}).get("evidence", [])))

    def test_forms_falhando_vira_inconclusivo(self):
        cfg = self.env.cfg()

        def fail_forms(p):
            if p.startswith("systemforms"):
                raise DataverseError(503, p, "indisponível")
        with patched_route(fail_forms):
            ctx, m, _ = self.env.extract(cfg)
        self.assertEqual(m["collectors"]["forms"]["status"], "falhou")
        fu = fu_fields(ctx.snapshot_dir)
        f = fu["account"]["contoso_legacy"]
        self.assertEqual(f["bucket"], "inconclusivo")
        self.assertIn("forms: falhou", f["inconclusive_reasons"])
        self.assertEqual(m["stats"]["field_usage"]["safe_candidates"], 0)
        v, _ = self.env.render(cfg)
        fnd = {x["id"] for x in json.loads((v.derived_dir / "findings.json").read_text())}
        self.assertNotIn("FLD-01", fnd)
        self.assertIn("FLD-04", fnd)
        note = (cfg.vault_dir / "Tabelas" / "account.md").read_text()
        self.assertIn("Cobertura incompleta", note)

    def test_sem_repositorio_e_inconclusivo(self):
        env = Env(repos=False)
        try:
            ctx, m, _ = env.extract(env.cfg())
            f = fu_fields(ctx.snapshot_dir)["account"]["contoso_legacy"]
            self.assertEqual(f["bucket"], "inconclusivo")
            self.assertTrue(any(r.startswith("repos:") for r in f["inconclusive_reasons"]))
        finally:
            env.close()

    def test_formulario_inativo_e_uso_fraco(self):
        cfg = self.env.cfg()
        inactive_xml = ('<form><tabs><tab><columns><column><sections><section><rows><row>'
                        '<cell><control id="l" datafieldname="contoso_legacy"/></cell></row></rows></section>'
                        '</sections></column></columns></tab></tabs></form>')

        def forms(p):
            if p.startswith(f"systemforms({G(62)})"):
                return {"formxml": inactive_xml}
            if p.startswith("systemforms?$select=formid,name") and "$filter" not in p:
                return [{"formid": G(60), "name": "Conta Principal", "type": 2, "objecttypecode": "account",
                         "formactivationstate": 1, "ismanaged": False},
                        {"formid": G(62), "name": "Conta Antiga", "type": 2, "objecttypecode": "account",
                         "formactivationstate": 0, "ismanaged": False}]
        with patched_route(forms):
            ctx, m, _ = self.env.extract(cfg)
        f = fu_fields(ctx.snapshot_dir)["account"]["contoso_legacy"]
        self.assertEqual(f["forms_inactive"], ["Conta Antiga"])
        self.assertEqual(f["forms"], [])
        self.assertEqual(f["bucket"], "sem-dados-uso-fraco")
        v, _ = self.env.render(cfg)
        fnd = {x["id"]: x for x in json.loads((v.derived_dir / "findings.json").read_text())}
        self.assertTrue(any("contoso_legacy" in e and "Conta Antiga" in e for e in fnd["FLD-02"]["evidence"]))
        self.assertFalse(any("contoso_legacy" in e for e in fnd.get("FLD-01", {}).get("evidence", [])))

    def test_candidato_so_com_cobertura_completa_e_texto_de_investigacao(self):
        cfg = self.env.cfg()
        ctx, m, _ = self.env.extract(cfg)
        tab = next(t for t in json.loads((ctx.snapshot_dir / "field_usage.json").read_text()) if t["table"] == "account")
        self.assertTrue(tab["coverage_complete"] and tab["count_complete"], tab.get("missing_sources"))
        self.assertEqual(fu_fields(ctx.snapshot_dir)["account"]["contoso_legacy"]["bucket"], "candidato-seguro")
        label = usage_mod.BUCKETS["candidato-seguro"].lower()
        self.assertIn("investigação de remoção", label)
        self.assertIn("confirmar integrações externas", label)
        v, _ = self.env.render(cfg)
        fnd = {x["id"]: x for x in json.loads((v.derived_dir / "findings.json").read_text())}
        self.assertIn("investigação de remoção", fnd["FLD-01"]["title"])
        self.assertNotRegex(fnd["FLD-01"]["title"] + fnd["FLD-01"]["detail"], r"(?i)seguro (a|para) remo")
        sec = (cfg.vault_dir / "05 Uso de Campos.md").read_text()
        self.assertIn("Candidatos a investigação de remoção", sec)
        self.assertNotIn("Candidatos seguros", sec)


# ================================================================================================
class _Resp:
    def __init__(self, body):
        self.status, self._body = 200, json.dumps(body).encode()

    def read(self):
        return self._body

    def getheader(self, name, default=None):
        return default


class _Conn:
    def __init__(self, pages):
        self.pages, self.requests = list(pages), []

    def request(self, method, target, headers=None):
        self.requests.append((method, target, headers))

    def getresponse(self):
        return _Resp(self.pages.pop(0))

    def close(self):
        pass


class _Cred:
    def __init__(self):
        self.calls = 0

    def get_token(self, *scopes, **_):
        self.calls += 1
        return type("T", (), {"token": "tok-fake", "expires_on": 4_000_000_000})()


class TestP102NextLink(unittest.TestCase):
    def setUp(self):
        self.cfg = type("C", (), {"url": "https://contoso.crm.dynamics.com", "parallel": 1,
                                  "api": "https://contoso.crm.dynamics.com/api/data/v9.2"})()

    def client(self, pages):
        cred = _Cred()
        c = Client(self.cfg, cred)
        conn = _Conn(pages)
        c._conn = lambda host, fresh=False: conn
        return c, cred, conn

    def test_nextlink_para_outro_host_e_recusado(self):
        for evil in ("https://evil.example/api/data/v9.2/accounts?$skiptoken=1",
                     "http://contoso.crm.dynamics.com/api/data/v9.2/accounts?$skiptoken=1",
                     "https://contoso.crm.dynamics.com.evil.example/x",
                     "https://user:pw@contoso.crm.dynamics.com/api/data/v9.2/accounts",
                     "https://contoso.crm.dynamics.com:8443/api/data/v9.2/accounts"):
            c, cred, conn = self.client([{"value": [{"a": 1}], "@odata.nextLink": evil}])
            with self.assertRaises(UnsafeUrlError, msg=evil):
                c.get_all("accounts?$select=name")
            self.assertEqual(len(conn.requests), 1, f"seguiu nextLink {evil}")

    def test_token_nao_sai_para_host_desconhecido(self):
        c, cred, conn = self.client([])
        with self.assertRaises(UnsafeUrlError):
            c._request("https://evil.example/api/data/v9.2/WhoAmI()")
        with self.assertRaises(UnsafeUrlError):
            c.get("https://evil.example/api/data/v9.2/WhoAmI()")
        self.assertEqual(cred.calls, 0)
        self.assertEqual(conn.requests, [])

    def test_mesmo_host_segue_normalmente(self):
        nxt = "https://CONTOSO.crm.dynamics.com/api/data/v9.2/accounts?$select=name&$skiptoken=2"
        c, cred, conn = self.client([{"value": [{"a": 1}], "@odata.nextLink": nxt}, {"value": [{"a": 2}]}])
        self.assertEqual(c.get_all("accounts?$select=name"), [{"a": 1}, {"a": 2}])
        self.assertEqual(len(conn.requests), 2)

    def test_paginacao_do_uso_de_campos_valida_nextlink(self):
        env = Env()
        try:
            cfg = env.cfg()
            client = tp.FakeClient(cfg, credential=None)

            def evil_page(url, page_size=5000):
                return {"value": [{"contoso_projectid": G(1), "contoso_name": None}],
                        "@odata.nextLink": "https://evil.example/next"}
            client._request = evil_page
            ctx = Context(cfg, client, Scope(cfg))
            t = {"entityset": "contoso_projects", "primary_id": "contoso_projectid", "logical": "contoso_project"}
            with self.assertRaises(UnsafeUrlError):
                usage_mod._page_counts(ctx, t, [{"logical": "contoso_name", "type": "String"}])
        finally:
            env.close()


class TestP103CacheDeToken(unittest.TestCase):
    def test_sem_cache_por_padrao_e_nunca_texto_puro(self):
        calls = []

        def opts(**kw):
            calls.append(kw)
            return kw
        kw = auth_mod.devicecode_kwargs(None, None, False, opts)
        self.assertNotIn("cache_persistence_options", kw)
        kw = auth_mod.devicecode_kwargs("t", None, True, opts)
        self.assertEqual(calls, [{"name": "dvinv", "allow_unencrypted_storage": False}])
        src = Path(auth_mod.__file__).read_text()
        self.assertNotIn("allow_unencrypted_storage=True", src)


# ================================================================================================
class TestPerfilMetadataOnly(unittest.TestCase):
    def test_perfil_trava_deep_mesmo_com_deep_all(self):
        env = Env(extra={"profile": "metadata_only", "deep": {"field_usage": True, "webresource_content": True}})
        try:
            cfg = env.cfg()
            for k in config_mod.PROFILES["metadata_only"]:
                self.assertFalse(cfg.deep[k], k)
            self.assertTrue(cfg.deep["record_counts"], "record_counts (contagem agregada) fica permitido")
            self.assertEqual(cfg.profile, "metadata_only")
            ctx, m, _ = env.extract(cfg)
            self.assertEqual(m["profile"], "metadata_only")
            self.assertEqual(sorted(m["profile_blocked"]), sorted(config_mod.PROFILES["metadata_only"]))
            paths = [q["path"] for q in m["queries"]]
            for bad in ("$select=content", "clientdata", "xaml", "plugintracelogs?", "annotations", "audits",
                        "teammemberships", "fetchXml", "systemuserrolescollection", "systemusers?$select=systemuserid,fullname"):
                self.assertFalse([p for p in paths if bad in p], f"perfil metadata_only leu {bad}")
            self.assertFalse((ctx.snapshot_dir / "field_usage.json").exists())
            self.assertFalse((ctx.snapshot_dir / "users.json").exists(), "lista nominal de usuários no metadata_only")
            self.assertTrue((ctx.snapshot_dir / "pcf.json").exists(), "PCF (metadado de customização) fica permitido")
            env.render(cfg)
            amb = (cfg.vault_dir / "01 Ambiente.md").read_text()
            self.assertIn("| Perfil de coleta | metadata_only |", amb)
        finally:
            env.close()

    def test_cli_sobrepoe_yaml(self):
        env = Env()
        try:
            cfg = config_mod.load(env.tmp / "inventory.yaml", {"deep_all": True, "profile": "metadata_only"})
            self.assertFalse(cfg.deep["webresource_content"] or cfg.deep["field_usage"] or cfg.deep["storage"])
            with self.assertRaises(SystemExit):
                config_mod.load(env.tmp / "inventory.yaml", {"profile": "inexistente"})
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
