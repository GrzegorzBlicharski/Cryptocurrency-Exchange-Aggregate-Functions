"""Outbound HTTP for integrations: HTTPS only, timeouts, size cap, bounded retries,
no requests to private/loopback IP literals (SSRF guard). Tests swap the transport."""
from __future__ import annotations

import ipaddress
import time
from urllib.parse import urlsplit

import httpx

MAX_BYTES = 5 * 1024 * 1024
_transport: httpx.BaseTransport | None = None


class FetchError(Exception):
    pass


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Test hook: route all integration traffic through a mock transport."""
    global _transport
    _transport = transport


def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchError(f"only https URLs are allowed: {url[:80]}")
    host = parts.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
            raise FetchError("local hosts are not allowed") from None
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise FetchError("private network addresses are not allowed")


def _once(c: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    """One request; redirects are followed manually so every hop passes check_url()."""
    for _ in range(4):
        check_url(url)
        with c.stream(method, url, **kw) as r:
            if r.is_redirect and r.headers.get("location"):
                url = str(r.url.join(r.headers["location"]))
                method, kw = "GET", {"headers": kw.get("headers")}
                continue
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise FetchError("response too large")
            return httpx.Response(r.status_code, headers=r.headers, content=body, request=r.request)
    raise FetchError("too many redirects")


def request(method: str, url: str, *, headers: dict | None = None, json: dict | None = None,
            data: bytes | None = None, timeout: float = 20.0, retries: int = 2) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(transport=_transport, timeout=timeout, follow_redirects=False,
                              headers={"User-Agent": "APEX-OS/0.2"}) as c:
                resp = _once(c, method, url, headers=headers, json=json, content=data)
        except httpx.TransportError as exc:
            last = exc
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code < 500 and resp.status_code != 429:
                raise FetchError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            last = FetchError(f"HTTP {resp.status_code}")
        if attempt < retries and _transport is None:
            time.sleep(0.5 * 2 ** attempt)
    raise FetchError(str(last))


def get(url: str, **kw) -> httpx.Response:
    return request("GET", url, **kw)


def post(url: str, **kw) -> httpx.Response:
    return request("POST", url, **kw)
