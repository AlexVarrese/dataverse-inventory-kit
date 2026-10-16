"""Cliente HTTP read-only para a Dataverse Web API v9.2.

Só lê: GET direto ou GETs empacotados num `$batch` (o POST /$batch só transporta leituras montadas aqui) — não
existe caminho de escrita neste pacote, por design.
Todas as chamadas ficam registradas em `log` (vai para manifest.json como trilha de evidência).
"""

import gzip
import http.client
import json
import re
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import uuid

from . import secrets

# Sem quote(), espaços no $filter quebram com "URL can't contain control characters".
_SAFE = ":/?&=$,()'@"


class DataverseError(Exception):
    """Erro da Web API. Mensagem e caminho são redigidos na origem: corpo de erro e URL podem ecoar
    segredos (query string, headers) e essa mensagem vai para console, lacunas e manifesto."""

    def __init__(self, status, path, body):
        msg = body
        try:
            msg = json.loads(body).get("error", {}).get("message") or body
        except (ValueError, AttributeError):
            pass
        msg = secrets.redact(str(msg))
        super().__init__(secrets.redact(f"HTTP {status}: {msg[:300]} — em {secrets.redact(str(path))[:160]}"))
        self.status = status
        self.message = msg


class UnsafeUrlError(DataverseError):
    """URL fora da base configurada (esquema ≠ https ou host diferente): não seguimos nem enviamos token."""

    def __init__(self, url, reason):
        super().__init__(0, url, f"URL recusada ({reason}) — o token só é enviado para a base configurada")


# Rastreamento detalhado (só no arquivo de log): uma linha por chamada HTTP. Ligado por cli.start_log.
TRACE = None


def _trace(text):
    if TRACE:
        try:
            TRACE(text)
        except Exception:  # noqa: BLE001 — log nunca derruba a coleta
            pass


def _fmt_s(s):
    return f"{int(s // 3600)}h{int(s % 3600 // 60):02d}m" if s >= 3600 else f"{int(s // 60)}m{int(s % 60):02d}s"


def progress(label, n, total, t0):
    """Linha de progresso com tempo decorrido e estimativa do restante."""
    el = time.time() - t0
    eta = el / n * (total - n) if n else 0
    fmt = lambda s: f"{int(s // 3600)}h{int(s % 3600 // 60):02d}m" if s >= 3600 else f"{int(s // 60)}m{int(s % 60):02d}s"  # noqa: E731
    print(f"[dvinv]   {label}: {n}/{total} ({100 * n // total}%) · {fmt(el)} decorrido"
          + (f" · ~{fmt(eta)} restante" if n < total else ""), flush=True)


_PART_RE = re.compile(r"HTTP/1\.[01] (\d{3})[^\r\n]*\r?\n(?:.*?\r?\n)?\r?\n(.*)", re.S)


def parse_batch(ctype, raw):
    """Resposta multipart de um `$batch` (sem changesets) → [(status, corpo)] na ordem dos pedidos; None se não for."""
    m = re.search(r'boundary="?([^";]+)"?', ctype or "")
    if not m:
        return None
    out = []
    for seg in raw.decode("utf-8", "replace").split("--" + m.group(1))[1:]:
        if seg.startswith("--"):
            break
        _head, _, http = seg.lstrip("\r\n").partition("\r\n\r\n")  # cabeçalhos da parte, depois a resposta HTTP
        pm = _PART_RE.match(http.lstrip())
        if not pm:
            return None
        out.append((int(pm.group(1)), pm.group(2).strip()))
    return out


def _short(path, n=180):
    p = urllib.parse.unquote(path)
    return p if len(p) <= n else p[:n] + "…"


