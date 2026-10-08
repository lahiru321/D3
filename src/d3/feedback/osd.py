"""On-screen confirmation: a small dark box in the bottom-right corner that fades out.

Runs its own Tk thread. The window never takes focus, so it can't steal focus from
the app or file D3 just opened.
"""

import ctypes
import queue
import threading
import tkinter as tk

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080      # no taskbar button
WS_EX_NOACTIVATE = 0x08000000      # never becomes the foreground window
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE | NOMOVE | NOACTIVATE | SHOWWINDOW
HWND_TOPMOST = -1

COLORS = {"ok": "#1f6f43", "error": "#8a2b2b", "info": "#2b2f36"}


class Osd:
    def __init__(self, seconds: float = 2.5) -> None:
        self._seconds = seconds
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        threading.Thread(target=self._run, name="d3-osd", daemon=True).start()

    def show(self, text: str, kind: str = "ok") -> None:
        self._queue.put((text, kind))

    def _run(self) -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays
        except OSError:
            pass
        root = tk.Tk()
        root.withdraw()
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True, "-alpha", 0.94)
        label = tk.Label(win, font=("Segoe UI", 12), fg="white", padx=18, pady=10, wraplength=520, justify="left")
        label.pack()
        win.withdraw()
        win.update_idletasks()

        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)

        hide_job = None

        def poll() -> None:
            nonlocal hide_job
            try:
                while True:
                    text, kind = self._queue.get_nowait()
                    label.config(text=text, bg=COLORS.get(kind, COLORS["info"]))
                    win.config(bg=label["bg"])
                    win.update_idletasks()
                    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
                    x = win.winfo_screenwidth() - w - 24
                    y = win.winfo_screenheight() - h - 72
                    win.geometry(f"+{x}+{y}")
                    # Show without activating (deiconify would take focus).
                    ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
                    if hide_job is not None:
                        root.after_cancel(hide_job)
                    hide_job = root.after(int(self._seconds * 1000), win.withdraw)
            except queue.Empty:
                pass
            root.after(50, poll)

        poll()
        root.mainloop()
