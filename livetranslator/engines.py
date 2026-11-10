"""Local speech recognition (Whisper) and translation (CTranslate2)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# Whisper sometimes "hears" these in music or silence.
_HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "thank you for watching.", "you", "bye.",
    "thank you so much for watching.", "please subscribe.", ".",
}


class Recognizer:
    def __init__(self, model_name: str, cpu_threads: int, language: str = "en"):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=cpu_threads)
        self.language = language
        self.context = ""

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            condition_on_previous_text=False,
            initial_prompt=self.context or None,  # keeps names/terms consistent
            without_timestamps=True,
        )
        parts = []
        for s in segments:
            if s.no_speech_prob > 0.6 and s.avg_logprob < -0.8:
                continue
            parts.append(s.text.strip())
        text = " ".join(p for p in parts if p).strip()
        if text.lower() in _HALLUCINATIONS:
            return ""
        words = text.lower().split()
        if len(words) >= 8 and len(set(words)) / len(words) < 0.4:
            return ""  # "it's the same, it's the same, ..." loops on noise/music
        if text:
            self.context = text[-200:]
        return text


class Translator:
    def __init__(self, model_dir: str, cpu_threads: int):
        import ctranslate2

        from .spm_tokenizer import load_spm

        d = Path(model_dir)
        if not (d / "model").exists():
            raise FileNotFoundError(
                f"Translation model not found at {d}. Run: .venv\\Scripts\\python -m livetranslator.download_models"
            )
        self.tok, _ = load_spm(str(d / "sentencepiece.model"))
        self.model = ctranslate2.Translator(
            str(d / "model"), device="cpu", compute_type="int8", intra_threads=max(1, cpu_threads // 2)
        )

    def translate(self, text: str) -> str:
        if not text:
            return ""
        r = self.model.translate_batch([self.tok.encode(text).tokens], beam_size=2, max_decoding_length=256)
        return self.tok.decoder.decode(r[0].hypotheses[0]).strip()


_NLLB_CODES = {"en": "eng_Latn", "he": "heb_Hebr"}
_nllb_shared = {}
_nllb_lock = __import__("threading").Lock()


class NllbTranslator:
    """Meta's NLLB-200 (600M, int8). Far better Hebrew than the Argos models
    (measured: "זה דולק" -> "It's on" instead of "It's fucked"), ~0.3-0.6 s
    per phrase. One model instance is shared by both directions."""

    def __init__(self, model_dir: Path, src: str, tgt: str, cpu_threads: int):
        import ctranslate2
        from tokenizers import Tokenizer

        if not (Path(model_dir) / "model.bin").exists():
            raise FileNotFoundError(
                f"NLLB model not found at {model_dir}. Run: .venv\\Scripts\\python -m livetranslator.download_models"
            )
        with _nllb_lock:
            key = str(model_dir)
            if key not in _nllb_shared:
                _nllb_shared[key] = (
                    ctranslate2.Translator(str(model_dir), device="cpu", compute_type="int8",
                                           intra_threads=max(1, cpu_threads)),
                    Tokenizer.from_file(str(Path(model_dir) / "tokenizer.json")),
                )
        self.model, self.tok = _nllb_shared[key]
        self.src, self.tgt = _NLLB_CODES[src], _NLLB_CODES[tgt]

    def translate(self, text: str) -> str:
        if not text:
            return ""
        tokens = [self.src] + self.tok.encode(text, add_special_tokens=False).tokens + ["</s>"]
        r = self.model.translate_batch([tokens], target_prefix=[[self.tgt]], beam_size=2, max_decoding_length=256)
        ids = [self.tok.token_to_id(t) for t in r[0].hypotheses[0] if t != self.tgt]
        return self.tok.decode([i for i in ids if i is not None]).strip()


def make_translator(settings, src: str, tgt: str, model_dir: str):
    """Local translator (NLLB, or Argos), wrapped by Claude when a key is set."""
    from .config import MODELS_DIR

    try:
        if settings.local_translator != "nllb":
            raise FileNotFoundError("argos selected")
        local = NllbTranslator(MODELS_DIR / "nllb-600m", src, tgt, settings.cpu_threads)
    except FileNotFoundError as e:
        if "argos" not in str(e):
            print(f"[translate] {e} - using Argos", flush=True)
        local = Translator(model_dir, settings.cpu_threads)
    if settings.claude_translation and os.environ.get("ANTHROPIC_API_KEY"):
        from .claude_translate import ClaudeTranslator

        print(f"[translate] {src}->{tgt}: Claude ({settings.claude_model})", flush=True)
        return ClaudeTranslator(src, tgt, settings.claude_model, settings.glossary, fallback=local)
    print(f"[translate] {src}->{tgt}: local ({type(local).__name__})", flush=True)
    return local


def short_path(p) -> str:
    """8.3 short form of a path. espeak-ng (inside Piper) cannot open paths
    with non-ASCII characters, e.g. a Hebrew Windows user name."""
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, 1024)
    return buf.value if n else str(p)


def make_voice(settings, models_dir: Path):
    """The English voice: Kokoro (most natural) or Piper (lighter)."""
    if settings.tts_engine == "kokoro":
        return KokoroVoice(settings.kokoro_voice, settings.kokoro_speed, models_dir, settings.cpu_threads)
    return Voice(settings.piper_voice, models_dir)


def _espeak_data_dir() -> str:
    import piper

    return short_path(Path(piper.__file__).parent / "espeak-ng-data")


class KokoroVoice:
    """Kokoro-82M (ONNX, int8): a very natural local voice.

    Pronunciation comes from Piper's bundled espeak-ng (which works with the
    short-path trick); kokoro-onnx's own espeak loader can't find its data
    under a Hebrew user folder, so we pass phonemes in directly.
    """

    def __init__(self, voice_name: str, speed: float, models_dir: Path, threads: int = 4):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro
        from piper.phonemize_espeak import EspeakPhonemizer

        d = Path(models_dir) / "kokoro"
        model, voices = d / "kokoro-v1.0.int8.onnx", d / "voices-v1.0.bin"
        if not model.exists():
            raise FileNotFoundError(
                "Kokoro model not found. Run: .venv\\Scripts\\python -m livetranslator.download_models"
            )
        # 4 threads measured fastest here; "all cores" was slower and spikier.
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        session = ort.InferenceSession(short_path(model), opts, providers=["CPUExecutionProvider"])
        self.kokoro = Kokoro.from_session(session, short_path(voices))
        self.phonemizer = EspeakPhonemizer(_espeak_data_dir())
        self.lang = "en-gb" if voice_name.startswith("b") else "en-us"
        self.voice_name, self.speed = voice_name, speed
        self.sample_rate = 24000

    def speak(self, text: str):
        """Yield audio sentence by sentence, so playback starts early."""
        for sentence in self.phonemizer.phonemize(self.lang, text):
            ipa = "".join(sentence).strip()
            if not ipa:
                continue
            pcm, self.sample_rate = self.kokoro.create(ipa, voice=self.voice_name, speed=self.speed, is_phonemes=True)
            yield np.asarray(pcm, dtype=np.float32)


class Voice:
    """Local neural text-to-speech (Piper)."""

    def __init__(self, voice_name: str, models_dir: Path):
        from piper import PiperVoice

        matches = list(Path(models_dir).rglob(f"{voice_name}.onnx"))
        if not matches:
            raise FileNotFoundError(
                f"Voice '{voice_name}' not found. Run: .venv\\Scripts\\python -m livetranslator.download_models"
            )
        # Only espeak needs the short path; the model is opened by Python.
        self.voice = PiperVoice.load(matches[0], espeak_data_dir=_espeak_data_dir())
        self.sample_rate = self.voice.config.sample_rate

    def speak(self, text: str):
        """Yield float32 audio sentence by sentence, so playback starts early."""
        for chunk in self.voice.synthesize(text):
            yield chunk.audio_float_array.astype(np.float32)
