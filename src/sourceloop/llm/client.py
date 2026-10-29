"""LLM client: provider abstraction + replay/record + token budget + JSON recovery + memo.

Cost/latency levers: replay store (zero calls), memo for deterministic calls, single repair attempt
on the cheap `fast` role only when parsing fails, per-project token budget enforced before each call.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from sourceloop.agents.common import provenance
from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.llm.config import LlmConfig, ModelRef

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(Exception):
    code = "BUDGET_EXCEEDED"


class ReplayMiss(Exception):
    code = "REPLAY_MISS"


class LLMUnavailable(Exception):
    code = "LLM_UNAVAILABLE"


class LLMInvalid(Exception):
    code = "LOW_CONFIDENCE"


@dataclass(frozen=True, slots=True)
class RawResult:
    text: str
    tokens: int


class Provider(Protocol):
    async def chat_json(self, model: ModelRef, system: str, user: str) -> RawResult: ...

    # Optional: `async def model_digest(self, model: ModelRef) -> str | None` (Ollama: /api/tags digest).


class LLMResult(BaseModel, Generic[T]):
    data: T
    tokens: int
    model_id: str
    replayed: bool = False
    provider: str = ""
    model: str = ""
    digest: str = ""  # model digest when the provider exposes one; "stub" in replay/stub mode; "unavailable" otherwise
    repaired: bool = False  # output came from the repair pass: callers must cap confidence


class ReplayStore(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put(self, key: str, value: dict) -> None: ...


class MemoryReplayStore:
    def __init__(self) -> None:
        self.d: dict[str, dict] = {}

    def get(self, key: str) -> dict | None:
        return self.d.get(key)

    def put(self, key: str, value: dict) -> None:
        self.d[key] = value


class DirReplayStore(MemoryReplayStore):
    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        for f in self.path.glob("*.json"):
            self.d[f.stem] = json.loads(f.read_text())

    def put(self, key: str, value: dict) -> None:
        super().put(key, value)
        (self.path / f"{key}.json").write_text(dumps(value))


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_json_loose(text: str) -> Any:
    """Recovery chain: parse -> strip code fences -> first {...} block."""
    for cand in (text, _FENCE.sub("", text.strip())):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError("no JSON found")


class LLMClient:
    def __init__(self, cfg: LlmConfig, providers: dict[str, Provider], replay: ReplayStore | None = None) -> None:
        self.cfg = cfg
        self.providers = providers
        self.replay = replay or MemoryReplayStore()
        self.used: dict[str, int] = defaultdict(int)
        self._memo: dict[str, LLMResult] = {}
        self.stats: dict[str, int] = defaultdict(int)
        self._digests: dict[str, str] = {}

    def release_budget_check(self, project_id: str, extra: int = 0) -> None:
        if self.used[project_id] + extra >= self.cfg.tokens_per_project:
            raise BudgetExceeded(project_id)

    async def complete_json(self, *, project_id: str, agent_key: str, role: str, template_hash: str,
                            system: str, user: str, schema: type[T]) -> LLMResult[T]:
        model = self.cfg.effective(agent_key, role)
        key = sha256_hex(dumps({"role": role, "agent": agent_key, "tpl": template_hash, "user": user,
                                "schema": schema.__name__}))
        if self.cfg.mode != "live":
            hit = self.replay.get(key)
            if hit is not None:
                self.stats["replay_hit"] += 1
                # digest is deliberately 'stub' here: provenance of a replayed answer is the recording, not a live model
                res = LLMResult[schema](data=schema.model_validate(hit["data"]), tokens=0,  # type: ignore[valid-type]
                                         model_id=hit["model_id"], replayed=True, provider=model.provider,
                                         model=model.model, digest="stub")
                self._note(res)
                return res
            if self.cfg.mode == "replay" and not self.cfg.replay_fallback_live:
                raise ReplayMiss(key)
        if key in self._memo and model.temperature == 0:
            self.stats["memo_hit"] += 1
            self._note(self._memo[key], tokens=0)
            return self._memo[key]

        self.release_budget_check(project_id)
        prov = self.providers.get(model.provider)
        if prov is None:
            raise LLMUnavailable(f"provider {model.provider}")
        self.stats["live_calls"] += 1
        raw = await self._call(prov, model, system, user)
        self.used[project_id] += raw.tokens
        repaired = False
        try:
            data = schema.model_validate(parse_json_loose(raw.text))
        except (ValueError, ValidationError):
            repaired = True
            data, extra = await self._repair(project_id, agent_key, raw.text, schema)
            raw = RawResult(raw.text, raw.tokens + extra)
        res = LLMResult[schema](data=data, tokens=raw.tokens, model_id=f"{model.provider}:{model.model}",  # type: ignore[valid-type]
                                 provider=model.provider, model=model.model, digest=await self._digest(prov, model),
                                 repaired=repaired)
        self._note(res)
        if self.cfg.mode == "record":
            self.replay.put(key, {"data": data.model_dump(mode="json"), "model_id": res.model_id})
        if model.temperature == 0:
            self._memo[key] = res
        return res

    async def decide(self, *, project_id: str, agent_key: str, role: str, template_hash: str, system: str, user: str,
                     labels: list[str], letters: str):
        """Closed-question decision with label probabilities (first-token log-probs). Same replay / record / budget / provenance rules as
        `complete_json`. Only providers that expose `first_token_mass` can answer; others raise LLMUnavailable (callers treat that as 'no opinion')."""
        from sourceloop.llm.decide import Decision, decision_from_mass

        model = self.cfg.effective(agent_key, role)
        key = sha256_hex(dumps({"kind": "decide", "role": role, "agent": agent_key, "tpl": template_hash, "user": user, "labels": labels}))
        if self.cfg.mode != "live":
            hit = self.replay.get(key)
            if hit is not None:
                self.stats["replay_hit"] += 1
                self._note(LLMResult[Decision](data=Decision(**hit["data"]), tokens=0, model_id=hit["model_id"], replayed=True,  # type: ignore[valid-type]
                                                provider=model.provider, model=model.model, digest="stub"))
                return Decision(**hit["data"])
            if self.cfg.mode == "replay" and not self.cfg.replay_fallback_live:
                raise ReplayMiss(key)
        self.release_budget_check(project_id)
        prov = self.providers.get(model.provider)
        fn = getattr(prov, "first_token_mass", None)
        if fn is None:
            raise LLMUnavailable(f"provider {model.provider} cannot return option probabilities")
        self.stats["live_calls"] += 1
        try:
            mass, toks = await fn(model, system, user, letters)
        except (TimeoutError, OSError) as e:
            raise LLMUnavailable(str(type(e).__name__)) from e
        self.used[project_id] += toks
        dec = decision_from_mass(labels, mass, toks)
        provenance.note({"provider": model.provider, "model": model.model, "digest": await self._digest(prov, model), "tokens": toks, "replayed": False})
        if self.cfg.mode == "record":
            from dataclasses import asdict
            self.replay.put(key, {"data": asdict(dec), "model_id": f"{model.provider}:{model.model}"})
        return dec

    async def _digest(self, prov: Provider, model: ModelRef) -> str:
        """Best-effort model digest for provenance. Never raises, never feeds replay keys or decisions."""
        if getattr(prov, "is_stub", False):
            return "stub"
        fn = getattr(prov, "model_digest", None)
        if fn is None:
            return "unavailable"
        ck = f"{model.provider}:{model.model}"
        if ck not in self._digests:
            try:
                self._digests[ck] = (await fn(model)) or "unavailable"
            except Exception:  # noqa: BLE001 - provenance must never break a run
                return "unavailable"
        return self._digests[ck]

    @staticmethod
    def _note(res: LLMResult, tokens: int | None = None) -> None:
        provenance.note({"provider": res.provider, "model": res.model, "digest": res.digest,
                         "tokens": res.tokens if tokens is None else tokens, "replayed": res.replayed})

    async def _call(self, prov: Provider, model: ModelRef, system: str, user: str) -> RawResult:
        try:
            return await prov.chat_json(model, system, user)
        except (TimeoutError, OSError) as e:
            raise LLMUnavailable(str(type(e).__name__)) from e

    async def _repair(self, project_id: str, agent_key: str, bad: str, schema: type[T]) -> tuple[T, int]:
        """One repair attempt on the cheap role. If that fails too, the caller escalates (LOW_CONFIDENCE)."""
        model = self.cfg.effective(agent_key, "fast")
        prov = self.providers[model.provider]
        self.release_budget_check(project_id)
        sys = "Respond only in English. Return ONLY valid JSON matching this JSON schema: " + dumps(schema.model_json_schema())
        raw = await self._call(prov, model, sys, "Fix this into valid JSON:\n" + bad[:4000])
        self.used[project_id] += raw.tokens
        self.stats["repairs"] += 1
        try:
            return schema.model_validate(parse_json_loose(raw.text)), raw.tokens
        except (ValueError, ValidationError) as e:
            raise LLMInvalid(agent_key) from e
