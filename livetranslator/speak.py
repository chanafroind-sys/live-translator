"""Speaking direction: your Hebrew (real mic) -> English voice (virtual mic).

    real microphone ──► MicCapture ──┬──► PhraseSegmenter ─► Whisper (he) ─► he→en ─► Piper voice ─┐
                                     │                                                             │
                                     └──► Mixer: your voice (quiet) + English voice (loud) ◄───────┘
                                                  │
                                                  ▼
                                    "CABLE Input"  ==  "CABLE Output" mic in Zoom/Meet/...

When translation is off the mixer passes your voice through unchanged, so the
virtual mic always works like a normal microphone.
"""
from __future__ import annotations

import queue
import threading
import time
import warnings

import numpy as np

from .audio import FRAME, SAMPLE_RATE, PhraseSegmenter

OUT_RATE = 48000
OUT_FRAME = FRAME * OUT_RATE // SAMPLE_RATE  # 1440 samples = 30 ms
_VIRTUAL = ("cable", "vb-audio", "voicemeeter", "virtual")


def _is_virtual(name: str) -> bool:
    return any(v in name.lower() for v in _VIRTUAL)


def _resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(x) == 0:
        return x.astype(np.float32)
    n = int(round(len(x) * dst / src))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def find_devices(sc, mic_name: str, out_name: str):
    """Return (real microphone, output speaker, output_is_virtual)."""
    mics = sc.all_microphones()
    mic = None
    if mic_name:
        mic = next((m for m in mics if mic_name.lower() in m.name.lower()), None)
    if mic is None:
        default = sc.default_microphone()
        # If Windows' default mic is the virtual cable, never record from it:
        # that would feed our own output back in.
        mic = default if not _is_virtual(default.name) else next((m for m in mics if not _is_virtual(m.name)), None)
    if mic is None:
        raise RuntimeError("No real microphone found")

    speakers = sc.all_speakers()
    out = next((s for s in speakers if out_name and out_name.lower() in s.name.lower()), None)
    if out is not None:
        return mic, out, True
    return mic, sc.default_speaker(), False


