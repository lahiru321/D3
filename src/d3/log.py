"""Local JSONL command log (FR-16). Text and timings only, never audio."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class CommandLog:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._dir = directory

    def write(self, **record: Any) -> None:
        now = datetime.now().astimezone()
        record = {"ts": now.isoformat(timespec="milliseconds"), **record}
        path = self._dir / f"commands-{now:%Y-%m}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
