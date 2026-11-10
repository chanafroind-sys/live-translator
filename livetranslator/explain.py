"""Short Hebrew explanations of hard terms in what the other side said.

After a line has been translated (and its subtitle shown / narration
queued), the English line goes to Claude in a separate, later call: it picks
terms this listener probably doesn't know, judged from about_me.txt, and
explains each in a few Hebrew words. It never suggests what to answer.

Runs on its own thread, so it can never delay the translation itself.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from collections import deque

from .config import ROOT

PROFILE = ROOT / "about_me.txt"
PROFILE_EXAMPLE = ROOT / "about_me.example.txt"

SYSTEM = """You help a Hebrew-speaking listener follow a live English \
conversation. You get the line the other side just said (already translated \
for her) and a few earlier lines for context.

Pick the technical terms, jargon, acronyms or idioms in the NEW line that \
this listener probably does not know, judging by her profile below. For each, \
explain in Hebrew, in at most 12 words, what it means in this context. \
Usually there are none or one; never more than three. Do not explain words \
she obviously knows, do not translate the sentence again, and never suggest \
what she should answer, do or think: only what the words mean.

Reply with one term per line as: <term as said in English>|<the term in Hebrew letters, as Israelis write it>|<Hebrew explanation>
If nothing needs explaining, reply with a single "-".

Listener profile:
{profile}"""


def _profile() -> str:
    if not PROFILE.exists() and PROFILE_EXAMPLE.exists():
        PROFILE.write_text(PROFILE_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    if not PROFILE.exists():
        return "(no profile given: explain only clearly specialised terms)"
    lines = [l for l in PROFILE.read_text(encoding="utf-8-sig").splitlines() if l.strip() and not l.startswith("#")]
    return "\n".join(lines)[:2000]


class Explainer:
    def __init__(self, model: str, on_explanation, timeout: float = 10.0):
        from . import net

        self.key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self.on_explanation = on_explanation  # callback(list[(term, term_in_hebrew, explanation)])
        self.session = net.Session("api.anthropic.com", timeout)
        self.on = threading.Event()
        self.lines: "queue.Queue[str]" = queue.Queue()
        self.history: deque[str] = deque(maxlen=4)
        threading.Thread(target=self._loop, daemon=True, name="explain").start()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def toggle(self) -> bool:
        if self.on.is_set():
            self.on.clear()
        else:
            self.on.set()
        return self.on.is_set()

    def submit(self, english: str) -> None:
        if self.on.is_set() and self.key and english:
            self.lines.put(english)

    def _loop(self) -> None:
        while True:
            line = self.lines.get()
            context = "\n".join(self.history)
            self.history.append(line)
            try:
                items = self._ask(line, context)
            except Exception as e:
                print(f"[explain] {e}", flush=True)
                continue
            if items:
                print(f"[explain] {items}", flush=True)
                self.on_explanation(items)

    def _ask(self, line: str, context: str) -> list[tuple[str, str, str]]:
        body = {
            "model": self.model,
            "max_tokens": 400,
            "output_config": {"effort": "low"},
            "system": SYSTEM.format(profile=_profile()),
            "messages": [{"role": "user", "content":
                          (f"Earlier lines:\n{context}\n\n" if context else "") + f"New line:\n{line}"}],
        }
        if "haiku" in self.model:
            body["thinking"] = {"type": "disabled"}
        headers = {"content-type": "application/json", "x-api-key": self.key, "anthropic-version": "2026-10-21"}
        with self.session.lock:
            r = self.session.request("/v1/messages", json.dumps(body).encode("utf-8"), headers)
            data = json.loads(r.read().decode("utf-8"))
        if data.get("stop_reason") == "refusal":
            return []
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        items = []
        for row in text.splitlines():
            parts = [p.strip() for p in row.split("|")]
            if len(parts) == 3 and all(parts):
                items.append(tuple(parts))
            elif len(parts) == 2 and all(parts):  # model skipped the Hebrew spelling
                items.append((parts[0], parts[0], parts[1]))
        return items[:3]
