"""CLI: python -m sourceloop.eval [--seeds 42 43 44] [--no-ablations] [--live] [--md docs/evaluation.md]"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sourceloop.eval.report import canonical_json, render_markdown, results_hash
from sourceloop.eval.suite import run_suite

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m sourceloop.eval")
    ap.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44])
    ap.add_argument("--needs", type=int, default=12)
    ap.add_argument("--no-ablations", action="store_true")
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "eval"))
    ap.add_argument("--md", default=None, help="markdown path (default: <out-dir>/evaluation.md)")
    ap.add_argument("--live", action="store_true", help="use a local Ollama model for news-risk extraction (skips ablations)")
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--fast", default="gemma4:e2b-mlx")
    ap.add_argument("--chat", default="qwen3.8:27b-mlx")
    a = ap.parse_args()

    hook = None
    prov = None
    if a.live:
        from sourceloop.demo import live_ollama

        cfg, providers, prov = live_ollama(a.ollama_url, a.fast, a.chat)

        def hook(spec):  # type: ignore[no-redef]
            spec.llm_cfg, spec.llm_providers = cfg, providers
            spec.news_timeout_s, spec.news_concurrency = 150.0, 2

    async def go():
        try:
            return await run_suite(tuple(a.seeds), a.needs, live_hook=hook, run_ablations=not (a.no_ablations or a.live))
        finally:
            if prov is not None:   # unload the moment the suite completes or fails
                for m in {a.fast, a.chat}:
                    await prov.release(m)

    data = asyncio.run(go())
    data["results_hash"] = results_hash(data["results"])
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(canonical_json(data), encoding="utf-8")
    md = render_markdown(data)
    md_path = Path(a.md) if a.md else out / "evaluation.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nwrote {out / 'results.json'} and {md_path}  (results sha256 {data['results_hash'][:16]})")


if __name__ == "__main__":
    main()
