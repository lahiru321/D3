"""Short synthesized chimes (no audio files needed). Played without blocking."""

import numpy as np
import sounddevice as sd

RATE = 44_100


def _tone(freqs: list[float], note_ms: int = 70, volume: float = 0.25) -> np.ndarray:
    notes = []
    for f in freqs:
        t = np.arange(int(RATE * note_ms / 1000)) / RATE
        env = np.minimum(1.0, np.minimum(t, t[::-1]) / 0.008)  # 8 ms fade in/out, no clicks
        notes.append(np.sin(2 * np.pi * f * t) * env * volume)
    return np.concatenate(notes).astype(np.float32)


CHIMES = {
    "wake": _tone([660, 880]),       # rising: listening
    "ok": _tone([880], note_ms=60),  # single blip: done
    "error": _tone([440, 330]),      # falling: didn't work / didn't catch that
}


def play(name: str) -> None:
    try:
        sd.play(CHIMES[name], RATE)
    except sd.PortAudioError:
        pass  # feedback must never break the pipeline
