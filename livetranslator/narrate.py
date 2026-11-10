"""Hebrew narration of the subtitles: hear the translation, original quieter.

While narration is on, every other app's volume (Zoom, Chrome, ...) is
lowered to `duck_volume` through Windows' per-app mixer, and our Hebrew voice
plays at full volume. Volumes are restored when narration is switched off or
the app exits.

The subtitles listen to "what the PC plays", which now includes our own
Hebrew voice; `busy()` lets the pipeline ignore audio while we speak.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import warnings

import json

import numpy as np

from .config import ROOT

# Original per-app volumes, on disk while they are lowered, so a crash or a
# forced close can never leave Zoom/Chrome quiet: the next start (or
# restore_saved_volumes()) puts them back.
SAVED = ROOT / "ducked_volumes.json"


def restore_saved_volumes() -> None:
    if not SAVED.exists():
        return
    try:
        from pycaw.pycaw import AudioUtilities

        saved = json.loads(SAVED.read_text(encoding="utf-8"))
        for s in AudioUtilities.GetAllSessions():
            if s.Process and s.Process.name() in saved:
                s.SimpleAudioVolume.SetMasterVolume(float(saved[s.Process.name()]), None)
        SAVED.unlink()
        print("[narrate] restored app volumes from last run", flush=True)
    except Exception as e:
        print(f"[narrate] could not restore saved volumes: {e}", flush=True)


class Narrator:
    def __init__(self, settings, models_dir):
        self.s = settings
        self.models_dir = models_dir
        self.texts: "queue.Queue[str]" = queue.Queue()
        self.on = threading.Event()
        self.quiet_until = 0.0
        self.speaking = False  # from taking a text until its audio has played
        self.voice = None
        self.saved_volumes: dict = {}
        restore_saved_volumes()  # left over from a crash / forced close
        threading.Thread(target=self._loop, daemon=True, name="narrate").start()
        # Loading the Hebrew voice (with its niqqud model) takes ~8 s: do it
        # now, in the background, so the first narration isn't late.
        threading.Thread(target=self._load_voice, daemon=True, name="narrate-load").start()

    def _load_voice(self) -> None:
        try:
            from .engines import Voice

            t0 = time.time()
            self.voice = Voice(self.s.hebrew_voice, self.models_dir)
            list(self.voice.speak("שלום"))  # warm up
            print(f"[narrate] Hebrew voice ready in {time.time() - t0:.1f}s", flush=True)
        except Exception as e:
            print(f"[narrate] Hebrew voice unavailable: {e}", flush=True)

    # ---- control --------------------------------------------------------
    def set(self, on: bool) -> None:
        if on:
            self.on.set()
            self._duck()
        else:
            self.on.clear()
            while not self.texts.empty():
                self.texts.get_nowait()
            self._restore()

    def toggle(self) -> bool:
        self.set(not self.on.is_set())
        return self.on.is_set()

    def say(self, hebrew: str) -> None:
        if self.on.is_set() and hebrew:
            self.texts.put(hebrew)

    def busy(self) -> bool:
        """True while we are (about to be) speaking, plus a short tail."""
        return self.speaking or not self.texts.empty() or time.time() < self.quiet_until

    def close(self) -> None:
        self._restore()

    # ---- other apps quieter ---------------------------------------------
    def _sessions(self):
        from pycaw.pycaw import AudioUtilities

        me = os.getpid()
        for s in AudioUtilities.GetAllSessions():
            if s.Process and s.Process.pid != me:
                yield s

    def _duck(self) -> None:
        try:
            sessions = list(self._sessions())
            for s in sessions:
                if s.Process.pid not in self.saved_volumes:
                    self.saved_volumes[s.Process.pid] = s.SimpleAudioVolume.GetMasterVolume()
            # Write originals to disk BEFORE lowering anything.
            names = {s.Process.name(): self.saved_volumes[s.Process.pid] for s in sessions}
            SAVED.write_text(json.dumps(names), encoding="utf-8")
            for s in sessions:
                s.SimpleAudioVolume.SetMasterVolume(self.saved_volumes[s.Process.pid] * self.s.duck_volume, None)
        except Exception as e:
            print(f"[narrate] could not lower other apps: {e}", flush=True)

    def _restore(self) -> None:
        if not self.saved_volumes:
            return
        try:
            for s in self._sessions():
                if s.Process.pid in self.saved_volumes:
                    s.SimpleAudioVolume.SetMasterVolume(self.saved_volumes[s.Process.pid], None)
        except Exception as e:
            print(f"[narrate] could not restore volumes: {e}", flush=True)
            return  # keep the file so the next start retries
        self.saved_volumes = {}
        SAVED.unlink(missing_ok=True)

    # ---- speaking -------------------------------------------------------
    def _loop(self) -> None:
        """One output stream kept open for the app's whole life.

        Windows applies the "exclude this app" capture rule to a stream only
        after it starts, so a fresh stream per sentence leaked its first
        moments back into the subtitles ("שלום" -> "Hello" -> "שלום"...).
        A single long-lived stream (silence when idle) never leaks.
        """
        import soundcard as sc

        warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
        while self.voice is None:  # loading in the background
            time.sleep(0.1)
        rate = self.voice.sample_rate
        block = rate // 20  # 50 ms
        silence = np.zeros(block, np.float32)
        while True:
            try:
                with sc.default_speaker().player(samplerate=rate, channels=1, blocksize=block) as player:
                    while True:
                        try:
                            text = self.texts.get(timeout=0.05)
                        except queue.Empty:
                            player.play(silence)
                            continue
                        if not self.on.is_set():
                            continue
                        self.speaking = True
                        try:
                            t0 = time.time()
                            pcm = np.concatenate(list(self.voice.speak(text)))
                            print(f"[narrate] voiced in {time.time() - t0:.1f}s: {text}", flush=True)
                            self.quiet_until = time.time() + len(pcm) / rate + 0.4
                            player.play(pcm * self.s.narration_volume)
                            self.quiet_until = time.time() + 0.4
                        finally:
                            self.speaking = False
            except Exception as e:  # device unplugged etc.: reopen
                print(f"[narrate] {e}", flush=True)
                time.sleep(1)
