"""On-screen feedback in the bottom-right corner, on its own Tk thread.

- show(): a short confirmation that fades out.
- show_choices(): a clickable "Which one?" list (FR-8). Click a row, or say
  "first / second / third" as a normal command.

The window never takes focus, so it can't steal focus from what D3 just opened;
clicks still reach it.
"""

import ctypes
import queue
import threading
import tkinter as tk
from typing import Callable

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080      # no taskbar button
WS_EX_NOACTIVATE = 0x08000000      # never becomes the foreground window

BG = {"ok": "#1f6f43", "error": "#8a2b2b", "info": "#2b2f36"}
ROW_BG, ROW_HOVER, MUTED = "#2b2f36", "#3a404a", "#aab1bd"


class Osd:
    def __init__(self, seconds: float = 2.5) -> None:
        self._seconds = seconds
        self._queue: queue.Queue[tuple] = queue.Queue()
        threading.Thread(target=self._run, name="d3-osd", daemon=True).start()

    def show(self, text: str, kind: str = "ok") -> None:
        self._queue.put(("message", text, kind))

    def show_choices(self, title: str, items: list[tuple[str, str]], on_pick: Callable[[int], None],
                     seconds: float) -> None:
        """items: (main line, detail line). on_pick(n) is called with 1-based n on the OSD thread."""
        self._queue.put(("choices", title, items, on_pick, seconds))

    def hide_choices(self) -> None:
        self._queue.put(("hide_choices",))

    def _run(self) -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays
        except OSError:
            pass
        self._root = tk.Tk()
        self._root.withdraw()
        self._win = tk.Toplevel(self._root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True, "-alpha", 0.95)
        self._frame = tk.Frame(self._win)
        self._frame.pack(fill="both", expand=True)
        self._win.withdraw()
        self._win.update_idletasks()
        self._hwnd = ctypes.windll.user32.GetParent(self._win.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE,
                                            style | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        self._hide_job = None
        self._showing_choices = False
        self._poll()
        self._root.mainloop()

    def _poll(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                if msg[0] == "message":
                    self._render_message(*msg[1:])
                elif msg[0] == "choices":
                    self._render_choices(*msg[1:])
                elif msg[0] == "hide_choices" and self._showing_choices:
                    self._hide()
        except queue.Empty:
            pass
        self._root.after(50, self._poll)

    def _clear(self, bg: str) -> None:
        for child in self._frame.winfo_children():
            child.destroy()
        self._frame.config(bg=bg)
        self._win.config(bg=bg)

    def _place_and_show(self, seconds: float) -> None:
        self._win.update_idletasks()
        w, h = self._win.winfo_reqwidth(), self._win.winfo_reqheight()
        x = self._win.winfo_screenwidth() - w - 24
        y = self._win.winfo_screenheight() - h - 72
        self._win.geometry(f"+{x}+{y}")
        # WS_EX_NOACTIVATE keeps deiconify from taking focus (verified: foreground window unchanged).
        self._win.deiconify()
        self._win.lift()
        if self._hide_job is not None:
            self._root.after_cancel(self._hide_job)
        self._hide_job = self._root.after(int(seconds * 1000), self._hide)

    def _hide(self) -> None:
        self._win.withdraw()
        self._showing_choices = False
        self._hide_job = None

    def _render_message(self, text: str, kind: str) -> None:
        bg = BG.get(kind, BG["info"])
        self._clear(bg)
        self._showing_choices = False
        tk.Label(self._frame, text=text, font=("Segoe UI", 12), fg="white", bg=bg,
                 padx=18, pady=10, wraplength=560, justify="left").pack()
        self._place_and_show(self._seconds)

    def _render_choices(self, title: str, items: list[tuple[str, str]], on_pick, seconds: float) -> None:
        self._clear(BG["info"])
        self._showing_choices = True
        tk.Label(self._frame, text=title, font=("Segoe UI Semibold", 12), fg="white", bg=BG["info"],
                 padx=16, pady=8, anchor="w").pack(fill="x")
        for n, (main, detail) in enumerate(items, start=1):
            row = tk.Frame(self._frame, bg=ROW_BG, cursor="hand2")
            row.pack(fill="x", padx=8, pady=(0, 6))
            num = tk.Label(row, text=str(n), font=("Segoe UI Semibold", 14), fg="white", bg=ROW_BG, padx=10)
            num.pack(side="left", fill="y")
            text = tk.Frame(row, bg=ROW_BG)
            text.pack(side="left", fill="x", expand=True, pady=4)
            top = tk.Label(text, text=main, font=("Segoe UI", 11), fg="white", bg=ROW_BG, anchor="w")
            top.pack(fill="x")
            sub = tk.Label(text, text=detail, font=("Segoe UI", 9), fg=MUTED, bg=ROW_BG, anchor="w",
                           wraplength=520, justify="left")
            sub.pack(fill="x", padx=(0, 12))
            widgets = (row, num, text, top, sub)

            def pick(_event, n=n):
                self._hide()
                on_pick(n)

            def hover(_event, ws=widgets, on=True):
                for w in ws:
                    w.config(bg=ROW_HOVER if on else ROW_BG)

            for w in widgets:
                w.bind("<Button-1>", pick)
                w.bind("<Enter>", hover)
                w.bind("<Leave>", lambda e, ws=widgets: hover(e, ws, on=False))
        tk.Label(self._frame, text='Click one, or say "first", "second" or "third"', font=("Segoe UI", 9),
                 fg=MUTED, bg=BG["info"], padx=16, pady=6, anchor="w").pack(fill="x")
        self._place_and_show(seconds)
