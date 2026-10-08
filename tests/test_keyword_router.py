import pytest

from d3 import intents as I
from d3.router.keyword import normalize, route


@pytest.mark.parametrize("text, name, args", [
    ("pause", I.PAUSE, {}),
    ("Pause.", I.PAUSE, {}),
    ("stop", I.PAUSE, {}),
    ("hold on", I.PAUSE, {}),
    ("pause the video please", I.PAUSE, {}),
    ("continue", I.RESUME, {}),
    ("Resume!", I.RESUME, {}),
    ("play", I.RESUME, {}),
    ("next", I.NEXT, {}),
    ("skip this song", I.NEXT, {}),
    ("previous, previous,", I.PREVIOUS, {}),   # Whisper repetition
    ("go back", I.PREVIOUS, {}),
    ("volume up", I.VOLUME_UP, {}),
    ("louder", I.VOLUME_UP, {}),
    ("volume down", I.VOLUME_DOWN, {}),
    ("quieter", I.VOLUME_DOWN, {}),
    ("mute", I.MUTE, {}),
    ("unmute, unmute.", I.UNMUTE, {}),
    ("on mute", I.UNMUTE, {}),                 # how Vosk hears "unmute"
    ("first", I.CHOOSE, {"n": 1}),
    ("second", I.CHOOSE, {"n": 2}),
    ("third", I.CHOOSE, {"n": 3}),
    ("cancel", I.CANCEL, {}),
    ("never mind", I.CANCEL, {}),
    ("volume upp", I.VOLUME_UP, {}),           # fuzzy
    ("Open the StoreX proposal.", I.OPEN, {"target": "the storex proposal"}),
    ("launch VS Code", I.OPEN, {"target": "vs code"}),
    ("show me my downloads", I.OPEN, {"target": "my downloads"}),
])
def test_routes(text, name, args):
    intent = route(text)
    assert intent is not None
    assert (intent.name, intent.args) == (name, args)


@pytest.mark.parametrize("text", [
    "play some music",       # must not resume
    "stop the presses now and tell me a story",
    "Pulse,",                # Whisper mishearing of "pause": reject, don't guess
    "I see you.",            # Whisper mishearing of "resume"
    "Thank you.",
    "",
])
def test_rejects(text):
    assert route(text) is None


def test_fuzzy_can_be_disabled():
    assert route("volume upp", fuzzy=False) is None


def test_normalize_dedupes_and_strips_punctuation():
    assert normalize("Previous, previous, PREVIOUS.") == "previous"
