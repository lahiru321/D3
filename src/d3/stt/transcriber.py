"""Two-tier speech-to-text.

1. Vosk with a closed grammar of command phrases (~30 ms). If the whole utterance
   is one known phrase, done.
2. Otherwise Whisper (faster-whisper, CPU int8) for open phrasing such as
   "open the StoreX proposal", with a confidence gate against hallucinations.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel
from vosk import KaldiRecognizer, Model, SetLogLevel

# Biases Whisper toward D3's vocabulary.
INITIAL_PROMPT = (
    "Voice commands: pause, continue, resume, play, stop, next, skip, previous, go back, "
    "volume up, volume down, louder, quieter, mute, unmute, open, launch, show me, "
    "first, second, third, cancel, never mind, VS Code, Downloads, StoreX."
)

# Whisper's classic outputs on silence or noise.
HALLUCINATIONS = {"", "you", "thank you", "thanks", "thank you for watching", "thanks for watching", "bye"}

NO_SPEECH_MAX = 0.6
AVG_LOGPROB_MIN = -1.0


@dataclass(frozen=True)
class Transcript:
    text: str
    engine: str        # vosk | whisper
    confident: bool
    ms: float


class Transcriber:
    def __init__(self, cfg: dict, grammar: list[str], vosk_dir: Path) -> None:
        SetLogLevel(-1)
        self._vosk = Model(str(vosk_dir))
        # Vosk silently drops grammar phrases with out-of-vocabulary words; filter them out up front.
        self._grammar = [p for p in grammar if all(self._vosk.vosk_model_find_word(w) >= 0 for w in p.split())]
        self._grammar_set = set(self._grammar)
        self._grammar_json = json.dumps(self._grammar + ["[unk]"])
        self._vosk_min_conf = cfg["vosk_min_conf"]

        self._whisper = WhisperModel(cfg["whisper_model"], device="cpu", compute_type="int8",
                                     cpu_threads=cfg["whisper_threads"])
        self._beam = cfg["whisper_beam"]
        self._prompt = INITIAL_PROMPT
        # Warm-up so the first real command isn't slow.
        list(self._whisper.transcribe(np.zeros(16_000, dtype=np.float32), language="en")[0])

    @property
    def grammar(self) -> list[str]:
        return list(self._grammar)

    def set_names(self, names: list[str]) -> None:
        """Bias Whisper toward the user's folder and app names. Without this, tiny.en heard
        'Lumora' as 'Lumura' on every test clip; with it, all clips were right. Names go
        last: faster-whisper keeps the end of an over-long prompt."""
        self._prompt = INITIAL_PROMPT + " Names: " + ", ".join(names) + "."

    def transcribe(self, audio: np.ndarray) -> Transcript:
        """audio: int16 mono at 16 kHz."""
        t0 = time.perf_counter()
        text, conf = self._vosk_pass(audio)
        if text in self._grammar_set and conf >= self._vosk_min_conf:
            return Transcript(text, "vosk", True, (time.perf_counter() - t0) * 1000)

        text, confident = self._whisper_pass(audio)
        return Transcript(text, "whisper", confident, (time.perf_counter() - t0) * 1000)

    def _vosk_pass(self, audio: np.ndarray) -> tuple[str, float]:
        rec = KaldiRecognizer(self._vosk, 16_000, self._grammar_json)
        rec.SetWords(True)
        rec.AcceptWaveform(audio.astype(np.int16).tobytes())
        result = json.loads(rec.FinalResult())
        words = result.get("result", [])
        return result.get("text", ""), min((w["conf"] for w in words), default=0.0)

    def _whisper_pass(self, audio: np.ndarray) -> tuple[str, bool]:
        segments, _ = self._whisper.transcribe(
            audio.astype(np.float32) / 32768.0, language="en", beam_size=self._beam,
            without_timestamps=True, condition_on_previous_text=False,
            initial_prompt=self._prompt, max_new_tokens=24,
        )
        segments = list(segments)
        text = " ".join(s.text for s in segments).strip()
        bare = text.lower().strip(" .,!?")
        confident = (
            bool(segments)
            and bare not in HALLUCINATIONS
            and max(s.no_speech_prob for s in segments) < NO_SPEECH_MAX
            and min(s.avg_logprob for s in segments) > AVG_LOGPROB_MIN
        )
        return text, confident
