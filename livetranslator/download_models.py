"""Download the local models (one time, ~280 MB). Run:

    .venv\\Scripts\\python -m livetranslator.download_models
"""
from __future__ import annotations

import io
import os
import urllib.request
import zipfile

from .config import MODELS_DIR, load_settings

ARGOS_EN_HE = "https://argos-net.com/v1/translate-en_he-1_5.argosmodel"


def main() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    s = load_settings()

    target = MODELS_DIR / "en_he"
    if not (target / "translate-en_he-1_5" / "model" / "model.bin").exists():
        print("Downloading English->Hebrew translation model (~130 MB)...")
        data = urllib.request.urlopen(ARGOS_EN_HE).read()
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():  # extract safely inside target only
                dest = (target / name).resolve()
                if not str(dest).startswith(str(target.resolve())):
                    raise RuntimeError(f"unsafe path in archive: {name}")
            z.extractall(target)
    print("Translation model: OK")

    print(f"Downloading speech model '{s.whisper_model}' (if needed)...")
    from faster_whisper import WhisperModel

    WhisperModel(s.whisper_model, device="cpu", compute_type="int8")
    print("Speech model: OK")


if __name__ == "__main__":
    main()
