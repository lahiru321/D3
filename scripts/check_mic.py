"""List input devices, record a few seconds at 16 kHz mono, and report levels.

Usage: uv run python scripts/check_mic.py [--device N] [--seconds 3] [--save out.wav]
"""

import argparse
import wave

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000


def list_inputs() -> None:
    default_in = sd.default.device[0]
    print("Input devices:")
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            hostapi = sd.query_hostapis(dev["hostapi"])["name"]
            mark = "*" if idx == default_in else " "
            print(f" {mark} [{idx:>2}] {dev['name']}  ({hostapi}, {int(dev['default_samplerate'])} Hz)")
    print(" * = default\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--save", default=None)
    args = parser.parse_args()

    list_inputs()
    sd.check_input_settings(device=args.device, samplerate=SAMPLE_RATE, channels=1, dtype="int16")

    print(f"Recording {args.seconds:.0f} s at {SAMPLE_RATE} Hz mono - say something...")
    audio = sd.rec(int(args.seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1,
                   dtype="int16", device=args.device)
    sd.wait()
    samples = audio[:, 0].astype(np.float32) / 32768.0

    rms_db = 20 * np.log10(np.sqrt(np.mean(samples**2)) + 1e-9)
    peak = float(np.max(np.abs(samples)))
    print(f"RMS {rms_db:.1f} dBFS, peak {peak:.2f}")
    if peak < 0.02:
        print("WARNING: very quiet - check the mic is unmuted and selected.")
    elif peak > 0.99:
        print("WARNING: clipping - lower the mic gain.")
    else:
        print("OK: 16 kHz mono capture works.")

    if args.save:
        with wave.open(args.save, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        print(f"Saved {args.save}")


if __name__ == "__main__":
    main()
