"""Your own terms: English words you mix into Hebrew, names, companies.

One entry per line in vocabulary.txt (next to start.bat). Two forms:

    Kubernetes                    an English term you say as-is
    מטריקס גלובל = Matrix Global   how something you say should come out

Used twice: as a spelling hint for speech recognition (so "דיפלוי" comes
back as "deploy") and as a glossary for Claude's translation. The file is
re-read automatically when it changes; no restart needed.
"""
from __future__ import annotations

from pathlib import Path

from .config import ROOT

FILE = ROOT / "vocabulary.txt"
EXAMPLE = ROOT / "vocabulary.example.txt"

_cache: dict = {"mtime": None, "terms": [], "pairs": []}


def _load() -> None:
    if not FILE.exists() and EXAMPLE.exists():
        FILE.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    mtime = FILE.stat().st_mtime if FILE.exists() else None
    if mtime == _cache["mtime"]:
        return
    terms, pairs = [], []
    if FILE.exists():
        for line in FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if "=" in line:
                said, written = (p.strip() for p in line.split("=", 1))
                if said and written:
                    pairs.append((said, written))
                    terms.append(written)
            else:
                terms.append(line)
    _cache.update(mtime=mtime, terms=terms, pairs=pairs)


def asr_hint(max_chars: int = 500) -> str:
    """Comma-separated terms for Whisper's prompt (it allows ~224 tokens)."""
    _load()
    out = ""
    for t in _cache["terms"]:
        if len(out) + len(t) + 2 > max_chars:
            break
        out += t + ", "
    return out.strip(", ") + ". " if out else ""


def glossary() -> str:
    """Glossary text for the translation prompt."""
    _load()
    lines = [f"- {said} -> {written}" for said, written in _cache["pairs"]]
    plain = [t for t in _cache["terms"] if t not in {w for _, w in _cache["pairs"]}]
    if plain:
        lines.append("- Keep these English terms exactly as written: " + ", ".join(plain))
    return "\n".join(lines)
