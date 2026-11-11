"""Never leave the PC with a silent microphone.

While Live Translator runs, Windows' default mic is "CABLE Output", which
is silent when the app isn't feeding it. If the app ends abnormally (crash,
killed, PC shut down) it can't switch the mic back itself, so:

    python -m livetranslator.guard run      start the app; when it ends for
                                            ANY reason, restore mic + volumes
    python -m livetranslator.guard restore  just restore (e.g. at Windows
                                            sign-in), unless the app is running
"""
from __future__ import annotations

import subprocess
import sys

from .config import ROOT


def app_running() -> bool:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
         "? { $_.CommandLine -like '*-m livetranslator' -or $_.CommandLine -like '*-m livetranslator *' } | "
         "Measure-Object | % Count"],
        capture_output=True, text=True, creationflags=0x08000000,
    ).stdout.strip()
    return out not in ("", "0")


def restore() -> None:
    import soundcard as sc

    from .app import _set_default_mic
    from .narrate import restore_saved_volumes

    if "cable" in sc.default_microphone().name.lower():
        real = next((m.name for m in sc.all_microphones() if "cable" not in m.name.lower()), None)
        if real:
            _set_default_mic(real.split(" (")[0])
    restore_saved_volumes()


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "restore":
        if not app_running():
            restore()
        return
    # "run": the app is a child process; whatever way it ends, clean up.
    app = subprocess.Popen([sys.executable, "-W", "ignore", "-m", "livetranslator"], cwd=ROOT)
    try:
        app.wait()
    finally:
        restore()


if __name__ == "__main__":
    main()
