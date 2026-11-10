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
    # "local" = everything on this PC (offline, free, slower).
    # "azure" = Azure AI Speech (internet, ~1-2 s delay, free tier 5 h/month).
    engine: str = "local"
    # Prefer environment variables AZURE_SPEECH_KEY / AZURE_SPEECH_REGION.
    # If you put them here, keep them in config.json only (git ignores it).
    azure_key: str = ""
    azure_region: str = ""
    # Very natural female voice; also try en-US-JennyNeural, en-US-AriaNeural.
    azure_voice: str = "en-US-AvaMultilingualNeural"

    # Translate with Claude when ANTHROPIC_API_KEY is set (internet). Much
    # better than the local model and fixes speech-recognition slips; falls
    # back to local automatically when offline.
    claude_translation: bool = True
    # Offline translator: "nllb" (much better, 620 MB) or "argos" (130 MB).
    local_translator: str = "nllb"
    claude_model: str = "claude-opus-5-5"
    # Names/terms to always translate the same way, e.g. "Deel = Deel; מוצר = product".
    glossary: str = ""

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
    # Show / hide the subtitles window. While hidden, subtitles are paused
    # (nothing is transcribed) and the app lives as an icon by the clock.
    window_hotkey: str = "ctrl+alt+s"
    start_hidden: bool = True

    # ---- Speaking direction: your Hebrew -> English voice --------------
    # Hebrew needs the "small" model; "base" garbles Hebrew.
    speak_whisper_model: str = "small"
    he_en_model_dir: str = str(MODELS_DIR / "he_en" / "translate-he_en-1_5")
    # English voice. "kokoro" sounds most natural; "piper" is lighter/faster.
    tts_engine: str = "kokoro"
    # Kokoro female voices: af_heart (default), af_bella, af_sarah, af_nicole,
    # bf_emma (British). Samples: samples/voice_kokoro_*.wav
    kokoro_voice: str = "af_heart"
    kokoro_speed: float = 1.0
    # Piper voice name; files live in models/piper/.
    piper_voice: str = "en_US-hfc_female-medium"
    # Where the English voice goes. Install VB-CABLE and keep "CABLE Input";
    # then pick "CABLE Output" as the microphone in Zoom/Meet/etc.
    # If not found, falls back to your speakers (handy for testing).
    speak_output_device: str = "CABLE Input"
    # Your real microphone ("" = Windows default, skipping virtual cables).
    mic_device: str = ""
    # How loud your own Hebrew voice is under the translation (0 = silent).
    # When translation is off, your voice passes through at full volume.
    original_voice_gain: float = 0.15
    mic_speech_threshold: float = 0.012
    speak_on_at_start: bool = False
    # While the app runs, make "CABLE Output" the Windows default mic so every
    # app gets it automatically; put your previous mic back on exit.
    auto_default_mic: bool = True
    speak_hotkey: str = "ctrl+alt+y"


def load_settings() -> Settings:
    s = Settings()
    if CONFIG_FILE.exists():
        # utf-8-sig: Notepad/PowerShell often save JSON with a BOM.
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        names = {f.name for f in fields(Settings)}
        for k, v in data.items():
            if k in names:
                setattr(s, k, v)
    return s


def write_example_config(path: Path) -> None:
    path.write_text(json.dumps(asdict(Settings()), indent=2, ensure_ascii=False), encoding="utf-8")


def save_setting(name: str, value) -> None:
    """Change one value in config.json, keeping everything else."""
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")) if CONFIG_FILE.exists() else {}
    data[name] = value
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
