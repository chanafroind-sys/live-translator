"""Cloud mode: Azure AI Speech for fast, streaming translation and voices.

Same audio plumbing as local mode (loopback capture, mic, mixer, virtual
cable); only recognition + translation + voice move to Azure. Azure streams
results while you are still talking, so delay drops to ~1-2 seconds.

Setup: create a free "Speech" resource in the Azure portal, then set
AZURE_SPEECH_KEY and AZURE_SPEECH_REGION (or put them in config.json,
which git ignores). The free tier covers 5 audio hours per month.

Cost control: audio is only sent while someone is speaking (plus a short
tail), never during silence, and never while a direction is switched off.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

import numpy as np

from .audio import FRAME, SAMPLE_RATE

_LOCALES = {"en": "en-US", "he": "he-IL"}


def azure_credentials(settings) -> tuple[str, str] | None:
    key = os.environ.get("AZURE_SPEECH_KEY") or settings.azure_key
    region = os.environ.get("AZURE_SPEECH_REGION") or settings.azure_region
    return (key, region) if key and region else None


class AzureStreamTranslator:
    """Continuous speech translation from pushed 16 kHz frames.

    on_partial(source, translation) fires while the phrase is in progress;
    on_final(source, translation, seconds_since_last_audio) when it ends.
    """

    def __init__(self, key, region, src: str, tgt: str, threshold: float, silence_ms: int,
                 on_partial, on_final, on_error):
        import azure.cognitiveservices.speech as sdk

        self.sdk = sdk
        self.tgt = tgt
        self.threshold = threshold
        self.on_partial, self.on_final, self.on_error = on_partial, on_final, on_error

        cfg = sdk.translation.SpeechTranslationConfig(subscription=key, region=region)
        cfg.speech_recognition_language = _LOCALES[src]
        cfg.add_target_language(tgt)
        cfg.set_property(sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, str(silence_ms))
        fmt = sdk.audio.AudioStreamFormat(samples_per_second=SAMPLE_RATE, bits_per_sample=16, channels=1)
        self.stream = sdk.audio.PushAudioInputStream(fmt)
        self.recognizer = sdk.translation.TranslationRecognizer(
            translation_config=cfg, audio_config=sdk.audio.AudioConfig(stream=self.stream)
        )
        self.recognizer.recognizing.connect(self._partial)
        self.recognizer.recognized.connect(self._final)
        self.recognizer.canceled.connect(self._canceled)
        self.recognizer.start_continuous_recognition_async()

        # Speech gate: only stream while there is sound (saves money).
        self.preroll: deque[np.ndarray] = deque(maxlen=10)  # 300 ms
        self.active_until = 0.0
        self.last_audio = time.time()

    def push(self, frame: np.ndarray) -> None:
        now = time.time()
        loud = float(np.sqrt(np.mean(frame * frame))) > self.threshold
        if loud:
            if now > self.active_until:  # speech starts: send what came just before
                for f in self.preroll:
                    self._write(f)
                self.preroll.clear()
            self.active_until = now + 1.5  # keep a tail so Azure detects the pause
        if now <= self.active_until:
            self._write(frame)
            self.last_audio = now
        else:
            self.preroll.append(frame)

    def _write(self, frame: np.ndarray) -> None:
        self.stream.write((np.clip(frame, -1, 1) * 32767).astype(np.int16).tobytes())

    def _partial(self, evt) -> None:
        r = evt.result
        self.on_partial(r.text, r.translations.get(self.tgt, ""))

    def _final(self, evt) -> None:
        r = evt.result
        if r.reason == self.sdk.ResultReason.TranslatedSpeech and r.text:
            self.on_final(r.text, r.translations.get(self.tgt, ""), time.time() - self.last_audio)

    def _canceled(self, evt) -> None:
        d = evt.cancellation_details
        if d.reason == self.sdk.CancellationReason.Error:
            self.on_error(f"Azure: {d.error_details}")

    def close(self) -> None:
        try:
            self.stream.close()
            self.recognizer.stop_continuous_recognition_async()
        except Exception:
            pass


class AzureVoice:
    """Azure neural voice, returned as float32 audio at 24 kHz."""

    def __init__(self, key, region, voice_name: str, rate: str = "+0%"):
        import azure.cognitiveservices.speech as sdk

        self.sdk = sdk
        cfg = sdk.SpeechConfig(subscription=key, region=region)
        cfg.speech_synthesis_voice_name = voice_name
        cfg.set_speech_synthesis_output_format(sdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm)
        self.synth = sdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
        self.voice_name, self.rate = voice_name, rate
        self.sample_rate = 24000
        self.lock = threading.Lock()

    def speak(self, text: str):
        from xml.sax.saxutils import escape

        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
            f'<voice name="{self.voice_name}"><prosody rate="{self.rate}">{escape(text)}</prosody></voice></speak>'
        )
        with self.lock:
            result = self.synth.speak_ssml_async(ssml).get()
        if result.reason != self.sdk.ResultReason.SynthesizingAudioCompleted:
            raise RuntimeError(f"Azure voice failed: {result.cancellation_details.error_details}")
        yield np.frombuffer(result.audio_data, np.int16).astype(np.float32) / 32768
