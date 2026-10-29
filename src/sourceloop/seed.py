"""Seed demo data: synthetic registry, clause library, demo orgs and one user per role.

    python -m sourceloop.seed            # idempotent; prints generated credentials ONCE (existing users are left untouched)

Passwords are random per run and never stored in plain text; they exist only in this program's output.
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import uuid
from decimal import Decimal

from sqlalchemy import func, select

from sourceloop.api.config import load_settings
from sourceloop.api.security import Passwords
from sourceloop.api.service import build_spec, registry, supplier_org_id
from sourceloop.api.studio_support import ensure_default_config
from sourceloop.clock import SystemClock
from sourceloop.data import db as dbm
from sourceloop.data.clauses import DEFAULT_CLAUSES
from sourceloop.data.enums import OrgKindEnum
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import (
    BuyerProfile,
    Certificate,
    Clause,
    IndexPrice,
    Organization,
    Supplier,
    TransactionHistory,
    User,
    WatchlistEntry,
)
from sourceloop.data.synthetic import TODAY
from sourceloop.orchestrator.scenario import build_project, run_to_completion

DOMAIN = "sourceloop.test"
BUYER_ROLES = (R.BUYER, R.COMPLIANCE, R.QUALITY, R.LEGAL, R.FINANCE)


async def _load_registry(s, seed: int, now) -> int:
    if (await s.execute(select(func.count()).select_from(Supplier))).scalar_one() > 0:
        return 0
    vis, _full = registry(seed)
    for r in vis.suppliers:
        s.add(Supplier(id=r.id, name=r.name, name_norm=r.name.lower(), aliases=list(r.aliases), country=r.country, tax_id=r.tax_id,
                       categories=list(r.categories), capacity_units=r.capacity_units, lead_time_weeks=r.lead_time_weeks,
                       financial_risk=None if r.financial_risk is None else Decimal(str(r.financial_risk)),
                       years_in_business=r.years_in_business, forced_labor_flag=r.forced_labor_flag,
                       subtier_origin=list(r.subtier_origin), network_status=r.network_status))
    await s.flush()
    for r in vis.suppliers:
        for c in r.certs:
            s.add(Certificate(supplier_id=r.id, type=c.type, issuer=c.issuer, expires_on=c.expires_on))
        if r.on_time_pct is not None or r.quality_score is not None:
            s.add(TransactionHistory(
                supplier_id=r.id, on_time_pct=None if r.on_time_pct is None else Decimal(str(r.on_time_pct)),
                quality_score=None if r.quality_score is None else Decimal(str(r.quality_score))))
    for w in vis.watchlist:
        s.add(WatchlistEntry(name=w.name, aliases=list(w.aliases), country=w.country, list_name=w.list_name))
    for material, value in vis.index_price.items():
        s.add(IndexPrice(material=material, month=TODAY.strftime("%Y-%m"), value=value))
    await s.flush()
    return len(vis.suppliers)


async def seed(seed_value: int = 42) -> list[tuple[str, str, str]]:
    cfg = load_settings()
    pw = Passwords(cfg)
    now = SystemClock().now()
    creds: list[tuple[str, str, str]] = []
    base = await run_to_completion(build_project(await build_spec("happy", 0, seed_value), *registry(seed_value)))
    demo_suppliers = (base.shortlist or [])[:2]
    async with dbm.session_scope() as s:
        n = await _load_registry(s, seed_value, now)
        for key, d in DEFAULT_CLAUSES.items():
            if await s.get(Clause, (key, 1)) is None:
                s.add(Clause(key=key, version=1, title=d["title"], text="", category="legal" if d["locked"] else "commercial",
                             negotiable=d["negotiable"], locked=d["locked"]))
        orgs: dict[str, Organization] = {}
        for name, kind in (("Acme Industrial Buyer", OrgKindEnum.BUYER), ("Globex Procurement", OrgKindEnum.BUYER),
                           ("SourceLoop Platform", OrgKindEnum.BUYER)):
            o = (await s.execute(select(Organization).where(Organization.name == name))).scalar_one_or_none()
            if o is None:
                o = Organization(name=name, kind=kind, created_at=now)
                s.add(o)
                await s.flush()
                if kind is OrgKindEnum.BUYER and name != "SourceLoop Platform":
                    s.add(BuyerProfile(org_id=o.id))
            orgs[name] = o
        plan: list[tuple[str, R, Organization]] = [(f"{r.value.lower()}@{DOMAIN}", r, orgs["Acme Industrial Buyer"]) for r in BUYER_ROLES]
        plan.append((f"buyer2@{DOMAIN}", R.BUYER, orgs["Globex Procurement"]))
        plan += [(f"{r.value.lower()}@{DOMAIN}", r, orgs["SourceLoop Platform"]) for r in (R.EVALUATOR, R.DESIGNER)]
        plan.append((f"superadmin@{DOMAIN}", R.SUPER_ADMIN, orgs["SourceLoop Platform"]))  # SBN operator: all access
        plan.append((f"buyeradmin@{DOMAIN}", R.ADMIN, orgs["Acme Industrial Buyer"]))  # organization admin of the buyer org
        for i, sid in enumerate(demo_suppliers, start=1):
            org_id = supplier_org_id(sid)
            sup = await s.get(Supplier, sid)
            assert sup is not None
            o = await s.get(Organization, org_id)
            if o is None:
                o = Organization(id=org_id, name=sup.name, kind=OrgKindEnum.SUPPLIER, created_at=now)
                s.add(o)
                await s.flush()
            sup.org_id = org_id
            plan.append((f"supplier{i}@{DOMAIN}", R.SUPPLIER, o))
            plan.append((f"supplier{i}admin@{DOMAIN}", R.ADMIN, o))  # organization admin of that supplier organization
        for email, role, org in plan:
            if (await s.execute(select(User.id).where(User.email == email))).first():
                creds.append((email, role.value, "(already exists - password unchanged)"))
                continue
            password = secrets.token_urlsafe(14)
            s.add(User(id=uuid.uuid4(), email=email, password_hash=pw.hash(password), role=role, org_id=org.id, created_at=now))
            creds.append((email, role.value, password))
    version = await ensure_default_config(cfg)  # config v1 = default_config() if no version exists yet
    print(f"registry rows loaded: {n}; demo supplier ids: {demo_suppliers}; active studio config version: {version}")
    return creds


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--create-schema", action="store_true", help="create tables directly (dev shortcut; prefer alembic upgrade head)")
    a = ap.parse_args()

    async def _run() -> list[tuple[str, str, str]]:
        dbm.configure(load_settings().database_url)
        if a.create_schema:
            await dbm.create_all()
        try:
            return await seed(a.seed)
        finally:
            await dbm.dispose()

    creds = asyncio.run(_run())
    print("\nSTART HERE: open http://localhost:3010 and sign in as buyer@sourceloop.test (buyer view),")
    print("supplier1@sourceloop.test (supplier view) or superadmin@sourceloop.test (everything). Then click 'Run a sample project'.")
    print("\nDemo credentials (generated now; shown once; local dev only):")
    print(f"{'email':<34}{'role':<12}password")
    for e, r, p in creds:
        print(f"{e:<34}{r:<12}{p}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
