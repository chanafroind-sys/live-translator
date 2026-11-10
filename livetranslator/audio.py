"""Capture whatever the computer is playing and cut it into spoken phrases."""
from __future__ import annotations

import queue
import threading
import time
import warnings
from collections import deque

import numpy as np

SAMPLE_RATE = 16000
FRAME = 480  # 30 ms at 16 kHz


class LoopbackCapture(threading.Thread):
    """Records the default speaker output ("what you hear") at 16 kHz mono.

    Uses WASAPI loopback, so it works with any app: Zoom, Meet, a browser,
    a video player, a downloaded lesson. Nothing is routed or changed;
    it only listens to what is already playing.
    """

    def __init__(self, out: "queue.Queue[np.ndarray]"):
        super().__init__(daemon=True, name="loopback")
        self.out = out
        self.stop_event = threading.Event()
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            # soundcard initializes COM for the importing thread, so import here.
            import soundcard as sc

            warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
            while not self.stop_event.is_set():
                speaker = sc.default_speaker()  # follow device changes
                mic = sc.get_microphone(str(speaker.name), include_loopback=True)
                with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=FRAME) as rec:
                    started = time.time()
                    while not self.stop_event.is_set():
                        data = rec.record(numframes=FRAME)
                        self.out.put(np.asarray(data, dtype=np.float32).reshape(-1))
                        # Re-check the default device every few seconds.
                        if time.time() - started > 5 and str(sc.default_speaker().name) != str(speaker.name):
                            break
        except Exception as e:  # surfaced to the UI
            self.error = e

    def stop(self) -> None:
        self.stop_event.set()


class PhraseSegmenter:
    """Groups 30 ms frames into phrases using loudness + silence timing."""

    def __init__(self, threshold: float, silence_end_sec: float, max_phrase_sec: float):
        self.threshold = threshold
        self.silence_frames = int(silence_end_sec * SAMPLE_RATE / FRAME)
        self.max_frames = int(max_phrase_sec * SAMPLE_RATE / FRAME)
        self.preroll: deque[np.ndarray] = deque(maxlen=10)  # 300 ms before speech
        self.frames: list[np.ndarray] = []
        self.silent_run = 0
        self.last_frame_time = time.time()

    def push(self, frame: np.ndarray) -> np.ndarray | None:
        """Add a frame; return a finished phrase or None."""
        self.last_frame_time = time.time()
        loud = float(np.sqrt(np.mean(frame * frame))) > self.threshold
        if not self.frames:
            if loud:
                self.frames = list(self.preroll) + [frame]
                self.silent_run = 0
            else:
                self.preroll.append(frame)
            return None

        self.frames.append(frame)
        self.silent_run = 0 if loud else self.silent_run + 1
        if self.silent_run >= self.silence_frames or len(self.frames) >= self.max_frames:
            return self._flush()
        return None

    def flush_if_stalled(self, stall_sec: float = 0.5) -> np.ndarray | None:
        """Loopback delivers no frames when playback stops; end the phrase then."""
        if self.frames and time.time() - self.last_frame_time > stall_sec:
            return self._flush()
        return None

    def _flush(self) -> np.ndarray | None:
        speech = len(self.frames) - self.silent_run
        frames, self.frames, self.silent_run = self.frames, [], 0
        self.preroll.clear()
        if speech * FRAME < 0.35 * SAMPLE_RATE:  # too short to be words
            return None
        return np.concatenate(frames)
