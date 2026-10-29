"""ADV-14: audit chain tamper detection, audit-before-side-effect ordering, canonical serialisation."""
# ruff: noqa: S311  (seeded test RNG / literal 0.0.0.0 as attack input, not security-sensitive)
import random
from dataclasses import replace
from datetime import timedelta

import pytest

from adversarial.helpers import VALID, offer
from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.audit.chain import GENESIS, AuditLog, MemoryAuditStore, verify_chain
from sourceloop.bus.bus import MessageBus
from sourceloop.clock import FixedClock
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import StageEnum as S


async def make_chain(n=12, pid="p"):
    store = MemoryAuditStore()
    log = AuditLog(store, FixedClock())
    for i in range(n):
        await log.append(pid, actor_type="orchestrator", actor_id="o", action=f"a{i}", rule_id=f"R{i}",
                         evidence=[{"i": i, "nested": {"x": [1, 2, i]}}], outcome="OK", approver_id=None, config_version=1)
    return log, store, await store.read(pid)


async def test_adv14_clean_chain_verifies():
    log, _, recs = await make_chain()
    assert verify_chain(recs) == (True, None) and await log.verify("p") == (True, None)
    assert recs[0].prev_hash == GENESIS and all(recs[i].prev_hash == recs[i - 1].hash for i in range(1, len(recs)))


@pytest.mark.parametrize("field,value", [
    ("outcome", "HACKED"), ("action", "approval_resolved"), ("actor_id", "someone-else"), ("actor_type", "human"),
    ("rule_id", None), ("evidence", ()), ("evidence", ({"i": 4, "nested": {"x": [1, 2, 4]}, "extra": 1},)),
    ("approver_id", "cfo"), ("config_version", 2), ("model_id", "gpt"),
])
async def test_adv14_altering_any_field_is_detected(field, value):
    log, store, recs = await make_chain()
    store.tamper("p", 5, **{field: value})
    ok, at = await log.verify("p")
    assert not ok and at == 5


async def test_adv14_altering_ts_or_seq_is_detected():
    log, store, recs = await make_chain()
    store.tamper("p", 3, ts=recs[2].ts + timedelta(days=1))
    assert await log.verify("p") == (False, 3)
    log, store, recs = await make_chain()
    store._rows["p"][2] = replace(recs[2], seq=33)
    assert (await log.verify("p"))[0] is False
    log, store, recs = await make_chain()
    store._rows["p"][2] = replace(recs[2], project_id="other")
    assert await log.verify("p") == (False, 3)


async def test_adv14_altering_hash_or_prev_hash_is_detected():
    log, store, _ = await make_chain()
    store.tamper("p", 4, hash="f" * 64)
    assert (await log.verify("p"))[0] is False
    log, store, _ = await make_chain()
    store.tamper("p", 4, prev_hash="f" * 64)
    assert await log.verify("p") == (False, 4)


async def test_adv14_recomputing_one_record_hash_breaks_the_next_link():
    """A smart attacker fixes the tampered record's own hash; the successor's prev_hash then no longer matches."""
    from sourceloop.audit.chain import compute_hash
    log, store, recs = await make_chain()
    forged = replace(recs[4], outcome="HACKED")
    forged = replace(forged, hash=compute_hash(forged))
    store.tamper("p", 5, **{k: getattr(forged, k) for k in ("outcome", "hash")})
    ok, at = await log.verify("p")
    assert not ok and at == 6


@pytest.mark.parametrize("drop", [[0], [4], [3, 4, 5], [1, 5, 9]])
async def test_adv14_deleting_records_is_detected(drop):
    _, _, recs = await make_chain()
    kept = [r for i, r in enumerate(recs) if i not in drop]
    assert verify_chain(kept)[0] is False


async def test_adv14_truncating_the_middle_is_detected():
    _, _, recs = await make_chain(20)
    assert verify_chain(recs[:5] + recs[12:])[0] is False


async def test_adv14_reordering_is_detected():
    _, _, recs = await make_chain()
    swapped = list(recs)
    swapped[3], swapped[4] = swapped[4], swapped[3]
    assert verify_chain(swapped)[0] is False
    assert verify_chain(list(reversed(recs)))[0] is False


async def test_adv14_duplicating_a_record_is_detected():
    _, _, recs = await make_chain()
    assert verify_chain(recs[:5] + [recs[4]] + recs[5:])[0] is False


