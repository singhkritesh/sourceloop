"""Per-tool-call collector for model provenance (provider, model, digest, tokens).

The trusted LLM client appends a record via `note()`; `ToolRunner._execute` opens a collector around each tool call and
forwards the records to its hook, so they reach `subagent_call` persistence. Provenance is metadata only: it never
enters replay keys, tool cache keys or the audit hash path, and carries no mandate data.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_current: ContextVar[list[dict[str, Any]] | None] = ContextVar("llm_provenance", default=None)


def start() -> tuple[list[dict[str, Any]], Token]:
    box: list[dict[str, Any]] = []
    return box, _current.set(box)


def stop(token: Token) -> None:
    _current.reset(token)


def note(rec: dict[str, Any]) -> None:
    box = _current.get()
    if box is not None:
        box.append(rec)
