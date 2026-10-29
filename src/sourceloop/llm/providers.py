"""Concrete providers. Local Ollama calls use non-streaming JSON; model families need different
'no-think' parameters, auto-detected here (never hardcoded at call sites)."""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from sourceloop.audit.canonical import dumps
from sourceloop.llm.client import RawResult
from sourceloop.llm.config import ModelRef
from sourceloop.net.safe_http import SafeHttp


def _ollama_no_think_kwargs(model: str) -> dict[str, Any]:
    """Thinking control for Ollama's NATIVE /api/chat: `think: false` for thinking-capable families.

    Verified against this install (gemma4 and qwen3.x): `reasoning_effort` is ignored by the native endpoint, so Gemma
    kept thinking, burned its whole token budget and returned empty content. (`reasoning_effort` applies to the
    OpenAI-compatible /v1 endpoint only.) Detection is by family so call sites never hardcode it.
    """
    m = model.lower()
    if any(f in m for f in ("gemma", "qwen", "deepseek-r1", "gpt-oss")):
        return {"think": False}
    return {}


# Safety net only: every request tells Ollama to drop the model 30 s after the call if nobody unloads it first. The real rule is stricter:
# whoever starts a task unloads the model the moment the task completes, fails, pauses or is cancelled (`release`, called from `finally`).
IDLE_KEEP_ALIVE = "30s"


class OllamaProvider:
    def __init__(self, base_url: str, http: SafeHttp) -> None:
        self.base = base_url.rstrip("/")
        self.http = http

    async def chat_json(self, model: ModelRef, system: str, user: str) -> RawResult:
        body: dict[str, Any] = {
            "model": model.model, "stream": False, "format": "json", "keep_alive": IDLE_KEEP_ALIVE,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": model.temperature, "num_predict": model.max_tokens},
        }
        if model.thinking == "off" or (model.thinking == "auto"):
            body.update(_ollama_no_think_kwargs(model.model))
        r = await self.http.post(f"{self.base}/api/chat", json=body, timeout=model.timeout_s)
        r.raise_for_status()
        j = r.json()
        toks = int(j.get("prompt_eval_count", 0)) + int(j.get("eval_count", 0))
        return RawResult(j.get("message", {}).get("content", ""), toks)

    async def first_token_mass(self, model: ModelRef, system: str, user: str, letters: str) -> tuple[dict[str, float], int]:
        """Probability mass the model puts on each option letter as its FIRST token (Ollama log-probs). One token, no JSON, no thinking."""
        body: dict[str, Any] = {"model": model.model, "stream": False, "keep_alive": IDLE_KEEP_ALIVE, "logprobs": True, "top_logprobs": 20,
                                "options": {"temperature": 0, "num_predict": 1},
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                **_ollama_no_think_kwargs(model.model)}
        r = await self.http.post(f"{self.base}/api/chat", json=body, timeout=model.timeout_s)
        r.raise_for_status()
        j = r.json()
        mass = dict.fromkeys(letters, 0.0)
        for t in ((j.get("logprobs") or [{}])[0].get("top_logprobs") or []):
            tok = str(t.get("token", "")).strip().upper()
            if tok in mass:
                mass[tok] += math.exp(float(t["logprob"]))
        return mass, int(j.get("prompt_eval_count", 0)) + 1

    async def list_models(self) -> list[dict[str, Any]]:
        r = await self.http.get(f"{self.base}/api/tags")
        r.raise_for_status()
        return [{"name": m["name"], "size": m.get("size"), "family": (m.get("details") or {}).get("family")}
                for m in r.json().get("models", [])]

    async def model_digest(self, model: ModelRef) -> str | None:
        """Digest of the locally installed model (from /api/tags), for audit provenance."""
        r = await self.http.get(f"{self.base}/api/tags")
        r.raise_for_status()
        for m in r.json().get("models", []):
            if m.get("name") == model.model or m.get("model") == model.model:
                d = m.get("digest")
                return str(d)[:80] if d else None
        return None

    async def release(self, model: str) -> None:
        """Free VRAM after a project: direct HTTP, not the SDK (serialization bugs)."""
        await self.http.post(f"{self.base}/api/generate", json={"model": model, "keep_alive": 0})


class AnthropicProvider:
    def __init__(self, http: SafeHttp, key_fn: Callable[[], str], base_url: str = "https://api.anthropic.com") -> None:
        self.http, self._key, self.base = http, key_fn, base_url.rstrip("/")

    async def chat_json(self, model: ModelRef, system: str, user: str) -> RawResult:
        r = await self.http.post(
            f"{self.base}/v1/messages",
            headers={"x-api-key": self._key(), "anthropic-version": "2023-06-01"},
            json={"model": model.model, "max_tokens": model.max_tokens, "temperature": model.temperature,
                  "system": system, "messages": [{"role": "user", "content": user}]},
            timeout=model.timeout_s,
        )
        r.raise_for_status()
        j = r.json()
        text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        u = j.get("usage", {})
        return RawResult(text, int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0)))


class OpenAICompatProvider:
    def __init__(self, base_url: str, http: SafeHttp, key_fn: Callable[[], str] | None = None) -> None:
        self.base, self.http, self._key = base_url.rstrip("/"), http, key_fn

    async def chat_json(self, model: ModelRef, system: str, user: str) -> RawResult:
        headers = {"Authorization": f"Bearer {self._key()}"} if self._key else {}
        r = await self.http.post(
            f"{self.base}/v1/chat/completions", headers=headers, timeout=model.timeout_s,
            json={"model": model.model, "temperature": model.temperature, "max_tokens": model.max_tokens,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        r.raise_for_status()
        j = r.json()
        return RawResult(j["choices"][0]["message"]["content"], int(j.get("usage", {}).get("total_tokens", 0)))


class StubProvider:
    """Deterministic provider for tests/demos. handlers: marker substring in system prompt -> fn(user)->dict."""

    is_stub = True

    def __init__(self, handlers: dict[str, Callable[[str], dict]] | None = None, default: dict | None = None,
                 choice_handlers: dict[str, Callable[[str], dict[str, float]]] | None = None) -> None:
        self.handlers = handlers or {}
        self.choice_handlers = choice_handlers or {}
        self.default = default or {}
        self.calls = 0

    async def first_token_mass(self, model: ModelRef, system: str, user: str, letters: str) -> tuple[dict[str, float], int]:
        """Scripted option masses (marker in the system prompt -> fn(user) -> {letter: mass}); default: all mass on the first option."""
        self.calls += 1
        for marker, fn in self.choice_handlers.items():
            if marker in system:
                return {k: float(fn(user).get(k, 0.0)) for k in letters}, max(1, len(user) // 4)
        return {k: (1.0 if i == 0 else 0.0) for i, k in enumerate(letters)}, 1

    async def chat_json(self, model: ModelRef, system: str, user: str) -> RawResult:
        self.calls += 1
        for marker, fn in self.handlers.items():
            if marker in system:
                out = fn(user)
                return RawResult(dumps(out), max(1, len(user) // 4))
        return RawResult(dumps(self.default), 1)
