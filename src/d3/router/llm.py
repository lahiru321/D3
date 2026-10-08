"""LLM fallback router (FR-5): transcript -> Intent via Claude tool use.

Only used when the local keyword router can't match. Text only, never audio.
One request per command: Claude must answer with exactly one tool call that
mirrors D3's intents (forced tool choice), so D3 executes it locally as usual.
No strict schemas: their first-use grammar compile pushed calls past the timeout;
the reply is validated here instead.
Guardrails for a small budget: daily call cap, short timeout, no retries,
small max_tokens, and every call's token usage recorded.
"""

import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from d3 import intents as I
from d3.intents import Intent

# Haiku 4.5 list prices, USD per million tokens (for the spend estimate only).
PRICE_IN, PRICE_OUT = 1.00, 5.00

SYSTEM = """You turn one spoken command for D3, a Windows voice assistant, into exactly one tool call.
The text comes from speech recognition, so words may be misheard: use the user's folder and app names below to correct them.
For open_item, give the target as the name would appear on disk or in the Start menu, without filler words. Keep date words ("last month's", "yesterday's") and type words ("pdf", "folder").
If the request isn't one of these actions, or is too unclear to act on, call not_understood. Never guess.

User's folder and app names: {names}"""


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties,
                         "required": list(properties), "additionalProperties": False},
    }


TOOLS = [
    _tool("open_item", "Open a file, folder or installed app on this PC, or switch to the app if it is open.",
          {"target": {"type": "string", "description": "What to open, e.g. 'lumora folder', 'last month's invoice', 'VS Code'"}}),
    _tool("close_app", "Close an app's windows (like clicking X), or the window in front when target is 'this'.",
          {"target": {"type": "string", "description": "App name as in the Start menu, e.g. 'Brave', or 'this'"}}),
    _tool("media_control", "Control whatever media is playing (video or music).",
          {"action": {"type": "string", "enum": ["pause", "resume", "next", "previous"]}}),
    _tool("set_volume", "Change the system volume.",
          {"action": {"type": "string", "enum": ["up", "down", "mute", "unmute"]}}),
    _tool("choose_option", "Pick an option from the 'which one?' list D3 is showing.",
          {"n": {"type": "integer", "enum": [1, 2, 3]}}),
    _tool("cancel", "Cancel the current command or the 'which one?' list.", {}),
    _tool("not_understood", "The request is not one of the other actions, or is too unclear.", {}),
]

MEDIA = {"pause": I.PAUSE, "resume": I.RESUME, "next": I.NEXT, "previous": I.PREVIOUS}
VOLUME = {"up": I.VOLUME_UP, "down": I.VOLUME_DOWN, "mute": I.MUTE, "unmute": I.UNMUTE}


@dataclass
class LlmResult:
    intent: Intent | None
    note: str
    ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens * PRICE_IN + self.output_tokens * PRICE_OUT) / 1_000_000


class UsageLedger:
    """Calls and tokens per day in data/llm_usage.json, for the daily cap and spend reports."""

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            self._data: dict[str, dict[str, int]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    def today(self) -> dict[str, int]:
        return self._data.get(date.today().isoformat(), {"calls": 0, "input_tokens": 0, "output_tokens": 0})

    def add(self, input_tokens: int, output_tokens: int) -> None:
        day = self._data.setdefault(date.today().isoformat(), {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        day["calls"] += 1
        day["input_tokens"] += input_tokens
        day["output_tokens"] += output_tokens
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=1), encoding="utf-8")


class LlmRouter:
    def __init__(self, cfg: dict, ledger: UsageLedger, names: list[str]) -> None:
        self._model = cfg["model"]
        self._max_tokens = cfg["max_tokens"]
        self._cap = cfg["daily_call_cap"]
        self._ledger = ledger
        self._system = SYSTEM.format(names=", ".join(names))
        self._timeout = cfg["timeout_s"]
        self._client = None  # created on first use: the SDK adds ~37 MB, and most days need few calls

    def route(self, text: str) -> LlmResult:
        if self._ledger.today()["calls"] >= self._cap:
            return LlmResult(None, f"daily LLM cap reached ({self._cap} calls)")
        import anthropic

        if self._client is None:
            # No retries: a slow or failed fallback should fail fast, and retries would cost twice.
            self._client = anthropic.Anthropic(timeout=self._timeout, max_retries=0)
        t0 = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._system,
                tools=TOOLS,
                tool_choice={"type": "any", "disable_parallel_tool_use": True},
                messages=[{"role": "user", "content": text}],
            )
        except anthropic.AuthenticationError:
            return LlmResult(None, "LLM: invalid API key", (time.perf_counter() - t0) * 1000)
        except anthropic.RateLimitError:
            return LlmResult(None, "LLM: rate limited", (time.perf_counter() - t0) * 1000)
        except anthropic.APIStatusError as exc:
            return LlmResult(None, f"LLM error {exc.status_code}", (time.perf_counter() - t0) * 1000)
        except anthropic.APIConnectionError:  # includes timeouts
            return LlmResult(None, "LLM unreachable or too slow", (time.perf_counter() - t0) * 1000)

        ms = (time.perf_counter() - t0) * 1000
        usage = response.usage
        self._ledger.add(usage.input_tokens, usage.output_tokens)
        result = LlmResult(None, "", ms, usage.input_tokens, usage.output_tokens)
        if response.stop_reason == "refusal":
            result.note = "LLM declined"
            return result
        call = next((b for b in response.content if b.type == "tool_use"), None)
        if call is None:
            result.note = "LLM gave no tool call"
            return result
        result.intent = self._to_intent(call.name, call.input, text)
        result.note = f"LLM: {call.name} {json.dumps(call.input)}"
        return result

    @staticmethod
    def _to_intent(name: str, args: dict, text: str) -> Intent | None:
        try:
            return LlmRouter._map(name, args, text)
        except (KeyError, TypeError, ValueError):
            return None  # malformed arguments: treat as not understood

    @staticmethod
    def _map(name: str, args: dict, text: str) -> Intent | None:
        if name == "open_item":
            if not isinstance(args["target"], str) or not args["target"].strip():
                return None
            return Intent(I.OPEN, {"target": args["target"]}, source="llm", text=text)
        if name == "close_app":
            if not isinstance(args["target"], str) or not args["target"].strip():
                return None
            return Intent(I.CLOSE, {"target": args["target"]}, source="llm", text=text)
        if name == "media_control":
            return Intent(MEDIA[args["action"]], source="llm", text=text)
        if name == "set_volume":
            return Intent(VOLUME[args["action"]], source="llm", text=text)
        if name == "choose_option":
            n = int(args["n"])
            return Intent(I.CHOOSE, {"n": n}, source="llm", text=text) if n in (1, 2, 3) else None
        if name == "cancel":
            return Intent(I.CANCEL, source="llm", text=text)
        return None  # not_understood
