"""Quarantine for external content (job posts, web pages, emails).

External text is DATA, never instructions. It is length-limited, stripped of markup,
scanned for prompt-injection patterns and stored with its source and retrieval time.
Nothing in APEX concatenates external text into instructions for an action, and
agents cannot trigger external actions — so a flagged page can at worst mislead an
analysis, which is why flags are shown to the user next to the item.
"""
from __future__ import annotations

import html
import re

MAX_LEN = 20_000
_TAGS = re.compile(r"<[^>]+>")
_PATTERNS = [
    (re.compile(r"ignore (all |any )?(previous|prior|above) (instructions|prompts)", re.I), "instruction override"),
    (re.compile(r"\b(system|developer) prompt\b", re.I), "prompt reference"),
    (re.compile(r"you are (now )?(an?|the) (ai|assistant|agent)", re.I), "role reassignment"),
    (re.compile(r"\b(send|email|forward|transfer|wire|pay)\b.{0,40}\b(to|money|funds|password|credentials)\b", re.I),
     "action request"),
    (re.compile(r"\b(api[_ -]?key|password|token|secret)\b", re.I), "credential mention"),
    (re.compile(r"(do not|don't) (tell|inform|show) the user", re.I), "concealment"),
    (re.compile(r"[​-‏⁠﻿]"), "hidden characters"),
]


def sanitize(text: str | None) -> tuple[str, list[str]]:
    if not text:
        return "", []
    flags = sorted({label for rx, label in _PATTERNS if rx.search(text)})
    clean = _TAGS.sub(" ", text)
    clean = html.unescape(clean)
    clean = re.sub(r"[​-‏⁠﻿]", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()[:MAX_LEN]
    return clean, flags
