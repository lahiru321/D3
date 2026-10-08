"""Microphone -> wake word / hotkey -> one command utterance.

Audio arrives in 80 ms frames (1280 samples at 16 kHz, openWakeWord's frame size).
While idle, frames feed the wake-word model. After a trigger, Silero VAD finds
the start of speech and the trailing silence that ends the command.
"""

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
from openwakeword.model import Model as WakeModel
from openwakeword.vad import VAD

SAMPLE_RATE = 16_000
FRAME = 1280                     # 80 ms
FRAME_MS = FRAME * 1000 // SAMPLE_RATE


@dataclass
class Utterance:
    audio: np.ndarray | None     # int16; None if nothing was said
    trigger: str                 # wake | hotkey
    wake_score: float
    speech_end: float            # perf_counter() at the last speech frame
    endpoint_ms: float           # trailing silence waited before cutting


class Listener:
    def __init__(self, audio_cfg: dict, wake_cfg: dict, listen_cfg: dict) -> None:
        self._device = self._pick_device(audio_cfg.get("device", ""))
        self._wake = WakeModel(wakeword_models=[wake_cfg["model"]], inference_framework="onnx")
        self._wake_name = next(iter(self._wake.models))
        self._wake_threshold = wake_cfg["threshold"]
        self._vad = VAD()
        self._cfg = listen_cfg
        self._frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._hotkey = threading.Event()
        self._stream: sd.InputStream | None = None

    @staticmethod
    def _pick_device(spec) -> int | None:
        if spec in ("", None):
            return None
        if isinstance(spec, int) or str(spec).isdigit():
            return int(spec)
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0 and str(spec).lower() in dev["name"].lower():
                return idx
        raise ValueError(f"No input device matching {spec!r}")

    def trigger_hotkey(self) -> None:
        self._hotkey.set()

    def clear_hotkey(self) -> None:
        """Forget presses made while a command was being handled."""
        self._hotkey.clear()

    def start(self) -> None:
        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                      blocksize=FRAME, device=self._device, callback=self._on_audio)
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.close()

    def _on_audio(self, indata, frames, time_info, status) -> None:
        try:
            self._frames.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # pipeline is busy (e.g. transcribing); dropping idle audio is fine

    def _next_frame(self) -> np.ndarray:
        return self._frames.get(timeout=2.0)

    def wait_for_trigger(self, on_trigger) -> tuple[str, float]:
        """Block until the wake word or hotkey fires. Calls on_trigger() immediately."""
        while True:
            if self._hotkey.is_set():
                self._hotkey.clear()
                trigger, score = "hotkey", 0.0
                break
            try:
                frame = self._next_frame()
            except queue.Empty:
                continue
            score = float(self._wake.predict(frame)[self._wake_name])
            if score >= self._wake_threshold:
                trigger = "wake"
                break
        on_trigger()
        self._wake.reset()
        self._drain()
        return trigger, score

    def _drain(self) -> None:
        while not self._frames.empty():
            self._frames.get_nowait()

    def capture_command(self, trigger: str, score: float) -> Utterance:
        cfg = self._cfg
        skip_frames = cfg["ignore_after_chime_ms"] // FRAME_MS
        silence_needed = cfg["silence_ms"] // FRAME_MS
        max_frames = cfg["max_ms"] // FRAME_MS
        wait_frames = cfg["no_speech_timeout_ms"] // FRAME_MS

        preroll_frames = cfg["preroll_ms"] // FRAME_MS

        self._vad.reset_states()
        chunks: list[np.ndarray] = []
        # VAD fires a little after a word starts; without this pre-roll the first
        # consonant is lost ("skip" -> nothing, "resume" -> [unk]).
        recent: deque[np.ndarray] = deque(maxlen=preroll_frames + 1)
        started = False
        silent = 0
        speech_end = time.perf_counter()
        n = 0
        while n < max_frames + wait_frames:
            try:
                frame = self._next_frame()
            except queue.Empty:
                break
            n += 1
            if n <= skip_frames:
                continue
            is_speech = self._vad.predict(frame, frame_size=640) >= cfg["vad_threshold"]
            if not started:
                recent.append(frame)
                if is_speech:
                    started = True
                    chunks.extend(recent)
                    speech_end = time.perf_counter()
                elif n >= wait_frames:
                    return Utterance(None, trigger, score, time.perf_counter(), 0.0)
                continue
            chunks.append(frame)
            if is_speech:
                silent = 0
                speech_end = time.perf_counter()
            else:
                silent += 1
                if silent >= silence_needed:
                    break
            if len(chunks) >= max_frames:
                break

        if not chunks:
            return Utterance(None, trigger, score, time.perf_counter(), 0.0)
        # Keep a little trailing context but not the full silence wait.
        audio = np.concatenate(chunks[: max(1, len(chunks) - max(0, silent - 2))])
        return Utterance(audio, trigger, score, speech_end, silent * FRAME_MS)
