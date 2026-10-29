"""Per-project message bus. Validates, enforces the sender's mandate, audits, then delivers.

One bus instance per project means no global lock: parallel projects never contend, and inside a
project the critical section is CPU-only (validate + hash + in-memory append).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import itertools
import secrets
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.audit.chain import AuditLog
from sourceloop.bus.messages import (
    STAGE_TYPES,
    Envelope,
    Offer,
    Strict,
    safe_parse_payload,
)
from sourceloop.bus.signing import KeyRing
from sourceloop.clock import Clock, SystemClock
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import StageEnum
from sourceloop.policy.engine import Action, PolicyContext, check
from sourceloop.policy.mandates import Mandate

ORCHESTRATOR = "orchestrator"


def ai_generated(sender: str) -> bool:
    """EU AI Act Art. 50 style disclosure flag for the API/SSE VIEW layer (not part of the signed envelope or audit hash):
    every bus message not authored by the deterministic orchestrator comes from an AI agent (buyer / supplier:<id>)."""
    return sender != ORCHESTRATOR

# Which kind of sender may emit which type. "buyer" / "supplier" / "orchestrator".
SENDERS: dict[T, frozenset[str]] = {
    T.BRIEF: frozenset({"buyer"}),
    T.INVITE: frozenset({"buyer"}),
    T.INTEREST_REPLY: frozenset({"supplier"}),
    T.DOC_REQUEST: frozenset({"buyer"}),
    T.DOC_SUBMISSION: frozenset({"supplier"}),
    T.QUAL_RESULT: frozenset({"buyer", "orchestrator"}),
    T.RFQ: frozenset({"buyer"}),
    T.OFFER: frozenset({"supplier"}),
    T.COUNTER: frozenset({"buyer", "supplier"}),
    T.ACCEPT: frozenset({"buyer", "supplier"}),
    T.REJECT: frozenset({"buyer", "supplier"}),
    T.CONTRACT_DRAFT: frozenset({"buyer"}),
    T.REDLINE: frozenset({"buyer", "supplier"}),
    T.SIGN_REQUEST: frozenset({"buyer"}),
    T.ONBOARD_DATA: frozenset({"supplier"}),
    T.ESCALATE: frozenset({"buyer", "supplier", "orchestrator"}),
}


def agent_kind(agent_id: str) -> str:
    return "supplier" if agent_id.startswith("supplier:") else agent_id


@dataclass(frozen=True, slots=True)
class Delivered:
    envelope: Envelope
    audit_seq: int


@dataclass(frozen=True, slots=True)
class Rejected:
    """Reason is a code only. Never a threshold, never the counterparty's data."""

    code: str
    rule_id: str | None = None
    action: Action | None = None  # BLOCK -> MANDATE_BREACH escalation, ESCALATE -> human decision

    @property
    def needs_escalation(self) -> bool:
        return self.action in (Action.BLOCK, Action.ESCALATE)


SendResult = Delivered | Rejected


@dataclass
class BusConfig:
    index_price: Decimal | None = None
    price_band_pct: Decimal = Decimal("15")
    require_index_price: bool = False  # production runs always carry an index; missing => PRICE_BAND_NO_INDEX escalation
    clause_library: dict[str, dict[str, Any]] = field(default_factory=dict)
    extra_rules: list = field(default_factory=list)
    config_version: int | None = None
    quarantine_after: int | None = None   # rejected messages from one sender before it is quarantined (None = off)


