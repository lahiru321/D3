"""Show how D3 would resolve "open <target>" phrases, with score breakdowns. Opens nothing.

Usage: uv run python scripts/try_open.py "the storex proposal" "my downloads" "vs code"
"""

import sys
import time

from d3.config import load_config
from d3.resolver import Resolver


def main() -> None:
    resolver = Resolver(load_config())
    for target in sys.argv[1:]:
        t0 = time.perf_counter()
        res = resolver.resolve(target)
        ms = (time.perf_counter() - t0) * 1000
        print(f"\n{target!r} -> {res.kind}  ({ms:.0f} ms)  {res.note}")
        for c in res.candidates[:3]:
            print(f"   {c.score:5.1f}  {c.parts}  {c.path}")
        if res.app:
            print(f"   app: {res.app.name} ({res.app.app_id})")
        if res.folder:
            print(f"   folder: {res.folder}")


if __name__ == "__main__":
    main()
