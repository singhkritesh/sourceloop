"""Typed decisions from a model: ask a closed question, get a probability for every label plus a confidence.

The idea (borrowed from "decision models"): software does not want prose, it wants `{label: probability}` and a number saying how much to
trust it. A chat model can give that without sampling many times: constrain the answer to ONE option letter and read the model's own
log-probabilities for that first token (Ollama `logprobs`). One short call, no JSON to repair, no thinking tokens.

Honest limits: the probabilities are the model's, not guaranteed calibrated. `scripts/jev_concept.py` measures agreement and calibration on
labelled cases (gemma4:12b: 83% agreement, no severe verdict softened, 87% accuracy when confident; the small default model is at chance and
overconfident, so the model choice matters). Output is advisory: code decides what to do with it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from sourceloop.llm.config import ModelRef
from sourceloop.llm.providers import OllamaProvider
from sourceloop.net.safe_http import SafeHttp

LETTERS = "ABCDEFGH"


@dataclass(frozen=True)
class Decision:
    label: str
    probs: dict[str, float]      # normalised over the valid labels only
    confidence: float            # 0..1: 1 - normalised entropy (1 = certain, 0 = a coin flip between all labels)
    mass: float                  # probability mass the model put on valid labels before normalising (low = it wanted to say something else)
    tokens: int


def _entropy_confidence(p: list[float]) -> float:
    if len(p) < 2:
        return 1.0
    h = -sum(x * math.log(x) for x in p if x > 0)
    return max(0.0, 1.0 - h / math.log(len(p)))


def build_question(rubric: str, state: str, options: dict[str, str]) -> tuple[list[str], str, str, str]:
    """-> (labels, letters, system, user). `options` maps label -> meaning; 2..8 labels, answered by letter."""
    labels = list(options)
    if not 2 <= len(labels) <= len(LETTERS):
        raise ValueError("2..8 options")
    letters = LETTERS[: len(labels)]
    menu = "\n".join(f"{ch}) {labels[i]}: {options[labels[i]]}" for i, ch in enumerate(letters))
    return labels, letters, f"Respond only in English. {rubric}\nAnswer with exactly one letter.", f"{state}\n\nOptions:\n{menu}\n\nAnswer (one letter):"


def decision_from_mass(labels: list[str], mass: dict[str, float], tokens: int = 0) -> Decision:
    letters = LETTERS[: len(labels)]
    total = sum(mass.get(ch, 0.0) for ch in letters)
    if total <= 0:   # the model said something that is not an option: no information, and we say so (confidence 0)
        return Decision(labels[0], {lab: 1.0 / len(labels) for lab in labels}, 0.0, 0.0, tokens)
    probs = {labels[i]: mass.get(ch, 0.0) / total for i, ch in enumerate(letters)}
    return Decision(max(probs, key=lambda k: probs[k]), probs, _entropy_confidence(list(probs.values())), min(1.0, total), tokens)


async def choice(http: SafeHttp, base_url: str, model: str, *, rubric: str, state: str, options: dict[str, str], timeout_s: float = 60.0) -> Decision:
    """Direct local call (used by the evaluation script). The app itself goes through `LLMClient.decide` (replay, budget, provenance)."""
    labels, letters, system, user = build_question(rubric, state, options)
    mass, toks = await OllamaProvider(base_url, http).first_token_mass(ModelRef(provider="ollama", model=model, timeout_s=timeout_s), system, user, letters)
    return decision_from_mass(labels, mass, toks)
