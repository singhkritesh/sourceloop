"""Can a LOCAL model give typed, calibrated decisions (the 'Jev' idea)?  Usage: .venv/bin/python scripts/jev_concept.py [model ...]

Cases are negotiation turns labelled by the code judge (`policy/judge.py`). The model sees only relative figures and a plain-language rubric
(no thresholds) and answers a closed question; we read label probabilities from its first-token log-probs. Reports agreement, how often it
would SOFTEN a code verdict (the dangerous direction), calibration (accuracy per confidence band, ECE) and latency.
"""
import asyncio
import random
import sys
import time
from decimal import Decimal as D

from sourceloop.llm.decide import choice
from sourceloop.net.safe_http import SafeHttp
from sourceloop.policy.judge import Level, Turn, judge, turn_lines

OPTIONS = {"APPROPRIATE": "normal, good-faith bargaining", "CONCERN": "questionable conduct worth a human look",
           "INAPPROPRIATE": "clearly bad faith or irrational conduct"}
from sourceloop.llm.prompts_default import JUDGE_RUBRIC as RUBRIC  # noqa: E402  (measure exactly what the app sends)


def state_text(t: Turn) -> str:
    ln = turn_lines(t)   # the exact wording the app sends
    return f"Actor: {t.actor}\nAction: {t.action}\nRound {t.round} of {t.max_rounds}\n{ln['move_line']}\n{ln['gap_line']}\n{ln['index_line']}"


def make_cases(n_per: int, seed: int = 7) -> list[tuple[Turn, Level]]:
    rng = random.Random(seed)  # noqa: S311 - test data
    out: dict[Level, list] = {lv: [] for lv in Level}
    idx = D("100")
    guard = 0
    while min(len(v) for v in out.values()) < n_per and guard < 200000:
        guard += 1
        actor = rng.choice(["buyer", "supplier"])
        action = rng.choice(["counter", "counter", "counter", "accept", "walk"]) if actor == "buyer" else rng.choice(["counter", "counter", "reject"])
        rnd = rng.randint(1, 4)
        base = D(str(rng.randint(80, 125)))
        other = base * D(str(rng.choice([0.9, 1.0, 1.1, 1.2])))
        own_prev = None if rnd == 1 and rng.random() < .6 else base * D(str(rng.choice([0.85, 0.95, 1.0, 1.05, 1.15])))
        price = None if action in ("walk", "reject") else (other if action == "accept" else base * D(str(rng.choice([0.4, 0.6, 0.9, 0.97, 1.0, 1.003, 1.03, 1.1, 1.3]))))
        t = Turn(actor, action, rnd, 4, price and price.quantize(D("0.01")), own_prev and own_prev.quantize(D("0.01")), other.quantize(D("0.01")), idx, D("0.5"))
        lv = judge(t).level
        if len(out[lv]) < n_per:
            out[lv].append((t, lv))
    return [c for v in out.values() for c in v]


async def run_model(http, model: str, cases) -> dict:
    rows, t0 = [], time.perf_counter()
    for t, truth in cases:
        d = await choice(http, "http://localhost:11434", model, rubric=RUBRIC, state=state_text(t), options=OPTIONS)
        rows.append((truth.value, d))
    secs = time.perf_counter() - t0
    n = len(rows)
    acc = sum(tr == d.label for tr, d in rows) / n
    rank = {"APPROPRIATE": 0, "CONCERN": 1, "INAPPROPRIATE": 2}
    soften = [r for r in rows if rank[r[1].label] < rank[r[0]]]
    worst = [r for r in rows if r[0] == "INAPPROPRIATE"]
    sev_soft = sum(1 for tr, d in worst if d.label == "APPROPRIATE")
    adj = sum(abs(rank[tr] - rank[d.label]) <= 1 for tr, d in rows) / n
    bands, ece = [], 0.0
    for lo, hi in ((0, .34), (.34, .67), (.67, 1.01)):
        b = [(tr == d.label, d.probs[d.label]) for tr, d in rows if lo <= d.confidence < hi]
        if b:
            a = sum(x for x, _ in b) / len(b)
            bands.append((f"conf {lo:.2f}-{min(hi, 1):.2f}", len(b), a))
    for lo in [i / 5 for i in range(5)]:
        b = [(tr == d.label, d.probs[d.label]) for tr, d in rows if lo <= d.probs[d.label] < lo + .2 + (1e-9 if lo > .75 else 0)]
        if b:
            ece += len(b) / n * abs(sum(x for x, _ in b) / len(b) - sum(p for _, p in b) / len(b))
    flagged = lambda d: d.probs["CONCERN"] + d.probs["INAPPROPRIATE"] >= 0.5 and d.mass >= 0.5   # noqa: E731 - the app's rule
    ok_rows = [d for tr, d in rows if tr == "APPROPRIATE"]
    bad_rows = [d for tr, d in rows if tr != "APPROPRIATE"]
    noise = sum(map(flagged, ok_rows)) / max(1, len(ok_rows))
    catch = sum(map(flagged, bad_rows)) / max(1, len(bad_rows))
    return {"noise": noise, "catch": catch, "model": model, "n": n, "accuracy": acc, "within_one": adj, "softened": len(soften) / n, "inappropriate_called_appropriate": f"{sev_soft}/{len(worst)}",
            "ece": ece, "bands": bands, "ms_per_call": secs / n * 1000}


# Only these local models may be used for tests (owner rule): qwen up to 4b, gemma up to 12b. Everything else is refused.
ALLOWED = {"qwen3.5:4b", "gemma4:e2b-mlx", "gemma4:e4b-mlx", "gemma4:12b-mlx"}


async def main(models: list[str]) -> None:
    refused = [m for m in models if m not in ALLOWED]
    if refused:
        raise SystemExit(f"refused (not in the allowed set {sorted(ALLOWED)}): {refused}")
    cases = make_cases(20)
    print(f"{len(cases)} cases, truth = code judge: " + ", ".join(f"{lv.value}={sum(1 for _, x in cases if x is lv)}" for lv in Level))
    http = SafeHttp({"localhost"}, local_hosts={"localhost"}, timeout_s=300)
    for m in models:
        try:
            r = await run_model(http, m, cases)
        except Exception as e:  # noqa: BLE001 - report and move on to the next model
            print(f"\n{m}: FAILED {type(e).__name__}")
            continue
        finally:   # unload the model as soon as its task completes OR fails
            try:
                await http.post("http://localhost:11434/api/generate", json={"model": m, "keep_alive": 0})
            except Exception:  # noqa: BLE001, S110 - Ollama's 30 s keep-alive is the backstop
                pass
        print(f"\n{r['model']}\n  FLAGGING (what the app uses): noise on normal turns {r['noise']:.0%}   catch rate on turns the code marks CONCERN/INAPPROPRIATE {r['catch']:.0%}\n  accuracy {r['accuracy']:.0%}  within one level {r['within_one']:.0%}  softened a code verdict {r['softened']:.0%}"
              f"  INAPPROPRIATE called APPROPRIATE {r['inappropriate_called_appropriate']}\n  calibration error (ECE) {r['ece']:.3f}  {r['ms_per_call']:.0f} ms/call")
        for name, n, a in r["bands"]:
            print(f"  {name}: n={n} accuracy={a:.0%}")

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["gemma4:e2b-mlx", "qwen3.5:4b"]))
