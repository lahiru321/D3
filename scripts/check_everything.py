"""Confirm the Everything SDK can query the index, and time a few searches.

Usage: uv run python scripts/check_everything.py [query ...]
"""

import sys
import time

from d3.search.everything import Everything, EverythingError


def main() -> None:
    queries = sys.argv[1:] or ["invoice", "proposal", "storex", "downloads"]
    ev = Everything()
    try:
        print(f"Everything {ev.version()}, database loaded: {ev.is_db_loaded()}")
    except EverythingError as exc:
        raise SystemExit(f"{exc}. Start Everything (or its service) and try again.")

    for q in queries:
        t = time.perf_counter()
        results = ev.search(q, max_results=5, sort_recent=True)
        ms = (time.perf_counter() - t) * 1000
        print(f"\n'{q}': {len(results)} shown, {ms:.0f} ms")
        for r in results:
            when = r.modified.strftime("%Y-%m-%d") if r.modified else "?"
            print(f"  {when}  {r.path}")


if __name__ == "__main__":
    main()
