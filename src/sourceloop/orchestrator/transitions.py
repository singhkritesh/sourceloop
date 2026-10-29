"""Single source of truth for valid stage and status transitions."""
from __future__ import annotations

from sourceloop.data.enums import ProjectStatusEnum as P
from sourceloop.data.enums import StageEnum as S

STAGE_ORDER: tuple[S, ...] = (S.NEED, S.DISCOVERY, S.QUALIFICATION, S.SOURCING, S.CONTRACTING, S.ONBOARDING, S.DONE)
OPTIONAL_STAGES: frozenset[S] = frozenset({S.ONBOARDING})

STATUS_TRANSITIONS: dict[P, frozenset[P]] = {
    P.DRAFT: frozenset({P.RUNNING, P.STOPPED}),
    P.RUNNING: frozenset({P.ESCALATED, P.STOPPED, P.DONE, P.FAILED_RECOVERABLE, P.STALLED, P.BUDGET_EXCEEDED}),
    P.ESCALATED: frozenset({P.RUNNING, P.STOPPED}),
    P.FAILED_RECOVERABLE: frozenset({P.RUNNING, P.STOPPED}),
    P.STALLED: frozenset({P.RUNNING, P.STOPPED}),
    P.BUDGET_EXCEEDED: frozenset({P.RUNNING, P.STOPPED}),
    P.STOPPED: frozenset(),
    P.DONE: frozenset(),
}


class IllegalTransition(Exception):
    code = "ILLEGAL_TRANSITION"


def check_status(frm: P, to: P) -> None:
    if to not in STATUS_TRANSITIONS[frm]:
        raise IllegalTransition(f"{frm}->{to}")


def enabled_stages(disabled_optional: frozenset[S] = frozenset()) -> list[S]:
    bad = disabled_optional - OPTIONAL_STAGES
    if bad:  # INV: only optional stages may be disabled
        raise ValueError(f"stages cannot be disabled: {sorted(bad)}")
    return [s for s in STAGE_ORDER if s not in disabled_optional]


def next_stage(cur: S, stages: list[S]) -> S:
    i = stages.index(cur)
    if i + 1 >= len(stages):
        raise IllegalTransition(f"{cur} is terminal")
    return stages[i + 1]
