"""Exact enum names/values shared by DB, bus and API. Import these; never type string literals."""
from enum import StrEnum


class RoleEnum(StrEnum):
    BUYER = "BUYER"
    SUPPLIER = "SUPPLIER"
    COMPLIANCE = "COMPLIANCE"
    QUALITY = "QUALITY"
    LEGAL = "LEGAL"
    FINANCE = "FINANCE"
    EVALUATOR = "EVALUATOR"
    DESIGNER = "DESIGNER"
    ADMIN = "ADMIN"              # ORGANIZATION admin: configures and sets up its own buyer or supplier organization
    SUPER_ADMIN = "SUPER_ADMIN"  # SBN platform operator: all access (every organization, Studio, settings, the full agent workflow)


class OrgKindEnum(StrEnum):
    BUYER = "BUYER"
    SUPPLIER = "SUPPLIER"


class StageEnum(StrEnum):
    NEED = "NEED"
    DISCOVERY = "DISCOVERY"
    QUALIFICATION = "QUALIFICATION"
    SOURCING = "SOURCING"
    CONTRACTING = "CONTRACTING"
    ONBOARDING = "ONBOARDING"
    DONE = "DONE"


class ProjectStatusEnum(StrEnum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    ESCALATED = "ESCALATED"
    STOPPED = "STOPPED"
    DONE = "DONE"
    FAILED_RECOVERABLE = "FAILED_RECOVERABLE"
    STALLED = "STALLED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class MessageTypeEnum(StrEnum):
    BRIEF = "BRIEF"
    INVITE = "INVITE"
    INTEREST_REPLY = "INTEREST_REPLY"
    DOC_REQUEST = "DOC_REQUEST"
    DOC_SUBMISSION = "DOC_SUBMISSION"
    QUAL_RESULT = "QUAL_RESULT"
    RFQ = "RFQ"
    OFFER = "OFFER"
    COUNTER = "COUNTER"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    CONTRACT_DRAFT = "CONTRACT_DRAFT"
    REDLINE = "REDLINE"
    SIGN_REQUEST = "SIGN_REQUEST"
    ONBOARD_DATA = "ONBOARD_DATA"
    ESCALATE = "ESCALATE"


class ApprovalStatusEnum(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EDITED = "EDITED"


class ApprovalKindEnum(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    COMPLIANCE_FLAG = "COMPLIANCE_FLAG"
    TECHNICAL_QUAL = "TECHNICAL_QUAL"
    AWARD = "AWARD"
    CLAUSE = "CLAUSE"
    SIGNATURE = "SIGNATURE"
    SUPPLIER_ACCEPT = "SUPPLIER_ACCEPT"
    BANK_DETAILS = "BANK_DETAILS"
    HOLD_RELEASE = "HOLD_RELEASE"
    MANDATE_BREACH = "MANDATE_BREACH"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    STALLED = "STALLED"
    BUDGET = "BUDGET"
    RULE = "RULE"


class QualStatusEnum(StrEnum):
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"


class OutcomeEnum(StrEnum):
    DEAL = "DEAL"
    NO_DEAL = "NO_DEAL"
    NO_VIABLE_SUPPLIER = "NO_VIABLE_SUPPLIER"
    STOPPED_BY_USER = "STOPPED_BY_USER"
    FAILED = "FAILED"


class HoldStatusEnum(StrEnum):
    HELD = "HELD"
    RELEASED = "RELEASED"


class ConfigStatusEnum(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class ProviderKindEnum(StrEnum):
    OLLAMA = "OLLAMA"
    ANTHROPIC = "ANTHROPIC"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    STUB = "STUB"
