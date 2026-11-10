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


def R(text: str) -> str:
    """Wrap Hebrew in RLM marks. Tk draws left-to-right; the marks keep edge
    punctuation (: ? .) and symbols (●) on the correct side of the Hebrew."""
    return f"‏{text}‏" if text else text


class SubtitleWindow:
    def __init__(
        self, settings, on_toggle: Callable[[], bool], on_quit: Callable[[], None],
        on_toggle_speak: Callable[[], bool] | None = None,
    ):
        self.s = settings
        self.on_toggle = on_toggle
        self.on_toggle_speak = on_toggle_speak
        self.on_toggle_voice = None  # set by app.py
        self.speak_enabled = bool(settings.speak_on_at_start)
        self.speak_ready = False
        self.speak_virtual = True
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
        close.bind("<Button-1>", lambda e: self.hide())  # hides; the app keeps running
        # Tk has no bidi support, so Hebrew and Latin text go in separate
        # labels laid out right-to-left instead of one mixed string.
        self.status = tk.Label(top, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.status.pack(side="right")
        self.hint = tk.Label(top, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.hint.pack(side="right", padx=(0, 12))
        self.hotkey_label = tk.Label(top, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.hotkey_label.pack(side="right", padx=(0, 4))

        # Second row: the speaking direction (your Hebrew -> English voice).
        row2 = tk.Frame(self.root, bg=BG)
        row2.pack(fill="x", padx=14, pady=(2, 0))
        self.speak_status = tk.Label(row2, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.speak_status.pack(side="right")
        self.speak_hint = tk.Label(row2, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.speak_hint.pack(side="right", padx=(0, 12))
        self.speak_hotkey_label = tk.Label(row2, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.speak_hotkey_label.pack(side="right", padx=(0, 4))
        self.speak_note = tk.Label(row2, text="", bg=BG, fg=OFF, font=("Segoe UI", 9))
        self.speak_note.pack(side="left")

        # What the other side last heard from you, in English.
        sent = tk.Frame(self.root, bg=BG)
        sent.pack(side="bottom", fill="x", padx=18, pady=(0, 10))
        self.sent_label = tk.Label(
            sent, text="", bg=BG, fg="#7FB3FF", font=("Segoe UI", 12), justify="left", anchor="w", wraplength=w - 120,
        )
        self.sent_label.pack(side="left", fill="x", expand=True)
        self.sent_title = tk.Label(sent, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.sent_title.pack(side="right")

        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=(2, 6))
        self._render()

        # The status texts are buttons: click to switch that direction on/off.
        self.status.configure(cursor="hand2")
        self.status.bind("<Button-1>", lambda e: self._toggle())
        self.speak_status.configure(cursor="hand2")
        self.speak_status.bind("<Button-1>", lambda e: self._toggle_speak())

        for widget in (self.root, self.body, top, self.hint, self.hotkey_label, row2,
                       self.speak_hint, self.speak_hotkey_label, sent, self.sent_label):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<Button-3>", self._menu)

        self.enabled = True
        self._set_status("טוען מודלים…", MUTED)
        if self.on_toggle_speak:
            self._show_speak()
        self.root.after(50, self._poll)

    # ---- public, thread-safe -------------------------------------------
    def post(self, kind: str, *args) -> None:
        self.events.put((kind, *args))

    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        self.on_quit()
        self.root.destroy()

    # ---- visibility: hidden window = subtitles paused -------------------
    visible = True
    tray = None
    _resume_on_show = False

    def state(self) -> dict:
        return {"speak": self.speak_enabled, "visible": self.visible, "fast_voice": self.s.tts_engine == "piper"}

    def hide(self) -> None:
        if not self.visible:
            return
        self.visible = False
        self._resume_on_show = self.enabled
        if self.enabled:
            self._toggle()  # stop transcribing while nobody is looking
        self.root.withdraw()
        self._refresh_tray()

    def show(self) -> None:
        if self.visible:
            return
        self.visible = True
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        if self._resume_on_show and not self.enabled:
            self._toggle()
        self._render()
        self._refresh_tray()

    def toggle_visible(self) -> None:
        self.hide() if self.visible else self.show()

    def _refresh_tray(self) -> None:
        if self.tray:
            self.tray.refresh()

    # ---- internals ------------------------------------------------------
    def _poll(self) -> None:
        try:
            while True:
                kind, *args = self.events.get_nowait()
                if kind == "line":
                    english, hebrew = args
                    self.history.append((english, hebrew))
                    self.partial = None
                    self._render()
                elif kind == "partial":  # cloud mode: text while the sentence is still going
                    if args[1]:
                        self.partial = args
                        self._render()
                elif kind == "ready":
                    self._show_enabled()
                elif kind == "status":
                    self._set_status(args[0], MUTED)
                elif kind == "error":
                    self._set_status(args[0], OFF)
                elif kind == "toggle":
                    self._toggle()
                elif kind == "speak_toggle":
                    self._toggle_speak()
                    if self.tray and not self.visible:  # no window: say it with a popup
                        self.tray.notify("תרגום הדיבור שלך לאנגלית: " + ("פועל" if self.speak_enabled else "כבוי"))
                elif kind == "voice_toggle" and self.on_toggle_voice:
                    import threading

                    def switch():  # loading a voice takes a moment: keep the UI responsive
                        new = self.on_toggle_voice()
                        if self.tray:
                            self.tray.refresh()
                            self.tray.notify("קול: " + ("מהיר" if new == "piper" else "טבעי"))

                    threading.Thread(target=switch, daemon=True).start()
                elif kind == "window_toggle":
                    self.toggle_visible()
                elif kind == "quit":
                    self.quit()
                    return
                elif kind == "speak_ready":
                    self.speak_ready = True
                    self._show_speak()
                elif kind == "speak_devices":
                    _mic, _out, virtual = args
                    self.speak_virtual = virtual
                    self._show_speak()
                elif kind == "speak_line":
                    _he, en = args
                    self.sent_title.configure(text=R("שמעו ממך:"))
                    self.sent_label.configure(text=en)
                    self._render()
                elif kind == "speak_error":
                    self.speak_status.configure(text="דיבור: שגיאה ●", fg=OFF)
                    self.speak_note.configure(text=args[0][:90])
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        partial = getattr(self, "partial", None)
        lines = list(self.history) + ([partial] if partial else [])
        if not lines:
            tk.Label(self.body, text="…", bg=BG, fg=MUTED, font=("Segoe UI", self.s.font_size)).pack(anchor="e")
        for i, (en, he) in enumerate(lines[-max(1, self.s.history_lines):]):
            last = i == len(lines[-max(1, self.s.history_lines):]) - 1
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
        where = "ענן" if self.s.engine == "azure" else "מקומי"
        if self.enabled:
            self._set_status(f"● כתוביות: מתרגם ({where})", ON, "להשהיה:")
        else:
            self._set_status(f"● כתוביות: מושהה ({where})", OFF, "להפעלה:")

    def _toggle_speak(self) -> None:
        if self.on_toggle_speak:
            self.speak_enabled = self.on_toggle_speak()
            print(f"[speak] translation {'ON' if self.speak_enabled else 'OFF'}", flush=True)
            self._show_speak()
            self._refresh_tray()

    def _show_speak(self) -> None:
        hk = self.s.speak_hotkey.replace("+", " + ").title()
        if self.speak_enabled and not self.speak_ready:
            self.speak_status.configure(text=R("● דיבור: טוען…"), fg=MUTED)
        elif self.speak_enabled:
            self.speak_status.configure(text=R("● דיבור: מתרגם לאנגלית"), fg=ON)
        else:
            self.speak_status.configure(text=R("● דיבור: כבוי (הקול שלך עובר רגיל)"), fg=MUTED)
        self.speak_hint.configure(text=R("לכיבוי: קליק כאן או" if self.speak_enabled else "להפעלה: קליק כאן או"))
        self.speak_hotkey_label.configure(text=hk)
        # Separate labels: Tk can't mix Hebrew and Latin in one string.
        self.speak_note.configure(text="" if self.speak_virtual else R("מצב בדיקה: אין כבל וירטואלי, האנגלית יוצאת לרמקולים"))

    def _set_status(self, text: str, color: str, hint: str = "") -> None:
        self.status.configure(text=R(text), fg=color)
        self.hint.configure(text=R(hint))
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
        m.add_command(label="כתוביות: השהה / המשך", command=self._toggle)
        if self.on_toggle_speak:
            m.add_command(label="דיבור לאנגלית: הפעל / כבה", command=self._toggle_speak)
        m.add_command(label="הצג / הסתר אנגלית", command=self._toggle_original)
        other = "מקומי" if self.s.engine == "azure" else "ענן (Azure)"
        m.add_command(label=f"עבור למנוע: {other} (הפעלה מחדש)", command=self._switch_engine)
        m.add_command(label="הגדל טקסט", command=lambda: self._font(+2))
        m.add_command(label="הקטן טקסט", command=lambda: self._font(-2))
        m.add_command(label="נקה", command=self._clear)
        m.add_separator()
        m.add_command(label="הסתר חלון (התוכנה ממשיכה לפעול)", command=self.hide)
        m.add_command(label="יציאה מהתוכנה", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    def _switch_engine(self) -> None:
        """Save the other engine to config.json and restart the app."""
        import os
        import sys

        from .config import save_setting

        save_setting("engine", "local" if self.s.engine == "azure" else "azure")
        self.on_quit()
        self.root.destroy()
        os.execv(sys.executable, [sys.executable, "-W", "ignore", "-m", "livetranslator"])

    def _toggle_original(self) -> None:
        self.s.show_original = not self.s.show_original
        self._render()

    def _font(self, delta: int) -> None:
        self.s.font_size = max(12, min(48, self.s.font_size + delta))
        self._render()

    def _clear(self) -> None:
        self.history.clear()
        self._render()
