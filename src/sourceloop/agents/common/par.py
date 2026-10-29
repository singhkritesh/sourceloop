"""Deterministic fan-out. `gather` behaves like asyncio.gather, except in SEQUENTIAL mode it awaits the coroutines one after another.

Why: the order in which concurrent agent steps append audit events depends on how long each one suspended (database I/O in the live server,
none in a test), so a concurrent run's audit chain cannot be reproduced. A replay-mode project runs SEQUENTIALLY: its event order depends only on
its inputs and decisions, which is what lets a restarted server rebuild it by replay and check the result against the stored chain. Live-mode runs
keep real concurrency (model latency matters there) and are never restored.

The mode is a context variable, set by the task that drives the run and inherited by the tasks it creates."""
from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Awaitable
from typing import Any

_SEQUENTIAL: contextvars.ContextVar[bool] = contextvars.ContextVar("sourceloop_sequential", default=False)


def set_sequential(on: bool) -> contextvars.Token[bool]:
    return _SEQUENTIAL.set(on)


def reset_sequential(token: contextvars.Token[bool]) -> None:
    _SEQUENTIAL.reset(token)


def is_sequential() -> bool:
    return _SEQUENTIAL.get()


async def gather(*aws: Awaitable[Any]) -> list[Any]:
    if not _SEQUENTIAL.get():
        return list(await asyncio.gather(*aws))
    out: list[Any] = []
    try:
        for a in aws:
            out.append(await a)
    except BaseException:
        for rest in aws[len(out):]:  # never leave an un-awaited coroutine behind
            close = getattr(rest, "close", None)
            if close is not None:
                close()
        raise
    return out
