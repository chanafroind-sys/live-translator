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


class Pipeline:
    """capture -> segment -> recognize -> translate -> callback."""

    def __init__(self, settings, on_line, on_status, on_ready, on_error):
        self.s = settings
        self.on_line, self.on_status, self.on_ready, self.on_error = on_line, on_status, on_ready, on_error
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
        try:
            from .engines import Recognizer, Translator

            self.recognizer = Recognizer(self.s.whisper_model, self.s.cpu_threads)
            self.translator = Translator(self.s.translation_model_dir, self.s.cpu_threads)
        except Exception as e:
            self.on_error(f"שגיאה בטעינת מודלים: {e}")
            return
        self.capture = LoopbackCapture(self.frames)
        self.capture.start()
        threading.Thread(target=self._segment_loop, daemon=True, name="segment").start()
        threading.Thread(target=self._work_loop, daemon=True, name="work").start()
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
            english = self.recognizer.transcribe(audio)
            if not english:
                continue
            hebrew = self.translator.translate(english)
            self.on_line(english, hebrew, time.time() - ended_at)


def run_live() -> None:
    from .ui import SubtitleWindow

    s = load_settings()
    holder: dict = {}
    ui = SubtitleWindow(s, on_toggle=lambda: holder["p"].toggle(), on_quit=lambda: holder["p"].stop())
    p = Pipeline(
        s,
        on_line=lambda en, he, lag: (print(f"[+{lag:.1f}s] {en} -> {he}", flush=True), ui.post("line", en, he)),
        on_status=lambda t: ui.post("status", t),
        on_ready=lambda: ui.post("ready"),
        on_error=lambda t: ui.post("error", t),
    )
    holder["p"] = p
    try:
        import keyboard

        keyboard.add_hotkey(s.hotkey, lambda: ui.post("toggle"))
    except Exception:
        pass  # hotkey is optional; right-click menu still works
    p.start()
    ui.run()


def run_file(path: str) -> None:
    """Feed a WAV file through the same pipeline in real time and print timings."""
    import wave

    from .engines import Recognizer, Translator

    s = load_settings()
    w = wave.open(path)
    sr, ch = w.getframerate(), w.getnchannels()
    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    if ch > 1:
        a = a.reshape(-1, ch).mean(1)
    a = np.interp(np.arange(0, len(a), sr / SAMPLE_RATE), np.arange(len(a)), a).astype(np.float32)
    a = np.concatenate([a, np.zeros(SAMPLE_RATE, np.float32)])  # trailing silence

    rec = Recognizer(s.whisper_model, s.cpu_threads)
    tr = Translator(s.translation_model_dir, s.cpu_threads)
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
    ap.add_argument("--file", help="test on a WAV file instead of live audio")
    args = ap.parse_args()
    run_file(args.file) if args.file else run_live()


if __name__ == "__main__":
    main()
