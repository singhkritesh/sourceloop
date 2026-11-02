"""Push a short "a decision is waiting" message to a chat webhook (Slack, Teams, ntfy and the like), so a person can answer from their phone.

Opt-in (NOTIFY_WEBHOOK_URL). The text names the decision and links to the chat page; it never carries prices, supplier names, limits or evidence.
Outbound HTTP goes through `net/safe_http` (host allowlist, no private ranges). A failure is logged by code and never affects the run."""
from __future__ import annotations

from urllib.parse import urlparse

import structlog

from sourceloop.net.safe_http import EgressBlocked, SafeHttp

log = structlog.get_logger()
_ROLE = {"BUYER": "the buyer", "SUPPLIER": "the supplier"}


async def notify_decision(webhook_url: str, app_base_url: str, title: str, role: str, http: SafeHttp | None = None) -> bool:
    host = (urlparse(webhook_url).hostname or "").lower()
    if not host:
        return False
    text = f"SourceLoop: \"{title}\" is waiting for {_ROLE.get(role, 'a person')}. Answer here: {app_base_url.rstrip('/')}/assistant"
    try:
        r = await (http or SafeHttp({host}, timeout_s=8.0)).request("POST", webhook_url, json={"text": text}, max_bytes=4096)
        return r.status_code < 300
    except EgressBlocked as e:
        log.warning("notify.blocked", code=str(e))
    except Exception as e:  # noqa: BLE001 - a notification problem must never touch the run
        log.warning("notify.failed", error=type(e).__name__)
    return False
