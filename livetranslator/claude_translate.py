"""Translation through Claude (needs internet + ANTHROPIC_API_KEY).

Claude sees the last few lines of the conversation, so it can repair speech
recognition mistakes ("זה דולק" -> "it's on", not "it's fucked") and keep
names and terms consistent. Any failure falls back to the local translator.

Speed (measured with Haiku 5.5, Hebrew phrase -> English):
  JSON-schema output, new connection          ~1.4 s
  plain text, thinking off, kept-alive conn.   ~0.8 s
and the reply is streamed, so the voice can start on the first clause.
Commentary can't reach the voice: generation stops at the first line break
(stop_sequences), and self-corrections always come after one.

Uses the HTTP API directly: Windows Smart App Control on this PC blocks an
unsigned DLL (`jiter`) inside the official `anthropic` Python SDK.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
from collections import deque

_NAMES = {"he": "Hebrew", "en": "English"}
_FILLER_MARK = "-"

SYSTEM = """You are a live simultaneous interpreter inside a video call.
You receive one phrase of {src} at a time. It comes from automatic speech \
recognition, so it can contain misheard words, missing punctuation, or a \
sentence cut in the middle.

{task} Use the earlier lines only as context to understand what was meant \
and to fix obvious recognition errors; translate only the new phrase. Keep \
names, numbers and technical terms exact. Speakers often mix English tech \
words into Hebrew, and the recognizer may spell them as Hebrew sound-alikes: \
"בקן"/"בקנד" = backend, "ריפקטור"/"דריף פקטור" = refactor, "רידיזיין" = \
redesign, "פרילנס" = freelance.

Your reply is spoken aloud to the other side the moment you write it. Reply \
with the translation only, on a single line: never a comment, a correction \
of yourself, an alternative, quotes, or a note about the input. If the \
phrase is an unfinished fragment, translate the fragment as is. If it is \
only a filler sound (uh, um, אה, אממ) or unintelligible, reply with a single \
"-".{glossary}{target_rules}"""

TASK_TRANSLATE = """Translate it into natural, spoken {tgt}, the way a \
professional interpreter would say it out loud: short, clear, conversational."""

# "Polish" mode (its own hotkey): same meaning, better English.
TASK_POLISH = """Render it in fluent, natural, professional {tgt}, the way a \
skilled interpreter would phrase it for this speaker: correct grammar, a \
clear sentence structure and precise word choice, without false starts, \
repetitions or filler words. Keep exactly the speaker's meaning, facts, \
tone and first person. Never add information, claims, numbers, examples or \
opinions the speaker did not say, and do not make the speaker sound more \
experienced or certain than they sounded."""

# Hebrew subtitles: the listener is a woman, and the subtitle window can't
# mix right-to-left and left-to-right text in one line.
HE_TARGET_RULES = """

The Hebrew is read by {listener}. When the other side speaks to her, use \
{form} forms (e.g. {example}). Write English technical terms and names the way \
Israelis write them in Hebrew letters (e.g. דיפלוי, קוברנטיס, מיקרוסרוויסים, \
קוד ריוויו), never in Latin letters, because the subtitle line cannot mix \
directions."""

_CLAUSE_END = re.compile(r"[,;:.!?]\s*$")
_META = re.compile(
    r"\b(the (phrase|sentence|speaker|input|text) (is|says|means)|translation|translated|"
    r"wait,|let me|i think (you|this|it) mean|note:|did you mean|correction|fragment)\b",
    re.IGNORECASE,
)
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _is_commentary(clause: str, source: str, target: str) -> bool:
    """Catch the interpreter talking *about* the phrase instead of translating.

    Seen live: "Outputs" -> 'Kubernetes-ו-NetExt? No, the phrase is: "Outputs"'.
    English target: meta phrases. Hebrew target: English words that were not
    in what they said (names and terms they did say are fine).
    """
    if _META.search(clause):
        return True
    if target == "he":
        said = {w.lower() for w in _LATIN_WORD.findall(source)}
        extra = [w for w in _LATIN_WORD.findall(clause) if w.lower() not in said]
        return len(extra) >= 2
    return False


