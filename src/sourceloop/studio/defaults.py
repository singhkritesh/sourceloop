"""default_config(): reproduces today's behaviour (demo.py live_ollama, RunConfig/BuyerPolicy/ScenarioSpec defaults)."""
from __future__ import annotations

import os
from decimal import Decimal

from sourceloop.agents.common import contracts as K
from sourceloop.data.clauses import DEFAULT_CLAUSES
from sourceloop.data.enums import ProviderKindEnum
from sourceloop.llm.prompts_default import DEFAULT_BODIES
from sourceloop.studio.schema import (
    AGENT_KEYS,
    AgentDef,
    ClauseDef,
    LlmSection,
    ModelDef,
    PromptDef,
    ProviderDef,
    ScenarioDef,
    StudioConfig,
)

OLLAMA_URL = "http://localhost:11434"
DEFAULT_FAST = "gemma4:e2b-mlx"
DEFAULT_CHAT = "qwen3.8:27b-mlx"
# Bootstrap only: seed publishes config v1 from this. After that the pinned config version rules (Studio edits it).
# install-mac.sh sets both to a 4b model so a laptop never needs the large default.
ENV_FAST, ENV_CHAT = "SL_FAST_MODEL", "SL_CHAT_MODEL"


# today's hard-coded tool timeouts (subagents/*_tools.py), so the default config changes no behaviour
_TOOL_TIMEOUTS = {K.RESEARCH: 30, K.COMPLIANCE: 5, K.NEWS: 20, K.WEATHER: 5, K.LANDED: 5, K.QUOTE: 5, K.BUYER_VERIFY: 5,
                  K.CAPACITY: 5, K.DEMAND: 5, K.DOCS: 5}


def default_config() -> StudioConfig:
    models = {
        "fast": ModelDef(provider="ollama", model=os.environ.get(ENV_FAST) or DEFAULT_FAST, timeout_s=120, max_tokens=512),
        "chat": ModelDef(provider="ollama", model=os.environ.get(ENV_CHAT) or DEFAULT_CHAT, timeout_s=120, max_tokens=1024),
    }
    return StudioConfig(
        providers={"ollama": ProviderDef(kind=ProviderKindEnum.OLLAMA, base_url=OLLAMA_URL, is_local=True)},
        llm=LlmSection(mode="replay", roles=models),
        agents={k: AgentDef(timeout_s=_TOOL_TIMEOUTS.get(k, 20)) for k in AGENT_KEYS},
        prompts={k: PromptDef(body=b) for k, b in DEFAULT_BODIES.items()},
        clauses={k: ClauseDef(**v) for k, v in DEFAULT_CLAUSES.items()},
        scenarios={
            "happy": ScenarioDef(name="Happy path"),
            "no_deal": ScenarioDef(name="No overlap (NO_DEAL)", buyer_max_factor=Decimal("0.5"),
                                   buyer_target_factor=Decimal("0.4")),
            "poisoned_news": ScenarioDef(name="Poisoned news", poisoned_news=True),
            "below_floor": ScenarioDef(name="Supplier attempts below floor",
                                       supplier_behaviours={"shortlist_0": "adversarial"}),
            "inflated_claims": ScenarioDef(name="Supplier inflates claims",
                                           supplier_behaviours={"shortlist_1": "adversarial"}),
            "locked_clause": ScenarioDef(name="Redline of a locked clause",
                                         supplier_behaviours={"awarded": "adversarial"}),
        },
    )
