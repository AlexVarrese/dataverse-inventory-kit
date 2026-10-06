"""Cliente HTTP read-only para a Dataverse Web API v9.2.

Só expõe GET — não existe caminho de escrita neste pacote, por design.
Todas as chamadas ficam registradas em `log` (vai para manifest.json como trilha de evidência).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

# Sem quote(), espaços no $filter quebram com "URL can't contain control characters".
_SAFE = ":/?&=$,()'@"


class DataverseError(Exception):
    def __init__(self, status, path, body):
        super().__init__(f"HTTP {status} em {path}: {body[:300]}")
        self.status = status


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
        self.log = []

    def _bearer(self):
        if not self._token or time.time() > self._token_exp - 120:
            tok = self.credential.get_token(self.scope)
            self._token, self._token_exp = tok.token, tok.expires_on
        return self._token

    def _url(self, path):
        url = path if path.startswith("http") else f"{self.base}/{path}"
        return urllib.parse.quote(url, safe=_SAFE + "%")

    def _request(self, url, page_size=5000):
        headers = {
            "Authorization": f"Bearer {self._bearer()}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Prefer": f'odata.include-annotations="*",odata.maxpagesize={page_size}',
        }
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                # 429 = service protection limit; 502/503/504 = transitório
                if e.code in (429, 502, 503, 504) and attempt < self.max_retries:
                    wait = float(e.headers.get("Retry-After") or 2 ** attempt)
                    time.sleep(min(wait, 60))
                    continue
                raise DataverseError(e.code, url.replace(self.base, ""), body) from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise DataverseError(0, url.replace(self.base, ""), str(e)) from None

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
