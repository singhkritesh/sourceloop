import pytest

from sourceloop.data.enums import ProjectStatusEnum as P
from sourceloop.data.enums import StageEnum as S
from sourceloop.orchestrator.transitions import IllegalTransition, check_status, enabled_stages, next_stage


def test_status_rules():
    check_status(P.RUNNING, P.ESCALATED)
    check_status(P.ESCALATED, P.RUNNING)
    with pytest.raises(IllegalTransition):
        check_status(P.DONE, P.RUNNING)
    with pytest.raises(IllegalTransition):
        check_status(P.DRAFT, P.ESCALATED)


def test_only_optional_stages_can_be_disabled():
    st = enabled_stages(frozenset({S.ONBOARDING}))
    assert next_stage(S.CONTRACTING, st) == S.DONE
    with pytest.raises(ValueError):
        enabled_stages(frozenset({S.QUALIFICATION}))
