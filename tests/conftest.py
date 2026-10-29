from decimal import Decimal

import pytest

from sourceloop.audit.chain import AuditLog, MemoryAuditStore
from sourceloop.bus.bus import AgentChannel, BusConfig, MessageBus
from sourceloop.bus.signing import KeyRing
from sourceloop.clock import FixedClock
from sourceloop.data.enums import StageEnum
from sourceloop.policy.mandates import BuyerMandate, SupplierMandate

CLAUSES = {
    "payment_terms": {"negotiable": True, "locked": False},
    "delivery": {"negotiable": True, "locked": False},
    "liability": {"negotiable": False, "locked": True},
    "termination": {"negotiable": False, "locked": True},
    "ip": {"negotiable": False, "locked": True},
}


@pytest.fixture
def ring():
    return KeyRing({"buyer-org": b"b" * 32, "sup-a": b"a" * 32, "sup-b": b"c" * 32, "orchestrator": b"o" * 32})


@pytest.fixture
def audit():
    store = MemoryAuditStore()
    return AuditLog(store, FixedClock())


@pytest.fixture
def buyer_mandate():
    return BuyerMandate(max_unit_price=Decimal("120"), max_lead_time_weeks=12, max_rounds=3,
                        excluded_countries=("RU",))


@pytest.fixture
def supplier_mandate():
    return SupplierMandate(unit_cost=Decimal("80"), min_margin_pct=Decimal("15"), max_capacity_units=10000,
                           lead_time_weeks=6, provable_certificates=("ISO9001",))


@pytest.fixture
def bus(ring, audit, buyer_mandate, supplier_mandate):
    b = MessageBus(
        "p1", audit, ring,
        participants={"buyer": "buyer-org", "supplier:a": "sup-a", "supplier:b": "sup-b"},
        mandates={"buyer": buyer_mandate, "supplier:a": supplier_mandate},
        config=BusConfig(clause_library=CLAUSES, config_version=1),
        supplier_countries={"supplier:a": "DE", "supplier:b": "RU"},
        clock=FixedClock(),
    )
    b.set_stage(StageEnum.SOURCING)
    return b


@pytest.fixture
def buyer(bus):
    return AgentChannel("buyer", bus)


@pytest.fixture
def sup_a(bus):
    return AgentChannel("supplier:a", bus)
