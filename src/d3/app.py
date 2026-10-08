"""The D3 pipeline: trigger -> listen -> transcribe -> route -> execute -> feedback -> log."""

import re
import threading
import time
import wave
from datetime import datetime

import comtypes
import keyboard

from d3 import intents as I
from d3.audio.listener import Listener
from d3.config import resolve
from d3.executor import Executor
from d3.feedback import sounds
from d3.handlers.media import MediaController
from d3.handlers.volume import VolumeController
from d3.log import CommandLog
from d3.router import keyword
from d3.stt.transcriber import Transcriber

VOLUME_INTENTS = {I.VOLUME_UP, I.VOLUME_DOWN, I.MUTE, I.UNMUTE}


class Assistant:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        print("Loading models...")
        t0 = time.perf_counter()
        self.transcriber = Transcriber(cfg["stt"], list(keyword.COMMANDS), resolve(cfg["stt"]["vosk_model"]))
        self.listener = Listener(cfg["audio"], cfg["wake"], cfg["listen"])
        self.media = MediaController()
        self.volume = VolumeController(cfg["volume"]["step"])
        self.executor = Executor(self.media, self.volume)
        self.log = CommandLog(resolve(cfg["log"]["dir"]))
        self._debug_dir = resolve("recordings/debug")
        self._stop = threading.Event()
        print(f"Ready in {time.perf_counter() - t0:.1f} s")

    def run(self) -> None:
        """Blocks until Ctrl+C. The pipeline runs on a worker thread (the main thread is kept for the tray later)."""
        # On release, so holding the key (auto-repeat) can't fire it twice.
        keyboard.add_hotkey(self.cfg["wake"]["hotkey"], self.listener.trigger_hotkey, trigger_on_release=True)
        self.listener.start()
        worker = threading.Thread(target=self._loop, name="d3-pipeline", daemon=True)
        worker.start()
        print(f"Listening. Say \"hey jarvis\" or press {self.cfg['wake']['hotkey']}, then a command. Ctrl+C quits.")
        if self.cfg["log"]["save_audio"]:
            print(f"Debug: saving every command's audio to {self._debug_dir}")
        try:
            while worker.is_alive():
                worker.join(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            self.volume.restore()
            self.listener.stop()
            keyboard.unhook_all()

    def _loop(self) -> None:
        comtypes.CoInitialize()  # pycaw needs COM on this thread
        while not self._stop.is_set():
            try:
                self._handle_one()
                self.listener.clear_hotkey()
            except Exception as exc:  # keep listening whatever happens
                self.volume.restore()
                sounds.play("error")
                print(f"  ! pipeline error: {exc!r}")
                self.log.write(event="error", error=repr(exc))

    def _save_debug_audio(self, audio, text: str) -> str:
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "empty"
        path = self._debug_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{slug}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16_000)
            wf.writeframes(audio.tobytes())
        return path.name

    def _on_trigger(self) -> None:
        sounds.play("wake")
        self.volume.duck(self.cfg["listen"]["duck_to"])

    def _handle_one(self) -> None:
        trigger, score = self.listener.wait_for_trigger(self._on_trigger)
        print(f"\n> {trigger}" + (f" ({score:.2f})" if trigger == "wake" else ""))
        utt = self.listener.capture_command(trigger, score)

        if utt.audio is None:
            self.volume.restore()
            print("  (no command heard)")
            self.log.write(event="no_command", trigger=trigger, wake_score=round(score, 3))
            return

        t_stt = time.perf_counter()
        tr = self.transcriber.transcribe(utt.audio)
        t_route = time.perf_counter()
        intent = keyword.route(tr.text, fuzzy=tr.engine == "whisper") if tr.confident else None
        route_ms = (time.perf_counter() - t_route) * 1000
        print(f"  heard [{tr.engine}, {tr.ms:.0f} ms]: {tr.text!r}" + ("" if tr.confident else " (low confidence)"))

        # Restore before acting so volume commands work from the real level, not the ducked one.
        self.volume.restore()
        t_exec = time.perf_counter()
        if intent is None:
            ok, message = False, "I didn't catch that"
        else:
            ok, message = self.executor.run(intent)
        done = time.perf_counter()

        sounds.play("ok" if ok else "error")
        latency_ms = (done - utt.speech_end) * 1000
        print(f"  {'OK' if ok else 'X '} {message}   ({latency_ms:.0f} ms from end of speech)")
        audio_file = self._save_debug_audio(utt.audio, tr.text) if self.cfg["log"]["save_audio"] else None
        self.log.write(
            event="command", trigger=trigger, wake_score=round(score, 3), audio_file=audio_file,
            text=tr.text, engine=tr.engine, confident=tr.confident,
            intent=intent.name if intent else None, args=intent.args if intent else None,
            ok=ok, result=message,
            timings_ms={
                "endpoint": round(utt.endpoint_ms), "stt": round(tr.ms), "route": round(route_ms, 2),
                "exec": round((done - t_exec) * 1000), "queue": round((t_stt - utt.speech_end) * 1000 - utt.endpoint_ms),
                "total": round(latency_ms),
            },
        )
