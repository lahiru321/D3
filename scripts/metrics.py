"""PRD success metrics from the command log (logs/commands-*.jsonl) and LLM spend (data/llm_usage.json).

Usage: uv run python scripts/metrics.py [--days 7]
"""

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime, timedelta

from d3.config import load_config, resolve
from d3.router.llm import PRICE_IN, PRICE_OUT


def pct(n: int, d: int) -> str:
    return f"{n}/{d} = {n / d:.0%}" if d else "n/a"


def latency(rows: list[dict]) -> str:
    totals = sorted(r["timings_ms"]["total"] for r in rows if r.get("timings_ms"))
    if not totals:
        return "n/a"
    p90 = totals[min(len(totals) - 1, int(len(totals) * 0.9))]
    return f"median {statistics.median(totals):.0f} ms, p90 {p90:.0f} ms (n={len(totals)})"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    cfg = load_config()
    since = datetime.now().astimezone() - timedelta(days=args.days)

    rows = []
    for path in sorted(resolve(cfg["log"]["dir"]).glob("commands-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if datetime.fromisoformat(r["ts"]) >= since:
                rows.append(r)

    commands = [r for r in rows if r.get("event") == "command"]
    voice = [r for r in commands if r.get("trigger") != "click"]
    days = max(1, len({r["ts"][:10] for r in commands}))
    local = [r for r in voice if r.get("source") == "keyword" or (r.get("intent") and not r.get("llm"))]
    via_llm = [r for r in voice if r.get("llm")]
    opens = [r for r in voice if r.get("intent") == "open"]
    no_command = [r for r in rows if r.get("event") == "no_command"]

    print(f"Last {args.days} days: {len(commands)} commands on {days} day(s)\n")
    print(f"Command success rate     {pct(sum(r['ok'] for r in voice), len(voice))}   (target >= 95%)")
    print(f"Local latency            {latency([r for r in local if r['ok']])}   (target median < 1000 ms)")
    print(f"LLM-routed latency       {latency([r for r in via_llm if r['ok']])}   (target < 3000 ms)")
    print(f"File-open first try      {pct(sum(r['ok'] and not r.get('asked') for r in opens), len(opens))}   (target >= 85%)")
    print(f"Daily use                {len(commands) / days:.1f} commands/day   (target >= 20)")
    print(f"Wakes with no command    {sum(r.get('trigger') == 'wake' for r in no_command)}   (false-wake signal)")

    by_trigger = Counter(r.get("trigger") for r in commands)
    by_engine = Counter(r.get("engine") for r in voice)
    print(f"\nBy trigger: {dict(by_trigger)}   by engine: {dict(by_engine)}")
    failures = Counter(r.get("result", "").split("(")[0].strip() for r in voice if not r["ok"])
    if failures:
        print("Top failures:", ", ".join(f"{k} x{v}" for k, v in failures.most_common(5)))

    usage_path = resolve(cfg["data"]["dir"]) / "llm_usage.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else {}
    calls = sum(d["calls"] for d in usage.values())
    tin = sum(d["input_tokens"] for d in usage.values())
    tout = sum(d["output_tokens"] for d in usage.values())
    cost = (tin * PRICE_IN + tout * PRICE_OUT) / 1_000_000
    print(f"\nLLM all time: {calls} calls, {tin:,} in / {tout:,} out tokens, about ${cost:.3f} "
          f"(Haiku 4.5 list price; check the Anthropic Console for the real bill)")


if __name__ == "__main__":
    main()
