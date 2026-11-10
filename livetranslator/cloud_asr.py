"""Fast speech recognition in the cloud (Groq, Whisper large-v3).

Local Whisper `small` needs ~5 s for 3-4 s of Hebrew on this laptop, slower
than real time, so continuous speech piles up. Groq runs Whisper large-v3
(better Hebrew than `small`) in well under a second.

Needs GROQ_API_KEY (free account at console.groq.com). Falls back to the
local recognizer on any error or when offline. Audio is only sent for
phrases that contain speech, and only while that direction is switched on.
"""
from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np

URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def _ssl_context():
    """Verify certificates with the Windows certificate store.

    Python 3.13+ adds VERIFY_X509_STRICT, which rejects Groq's certificate
    chain ("Missing Authority Key Identifier"). Windows' own verifier accepts
    it; verification stays fully on, just without that extra strictness.
    """
    import ssl

    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return ctx


_CTX = _ssl_context()


def _wav_bytes(audio: np.ndarray, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


class GroqRecognizer:
    def __init__(self, language: str, model: str, make_fallback, timeout: float = 6.0):
        self.key = os.environ.get("GROQ_API_KEY", "")
        if not self.key:
            raise RuntimeError("GROQ_API_KEY is not set")
        self.language, self.model, self.timeout = language, model, timeout
        self._make_fallback, self._fallback = make_fallback, None
        self.context = ""
        self.failures = 0

    def _local(self, audio: np.ndarray) -> str:
        if self._fallback is None:  # loaded only if the cloud ever fails
            print("[asr] loading local model as fallback", flush=True)
            self._fallback = self._make_fallback()
        return self._fallback.transcribe(audio)

    def transcribe(self, audio: np.ndarray) -> str:
        if self.failures >= 3:
            return self._local(audio)
        peak = float(np.abs(audio).max()) if len(audio) else 0.0
        if 0 < peak < 0.5:
            audio = audio * (0.7 / peak)
        # verbose_json gives per-segment no_speech_prob, to drop what Whisper
        # "hears" in noise (its favourite in Hebrew: "תודה רבה.").
        fields = {"model": self.model, "language": self.language, "response_format": "verbose_json", "temperature": "0"}
        # Whisper's prompt steers spelling: tech words people mix into Hebrew,
        # plus the last thing said for names/context.
        hint = "Backend, Frontend, Refactor, Redesign, Freelance, AI, Agents. " if self.language == "he" else ""
        fields["prompt"] = (hint + self.context)[-200:] or " "
        boundary = uuid.uuid4().hex
        body = io.BytesIO()
        for k, v in fields.items():
            body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                   f"Content-Type: audio/wav\r\n\r\n".encode())
        body.write(_wav_bytes(audio))
        body.write(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(URL, data=body.getvalue(), headers={
            "Authorization": f"Bearer {self.key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "live-translator",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_CTX) as r:
                data = json.loads(r.read().decode("utf-8"))
            kept = []
            for seg in data.get("segments") or []:
                if seg.get("no_speech_prob", 0) > 0.5 or seg.get("avg_logprob", 0) < -1.0:
                    print(f"[asr] dropped as non-speech: {seg.get('text', '').strip()!r} "
                          f"(no_speech {seg.get('no_speech_prob', 0):.2f}, logprob {seg.get('avg_logprob', 0):.2f})", flush=True)
                    continue
                kept.append(seg.get("text", "").strip())
            text = " ".join(kept).strip() if data.get("segments") is not None else data.get("text", "").strip()
        except urllib.error.HTTPError as e:
            print(f"[asr] Groq HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}", flush=True)
            self.failures += 1 if e.code in (400, 401, 403, 404) else 0
            return self._local(audio)
        except Exception as e:  # offline, timeout
            print(f"[asr] Groq: {e}", flush=True)
            self.failures += 1
            return self._local(audio)
        self.failures = 0
        from .engines import _HALLUCINATIONS

        if text.lower() in _HALLUCINATIONS:
            return ""
        # Whisper large "hears" a lone thank-you in short bursts of noise.
        if len(audio) < 1.2 * 16000 and text.strip(" .!") in ("תודה", "תודה רבה", "Thank you"):
            print(f"[asr] dropped likely noise: {text!r}", flush=True)
            return ""
        if text:
            self.context = text
        return text
