"""Media control through Windows' Global System Media Transport Controls (GSMTC).

GSMTC exposes every registered player (browsers, Spotify, VLC, Media Player...)
with its playback state, so "pause" only pauses what is playing and "continue"
only resumes what is paused. Plain media keys (a single toggle) are the fallback
when no player is registered.
"""

import asyncio
import ctypes
import threading

from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
)

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002


def press_media_key(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


class MediaController:
    """Sync facade over the async WinRT API; runs its own event loop thread."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, name="d3-media", daemon=True).start()
        self._manager = self._run(SessionManager.request_async())

    def _run(self, operation, timeout: float = 2.0):
        """Await a WinRT async operation (awaitable, but not a coroutine) on the media loop."""
        async def wait():
            return await operation
        return asyncio.run_coroutine_threadsafe(wait(), self._loop).result(timeout)

    def _sessions(self) -> list:
        return list(self._manager.get_sessions())

    @staticmethod
    def _status(session):
        return session.get_playback_info().playback_status

    @staticmethod
    def _name(session) -> str:
        return session.source_app_user_model_id.removesuffix(".exe")

    def pause(self) -> tuple[bool, str]:
        sessions = self._sessions()
        playing = [s for s in sessions if self._status(s) == Status.PLAYING]
        if not sessions:
            press_media_key(VK_MEDIA_PLAY_PAUSE)
            return True, "Sent play/pause key (no player registered)"
        if not playing:
            return True, "Nothing is playing"
        ok = all(self._run(s.try_pause_async()) for s in playing)
        return ok, "Paused " + ", ".join(self._name(s) for s in playing)

    def resume(self) -> tuple[bool, str]:
        sessions = self._sessions()
        if not sessions:
            press_media_key(VK_MEDIA_PLAY_PAUSE)
            return True, "Sent play/pause key (no player registered)"
        if any(self._status(s) == Status.PLAYING for s in sessions):
            return True, "Already playing"
        current = self._manager.get_current_session()
        paused = [s for s in sessions if self._status(s) == Status.PAUSED]
        target = current if current is not None and self._status(current) == Status.PAUSED else (paused or [None])[0]
        if target is None:
            return False, "Nothing to resume"
        ok = self._run(target.try_play_async())
        return ok, f"Resumed {self._name(target)}"

    def next(self) -> tuple[bool, str]:
        return self._skip(forward=True)

    def previous(self) -> tuple[bool, str]:
        return self._skip(forward=False)

    def _skip(self, forward: bool) -> tuple[bool, str]:
        sessions = self._sessions()
        playing = [s for s in sessions if self._status(s) == Status.PLAYING]
        target = playing[0] if playing else self._manager.get_current_session()
        word = "Next" if forward else "Previous"
        if target is not None:
            ok = self._run(target.try_skip_next_async() if forward else target.try_skip_previous_async())
            if ok:
                return True, f"{word} in {self._name(target)}"
        press_media_key(VK_MEDIA_NEXT_TRACK if forward else VK_MEDIA_PREV_TRACK)
        return True, f"{word} (media key)"
