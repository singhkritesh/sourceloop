"""Second-review fixes: price-band fail-closed, orchestrator-level edit validation, Postgres trigger DDL, tamper guard,
audit export, reviewer override rates, DB-level approval version guard."""
import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.dialects import postgresql

from api_support import create_and_start, w  # noqa: F401
from sourceloop.api.deps import Principal
from sourceloop.api.errors import ApiError
from sourceloop.audit.chain import AuditLog, MemoryAuditStore
from sourceloop.clock import FixedClock
from sourceloop.data import db as dbm
from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.models import Approval, AuditEvent, Base, User, postgres_trigger_ddl
from sourceloop.orchestrator.approvals import ApprovalEditError, ApprovalService, sla_fields
from sourceloop.orchestrator.tamper_guard import TamperSuspected, assert_chain_ok
from sourceloop.policy.engine import Action, PolicyContext, _price_band

LONG = "a sufficiently long comment"


# ---------------------------------------------------------------- R2-15
def test_price_band_without_index_fails_closed_only_when_required():
    assert _price_band(Decimal("100"), PolicyContext()) is None  # no band check configured
    r = _price_band(Decimal("100"), PolicyContext(require_index=True))
    assert r is not None and r.action is Action.ESCALATE and r.rule_id == "PRICE_BAND_NO_INDEX"
    ctx = PolicyContext(index_price=Decimal("100"), require_index=True)
    assert _price_band(Decimal("100"), ctx) is None and _price_band(Decimal("200"), ctx).rule_id == "PRICE_BAND"  # unchanged


def test_scenario_buses_require_an_index():
    from sourceloop.bus.bus import BusConfig
    assert BusConfig().require_index_price is False
    import inspect

    from sourceloop.orchestrator import scenario
    assert "require_index_price=True" in inspect.getsource(scenario)


# ---------------------------------------------------------------- approvals edit validation (orchestrator level)
async def _svc_with(kind):
    s = ApprovalService(AuditLog(MemoryAuditStore(), FixedClock()))
    a = await s.ensure("p", kind, "k", ("R",))
    return s, a


@pytest.mark.parametrize("kind,edit", [(AK.AWARD, {}), (AK.COMPLIANCE_FLAG, {}), (AK.BANK_DETAILS, {"x": 1}), (AK.SIGNATURE, {"x": 1})])
async def test_edited_requires_nonempty_edit_for_a_kind_that_takes_one(kind, edit):
    s, a = await _svc_with(kind)
    with pytest.raises(ApprovalEditError):
        await s.resolve(a.id, AS.EDITED, "u", a.target_role, LONG, edit=edit)
    assert a.status is AS.PENDING and a.version == 1
    assert not [r for r in await s.audit.store.read("p") if r.action == "approval_resolved"]


async def test_edit_payload_only_with_edited():
    s, a = await _svc_with(AK.AWARD)
    with pytest.raises(ApprovalEditError):
        await s.resolve(a.id, AS.APPROVED, "u", a.target_role, "", edit={"supplier": "S1"})
    await s.resolve(a.id, AS.EDITED, "u", a.target_role, LONG, edit={"supplier": "S1"})
    assert a.status is AS.EDITED


def test_sla_fields_are_computed_not_stored():
    from datetime import UTC, datetime, timedelta
    t = datetime(2026, 10, 1, tzinfo=UTC)
    assert sla_fields(AK.AWARD, AS.PENDING, t, t + timedelta(hours=25))["overdue"] is True
    assert sla_fields(AK.AWARD, AS.PENDING, t, t + timedelta(hours=1))["overdue"] is False
    assert sla_fields(AK.AWARD, AS.APPROVED, t, t + timedelta(hours=99))["overdue"] is False


# ---------------------------------------------------------------- R2-14 (Postgres UNTESTED LIVE: DDL generation only)
def test_postgres_trigger_ddl_compiles_and_covers_immutable_tables():
    rows = postgres_trigger_ddl()
    assert rows[0][2].startswith("CREATE OR REPLACE FUNCTION sl_immutable_abort")
    from sqlalchemy import DDL
    for _t, _n, stmt in rows:
        str(DDL(stmt).compile(dialect=postgresql.dialect()))
    trig = {(t, n): stmt for t, n, stmt in rows[1:]}
    for t in ("audit_event", "mandate"):
        assert any(k[0] == t and "BEFORE UPDATE" in v for k, v in trig.items())
        assert any(k[0] == t and "BEFORE DELETE" in v for k, v in trig.items())
    pub = trig[("config_version", "config_version_frozen_update")]
    assert "OLD.status = 'PUBLISHED'" in pub and "IS DISTINCT FROM" in pub
    assert "%" not in "".join(s for _, _, s in rows)  # DDL() would treat % as a format char


def test_postgres_ddl_is_registered_only_for_postgresql_dialect():
    ddl_listeners = Base.metadata.tables["audit_event"].dispatch.after_create
    assert len(ddl_listeners) >= 4  # 2 sqlite + 2 postgres


def test_migration_0004_is_additive_and_dialect_guarded():
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).parent.parent / "alembic/versions/0004_postgres_immutability_triggers.py"
    src = p.read_text()
    assert "down_revision = '0003'" in src and 'dialect.name != "postgresql"' in src
    assert importlib.util.spec_from_file_location("m0004", p) is not None


