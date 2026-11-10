"""HTTPS for the cloud services (Claude, Groq).

- Certificates are checked against the Windows certificate store
  (truststore). Python 3.13+'s own strict check rejects some valid chains,
  and networks with a filtering proxy install their certificate in Windows.
- A filtered network (e.g. Netspark / Etrog) answers blocked sites with an
  HTML redirect page instead of an error. We recognise that and raise
  Blocked, so the app switches to local processing at once and says why.
"""
from __future__ import annotations

import ssl
import urllib.request


class Blocked(Exception):
    """The network's content filter blocked this service."""


def _context():
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return ctx


CTX = _context()


on_blocked = None  # set by the app: callback(message) to tell the user once
_reported: set = set()


def report_blocked(service: str, err: Exception) -> None:
    print(f"[net] {service}: {err}", flush=True)
    if service not in _reported:
        _reported.add(service)
        if on_blocked:
            on_blocked(f"סינון האינטרנט חוסם את {service}: עובד מקומית (איטי יותר)")


class Session:
    """One kept-alive HTTPS connection per service (saves the TLS handshake,
    ~0.1-0.3 s per request through a filtering proxy). Thread-safe."""

    def __init__(self, host: str, timeout: float):
        import threading

        self.host, self.timeout = host, timeout
        self.conn = None
        self.lock = threading.Lock()

    def _connect(self):
        import http.client

        self.conn = http.client.HTTPSConnection(self.host, context=CTX, timeout=self.timeout)

    def request(self, path: str, body: bytes, headers: dict):
        """POST; returns an open response (caller reads it while holding .lock).

        Raises Blocked for a content filter's page, urllib-style HTTPError
        for API errors.
        """
        import urllib.error

        hdr = {"User-Agent": "live-translator", **headers}
        for attempt in (1, 2):  # a kept-alive connection may have been closed
            if self.conn is None:
                self._connect()
            try:
                self.conn.request("POST", path, body, hdr)
                r = self.conn.getresponse()
                break
            except (ConnectionError, OSError, Exception) as e:
                self.conn.close()
                self.conn = None
                if attempt == 2:
                    raise e
        ctype = r.headers.get("content-type", "").lower()
        if "html" in ctype:
            page = r.read()
            hint = "etrog/netspark" if b"etrog" in page or b"netspark" in page.lower() else "unknown filter"
            raise Blocked(f"blocked by the network's content filter ({hint})")
        if r.status >= 400:
            detail = r.read()
            raise urllib.error.HTTPError(f"https://{self.host}{path}", r.status, detail.decode("utf-8", "replace")[:300],
                                         r.headers, None)
        return r


def post(url: str, data: bytes, headers: dict, timeout: float) -> bytes:
    """POST and return the body; raises Blocked for a filter's block page."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "live-translator", **headers})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        body = r.read()
        ctype = r.headers.get("content-type", "")
    if "html" in ctype.lower() or body.lstrip()[:1] == b"<":
        hint = "etrog/netspark" if b"etrog" in body or b"netspark" in body.lower() else "unknown filter"
        raise Blocked(f"blocked by the network's content filter ({hint})")
    return body
