"""The single contract between routers (keyword, later LLM) and the executor."""

from dataclasses import dataclass, field
from typing import Any

PAUSE = "pause"
RESUME = "resume"
NEXT = "next"
PREVIOUS = "previous"
VOLUME_UP = "volume_up"
VOLUME_DOWN = "volume_down"
MUTE = "mute"
UNMUTE = "unmute"
CHOOSE = "choose"   # args: {"n": 1..3}
CANCEL = "cancel"
OPEN = "open"       # args: {"target": str}
CLOSE = "close"     # args: {"target": str}; "this" = the window in front


@dataclass(frozen=True)
class Intent:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    source: str = "keyword"  # keyword | llm
    text: str = ""           # the transcript it came from
