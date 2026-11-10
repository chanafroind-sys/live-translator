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
    def __init__(self, model_name: str, cpu_threads: int):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=cpu_threads)
        self.context = ""

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language="en",
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
