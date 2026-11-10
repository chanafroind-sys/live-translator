"""Icon next to the Windows clock, so the app can run with no window open.

Colours:  green = your speech is being translated to English
          blue  = subtitles window is open
          grey  = idle (your mic passes through unchanged)
"""
from __future__ import annotations

import threading

GREEN, BLUE, GREY = "#3DDC84", "#4A90E2", "#8A94A3"


def _image(color: str):
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((4, 4, 60, 60), fill=color)
    d.rectangle((18, 22, 46, 28), fill="white")  # simple "speech lines" mark
    d.rectangle((18, 36, 38, 42), fill="white")
    return im


class Tray:
    def __init__(self, post, get_state):
        """post(kind) sends an event to the UI thread; get_state() -> dict."""
        import pystray
        from pystray import Menu, MenuItem as Item

        self.post, self.get_state = post, get_state
        st = lambda k: (lambda item: bool(self.get_state()[k]))  # noqa: E731
        menu = Menu(
            Item("הצג / הסתר כתוביות", lambda: post("window_toggle"), default=True),
            Item("תרגום הדיבור שלי לאנגלית", lambda: post("speak_toggle"), checked=st("speak")),
            Item("ניסוח משופר של מה שאני אומרת", lambda: post("polish_toggle"), checked=st("polish")),
            Item("הקראה בעברית של מה שהם אומרים", lambda: post("narrate_toggle"), checked=st("narrate")),
            Item("הסברים למונחים קשים", lambda: post("explain_toggle"), checked=st("explain")),
            Item("מה אני יודעת (בשביל ההסברים)…", self._open_profile),
            Item("קול מהיר (פחות טבעי)", lambda: post("voice_toggle"), checked=st("fast_voice")),
            Item("מילון מונחים ושמות…", self._open_vocabulary),
            Menu.SEPARATOR,
            Item("יציאה", lambda: post("quit")),
        )
        self.icon = pystray.Icon("live-translator", _image(GREY), "Live Translator", menu)
        threading.Thread(target=self.icon.run, daemon=True, name="tray").start()

    @staticmethod
    def _open_vocabulary() -> None:
        import os

        from .vocabulary import FILE, _load

        _load()  # creates vocabulary.txt from the example on first use
        os.startfile(str(FILE))  # opens in Notepad; saved changes apply at once

    @staticmethod
    def _open_profile() -> None:
        import os

        from .explain import PROFILE, _profile

        _profile()  # creates about_me.txt from the example on first use
        os.startfile(str(PROFILE))

    def refresh(self) -> None:
        s = self.get_state()
        color = GREEN if s["speak"] else BLUE if (s["visible"] or s["narrate"]) else GREY
        parts = ["Live Translator", "דיבור לאנגלית: " + ("פועל" if s["speak"] else "כבוי")]
        try:
            self.icon.icon = _image(color)
            self.icon.title = " | ".join(parts)
            self.icon.update_menu()
        except Exception:
            pass

    def notify(self, text: str) -> None:
        try:
            self.icon.notify(text, "Live Translator")
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
