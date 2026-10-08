"""The D3 pipeline: trigger -> listen -> transcribe -> route -> execute -> feedback -> log."""

import os
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
from d3.feedback.tray import Tray
from d3.handlers.media import MediaController
from d3.handlers.volume import VolumeController
from d3.log import CommandLog
from d3.resolver import Resolver
from d3.router import keyword
from d3.router.llm import LlmRouter, UsageLedger
from d3.settings import Settings
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
        keyword.add_aliases(cfg.get("commands", {}).get("aliases", {}))
        self.settings = Settings(resolve(cfg["data"]["dir"]) / "settings.json")
        self.transcriber = Transcriber(cfg["stt"], list(keyword.COMMANDS), resolve(cfg["stt"]["vosk_model"]))
        # A microphone picked in the tray wins over config.
        audio_cfg = dict(cfg["audio"], device=self.settings.get("audio_device", cfg["audio"]["device"]))
        self.listener = Listener(audio_cfg, cfg["wake"], cfg["listen"])
        self.mic_lost = False
        self.media = MediaController()
        self.volume = VolumeController(cfg["volume"]["step"])
        self.resolver = Resolver(cfg)
        if self.resolver.files is None:
            print("  ! Everything isn't running: file search disabled (folders and apps still work)")
        self.transcriber.set_names(self.resolver.hint_names())
        self.executor = Executor(self.media, self.volume, self.resolver,
                                 choice_timeout_s=cfg["dialog"]["choice_timeout_ms"] / 1000)
        self.osd = Osd()
        self.llm = self._make_llm(cfg)
        self.log = CommandLog(resolve(cfg["log"]["dir"]))
        self._debug_dir = resolve("recordings/debug")
        self._stop = threading.Event()
        self.tray: Tray | None = None
        print(f"Ready in {time.perf_counter() - t0:.1f} s")

    def _make_llm(self, cfg: dict) -> LlmRouter | None:
        llm_cfg = cfg["llm"]
        if not llm_cfg["enabled"]:
            return None
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            print("  ! No ANTHROPIC_API_KEY: LLM fallback disabled (local commands still work)")
            return None
        ledger = UsageLedger(resolve(cfg["data"]["dir"]) / "llm_usage.json")
        today = ledger.today()
        print(f"  LLM fallback: {llm_cfg['model']}, {today['calls']}/{llm_cfg['daily_call_cap']} calls used today")
        # Cap the names: every one is paid input on each call.
        return LlmRouter(llm_cfg, ledger, self.resolver.hint_names()[:150])

    def run(self) -> None:
        """Blocks until Ctrl+C or Turn off. The pipeline runs on a worker thread, the tray on its own."""
        ptt_key = self.cfg["wake"]["ptt_key"]
        register_ptt(ptt_key, self.listener.ptt_press, self.listener.ptt_release)
        self.listener.start()
        worker = threading.Thread(target=self._loop, name="d3-pipeline", daemon=True)
        worker.start()
        self.tray = Tray(self)
        self.tray.start()
        print(f"Listening on {self.listener.device_name}. Say \"hey jarvis\" then a command, "
              f"or hold {ptt_key} while you speak. Ctrl+C or the tray's Turn off quits.")
        if self.cfg["log"]["save_audio"]:
            print(f"Debug: saving every command's audio to {self._debug_dir}")
        try:
            while worker.is_alive():
                worker.join(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self._stop.set()
        self.volume.restore()
        self.listener.stop()
        keyboard.unhook_all()
        if self.tray is not None:
            self.tray.stop()

    def turn_off(self) -> None:
        """Tray 'Turn off D3': clean up, then end the process at once (threads may be blocked in audio calls)."""
        print("Turning off.")
        self.log.write(event="turn_off")
        try:
            self.shutdown()
        finally:
            os._exit(0)

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.listener.paused.set()
            self.volume.restore()
        else:
            self.listener.paused.clear()
        self.osd.show("Listening paused" if paused else "Listening again", "info")
        self.log.write(event="paused" if paused else "resumed")

    def choose_microphone(self, name: str, bluetooth: bool = False) -> bool:
        """From the tray: switch now and remember it ('' = Windows default)."""
        ok = self.listener.set_device(name)
        self.settings.set("audio_device", name)
        if ok:
            note = "\nBluetooth headset mics put the headset in call mode: sound quality drops while D3 uses it." \
                if bluetooth else ""
            self.osd.show(f"Microphone: {self.listener.device_name}{note}", "info")
        self.log.write(event="microphone", chosen=name, using=self.listener.device_name, ok=ok)
        return ok

    def _loop(self) -> None:
        comtypes.CoInitialize()  # pycaw needs COM on this thread
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

    def _on_mic_change(self, lost: bool) -> None:
        self.mic_lost = lost
        message = "Microphone disconnected: waiting for it" if lost else "Microphone reconnected"
        print(f"  ! {message}")
        self.osd.show(message, "error" if lost else "ok")
        self.log.write(event="mic_lost" if lost else "mic_back")

    def _handle_one(self) -> None:
        trigger, score = self.listener.wait_for_trigger(self._on_mic_change)
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

        self._process(utt, trigger, score)

    def _on_click_choice(self, n: int) -> None:
        """A row of the 'which one?' list was clicked (runs on the OSD thread)."""
        result = self.executor.run(I.Intent(I.CHOOSE, {"n": n}, text=f"<click {n}>"))
        sounds.play("ok" if result.ok else "error")
        self.osd.show(result.message, "ok" if result.ok else "error")
        print(f"  {'OK' if result.ok else 'X '} clicked {n}: {result.message}")
        self.log.write(event="command", trigger="click", text=f"<click {n}>", intent=I.CHOOSE, args={"n": n},
                       ok=result.ok, result=result.message)

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

        # LLM fallback: only for confident, multi-word phrases the local router couldn't match
        # (single unmatched words are almost always noise, and every call costs money).
        llm = None
        if intent is None and tr.confident and self.llm is not None and len(keyword.normalize(tr.text).split()) >= 2:
            self.volume.restore()
            llm = self.llm.route(tr.text)
            intent = llm.intent
            print(f"  llm [{llm.ms:.0f} ms, ${llm.cost_usd:.4f}]: {llm.note}")

        # Restore before acting so volume commands work from the real level, not the ducked one.
        self.volume.restore()
        t_exec = time.perf_counter()
        if intent is None:
            result = Result(False, "I didn't catch that")
        else:
            result = self.executor.run(intent)
        done = time.perf_counter()

        sounds.play("ok" if result.ok else "error")
        if result.choices:
            self.osd.show_choices("Which one?", result.choices, self._on_click_choice, self.executor.choice_timeout_s)
        else:
            self.osd.show(result.message, "ok" if result.ok else "error")
        latency_ms = (done - utt.speech_end) * 1000
        print(f"  {'OK' if result.ok else 'X '} {result.message}   ({latency_ms:.0f} ms from end of speech)")
        audio_file = self._save_debug_audio(utt.audio, tr.text) if self.cfg["log"]["save_audio"] else None
        self.log.write(
            event="command", trigger=trigger, wake_score=round(score, 3), audio_file=audio_file,
            text=tr.text, engine=tr.engine, confident=tr.confident,
            intent=intent.name if intent else None, args=intent.args if intent else None,
            source=intent.source if intent else None,
            ok=result.ok, result=result.message, asked=bool(result.choices),
            llm=None if llm is None else {"note": llm.note, "ms": round(llm.ms), "input_tokens": llm.input_tokens,
                                          "output_tokens": llm.output_tokens, "cost_usd": round(llm.cost_usd, 6)},
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
