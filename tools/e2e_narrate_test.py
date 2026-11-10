"""End-to-end test of Hebrew narration.

Starts the app with narration on, plays an English recording on the
speakers (as if someone spoke in Zoom), records everything the speakers play
and transcribes it in Hebrew: you should find the Hebrew narration there,
and the app log shows each phrase's timing.

    .venv\\Scripts\\python tools\\e2e_narrate_test.py samples\\english_tech_test.wav
"""
import json
import os
import subprocess
import sys
import threading
import time
import warnings
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
LISTEN = 40


def main(wav: str) -> None:
    import soundcard as sc

    warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
    cfg = ROOT / "config.json"
    backup = cfg.read_text(encoding="utf-8-sig") if cfg.exists() else None
    data = json.loads(backup) if backup else {}
    data.update({"narrate_on_at_start": True, "start_hidden": True, "speak_on_at_start": False})
    cfg.write_text(json.dumps(data), encoding="utf-8")
    log = ROOT / "e2e.log"
    app = subprocess.Popen([sys.executable, "-W", "ignore", "-m", "livetranslator"], cwd=ROOT,
                           stdout=open(log, "w", encoding="utf-8"), stderr=subprocess.STDOUT,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        deadline = time.time() + 120
        while "Hebrew voice ready" not in log.read_text(encoding="utf-8", errors="ignore"):
            if time.time() > deadline:
                raise SystemExit("app did not get ready:\n" + log.read_text(encoding="utf-8"))
            time.sleep(1)
        time.sleep(3)
        speaker = sc.default_speaker()
        loop = sc.get_microphone(str(speaker.name), include_loopback=True)
        rec = []

        def record():
            with loop.recorder(samplerate=16000, channels=1) as r:
                for _ in range(LISTEN * 10):
                    rec.append(r.record(numframes=1600).reshape(-1))

        t = threading.Thread(target=record)
        t.start()
        time.sleep(0.5)
        w = wave.open(wav)
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        # A separate process plays the "call", like Zoom would, so it gets ducked.
        player = subprocess.Popen([sys.executable, "-c",
                                   f"import soundcard as sc, numpy as np, wave; w=wave.open(r'{wav}');"
                                   "a=np.frombuffer(w.readframes(w.getnframes()),np.int16).astype(np.float32)/32768;"
                                   "sc.default_speaker().play(a, samplerate=w.getframerate(), channels=w.getnchannels())"])
        player.wait()
        t.join()
    finally:
        app.terminate()
        app.wait()
        from livetranslator.narrate import restore_saved_volumes

        restore_saved_volumes()  # terminate() skips the app's own cleanup
        cfg.write_text(backup, encoding="utf-8") if backup is not None else cfg.unlink()

    audio = np.concatenate(rec).astype(np.float32)
    from faster_whisper import WhisperModel

    m = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = m.transcribe(audio, language="he", condition_on_previous_text=False)
    print("Hebrew heard on the speakers:")
    for s in segs:
        print(f"  {s.start:5.1f}s  {s.text.strip()}")
    print("\nApp log:")
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.startswith(("[+", "[narrate]")):
            print(line)


if __name__ == "__main__":
    main(sys.argv[1])