# ---------------------------------------------------------------- G3 tamper guard + export
async def _tamper(pid, seq):
    async with dbm.get_sessionmaker()() as s:
        await s.execute(text("DROP TRIGGER IF EXISTS audit_event_no_update"))
        await s.execute(update(AuditEvent).where(AuditEvent.project_id == pid, AuditEvent.seq == seq).values(outcome="TAMPERED"))
        await s.commit()


async def test_tamper_guard_blocks_and_audits_once(w):  # noqa: F811
    pid = await create_and_start(w)
    sm = dbm.get_sessionmaker()
    await assert_chain_ok(sm, pid)  # healthy chain passes
    await _tamper(pid, 2)
    with pytest.raises(TamperSuspected) as e:
        await assert_chain_ok(sm, pid)
    assert e.value.first_bad_seq == 2 and e.value.code == "TAMPER_SUSPECTED"
    async with sm() as s:
        acts = [r for r in (await s.execute(select(AuditEvent).where(AuditEvent.project_id == pid).order_by(AuditEvent.seq))).scalars()]
    assert acts[-1].action == "tamper_suspected" and acts[-1].outcome == "RUN_BLOCKED"
    with pytest.raises(TamperSuspected):
        await assert_chain_ok(sm, pid)
    async with sm() as s:
        n = len([1 for r in (await s.execute(select(AuditEvent.action).where(AuditEvent.project_id == pid))).scalars() if r == "tamper_suspected"])
    assert n == 1  # not re-appended on every attempt


async def test_audit_export_ndjson_with_trailer_and_tamper_flag(w):  # noqa: F811
    pid = await create_and_start(w)
    ev = await w.client("evaluator")
    r = await ev.get(f"/projects/{pid}/audit/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(x) for x in r.text.splitlines()]
    assert all(x["type"] == "event" for x in lines[:-1]) and lines[-1]["type"] == "trailer"
    t = lines[-1]
    assert t["valid"] is True and t["anchor_verified"] is True and t["events"] == len(lines) - 1
    assert t["head_hash"] == lines[-2]["hash"] and t["anchor"]["hash"] == t["head_hash"]
    assert [x["seq"] for x in lines[:-1]] == list(range(1, len(lines)))
    await _tamper(pid, 2)
    t2 = json.loads((await (await w.client("admin")).get(f"/projects/{pid}/audit/export")).text.splitlines()[-1])
    assert t2["valid"] is False and t2["first_bad_seq"] == 2
    assert (await (await w.client("buyer")).get(f"/projects/{pid}/audit/export")).status_code == 403
    assert (await (await w.client("supplier1")).get(f"/projects/{pid}/audit/export")).status_code == 403
    assert (await (await w.client("evaluator")).get(f"/projects/{uuid.uuid4()}/audit/export")).status_code == 404


# ---------------------------------------------------------------- G2 reviewer override rates
async def test_override_rates_computed_from_resolved_approvals(w):  # noqa: F811
    pid = await create_and_start(w)
    b = await w.client("buyer")
    a = (await b.get(f"/approvals?status=PENDING&project_id={pid}")).json()["items"][0]
    assert (await b.post(f"/approvals/{a['id']}/resolve", {"status": "REJECTED", "version": a["version"], "comment": LONG})).status_code == 200
    ev = await w.client("evaluator")
    body = (await ev.get("/reviewers/override-rates")).json()
    mine = [i for i in body["items"] if i["resolver_id"] == str(w.ids["buyer"])]
    assert len(mine) == 1 and mine[0]["rejected"] == 1 and mine[0]["override_rate"] == 1.0 and mine[0]["flag"] is None
    assert mine[0]["by_kind"][0]["kind"] == a["kind"] and mine[0]["email"] == w.emails["buyer"]
    assert (await b.get("/reviewers/override-rates")).status_code == 403
    assert (await (await w.client("admin")).get("/reviewers/override-rates")).status_code == 200


# ---------------------------------------------------------------- mutant M15: DB-level compare-and-set on Approval.version
async def test_db_cas_rejects_stale_version_even_when_row_looks_current(w):  # noqa: F811
    """The in-memory pre-check passes (row object says version 1) but the DB row has moved on while still PENDING: only the
    `Approval.version == version` clause in the UPDATE can stop this. Removing it must fail this test."""
    pid = await create_and_start(w)
    sm = dbm.get_sessionmaker()
    async with sm() as s:
        row = (await s.execute(select(Approval).where(Approval.project_id == uuid.UUID(pid), Approval.status == AS.PENDING))).scalars().first()
        assert row is not None and row.version == 1
        row_id = row.id
        role_key = {"BUYER": "buyer"}.get(row.target_role.value, row.target_role.value.lower())
        user = await s.get(User, w.ids[role_key])
        p = Principal(user.id, user.email, user.role, user.org_id, "BUYER")
        async with sm() as other:  # a concurrent writer bumped the version but (hypothetically) left it PENDING
            await other.execute(update(Approval).where(Approval.id == row_id).values(version=2))
            await other.commit()
        with pytest.raises(ApiError) as e:
            await w.service.resolve_approval(s, p, row, AS.APPROVED, "", 1, {})
        assert e.value.status == 409 and e.value.code == "VERSION_CONFLICT"
    async with sm() as s:
        fresh = await s.get(Approval, row_id)
        assert fresh.status is AS.PENDING and fresh.version == 2 and fresh.resolver_id is None


def test_authz_matrix_has_rows_for_new_routes():
    import pathlib
    src = (pathlib.Path(__file__).parent / "test_authz_matrix.py").read_text()
    assert "/projects/{pid}/audit/export" in src and "/reviewers/override-rates" in src
