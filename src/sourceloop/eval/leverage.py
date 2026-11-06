"""Does stating competing offers move the price? A measurement, not a proof.

The SAME deterministic negotiation (replay mode, rule-based moves, synthetic data) runs once without and once with the buyer agent's leverage
statements (closed signals and a rounded price band, see `policy/leverage.py`). A supplier that hears a clear price gap against a rival moves its planned
counter towards the buyer, never below its own floor. Reported: how often a statement was made, how often the deal and the winner stayed the same, the
price change per unit, and the saving in currency (price without leverage minus price with it, times volume; 0 when no deal is reached either way).

`python -m sourceloop.eval.leverage [--seeds 42 43 44] [--needs 12]` writes data/eval/leverage.md. Rule-based moves only: a live model may react differently.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
from decimal import Decimal
from pathlib import Path
from typing import Any

from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import OutcomeEnum
from sourceloop.eval.world import World, build_world
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion

ROOT = Path(__file__).resolve().parents[3]


async def _run(w: World, idx: int, leverage: bool):
    spec = ScenarioSpec(seed=w.seed, need_index=idx)
    spec.run = dataclasses.replace(spec.run, leverage=leverage)
    rt = build_project(spec, w.vis, w.full, project_id=f"L{w.seed}N{idx:02d}")
    st = await run_to_completion(rt)
    deal = st.outcome is OutcomeEnum.DEAL and st.awarded is not None
    return rt, st, deal


async def measure(seeds: tuple[int, ...] = (42, 43, 44), n_needs: int = 12) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for seed in seeds:
        w = build_world(seed)
        for idx in range(min(n_needs, len(w.needs))):
            rt0, st0, d0 = await _run(w, idx, False)
            rt1, st1, d1 = await _run(w, idx, True)
            said = [e for e in rt1.bus.log if e.type is T.LEVERAGE]
            vol = rt0.need.volume_units
            p0 = Decimal(str(st0.deals[st0.awarded]["price"])) if d0 else None
            p1 = Decimal(str(st1.deals[st1.awarded]["price"])) if d1 else None
            floor_ok = all(Decimal(str(e.payload["unit_price"])) >= rt1.supplier(e.from_agent.split(":", 1)[1]).mandate.min_unit_price
                           for e in rt1.bus.log if e.type in (T.COUNTER, T.OFFER) and e.from_agent.startswith("supplier:"))
            rows.append({"said": bool(said), "price_gap": any("PRICE_BELOW" in e.payload["signals"] for e in said), "d0": d0, "d1": d1,
                         "same_winner": d0 and d1 and st0.awarded == st1.awarded, "floor_ok": floor_ok,
                         "delta": (p1 - p0) if p0 is not None and p1 is not None else None,
                         "saving": ((p0 - p1) * vol) if p0 is not None and p1 is not None else Decimal(0)})
    n = len(rows)
    both = [r for r in rows if r["delta"] is not None]
    gap = [r for r in both if r["price_gap"]]

    def mean(vs: list[Decimal]) -> str:
        return f"{sum(vs) / len(vs):+.3f}" if vs else "-"
    pct = lambda k, rs=rows: f"{sum(1 for r in rs if r[k]) / len(rs):.0%}" if rs else "-"  # noqa: E731
    return {"seeds": list(seeds), "runs": n,
            "table": [["All runs", n, pct("said"), pct("d0"), pct("d1"), mean([r["delta"] for r in both]), mean([r["saving"] for r in both])],
                      ["Runs where a rival was cheaper", len(gap), pct("said", gap), pct("d0", gap), pct("d1", gap), mean([r["delta"] for r in gap]),
                       mean([r["saving"] for r in gap])]],
            "floor_respected": all(r["floor_ok"] for r in rows), "winner_changed": sum(1 for r in both if not r["same_winner"])}


def render(m: dict[str, Any]) -> str:
    head = ["Runs", "Count", "A statement was made", "Deal without", "Deal with", "Price change per unit", "Mean saving (currency)"]
    t = "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head), *("| " + " | ".join(str(c) for c in r) + " |" for r in m["table"])])
    return "\n".join([
        "# Does stating competing offers move the price?", "",
        f"Replay-mode negotiations on synthetic data, seeds {m['seeds']}, {m['runs']} runs, each run twice (without and with the statement). "
        "Rule-based moves; a live model may react differently. Prices are synthetic.", "", t, "",
        f"Every supplier price stayed at or above that supplier's own floor: {'yes' if m['floor_respected'] else 'NO'}. "
        f"Runs where a different supplier won once the statement was made: {m['winner_changed']}.", ""])


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m sourceloop.eval.leverage")
    ap.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44])
    ap.add_argument("--needs", type=int, default=12)
    ap.add_argument("--md", default=str(ROOT / "data" / "eval" / "leverage.md"))
    a = ap.parse_args()
    m = asyncio.run(measure(tuple(a.seeds), a.needs))
    out = Path(a.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(m), encoding="utf-8")
    print(render(m))


if __name__ == "__main__":
    main()
