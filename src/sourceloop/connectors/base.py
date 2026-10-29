"""Connector contracts. Strict models (extra=forbid): external data is untrusted and is validated at this boundary.
A connector that cannot deliver raises ConnectorError(code); callers lower confidence / escalate. Blank never means pass."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class Capability(StrEnum):
    SANCTIONS = "SANCTIONS"
    WEATHER = "WEATHER"
    NEWS = "NEWS"
    SUPPLIER_PROFILE = "SUPPLIER_PROFILE"
    FINANCIAL_RISK = "FINANCIAL_RISK"
    CERTIFICATES = "CERTIFICATES"
    CONTRACT_CLAUSES = "CONTRACT_CLAUSES"


class Status(StrEnum):
    LIVE = "LIVE"            # implemented and reaches a real public source
    STUB = "STUB"            # contract defined, fails closed with NOT_CONFIGURED until wired to a real system
    SYNTHETIC = "SYNTHETIC"  # the built-in generated / recorded data the prototype runs on


class ConnectorError(Exception):
    """Short machine code only: never upstream text, URLs or keys."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ConnectorInfo:
    id: str
    capability: Capability
    provider: str
    status: Status
    summary: str
    fields: tuple[str, ...] = ()          # what it supplies (the data the prototype is missing today)
    hosts: tuple[str, ...] = ()           # egress hosts it needs (must be allowed; see Studio data_sources.egress_allowlist)
    needs_credentials: bool = False
    sap_system: str | None = None         # the SAP-side system this stands in for, if any
    notes: str = ""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SanctionedEntity(_Strict):
    source_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    aliases: tuple[str, ...] = Field(default=(), max_length=60)
    country: str = Field(min_length=2, max_length=2)   # ISO 3166-1 alpha-2, or "XX" when the list gives none
    list_name: str = Field(min_length=1, max_length=60)


class WeatherSignal(_Strict):
    horizon_days: int = Field(ge=1, le=16)
    max_precip_mm: float = Field(ge=0, le=2000)
    max_wind_kmh: float = Field(ge=0, le=500)
    severe_days: int = Field(ge=0, le=16)
    source_url: str = Field(max_length=300)            # attribution only; never fetched again


class SupplierProfilePatch(_Strict):
    """Fields the registry may be missing. Only blanks are filled; an existing value is never overwritten."""
    supplier_id: str = Field(max_length=40)
    lead_time_weeks: int | None = Field(default=None, ge=0, le=104)
    capacity_units: int | None = Field(default=None, ge=0)
    years_in_business: int | None = Field(default=None, ge=0, le=300)
    financial_risk: float | None = Field(default=None, ge=0, le=1)   # in production a credit-risk provider, not the SBN profile
    source: str = Field(max_length=80)


class FinancialRisk(_Strict):
    supplier_id: str = Field(max_length=40)
    risk: float = Field(ge=0, le=1)                    # 0 = safe, 1 = distressed
    as_of: str = Field(max_length=10)
    source: str = Field(max_length=80)


class CertificateStatus(_Strict):
    supplier_id: str = Field(max_length=40)
    cert_type: str = Field(max_length=40)
    valid: bool
    expires_on: str | None = Field(default=None, max_length=10)
    issuer: str = Field(max_length=120)


class SanctionsSource(Protocol):
    async def fetch(self) -> list[SanctionedEntity]: ...


class WeatherSource(Protocol):
    async def signal(self, country: str) -> WeatherSignal: ...


class SupplierProfileSource(Protocol):
    async def profile(self, supplier_id: str) -> SupplierProfilePatch: ...


class FinancialRiskSource(Protocol):
    async def risk(self, supplier_id: str) -> FinancialRisk: ...


class CertificateSource(Protocol):
    async def check(self, supplier_id: str, cert_type: str) -> CertificateStatus: ...
