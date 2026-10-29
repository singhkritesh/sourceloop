"""The only outbound HTTP client. Allowlist + private-range blocking + no auto-redirects + size cap."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 2 * 1024 * 1024
HARD_MAX_BYTES = 16 * 1024 * 1024  # ceiling for an explicit per-call max_bytes (bulk list downloads)


class EgressBlocked(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_METADATA = {"169.254.169.254", "fd00:ec2::254", "100.100.100.200"}


def _is_blocked_always(ip: ipaddress._BaseAddress, local: bool) -> bool:
    """Cloud-metadata and non-routable targets are never reachable. Generic link-local is blocked too, except for an
    explicitly configured local host (macOS resolves `localhost` to fe80::1%lo0)."""
    if str(ip) in _METADATA or ip.is_multicast or ip.is_unspecified:
        return True
    if local:
        return False
    return ip.is_link_local or ip.is_reserved


class _PinnedTransport(httpx.AsyncBaseTransport):
    """Connects to the IP that `_check` validated (no second DNS lookup => no rebinding window) while the request keeps
    its original Host header and TLS SNI / certificate-verification name."""

    def __init__(self, ips: str | list[str]) -> None:
        self._ips = [ips] if isinstance(ips, str) else list(ips)
        self._inner = httpx.AsyncHTTPTransport()

    @property
    def _ip(self) -> str:
        return self._ips[0]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Try every validated address in order (dual stack, e.g. localhost -> 127.0.0.1 then ::1). Only connection-level
        failures fall through to the next address; the last failure is re-raised. Never a second DNS lookup."""
        host = request.url.host
        last: Exception | None = None
        for ip in self._ips:
            req = httpx.Request(request.method, request.url.copy_with(host=ip), headers=request.headers,
                                extensions={**request.extensions, "sni_hostname": host})
            req.stream = request.stream  # replayable (bytes body) across attempts
            try:
                return await self._inner.handle_async_request(req)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last = e
        assert last is not None
        raise last

    async def aclose(self) -> None:
        await self._inner.aclose()


class SafeHttp:
    def __init__(self, allowed_hosts: set[str], local_hosts: set[str] | None = None, timeout_s: float = 10.0):
        self.allowed = {h.lower() for h in allowed_hosts}
        self.local = {h.lower() for h in (local_hosts or set())}
        self.timeout = timeout_s

    def update(self, allowed_hosts: set[str], local_hosts: set[str]) -> None:
        self.allowed = {h.lower() for h in allowed_hosts}
        self.local = {h.lower() for h in local_hosts}

    async def _check(self, url: str) -> list[str]:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        if u.scheme not in ("http", "https") or not host:
            raise EgressBlocked("BAD_URL")
        if host not in self.allowed and host not in self.local:
            raise EgressBlocked("HOST_NOT_ALLOWED")
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as e:
            raise EgressBlocked("DNS_FAILED") from e
        pinned: list[str] = []
        for info in infos:  # re-checked at call time (DNS rebinding)
            ip = ipaddress.ip_address(info[4][0])
            if _is_blocked_always(ip, host in self.local):
                raise EgressBlocked("IP_BLOCKED")
            if not ip.is_global and host not in self.local:  # private, loopback, CGNAT 100.64/10, etc.
                raise EgressBlocked("PRIVATE_IP_BLOCKED")
            if str(ip) not in pinned:
                pinned.append(str(ip))
        if not pinned:
            raise EgressBlocked("DNS_FAILED")
        return pinned

    async def request(self, method: str, url: str, *, max_bytes: int = MAX_BYTES, **kw) -> httpx.Response:
        cap = min(max(1, max_bytes), HARD_MAX_BYTES)
        ips = await self._check(url)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False, trust_env=False,
                                     transport=_PinnedTransport(ips)) as c:
            async with c.stream(method, url, **kw) as r:
                if r.is_redirect:
                    raise EgressBlocked("REDIRECT_BLOCKED")
                declared = r.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > cap:
                    raise EgressBlocked("RESPONSE_TOO_LARGE")
                buf = bytearray()
                async for chunk in r.aiter_bytes():  # decoded size is capped, aborting before the body is buffered
                    buf += chunk
                    if len(buf) > cap:
                        raise EgressBlocked("RESPONSE_TOO_LARGE")
                headers = [(k, v) for k, v in r.headers.multi_items()
                           if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
                return httpx.Response(r.status_code, headers=headers, content=bytes(buf), request=r.request,
                                      extensions=r.extensions)

    async def get(self, url: str, **kw) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def get_following(self, url: str, *, max_redirects: int = 3, max_bytes: int = MAX_BYTES, **kw) -> httpx.Response:
        """GET that follows redirects by hand: every hop must be https and on the allowlist and is re-resolved and
        IP-checked (so a redirect can never reach a host the caller did not allow). Raises REDIRECT_BLOCKED when a hop
        is not allowed or the chain is too long. Never log `url`: a hop may be a pre-signed URL."""
        for _ in range(max(0, max_redirects) + 1):
            u = urlparse(url)
            if u.scheme != "https":
                raise EgressBlocked("BAD_URL")
            ips = await self._check(url)
            cap = min(max(1, max_bytes), HARD_MAX_BYTES)
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False, trust_env=False,
                                         transport=_PinnedTransport(ips)) as c:
                async with c.stream("GET", url, **kw) as r:
                    if r.is_redirect:
                        loc = r.headers.get("location", "")
                        nxt = urljoin(url, loc)
                        nu = urlparse(nxt)
                        if not loc or nu.scheme != "https" or (nu.hostname or "").lower() not in self.allowed:
                            raise EgressBlocked("REDIRECT_BLOCKED")
                        url = nxt
                        continue
                    declared = r.headers.get("content-length")
                    if declared is not None and declared.isdigit() and int(declared) > cap:
                        raise EgressBlocked("RESPONSE_TOO_LARGE")
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) > cap:
                            raise EgressBlocked("RESPONSE_TOO_LARGE")
                    headers = [(k, v) for k, v in r.headers.multi_items()
                               if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
                    return httpx.Response(r.status_code, headers=headers, content=bytes(buf), request=r.request, extensions=r.extensions)
        raise EgressBlocked("REDIRECT_BLOCKED")

    async def post(self, url: str, **kw) -> httpx.Response:
        return await self.request("POST", url, **kw)
