"""Settings. Defaults live here; override any of them in `config.json`."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
CONFIG_FILE = ROOT / "config.json"


@dataclass
class Settings:
    # Speech recognition (faster-whisper). "base.en" is fast and light;
    # "small.en" is more accurate on accents/noise but ~5x slower.
    whisper_model: str = "base.en"
    cpu_threads: int = 4

    # English -> Hebrew translation model (Argos / CTranslate2 format).
    translation_model_dir: str = str(MODELS_DIR / "en_he" / "translate-en_he-1_5")

    # Segmenting speech: a phrase ends after this much silence...
    silence_end_sec: float = 0.5
    # ...or is cut at this length so subtitles never lag too far behind.
    max_phrase_sec: float = 6.0
    # Loudness (RMS, 0..1) above which audio counts as speech.
    speech_threshold: float = 0.006

    # Subtitle window
    font_size: int = 24
    show_original: bool = True
    window_opacity: float = 0.88
    history_lines: int = 2

    # Global on/off hotkey (needs the `keyboard` package).
    hotkey: str = "ctrl+alt+t"


def load_settings() -> Settings:
    s = Settings()
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        names = {f.name for f in fields(Settings)}
        for k, v in data.items():
            if k in names:
                setattr(s, k, v)
    return s


def write_example_config(path: Path) -> None:
    path.write_text(json.dumps(asdict(Settings()), indent=2, ensure_ascii=False), encoding="utf-8")
