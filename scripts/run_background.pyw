"""Start D3 with no window at all (desktop shortcut, Start with Windows).

Run by the base interpreter's pythonw.exe, not .venv\\Scripts\\pythonw.exe: uv's venv
launcher starts a console python.exe, and Windows Terminal then opens a window for it.
The base pythonw doesn't know about the venv, so add its packages here.
"""

import runpy
import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))  # also runs the editable-install .pth for src/
sys.argv = ["d3", "--background"]
runpy.run_module("d3", run_name="__main__")
