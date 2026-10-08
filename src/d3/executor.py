"""Runs an Intent against the handlers. Router-agnostic.

Holds the pending "which one?" options between an ambiguous `open` and the
choice, which arrives either as a spoken "first / second / third" (pipeline
thread) or a click on the on-screen list (OSD thread) - hence the lock.
"""

import os
import threading
import time
from dataclasses import dataclass

from d3 import intents as I
from d3 import resolver as R
from d3.handlers.media import MediaController
from d3.handlers.volume import VolumeController
from d3.intents import Intent
from d3.resolver import Resolver
from d3.search.apps import launch_or_focus
from d3.search.files import Candidate

@dataclass
class Result:
    ok: bool
    message: str                                  # on-screen / log text
    choices: list[tuple[str, str]] | None = None  # "which one?" rows: (name, folder) to display


class Executor:
    def __init__(self, media: MediaController, volume: VolumeController, resolver: Resolver | None,
                 choice_timeout_s: float = 30.0) -> None:
        self.media = media
        self.volume = volume
        self.resolver = resolver
        self.choice_timeout_s = choice_timeout_s
        self._lock = threading.RLock()
        self._pending: list[Candidate] = []
        self._pending_words: list[str] = []
        self._pending_until = 0.0
        self._actions = {
            I.PAUSE: media.pause,
            I.RESUME: media.resume,
            I.NEXT: media.next,
            I.PREVIOUS: media.previous,
            I.VOLUME_UP: volume.up,
            I.VOLUME_DOWN: volume.down,
            I.MUTE: volume.mute,
            I.UNMUTE: volume.unmute,
        }

    @property
    def awaiting_choice(self) -> bool:
        return bool(self._pending) and time.monotonic() < self._pending_until

    def clear_pending(self) -> None:
        self._pending, self._pending_words = [], []

    def run(self, intent: Intent) -> Result:
        with self._lock:
            try:
                return self._run(intent)
            except Exception as exc:  # a handler failure must not kill the assistant
                return Result(False, f"{intent.name} failed: {exc}")

    def _run(self, intent: Intent) -> Result:
        if intent.name in self._actions:
            self.clear_pending()
            return Result(*self._actions[intent.name]())
        if intent.name == I.CANCEL:
            self.clear_pending()
            return Result(True, "Cancelled")
        if intent.name == I.CHOOSE:
            return self._choose(intent.args["n"])
        if intent.name == I.OPEN:
            self.clear_pending()
            return self._open(intent.args["target"])
        return Result(False, f"Unknown intent {intent.name}")

    def _open(self, target: str) -> Result:
        if self.resolver is None:
            return Result(False, "Opening things is unavailable")
        res = self.resolver.resolve(target)
        if res.kind == R.FOLDER:
            os.startfile(res.folder)
            return Result(True, f"Opening {res.folder.name or res.folder}")
        if res.kind == R.APP:
            return Result(True, launch_or_focus(res.app))
        if res.kind == R.EXE:
            os.startfile(res.exe)
            return Result(True, f"Opening {target}")
        if res.kind == R.FILE:
            result = self._open_candidate(res.candidates[0])
            if res.note:  # e.g. corrected spelling: show what was matched
                result.message += f"  ({res.note})"
            return result
        if res.kind == R.CHOOSE:
            self._pending, self._pending_words = res.candidates, res.words
            self._pending_until = time.monotonic() + self.choice_timeout_s
            lines = "\n".join(f"{i + 1}. {c.path}" for i, c in enumerate(res.candidates))
            rows = [(c.path.name + ("  (folder)" if c.is_folder else ""), str(c.path.parent)) for c in res.candidates]
            return Result(True, f"Which one?\n{lines}", choices=rows)
        return Result(False, f"Couldn't find {target}" + (f" ({res.note})" if res.note else ""))

    def _choose(self, n: int) -> Result:
        if not self.awaiting_choice:
            self.clear_pending()
            return Result(False, "Nothing to choose from")
        if n > len(self._pending):
            return Result(False, f"There are only {len(self._pending)} options")
        candidate, words = self._pending[n - 1], self._pending_words
        self.clear_pending()
        result = self._open_candidate(candidate)
        # Learn only from explicit picks: recording D3's own guesses would reinforce wrong ones.
        if result.ok and self.resolver is not None:
            self.resolver.memory.record(words, candidate.path)
        return result

    @staticmethod
    def _open_candidate(candidate: Candidate) -> Result:
        if not candidate.path.exists():
            return Result(False, f"{candidate.path.name} no longer exists")
        os.startfile(candidate.path)
        return Result(True, f"Opening {candidate.path.name}")
