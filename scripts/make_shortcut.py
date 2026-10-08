"""Put a D3 shortcut on the desktop, so D3 starts with a double-click (no VS Code or terminal).

It runs the same command as 'Start with Windows' (pythonw, no console window). Run it again
if the project folder or .venv moves.

Usage: uv run python scripts/make_shortcut.py
"""

import shlex

import comtypes.client

from d3 import autostart
from d3.config import ROOT, resolve
from d3.feedback.tray import logo_image


def main() -> None:
    icon = resolve("data/d3.ico")
    icon.parent.mkdir(parents=True, exist_ok=True)
    logo_image(256).save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])

    target, *args = shlex.split(autostart.command(), posix=False)
    shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    path = rf"{shell.SpecialFolders('Desktop')}\D3.lnk"  # follows a OneDrive-redirected desktop
    link = shell.CreateShortcut(path)
    link.TargetPath = target.strip('"')
    link.Arguments = " ".join(args)
    link.WorkingDirectory = str(ROOT)
    link.IconLocation = f"{icon},0"
    link.Description = "D3 voice assistant"
    link.Save()
    print(f"Created {path}")


if __name__ == "__main__":
    main()
