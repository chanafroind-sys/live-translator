"""End-to-end test of the speaking direction through the real virtual cable.

Starts the app with the microphone replaced by "what the speakers play"
(mic_device = "loopback"), turns translation on, plays a Hebrew recording,
records "CABLE Output" (exactly what Zoom would receive) and transcribes it.

    .venv\\Scripts\\python tools\\e2e_speak_test.py samples\\hebrew_test.wav
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
LISTEN = 45


def main(wav: str) -> None:
    import soundcard as sc

    from livetranslator.hotkeys import _parse  # noqa: F401  (import check)

    warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
    cfg = ROOT / "config.json"
    backup = cfg.read_text(encoding="utf-8-sig") if cfg.exists() else None
    data = json.loads(backup) if backup else {}
    data.update({"mic_device": "loopback", "speak_on_at_start": True, "start_hidden": True})
    cfg.write_text(json.dumps(data), encoding="utf-8")
    log = ROOT / "e2e.log"
    app = subprocess.Popen([sys.executable, "-W", "ignore", "-m", "livetranslator"], cwd=ROOT,
                           stdout=open(log, "w", encoding="utf-8"), stderr=subprocess.STDOUT,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        deadline = time.time() + 120
        while "he->en" not in log.read_text(encoding="utf-8", errors="ignore"):
            if time.time() > deadline:
                raise SystemExit("app did not start")
            time.sleep(1)
        time.sleep(15)  # speech models load after the translator line

        cable = next(m for m in sc.all_microphones() if "cable output" in m.name.lower())
        rec = []

        def record():
            with cable.recorder(samplerate=16000, channels=1) as r:
                for _ in range(LISTEN * 10):
                    rec.append(r.record(numframes=1600).reshape(-1))

        t = threading.Thread(target=record)
        t.start()
        time.sleep(0.5)
        start = time.time()
        w = wave.open(wav)
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        sc.default_speaker().play(a, samplerate=w.getframerate(), channels=w.getnchannels())
        print(f"Hebrew played for {time.time() - start:.1f}s")
        t.join()
    finally:
        app.terminate()
        app.wait()
        cfg.write_text(backup, encoding="utf-8") if backup is not None else cfg.unlink()

    audio = np.concatenate(rec).astype(np.float32)
    print(f"CABLE Output peak {np.abs(audio).max():.2f}")
    from faster_whisper import WhisperModel

    m = WhisperModel("base.en", device="cpu", compute_type="int8")
    segs, _ = m.transcribe(audio, language="en", condition_on_previous_text=False)
    print("What Zoom would hear (timestamps from when the Hebrew started):")
    for s in segs:
        print(f"  {s.start - 0.5:5.1f}s  {s.text.strip()}")
    print("\nApp log:")
    print("\n".join(line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("[speak")))


if __name__ == "__main__":
    main(sys.argv[1])