class ClaudeTranslator:
    def __init__(self, src: str, tgt: str, model: str, glossary: str, fallback, timeout: float = 8.0,
                 listener_gender: str = "female"):
        from . import net

        self.key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.model, self.fallback, self.timeout = model, fallback, timeout
        self.src, self.tgt = src, tgt
        extra = f"\n\nGlossary (always use these): {glossary}" if glossary.strip() else ""
        rules = ""
        if tgt == "he":
            female = listener_gender != "male"
            rules = HE_TARGET_RULES.format(
                listener="a woman" if female else "a man",
                form="feminine" if female else "masculine",
                example="ספרי לי, את" if female else "ספר לי, אתה",
            )
        self.systems = {
            mode: SYSTEM.format(src=_NAMES[src], task=task.format(tgt=_NAMES[tgt]), glossary=extra, target_rules=rules)
            for mode, task in (("translate", TASK_TRANSLATE), ("polish", TASK_POLISH))
        }
        self.polish = False
        self.history: deque[str] = deque(maxlen=5)
        self.failures = 0
        self.retry_at = 0.0
        # Own kept-alive connection per direction, so they never wait on each other.
        self.session = net.Session("api.anthropic.com", timeout)

    def translate(self, text: str) -> str:
        return " ".join(self.translate_stream(text)).strip()

    def translate_stream(self, text: str):
        """Yield the translation clause by clause as Claude writes it."""
        if not text:
            return
        context = "\n".join(self.history)
        self.history.append(text)
        # After repeated errors (offline, blocked, bad key) stop waiting on
        # the network for a while, then try again.
        if self.failures >= 3:
            if time.time() < self.retry_at:
                yield self.fallback.translate(text)
                return
            self.failures = 2
        from . import net
        from .vocabulary import glossary

        terms = glossary()
        prompt = (
            (f"Speaker's own terms (the recognizer may write them in Hebrew letters):\n{terms}\n\n" if terms else "")
            + (f"Earlier lines (context only):\n{context}\n\n" if context else "")
            + f"New phrase:\n{text}"
            # Repeated at the end, where a small fast model follows it best.
            + ("\n\n(Hebrew letters only, also for names and terms. Example: \"I worked on Java and "
               "Hibernate at Matrix IT\" -> \"עבדתי על ג'אווה והייברנייט במטריקס איי-טי\")"
               if self.tgt == "he" else "")
        )
        body = {
            "model": self.model,
            "max_tokens": 1024,
            "stream": True,
            "output_config": {"effort": "low"},
            "system": self.systems["polish" if self.polish else "translate"],
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"content-type": "application/json", "x-api-key": self.key, "anthropic-version": "2026-10-21"}
        if "haiku" in self.model:
            body["thinking"] = {"type": "disabled"}  # allowed on Haiku at low effort; fastest
        else:  # Opus/Sonnet: thinking can't be disabled; add the refusal fallback
            body["fallbacks"] = "default"
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"

        sent_any = False
        try:
            session = self.session
            with session.lock:
                r = session.request("/v1/messages", json.dumps(body).encode("utf-8"), headers)
                buf, refused = "", False
                for clause, done, refusal in _sse_clauses(r):
                    refused = refused or refusal
                    if "\n" in clause:
                        # Only the first line is the translation; anything after
                        # a line break would be commentary. Stop reading here
                        # (the API rejects "\n" as a stop sequence) and drop
                        # the half-read connection.
                        buf += clause.split("\n", 1)[0]
                        if buf.strip() or sent_any:
                            session.conn.close()
                            session.conn = None
                            break
                        continue  # leading blank line: keep going
                    buf += clause
                    if done or (_CLAUSE_END.search(buf) and len(buf.split()) >= 3):
                        piece = buf.strip()
                        buf = ""
                        if piece and piece != _FILLER_MARK:
                            if _is_commentary(piece, text, self.tgt):
                                print(f"[claude] dropped commentary: {piece!r}", flush=True)
                                session.conn.close()
                                session.conn = None
                                buf = ""
                                break
                            sent_any = True
                            yield piece
                piece = buf.strip()
                if piece and piece != _FILLER_MARK:
                    if _is_commentary(piece, text, self.tgt):
                        print(f"[claude] dropped commentary: {piece!r}", flush=True)
                    else:
                        sent_any = True
                        yield piece
            self.failures = 0
            if refused and not sent_any:
                yield self.fallback.translate(text)
        except net.Blocked as e:
            net.report_blocked("Claude", e)
            self.failures, self.retry_at = 3, time.time() + 300
            if not sent_any:
                yield self.fallback.translate(text)
        except urllib.error.HTTPError as e:
            print(f"[claude] HTTP {e.code}: {e.msg}", flush=True)
            self.failures += 1 if e.code in (400, 401, 403, 404) else 0
            if not sent_any:
                yield self.fallback.translate(text)
        except Exception as e:  # offline, timeout
            print(f"[claude] {e}", flush=True)
            self.failures += 1
            self.retry_at = time.time() + 60
            if not sent_any:
                yield self.fallback.translate(text)


def _sse_clauses(response):
    """Yield (text_delta, done, refused) from an SSE Messages stream."""
    event = None
    while True:
        line = response.readline()
        if not line:
            yield "", True, False
            return
        line = line.decode("utf-8").rstrip("\r\n")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data = json.loads(line[5:])
            if event == "content_block_delta" and data.get("delta", {}).get("type") == "text_delta":
                yield data["delta"]["text"], False, False
            elif event == "message_delta":
                if data.get("delta", {}).get("stop_reason") == "refusal":
                    yield "", False, True
            elif event == "message_stop":
                yield "", True, False
                return
            elif event == "error":
                raise RuntimeError(data.get("error", {}).get("message", "stream error"))