class Client:
    def __init__(self, cfg, credential=None, timeout=180, max_retries=5):
        self.cfg = cfg
        self.base = cfg.api
        self.scope = cfg.url.rstrip("/") + "/.default"
        self.credential = credential
        self.timeout = timeout
        self.max_retries = max_retries
        self._token = None
        self._token_exp = 0
        self._lock = threading.Lock()
        self._local = threading.local()   # uma conexão HTTPS persistente por thread (keep-alive)
        self._ssl = ssl.create_default_context()
        self.throttled = 0                # quantas vezes o serviço pediu para esperar (429)
        self.batch_off = None             # motivo, se o serviço/proxy recusou o $batch nesta execução
        self.workers = max(1, int(getattr(cfg, "parallel", 4) or 1))
        self.log = []
        base = urllib.parse.urlsplit(self.base)
        self._host = (base.hostname or "").lower()
        self._port = base.port or 443
        if base.scheme != "https" or not self._host:
            raise ValueError(f"environment.url precisa ser https://<org>.crm*.dynamics.com (recebido: {cfg.url})")

    def check_url(self, url):
        """Só https e o MESMO host/porta da base configurada. Vale para caminhos absolutos passados por
        coletores e, principalmente, para `@odata.nextLink` vindo na resposta (não é confiável)."""
        try:
            parts = urllib.parse.urlsplit(url)
            port = parts.port or 443
        except ValueError:
            raise UnsafeUrlError(url, "URL malformada") from None
        if parts.scheme.lower() != "https":
            raise UnsafeUrlError(url, f"esquema '{parts.scheme}' não é https")
        if parts.username or parts.password:
            raise UnsafeUrlError(url, "credencial embutida na URL")
        if (parts.hostname or "").lower() != self._host or port != self._port:
            raise UnsafeUrlError(url, f"host '{parts.hostname}' difere de '{self._host}'")
        return url

    def _bearer(self):
        with self._lock:  # threads compartilham o token; renova uma vez só
            if not self._token or time.time() > self._token_exp - 120:
                tok = self.credential.get_token(self.scope)
                self._token, self._token_exp = tok.token, tok.expires_on
            return self._token

    def parallel(self, fn, items, label=None):
        """Aplica fn(item) com `self.workers` chamadas simultâneas → [(item, resultado, erro)] na ordem.

        Para etapas de uma chamada por item (colunas por tabela, ribbons, dependências). Mostra
        progresso a cada ~10%. Os limites de serviço continuam respeitados: 429 → espera Retry-After.
        """
        items = list(items)
        total, step = len(items), max(1, len(items) // 10)

        def one(it):
            try:
                return it, fn(it), None
            except Exception as e:  # noqa: BLE001
                return it, None, e

        out, t0 = [], time.time()
        last = t0
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for n, r in enumerate(ex.map(one, items), 1):
                out.append(r)
                # a cada ~10% ou, em etapas longas, pelo menos a cada 30 s — para acompanhar e estimar o fim
                if label and total >= 5 and (n % step == 0 or n == total or time.time() - last >= 30):
                    progress(label, n, total, t0)
                    last = time.time()
        return out

    def _url(self, path):
        url = path if path.lower().startswith(("http:", "https:", "//")) else f"{self.base}/{path}"
        return self.check_url(urllib.parse.quote(url, safe=_SAFE + "%"))

    def _conn(self, host, fresh=False):
        """Conexão HTTPS reaproveitada (keep-alive): sem isso cada chamada paga TCP+TLS de novo
        (~0,6 s com 200 ms de distância até o datacenter — medido Brasil → NAM)."""
        c = getattr(self._local, "conn", None)
        if fresh or c is None or self._local.host != host:
            if c is not None:
                c.close()
            c = http.client.HTTPSConnection(host, timeout=self.timeout, context=self._ssl)
            self._local.conn, self._local.host = c, host
        return c

    def _send(self, url, method="GET", payload=None, headers=None, page_size=5000):
        """Uma requisição com keep-alive e novas tentativas (conexão caída, 429, 502/503/504) → (status, resp, corpo)."""
        self.check_url(url)  # antes de pedir token: nada de Bearer para host desconhecido
        parts = urllib.parse.urlsplit(url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        short = url.replace(self.base, "")
        for attempt in range(self.max_retries + 1):
            h = {
                "Authorization": f"Bearer {self._bearer()}",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "OData-MaxVersion": "4.0",
                "OData-Version": "4.0",
                "Prefer": f'odata.include-annotations="*",odata.maxpagesize={page_size}',
                **(headers or {}),
            }
            t0 = time.time()
            try:
                conn = self._conn(parts.netloc, fresh=attempt > 0)
                if payload is None:
                    conn.request(method, target, headers=h)
                else:
                    conn.request(method, target, body=payload, headers=h)
                resp = conn.getresponse()
                body = resp.read()
                if resp.getheader("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
            except (http.client.HTTPException, OSError) as e:  # conexão caiu/expirou: reabre e tenta de novo
                why = "tempo limite" if "timed out" in str(e).lower() else e.__class__.__name__
                msg = (f"[dvinv]   {why} após {time.time() - t0:.0f}s em {_short(short)} — "
                       f"tentativa {attempt + 1}/{self.max_retries + 1}")
                if attempt < self.max_retries:
                    print(msg + ", tentando de novo", flush=True)  # antes era silencioso: parecia travado
                    time.sleep(min(2 ** attempt, 30))
                    continue
                print(msg + ", desistindo", flush=True)
                raise DataverseError(0, short, str(e)) from None
            _trace(f"{method} {_short(short)} → {resp.status} · {len(body) / 1024:,.0f} KB · {time.time() - t0:.1f}s"
                   + (f" · tentativa {attempt + 1}" if attempt else ""))
            # 429 = service protection limit; 502/503/504 = transitório
            if resp.status in (429, 502, 503, 504) and attempt < self.max_retries:
                wait = min(float(resp.getheader("Retry-After") or 2 ** attempt), 300)
                if resp.status == 429:
                    with self._lock:
                        self.throttled += 1
                    print(f"[dvinv]   limite do serviço (429): aguardando {wait:.0f}s", flush=True)
                time.sleep(wait)
                continue
            return resp.status, resp, body

    def _request(self, url, page_size=5000):
        status, _resp, body = self._send(url, page_size=page_size)
        if status == 200:
            return json.loads(body.decode("utf-8"))
        raise DataverseError(status, url.replace(self.base, ""), body.decode("utf-8", "replace"))

    def _batch_post(self, payload, boundary):
        """POST /$batch → (status, content-type, corpo). Separado para os testes simularem o serviço."""
        status, resp, body = self._send(f"{self.base}/$batch", "POST", payload, {
            "Content-Type": f"multipart/mixed; boundary={boundary}", "Prefer": "odata.continue-on-error"})
        return status, resp.getheader("Content-Type", ""), body

    def get_batch(self, items, path, label=None, pages=False, size=None):
        """Uma leitura por item, empacotadas em `$batch` → [(item, resposta, erro)] na ordem (como `parallel`).

        Para etapas de uma chamada por item (ribbons, privilégios por papel, colunas por tabela…): cada POST /$batch
        leva `size` GETs (deep.batch_size, padrão 20) e os pacotes correm em paralelo (`parallel`). Troca centenas
        de idas e voltas por poucas — o ganho é grande quando o gargalo é a rede (proxy, VDI). O serviço executa
        cada GET separadamente: limites de serviço e permissões não mudam. Leitura que volta 429/5xx no pacote é
        refeita sozinha (com espera); se o serviço ou o proxy recusarem o `$batch`, segue uma chamada por item
        até o fim da execução. `path(item)` → caminho do GET; pages=True segue @odata.nextLink → lista de linhas.
        """
        items = list(items)
        single = (lambda it: self.get_all(path(it))) if pages else (lambda it: self.get(path(it)))
        size = int(size or (getattr(self.cfg, "deep", None) or {}).get("batch_size", 20) or 0)
        if size <= 1 or self.batch_off or len(items) < 2:
            return self.parallel(single, items, label=label)
        groups = [items[i:i + size] for i in range(0, len(items), size)]
        out = []
        for group, res, err in self.parallel(lambda g: self._batch_group(g, path, single, pages), groups,
                                             label=f"{label} (pacotes de {size})" if label else None):
            out.extend(res if err is None else self._each(group, single))
        return out

    @staticmethod
    def _each(group, single):
        res = []
        for it in group:
            try:
                res.append((it, single(it), None))
            except Exception as e:  # noqa: BLE001
                res.append((it, None, e))
        return res

    def _batch_group(self, group, path, single, pages):
        if self.batch_off:
            return self._each(group, single)
        paths = [path(it) for it in group]
        urls = [self._url(p) for p in paths]  # check_url: só a base configurada entra no pacote
        b = f"batch_{uuid.uuid4().hex}"
        payload = "".join(
            f"--{b}\r\nContent-Type: application/http\r\nContent-Transfer-Encoding: binary\r\n\r\n"
            f"GET {u} HTTP/1.1\r\nAccept: application/json\r\n"
            f'Prefer: odata.include-annotations="*",odata.maxpagesize=5000\r\n\r\n' for u in urls) + f"--{b}--\r\n"
        t0 = time.time()
        status, ctype, raw = self._batch_post(payload.encode("utf-8"), b)
        ms = int((time.time() - t0) * 1000)
        parts = parse_batch(ctype, raw) if status == 200 else None
        if not parts or len(parts) != len(group):
            if status in (400, 403, 404, 405, 413, 414, 501) or status == 200:  # recusado ou resposta irreconhecível
                with self._lock:
                    if not self.batch_off:
                        self.batch_off = f"HTTP {status}"
                        print(f"[dvinv]   $batch indisponível ({self.batch_off}) — seguindo com uma chamada por item",
                              flush=True)
            return self._each(group, single)
        res = []
        for it, p, (st, text) in zip(group, paths, parts):
            if st in (429, 502, 503, 504):  # refeita sozinha: get/get_all esperam o Retry-After
                res += self._each([it], single)
                continue
            self.log.append({"path": p, "status": st, "ms": ms, "batch": True})
            if st != 200:
                res.append((it, None, DataverseError(st, p, text)))
                continue
            data = json.loads(text)
            if pages:
                rows, nxt = data.get("value", []), data.get("@odata.nextLink")
                while nxt:
                    self.check_url(nxt)
                    more = self._request(nxt)
                    rows += more.get("value", [])
                    nxt = more.get("@odata.nextLink")
                data = rows
            res.append((it, data, None))
        return res

    def get(self, path):
        """Uma única resposta (entidade, função ou primeira página)."""
        t0 = time.time()
        url = self._url(path)
        try:
            data = self._request(url)
            self.log.append({"path": path, "status": 200, "ms": int((time.time() - t0) * 1000)})
            return data
        except DataverseError as e:
            self.log.append({"path": path, "status": e.status, "ms": int((time.time() - t0) * 1000)})
            raise

    def _page_progress(self, path, rows, pages, t0, state):
        """Leituras paginadas longas: linha a cada 30 s (antes, minutos sem nenhuma mensagem)."""
        now = time.time()
        if now - t0 >= 15 and now - state.get("last", t0) >= 30:
            state["last"] = now
            print(f"[dvinv]   lendo {path.split('?')[0]}: {rows:,} linhas ({pages} páginas) · {_fmt_s(now - t0)}"
                  .replace(",", "."), flush=True)

    def get_all(self, path, max_rows=None):
        """Segue @odata.nextLink até o fim (ou até max_rows linhas, se informado)."""
        t0 = time.time()
        url, out = self._url(path), []
        pages, state = 0, {}
        try:
            while url:
                data = self._request(url)
                out.extend(data.get("value", []))
                pages += 1
                self._page_progress(path, len(out), pages, t0, state)
                url = data.get("@odata.nextLink")
                if max_rows and len(out) >= max_rows:
                    del out[max_rows:]
                    break
                if url:
                    self.check_url(url)
        except DataverseError as e:
            self.log.append({"path": path, "status": e.status, "rows": len(out),
                             "ms": int((time.time() - t0) * 1000)})
            raise
        self.log.append({"path": path, "status": 200, "rows": len(out), "ms": int((time.time() - t0) * 1000)})
        return out

    def iter_all(self, path):
        """Como get_all, mas entrega página a página (leituras de centenas de milhares de linhas)."""
        t0, n = time.time(), 0
        url = self._url(path)
        pages, state = 0, {}
        try:
            while url:
                data = self._request(url)
                rows = data.get("value", [])
                n += len(rows)
                pages += 1
                self._page_progress(path, n, pages, t0, state)
                yield from rows
                url = data.get("@odata.nextLink")
                if url:
                    self.check_url(url)
        except DataverseError as e:
            self.log.append({"path": path, "status": e.status, "rows": n, "ms": int((time.time() - t0) * 1000)})
            raise
        self.log.append({"path": path, "status": 200, "rows": n, "ms": int((time.time() - t0) * 1000)})

    def entity_definitions(self, select, expand, chunk=40, label="metadados das tabelas"):
        """EntityDefinitions com `$expand` (colunas, chaves) em lotes de tabelas, em paralelo.

        A API não pagina metadados: `EntityDefinitions?$expand=Attributes` da org inteira vira uma resposta única
        de dezenas/centenas de MB e, em ambientes grandes, a conexão cai no meio (ConnectionAbortError) — e o
        coletor de tabelas, que define o escopo de quase todos os outros, falhava. Aqui: 1) lista as tabelas sem
        expand (resposta pequena); 2) busca o expand em lotes de `chunk` tabelas por MetadataId; 3) lote que
        falhar é refeito tabela a tabela. Retorna a mesma lista que a chamada única retornaria.
        """
        sel = ",".join(dict.fromkeys(["MetadataId", "LogicalName"] + [x for x in select.split(",") if x]))
        base = self.get_all(f"EntityDefinitions?$select={sel}")
        if not expand:
            return base
        ids = [e["MetadataId"] for e in base if e.get("MetadataId")]
        chunks = [ids[i:i + chunk] for i in range(0, len(ids), chunk)]

        def fetch(ch):
            flt = " or ".join(f"MetadataId eq {x}" for x in ch)
            try:
                return self.get_all(f"EntityDefinitions?$select={sel}&$filter={flt}&$expand={expand}")
            except DataverseError:
                got = []
                for x in ch:  # isola a tabela problemática; as demais do lote seguem
                    try:
                        got.append(self.get(f"EntityDefinitions({x})?$select={sel}&$expand={expand}"))
                    except DataverseError:
                        pass
                return got

        full = {}
        for _ch, got, err in self.parallel(fetch, chunks, label=f"{label} (lotes de {chunk} tabelas)"):
            for e in got or []:
                full[e["MetadataId"]] = e
        missing = [e["LogicalName"] for e in base if e.get("MetadataId") not in full]
        if missing:
            raise DataverseError(0, "EntityDefinitions (lotes)",
                                 f"{len(missing)} tabela(s) sem metadados após nova tentativa: {', '.join(missing[:15])}")
        return [full[e["MetadataId"]] for e in base if e.get("MetadataId") in full]

    def get_many(self, entity_set, key, ids, select, batch=10, label=None):
        """Busca vários registros por id em lotes (`key eq a or key eq b …`) → {id_minúsculo: registro}.

        Troca N chamadas por N/batch. Se um lote falhar, refaz item a item para isolar o registro
        problemático; ids que nem assim vierem ficam fora do resultado (o chamador registra a lacuna).
        """
        ids = [i for i in dict.fromkeys(ids) if i]
        chunks = [ids[i:i + batch] for i in range(0, len(ids), batch)]

        def fetch(chunk):
            got = {}
            flt = " or ".join(f"{key} eq {x}" for x in chunk)
            try:
                for r in self.get_all(f"{entity_set}?$select={key},{select}&$filter={flt}"):
                    got[str(r.get(key)).lower()] = r
            except DataverseError:
                for x in chunk:
                    try:
                        got[x.lower()] = self.get(f"{entity_set}({x})?$select={select}")
                    except DataverseError:
                        pass
            return got

        # lotes em paralelo (cfg.parallel): conteúdo de web resource, formxml, xaml… são respostas grandes, e
        # em sequência a latência até o datacenter somava horas em ambientes muito customizados
        out = {}
        for _chunk, got, err in self.parallel(fetch, chunks, label=f"{label or entity_set} (lotes de {batch})"):
            if got:
                out.update(got)
        return out

    def get_first_ok(self, *paths):
        """Tenta variações de $select (colunas mudam entre versões/regiões); devolve a primeira que funcionar."""
        last = None
        for p in paths:
            try:
                return self.get_all(p)
            except DataverseError as e:
                if e.status not in (400, 404):
                    raise
                last = e
        raise last
