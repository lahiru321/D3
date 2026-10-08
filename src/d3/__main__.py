"""Entry point: `uv run d3` (or `python -m d3`).

  d3                     run the voice assistant
  d3 --text "pause"      run a typed command through the router + executor (no mic); repeatable
"""

import argparse
import ctypes
import sys


def run_text(texts: list[str], cfg: dict) -> None:
    """Run typed commands in order, e.g. --text "open the report" --text "second"."""
    from d3.executor import Executor
    from d3.handlers.media import MediaController
    from d3.handlers.volume import VolumeController
    from d3.resolver import Resolver
    from d3.router import keyword

    executor = Executor(MediaController(), VolumeController(cfg["volume"]["step"]), Resolver(cfg))
    for text in texts:
        intent = keyword.route(text)
        if intent is None:
            print(f"{text!r}: I didn't catch that")
            continue
        result = executor.run(intent)
        message = "Which one?" if result.choices else result.message
        print(f"{text!r}: {intent.name} {intent.args or ''} -> {'OK' if result.ok else 'X'} {message}")
        for n, (name, folder) in enumerate(result.choices or [], start=1):
            print(f"   {n}. {name}  -  {folder}")


def main() -> None:
    from d3.config import load_config

    parser = argparse.ArgumentParser(prog="d3", description="D3 voice-controlled desktop assistant")
    parser.add_argument("--text", action="append", help="run a typed command instead of listening (repeatable)")
    parser.add_argument("--background", action="store_true",
                        help="no console: log to logs/d3.log (desktop shortcut, Start with Windows)")
    args = parser.parse_args()
    cfg = load_config()

    if args.text:
        run_text(args.text, cfg)
        return

    # No console (scripts/run_background.pyw): pythonw would drop the output, so log to a file.
    windowless = args.background or sys.stdout is None
    if windowless:
        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            ctypes.windll.user32.ShowWindow(console, SW_HIDE)
        log_to_file(cfg)
    if not single_instance():
        print("D3 is already running (see the tray icon).")
        if windowless:
            ctypes.windll.user32.MessageBoxW(None, "D3 is already running: look for its icon in the tray.",
                                             "D3", MB_ICONINFORMATION)
        return
    ensure_everything(cfg["search"]["everything_exe"])

    from d3.app import Assistant
    Assistant(cfg).run()


ERROR_ALREADY_EXISTS = 183
MB_ICONINFORMATION = 0x40
SW_HIDE = 0


def log_to_file(cfg: dict) -> None:
    """pythonw has no console and silently drops output, including the traceback if D3
    crashes. Send both to logs/d3.log so a failed start leaves a trace."""
    from datetime import datetime

    from d3.config import resolve

    log_dir = resolve(cfg["log"]["dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "d3.log"
    try:
        if path.stat().st_size > 1_000_000:  # keep one old copy, ~2 MB at most
            path.replace(log_dir / "d3.old.log")
    except OSError:  # no log yet, or a running D3 has it open
        pass
    sys.stdout = sys.stderr = open(path, "a", encoding="utf-8", buffering=1)
    print(f"\n=== D3 starting {datetime.now():%Y-%m-%d %H:%M:%S} ===")


def single_instance() -> bool:
    """False if another D3 is running: with 'Start with Windows' plus a manual start,
    two copies would both react to every command."""
    global _mutex  # keep the handle for the life of the process
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\D3VoiceAssistant")
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def ensure_everything(exe: str) -> None:
    """File search needs the Everything app running; start it in the tray if it isn't."""
    import os
    import time

    from d3.search.everything import Everything, EverythingError

    try:
        Everything().version()
        return
    except (OSError, EverythingError):
        pass
    if not os.path.exists(exe):
        return
    os.startfile(exe, arguments="-startup")
    for _ in range(20):
        time.sleep(0.25)
        try:
            Everything().version()
            return
        except EverythingError:
            continue


if __name__ == "__main__":
    main()
