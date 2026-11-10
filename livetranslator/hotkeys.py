"""Global hotkeys through the Win32 RegisterHotKey API.

The `keyboard` package uses a low-level keyboard hook, and Windows silently
removes such hooks when the process is slow to answer (e.g. while Whisper
keeps the CPU busy). RegisterHotKey is handled by Windows itself, so the
shortcut keeps working no matter how loaded the app is.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import threading

_MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312


def _parse(combo: str) -> tuple[int, int]:
    mods, vk = _NOREPEAT, 0
    for part in combo.lower().replace(" ", "").split("+"):
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())  # VK codes for A-Z / 0-9 equal their ASCII
        elif part.startswith("f") and part[1:].isdigit():
            vk = 0x6F + int(part[1:])  # F1 = 0x70
        else:
            raise ValueError(f"unsupported key '{part}' in hotkey '{combo}'")
    if not vk:
        raise ValueError(f"hotkey '{combo}' has no main key")
    return mods, vk


def register(bindings: dict[str, callable]) -> None:
    """Register {"ctrl+alt+y": callback, ...} on a background message loop."""

    def loop():
        user32 = ctypes.windll.user32
        ids = {}
        for i, (combo, cb) in enumerate(bindings.items(), start=1):
            mods, vk = _parse(combo)
            if user32.RegisterHotKey(None, i, mods, vk):
                ids[i] = cb
            else:
                print(f"[hotkey] {combo} is taken by another program", flush=True)
        print(f"[hotkey] registered: {', '.join(bindings)}", flush=True)
        msg = w.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == _WM_HOTKEY and msg.wParam in ids:
                try:
                    ids[msg.wParam]()
                except Exception as e:
                    print(f"[hotkey] {e}", flush=True)

    threading.Thread(target=loop, daemon=True, name="hotkeys").start()
