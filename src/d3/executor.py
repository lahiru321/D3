"""Runs an Intent against the handlers. Router-agnostic."""

from d3 import intents as I
from d3.handlers.media import MediaController
from d3.handlers.volume import VolumeController
from d3.intents import Intent


class Executor:
    def __init__(self, media: MediaController, volume: VolumeController) -> None:
        self.media = media
        self.volume = volume
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

    def run(self, intent: Intent) -> tuple[bool, str]:
        if intent.name in self._actions:
            try:
                return self._actions[intent.name]()
            except Exception as exc:  # a handler failure must not kill the assistant
                return False, f"{intent.name} failed: {exc}"
        if intent.name == I.CANCEL:
            return True, "Cancelled"
        if intent.name == I.CHOOSE:
            return False, "Nothing to choose from"
        if intent.name == I.OPEN:
            return False, f"Opening files comes in Phase 2 ({intent.args['target']!r})"
        return False, f"Unknown intent {intent.name}"
