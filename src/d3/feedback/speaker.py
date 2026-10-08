"""Spoken replies through Windows SAPI (offline). Used only when D3 asks a question.

Create and use it on one thread with COM initialised (the pipeline thread).
"""

import comtypes.client


class Speaker:
    def __init__(self, rate: int = 1) -> None:
        self._voice = comtypes.client.CreateObject("SAPI.SpVoice")
        self._voice.Rate = rate

    def say(self, text: str) -> None:
        """Blocks until finished, so D3 doesn't hear itself when it listens for the answer."""
        self._voice.Speak(text, 0)
