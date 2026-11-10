"""Entry point.

    python -m livetranslator            # live: subtitles for whatever the PC plays
    python -m livetranslator --file x.wav   # test on a recording, prints timings
"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import time

import numpy as np

from .audio import FRAME, SAMPLE_RATE, LoopbackCapture, PhraseSegmenter
from .config import load_settings


def merge_backlog(q, audio, ended_at, max_sec: float = 20.0):
    """If phrases piled up while we were busy, recognise them in one pass.

    One Whisper call on 12 s of audio is much faster than four calls on 3 s,
    so a slow moment no longer snowballs into a growing delay.
    """
    parts = [audio]
    while sum(len(p) for p in parts) < max_sec * SAMPLE_RATE:
        try:
            nxt, ended_at = q.get_nowait()
        except queue.Empty:
            break
        parts.append(nxt)
    if len(parts) > 1:
        print(f"[backlog] merged {len(parts)} phrases", flush=True)
    return np.concatenate(parts), ended_at


def cloud_credentials(settings, on_error=None):
    """(key, region) when cloud mode is selected and configured, else None."""
    if settings.engine != "azure":
        return None
    from .cloud import azure_credentials

    creds = azure_credentials(settings)
    if not creds and on_error:
        on_error("מצב ענן נבחר אבל חסר מפתח Azure: עובד מקומית")
    return creds


class Pipeline:
    """capture -> segment -> recognize -> translate -> callback."""

    def __init__(self, settings, on_line, on_status, on_ready, on_error, on_partial=None):
        self.s = settings
        self.on_line, self.on_status, self.on_ready, self.on_error = on_line, on_status, on_ready, on_error
        self.on_partial = on_partial or (lambda en, he: None)
        self.enabled = threading.Event()
        self.enabled.set()
        self.stopping = threading.Event()
        self.frames: "queue.Queue[np.ndarray]" = queue.Queue()
        self.phrases: "queue.Queue[tuple[np.ndarray, float]]" = queue.Queue(maxsize=8)
        self.capture: LoopbackCapture | None = None

    def toggle(self) -> bool:
        if self.enabled.is_set():
            self.enabled.clear()
        else:
            self.enabled.set()
        return self.enabled.is_set()

    def start(self) -> None:
        threading.Thread(target=self._boot, daemon=True, name="boot").start()

    def stop(self) -> None:
        self.stopping.set()
        if self.capture:
            self.capture.stop()

    def _boot(self) -> None:
        creds = cloud_credentials(self.s, self.on_error)
        if creds:
            self._boot_cloud(*creds)
            return
        try:
            from .engines import Recognizer, make_translator

            self.recognizer = Recognizer(self.s.whisper_model, self.s.cpu_threads)
            self.translator = make_translator(self.s, "en", "he", self.s.translation_model_dir)
        except Exception as e:
            self.on_error(f"שגיאה בטעינת מודלים: {e}")
            return
        self.capture = LoopbackCapture(self.frames)
        self.capture.start()
        threading.Thread(target=self._segment_loop, daemon=True, name="segment").start()
        threading.Thread(target=self._work_loop, daemon=True, name="work").start()
        self.on_ready()

    def _boot_cloud(self, key: str, region: str) -> None:
        from .cloud import AzureStreamTranslator

        try:
            self.cloud = AzureStreamTranslator(
                key, region, "en", "he", self.s.speech_threshold, int(self.s.silence_end_sec * 1000),
                on_partial=self.on_partial,
                on_final=lambda en, he, lag: self.on_line(en, he, lag),
                on_error=self.on_error,
            )
        except Exception as e:
            self.on_error(f"שגיאת ענן: {e}")
            return
        self.capture = LoopbackCapture(self.frames)
        self.capture.start()

        def feed():
            while not self.stopping.is_set():
                try:
                    frame = self.frames.get(timeout=0.2)
                except queue.Empty:
                    continue
                if self.enabled.is_set():  # nothing is sent while paused
                    self.cloud.push(frame)
            self.cloud.close()

        threading.Thread(target=feed, daemon=True, name="cloud-feed").start()
        self.on_ready()

    def _segment_loop(self) -> None:
        seg = PhraseSegmenter(self.s.speech_threshold, self.s.silence_end_sec, self.s.max_phrase_sec)
        while not self.stopping.is_set():
            try:
                frame = self.frames.get(timeout=0.1)
            except queue.Empty:
                phrase = seg.flush_if_stalled()
                if self.capture and self.capture.error:
                    self.on_error(f"שגיאת שמע: {self.capture.error}")
                    return
            else:
                phrase = seg.push(frame) if self.enabled.is_set() else None
            if phrase is not None and self.enabled.is_set():
                try:
                    self.phrases.put_nowait((phrase, time.time()))
                except queue.Full:  # falling behind: drop oldest to stay live
                    self.phrases.get_nowait()
                    self.phrases.put_nowait((phrase, time.time()))

    def _work_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                audio, ended_at = self.phrases.get(timeout=0.2)
            except queue.Empty:
                continue
            audio, ended_at = merge_backlog(self.phrases, audio, ended_at)
            english = self.recognizer.transcribe(audio)
            if not english:
                continue
            hebrew = self.translator.translate(english)
            self.on_line(english, hebrew, time.time() - ended_at)


def _set_default_mic(name: str) -> bool:
    """Make the Windows default recording device the one matching `name`."""
    import subprocess

    from .config import ROOT

    script = ROOT / "tools" / "set_default_audio.ps1"
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Recording", name],
        capture_output=True, text=True, creationflags=0x08000000,  # no console window
    )
    print(f"[default mic] {r.stdout.strip() or r.stderr.strip()}", flush=True)
    return r.returncode == 0


def _route_default_mic(s):
    """Switch the default mic to the virtual cable; return the mic to restore."""
    if not s.auto_default_mic:
        return None
    try:
        import soundcard as sc

        current = sc.default_microphone().name
        if "cable" in current.lower():
            # Already routed (the app was killed last time): restore to the
            # real mic on exit this time.
            real = next((m.name for m in sc.all_microphones() if "cable" not in m.name.lower()), None)
            return real
        if not any("cable output" in m.name.lower() for m in sc.all_microphones()):
            return None  # VB-CABLE not installed
        if _set_default_mic("CABLE Output"):
            return current
    except Exception as e:
        print(f"[default mic] {e}", flush=True)
    return None


def run_live() -> None:
    from .config import MODELS_DIR
    from .speak import SpeakPipeline
    from .ui import SubtitleWindow

    s = load_settings()
    holder: dict = {}
    restore_mic = _route_default_mic(s)

    def quit_all():
        holder["p"].stop()
        holder["sp"].stop()
        if holder.get("tray"):
            holder["tray"].stop()
        if restore_mic:
            _set_default_mic(restore_mic.split(" (")[0])

    ui = SubtitleWindow(
        s,
        on_toggle=lambda: holder["p"].toggle(),
        on_quit=quit_all,
        on_toggle_speak=lambda: holder["sp"].toggle(),
    )
    sp = SpeakPipeline(
        s,
        MODELS_DIR,
        on_line=lambda he, en, lag: (print(f"[speak +{lag:.1f}s] {he} -> {en}", flush=True), ui.post("speak_line", he, en)),
        on_status=lambda mic, out, virtual: (print(f"[speak] mic={mic!r} out={out!r} virtual={virtual}", flush=True),
                                             ui.post("speak_devices", mic, out, virtual)),
        on_error=lambda t: (print(f"[speak] {t}", flush=True), ui.post("speak_error", t)),
    )
    holder["sp"] = sp
    ui.on_toggle_voice = sp.toggle_voice
    p = Pipeline(
        s,
        on_line=lambda en, he, lag: (print(f"[+{lag:.1f}s] {en} -> {he}", flush=True), ui.post("line", en, he)),
        on_status=lambda t: ui.post("status", t),
        on_ready=lambda: ui.post("ready"),
        on_error=lambda t: ui.post("error", t),
        on_partial=lambda en, he: ui.post("partial", en, he),
    )
    holder["p"] = p
    print(f"[engine] {'azure (cloud)' if cloud_credentials(s) else 'local'}", flush=True)
    try:
        from .hotkeys import register

        register({
            s.hotkey: lambda: ui.post("toggle"),
            s.speak_hotkey: lambda: ui.post("speak_toggle"),
            s.window_hotkey: lambda: ui.post("window_toggle"),
        })
    except Exception as e:
        print(f"[hotkey] unavailable: {e}", flush=True)  # tray menu still works

    try:
        from .tray import Tray

        ui.tray = holder["tray"] = Tray(ui.post, ui.state)
    except Exception as e:
        print(f"[tray] unavailable: {e}", flush=True)
    if s.start_hidden and ui.tray:  # live by the clock until subtitles are wanted
        p.enabled.clear()
        ui.enabled = False
        ui._resume_on_show = True
        ui.visible = False
        ui.root.withdraw()
        ui.tray.refresh()
    p.start()
    sp.start()

    def watch_speak_ready():
        if sp.ready:
            ui.post("speak_ready")
        else:
            ui.root.after(500, watch_speak_ready)

    watch_speak_ready()
    ui.run()


def run_speak_file(path: str, out_path: str, volume: float = 1.0) -> None:
    """Hebrew WAV -> English voice WAV, printing timings per phrase."""
    import wave

    from .config import MODELS_DIR
    from .engines import make_recognizer, make_translator, make_voice

    s = load_settings()
    a = _read_wav(path)
    rec = make_recognizer(s, s.speak_whisper_model, "he")
    tr = make_translator(s, "he", "en", s.he_en_model_dir)
    voice = make_voice(s, MODELS_DIR)
    seg = PhraseSegmenter(s.mic_speech_threshold, s.silence_end_sec, s.max_phrase_sec, adaptive=True)
    a = a * volume  # --volume: simulate a quiet microphone
    a = np.concatenate([np.random.normal(0, 0.0003, SAMPLE_RATE * 2).astype(np.float32), a])  # room noise lead-in
    out = []
    for i in range(0, len(a) - FRAME, FRAME):
        phrase = seg.push(a[i:i + FRAME])
        if phrase is None:
            continue
        t0 = time.time()
        he = rec.transcribe(phrase)
        t1 = time.time()
        en = tr.translate(he)
        t2 = time.time()
        first = None
        for pcm in voice.speak(en):
            first = first or time.time()
            out.append(pcm)
        print(f"[{len(phrase) / SAMPLE_RATE:4.1f}s phrase | ASR {t1 - t0:.2f}s | MT {t2 - t1:.2f}s | "
              f"voice starts {first - t2 if first else 0:.2f}s | total {(first or t2) - t0:.2f}s]")
        print(f"  HE: {he}\n  EN: {en}")
    if out:
        pcm = (np.clip(np.concatenate(out), -1, 1) * 32767).astype(np.int16)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(voice.sample_rate)
            w.writeframes(pcm.tobytes())
        print("English voice saved to", out_path)


def _read_wav(path: str) -> np.ndarray:
    import wave

    w = wave.open(path)
    sr, ch = w.getframerate(), w.getnchannels()
    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    if ch > 1:
        a = a.reshape(-1, ch).mean(1)
    a = np.interp(np.arange(0, len(a), sr / SAMPLE_RATE), np.arange(len(a)), a).astype(np.float32)
    return np.concatenate([a, np.zeros(SAMPLE_RATE, np.float32)])  # trailing silence


def run_file(path: str) -> None:
    """Feed a WAV file through the same pipeline in real time and print timings."""
    import wave

    from .engines import Recognizer, make_translator

    s = load_settings()
    a = _read_wav(path)
    rec = Recognizer(s.whisper_model, s.cpu_threads)
    tr = make_translator(s, "en", "he", s.translation_model_dir)
    seg = PhraseSegmenter(s.speech_threshold, s.silence_end_sec, s.max_phrase_sec)
    for i in range(0, len(a) - FRAME, FRAME):
        phrase = seg.push(a[i:i + FRAME])
        if phrase is None:
            continue
        t0 = time.time()
        en = rec.transcribe(phrase)
        t1 = time.time()
        he = tr.translate(en)
        t2 = time.time()
        print(f"[{len(phrase) / SAMPLE_RATE:4.1f}s phrase | ASR {t1 - t0:.2f}s | MT {t2 - t1:.2f}s]")
        print(f"  EN: {en}\n  HE: {he}")


def main() -> None:
    if sys.stdout is None:  # started with pythonw (no console): log to a file
        from .config import ROOT

        sys.stdout = sys.stderr = open(ROOT / "livetranslator.log", "a", encoding="utf-8", buffering=1)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Live English -> Hebrew subtitles for any audio on this PC")
    ap.add_argument("--file", help="test subtitles on an English WAV file instead of live audio")
    ap.add_argument("--speak-file", help="test the speaking direction on a Hebrew WAV file")
    ap.add_argument("--out", default="english_voice.wav", help="where --speak-file saves the English voice")
    ap.add_argument("--volume", type=float, default=1.0, help="scale --speak-file audio (e.g. 0.02 = very quiet mic)")
    args = ap.parse_args()
    if args.speak_file:
        run_speak_file(args.speak_file, args.out, args.volume)
    elif args.file:
        run_file(args.file)
    else:
        run_live()


if __name__ == "__main__":
    main()
