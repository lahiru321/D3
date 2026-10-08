"""Microphone -> wake word / push-to-talk -> one command utterance.

Audio arrives in 80 ms frames (1280 samples at 16 kHz, openWakeWord's frame size),
each stamped with the time it was captured. While idle, frames feed the wake-word
model. Two ways to capture a command:

- Wake word: Silero VAD finds the start of speech and the trailing silence that ends it.
- Push-to-talk: everything between key press and release (plus a short pre-roll and
  tail). Releasing ends the command at once, with no silence wait. Taps shorter than
  `ptt_min_hold_ms` are ignored, so typing the key in an editor does nothing.
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

from d3.config import resolve

SAMPLE_RATE = 16_000
FRAME = 1280                     # 80 ms
FRAME_MS = FRAME * 1000 // SAMPLE_RATE
PTT_TAIL_FRAMES = 1              # one more frame after release: people let go mid-word


@dataclass
class Utterance:
    audio: np.ndarray | None     # int16; None if nothing was said
    trigger: str                 # wake | ptt
    wake_score: float
    speech_end: float            # perf_counter() at the last speech frame / key release
    endpoint_ms: float           # trailing silence waited before cutting
    tap: bool = False            # push-to-talk key only tapped: ignore silently


class Listener:
    def __init__(self, audio_cfg: dict, wake_cfg: dict, listen_cfg: dict) -> None:
        self._device_spec = audio_cfg.get("device", "")
        self._device = self._pick_device(self._device_spec)
        model = wake_cfg["model"]  # a built-in name ("hey_jarvis") or a trained file ("models/hey_d3.onnx")
        if model.endswith(".onnx"):
            model = str(resolve(model))
        self._wake = WakeModel(wakeword_models=[model], inference_framework="onnx")
        self._wake_name = next(iter(self._wake.models))
        self._wake_threshold = wake_cfg["threshold"]
        self._vad = VAD()
        self._cfg = listen_cfg
        self._frames: queue.Queue[tuple[float, np.ndarray]] = queue.Queue(maxsize=200)
        self._recent: deque[tuple[float, np.ndarray]] = deque(maxlen=listen_cfg["preroll_ms"] // FRAME_MS + 1)
        self._ptt_down = threading.Event()
        self._ptt_up = threading.Event()
        self._press_time = 0.0
        self._release_time = 0.0
        self._stream: sd.InputStream | None = None
        self._stream_lock = threading.RLock()  # the tray (device switch) and the pipeline (recovery) both reopen
        self.paused = threading.Event()        # tray "Pause listening": ignore wake word and push-to-talk

    @staticmethod
    def _pick_device(spec) -> int | None:
        """Device index for a name from the tray/config; None = Windows default. If the named mic
        isn't connected, fall back to the default instead of failing."""
        if spec in ("", None):
            return None
        if isinstance(spec, int) or str(spec).isdigit():
            return int(spec)
        wanted = str(spec).lower()
        for idx, dev in enumerate(sd.query_devices()):  # MME devices come first and resample to 16 kHz
            name = dev["name"].lower()
            # MME truncates names to 31 characters, so accept a prefix match too.
            if dev["max_input_channels"] > 0 and (wanted in name or (len(name) >= 20 and wanted.startswith(name))):
                return idx
        return None

    @property
    def device_spec(self) -> str:
        return self._device_spec or ""

    @property
    def device_name(self) -> str:
        """Name of the mic actually in use."""
        try:
            index = self._device if self._device is not None else sd.default.device[0]
            return sd.query_devices(index)["name"]
        except (sd.PortAudioError, ValueError):
            return "no microphone"

    def set_device(self, spec: str) -> bool:
        """Switch microphone ('' = Windows default) without restarting D3."""
        with self._stream_lock:
            self._device_spec = spec
            return self.recover()

    # Called from the keyboard hook thread.
    def ptt_press(self) -> None:
        if not self._ptt_down.is_set():  # ignore key auto-repeat
            self._press_time = time.perf_counter()
            self._ptt_up.clear()
            self._ptt_down.set()

    def ptt_release(self) -> None:
        if self._ptt_down.is_set():
            self._release_time = time.perf_counter()
            self._ptt_down.clear()
            self._ptt_up.set()

    def start(self) -> None:
        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                      blocksize=FRAME, device=self._device, callback=self._on_audio)
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.close()

    def recover(self) -> bool:
        """Called when no audio has arrived for a while (mic unplugged, driver reset).
        Re-scans devices and reopens the stream; returns True once audio flows again."""
        with self._stream_lock:
            try:
                if self._stream is not None:
                    self._stream.close()
            except sd.PortAudioError:
                pass
            self._stream = None
            try:
                sd._terminate()   # PortAudio only sees new/removed devices after re-initialising
                sd._initialize()
                self._device = self._pick_device(self._device_spec)
                self.start()
                return True
            except (sd.PortAudioError, ValueError):
                return False

    def _on_audio(self, indata, frames, time_info, status) -> None:
        try:
            self._frames.put_nowait((time.perf_counter(), indata[:, 0].copy()))
        except queue.Full:
            pass  # pipeline is busy (e.g. transcribing); dropping idle audio is fine

    def _next_frame(self) -> tuple[float, np.ndarray]:
        return self._frames.get(timeout=2.0)

    def wait_for_trigger(self, on_mic_change=None) -> tuple[str, float]:
        """Block until the wake word fires or the push-to-talk key goes down.
        Reconnects the mic if audio stops; on_mic_change(lost: bool) reports it."""
        self._recent.clear()
        mic_lost = False
        while True:
            if self._ptt_down.is_set():
                if self.paused.is_set():
                    self._ptt_down.clear()
                else:
                    self._wake.reset()
                    return "ptt", 0.0
            try:
                stamped = self._next_frame()
            except queue.Empty:
                # 2 s without a frame: the mic is gone. Retry every couple of seconds.
                if not mic_lost:
                    mic_lost = True
                    if on_mic_change:
                        on_mic_change(True)
                if not self.recover():
                    time.sleep(2.0)
                continue
            if mic_lost:
                mic_lost = False
                if on_mic_change:
                    on_mic_change(False)
            if self.paused.is_set():
                continue  # keep the stream drained, but don't listen
            self._recent.append(stamped)
            score = float(self._wake.predict(stamped[1])[self._wake_name])
            if score >= self._wake_threshold:
                self._wake.reset()
                return "wake", score

    def drain(self) -> None:
        while not self._frames.empty():
            self._frames.get_nowait()

    def capture_ptt(self, on_hold) -> Utterance:
        """Collect audio while the key is held. Calls on_hold() once the press is long enough to count."""
        cfg = self._cfg
        min_hold = cfg["ptt_min_hold_ms"] / 1000
        preroll = cfg["preroll_ms"] / 1000
        max_s = cfg["max_ms"] / 1000
        press = self._press_time

        # Pre-roll from frames already seen, then anything queued since; drop audio from before the press window.
        chunks = [f for t, f in self._recent if t >= press - preroll]
        held_announced = False
        tail = None
        while True:
            try:
                t, frame = self._next_frame()
            except queue.Empty:
                break
            if t < press - preroll:
                continue  # backlog from before the press
            chunks.append(frame)
            now = time.perf_counter()
            if not held_announced and now - press >= min_hold and not self._ptt_up.is_set():
                held_announced = True
                on_hold()
            if self._ptt_up.is_set():
                if self._release_time - press < min_hold:
                    return Utterance(None, "ptt", 0.0, self._release_time, 0.0, tap=True)
                tail = PTT_TAIL_FRAMES if tail is None else tail - 1
                if tail <= 0:
                    break
            if now - press > max_s:
                break

        end = self._release_time if self._ptt_up.is_set() else time.perf_counter()
        audio = np.concatenate(chunks) if chunks else None
        return Utterance(audio, "ptt", 0.0, end, 0.0)

    def capture_command(self, trigger: str, score: float) -> Utterance:
        """Wake-word path: VAD start/end detection."""
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
                _, frame = self._next_frame()
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
