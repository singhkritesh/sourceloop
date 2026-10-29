"""Subagent tool runner — the optimized core of the agent framework.

What it buys (all measurable through `runner.stats`):
  * run-scoped memo cache keyed by (tool, input-hash): identical calls cost nothing
  * single-flight: concurrent identical calls share one in-flight execution
  * bounded concurrency (semaphore) so parallel fan-out to many suppliers cannot stampede the LLM
  * per-call timeout + capped retry with backoff; failure => fail-closed result (confidence 0)
  * loop guard per agent turn (max calls, no-progress detection)
  * hooks for audit/SSE so every call is recorded without the tool knowing about it
"""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sourceloop.agents.common import provenance
from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.llm.client import BudgetExceeded

In = TypeVar("In", bound=BaseModel)
Out = TypeVar("Out", bound=BaseModel)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str = Field(max_length=500)
    title: str = Field(default="", max_length=300)
    date: str | None = None


class ToolOutput(BaseModel, Generic[Out]):
    """What a tool function returns. Free text never influences control flow: only `value` (typed),
    `confidence`, `sources` and `assumptions` are consumed by lead agents."""

    model_config = ConfigDict(extra="forbid")
    value: Out
    confidence: float = Field(default=1.0, ge=0, le=1)
    sources: tuple[Source, ...] = ()
    assumptions: tuple[str, ...] = ()


class ToolResult(BaseModel, Generic[Out]):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    tool: str
    value: Out | None = None
    confidence: float = 0.0
    sources: tuple[Source, ...] = ()
    assumptions: tuple[str, ...] = ()
    error: str | None = None  # code only: TIMEOUT | ERROR | INVALID_OUTPUT | LOOP_LIMIT | UNKNOWN_TOOL | DISABLED | NOT_ALLOWED
    cached: bool = False
    latency_ms: int = 0


@dataclass
class Tool(Generic[In, Out]):
    name: str
    input_model: type[In]
    output_model: type[Out]
    fn: Callable[[In], Awaitable[ToolOutput[Out]]]
    timeout_s: float = 20.0
    retries: int = 1
    cacheable: bool = True
    enabled: bool = True


class LoopLimit(Exception):
    pass


@dataclass
class TurnGuard:
    """One per lead-agent turn. Enforces max tool calls and terminates no-progress loops."""

    max_calls: int = 12
    max_stale: int = 3
    calls: int = 0
    _states: list[str] = field(default_factory=list)

    def tick(self) -> None:
        self.calls += 1
        if self.calls > self.max_calls:
            raise LoopLimit("MAX_CALLS")

    def progress(self, state: object) -> None:
        h = sha256_hex(dumps(state))
        self._states.append(h)
        if len(self._states) >= self.max_stale and len(set(self._states[-self.max_stale:])) == 1:
            raise LoopLimit("NO_PROGRESS")


Hook = Callable[[str, dict[str, Any]], Awaitable[None]]