async def test_adv14_splicing_in_a_foreign_chain_is_detected():
    _, _, a = await make_chain(8, "p")
    _, _, b = await make_chain(8, "q")
    assert verify_chain(a[:4] + b[4:])[0] is False


async def test_adv14_property_random_single_mutation_always_detected():
    rnd = random.Random(2026)
    _, _, recs = await make_chain(25)
    mutations = [("outcome", lambda r: r.outcome + "x"), ("action", lambda r: r.action + "x"), ("actor_id", lambda r: r.actor_id + "x"),
                 ("rule_id", lambda r: (r.rule_id or "") + "x"), ("hash", lambda r: sha256_hex(r.hash)),
                 ("prev_hash", lambda r: sha256_hex(r.prev_hash)), ("evidence", lambda r: (*r.evidence, {"z": 1})),
                 ("config_version", lambda r: (r.config_version or 0) + 1), ("ts", lambda r: r.ts + timedelta(microseconds=1))]
    for _ in range(300):
        i = rnd.randrange(len(recs))
        field, fn = rnd.choice(mutations)
        mutated = list(recs)
        mutated[i] = replace(recs[i], **{field: fn(recs[i])})
        ok, at = verify_chain(mutated)
        assert not ok and at is not None and at <= recs[i].seq + 1


async def test_adv14_tail_truncation_is_detected():
    log, store, _ = await make_chain()
    store._rows["p"] = store._rows["p"][:6]
    ok, _ = await log.verify("p")
    assert not ok


async def test_adv14_empty_log_verifies_and_genesis_is_enforced():
    assert verify_chain([]) == (True, None)
    _, _, recs = await make_chain(3)
    assert verify_chain(recs[1:])[0] is False  # chain must start at seq 1 / GENESIS


async def test_adv14_audit_is_written_before_the_message_is_delivered(ring, buyer_mandate, supplier_mandate):
    """Rule 10: if the audit write fails the message must not be delivered."""
    class FailingStore(MemoryAuditStore):
        async def write(self, records):
            raise OSError("disk full")

    b = MessageBus("p1", AuditLog(FailingStore(), FixedClock()), ring, {"buyer": "buyer-org", "supplier:a": "sup-a"},
                   {"supplier:a": supplier_mandate}, clock=FixedClock())
    b.set_stage(S.SOURCING)
    env = b.build("supplier:a", "buyer", T.OFFER, offer("100"))
    with pytest.raises(OSError):
        await b.submit(env)
    assert b.log == [] and b.inbox("buyer", drain=False) == []
    b.audit.store = MemoryAuditStore()  # retry after the store recovers: not poisoned by the failed attempt
    b.audit._heads.clear()
    assert hasattr(await b.submit(env), "envelope")


async def test_adv14_audit_failure_in_policy_block_path_does_not_deliver(ring, supplier_mandate):
    class FailingStore(MemoryAuditStore):
        async def write(self, records):
            raise OSError("down")

    b = MessageBus("p1", AuditLog(FailingStore(), FixedClock()), ring, {"buyer": "buyer-org", "supplier:a": "sup-a"},
                   {"supplier:a": supplier_mandate}, clock=FixedClock())
    b.set_stage(S.SOURCING)
    with pytest.raises(OSError):
        await b.submit(b.build("supplier:a", "buyer", T.OFFER, offer("1")))
    assert b.log == []


def test_adv14_canonical_json_is_order_and_whitespace_independent():
    rnd = random.Random(7)
    keys = [f"k{i}" for i in range(30)]
    for _ in range(50):
        a = {k: {"z": 1, "a": [3, 2, {"y": 1, "b": 2}]} for k in rnd.sample(keys, len(keys))}
        b = {k: a[k] for k in sorted(a)}
        assert dumps(a) == dumps(b)
        assert ": " not in dumps(a) and ", " not in dumps(a)


def test_adv14_canonical_json_rejects_unknown_types_instead_of_guessing():
    with pytest.raises(TypeError):
        dumps({"x": object()})


def test_adv14_canonical_decimal_is_stringified_exactly():
    from decimal import Decimal
    assert dumps({"p": Decimal("1.10")}) == '{"p":"1.10"}' != dumps({"p": Decimal("1.1")})


def test_adv14_valid_payload_fixture_is_complete():
    assert set(VALID) == set(T)