class MessageBus:
    def __init__(
        self,
        project_id: str,
        audit: AuditLog,
        keyring: KeyRing,
        participants: dict[str, str],  # agent_id -> org_id (signing key owner)
        mandates: dict[str, Mandate],  # agent_id -> verified mandate (bus-private)
        config: BusConfig | None = None,
        supplier_countries: dict[str, str] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.project_id = project_id
        self.audit = audit
        self.keyring = keyring
        self.participants = {**participants, ORCHESTRATOR: ORCHESTRATOR}
        self._mandates = mandates
        self.cfg = config or BusConfig()
        self._countries = supplier_countries or {}
        self.clock = clock or SystemClock()
        self.stage: StageEnum = StageEnum.NEED
        self._seq = 0
        self._lock = asyncio.Lock()
        self._inbox: dict[str, list[Envelope]] = defaultdict(list)
        self._log: list[Envelope] = []
        self._offers: dict[UUID, Offer] = {}
        self._offer_parties: dict[UUID, tuple[str, str]] = {}  # offer id -> (author, recipient)
        self._last_price: dict[tuple[str, str], Decimal] = {}  # (sender, supplier_id) -> price
        self._seen_ids: set[UUID] = set()
        self._ids = itertools.count(1)
        # human-APPROVED waivers (ESCALATE-class only; BLOCK is never waivable). Entries are a bare rule id or a
        # (rule_id, sender, clause) scope tuple; every entry is SINGLE-USE (consumed by the message it waives).
        self.waivers: set[str | tuple[str, str | None, str | None]] = set()
        self._cap_key: bytes | None = None          # per-run identity key: random, in memory only, never persisted or logged
        self._strikes: dict[str, int] = defaultdict(int)
        self._quarantined: set[str] = set()

    # ---- per-run agent identity: "agent X of THIS project may send types T" ----
    def enable_identity(self) -> None:
        """After this, every message must carry a capability minted for its sender in this run. Defence in depth next to the org
        signature: a forged or replayed envelope that did not come through the agent's own channel is refused."""
        self._cap_key = secrets.token_bytes(32)

    def _cap_mac(self, agent_id: str, scopes: str) -> str:
        assert self._cap_key is not None
        return hmac.new(self._cap_key, f"{self.project_id}|{agent_id}|{scopes}".encode(), hashlib.sha256).hexdigest()

    def mint(self, agent_id: str) -> str | None:
        """Orchestrator-side. Scopes come from the same sender matrix the bus already enforces, so the token can only narrow it."""
        if self._cap_key is None:
            return None
        scopes = ",".join(sorted(t.value for t, kinds in SENDERS.items() if agent_kind(agent_id) in kinds))
        return f"{agent_id}.{scopes}.{self._cap_mac(agent_id, scopes)}"

    def _cap_ok(self, env: Envelope, cap: str | None) -> str | None:
        """None = fine, otherwise the rejection code."""
        if self._cap_key is None:
            return None
        try:
            agent, scopes, mac = (cap or "").rsplit(".", 2)
        except ValueError:
            return "IDENTITY_INVALID"
        if agent != env.from_agent or not hmac.compare_digest(mac, self._cap_mac(agent, scopes)):
            return "IDENTITY_INVALID"
        return None if env.type.value in scopes.split(",") else "SCOPE_DENIED"

    def grant_waiver(self, rule_id: str, sender: str | None = None, clause: str | None = None) -> tuple[str, str | None, str | None]:
        key = (rule_id, sender, clause)
        self.waivers.add(key)
        return key

    def revoke_waiver(self, key: object) -> None:
        self.waivers.discard(key)  # type: ignore[arg-type]

    def _consume_waiver(self, rule_id: str, sender: str, clause: str | None) -> bool:
        for key in ((rule_id, sender, clause), (rule_id, sender, None), (rule_id, None, clause), (rule_id, None, None), rule_id):
            if key in self.waivers:
                self.waivers.discard(key)
                return True
        return False

    # ---- read side (used by orchestrator; agents use AgentChannel) ----
    @property
    def log(self) -> list[Envelope]:
        return list(self._log)

    def inbox(self, agent_id: str, *, types: set[T] | None = None, sender: str | None = None,
              drain: bool = True) -> list[Envelope]:
        box = self._inbox[agent_id]
        out = [e for e in box if (types is None or e.type in types) and (sender is None or e.from_agent == sender)]
        if drain:
            self._inbox[agent_id] = [e for e in box if e not in out]
        return out

    def register(self, agent_id: str, org_id: str, mandate: Mandate | None = None, country: str | None = None) -> None:
        """Called by the orchestrator only (never by an agent) when a participant joins the project."""
        self.participants[agent_id] = org_id
        if mandate is not None:
            self._mandates[agent_id] = mandate
        if country is not None:
            self._countries[agent_id] = country

    def set_stage(self, stage: StageEnum) -> None:
        self.stage = stage

    def sign(self, agent_id: str, env: Envelope) -> Envelope:
        return self.keyring.sign_envelope(self.participants[agent_id], env)

    def build(self, from_agent: str, to_agent: str, type_: T, payload: Strict | dict[str, Any],
              in_reply_to: UUID | None = None) -> Envelope:
        body = payload.model_dump(mode="json") if isinstance(payload, Strict) else dict(payload)
        # deterministic ids: replay runs produce identical audit hash chains
        mid = uuid.uuid5(uuid.NAMESPACE_URL, f"sourceloop:{self.project_id}:{next(self._ids)}")
        env = Envelope(id=mid, project_id=self.project_id, stage=self.stage, type=type_, from_agent=from_agent,
                       to_agent=to_agent, payload=body, in_reply_to=in_reply_to)
        return self.sign(from_agent, env)

    # ---- the one write path ----
    async def submit(self, env: Envelope, cap: str | None = None) -> SendResult:
        async with self._lock:
            return await self._submit(env, cap)

    async def _reject(self, env: Envelope, code: str, rule_id: str | None = None,
                      action: Action | None = None) -> Rejected:
        await self.audit.append(
            self.project_id, actor_type="orchestrator", actor_id=ORCHESTRATOR,
            action="policy_block" if action else "message_rejected", rule_id=rule_id or code,
            evidence=[{"type": env.type.value, "from": env.from_agent, "to": env.to_agent}],
            outcome=(action.value if action else "REJECTED"), config_version=self.cfg.config_version,
        )
        n = self.cfg.quarantine_after
        if n is not None and code not in ("REPLAY", "QUARANTINED") and env.from_agent != ORCHESTRATOR:
            self._strikes[env.from_agent] += 1
            if self._strikes[env.from_agent] >= n and env.from_agent not in self._quarantined:
                self._quarantined.add(env.from_agent)   # audit first, then it takes effect on the very next message
                await self.audit.append(self.project_id, actor_type="orchestrator", actor_id=ORCHESTRATOR, action="participant_quarantined",
                                        rule_id="REPEATED_VIOLATIONS", evidence=[{"agent": env.from_agent}], outcome="QUARANTINED",
                                        config_version=self.cfg.config_version)
        return Rejected(code, rule_id, action)

    async def _submit(self, env: Envelope, cap: str | None = None) -> SendResult:
        if env.project_id != self.project_id:
            return await self._reject(env, "WRONG_PROJECT")
        if env.from_agent in self._quarantined:
            return await self._reject(env, "QUARANTINED")
        if env.id in self._seen_ids:
            return await self._reject(env, "REPLAY")
        if env.stage != self.stage:
            return await self._reject(env, "STAGE_MISMATCH")
        if env.type not in STAGE_TYPES[env.stage]:
            return await self._reject(env, "INVALID_MESSAGE_FOR_STAGE")
        if env.from_agent not in self.participants or env.to_agent not in self.participants:
            return await self._reject(env, "UNKNOWN_PARTICIPANT")
        if agent_kind(env.from_agent) not in SENDERS[env.type]:
            return await self._reject(env, "SENDER_NOT_ALLOWED")
        if not self.keyring.verify_envelope(self.participants[env.from_agent], env):
            return await self._reject(env, "BAD_SIGNATURE")
        if (bad := self._cap_ok(env, cap)) is not None:
            return await self._reject(env, bad)
        payload, err = safe_parse_payload(env.type, env.payload)
        if payload is None:
            return await self._reject(env, err or "INVALID_PAYLOAD")

        mandate = self._mandates.get(env.from_agent)
        if mandate is not None:
            res = check(env, payload, mandate, self._ctx(env, payload))
            if not res.passed:
                if (res.action is Action.ESCALATE and res.rule_id
                        and self._consume_waiver(res.rule_id, env.from_agent, getattr(payload, "clause_key", None))):
                    await self.audit.append(
                        self.project_id, actor_type="orchestrator", actor_id=ORCHESTRATOR, action="policy_waived",
                        rule_id=res.rule_id, evidence=[{"type": env.type.value, "from": env.from_agent}],
                        outcome="WAIVED_BY_HUMAN", config_version=self.cfg.config_version)
                else:
                    return await self._reject(env, "POLICY", res.rule_id, res.action)

        seq = self._seq + 1  # committed only after the audit append succeeds (audit "delivered" <=> bus-delivered)
        delivered = env.model_copy(update={"seq": seq, "ts": self.clock.now()})
        rec = await self.audit.append(
            self.project_id, actor_type="orchestrator", actor_id=ORCHESTRATOR, action="message_delivered",
            evidence=[{"id": str(env.id), "type": env.type.value, "from": env.from_agent, "to": env.to_agent,
                       "payload_hash": sha256_hex(dumps(env.payload))}],
            outcome="DELIVERED", config_version=self.cfg.config_version,
        )
        self._seq = seq
        self._seen_ids.add(env.id)
        self._log.append(delivered)
        self._inbox[env.to_agent].append(delivered)
        self._track(delivered, payload)
        return Delivered(delivered, rec.seq)

    # ---- helpers ----
    def _thread_key(self, env: Envelope) -> str:
        return env.to_agent if agent_kind(env.from_agent) == "buyer" else env.from_agent

    def _ctx(self, env: Envelope, payload: Strict) -> PolicyContext:
        ctx = PolicyContext(
            index_price=self.cfg.index_price, price_band_pct=self.cfg.price_band_pct,
            require_index=self.cfg.require_index_price,
            clause_library=self.cfg.clause_library, extra_block_rules=self.cfg.extra_rules,
        )
        if env.type == T.INVITE:
            ctx.supplier_country = self._countries.get(env.to_agent)
        if env.type == T.ACCEPT:
            ref = self._offers.get(getattr(payload, "offer_id", None))
            # an ACCEPT is valid only for an offer authored by the ACCEPT's recipient and addressed to its sender;
            # anything else is indistinguishable from an unknown offer (no counterparty data leaks)
            if ref is not None and self._offer_parties.get(payload.offer_id) != (env.to_agent, env.from_agent):  # type: ignore[attr-defined]
                ref = None
            ctx.referenced_offer = ref
        if env.type in (T.OFFER, T.COUNTER):
            ctx.previous_own_price = self._last_price.get((env.from_agent, self._thread_key(env)))
        return ctx

    def _track(self, env: Envelope, payload: Strict) -> None:
        if env.type in (T.OFFER, T.COUNTER) and isinstance(payload, Offer):
            self._offers[env.id] = payload
            self._offer_parties[env.id] = (env.from_agent, env.to_agent)
            self._last_price[(env.from_agent, self._thread_key(env))] = payload.unit_price


class AgentChannel:
    """The only handle an agent gets (the `bus.client` interface). Signing happens inside; the agent
    never sees keys, mandates of the other side, or other agents' inboxes."""

    def __init__(self, agent_id: str, bus: MessageBus) -> None:
        self.agent_id = agent_id
        self._bus = bus
        self._cap = bus.mint(agent_id)   # None unless the run enabled identity; the agent holds it opaquely and cannot widen it

    @property
    def stage(self) -> StageEnum:
        return self._bus.stage

    async def send(self, type_: T, to: str, payload: Strict | dict[str, Any],
                   in_reply_to: UUID | None = None) -> SendResult:
        env = self._bus.build(self.agent_id, to, type_, payload, in_reply_to)
        return await self._bus.submit(env, self._cap)

    def inbox(self, *, types: set[T] | None = None, drain: bool = True) -> list[Envelope]:
        return self._bus.inbox(self.agent_id, types=types, drain=drain)
