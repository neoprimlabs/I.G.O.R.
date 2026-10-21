"""IGOR's model calls over the last N days, per agent, as a Markdown report.

Run on the server, which holds the journal:

    sudo /opt/igor/venv/bin/python /opt/igor/scripts/usage_report.py --days 30

Every model call logs one line through usage.record, for example:

    2026-10-20 16:40:14,667 INFO usage: Presence used 910 tokens (872 in, 38 out) on openai/gpt-oss-20b

Lines from 2026-10-22 also end "in 2310 ms": wall time per call, including the
client's own retries after a 429. Older lines have tokens only, so times cover
fewer calls than tokens do, and the report says how many.
"""

import argparse
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LINE = re.compile(
    r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2},\d+ INFO usage: (.+?) used (\d+) tokens "
    r"\((\d+) in, (\d+) out\) on (\S+?)(?: in (\d+) ms)?\s*$")


def parse(lines) -> list[dict]:
    calls = []
    for line in lines:
        m = LINE.search(line)
        if not m:
            continue
        day, label, total, tin, tout, model, ms = m.groups()
        calls.append({"day": day, "label": label, "tokens": int(total), "in": int(tin),
                      "out": int(tout), "model": model, "ms": int(ms) if ms else None})
    return calls


def _secs(values: list[int], q: float) -> str:
    if not values:
        return "-"
    ordered = sorted(values)
    return f"{ordered[min(len(ordered) - 1, int(q * len(ordered)))] / 1000:.1f}s"


def report(calls: list[dict], caps: dict[str, int]) -> str:
    if not calls:
        return "No model calls found."
    days = sorted({c["day"] for c in calls})
    total = sum(c["tokens"] for c in calls)
    lines = [f"Model calls from {days[0]} to {days[-1]}: {len(days)} days with calls, "
             f"{len(calls):,} calls, {total:,} tokens "
             f"(about {len(calls) // len(days):,} calls and {total // len(days):,} tokens a day).",
             "", "| Agent | Calls | Tokens per call | Share of tokens | Median time | Slowest 5% |",
             "|---|---:|---:|---:|---:|---:|"]
    by_label = defaultdict(list)
    for c in calls:
        by_label[c["label"]].append(c)
    timed = 0
    for label, group in sorted(by_label.items(), key=lambda kv: -sum(c["tokens"] for c in kv[1])):
        tokens = sum(c["tokens"] for c in group)
        times = [c["ms"] for c in group if c["ms"] is not None]
        timed += len(times)
        lines.append(f"| {label} | {len(group):,} | {tokens // len(group):,} | "
                     f"{100 * tokens / total:.1f}% | {_secs(times, 0.5)} | {_secs(times, 0.95)} |")
    lines += ["", f"Times cover {timed:,} of {len(calls):,} calls, the ones logged with a duration.",
              "", "| Model | Daily cap | Median day | Busiest day |", "|---|---:|---:|---:|"]
    per_day = defaultdict(lambda: defaultdict(int))
    for c in calls:
        per_day[c["model"]][c["day"]] += c["tokens"]
    for model, by_day in sorted(per_day.items()):
        values = list(by_day.values())
        cap = caps.get(model)
        busiest_day = max(by_day, key=by_day.get)

        def share(n):
            return f"{n:,} ({100 * n / cap:.0f}%)" if cap else f"{n:,}"

        lines.append(f"| {model} | {f'{cap:,}' if cap else 'not measured'} | "
                     f"{share(int(statistics.median(values)))} | "
                     f"{share(by_day[busiest_day])}, {busiest_day} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    import config
    out = subprocess.run(["journalctl", "-u", "igor", "--since", f"{args.days} days ago",
                          "--no-pager", "-o", "cat"], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"journalctl failed: {out.stderr.strip()}")
    print(report(parse(out.stdout.splitlines()), config.DAILY_TOKEN_CAPS))


if __name__ == "__main__":
    main()
