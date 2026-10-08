"""Local keyword router: transcript -> Intent, no network.

Short media/volume commands must be the *whole* utterance (after dropping filler
words), so "play some music" never resumes playback by accident.
"""

import re

from rapidfuzz import fuzz, process

from d3 import intents as I
from d3.intents import Intent

# Phrase -> (intent, args). Also the Vosk grammar, so include the spellings Vosk
# produces for words outside its vocabulary ("unmute" is heard as "on mute").
COMMANDS: dict[str, tuple[str, dict]] = {
    "pause": (I.PAUSE, {}),
    "stop": (I.PAUSE, {}),
    "hold on": (I.PAUSE, {}),
    "continue": (I.RESUME, {}),
    "resume": (I.RESUME, {}),
    "play": (I.RESUME, {}),
    "next": (I.NEXT, {}),
    "skip": (I.NEXT, {}),
    "previous": (I.PREVIOUS, {}),
    "go back": (I.PREVIOUS, {}),
    "volume up": (I.VOLUME_UP, {}),
    "louder": (I.VOLUME_UP, {}),
    "volume down": (I.VOLUME_DOWN, {}),
    "quieter": (I.VOLUME_DOWN, {}),
    "mute": (I.MUTE, {}),
    "unmute": (I.UNMUTE, {}),
    "on mute": (I.UNMUTE, {}),
    "first": (I.CHOOSE, {"n": 1}),
    "second": (I.CHOOSE, {"n": 2}),
    "third": (I.CHOOSE, {"n": 3}),
    "one": (I.CHOOSE, {"n": 1}),
    "two": (I.CHOOSE, {"n": 2}),
    "three": (I.CHOOSE, {"n": 3}),
    "cancel": (I.CANCEL, {}),
    "never mind": (I.CANCEL, {}),
}

# Dropped before matching short commands ("pause the video please" -> "pause").
FILLER = {
    "please", "now", "d3", "hey", "jarvis", "ok", "okay", "the", "it", "this", "that",
    "video", "music", "song", "track", "playback", "one",
}

def add_aliases(aliases: dict[str, str]) -> None:
    """User phrases for built-in commands, from config [commands.aliases]: {"shut up": "mute"}.
    Call before the Transcriber is built so the aliases join Vosk's grammar."""
    for phrase, target in aliases.items():
        target_key = normalize(target)
        if target_key not in COMMANDS:
            raise ValueError(f"Alias {phrase!r} points to unknown command {target!r}; use one of {sorted(COMMANDS)}")
        COMMANDS[normalize(phrase)] = COMMANDS[target_key]


OPEN_RE = re.compile(r"^(?:open|launch|start|show me|show)\s+(?P<target>.+)$")
CLOSE_RE = re.compile(r"^(?:close|quit|exit)\s+(?P<target>.+)$")

FUZZY_CUTOFF = 85


def normalize(text: str) -> str:
    text = re.sub(r"[^a-z0-9' ]+", " ", text.lower())
    words = text.split()
    deduped = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]  # "previous previous"
    return " ".join(deduped)


def route(text: str, fuzzy: bool = True) -> Intent | None:
    """Return the Intent for a transcript, or None if the router can't match it."""
    norm = normalize(text)
    if not norm:
        return None

    m = OPEN_RE.match(norm)
    if m:
        return Intent(I.OPEN, {"target": m.group("target")}, text=text)
    m = CLOSE_RE.match(norm)
    if m:
        return Intent(I.CLOSE, {"target": m.group("target")}, text=text)

    core = " ".join(w for w in norm.split() if w not in FILLER) or norm
    if core in COMMANDS:
        name, args = COMMANDS[core]
        return Intent(name, dict(args), text=text)

    if fuzzy and len(core.split()) <= 3:
        hit = process.extractOne(core, COMMANDS.keys(), scorer=fuzz.ratio, score_cutoff=FUZZY_CUTOFF)
        if hit:
            name, args = COMMANDS[hit[0]]
            return Intent(name, dict(args), text=text)
    return None
