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
