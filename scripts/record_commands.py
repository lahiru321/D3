"""Record your own voice saying each line of bench/commands.txt.

Press Enter, say the command, and recording stops after ~0.7 s of silence.
Files go to recordings/<take>/<slug>.wav (gitignored; no audio leaves the machine).

Usage: uv run python scripts/record_commands.py [--take quiet] [--device N]
"""

import argparse
import re
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000
BLOCK = 480  # 30 ms
ROOT = Path(__file__).resolve().parents[1]


def load_commands() -> list[str]:
    lines = (ROOT / "bench" / "commands.txt").read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def record_until_silence(device: int | None, silence_s: float = 0.7, max_s: float = 6.0) -> np.ndarray:
    """Energy-based endpointing: good enough for collecting test clips."""
    frames: list[np.ndarray] = []
    noise_floor = None
    speaking = False
    silent_blocks = 0
    needed_silent = int(silence_s * SAMPLE_RATE / BLOCK)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=BLOCK, device=device) as stream:
        for _ in range(int(max_s * SAMPLE_RATE / BLOCK)):
            block, _ = stream.read(BLOCK)
            frames.append(block[:, 0].copy())
            energy = float(np.sqrt(np.mean((block.astype(np.float32) / 32768.0) ** 2)))
            if noise_floor is None:
                noise_floor = energy
            threshold = max(noise_floor * 3.0, 0.01)
            if energy > threshold:
                speaking = True
                silent_blocks = 0
            elif speaking:
                silent_blocks += 1
                if silent_blocks >= needed_silent:
                    break
            else:
                noise_floor = 0.9 * noise_floor + 0.1 * energy
    return np.concatenate(frames)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--take", default="quiet", help="label, e.g. quiet / video-playing")
    parser.add_argument("--device", type=int, default=None)
    args = parser.parse_args()

    out_dir = ROOT / "recordings" / args.take
    out_dir.mkdir(parents=True, exist_ok=True)
    commands = load_commands()

    print(f"Recording {len(commands)} commands into {out_dir}")
    for n, cmd in enumerate(commands, 1):
        input(f"\n[{n}/{len(commands)}] Press Enter, then say:  \"{cmd}\" ")
        audio = record_until_silence(args.device)
        path = out_dir / f"{slugify(cmd)}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        print(f"   saved {path.name} ({len(audio) / SAMPLE_RATE:.1f} s)")


if __name__ == "__main__":
    main()
