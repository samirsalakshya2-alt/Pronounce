"""Deterministic article segmentation (M12).

An article is split into paragraphs (blank lines), then sentences, then — only
for sentences longer than the analysis limit (MAX_TEXT_CHARS) — clauses. Every
segment records its exact character span in the article text, so the article
can always be reconstructed and a recording always points at the exact words
it belongs to. The same text and SEGMENTER_VERSION always give the same
segments.

Segment text is the span with internal whitespace collapsed; it is the target
text the recording is analysed against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SEGMENTER_VERSION = "seg-1"
MAX_SEGMENT_CHARS = 300  # = app.service.MAX_TEXT_CHARS (one analysis)
MAX_ARTICLE_CHARS = 50_000

_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_WS = re.compile(r"\s+")
_PARAGRAPH = re.compile(r"\n[ \t]*\n\s*")
# a sentence ends at . ! ? … (possibly repeated), optionally followed by closing quotes/brackets
_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s)")
ABBREVIATIONS = frozenset("""mr mrs ms dr prof st sr jr vs etc inc ltd co corp no fig e.g i.e a.m p.m u.s u.k approx
    dept est govt mt jan feb mar apr jun jul aug sep sept oct nov dec""".split())
_CLAUSE = re.compile(r"[;:,—–]\s")


@dataclass(frozen=True)
class SegmentSpan:
    index: int
    paragraph_index: int
    char_start: int
    char_end: int
    text: str


def collapse(text: str) -> str:
    return _WS.sub(" ", text).strip()


def has_letter(text: str) -> bool:
    return bool(_LETTER.search(text))


def _paragraphs(text: str) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for m in _PARAGRAPH.finditer(text):
        spans.append((pos, m.start()))
        pos = m.end()
    spans.append((pos, len(text)))
    out = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if s < e:
            out.append((s, e))
    return out


def _is_abbreviation(text: str, end: int) -> bool:
    """`end` is just after the terminal punctuation; is the preceding token an abbreviation or initial?"""
    if text[end - 1] != ".":
        return False
    start = end - 1
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    token = text[start:end - 1].strip("\"'“‘([").lower()
    if token in ABBREVIATIONS:
        return True
    return len(token) == 1 and token.isalpha()  # an initial: "J. K. Rowling"


def _sentences(text: str, start: int, end: int) -> list[tuple[int, int]]:
    out, s = [], start
    for m in _END.finditer(text, start, end):
        e = m.end()
        if _is_abbreviation(text, m.start() + 1 if text[m.start()] == "." else e):
            continue
        # the next sentence must start with an upper-case letter, digit or opening quote
        nxt = e
        while nxt < end and text[nxt].isspace():
            nxt += 1
        if nxt < end and not (text[nxt].isupper() or text[nxt].isdigit() or text[nxt] in "\"'“‘(["):
            continue
        out.append((s, e))
        s = nxt
    if s < end:
        out.append((s, end))
    return out


def _split_long(text: str, s: int, e: int) -> list[tuple[int, int]]:
    """Split a span whose collapsed text exceeds the limit: at the last clause mark, else the last space."""
    parts = []
    while len(collapse(text[s:e])) > MAX_SEGMENT_CHARS:
        # furthest end position whose collapsed length fits (one pass; runs of whitespace count once)
        window_end, count, prev_ws = s, 0, False
        for i in range(s, e):
            ws = text[i].isspace()
            if not (ws and prev_ws):
                count += 1
            prev_ws = ws
            if count > MAX_SEGMENT_CHARS:
                break
            window_end = i + 1
        cut = None
        for m in _CLAUSE.finditer(text, s, window_end):
            if has_letter(text[s:m.start() + 1]):
                cut = m.start() + 1
        if cut is None:
            sp = text.rfind(" ", s, window_end)
            cut = sp if sp > s else window_end
        parts.append((s, cut))
        s = cut
        while s < e and text[s].isspace():
            s += 1
    if s < e:
        parts.append((s, e))
    return parts


def segment(text: str) -> list[SegmentSpan]:
    """Segments in reading order. Raises ValueError for empty or oversized articles."""
    if len(text) > MAX_ARTICLE_CHARS:
        raise ValueError(f"article longer than {MAX_ARTICLE_CHARS} characters")
    spans: list[tuple[int, int, int]] = []
    for pi, (ps, pe) in enumerate(_paragraphs(text)):
        para: list[tuple[int, int]] = []
        for s, e in _sentences(text, ps, pe):
            para.extend(_split_long(text, s, e))
        # A piece without letters (e.g. a list number "2." or "—") is merged into the
        # following piece (or, at the end of a paragraph, the previous one) — never dropped.
        merged: list[tuple[int, int]] = []
        pending: int | None = None
        for s, e in para:
            if pending is not None and len(collapse(text[pending:e])) <= MAX_SEGMENT_CHARS:
                s, pending = pending, None
            elif pending is not None:
                merged.append((pending, s))
                pending = None
            if not has_letter(text[s:e]):
                pending = s
                continue
            merged.append((s, e))
        if pending is not None:
            last_end = para[-1][1]
            if merged and len(collapse(text[merged[-1][0]:last_end])) <= MAX_SEGMENT_CHARS:
                merged[-1] = (merged[-1][0], last_end)
            else:
                merged.append((pending, last_end))  # kept as its own (non-readable) segment
        spans.extend((pi, s, e) for s, e in merged)
    out = [SegmentSpan(i, pi, s, e, collapse(text[s:e])) for i, (pi, s, e) in enumerate(spans)]
    if not any(has_letter(x.text) for x in out):
        raise ValueError("article contains no words")
    return out