class SpeakPipeline:
    def __init__(self, settings, models_dir, on_line, on_status, on_error):
        self.s = settings
        self.models_dir = models_dir
        self.on_line, self.on_status, self.on_error = on_line, on_status, on_error
        self.enabled = threading.Event()
        if settings.speak_on_at_start:
            self.enabled.set()
        self.stopping = threading.Event()
        self.ready = False
        self.output_is_virtual = False
        self.seg_frames: "queue.Queue[np.ndarray]" = queue.Queue()
        self.mix_frames: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=50)
        self.phrases: "queue.Queue[tuple[np.ndarray, float]]" = queue.Queue(maxsize=4)
        self.tts = np.zeros(0, np.float32)  # English audio waiting to be mixed in
        self.tts_lock = threading.Lock()

    # ---- control --------------------------------------------------------
    def toggle(self) -> bool:
        if self.enabled.is_set():
            self.enabled.clear()
            with self.tts_lock:  # stop any English still queued
                self.tts = np.zeros(0, np.float32)
        else:
            self.enabled.set()
        return self.enabled.is_set()

    def start(self) -> None:
        threading.Thread(target=self._audio_loop, daemon=True, name="speak-audio").start()
        threading.Thread(target=self._boot_models, daemon=True, name="speak-boot").start()

    def stop(self) -> None:
        self.stopping.set()

    # ---- audio I/O: runs from the start so the virtual mic always works --
    def _audio_loop(self) -> None:
        try:
            import soundcard as sc

            warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
            mic, out, virtual = find_devices(sc, self.s.mic_device, self.s.speak_output_device)
            self.output_is_virtual = virtual
            self.on_status(mic.name, out.name, virtual)
            threading.Thread(target=self._mix_loop, args=(out,), daemon=True, name="speak-mix").start()
            with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=FRAME) as rec:
                while not self.stopping.is_set():
                    frame = np.asarray(rec.record(numframes=FRAME), dtype=np.float32).reshape(-1)
                    self.seg_frames.put(frame)
                    try:
                        self.mix_frames.put_nowait(frame)
                    except queue.Full:
                        pass
        except Exception as e:
            self.on_error(f"שגיאת מיקרופון: {e}")

    def _mix_loop(self, out) -> None:
        """Your voice is the clock: every 30 ms of mic audio produces 30 ms of output."""
        try:
            import soundcard as sc  # noqa: F401  (COM init for this thread)

            with out.player(samplerate=OUT_RATE, channels=1, blocksize=OUT_FRAME) as player:
                while not self.stopping.is_set():
                    try:
                        frame = self.mix_frames.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    on = self.enabled.is_set()
                    # In test mode (no virtual cable) the output is your own
                    # speakers, so don't echo your voice back at you.
                    gain = (self.s.original_voice_gain if on else 1.0) if self.output_is_virtual else 0.0
                    chunk = _resample(frame, SAMPLE_RATE, OUT_RATE)[:OUT_FRAME] * gain
                    if len(chunk) < OUT_FRAME:
                        chunk = np.pad(chunk, (0, OUT_FRAME - len(chunk)))
                    with self.tts_lock:
                        if len(self.tts):
                            take = self.tts[:OUT_FRAME]
                            self.tts = self.tts[OUT_FRAME:]
                            chunk[: len(take)] += take
                    player.play(np.clip(chunk, -1, 1))
        except Exception as e:
            self.on_error(f"שגיאת פלט שמע: {e}")

    # ---- recognition / translation / voice ------------------------------
    def _boot_models(self) -> None:
        from .app import cloud_credentials

        creds = cloud_credentials(self.s)
        if creds:
            self._boot_cloud(*creds)
            return
        try:
            from .engines import Recognizer, make_translator, make_voice

            self.recognizer = Recognizer(self.s.speak_whisper_model, self.s.cpu_threads, language="he")
            self.translator = make_translator(self.s, "he", "en", self.s.he_en_model_dir)
            self.voice = make_voice(self.s, self.models_dir)
        except Exception as e:
            self.on_error(f"שגיאה בטעינת מודלי דיבור: {e}")
            return
        self.ready = True
        threading.Thread(target=self._segment_loop, daemon=True, name="speak-seg").start()
        threading.Thread(target=self._work_loop, daemon=True, name="speak-work").start()

    def _boot_cloud(self, key: str, region: str) -> None:
        """Azure: streaming he->en translation, then an Azure neural voice."""
        from .cloud import AzureStreamTranslator, AzureVoice

        finals: "queue.Queue[tuple[str, str, float]]" = queue.Queue()
        try:
            self.voice = AzureVoice(key, region, self.s.azure_voice)
            self.cloud = AzureStreamTranslator(
                key, region, "he", "en", self.s.mic_speech_threshold, int(self.s.silence_end_sec * 1000),
                on_partial=lambda he, en: None,
                on_final=lambda he, en, lag: finals.put((he, en, time.time())),
                on_error=self.on_error,
            )
        except Exception as e:
            self.on_error(f"שגיאת ענן: {e}")
            return

        def feed():
            while not self.stopping.is_set():
                try:
                    frame = self.seg_frames.get(timeout=0.2)
                except queue.Empty:
                    continue
                if self.enabled.is_set():  # nothing is sent while off
                    self.cloud.push(frame)
            self.cloud.close()

        def speak():
            while not self.stopping.is_set():
                try:
                    he, en, got_at = finals.get(timeout=0.2)
                except queue.Empty:
                    continue
                if not self.enabled.is_set() or not en:
                    continue
                try:
                    self._queue_voice(he, en, got_at)
                except Exception as e:
                    self.on_error(str(e))

        threading.Thread(target=feed, daemon=True, name="speak-cloud-feed").start()
        threading.Thread(target=speak, daemon=True, name="speak-cloud-voice").start()
        self.ready = True

    def _queue_voice(self, hebrew: str, english: str, since: float) -> None:
        first = True
        for pcm in self.voice.speak(english):
            if not self.enabled.is_set():
                break
            pcm = _resample(pcm, self.voice.sample_rate, OUT_RATE)
            with self.tts_lock:
                self.tts = np.concatenate([self.tts, pcm])
            if first:
                self.on_line(hebrew, english, time.time() - since)
                first = False

    def _segment_loop(self) -> None:
        seg = PhraseSegmenter(self.s.mic_speech_threshold, self.s.silence_end_sec, self.s.max_phrase_sec)
        while not self.stopping.is_set():
            try:
                frame = self.seg_frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if not self.enabled.is_set():
                continue
            phrase = seg.push(frame)
            if phrase is not None:
                try:
                    self.phrases.put_nowait((phrase, time.time()))
                except queue.Full:
                    self.phrases.get_nowait()
                    self.phrases.put_nowait((phrase, time.time()))

    def _work_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                audio, ended_at = self.phrases.get(timeout=0.2)
            except queue.Empty:
                continue
            if not self.enabled.is_set():
                continue
            from .app import merge_backlog

            audio, ended_at = merge_backlog(self.phrases, audio, ended_at)
            hebrew = self.recognizer.transcribe(audio)
            if not hebrew:
                continue
            english = self.translator.translate(hebrew)
            self._queue_voice(hebrew, english, ended_at)
