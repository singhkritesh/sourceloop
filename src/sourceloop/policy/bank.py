"""Bank details check before payment is released: pure functions, no I/O.

The supplier's own onboarding details are checked against what the platform already knows (the registered name) and against plain format rules,
so the person releasing payment sees concrete results instead of a promise. The full account number never leaves the onboarding message: only the
last four characters are kept in approvals, the audit and the screen. An EXTERNAL verification (a bank or a verification service) is not wired in
this build: `external` says so, and the screen tells the person to confirm the account out of band. A failed check blocks the approval."""
from __future__ import annotations

import re
from dataclasses import dataclass

ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{5,33}$")
EXTERNAL_NOT_WIRED = "NOT_CONFIGURED"   # TODO(wire): a bank-account verification connector (see connectors/stubs.py); until then the person confirms out of band


@dataclass(frozen=True)
class BankCheck:
    ok: bool
    checks: tuple[tuple[str, bool], ...]
    masked: str | None
    external: str

    def as_evidence(self) -> dict[str, object]:
        return {"ok": self.ok, "checks": [{"check": c, "passed": p} for c, p in self.checks], "account_ending": self.masked, "external_verification": self.external}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def mask(account: str) -> str:
    a = re.sub(r"[\s-]", "", account)   # spaces and hyphens are layout, not part of the number
    return a[-4:] if len(a) >= 4 else a


def check(fields: dict[str, str], registered_name: str) -> BankCheck:
    account = str(fields.get("bank_account", "")).strip()
    present = bool(account)
    results = (
        ("ACCOUNT_PRESENT", present),
        ("ACCOUNT_FORMAT", present and bool(ACCOUNT.match(account))),
        ("NAME_MATCHES_REGISTRY", bool(registered_name) and _norm(str(fields.get("legal_name", ""))) == _norm(registered_name)),
    )
    return BankCheck(all(p for _, p in results), results, mask(account) if present else None, EXTERNAL_NOT_WIRED)
