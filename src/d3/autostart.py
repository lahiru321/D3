"""'Start with Windows' via the per-user Run key (no admin rights needed)."""

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "D3"


def command() -> str:
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):  # packaged D3.exe
        return f'"{exe}"'
    pythonw = exe.with_name("pythonw.exe")  # no console window
    return f'"{pythonw if pythonw.exists() else exe}" -m d3'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, VALUE)
            except FileNotFoundError:
                pass
