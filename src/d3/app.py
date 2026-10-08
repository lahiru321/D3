"""The D3 pipeline: trigger -> listen -> transcribe -> route -> execute -> feedback -> log."""

import re
import threading
import time
import wave
from datetime import datetime

import comtypes
import keyboard

from d3 import intents as I
from d3.audio.listener import Listener, Utterance
from d3.config import resolve
from d3.executor import Executor, Result
from d3.feedback import sounds
from d3.feedback.osd import Osd
from d3.feedback.speaker import Speaker
from d3.handlers.media import MediaController
from d3.handlers.volume import VolumeController
from d3.log import CommandLog
from d3.resolver import Resolver
from d3.router import keyword
from d3.stt.transcriber import Transcriber

MODIFIERS = ("ctrl", "shift", "alt", "windows")


def register_ptt(key: str, on_press, on_release) -> None:
    """Push-to-talk on a single key. Presses with a modifier held are ignored, so
    shift+` (typing ~) is left alone. Hooks the key directly: keyboard.add_hotkey
    can't report releases of single keys."""
    if "+" in key or "," in key:
        raise ValueError(f"Push-to-talk needs a single key, not a combination: {key!r}")

    def pressed(_event) -> None:
        if not any(keyboard.is_pressed(m) for m in MODIFIERS):
            on_press()

    keyboard.on_press_key(key, pressed)
    keyboard.on_release_key(key, lambda _event: on_release())


class Assistant:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        print("Loading models...")
        t0 = time.perf_counter()
        self.transcriber = Transcriber(cfg["stt"], list(keyword.COMMANDS), resolve(cfg["stt"]["vosk_model"]))
        self.listener = Listener(cfg["audio"], cfg["wake"], cfg["listen"])
        self.media = MediaController()
        self.volume = VolumeController(cfg["volume"]["step"])
        self.resolver = Resolver(cfg)
        if self.resolver.files is None:
            print("  ! Everything isn't running: file search disabled (folders and apps still work)")
        self.executor = Executor(self.media, self.volume, self.resolver)
        self.osd = Osd()
        self.speaker: Speaker | None = None  # created on the pipeline thread (COM)
        self.log = CommandLog(resolve(cfg["log"]["dir"]))
        self._debug_dir = resolve("recordings/debug")
        self._stop = threading.Event()
        print(f"Ready in {time.perf_counter() - t0:.1f} s")

    def run(self) -> None:
        """Blocks until Ctrl+C. The pipeline runs on a worker thread (the main thread is kept for the tray later)."""
        ptt_key = self.cfg["wake"]["ptt_key"]
        register_ptt(ptt_key, self.listener.ptt_press, self.listener.ptt_release)
        self.listener.start()
        worker = threading.Thread(target=self._loop, name="d3-pipeline", daemon=True)
        worker.start()
        print(f"Listening. Say \"hey jarvis\" then a command, or hold {ptt_key} while you speak. Ctrl+C quits.")
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
        comtypes.CoInitialize()  # pycaw and SAPI need COM on this thread
        self.speaker = Speaker()
        while not self._stop.is_set():
            try:
                self._handle_one()
            except Exception as exc:  # keep listening whatever happens
                self.volume.restore()
                sounds.play("error")
                print(f"  ! pipeline error: {exc!r}")
                self.log.write(event="error", error=repr(exc))

    def _duck(self) -> None:
        self.volume.duck(self.cfg["listen"]["duck_to"])

    def _handle_one(self) -> None:
        trigger, score = self.listener.wait_for_trigger()
        if trigger == "ptt":
            # No wake chime: holding the key is the feedback, and the chime would land in the recording.
            utt = self.listener.capture_ptt(on_hold=self._duck)
            if utt.tap:
                return  # key typed, not held: ignore silently
            print("\n> push-to-talk")
        else:
            sounds.play("wake")
            self._duck()
            self.listener.drain()
            print(f"\n> wake ({score:.2f})")
            utt = self.listener.capture_command(trigger, score)

        result = self._process(utt, trigger, score)
        # "Which one?": ask, then listen for the answer without the wake word.
        while result is not None and result.question and self.executor.awaiting_choice:
            result = self._follow_up(result.question)

    def _follow_up(self, question: str) -> Result | None:
        self.volume.restore()
        print(f"  ? {question}")
        self.speaker.say(question)
        self._duck()
        self.listener.drain()  # don't transcribe D3's own voice
        cfg = dict(self.cfg["listen"], no_speech_timeout_ms=self.cfg["dialog"]["choice_timeout_ms"])
        utt = self.listener.capture_command("follow_up", 0.0, cfg=cfg)
        if utt.audio is None:
            self.volume.restore()
            self.executor.clear_pending()
            self.osd.show("No answer: cancelled", "info")
            print("  (no answer: cancelled)")
            return None
        result = self._process(utt, "follow_up", 0.0)
        if result is not None and self.executor.awaiting_choice and not result.question:
            self.executor.clear_pending()  # answered with something else: drop the old options
        return result

    def _process(self, utt: Utterance, trigger: str, score: float) -> Result | None:
        if utt.audio is None:
            self.volume.restore()
            print("  (no command heard)")
            self.log.write(event="no_command", trigger=trigger, wake_score=round(score, 3))
            return None

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
            result = Result(False, "I didn't catch that")
        else:
            result = self.executor.run(intent)
        done = time.perf_counter()

        sounds.play("ok" if result.ok else "error")
        if not result.question:
            self.osd.show(result.message, "ok" if result.ok else "error")
        latency_ms = (done - utt.speech_end) * 1000
        print(f"  {'OK' if result.ok else 'X '} {result.message}   ({latency_ms:.0f} ms from end of speech)")
        audio_file = self._save_debug_audio(utt.audio, tr.text) if self.cfg["log"]["save_audio"] else None
        self.log.write(
            event="command", trigger=trigger, wake_score=round(score, 3), audio_file=audio_file,
            text=tr.text, engine=tr.engine, confident=tr.confident,
            intent=intent.name if intent else None, args=intent.args if intent else None,
            ok=result.ok, result=result.message, asked=bool(result.question),
            timings_ms={
                "endpoint": round(utt.endpoint_ms), "stt": round(tr.ms), "route": round(route_ms, 2),
                "exec": round((done - t_exec) * 1000), "queue": round((t_stt - utt.speech_end) * 1000 - utt.endpoint_ms),
                "total": round(latency_ms),
            },
        )
        return result

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
