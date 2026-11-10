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
terms exact. If the phrase is an unfinished fragment, translate the fragment \
as is. Output only the translation, with no quotes, notes or explanations.{glossary}"""


class ClaudeTranslator:
    def __init__(self, src: str, tgt: str, model: str, glossary: str, fallback, timeout: float = 8.0):
        self.key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.model, self.fallback, self.timeout = model, fallback, timeout
        extra = f"\n\nGlossary (always use these): {glossary}" if glossary.strip() else ""
        self.system = SYSTEM.format(src=_NAMES[src], tgt=_NAMES[tgt], glossary=extra)
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
        prompt = (f"Earlier lines (context only):\n{context}\n\n" if context else "") + f"New phrase:\n{text}"
        body = {
            "model": self.model,
            "max_tokens": 2000,
            "output_config": {"effort": "low"},  # short phrases: speed matters most
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
        out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        return out or self.fallback.translate(text)
