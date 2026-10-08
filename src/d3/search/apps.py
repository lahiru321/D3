"""Installed apps from the Start menu (PowerShell Get-StartApps), launched via shell:AppsFolder.

If the app already has a window open, D3 brings it to the front instead of starting
a second copy (e.g. a new Chrome window).
"""

import ctypes
import json
import os
import re
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

from d3.search.folders import known_folder_by_guid

CACHE_MAX_AGE_S = 24 * 3600
SKIP_NAME = re.compile(r"uninstall|reset preferences|support center|readme|help|documentation|website|release notes",
                       re.IGNORECASE)

# Spoken names that don't fuzzy-match the Start menu name.
BUILTIN_ALIASES = {
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "explorer": "File Explorer",
    "file manager": "File Explorer",
    "calculator": "Calculator",
    "terminal": "Terminal",
    "vlc": "VLC media player",
    "word": "Word",
    "excel": "Excel",
    "powerpoint": "PowerPoint",
    "settings": "Settings",
}

# Apps missing from Get-StartApps but launchable through the App Paths registry.
EXE_FALLBACKS = {"chrome": "chrome.exe", "google chrome": "chrome.exe", "edge": "msedge.exe", "firefox": "firefox.exe"}

MATCH_CUTOFF = 85


@dataclass(frozen=True)
class App:
    name: str
    app_id: str

    @property
    def exe(self) -> str | None:
        """Executable file name when the AppID is a path (used to find running windows)."""
        app_id = self.app_id
        m = re.match(r"^\{([0-9A-Fa-f-]+)\}(.*)$", app_id)
        if m:
            base = known_folder_by_guid(m.group(1))
            app_id = str(base) + m.group(2) if base else app_id
        return Path(app_id).name.lower() if app_id.lower().endswith(".exe") else None


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


class AppIndex:
    def __init__(self, cache_file: Path, aliases: dict[str, str] | None = None) -> None:
        self._cache = cache_file
        self._aliases = {**BUILTIN_ALIASES, **{k.lower(): v for k, v in (aliases or {}).items()}}
        self.apps: list[App] = self._load()

    def _load(self) -> list[App]:
        if self._cache.exists() and time.time() - self._cache.stat().st_mtime < CACHE_MAX_AGE_S:
            data = json.loads(self._cache.read_text(encoding="utf-8"))
        else:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout
            data = [{"name": a["Name"], "app_id": a["AppID"]} for a in json.loads(out or "[]")]
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            self._cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return [App(a["name"], a["app_id"]) for a in data
                if not SKIP_NAME.search(a["name"]) and not a["app_id"].startswith("http")]

    def find(self, target: str) -> App | None:
        phrase = _norm(target)
        if phrase in self._aliases:
            alias = _norm(self._aliases[phrase])
            for app in self.apps:
                if _norm(app.name) == alias:
                    return app

        best, best_score = None, 0.0
        for app in self.apps:
            name = _norm(app.name)
            score = fuzz.ratio(phrase, name)
            if name.split()[: len(phrase.split())] == phrase.split():
                score = max(score, 90)  # "vlc" -> "VLC media player"
            if app.exe and fuzz.ratio(phrase, app.exe.removesuffix(".exe")) >= 90:
                score = max(score, 90)
            if score > best_score:
                best, best_score = app, score
        return best if best_score >= MATCH_CUTOFF else None

    @staticmethod
    def fallback_exe(target: str) -> str | None:
        return EXE_FALLBACKS.get(_norm(target))


# --- launching and focusing -------------------------------------------------

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


def _window_exe(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def find_windows(exe: str | None, title_hint: str) -> list[int]:
    """Visible top-level windows owned by `exe`, or whose title ends with the app name."""
    found: list[int] = []
    hint = title_hint.lower()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and user32.GetWindow(hwnd, 4) == 0:  # visible, unowned
            title = _window_title(hwnd)
            if title and ((exe and _window_exe(hwnd) == exe) or (hint and title.lower().endswith(hint))):
                found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return found


def find_window(exe: str | None, title_hint: str) -> int | None:
    windows = find_windows(exe, title_hint)
    return windows[0] if windows else None


def focus_window(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows only lets the foreground process change focus; a synthetic Alt press lifts that lock.
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)


WM_CLOSE = 0x0010
EXPLORER_CLASS = "CabinetWClass"  # File Explorer folder windows (not the taskbar or desktop, which share explorer.exe)


def explorer_windows() -> list[tuple[int, str]]:
    """Open File Explorer windows and the folder each shows ('Documents - File Explorer' -> 'Documents')."""
    found: list[tuple[int, str]] = []
    cls = ctypes.create_unicode_buffer(64)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            user32.GetClassNameW(hwnd, cls, len(cls))
            if cls.value == EXPLORER_CLASS:
                found.append((hwnd, _window_title(hwnd).removesuffix(" - File Explorer")))
        return True

    user32.EnumWindows(callback, 0)
    return found


def close_windows(windows: list[int]) -> None:
    """Ask windows to close, exactly like clicking X: apps can still prompt to save. Never force-kills."""
    for hwnd in windows:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def foreground_window() -> tuple[int, str]:
    hwnd = user32.GetForegroundWindow()
    return hwnd, _window_title(hwnd) if hwnd else ""


def launch_or_focus(app: App) -> str:
    hwnd = find_window(app.exe, app.name)
    if hwnd:
        focus_window(hwnd)
        return f"Switched to {app.name}"
    os.startfile(f"shell:AppsFolder\\{app.app_id}")
    return f"Opening {app.name}"
