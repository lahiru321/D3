"""Benchmark faster-whisper model sizes on short command clips (CPU).

Measures transcription latency per clip (model already loaded and warmed up,
which is how D3 runs) and accuracy against the expected text.

Usage:
  uv run python scripts/bench_stt.py                       # synthetic clips
  uv run python scripts/bench_stt.py --dir recordings/quiet
  uv run python scripts/bench_stt.py --models base.en --beam 1 5 --threads 4 8
"""

import argparse
import re
import statistics
import time
import wave
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
GATE_MS = 400  # Gate 0: a 2 s command transcribes in under 400 ms

# Biases decoding toward D3's vocabulary; mirrors what the real transcriber will pass.
INITIAL_PROMPT = (
    "Voice commands: pause, continue, resume, play, stop, next, skip, previous, go back, "
    "volume up, volume down, louder, quieter, mute, unmute, open, launch, show me, "
    "first, second, third, cancel, never mind, VS Code, Downloads, StoreX."
)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def normalize(text: str) -> str:
    text = text.lower().replace("store x", "storex").replace("vs code", "vscode").replace("v.s. code", "vscode")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text).split())


def load_expected() -> dict[str, str]:
    lines = (ROOT / "bench" / "commands.txt").read_text(encoding="utf-8").splitlines()
    cmds = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
    return {slugify(c): c for c in cmds}


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        assert wf.getframerate() == 16_000 and wf.getnchannels() == 1, f"{path} must be 16 kHz mono"
        data = wf.readframes(wf.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="bench/out/synthetic")
    parser.add_argument("--models", nargs="+", default=["tiny.en", "base.en", "small.en"])
    parser.add_argument("--beam", nargs="+", type=int, default=[1])
    parser.add_argument("--threads", nargs="+", type=int, default=[4, 8])
    args = parser.parse_args()

    expected = load_expected()
    clips = []
    for path in sorted((ROOT / args.dir).glob("*.wav")):
        slug = path.stem.split("__")[-1]
        if slug in expected:
            clips.append((path, expected[slug], read_wav(path)))
    if not clips:
        raise SystemExit(f"No matching clips in {args.dir}")
    print(f"{len(clips)} clips from {args.dir}\n")

    header = f"{'model':<10}{'thr':>4}{'beam':>5}{'load s':>8}{'med ms':>8}{'p90 ms':>8}{'max ms':>8}{'exact':>8}{'fuzzy85':>9}"
    print(header)
    print("-" * len(header))
    misses: dict[str, list[str]] = {}

    for model_name in args.models:
        for threads in args.threads:
            t0 = time.perf_counter()
            model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads)
            load_s = time.perf_counter() - t0
            # Warm-up so the first timed clip isn't paying one-off costs.
            list(model.transcribe(np.zeros(16_000, dtype=np.float32), language="en")[0])

            for beam in args.beam:
                times, exact, fuzzy = [], 0, 0
                key = f"{model_name} thr={threads} beam={beam}"
                for path, want, audio in clips:
                    t = time.perf_counter()
                    segments, _ = model.transcribe(
                        audio, language="en", beam_size=beam, without_timestamps=True,
                        condition_on_previous_text=False, initial_prompt=INITIAL_PROMPT,
                    )
                    got = " ".join(s.text for s in segments).strip()
                    times.append((time.perf_counter() - t) * 1000)
                    if normalize(got) == normalize(want):
                        exact += 1
                    if fuzz.ratio(normalize(got), normalize(want)) >= 85:
                        fuzzy += 1
                    else:
                        misses.setdefault(key, []).append(f"{path.name}: want {want!r}, got {got!r}")

                times.sort()
                p90 = times[min(len(times) - 1, int(len(times) * 0.9))]
                n = len(clips)
                print(f"{model_name:<10}{threads:>4}{beam:>5}{load_s:>8.1f}{statistics.median(times):>8.0f}"
                      f"{p90:>8.0f}{times[-1]:>8.0f}{exact / n:>8.0%}{fuzzy / n:>9.0%}")
            del model

    print(f"\nGate 0: median under {GATE_MS} ms on the chosen model.")
    for key, items in misses.items():
        print(f"\nMisses for {key}:")
        for item in items:
            print("  " + item)


if __name__ == "__main__":
    main()
