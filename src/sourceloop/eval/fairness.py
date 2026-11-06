"""Does misreporting a limit pay? A measurement, not a proof.

The open design question is whether the neutral platform is fair: a side that overstates its own limit might gain. Here the SAME deterministic
negotiation (replay mode, rule-based moves, synthetic data) runs once with truthful limits and again with one side's limit misreported by k:

* supplier: the awarded supplier reports a margin (so a floor) k points above its true 15 %. Its surplus is (price - TRUE floor) x volume, and 0 when
  it no longer wins the award.
* buyer: the buyer reports a highest price k % below its true ceiling. Its saving is (truthful price - price) x volume, and 0 when no deal is reached.

`python -m sourceloop.eval.fairness [--seeds 42 43 44] [--needs 12]` writes data/eval/fairness.md. The negotiation moves are rule-based (a live
model may behave differently), and supplier floors here follow cost plus margin; both limits and all prices are synthetic.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

from sourceloop.data.enums import OutcomeEnum
from sourceloop.eval.world import World, build_world
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion

SUPPLIER_POINTS = (0, 5, 10, 20, 40)   # extra margin points a supplier reports above its true margin
BUYER_PERCENT = (0, 5, 10, 20)         # percent a buyer reports its highest price below the truth
TRUE_MARGIN = Decimal("15")
ROOT = Path(__file__).resolve().parents[3]


async def _run(w: World, idx: int, **kw: Any):
    rt = build_project(ScenarioSpec(seed=w.seed, need_index=idx, **kw), w.vis, w.full, project_id=f"F{w.seed}N{idx:02d}")
    st = await run_to_completion(rt)
    deal = st.outcome is OutcomeEnum.DEAL and st.awarded is not None
    return rt, st, deal


async def measure(seeds: tuple[int, ...] = (42, 43, 44), n_needs: int = 12) -> dict[str, Any]:
    sup: dict[int, list[dict[str, Any]]] = {k: [] for k in SUPPLIER_POINTS}
    buy: dict[int, list[dict[str, Any]]] = {k: [] for k in BUYER_PERCENT}
    for seed in seeds:
        w = build_world(seed)
        for idx in range(min(n_needs, len(w.needs))):
            rt0, st0, deal0 = await _run(w, idx)
            if not deal0:
                continue
            win, vol = st0.awarded, rt0.need.volume_units
            p0 = Decimal(str(st0.deals[win]["price"]))
            cost = w.full.get(win).unit_cost
            true_floor = cost * (1 + TRUE_MARGIN / 100)
            s0 = max(Decimal(0), (p0 - true_floor) * vol)
            for k in SUPPLIER_POINTS:
                rt, st, deal = await _run(w, idx, supplier_margin={win: TRUE_MARGIN + k})
                won = deal and st.awarded == win
                price = Decimal(str(st.deals[win]["price"])) if won else None
                surplus = max(Decimal(0), (price - true_floor) * vol) if price is not None else Decimal(0)
                sup[k].append({"won": won, "deal": deal, "gain": surplus - s0, "price_delta": price - p0 if price is not None else None})
            factor = Decimal("1.45")
            for k in BUYER_PERCENT:
                rt, st, deal = await _run(w, idx, buyer_max_factor=factor * (1 - Decimal(k) / 100))
                price = Decimal(str(st.deals[st.awarded]["price"])) if deal else None
                saving = (p0 - price) * vol if price is not None else Decimal(0)
                buy[k].append({"deal": deal, "saving": saving, "price_delta": price - p0 if price is not None else None})

    def row(label: str, rs: list[dict[str, Any]], key: str, flag: str) -> list[Any]:
        n = len(rs)
        d = [r["price_delta"] for r in rs if r["price_delta"] is not None]
        return [label, n, f"{sum(r[flag] for r in rs) / n:.0%}" if n else "-", f"{sum(d) / len(d):+.3f}" if d else "-",
                f"{sum(r[key] for r in rs) / n:+.0f}" if n else "-"]
    return {"seeds": list(seeds),
            "supplier": [row(f"+{k} points", sup[k], "gain", "won") for k in SUPPLIER_POINTS],
            "buyer": [row(f"-{k} %", buy[k], "saving", "deal") for k in BUYER_PERCENT]}


def render(m: dict[str, Any]) -> str:
    def table(head: list[str], rows: list[list[Any]]) -> str:
        return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head), *("| " + " | ".join(str(c) for c in r) + " |" for r in rows)])
    return "\n".join([
        "# Does misreporting a limit pay?", "",
        f"Replay-mode negotiations on synthetic data, seeds {m['seeds']}. Rule-based moves; a live model may differ. Prices are synthetic.", "",
        "## A supplier that overstates its floor", "",
        "Only runs where the truthful supplier won are compared. Gain is the supplier's surplus over its TRUE floor (0 when it loses the award), versus telling the truth.", "",
        table(["Reported margin", "Runs", "Still wins", "Price change per unit", "Mean gain (currency)"], m["supplier"]), "",
        "## A buyer that understates its highest price", "",
        "Saving is the price paid versus the truthful run, times volume (0 when no deal is reached).", "",
        table(["Reported highest price", "Runs", "Deal reached", "Price change per unit", "Mean saving (currency)"], m["buyer"]), ""])


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m sourceloop.eval.fairness")
    ap.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44])
    ap.add_argument("--needs", type=int, default=12)
    ap.add_argument("--md", default=str(ROOT / "data" / "eval" / "fairness.md"))
    a = ap.parse_args()
    m = asyncio.run(measure(tuple(a.seeds), a.needs))
    out = Path(a.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(m), encoding="utf-8")
    print(render(m))


if __name__ == "__main__":
    main()
