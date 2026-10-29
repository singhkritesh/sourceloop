"""SQLAlchemy 2 models (TRD section 3).

Rules baked in: UUID PKs are generated client-side (no DB default), money is Numeric(14,4), enums are stored as exact
strings with a CHECK constraint, timestamps are UTC, `mandate` / `audit_event` / published `config_version` are
immutable (ORM guard + SQLite triggers).
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
    event,
    false,
    inspect,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from sourceloop.data import enums as E


class ImmutableError(Exception):
    code = "IMMUTABLE_ROW"


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo; we restore it)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def enum_col(cls: type[StrEnum], **kw: Any) -> Any:  
    return mapped_column(
        Enum(cls, native_enum=False, create_constraint=True, length=32, validate_strings=True,
             values_callable=lambda c: [m.value for m in c], name=f"ck_{cls.__name__.lower()}"), **kw)


def _uuid_pk() -> Any:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON, datetime: UTCDateTime}


# ---------------------------------------------------------------- identity
class Organization(Base):
    __tablename__ = "organization"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[E.OrgKindEnum] = enum_col(E.OrgKindEnum)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)  # supplier-side buyer rating 0..1
    override_version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ACTIVE org_override version (pointer only; the rows are immutable)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class User(Base):
    __tablename__ = "app_user"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[E.RoleEnum] = enum_col(E.RoleEnum)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class OrgOverride(Base):
    """An organization's own-side settings, layered on the platform configuration. INSERT-only and versioned: a project records the exact
    version it used, so a later edit never changes a running or restorable project."""
    __tablename__ = "org_override"
    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (UniqueConstraint("org_id", "version", name="uq_org_override_version"),)


class RefreshToken(Base):
    __tablename__ = "refresh_token"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the opaque token
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class RevokedToken(Base):
    """Access-token revocation list (survives restarts). Rows are pruned once `expires_at` has passed (the JWT is dead anyway)."""
    __tablename__ = "revoked_token"
    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    revoked_at: Mapped[datetime] = mapped_column(UTCDateTime)


# ---------------------------------------------------------------- registry
class Supplier(Base):
    __tablename__ = "supplier"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # registry id (e.g. S01253); not a UUID
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organization.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    name_norm: Mapped[str] = mapped_column(String(200), index=True)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    country: Mapped[str] = mapped_column(String(2))
    tax_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    categories: Mapped[list[Any]] = mapped_column(JSON, default=list)
    capacity_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_time_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    financial_risk: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    years_in_business: Mapped[int | None] = mapped_column(Integer, nullable=True)
    forced_labor_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    subtier_origin: Mapped[list[Any]] = mapped_column(JSON, default=list)
    network_status: Mapped[str] = mapped_column(String(20), default="member")
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # NOTE: no unit_cost column on purpose: supplier cost is mandate-private and never stored in the registry.


class Certificate(Base):
    __tablename__ = "certificate"
    id: Mapped[uuid.UUID] = _uuid_pk()
    supplier_id: Mapped[str] = mapped_column(ForeignKey("supplier.id"))
    type: Mapped[str] = mapped_column(String(40))
    issuer: Mapped[str] = mapped_column(String(100))
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_on: Mapped[date] = mapped_column(Date)  # DATE, UTC
    __table_args__ = (Index("ix_certificate_supplier_type", "supplier_id", "type"),)


class WatchlistEntry(Base):
    __tablename__ = "watchlist_entry"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    country: Mapped[str] = mapped_column(String(2))
    list_name: Mapped[str] = mapped_column(String(60))


class TransactionHistory(Base):
    __tablename__ = "transaction_history"
    supplier_id: Mapped[str] = mapped_column(ForeignKey("supplier.id"), primary_key=True)
    on_time_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    disputes_12m: Mapped[int] = mapped_column(Integer, default=0)


class BuyerProfile(Base):
    __tablename__ = "buyer_profile"
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), primary_key=True)
    payment_days_avg: Mapped[int] = mapped_column(Integer, default=45)
    disputes_12m: Mapped[int] = mapped_column(Integer, default=0)
    resource_churn_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0"))


class IndexPrice(Base):
    __tablename__ = "index_price"
    material: Mapped[str] = mapped_column(String(80), primary_key=True)
    month: Mapped[str] = mapped_column(String(7), primary_key=True)  # YYYY-MM
    value: Mapped[Decimal] = mapped_column(Numeric(14, 4))


# ---------------------------------------------------------------- config (Studio pinning)
class ConfigVersion(Base):
    """Immutable once PUBLISHED (only the status may move to ARCHIVED). Studio owns the body schema."""

    __tablename__ = "config_version"
    id: Mapped[uuid.UUID] = _uuid_pk()
    version: Mapped[int] = mapped_column(Integer, unique=True)  # monotonic
    content_hash: Mapped[str] = mapped_column(String(64))
    body: Mapped[dict[str, Any]] = mapped_column(JSON)  # full StudioConfig; secrets only as ids
    status: Mapped[E.ConfigStatusEnum] = enum_col(E.ConfigStatusEnum)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("config_version.id"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    note: Mapped[str] = mapped_column(String(500), default="")
    rollback_of: Mapped[int | None] = mapped_column(Integer, nullable=True)  # version this one is a re-validated copy of


class StudioDraft(Base):
    """One mutable draft per editor (`owner_id` = user id). Never read by the runtime; only published versions are."""

    __tablename__ = "studio_draft"
    owner_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    body: Mapped[dict[str, Any]] = mapped_column(JSON)
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # active version when the draft was started
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Provider(Base):
    __tablename__ = "provider"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[E.ProviderKindEnum] = enum_col(E.ProviderKindEnum)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_local: Mapped[bool] = mapped_column(Boolean, default=False)
    last_test_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_detail: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ProviderKey(Base):
    """Encrypted provider API key. Ciphertext only; only `studio.vault` ever reads/decrypts it, never an API model."""

    __tablename__ = "provider_key"
    id: Mapped[uuid.UUID] = _uuid_pk()
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("provider.id"), unique=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_id: Mapped[str] = mapped_column(String(40), default="k1")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)


class AppSetting(Base):
    """Single row, fixed id 1. Always upsert (db.upsert_setting); DB values win over env defaults."""

    __tablename__ = "app_setting"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    active_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    setup_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    token_budget_per_project: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())  # operators pause every run
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (CheckConstraint("id = 1", name="ck_app_setting_single_row"),)


# ---------------------------------------------------------------- sourcing
class Mandate(Base):
    """Immutable and signed. Thresholds inside `payload` never cross a side boundary."""

    __tablename__ = "mandate"
    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    side: Mapped[str] = mapped_column(String(10))  # buyer | supplier
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    signature: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (UniqueConstraint("org_id", "version", name="uq_mandate_org_version"),)


class Project(Base):
    __tablename__ = "project"
    id: Mapped[uuid.UUID] = _uuid_pk()
    buyer_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    need_text: Mapped[str] = mapped_column(Text, default="")
    brief: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stage: Mapped[E.StageEnum] = enum_col(E.StageEnum)
    status: Mapped[E.ProjectStatusEnum] = enum_col(E.ProjectStatusEnum)
    outcome: Mapped[E.OutcomeEnum | None] = enum_col(E.OutcomeEnum, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    buyer_mandate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mandate.id"), nullable=True)
    config_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("config_version.id"), nullable=True)
    llm_mode: Mapped[str] = mapped_column(String(10), default="replay")
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic lock
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __mapper_args__ = {"version_id_col": version}


class ProjectSupplier(Base):
    __tablename__ = "project_supplier"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("supplier.id"), primary_key=True)
    supplier_mandate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mandate.id"), nullable=True)
    qual_status: Mapped[E.QualStatusEnum | None] = enum_col(E.QualStatusEnum, nullable=True)
    qual_reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    round_count: Mapped[int] = mapped_column(Integer, default=0)


class Message(Base):
    __tablename__ = "message"
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    seq: Mapped[int] = mapped_column(Integer)
    stage: Mapped[E.StageEnum] = enum_col(E.StageEnum)
    type: Mapped[E.MessageTypeEnum] = enum_col(E.MessageTypeEnum)
    from_agent: Mapped[str] = mapped_column(String(60))
    to_agent: Mapped[str] = mapped_column(String(60))
    in_reply_to: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    signature: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    __table_args__ = (UniqueConstraint("project_id", "seq", name="uq_message_project_seq"),)


class Offer(Base):
    __tablename__ = "offer"
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("supplier.id"))
    round: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(10))  # buyer | supplier
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    lead_time_weeks: Mapped[int] = mapped_column(Integer)
    terms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("message.id"))


class SubagentCall(Base):
    __tablename__ = "subagent_call"
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"))
    ord: Mapped[int] = mapped_column(Integer)  # per-project order
    audit_seq: Mapped[int] = mapped_column(Integer)  # audit head when the call finished (SSE ordering)
    agent: Mapped[str] = mapped_column(String(10))  # buyer | supplier
    tool: Mapped[str] = mapped_column(String(60))
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_summary: Mapped[str] = mapped_column(String(300), default="")
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    model_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (UniqueConstraint("project_id", "ord", name="uq_subagent_call_order"),)


class Approval(Base):
    __tablename__ = "approval"
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"), index=True)
    core_id: Mapped[str] = mapped_column(String(20))  # orchestrator-side id (A0001...)
    key: Mapped[str] = mapped_column(String(200))
    kind: Mapped[E.ApprovalKindEnum] = enum_col(E.ApprovalKindEnum)
    stage: Mapped[str] = mapped_column(String(20), default="")
    target_role: Mapped[E.RoleEnum] = enum_col(E.RoleEnum)
    status: Mapped[E.ApprovalStatusEnum] = enum_col(E.ApprovalStatusEnum)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recommendation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    edit: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolver_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    comment: Mapped[str] = mapped_column(String(1000), default="")
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)  # version-locked: resolve is a compare-and-set
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (UniqueConstraint("project_id", "kind", "key", name="uq_approval_project_kind_key"),
                      UniqueConstraint("project_id", "core_id", name="uq_approval_project_core"))


class Clause(Base):
    __tablename__ = "clause"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40), default="commercial")
    negotiable: Mapped[bool] = mapped_column(Boolean)
    locked: Mapped[bool] = mapped_column(Boolean)


class Contract(Base):
    __tablename__ = "contract"
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"), unique=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("supplier.id"))
    clauses: Mapped[list[Any]] = mapped_column(JSON)
    deviations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    signature_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class LedgerHold(Base):
    __tablename__ = "ledger_hold"
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id"), unique=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("supplier.id"))
    status: Mapped[E.HoldStatusEnum] = enum_col(E.HoldStatusEnum, default=E.HoldStatusEnum.HELD)
    released_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class AuditEvent(Base):
    """INSERT only. `project_id` is a plain string (a project UUID, or 'system' for non-project events)."""

    __tablename__ = "audit_event"
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)  # per-project 1..n
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
    actor_type: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(60), index=True)
    rule_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)
    model_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(60), nullable=True)
    approver_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditAnchor(Base):
    """Head (seq, hash) of each audit stream, written in the same transaction as the events. `verify` compares it with
    the stored chain, so deleting the TAIL of a stream (which leaves a valid prefix) is detected. Can only move forward."""

    __tablename__ = "audit_anchor"
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


# ---------------------------------------------------------------- immutability
_IMMUTABLE_ALWAYS = (Mandate, AuditEvent, OrgOverride)


@event.listens_for(Session, "before_flush")
def _guard_immutable(session: Session, _ctx: Any, _instances: Any) -> None:
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, _IMMUTABLE_ALWAYS) and (obj in session.deleted or session.is_modified(obj)):
            raise ImmutableError(f"{type(obj).__name__} rows are INSERT-only")
    for obj in session.deleted:
        if isinstance(obj, ConfigVersion) and obj.status is E.ConfigStatusEnum.PUBLISHED:
            raise ImmutableError("published config_version is immutable")
    for obj in session.dirty:
        if isinstance(obj, ConfigVersion) and session.is_modified(obj):
            st = inspect(obj)
            was_published = bool(st.attrs.status.history.deleted) and \
                st.attrs.status.history.deleted[0] is E.ConfigStatusEnum.PUBLISHED
            frozen_changed = any(st.attrs[c].history.has_changes() for c in ("body", "content_hash", "version"))
            if (was_published or obj.status is E.ConfigStatusEnum.PUBLISHED) and frozen_changed:
                raise ImmutableError("published config_version is immutable")


_TRIGGERS = [
    ("audit_event", "audit_event_no_update", "BEFORE UPDATE", "", "audit_event is INSERT only"),
    ("audit_event", "audit_event_no_delete", "BEFORE DELETE", "", "audit_event is INSERT only"),
    ("mandate", "mandate_no_update", "BEFORE UPDATE", "", "mandate is immutable"),
    ("mandate", "mandate_no_delete", "BEFORE DELETE", "", "mandate is immutable"),
    ("config_version", "config_version_frozen_update", "BEFORE UPDATE",
     "WHEN OLD.status = 'PUBLISHED' AND (NEW.body IS NOT OLD.body OR NEW.content_hash IS NOT OLD.content_hash "
     "OR NEW.version IS NOT OLD.version)", "published config_version is immutable"),
    ("config_version", "config_version_frozen_delete", "BEFORE DELETE", "WHEN OLD.status = 'PUBLISHED'",
     "published config_version is immutable"),
]


_TRIGGERS_V2 = [
    ("audit_anchor", "audit_anchor_no_regress", "BEFORE UPDATE", "WHEN NEW.seq < OLD.seq", "audit_anchor can only move forward"),
    ("audit_anchor", "audit_anchor_no_delete", "BEFORE DELETE", "", "audit_anchor is never deleted"),
]


_TRIGGERS_V3 = [
    ("org_override", "org_override_no_update", "BEFORE UPDATE", "", "org_override is immutable"),
    ("org_override", "org_override_no_delete", "BEFORE DELETE", "", "org_override is immutable"),
]


def sqlite_trigger_ddl_v3() -> list[tuple[str, str, str]]:
    """Triggers added by migration 0006 (kept apart so earlier revisions stay reproducible)."""
    return [(t, n, f"CREATE TRIGGER IF NOT EXISTS {n} {when} ON {t} {cond} "
                   f"BEGIN SELECT RAISE(ABORT, '{msg}'); END")
            for t, n, when, cond, msg in _TRIGGERS_V3]


def sqlite_trigger_ddl_v2() -> list[tuple[str, str, str]]:
    """Triggers added by migration 0002 (kept apart so revision 0001 stays reproducible)."""
    return [(t, n, f"CREATE TRIGGER IF NOT EXISTS {n} {when} ON {t} {cond} "
                   f"BEGIN SELECT RAISE(ABORT, '{msg}'); END")
            for t, n, when, cond, msg in _TRIGGERS_V2]


def sqlite_trigger_ddl() -> list[tuple[str, str, str]]:
    """(table, name, CREATE TRIGGER statement) for SQLite; shared by metadata.create_all and the Alembic migration."""
    return [(t, n, f"CREATE TRIGGER IF NOT EXISTS {n} {when} ON {t} {cond} "
                   f"BEGIN SELECT RAISE(ABORT, '{msg}'); END")
            for t, n, when, cond, msg in _TRIGGERS]


for _t, _n, _stmt in sqlite_trigger_ddl():
    event.listen(Base.metadata.tables[_t], "after_create", DDL(_stmt).execute_if(dialect="sqlite"))
for _t, _n, _stmt in sqlite_trigger_ddl_v2():
    event.listen(Base.metadata.tables[_t], "after_create", DDL(_stmt).execute_if(dialect="sqlite"))
for _t, _n, _stmt in sqlite_trigger_ddl_v3():
    event.listen(Base.metadata.tables[_t], "after_create", DDL(_stmt).execute_if(dialect="sqlite"))


# ---- PostgreSQL parity (R2-14). UNTESTED LIVE: only the generated DDL is compile-checked in tests (no Postgres in CI).
PG_ABORT_FN = ("CREATE OR REPLACE FUNCTION sl_immutable_abort() RETURNS trigger LANGUAGE plpgsql AS $$ "
               "BEGIN RAISE EXCEPTION USING MESSAGE = TG_ARGV[0], ERRCODE = 'integrity_constraint_violation'; END; $$")
_PG_FROZEN = ("(OLD.status = 'PUBLISHED' AND (NEW.body IS DISTINCT FROM OLD.body OR NEW.content_hash IS DISTINCT FROM "
              "OLD.content_hash OR NEW.version IS DISTINCT FROM OLD.version))")
_PG_TRIGGERS = [  # (table, name, event, WHEN condition or "", message)
    ("audit_event", "audit_event_no_update", "UPDATE", "", "audit_event is INSERT only"),
    ("audit_event", "audit_event_no_delete", "DELETE", "", "audit_event is INSERT only"),
    ("mandate", "mandate_no_update", "UPDATE", "", "mandate is immutable"),
    ("mandate", "mandate_no_delete", "DELETE", "", "mandate is immutable"),
    ("config_version", "config_version_frozen_update", "UPDATE", _PG_FROZEN, "published config_version is immutable"),
    ("config_version", "config_version_frozen_delete", "DELETE", "(OLD.status = 'PUBLISHED')", "published config_version is immutable"),
    ("audit_anchor", "audit_anchor_no_regress", "UPDATE", "(NEW.seq < OLD.seq)", "audit_anchor can only move forward"),
    ("audit_anchor", "audit_anchor_no_delete", "DELETE", "", "audit_anchor is never deleted"),
    ("org_override", "org_override_no_update", "UPDATE", "", "org_override is immutable"),
    ("org_override", "org_override_no_delete", "DELETE", "", "org_override is immutable"),
]


def postgres_trigger_ddl() -> list[tuple[str, str, str]]:
    """(table, name, statement) for PostgreSQL (>= 14 for CREATE OR REPLACE TRIGGER). The first row is the shared function."""
    out = [("", "sl_immutable_abort", PG_ABORT_FN)]
    for t, n, ev, when, msg in _PG_TRIGGERS:
        w = f" WHEN {when}" if when else ""
        out.append((t, n, f"CREATE OR REPLACE TRIGGER {n} BEFORE {ev} ON {t} FOR EACH ROW{w} "
                          f"EXECUTE FUNCTION sl_immutable_abort('{msg}')"))
    return out


for _t, _n, _stmt in postgres_trigger_ddl():
    if _t:  # triggers attach after the table exists; the function must exist first (before_create of the first table)
        event.listen(Base.metadata.tables[_t], "after_create", DDL(_stmt).execute_if(dialect="postgresql"))
event.listen(Base.metadata, "before_create", DDL(PG_ABORT_FN).execute_if(dialect="postgresql"))
