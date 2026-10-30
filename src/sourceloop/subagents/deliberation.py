"""Model-backed deliberator (trusted side). An agent hands it the legal moves (the code's, rule-based move first); a small local model picks
one as a typed decision (a probability per option from the first-token log-probs). The pick is used only when the model was actually
answering the question and sure enough; otherwise the rule-based move stands and the reason is recorded. Either way the decision is written
to the audit log BEFORE the agent acts, with codes only (no prices), so agent performance can be measured."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sourceloop.agents.common.deliberate import Move, Pick
from sourceloop.llm.client import LLMClient
from sourceloop.llm.prompt_builder import build_choice
from sourceloop.llm.prompts_default import (
    AWARD_RUBRIC,
    MOVE_RUBRIC_BUYER,
    MOVE_RUBRIC_SUPPLIER,
    MOVE_STATE,
    MOVE_VARS,
)

MIN_MASS = 0.5         # the model must put at least half its probability on the listed options
MIN_CONFIDENCE = 0.25  # and not be close to a coin flip between all of them
Sink = Callable[[dict[str, Any]], Awaitable[None]]


class LLMDeliberator:
    def __init__(self, llm: LLMClient, project_id: str) -> None:
        self.llm, self.project_id = llm, project_id
        self.sink: Sink | None = None   # set by the RunExecutor: appends the `agent_decision` audit event

    async def pick(self, agent: str, sid: str | None, rnd: int, situation: Sequence[str], moves: Sequence[Move]) -> Pick:
        rule = moves[0]
        base = Pick(0, rule.label, rule.kind, "rule")
        if len(moves) < 2:
            return base
        rubric, key = (MOVE_RUBRIC_BUYER, "negotiation_buyer") if agent == "buyer" else (MOVE_RUBRIC_SUPPLIER, "negotiation_supplier")
        if agent == "award":
            rubric, key = AWARD_RUBRIC, "award_advisor"
        labels, letters, system, user, tpl = build_choice(rubric, MOVE_STATE, MOVE_VARS, {"situation": "\n".join(situation)},
                                                          {m.label: m.text for m in moves})
        t0 = time.perf_counter()
        pick = base
        try:
            d = await self.llm.decide(project_id=self.project_id, agent_key=key, role="fast", template_hash=tpl, system=system, user=user,
                                      labels=labels, letters=letters)
        except Exception as e:  # noqa: BLE001 - advisory path: the rule-based move stands
            pick = Pick(0, rule.label, rule.kind, "rule", fallback=f"UNAVAILABLE:{type(e).__name__}"[:40])
        else:
            i = labels.index(d.label)
            if d.mass < MIN_MASS or d.confidence < MIN_CONFIDENCE:
                pick = Pick(0, rule.label, rule.kind, "rule", confidence=d.confidence, mass=d.mass, p_chosen=d.probs[labels[0]], tokens=d.tokens,
                            fallback="LOW_CONFIDENCE" if d.mass >= MIN_MASS else "OFF_MENU")
            else:
                pick = Pick(i, moves[i].label, moves[i].kind, "model", d.confidence, d.mass, d.probs[d.label], i == 0, None, d.tokens)
        pick.ms = int((time.perf_counter() - t0) * 1000)
        pick.model = f"{self.llm.cfg.effective(key, 'fast').model}"
        if self.sink is not None:
            await self.sink({"agent": agent, "supplier": sid, "round": rnd, "options": [m.label for m in moves], "rule": rule.label,
                             "chosen": pick.label, "kind": pick.kind, "source": pick.source, "agrees_with_rule": pick.index == 0,
                             "p_chosen": round(pick.p_chosen, 3), "confidence": round(pick.confidence, 3), "mass": round(pick.mass, 3),
                             "fallback": pick.fallback, "tokens": pick.tokens, "ms": pick.ms, "model": pick.model})
        return pick
