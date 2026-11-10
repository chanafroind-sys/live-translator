"""Download the local models (one time, ~900 MB). Run:

    .venv\\Scripts\\python -m livetranslator.download_models
"""
from __future__ import annotations

import io
import os
import urllib.request
import zipfile
from pathlib import Path

from .config import MODELS_DIR, load_settings

UA = {"User-Agent": "Mozilla/5.0 live-translator"}  # server rejects Python's default (HTTP 403)
ARGOS = "https://argos-net.com/v1/translate-{pair}-1_5.argosmodel"
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def _get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA)).read()


def _argos(pair: str) -> None:
    target = MODELS_DIR / pair
    if (target / f"translate-{pair}-1_5" / "model" / "model.bin").exists():
        print(f"Translation model {pair}: OK")
        return
    print(f"Downloading translation model {pair} (~130 MB)...")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(_get(ARGOS.format(pair=pair)))) as z:
        for name in z.namelist():  # extract safely inside target only
            if not str((target / name).resolve()).startswith(str(target.resolve())):
                raise RuntimeError(f"unsafe path in archive: {name}")
        z.extractall(target)
    print(f"Translation model {pair}: OK")


def _file(url: str, dest: Path) -> None:
    if dest.exists():
        return
    print(f"Downloading {dest.name}...")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_get(url))


def main() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    s = load_settings()

    _argos("en_he")  # subtitles: English -> Hebrew (light fallback)
    _argos("he_en")  # speaking:  Hebrew -> English (light fallback)

    from huggingface_hub import snapshot_download

    print("Translation model NLLB-200 600M (~620 MB, downloads if needed)...")
    snapshot_download("JustFrederik/nllb-200-distilled-600M-ct2-int8", local_dir=str(MODELS_DIR / "nllb-600m"))
    print("Translation model NLLB: OK")

    from faster_whisper import WhisperModel

    for name in {s.whisper_model, s.speak_whisper_model}:
        print(f"Speech model '{name}' (downloads if needed)...")
        WhisperModel(name, device="cpu", compute_type="int8")
    print("Speech models: OK")

    # English voices
    _file(KOKORO + "kokoro-v1.0.int8.onnx", MODELS_DIR / "kokoro" / "kokoro-v1.0.int8.onnx")
    _file(KOKORO + "voices-v1.0.bin", MODELS_DIR / "kokoro" / "voices-v1.0.bin")
    from huggingface_hub import hf_hub_download

    v = s.piper_voice  # e.g. en_US-hfc_female-medium
    lang, name, quality = v.split("-")
    base = f"{lang.split('_')[0]}/{lang}/{name}/{quality}/{v}"
    for ext in (".onnx", ".onnx.json"):
        hf_hub_download("rhasspy/piper-voices", base + ext, local_dir=str(MODELS_DIR / "piper"))
    print("Voices: OK")


if __name__ == "__main__":
    main()