class ToolRunner:
    def __init__(self, tools: list[Tool], *, max_concurrency: int = 8, hook: Hook | None = None) -> None:
        self._tools = {t.name: t for t in tools}
        self._sem = asyncio.Semaphore(max_concurrency)
        self._cache: dict[str, ToolResult] = {}
        self._inflight: dict[str, asyncio.Future[ToolResult]] = {}
        self._hook = hook
        self._allowed: frozenset[str] | None = None  # None = every registered tool (legacy); set from the pinned Studio config
        self.on_denied: Callable[[str], Awaitable[None]] | None = None  # audit sink for NOT_ALLOWED (set by the builder)
        self.stats: Counter[str] = Counter()

    def set_enabled(self, name: str, enabled: bool) -> None:
        self._tools[name].enabled = enabled

    def set_allowed(self, names: frozenset[str] | set[str] | None) -> None:
        """Per-agent allowlist from the pinned config. Empty set = nothing allowed (fail closed)."""
        self._allowed = None if names is None else frozenset(names)

    def configure(self, name: str, *, enabled: bool | None = None, timeout_s: float | None = None) -> None:
        """Apply per-agent Studio settings from the pinned config; unknown tool names are ignored (other side's tools)."""
        t = self._tools.get(name)
        if t is None:
            return
        if enabled is not None:
            t.enabled = enabled
        if timeout_s is not None:
            t.timeout_s = timeout_s

    async def call(self, name: str, inp: BaseModel, *, guard: TurnGuard | None = None) -> ToolResult:
        if self._allowed is not None and name not in self._allowed:  # checked first: also covers names not registered
            self.stats["not_allowed"] += 1
            if self.on_denied is not None:
                await self.on_denied(name)  # audit first; an audit failure propagates (run fails, recoverable) - not swallowed
            await self._emit("subagent_result", {"tool": name[:60], "ok": False, "confidence": 0.0, "latency_ms": 0,
                                                 "error": "NOT_ALLOWED", "sources": 0, "llm": []})
            return ToolResult(ok=False, tool=name, error="NOT_ALLOWED")
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, tool=name, error="UNKNOWN_TOOL")
        if not tool.enabled:
            return ToolResult(ok=False, tool=name, error="DISABLED")
        if guard is not None:
            try:
                guard.tick()
            except LoopLimit as e:
                return ToolResult(ok=False, tool=name, error="LOOP_LIMIT", assumptions=(str(e),))

        key = f"{name}:{sha256_hex(dumps(inp))}"
        if tool.cacheable and key in self._cache:  # 1) memo
            self.stats["cache_hit"] += 1
            return self._cache[key].model_copy(update={"cached": True, "latency_ms": 0})
        if key in self._inflight:  # 2) single-flight
            self.stats["singleflight_join"] += 1
            res = await self._inflight[key]
            return res.model_copy(update={"cached": True, "latency_ms": 0})

        fut: asyncio.Future[ToolResult] = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            res = await self._execute(tool, inp)
            if tool.cacheable and res.ok:
                self._cache[key] = res
            fut.set_result(res)
            return res
        except BaseException as e:  # cancellation must not strand joiners
            if not fut.done():
                fut.set_exception(e)
                fut.exception()  # mark retrieved
            raise
        finally:
            self._inflight.pop(key, None)

    async def _execute(self, tool: Tool, inp: BaseModel) -> ToolResult:
        await self._emit("subagent_call", {"tool": tool.name})
        t0 = time.perf_counter()
        err = "ERROR"
        out: ToolOutput | None = None
        budget: BudgetExceeded | None = None
        prov_box, prov_tok = provenance.start()
        async with self._sem:
            for attempt in range(tool.retries + 1):
                try:
                    out = await asyncio.wait_for(tool.fn(inp), tool.timeout_s)
                    if not isinstance(out.value, tool.output_model):
                        out, err = None, "INVALID_OUTPUT"
                    break
                except BudgetExceeded as e:  # not a tool error: the executor must PAUSE the run (BUDGET_EXCEEDED), fail closed
                    budget = e
                    break
                except TimeoutError:
                    err = "TIMEOUT"
                except ValidationError:
                    err = "INVALID_OUTPUT"
                    break  # a retry will not fix a schema violation
                except Exception:  # noqa: BLE001 - fail closed; details go to logs, not results
                    err = "ERROR"
                if attempt < tool.retries:
                    self.stats["retry"] += 1
                    await asyncio.sleep(min(0.05 * 2**attempt, 1.0))
        provenance.stop(prov_tok)
        if budget is not None:
            self.stats["budget_exceeded"] += 1
            raise budget
        ms = int((time.perf_counter() - t0) * 1000)
        self.stats["calls"] += 1
        if out is None:
            self.stats["failed"] += 1
            res = ToolResult(ok=False, tool=tool.name, error=err, latency_ms=ms)
        else:
            res = ToolResult(ok=True, tool=tool.name, value=out.value, confidence=out.confidence,
                             sources=out.sources, assumptions=out.assumptions, latency_ms=ms)
        await self._emit("subagent_result", {"tool": tool.name, "ok": res.ok, "confidence": res.confidence,
                                             "latency_ms": ms, "error": res.error, "sources": len(res.sources),
                                             "llm": prov_box})
        return res

    async def _emit(self, kind: str, data: dict[str, Any]) -> None:
        if self._hook is not None:
            await self._hook(kind, data)
