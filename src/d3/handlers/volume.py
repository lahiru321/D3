"""System (default output device) volume via pycaw / Core Audio.

COM must be initialised on the calling thread: call comtypes.CoInitialize() once
in any non-main thread that uses this class.
"""

from pycaw.pycaw import AudioUtilities


class VolumeController:
    def __init__(self, step: float = 0.10) -> None:
        self.step = step
        self._ducked_from: float | None = None

    @staticmethod
    def _endpoint():
        # Looked up on every call so a changed default device (e.g. headphones) is followed.
        return AudioUtilities.GetSpeakers().EndpointVolume

    def level(self) -> float:
        return self._endpoint().GetMasterVolumeLevelScalar()

    def _set(self, value: float) -> float:
        value = min(1.0, max(0.0, value))
        self._endpoint().SetMasterVolumeLevelScalar(value, None)
        return value

    def up(self) -> tuple[bool, str]:
        ep = self._endpoint()
        ep.SetMute(0, None)
        return True, f"Volume {round(self._set(self.level() + self.step) * 100)}%"

    def down(self) -> tuple[bool, str]:
        return True, f"Volume {round(self._set(self.level() - self.step) * 100)}%"

    def mute(self) -> tuple[bool, str]:
        self._endpoint().SetMute(1, None)
        return True, "Muted"

    def unmute(self) -> tuple[bool, str]:
        self._endpoint().SetMute(0, None)
        return True, "Unmuted"

    def duck(self, factor: float) -> None:
        """Lower the volume while D3 listens, so playing video doesn't drown the command."""
        if self._ducked_from is None:
            self._ducked_from = self.level()
            self._set(self._ducked_from * factor)

    def restore(self) -> None:
        if self._ducked_from is not None:
            self._set(self._ducked_from)
            self._ducked_from = None
