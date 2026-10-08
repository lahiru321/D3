"""Runs an Intent against the handlers. Router-agnostic.

Holds the pending "which one?" options between an ambiguous `open` and the
follow-up "first / second / third".
"""

import os
from dataclasses import dataclass

from d3 import intents as I
from d3 import resolver as R
from d3.handlers.media import MediaController
from d3.handlers.volume import VolumeController
from d3.intents import Intent
from d3.resolver import Resolver
from d3.search.apps import launch_or_focus
from d3.search.files import Candidate

ORDINALS = ["First", "Second", "Third"]


@dataclass
class Result:
    ok: bool
    message: str                 # on-screen / log text
    question: str | None = None  # spoken; the app then listens for an answer without the wake word


class Executor:
    def __init__(self, media: MediaController, volume: VolumeController, resolver: Resolver | None) -> None:
        self.media = media
        self.volume = volume
        self.resolver = resolver
        self._pending: list[Candidate] = []
        self._pending_words: list[str] = []
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
        return bool(self._pending)

    def clear_pending(self) -> None:
        self._pending, self._pending_words = [], []

    def run(self, intent: Intent) -> Result:
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
            return self._open_candidate(res.candidates[0], res.words)
        if res.kind == R.CHOOSE:
            self._pending, self._pending_words = res.candidates, res.words
            spoken = " ".join(f"{ORDINALS[i]}: {c.display}." for i, c in enumerate(res.candidates))
            lines = "\n".join(f"{i + 1}. {c.path}" for i, c in enumerate(res.candidates))
            return Result(True, f"Which one?\n{lines}",
                          question=f"I found {len(res.candidates)}. {spoken} Which one?")
        return Result(False, f"Couldn't find {target}" + (f" ({res.note})" if res.note else ""))

    def _choose(self, n: int) -> Result:
        if not self._pending:
            return Result(False, "Nothing to choose from")
        if n > len(self._pending):
            return Result(False, f"There are only {len(self._pending)} options")
        candidate, words = self._pending[n - 1], self._pending_words
        self.clear_pending()
        return self._open_candidate(candidate, words)

    def _open_candidate(self, candidate: Candidate, words: list[str]) -> Result:
        if not candidate.path.exists():
            return Result(False, f"{candidate.path.name} no longer exists")
        os.startfile(candidate.path)
        if self.resolver is not None:
            self.resolver.memory.record(words, candidate.path)
        return Result(True, f"Opening {candidate.path.name}")
