"""LLM router logic with a fake client: no network, no cost."""

from types import SimpleNamespace

import pytest

from d3 import intents as I
from d3.router import keyword
from d3.router.llm import LlmRouter, UsageLedger

CFG = {"model": "claude-haiku-4-5", "max_tokens": 200, "daily_call_cap": 2, "timeout_s": 4.0}


def response(name: str, args: dict, stop_reason: str = "tool_use"):
    block = SimpleNamespace(type="tool_use", name=name, input=args)
    return SimpleNamespace(content=[block], stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=1600, output_tokens=30))


def router(tmp_path, monkeypatch, reply):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r = LlmRouter(CFG, UsageLedger(tmp_path / "usage.json"), ["Lumora", "PROJECTS"])
    r._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: reply))
    return r


@pytest.mark.parametrize("name, args, expected", [
    ("open_item", {"target": "Lumora folder"}, (I.OPEN, {"target": "Lumora folder"})),
    ("media_control", {"action": "pause"}, (I.PAUSE, {})),
    ("set_volume", {"action": "up"}, (I.VOLUME_UP, {})),
    ("choose_option", {"n": 2}, (I.CHOOSE, {"n": 2})),
    ("cancel", {}, (I.CANCEL, {})),
])
def test_maps_tool_calls_to_intents(tmp_path, monkeypatch, name, args, expected):
    result = router(tmp_path, monkeypatch, response(name, args)).route("whatever was said")
    assert (result.intent.name, result.intent.args) == expected
    assert result.intent.source == "llm"


@pytest.mark.parametrize("name, args", [
    ("not_understood", {}),
    ("open_item", {"target": "  "}),
    ("media_control", {"action": "explode"}),
    ("choose_option", {"n": 7}),
    ("choose_option", {"n": "two"}),
])
def test_unclear_or_malformed_replies_do_nothing(tmp_path, monkeypatch, name, args):
    assert router(tmp_path, monkeypatch, response(name, args)).route("hmm").intent is None


def test_refusal_does_nothing(tmp_path, monkeypatch):
    reply = response("open_item", {"target": "x"}, stop_reason="refusal")
    assert router(tmp_path, monkeypatch, reply).route("x").intent is None


def test_daily_cap_and_usage_ledger(tmp_path, monkeypatch):
    r = router(tmp_path, monkeypatch, response("media_control", {"action": "next"}))
    first, second, third = r.route("a b"), r.route("a b"), r.route("a b")
    assert first.intent and second.intent and third.intent is None
    assert "cap" in third.note
    assert first.cost_usd == pytest.approx((1600 * 1 + 30 * 5) / 1e6)
    assert UsageLedger(tmp_path / "usage.json").today() == {"calls": 2, "input_tokens": 3200, "output_tokens": 60}


def test_command_aliases(monkeypatch):
    monkeypatch.setattr(keyword, "COMMANDS", dict(keyword.COMMANDS))
    keyword.add_aliases({"shut up": "mute", "Keep going": "continue"})
    assert keyword.route("shut up").name == I.MUTE
    assert keyword.route("keep going").name == I.RESUME
    with pytest.raises(ValueError):
        keyword.add_aliases({"blast it": "explode"})
