"""Gate 2: first-try accuracy of "open ..." on bench/open_cases.txt. Opens nothing.

  first try  - D3 would open the expected item straight away
  asked      - D3 would ask "which one?" and the expected item is among the options
  wrong      - D3 would open something else, or find nothing

Usage: uv run python scripts/eval_open.py [cases file]
"""

import sys
from pathlib import Path

from d3 import resolver as R
from d3.config import ROOT, load_config
from d3.resolver import Resolver


def load_cases(path: Path) -> list[tuple[str, str]]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "|" in line:
            phrase, expected = (part.strip() for part in line.split("|", 1))
            cases.append((phrase, expected.lower()))
    return cases


def describe(res: R.Resolution) -> list[str]:
    if res.kind == R.FOLDER:
        return [str(res.folder)]
    if res.kind == R.APP:
        return [res.app.name]
    if res.kind == R.EXE:
        return [res.exe]
    return [str(c.path) for c in res.candidates]


def main() -> None:
    cases_file = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "bench" / "open_cases.txt"
    resolver = Resolver(load_config())
    counts = {"first try": 0, "asked": 0, "wrong": 0}

    for phrase, expected in load_cases(cases_file):
        res = resolver.resolve(phrase)
        found = describe(res)
        if res.kind in (R.FOLDER, R.APP, R.EXE, R.FILE) and found and expected in found[0].lower():
            verdict = "first try"
        elif res.kind == R.CHOOSE and any(expected in f.lower() for f in found):
            verdict = "asked"
        else:
            verdict = "wrong"
        counts[verdict] += 1
        shown = found[0] if found else res.note or "nothing"
        print(f"{verdict:<10} {phrase!r:<40} -> {res.kind:<7} {shown}")

    total = sum(counts.values())
    if total:
        print(f"\nFirst-try accuracy: {counts['first try']}/{total} = {counts['first try'] / total:.0%} "
              f"(target >= 85%); asked: {counts['asked']}; wrong: {counts['wrong']}")


if __name__ == "__main__":
    main()
