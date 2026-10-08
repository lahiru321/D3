"""System tray icon (FR-15, privacy indicator): listening state, pause, microphone choice,
start with Windows, and Turn off.

The microphone list shows the devices connected right now and refreshes every few
seconds, so plugging in a headset makes it appear without restarting D3.
"""

import os
import threading
from functools import lru_cache
from typing import TYPE_CHECKING

import comtypes
import pystray
from PIL import Image, ImageDraw

from d3 import autostart
from d3.audio.devices import InputDevice, active_inputs
from d3.config import ROOT, resolve

if TYPE_CHECKING:
    from d3.app import Assistant

COLORS = {"listening": "#2e9e5b", "paused": "#7a7f87", "no mic": "#c0392b"}
REFRESH_S = 3.0


LOGO = ROOT / "assets" / "logo.png"
LOGO_BOX = (176, 139, 1076, 1039)  # the robot, without most of the empty margin around it


@lru_cache
def logo_image(size: int) -> Image.Image:
    """The D3 logo as a rounded-square app icon (desktop shortcut, tray)."""
    img = Image.open(LOGO).convert("RGBA").crop(LOGO_BOX).resize((size, size), Image.LANCZOS)
    big = size * 4  # draw the corners large and shrink, for smooth edges
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 5, fill=255)
    img.putalpha(mask.resize((size, size), Image.LANCZOS))
    return img


def _icon_image(state: str, size: int = 64) -> Image.Image:
    """The logo with a status dot: green listening, grey paused, red no mic."""
    img = logo_image(size).copy()
    d = round(size * 0.38)
    box = (size - d, size - d, size - 1, size - 1)
    ImageDraw.Draw(img).ellipse(box, fill=COLORS[state], outline="#060e29", width=max(1, size // 20))
    return img


class Tray:
    def __init__(self, assistant: "Assistant") -> None:
        self._a = assistant
        self._devices: list[InputDevice] = []
        self._state = ""
        self._stop = threading.Event()
        self._icon = pystray.Icon("D3", _icon_image("listening"), "D3", menu=pystray.Menu(self._menu))

    def start(self) -> None:
        self._icon.run_detached()
        threading.Thread(target=self._refresh_loop, name="d3-tray-refresh", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._icon.visible = False
            self._icon.stop()
        except Exception:  # tray may not have started
            pass

    # --- state -------------------------------------------------------------

    def _current_state(self) -> str:
        if self._a.listener.paused.is_set():
            return "paused"
        return "no mic" if self._a.mic_lost else "listening"

    def _refresh_loop(self) -> None:
        comtypes.CoInitialize()  # device enumeration uses COM on this thread
        while not self._stop.is_set():
            try:
                devices = active_inputs()
            except OSError:
                devices = self._devices
            listener = self._a.listener
            if (devices != self._devices and listener.on_fallback
                    and any(listener.device_spec.lower() in d.name.lower() for d in devices)):
                listener.set_device(listener.device_spec)  # the chosen mic just appeared: switch back to it
                self._state = ""  # refresh the title with the new mic
            state = self._current_state()
            if devices != self._devices or state != self._state:
                self._devices, self._state = devices, state
                self._icon.icon = _icon_image(state)
                self._icon.title = f"D3: {state} ({self._a.listener.device_name})"
                self._icon.update_menu()
            self._stop.wait(REFRESH_S)

    # --- menu --------------------------------------------------------------

    def _menu(self):
        a = self._a
        yield pystray.MenuItem(f"D3: {self._current_state()}", None, enabled=False)
        yield pystray.MenuItem("Pause listening", self._toggle_pause,
                               checked=lambda _: a.listener.paused.is_set())
        yield pystray.MenuItem("Microphone", pystray.Menu(self._mic_items))
        yield pystray.MenuItem("Start with Windows", self._toggle_autostart,
                               checked=lambda _: autostart.is_enabled())
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Edit settings", self._open_settings)
        yield pystray.MenuItem("Open logs folder", lambda: os.startfile(resolve(a.cfg["log"]["dir"])))
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Turn off D3", self._turn_off)

    def _mic_items(self):
        # pystray rejects callbacks with more than two parameters (defaults count), hence the factories.
        def choose(name: str, bluetooth: bool = False):
            return lambda: self._choose_mic(name, bluetooth)

        def is_current(name: str):
            return lambda _item: self._a.listener.device_spec == name

        default = next((d.name for d in self._devices if d.is_default), "")
        yield pystray.MenuItem(f"Windows default ({default})" if default else "Windows default",
                               choose(""), checked=is_current(""), radio=True)
        for device in self._devices:
            label = f"{device.name}   (Bluetooth: lowers headset sound quality)" if device.bluetooth else device.name
            yield pystray.MenuItem(label, choose(device.name, device.bluetooth), checked=is_current(device.name),
                                   radio=True)
        if not self._devices:
            yield pystray.MenuItem("No microphone connected", None, enabled=False)

    # --- actions (run on the tray thread; slow work goes to a thread) -------

    def _choose_mic(self, name: str, bluetooth: bool = False) -> None:
        def switch():
            ok = self._a.choose_microphone(name, bluetooth)
            self._state = ""  # force a refresh of title/menu
            self._icon.update_menu()
            if not ok:
                self._a.osd.show(f"Couldn't open {name or 'the default microphone'}", "error")
        threading.Thread(target=switch, daemon=True).start()

    def _toggle_pause(self) -> None:
        self._a.set_paused(not self._a.listener.paused.is_set())
        self._state = ""
        self._icon.update_menu()

    def _toggle_autostart(self) -> None:
        autostart.set_enabled(not autostart.is_enabled())
        self._icon.update_menu()

    @staticmethod
    def _open_settings() -> None:
        user = ROOT / "config.toml"
        if not user.exists():
            user.write_text("# Your D3 settings. Copy any section/key from config.default.toml here to override it.\n"
                            "# Restart D3 after editing.\n", encoding="utf-8")
        os.startfile(user)

    def _turn_off(self) -> None:
        self._a.turn_off()
