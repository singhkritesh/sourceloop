"""The push to a chat webhook says a decision is waiting, links to the chat page, and carries no business data; failures never raise."""
import httpx

from sourceloop.api.notify import notify_decision
from sourceloop.net.safe_http import EgressBlocked


class FakeHttp:
    def __init__(self, status=200, raises=None):
        self.sent, self.status, self.raises = [], status, raises

    async def request(self, method, url, **kw):
        if self.raises:
            raise self.raises
        self.sent.append((method, url, kw["json"]))
        return httpx.Response(self.status)


async def test_message_names_the_decision_and_links_to_the_chat_page_only():
    http = FakeHttp()
    assert await notify_decision("https://hooks.example.test/x", "https://app.example.test/", "Award", "BUYER", http)
    (method, url, body), = http.sent
    assert method == "POST" and url == "https://hooks.example.test/x"
    assert body == {"text": 'SourceLoop: "Award" is waiting for the buyer. Answer here: https://app.example.test/assistant'}


async def test_a_blocked_or_failing_webhook_returns_false_and_never_raises():
    assert not await notify_decision("https://hooks.example.test/x", "http://a", "Award", "BUYER", FakeHttp(raises=EgressBlocked("PRIVATE_IP_BLOCKED")))
    assert not await notify_decision("https://hooks.example.test/x", "http://a", "Award", "BUYER", FakeHttp(raises=RuntimeError("boom")))
    assert not await notify_decision("https://hooks.example.test/x", "http://a", "Award", "BUYER", FakeHttp(status=500))
    assert not await notify_decision("not a url", "http://a", "Award", "BUYER", FakeHttp())
