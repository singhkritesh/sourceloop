"""Persistence layer: single engine, PRAGMAs, client-side UUIDs, immutability, SQL audit store."""
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError
from sqlalchemy.orm.exc import StaleDataError

from api_support import make_settings
from sourceloop.audit.chain import AuditLog, verify_chain
from sourceloop.clock import FixedClock
from sourceloop.data import db as dbm
from sourceloop.data.audit_store import SqlAuditStore
from sourceloop.data.enums import ConfigStatusEnum, OrgKindEnum, ProjectStatusEnum, StageEnum
from sourceloop.data.models import (
    AppSetting,
    AuditEvent,
    ConfigVersion,
    ImmutableError,
    Mandate,
    Message,
    Organization,
    Project,
    User,
)

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
async def database(tmp_path):
    dbm.configure(make_settings(tmp_path).database_url)
    await dbm.create_all()
    yield
    await dbm.dispose()


async def _org_user_project(s):
    org = Organization(name="o", kind=OrgKindEnum.BUYER, created_at=NOW)
    s.add(org)
    await s.flush()
    user = User(email="x@sourceloop.test", password_hash="h", role="BUYER", org_id=org.id, created_at=NOW)
    s.add(user)
    await s.flush()
    proj = Project(buyer_org_id=org.id, title="t", stage=StageEnum.NEED, status=ProjectStatusEnum.DRAFT,
                   created_by=user.id, created_at=NOW, updated_at=NOW)
    s.add(proj)
    await s.flush()
    return org, user, proj


async def test_foreign_keys_enforced_and_uuid_client_side(database):
    async with dbm.session_scope() as s:
        org, _u, proj = await _org_user_project(s)
        assert isinstance(org.id, uuid.UUID) and isinstance(proj.id, uuid.UUID)  # generated before any DB round trip
    async with dbm.get_sessionmaker()() as s:
        assert (await s.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
        s.add(User(email="y@sourceloop.test", password_hash="h", role="BUYER", org_id=uuid.uuid4(), created_at=NOW))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_enum_check_constraint_and_decimal_money(database):
    async with dbm.get_sessionmaker()() as s:
        org, user, proj = await _org_user_project(s)
        with pytest.raises(StatementError):  # exact enum strings: a typo cannot be stored
            s.add(Message(project_id=proj.id, seq=1, stage="Discovery", type="BRIEF", from_agent="a", to_agent="b",
                          payload={}, signature="x"))
            await s.flush()
        await s.rollback()
    from sourceloop.data.models import IndexPrice
    async with dbm.session_scope() as s:
        s.add(IndexPrice(material="pcb", month="2026-10", value=Decimal("94.5000")))
    async with dbm.get_sessionmaker()() as s:
        v = (await s.execute(select(IndexPrice.value))).scalar_one()
        assert isinstance(v, Decimal) and v == Decimal("94.5000")


async def test_project_optimistic_lock_and_message_unique_seq(database):
    async with dbm.session_scope() as s:
        _o, _u, proj = await _org_user_project(s)
        pid = proj.id
    async with dbm.get_sessionmaker()() as a, dbm.get_sessionmaker()() as b:
        pa, pb = await a.get(Project, pid), await b.get(Project, pid)
        pa.title = "first"
        await a.commit()
        pb.title = "second"
        with pytest.raises(StaleDataError):
            await b.commit()
    async with dbm.get_sessionmaker()() as s:
        assert (await s.get(Project, pid)).version == 2
        s.add(Message(project_id=pid, seq=1, stage="NEED", type="BRIEF", from_agent="a", to_agent="b", payload={}, signature="x"))
        s.add(Message(project_id=pid, seq=1, stage="NEED", type="BRIEF", from_agent="a", to_agent="b", payload={}, signature="x"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_app_setting_upsert_single_row(database):
    async with dbm.session_scope() as s:
        await dbm.upsert_setting(s, NOW, llm_mode="replay")
    async with dbm.session_scope() as s:
        await dbm.upsert_setting(s, NOW, token_budget_per_project=5000)
    async with dbm.get_sessionmaker()() as s:
        rows = (await s.execute(select(AppSetting))).scalars().all()
        assert len(rows) == 1 and rows[0].id == 1 and rows[0].llm_mode == "replay" and rows[0].token_budget_per_project == 5000
        s.add(AppSetting(id=2, updated_at=NOW))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_audit_store_roundtrip_verifies_and_is_immutable(database):
    log = AuditLog(SqlAuditStore(), FixedClock())
    for i in range(5):
        await log.append("proj-1", actor_type="orchestrator", actor_id="orchestrator", action=f"a{i}",
                         evidence=[{"price": Decimal("12.3400"), "ids": (1, 2)}], outcome="ok", config_version=1)
    assert await log.verify("proj-1") == (True, None)
    fresh = AuditLog(SqlAuditStore(), FixedClock())  # new instance: head comes from the DB, chain continues
    rec = await fresh.append("proj-1", actor_type="human", actor_id="u", action="more")
    assert rec.seq == 6 and await fresh.verify("proj-1") == (True, None)
    # INSERT only: ORM guard and DB trigger both refuse updates / deletes
    async with dbm.get_sessionmaker()() as s:
        ev = (await s.execute(select(AuditEvent).where(AuditEvent.seq == 2))).scalar_one()
        ev.action = "tampered"
        with pytest.raises(ImmutableError):
            await s.flush()
        await s.rollback()
        with pytest.raises(DBAPIError):
            await s.execute(text("UPDATE audit_event SET action='x' WHERE seq=2"))
        await s.rollback()
        with pytest.raises(DBAPIError):
            await s.execute(text("DELETE FROM audit_event"))
        await s.rollback()
    # if someone does bypass the guards (trigger dropped), verification detects it
    async with dbm.session_scope() as s:
        await s.execute(text("DROP TRIGGER audit_event_no_update"))
        await s.execute(text("UPDATE audit_event SET outcome='forged' WHERE seq=3 AND project_id='proj-1'"))
    ok, bad = verify_chain(await SqlAuditStore().read("proj-1"))
    assert (ok, bad) == (False, 3)


async def test_mandate_and_published_config_are_immutable(database):
    async with dbm.session_scope() as s:
        org = Organization(name="o", kind=OrgKindEnum.BUYER, created_at=NOW)
        s.add(org)
        await s.flush()
        m = Mandate(org_id=org.id, side="buyer", version=1, payload={"max": "1"}, signature="s", created_at=NOW)
        cv = ConfigVersion(version=1, content_hash="h", body={"a": 1}, status=ConfigStatusEnum.PUBLISHED, created_at=NOW)
        s.add_all([m, cv])
        await s.flush()
        mid, cid = m.id, cv.id
    async with dbm.get_sessionmaker()() as s:
        (await s.get(Mandate, mid)).payload = {"max": "999"}
        with pytest.raises(ImmutableError):
            await s.flush()
        await s.rollback()
        cv = await s.get(ConfigVersion, cid)
        cv.body = {"a": 2}
        with pytest.raises(ImmutableError):
            await s.flush()
        await s.rollback()
        cv = await s.get(ConfigVersion, cid)
        cv.status = ConfigStatusEnum.ARCHIVED  # the only allowed transition
        await s.commit()
        with pytest.raises(DBAPIError):
            await s.execute(text("UPDATE mandate SET signature='x'"))
