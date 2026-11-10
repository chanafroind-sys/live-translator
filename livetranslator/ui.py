"""Always-on-top Hebrew subtitle window."""
from __future__ import annotations

import queue
import tkinter as tk
from collections import deque
from typing import Callable

BG = "#111418"
FG = "#F5F7FA"
MUTED = "#8A94A3"
ON = "#3DDC84"
OFF = "#F25F5C"


class SubtitleWindow:
    def __init__(self, settings, on_toggle: Callable[[], bool], on_quit: Callable[[], None]):
        self.s = settings
        self.on_toggle = on_toggle
        self.on_quit = on_quit
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.history: deque[tuple[str, str]] = deque(maxlen=max(1, settings.history_lines))

        try:  # sharp text and correct positions with Windows display scaling
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title("Live Translator")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", settings.window_opacity)
        self.root.configure(bg=BG)

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = min(1100, int(sw * 0.7))
        self._x = (sw - w) // 2  # tracked ourselves: winfo_x() is 0 until mapped
        self.root.geometry(f"{w}x10+{self._x}+{int(sh * 0.74)}")
        self.wrap = w - 40

        self.bottom_margin = int(sh * 0.08)
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=14, pady=(8, 0))
        close = tk.Label(top, text="✕", bg=BG, fg=MUTED, font=("Segoe UI", 11), cursor="hand2")
        close.pack(side="left")
        close.bind("<Button-1>", lambda e: self.quit())
        # Tk has no bidi support, so Hebrew and Latin text go in separate
        # labels laid out right-to-left instead of one mixed string.
        self.status = tk.Label(top, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.status.pack(side="right")
        self.hint = tk.Label(top, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.hint.pack(side="right", padx=(0, 12))
        self.hotkey_label = tk.Label(top, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.hotkey_label.pack(side="right", padx=(0, 4))

        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=(2, 12))
        self._render()

        for widget in (self.root, self.body, top, self.status, self.hint, self.hotkey_label):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<Button-3>", self._menu)

        self.enabled = True
        self._set_status("טוען מודלים…", MUTED)
        self.root.after(50, self._poll)

    # ---- public, thread-safe -------------------------------------------
    def post(self, kind: str, *args) -> None:
        self.events.put((kind, *args))

    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        self.on_quit()
        self.root.destroy()

    # ---- internals ------------------------------------------------------
    def _poll(self) -> None:
        try:
            while True:
                kind, *args = self.events.get_nowait()
                if kind == "line":
                    english, hebrew = args
                    self.history.append((english, hebrew))
                    self._render()
                elif kind == "ready":
                    self._show_enabled()
                elif kind == "status":
                    self._set_status(args[0], MUTED)
                elif kind == "error":
                    self._set_status(args[0], OFF)
                elif kind == "toggle":
                    self._toggle()
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        if not self.history:
            tk.Label(self.body, text="…", bg=BG, fg=MUTED, font=("Segoe UI", self.s.font_size)).pack(anchor="e")
        for i, (en, he) in enumerate(self.history):
            last = i == len(self.history) - 1
            # Windows draws each label left-to-right; RLM marks make trailing
            # punctuation (? . ,) stick to the Hebrew and land on the left.
            tk.Label(
                self.body, text=f"‏{he}‏", bg=BG, fg=FG if last else MUTED,
                font=("Segoe UI", self.s.font_size if last else int(self.s.font_size * 0.75), "bold" if last else "normal"),
                justify="right", anchor="e", wraplength=self.wrap,
            ).pack(fill="x", anchor="e")
            if self.s.show_original and last:
                tk.Label(
                    self.body, text=en, bg=BG, fg=MUTED, font=("Segoe UI", max(10, int(self.s.font_size * 0.5))),
                    justify="left", anchor="w", wraplength=self.wrap,
                ).pack(fill="x", anchor="w", pady=(2, 0))
        self.root.update_idletasks()
        # Grow upward: keep the window's bottom edge where it is.
        h = self.root.winfo_reqheight()
        if not hasattr(self, "_bottom"):
            self._bottom = self.root.winfo_screenheight() - self.bottom_margin
        self.root.geometry(f"{self.wrap + 40}x{h}+{self._x}+{max(0, self._bottom - h)}")

    def _toggle(self) -> None:
        self.enabled = self.on_toggle()
        self._show_enabled()

    def _show_enabled(self) -> None:
        if self.enabled:
            self._set_status("מתרגם ●", ON, "להשהיה:   ·   קליק ימני = אפשרויות")
        else:
            self._set_status("מושהה ●", OFF, "להפעלה:")

    def _set_status(self, text: str, color: str, hint: str = "") -> None:
        self.status.configure(text=text, fg=color)
        self.hint.configure(text=hint)
        self.hotkey_label.configure(text=self.s.hotkey.replace("+", " + ").title() if hint else "")

    def _start_drag(self, e) -> None:
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag(self, e) -> None:
        y = e.y_root - self._dy
        self._x = e.x_root - self._dx
        self.root.geometry(f"+{self._x}+{y}")
        self._bottom = y + self.root.winfo_height()

    def _menu(self, e) -> None:
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="השהה / המשך", command=self._toggle)
        m.add_command(label="הצג / הסתר אנגלית", command=self._toggle_original)
        m.add_command(label="הגדל טקסט", command=lambda: self._font(+2))
        m.add_command(label="הקטן טקסט", command=lambda: self._font(-2))
        m.add_command(label="נקה", command=self._clear)
        m.add_separator()
        m.add_command(label="יציאה", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    def _toggle_original(self) -> None:
        self.s.show_original = not self.s.show_original
        self._render()

    def _font(self, delta: int) -> None:
        self.s.font_size = max(12, min(48, self.s.font_size + delta))
        self._render()

    def _clear(self) -> None:
        self.history.clear()
        self._render()
