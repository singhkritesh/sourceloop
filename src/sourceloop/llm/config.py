"""LLM configuration — a section of StudioConfig. Models are chosen per role with per-agent overrides."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sourceloop.data.enums import ProviderKindEnum


class ProviderCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: ProviderKindEnum
    base_url: str = ""
    is_local: bool = False
    has_key: bool = False  # the key itself lives in the vault, never here


class ModelRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    model: str
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=16, le=16000)
    timeout_s: float = Field(default=20.0, gt=0, le=300)
    thinking: Literal["auto", "on", "off"] = "auto"


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["live", "replay", "record"] = "replay"
    providers: dict[str, ProviderCfg] = Field(default_factory=dict)
    roles: dict[str, ModelRef] = Field(default_factory=dict)      # "chat", "fast"
    overrides: dict[str, ModelRef] = Field(default_factory=dict)  # agent_key -> ModelRef
    tokens_per_project: int = Field(default=400_000, ge=1000)
    replay_fallback_live: bool = False

    def effective(self, agent_key: str, role: str) -> ModelRef:
        if agent_key in self.overrides:
            return self.overrides[agent_key]
        if role in self.roles:
            return self.roles[role]
        raise KeyError(f"no model configured for role {role!r}")


def stub_config() -> LlmConfig:
    ref = ModelRef(provider="stub", model="stub-1")
    return LlmConfig(mode="live", providers={"stub": ProviderCfg(name="stub", kind=ProviderKindEnum.STUB)},
                     roles={"chat": ref, "fast": ref})
