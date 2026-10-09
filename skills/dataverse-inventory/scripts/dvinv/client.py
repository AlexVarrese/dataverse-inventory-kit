"""Cliente HTTP read-only para a Dataverse Web API v9.2.

Só expõe GET — não existe caminho de escrita neste pacote, por design.
Todas as chamadas ficam registradas em `log` (vai para manifest.json como trilha de evidência).
"""

import gzip
import http.client
import json
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.parse

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
        self.workers = max(1, int(getattr(cfg, "parallel", 4) or 1))
        self.log = []

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

        out = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for n, r in enumerate(ex.map(one, items), 1):
                out.append(r)
                if label and total >= 20 and (n % step == 0 or n == total):
                    print(f"[dvinv]   {label}: {n}/{total}", flush=True)
        return out

    def _url(self, path):
        url = path if path.startswith("http") else f"{self.base}/{path}"
        return urllib.parse.quote(url, safe=_SAFE + "%")

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

    def _request(self, url, page_size=5000):
        parts = urllib.parse.urlsplit(url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        short = url.replace(self.base, "")
        for attempt in range(self.max_retries + 1):
            headers = {
                "Authorization": f"Bearer {self._bearer()}",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "OData-MaxVersion": "4.0",
                "OData-Version": "4.0",
                "Prefer": f'odata.include-annotations="*",odata.maxpagesize={page_size}',
            }
            try:
                conn = self._conn(parts.netloc, fresh=attempt > 0)
                conn.request("GET", target, headers=headers)
                resp = conn.getresponse()
                body = resp.read()
                if resp.getheader("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
            except (http.client.HTTPException, OSError) as e:  # conexão caiu/expirou: reabre e tenta de novo
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise DataverseError(0, short, str(e)) from None
            if resp.status == 200:
                return json.loads(body.decode("utf-8"))
            # 429 = service protection limit; 502/503/504 = transitório
            if resp.status in (429, 502, 503, 504) and attempt < self.max_retries:
                wait = min(float(resp.getheader("Retry-After") or 2 ** attempt), 300)
                if resp.status == 429:
                    with self._lock:
                        self.throttled += 1
                    print(f"[dvinv]   limite do serviço (429): aguardando {wait:.0f}s", flush=True)
                time.sleep(wait)
                continue
            raise DataverseError(resp.status, short, body.decode("utf-8", "replace"))

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

    def get_all(self, path):
        """Segue @odata.nextLink até o fim."""
        t0 = time.time()
        url, out = self._url(path), []
        try:
            while url:
                data = self._request(url)
                out.extend(data.get("value", []))
                url = data.get("@odata.nextLink")
        except DataverseError as e:
            self.log.append({"path": path, "status": e.status, "rows": len(out),
                             "ms": int((time.time() - t0) * 1000)})
            raise
        self.log.append({"path": path, "status": 200, "rows": len(out), "ms": int((time.time() - t0) * 1000)})
        return out

    def get_many(self, entity_set, key, ids, select, batch=10):
        """Busca vários registros por id em lotes (`key eq a or key eq b …`) → {id_minúsculo: registro}.

        Troca N chamadas por N/batch. Se um lote falhar, refaz item a item para isolar o registro
        problemático; ids que nem assim vierem ficam fora do resultado (o chamador registra a lacuna).
        """
        out, ids = {}, [i for i in dict.fromkeys(ids) if i]
        for i in range(0, len(ids), batch):
            chunk = ids[i:i + batch]
            flt = " or ".join(f"{key} eq {x}" for x in chunk)
            try:
                for r in self.get_all(f"{entity_set}?$select={key},{select}&$filter={flt}"):
                    out[str(r.get(key)).lower()] = r
            except DataverseError:
                for x in chunk:
                    try:
                        out[x.lower()] = self.get(f"{entity_set}({x})?$select={select}")
                    except DataverseError:
                        pass
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
