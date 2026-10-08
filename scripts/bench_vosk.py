"""Benchmark Vosk with a closed command grammar (the planned fast path for short commands).

For each clip, Vosk either returns a whole command phrase with high confidence
(accepted: no Whisper needed) or is rejected (would fall through to Whisper).
Open-vocabulary clips like "open the StoreX proposal" should be rejected, not mis-matched.

Usage: uv run python scripts/bench_vosk.py [--dir recordings/quiet] [--min-conf 0.9]
"""

import argparse
import json
import re
import statistics
import time
import wave
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "vosk-model-small-en-us-0.15"

# Every closed-vocabulary trigger phrase from the PRD command catalogue, mapped to the
# canonical command. Grammar phrases must use words in Vosk's vocabulary: "unmute"
# isn't, so it is spelled the way Vosk hears it.
PHRASES = {
    "pause": "pause", "stop": "stop", "hold on": "hold on",
    "continue": "continue", "resume": "resume", "play": "play",
    "next": "next", "skip": "skip",
    "previous": "previous", "go back": "go back",
    "volume up": "volume up", "louder": "louder", "volume down": "volume down", "quieter": "quieter",
    "mute": "mute", "on mute": "unmute",
    "first": "first", "second": "second", "third": "third",
    "cancel": "cancel", "never mind": "never mind",
}
GRAMMAR = list(PHRASES)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def load_expected() -> dict[str, str]:
    lines = (ROOT / "bench" / "commands.txt").read_text(encoding="utf-8").splitlines()
    cmds = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
    return {slugify(c): c for c in cmds}


def recognize(model: Model, path: Path) -> tuple[str, float, float]:
    """Return (text, min word confidence, ms)."""
    with wave.open(str(path), "rb") as wf:
        data = wf.readframes(wf.getnframes())
    t = time.perf_counter()
    rec = KaldiRecognizer(model, 16_000, json.dumps(GRAMMAR + ["[unk]"]))
    rec.SetWords(True)
    rec.AcceptWaveform(data)
    result = json.loads(rec.FinalResult())
    ms = (time.perf_counter() - t) * 1000
    words = result.get("result", [])
    min_conf = min((w["conf"] for w in words), default=0.0)
    text = result.get("text", "")
    return PHRASES.get(text, text), min_conf, ms


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="recordings/quiet")
    parser.add_argument("--min-conf", type=float, default=0.9)
    args = parser.parse_args()

    SetLogLevel(-1)
    t0 = time.perf_counter()
    model = Model(str(MODEL_DIR))
    print(f"Model loaded in {time.perf_counter() - t0:.1f} s\n")

    expected = load_expected()
    in_grammar = set(PHRASES.values())
    times = []
    stats = {"closed_ok": 0, "closed_wrong": 0, "closed_rejected": 0, "open_rejected": 0, "open_false_match": 0}

    print(f"{'clip':<28}{'want':<26}{'vosk':<20}{'conf':>6}{'ms':>6}  verdict")
    for path in sorted((ROOT / args.dir).glob("*.wav")):
        slug = path.stem.split("__")[-1]
        if slug not in expected:
            continue
        want = expected[slug].lower()
        text, conf, ms = recognize(model, path)
        times.append(ms)
        accepted = text in in_grammar and conf >= args.min_conf

        if want in in_grammar:
            if accepted and text == want:
                verdict, key = "OK", "closed_ok"
            elif accepted:
                verdict, key = "WRONG (accepted a different command)", "closed_wrong"
            else:
                verdict, key = "rejected -> Whisper", "closed_rejected"
        else:
            verdict, key = ("FALSE MATCH", "open_false_match") if accepted else ("rejected -> Whisper (correct)", "open_rejected")
        stats[key] += 1
        print(f"{path.stem:<28}{want:<26}{text or '-':<20}{conf:>6.2f}{ms:>6.0f}  {verdict}")

    closed = stats["closed_ok"] + stats["closed_wrong"] + stats["closed_rejected"]
    opened = stats["open_rejected"] + stats["open_false_match"]
    print(f"\nMedian {statistics.median(times):.0f} ms, max {max(times):.0f} ms")
    print(f"Closed commands: {stats['closed_ok']}/{closed} correct, {stats['closed_wrong']} wrong, "
          f"{stats['closed_rejected']} fell back to Whisper")
    print(f"Open commands:   {stats['open_rejected']}/{opened} correctly rejected, "
          f"{stats['open_false_match']} false matches")


if __name__ == "__main__":
    main()
