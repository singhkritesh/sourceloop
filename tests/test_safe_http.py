import pytest

from sourceloop.net.safe_http import EgressBlocked, SafeHttp


async def test_blocks():
    h = SafeHttp({"api.example.com", "169.254.169.254", "127.0.0.1"})
    for url, code in [("ftp://api.example.com/x", "BAD_URL"), ("https://evil.com/x", "HOST_NOT_ALLOWED"),
                      ("http://169.254.169.254/latest", "IP_BLOCKED"), ("http://127.0.0.1:9/x", "PRIVATE_IP_BLOCKED")]:
        with pytest.raises(EgressBlocked) as e:
            await h.get(url)
        assert e.value.code == code


async def test_pinned_transport_falls_back_across_validated_addresses():
    """R2-12: localhost -> 127.0.0.1 refused, then ::1 answers; only validated addresses are ever dialled."""
    import httpx

    from sourceloop.net.safe_http import _PinnedTransport

    dialled: list[str] = []

    class Inner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            dialled.append(request.url.host)
            if request.url.host == "127.0.0.1":
                raise httpx.ConnectError("refused")
            assert request.extensions["sni_hostname"] == "localhost"
            assert request.headers["host"].startswith("localhost")
            return httpx.Response(200, content=b"ok")

    t = _PinnedTransport(["127.0.0.1", "::1"])
    t._inner = Inner()
    async with httpx.AsyncClient(transport=t) as c:
        r = await c.post("http://localhost:8123/x", content=b"body")
    assert r.status_code == 200 and dialled == ["127.0.0.1", "::1"]


async def test_pinned_transport_all_fail_raises_last_error():
    import httpx

    from sourceloop.net.safe_http import _PinnedTransport

    class Inner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("refused")

    t = _PinnedTransport(["127.0.0.1", "::1"])
    t._inner = Inner()
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(httpx.ConnectError):
            await c.get("http://localhost:8123/x")
