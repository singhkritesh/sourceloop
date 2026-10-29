"""Buyer-side subagent implementations (trusted side: may touch the registry). Lead agents see only
tool names + typed contracts. Rules compute; the LLM is used only where reading text is needed (news)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from sourceloop.agents.common import contracts as K
from sourceloop.agents.common.tools import Source, Tool, ToolOutput
from sourceloop.compliance.resolution import resolve
from sourceloop.compliance.rules import WatchlistIndex, qualify
from sourceloop.compliance.scoring import score, shortlist
from sourceloop.connectors.base import ConnectorError, SupplierProfileSource, WeatherSource
from sourceloop.connectors.stubs import PROFILE_FIELDS
from sourceloop.data.enums import QualStatusEnum as Q
from sourceloop.data.news import NewsFeed
from sourceloop.data.registry import Need, Registry, SupplierRec
from sourceloop.llm.client import LLMClient
from sourceloop.llm.prompt_builder import build
from sourceloop.llm.prompts_default import ALLOWED_VARS, DEFAULT_BODIES
from sourceloop.security.injection import injection_flags

TARIFF_PCT = {"CN": 25, "VN": 5, "MX": 0, "DE": 3, "PL": 3, "TR": 10, "IN": 8, "US": 0, "MY": 5, "TH": 5, "KR": 2, "RU": 35}
FREIGHT_PCT = {"CN": 4, "VN": 4, "MX": 1.5, "DE": 3, "PL": 3, "TR": 3.5, "IN": 5, "US": 0.5, "MY": 4.5, "TH": 4.5, "KR": 4, "RU": 6}
_SEASON = {  # country -> (months, extra delay probability, driver)
    "VN": ({7, 8, 9, 10}, 0.18, "typhoon season"), "CN": ({7, 8, 9}, 0.12, "typhoon season"),
    "MY": ({10, 11, 12}, 0.08, "monsoon"), "TH": ({8, 9, 10}, 0.12, "seasonal flooding"),
    "IN": ({6, 7, 8, 9}, 0.2, "monsoon"), "MX": ({8, 9, 10}, 0.1, "hurricane season"),
    "DE": ({12, 1, 2}, 0.07, "winter storms"), "PL": ({12, 1, 2}, 0.07, "winter storms"),
    "TR": ({1, 2}, 0.05, "winter weather"), "US": ({8, 9, 10}, 0.06, "hurricane season"),
}


def to_need(n: K.NeedSpec) -> Need:
    return Need(n.id, n.category, n.industry, n.volume_units, n.required_certs, n.excluded_countries,
                n.max_lead_time_weeks, n.max_financial_risk, n.min_years_in_business)


def _completeness(s: SupplierRec) -> int:
    return sum(v is not None for v in (s.capacity_units, s.lead_time_weeks, s.financial_risk, s.years_in_business,
                                       s.on_time_pct, s.tax_id))


def build_buyer_tools(registry: Registry, llm: LLMClient, news: NewsFeed, project_id: str, today: date,
                      prompts: dict[str, str] | None = None, news_timeout_s: float = 20.0,
                      weather: WeatherSource | None = None, weather_wanted: bool = False,
                      profile: SupplierProfileSource | None = None) -> list[Tool]:
    live_weather = weather  # the inner tool function below is also called `weather`: keep the injected source under its own name
    wl = WatchlistIndex(registry.watchlist)
    enriched: dict[str, SupplierRec] = {}      # id -> record with blanks filled from the profile source (never overwritten)
    stats = {"filled": 0, "failed": 0}

    async def enrich(recs: list[SupplierRec]) -> list[SupplierRec]:
        """Fill ONLY blank mandatory fields from the profile source. A source that cannot answer leaves the blank in place, so the
        record still goes to REVIEW (blank never means pass). A real source needs batching + a concurrency cap here."""
        if profile is None:
            return recs
        out: list[SupplierRec] = []
        for s in recs:
            if s.id in enriched:
                out.append(enriched[s.id])
                continue
            blank = [k for k in PROFILE_FIELDS if (v := getattr(s, k)) is None or (isinstance(v, float) and v != v)]
            if not blank:
                out.append(s)
                continue
            try:
                patch = await profile.profile(s.id)
            except ConnectorError:
                stats["failed"] += 1
                out.append(s)
                continue
            upd = {k: getattr(patch, k) for k in blank if getattr(patch, k) is not None}
            if upd:
                s = replace(s, **upd)
                enriched[s.id] = s
                stats["filled"] += 1
            out.append(s)
        return out
    bodies = {**DEFAULT_BODIES, **(prompts or {})}
    canon_cache: dict[str, dict[str, str]] = {}

    def canon(category: str) -> dict[str, str]:
        if category not in canon_cache:  # resolved once per category per run (blocked, so ~linear)
            canon_cache[category] = resolve(registry.in_category(category))
        return canon_cache[category]

    async def research(inp: K.ResearchIn) -> ToolOutput[K.ResearchOut]:
        need = to_need(inp.need)
        pool = await enrich(registry.in_category(need.category))
        cmap = canon(need.category)
        clusters: dict[str, list[SupplierRec]] = {}
        for s in pool:
            clusters.setdefault(cmap[s.id], []).append(s)
        merged = len(pool) - len(clusters)
        filtered: dict[str, int] = {"blocked": 0, "rejected": 0}
        scored, meta = [], {}
        for members in clusters.values():
            rep = max(members, key=lambda m: (_completeness(m), -ord(m.id[-1])))
            d = qualify(rep, need, wl, today)
            if d.status is not Q.BLOCKED:  # every cluster member is screened, not just the representative
                for m in members:
                    if m is not rep and qualify(m, need, wl, today).status is Q.BLOCKED:
                        d = qualify(m, need, wl, today)
                        break
            if d.status is Q.BLOCKED:
                filtered["blocked"] += 1
            elif d.status is Q.REJECTED:
                filtered["rejected"] += 1
            else:
                sc = score(rep, need)
                scored.append(sc)
                meta[rep.id] = d
        top = shortlist(scored, k=inp.longlist_size, min_countries=inp.min_countries)
        cands = tuple(K.Cand(supplier_id=t.supplier_id, country=t.country, score=t.score,
                             status=meta[t.supplier_id].status.value, reasons=meta[t.supplier_id].reasons + t.reasons)
                      for t in top)
        return ToolOutput[K.ResearchOut](
            value=K.ResearchOut(candidates=cands, considered=len(pool), merged_duplicates=merged, filtered=filtered),
            confidence=0.8 if stats["failed"] else 1.0,
            sources=(Source(url=f"sbn://registry/{need.category}", title="SBN verified registry"),),
            assumptions=((f"{stats['filled']} supplier record(s) had blank fields filled from the profile source",) if stats["filled"] else ())
            + ((f"{stats['failed']} supplier(s) had no profile data; their blanks stay and they need review",) if stats["failed"] else ()))

    async def compliance(inp: K.ComplianceIn) -> ToolOutput[K.ComplianceOut]:
        s = enriched.get(inp.supplier_id) or registry.get(inp.supplier_id)
        if s is None:
            raise KeyError("unknown supplier")
        d = qualify(s, to_need(inp.need), wl, today)
        return ToolOutput[K.ComplianceOut](value=K.ComplianceOut(supplier_id=s.id, status=d.status.value, reasons=d.reasons))

    async def regional_news(inp: K.NewsIn) -> ToolOutput[K.NewsOut]:
        arts = await news.fetch(inp.country, inp.horizon_days)
        block = "\n".join(f"[{i}] {a.title}: {a.text}" for i, a in enumerate(arts))
        p = build(bodies["regional_news_risk"], ALLOWED_VARS["regional_news_risk"], {"region": inp.country},
                  K.NewsExtraction, external_text=block)
        res = await llm.complete_json(project_id=project_id, agent_key="regional_news_risk", role="fast",
                                      template_hash=p.template_hash, system=p.system, user=p.user, schema=K.NewsExtraction)
        sigs = [s for s in res.data.signals if s.article_index < len(arts) and s.kind != "none"]
        injected = bool(p.injection_flags)
        if injected:  # flagged content can never move a decision: confidence capped, signals from flagged articles dropped
            bad = {i for i, a in enumerate(arts) if injection_flags(f"{a.title} {a.text}")}
            sigs = [s for s in sigs if s.article_index not in bad]
        sources = tuple({a.url: Source(url=a.url, title=a.title, date=a.date) for a in
                         (arts[s.article_index] for s in sigs)}.values()) or tuple(
            Source(url=a.url, title=a.title, date=a.date) for a in arts[:3])
        out = K.NewsOut(country=inp.country, max_severity=max((s.severity for s in sigs), default=0.0),
                        kinds=tuple(sorted({s.kind for s in sigs})), injection_suspected=injected)
        return ToolOutput[K.NewsOut](value=out, confidence=0.3 if (injected or res.repaired) else 0.8, sources=sources,
                                     assumptions=("news corpus is synthetic",) + (("injection suspected",) if injected else ()) + (("llm output repaired",) if res.repaired else ()))

    async def weather(inp: K.WeatherIn) -> ToolOutput[K.WeatherOut]:
        months, bump, driver = _SEASON.get(inp.origin_country, (set(), 0.0, ""))
        month = today.month
        in_season = month in months or ((month + inp.lead_time_weeks // 4 - 1) % 12 + 1) in months
        p = 0.04 + (bump if in_season else 0.0)
        drivers = [driver] if in_season and driver else []
        sources = [Source(url=f"synthetic://climatology/{inp.origin_country}", title="Synthetic climatology")]
        assumptions = ["synthetic climatology table; replace with a live forecast source in the Studio"]
        conf = 0.7
        if weather_wanted:  # a live source was asked for: if it cannot deliver, say so and lower confidence (never silently pass)
            try:
                if live_weather is None:
                    raise ConnectorError("NOT_AVAILABLE")
                sig = await live_weather.signal(inp.origin_country)
            except ConnectorError as e:
                conf = 0.4
                assumptions = [f"live weather unavailable ({e.code}); seasonal table used"]
            else:
                if sig.severe_days:
                    p += min(0.25, 0.05 * sig.severe_days)
                    drivers.append(f"severe weather forecast on {sig.severe_days} of the next {sig.horizon_days} days")
                conf = 0.75
                sources.append(Source(url=sig.source_url, title="Open-Meteo forecast"))
                assumptions = [f"{sig.horizon_days}-day forecast blended with the seasonal table; forecast is shorter than the lead time"]
        return ToolOutput[K.WeatherOut](
            value=K.WeatherOut(delay_probability=round(min(p, 1.0), 3), drivers=tuple(drivers)),
            confidence=conf, sources=tuple(sources), assumptions=tuple(assumptions))

    async def landed(inp: K.LandedIn) -> ToolOutput[K.LandedOut]:
        price = inp.unit_price
        fr = (price * Decimal(str(FREIGHT_PCT.get(inp.origin_country, 5))) / 100).quantize(Decimal("0.0001"))
        du = (price * Decimal(str(TARIFF_PCT.get(inp.origin_country, 10))) / 100).quantize(Decimal("0.0001"))
        rk = (price * Decimal(str(inp.delay_probability)) * inp.delay_penalty_pct / 100).quantize(Decimal("0.0001"))
        return ToolOutput[K.LandedOut](value=K.LandedOut(unit_price=price, freight=fr, duty=du, risk_cost=rk,
                                                          total=(price + fr + du + rk).quantize(Decimal("0.0001"))))

    return [
        Tool(K.RESEARCH, K.ResearchIn, K.ResearchOut, research, timeout_s=30),
        Tool(K.COMPLIANCE, K.ComplianceIn, K.ComplianceOut, compliance, timeout_s=5, retries=0),
        Tool(K.NEWS, K.NewsIn, K.NewsOut, regional_news, timeout_s=news_timeout_s),
        Tool(K.WEATHER, K.WeatherIn, K.WeatherOut, weather, timeout_s=5, retries=0),
        Tool(K.LANDED, K.LandedIn, K.LandedOut, landed, timeout_s=5, retries=0),
    ]
