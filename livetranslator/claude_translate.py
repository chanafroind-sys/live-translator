"""Translation through Claude (needs internet + ANTHROPIC_API_KEY).

Claude sees the last few lines of the conversation, so it can repair speech
recognition mistakes ("זה דולק" -> "it's on", not "it's fucked") and keep
names and terms consistent. Any failure falls back to the local translator.

Uses the HTTP API directly: Windows Smart App Control on this PC blocks an
unsigned DLL (`jiter`) inside the official `anthropic` Python SDK.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections import deque

API_URL = "https://api.anthropic.com/v1/messages"
_NAMES = {"he": "Hebrew", "en": "English"}

SYSTEM = """You are a live simultaneous interpreter inside a video call.
You receive one phrase of {src} at a time. It comes from automatic speech \
recognition, so it can contain misheard words, missing punctuation, or a \
sentence cut in the middle.

Translate it into natural, spoken {tgt}, the way a professional interpreter \
would say it out loud: short, clear, conversational. Use the earlier lines \
only as context to understand what was meant and to fix obvious recognition \
errors; translate only the new phrase. Keep names, numbers and technical \
terms exact. Speakers often mix English tech words into Hebrew, and the \
recognizer may spell them as Hebrew sound-alikes: "בקן"/"בקנד" = backend, \
"ריפקטור"/"דריף פקטור" = refactor, "רידיזיין" = redesign, "פרילנס" = freelance.

Whatever you write is spoken aloud to the other side immediately, so the \
translation field must contain only words the speaker said: never a comment, \
a correction of yourself, an alternative, or a note about the input. If the \
phrase is an unfinished fragment, translate the fragment as is. If it is \
only a filler sound (uh, um, אה, אממ) or unintelligible, return an empty \
translation.{glossary}{target_rules}"""

# Hebrew subtitles: the listener is a woman, and the subtitle window can't
# mix right-to-left and left-to-right text in one line.
HE_TARGET_RULES = """

The Hebrew is read by {listener}. When the other side speaks to her, use \
{form} forms (e.g. {example}). Write English technical terms and names the way \
Israelis write them in Hebrew letters (e.g. דיפלוי, קוברנטיס, מיקרוסרוויסים, \
קוד ריוויו), never in Latin letters, because the subtitle line cannot mix \
directions."""

_SCHEMA = {
    "type": "object",
    "properties": {"translation": {"type": "string"}},
    "required": ["translation"],
    "additionalProperties": False,
}


class ClaudeTranslator:
    def __init__(self, src: str, tgt: str, model: str, glossary: str, fallback, timeout: float = 8.0,
                 listener_gender: str = "female"):
        self.key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.model, self.fallback, self.timeout = model, fallback, timeout
        extra = f"\n\nGlossary (always use these): {glossary}" if glossary.strip() else ""
        rules = ""
        if tgt == "he":
            female = listener_gender != "male"
            rules = HE_TARGET_RULES.format(
                listener="a woman" if female else "a man",
                form="feminine" if female else "masculine",
                example="ספרי לי, את" if female else "ספר לי, אתה",
            )
        self.system = SYSTEM.format(src=_NAMES[src], tgt=_NAMES[tgt], glossary=extra, target_rules=rules)
        self.history: deque[str] = deque(maxlen=5)
        self.failures = 0

    def translate(self, text: str) -> str:
        if not text:
            return ""
        context = "\n".join(self.history)
        self.history.append(text)
        # After repeated errors (offline, bad key) stop waiting on the network.
        if self.failures >= 3:
            return self.fallback.translate(text)
        from .vocabulary import glossary

        terms = glossary()
        prompt = (
            (f"Speaker's own terms (the recognizer may write them in Hebrew letters):\n{terms}\n\n" if terms else "")
            + (f"Earlier lines (context only):\n{context}\n\n" if context else "")
            + f"New phrase:\n{text}"
        )
        body = {
            "model": self.model,
            "max_tokens": 2000,
            # Short phrases: speed matters most. A fixed JSON shape keeps any
            # commentary out of what gets spoken.
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
            "fallbacks": "default",  # on a policy decline, retry on another model
            "system": self.system,
            "messages": [{"role": "user", "content": prompt}],
        }
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.key,
                "anthropic-version": "2026-10-21",
                "anthropic-beta": "server-side-fallback-2026-07-01",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            print(f"[claude] HTTP {e.code}: {detail}", flush=True)
            self.failures += 1 if e.code in (400, 401, 403, 404) else 0
            return self.fallback.translate(text)
        except Exception as e:  # offline, timeout
            print(f"[claude] {e}", flush=True)
            self.failures += 1
            return self.fallback.translate(text)

        self.failures = 0
        if data.get("stop_reason") == "refusal":
            return self.fallback.translate(text)
        raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        try:
            return json.loads(raw)["translation"].strip()  # "" = filler: say nothing
        except (ValueError, KeyError, TypeError):
            print(f"[claude] unexpected output: {raw[:120]!r}", flush=True)
            return self.fallback.translate(text)
