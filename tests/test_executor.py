from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from d3 import intents as I
from d3 import resolver as R
from d3.executor import Executor
from d3.intents import Intent
from d3.search.files import Candidate, ChoiceMemory


class FakeResolver:
    def __init__(self, tmp_path: Path, resolution: R.Resolution) -> None:
        self.memory = ChoiceMemory(tmp_path / "choices.json")
        self._resolution = resolution

    def resolve(self, target: str) -> R.Resolution:
        return self._resolution


def make(tmp_path, kind, paths):
    files = []
    for name in paths:
        p = tmp_path / name
        p.write_text("x")
        files.append(Candidate(p, False, datetime.now(timezone.utc)))
    resolver = FakeResolver(tmp_path, R.Resolution(kind, "lumora", candidates=files, words=["lumora"]))
    noop = lambda: (True, "")  # noqa: E731
    media = SimpleNamespace(pause=noop, resume=noop, next=noop, previous=noop)
    volume = SimpleNamespace(up=noop, down=noop, mute=noop, unmute=noop)
    return Executor(media, volume, resolver), resolver, files


def test_auto_open_does_not_teach_itself(tmp_path, monkeypatch):
    # Live bug: each wrong auto-open was recorded as a choice and reinforced itself.
    monkeypatch.setattr("os.startfile", lambda p: None)
    executor, resolver, files = make(tmp_path, R.FILE, ["a.png"])
    assert executor.run(Intent(I.OPEN, {"target": "lumora"})).ok
    assert resolver.memory.count(["lumora"], files[0].path) == (0, 0)


def test_explicit_choice_is_learned_and_choices_expire(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("os.startfile", opened.append)
    executor, resolver, files = make(tmp_path, R.CHOOSE, ["a.png", "b.png"])

    result = executor.run(Intent(I.OPEN, {"target": "lumora"}))
    assert result.choices and len(result.choices) == 2 and not opened

    assert executor.run(Intent(I.CHOOSE, {"n": 2})).ok
    assert opened == [files[1].path]
    assert resolver.memory.count(["lumora"], files[1].path) == (1, 1)
    assert not executor.run(Intent(I.CHOOSE, {"n": 1})).ok  # already chosen

    executor.run(Intent(I.OPEN, {"target": "lumora"}))
    executor._pending_until = 0  # time out
    assert not executor.run(Intent(I.CHOOSE, {"n": 1})).ok
