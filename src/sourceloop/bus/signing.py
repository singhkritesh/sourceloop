"""HMAC-SHA256 signing for envelopes and mandates, with per-org keys."""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from sourceloop.audit.canonical import dumps
from sourceloop.bus.messages import Envelope


@dataclass
class KeyRing:
    """org_id -> secret bytes. In production these come from a KMS; here from ORG_SIGNING_KEYS."""

    keys: dict[str, bytes]

    def _mac(self, org_id: str, body: object) -> str:
        key = self.keys[org_id]
        return hmac.new(key, dumps(body).encode(), hashlib.sha256).hexdigest()

    def sign_envelope(self, org_id: str, env: Envelope) -> Envelope:
        return env.model_copy(update={"signature": self._mac(org_id, env.signing_body())})

    def verify_envelope(self, org_id: str, env: Envelope) -> bool:
        if org_id not in self.keys or not env.signature:
            return False
        return hmac.compare_digest(env.signature, self._mac(org_id, env.signing_body()))

    def sign_payload(self, org_id: str, body: object) -> str:
        return self._mac(org_id, body)

    def verify_payload(self, org_id: str, body: object, sig: str) -> bool:
        if org_id not in self.keys:
            return False
        return hmac.compare_digest(sig, self._mac(org_id, body))
