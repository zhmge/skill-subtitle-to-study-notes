#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize a subtitle/transcript file into a segmented, order-preserving transcript.

This script is the DETERMINISTIC layer of the `subtitle-to-study-notes` skill.
It does mechanical work only:

  * detect the encoding (BOM / utf-8 / gb18030 / fallback)
  * detect the format by content signature (SRT / VTT / ASS-SSA / LRC / plain text)
  * normalize every format into a single cue sequence {index, start, end, text}
  * drop exact-duplicate and rolled-up caption cues
  * merge fragmentary cues into paragraphs WITHOUT changing their order
  * surface ASR term candidates (evidence only -- never auto-correct)
  * surface speaker stress signals and definition sentences (evidence only)
  * build Bilibili jump-to-timestamp links when a BV/av id is recognisable

It deliberately does NOT: rewrite prose, summarize, reorder, decide what is
important, guess missing on-screen content, or replace terminology. Those
require semantic judgement and belong to the model, not to a regex.

Usage:
    python parse_subtitles.py <file> [options]

Options:
    --json              emit structured JSON instead of the text transcript
    --gap SECONDS       pause that forces a paragraph break (default 0.9)
    --max-chars N       soft cap on paragraph length in characters (default 140)
    --soft-min N        min paragraph length before a soft break may trigger (default 40)
    --drop-filler       remove cues made up only of filler words
    --no-dedupe         keep exact-duplicate / rolled-up cues
    --no-suspicious     skip ASR term candidate discovery (faster)
    --no-keys           skip stress-signal / definition sentence discovery
    --top N             max suspicious clusters to report (default 20)
    --video-id ID       override the auto-detected BV/av id for jump links
    --no-links          do not build jump links even when an id is found
    --quiet             text mode: print only the paragraph transcript
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

try:  # optional accelerator for pinyin-based clustering
    from pypinyin import lazy_pinyin

    _HAS_PYPINYIN = True
except Exception:  # pragma: no cover - optional dependency
    lazy_pinyin = None
    _HAS_PYPINYIN = False


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)


def decode_bytes(raw):
    """Return (text, encoding_name, warning_or_None)."""
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            try:
                return raw.decode(enc), enc, None
            except UnicodeDecodeError:
                break
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    text = raw.decode("utf-8", errors="replace")
    return text, "utf-8(replace)", "could not decode cleanly; used utf-8 with replacement"


# --------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------

def parse_timestamp(value):
    """Parse SRT/VTT/ASS style timestamps into seconds."""
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
        elif len(parts) == 2:
            h, (m, s) = "0", parts
        else:
            return None
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return None


def format_timestamp(seconds):
    if seconds is None:
        return "--:--:--.---"
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return "%02d:%02d:%06.3f" % (h, m, s)


# --------------------------------------------------------------------------
# Platform deep links
# --------------------------------------------------------------------------

_RE_BV = re.compile(r"BV[0-9A-Za-z]{10}")
_RE_AV = re.compile(r"av(\d{1,12})", re.IGNORECASE)


def detect_video_ref(source):
    """Find a Bilibili BV/av id near the input, for deep links.

    Mechanical only: returns the id exactly as written. It does NOT verify the
    id belongs to the video being noted -- the model must confirm that before
    emitting links, otherwise every timestamp points somewhere wrong.
    """
    haystack = str(source)
    m = _RE_BV.search(haystack)
    if m:
        return {"platform": "bilibili", "video_id": m.group(0)}
    m = _RE_AV.search(haystack)
    if m:
        return {"platform": "bilibili", "video_id": "av" + m.group(1)}
    return None


def deeplink(video_ref, seconds):
    """Jump-to-timestamp URL, or None when it cannot be built."""
    if not video_ref or seconds is None:
        return None
    return "https://www.bilibili.com/video/%s?t=%.1f" % (
        video_ref["video_id"], max(0.0, float(seconds)),
    )


# --------------------------------------------------------------------------
# Format detection (by content signature, never by extension)
# --------------------------------------------------------------------------

_RE_SRT_TIME = re.compile(
    r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}"
)
_RE_SRT_COMMA = re.compile(
    r"\d{1,2}:\d{2}:\d{2},\d{1,3}\s*-->"
)
_RE_VTT_TIME = re.compile(
    r"(?:\d{1,2}:)?\d{2}:\d{2}\.\d{1,3}\s*-->"
)
_RE_LRC_LINE = re.compile(r"^\s*(?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\])+\s*\S")
_RE_ASS_DIALOGUE = re.compile(r"^\s*Dialogue\s*:", re.IGNORECASE)
_RE_CUE_ID = re.compile(r"^\s*(?:cue\d+|\d+)\s*$", re.IGNORECASE)


def detect_format(text):
    lines = [ln for ln in text.splitlines()]
    head = "\n".join(lines[:40])

    if lines and lines[0].lstrip("\ufeff").strip().upper().startswith("WEBVTT"):
        return "vtt"
    if re.search(r"^\s*\[(?:Script Info|Events|V4\+? Styles)\]\s*$", head, re.MULTILINE):
        return "ass"
    if any(_RE_ASS_DIALOGUE.match(ln) for ln in lines):
        return "ass"

    lrc_lines = sum(1 for ln in lines if _RE_LRC_LINE.match(ln))
    if lrc_lines >= 3:
        return "lrc"

    if _RE_SRT_COMMA.search(text):
        return "srt"
    if _RE_SRT_TIME.search(text):
        return "srt"
    return "plain"


# --------------------------------------------------------------------------
# Text cleaning (mechanical only)
# --------------------------------------------------------------------------

_RE_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")
_RE_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_RE_VTT_TAG = re.compile(r"<[^>]+>")
_RE_MULTISPACE = re.compile(r"[ \t\u3000]+")

_FILLER_CORE = {
    "哈", "哈哈", "哎", "唉", "诶", "嗯", "唔", "呃", "哦", "噢", "啊",
    "吧", "呀", "呐", "ok", "OK", "好", "那么", "然后", "就是说",
}


def clean_text(raw):
    """Strip markup and normalize spacing. Keeps wording untouched."""
    text = raw.replace("\ufeff", "")
    if _RE_ASS_OVERRIDE.search(text):
        text = _RE_ASS_OVERRIDE.sub("", text)
    text = _RE_HTML_TAG.sub("", text)
    text = _RE_VTT_TAG.sub("", text)
    text = text.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
    text = text.replace("\r", " ").replace("\n", " ")
    # fullwidth digits / latin -> halfwidth (keep CJK punctuation as-is)
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19 or 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A:
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            out.append(" ")
        else:
            out.append(ch)
    text = "".join(out)
    text = _RE_MULTISPACE.sub(" ", text).strip()
    return text


def is_filler(text):
    core = re.sub(r"[^\w\u4e00-\u9fff]+", "", text).lower()
    return len(core) <= 4 and core in {f.lower() for f in _FILLER_CORE}


# --------------------------------------------------------------------------
# Parsers -> list of cues
# --------------------------------------------------------------------------

def _cue(index, start, end, text):
    return {"index": index, "start": start, "end": end, "text": text}


def parse_srt(text):
    cues = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    auto = 0
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        idx = None
        if _RE_CUE_ID.match(lines[0]):
            digits = re.sub(r"\D", "", lines[0])
            idx = int(digits) if digits else None
            lines = lines[1:]
        if not lines:
            continue
        m = _RE_SRT_TIME.search(lines[0])
        if not m:
            continue
        pieces = lines[0].split("-->")
        start = parse_timestamp(pieces[0])
        end = parse_timestamp(pieces[1].split()[0]) if len(pieces) > 1 else None
        body = " ".join(lines[1:])
        auto += 1
        cues.append(_cue(idx if idx is not None else auto, start, end, body))
    return cues


def parse_vtt(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues = []
    auto = 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            pieces = line.split("-->")
            start = parse_timestamp(pieces[0])
            end = parse_timestamp(pieces[1].split()[0]) if len(pieces) > 1 else None
            body = []
            i += 1
            while i < len(lines) and lines[i].strip():
                body.append(lines[i])
                i += 1
            auto += 1
            cues.append(_cue(auto, start, end, " ".join(body)))
        else:
            i += 1
    return cues


def parse_ass(text):
    cues = []
    auto = 0
    in_events = False
    fmt = None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("[events]"):
            in_events = True
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_events = False
            continue
        if not in_events:
            continue
        if stripped.lower().startswith("format:"):
            fmt = [p.strip().lower() for p in stripped.split(":", 1)[1].split(",")]
            continue
        if not _RE_ASS_DIALOGUE.match(stripped):
            continue
        payload = stripped.split(":", 1)[1]
        # Text is the last field and may itself contain commas -> split(9) at most
        parts = payload.split(",", 9)
        if len(parts) < 10:
            continue
        try:
            start = parse_timestamp(parts[1])
            end = parse_timestamp(parts[2])
        except Exception:
            start = end = None
        body = parts[9]
        auto += 1
        cues.append(_cue(auto, start, end, body))
    return cues


def parse_lrc(text):
    cues = []
    auto = 0
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        tags = re.findall(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]", line)
        if not tags:
            continue
        body = re.sub(r"\[[^\]]*\]", "", line).strip()
        for mm, ss, frac in tags:
            start = int(mm) * 60 + int(ss)
            if frac:
                start += float("0." + frac)
            auto += 1
            cues.append(_cue(auto, start, None, body))
    return cues


def parse_plain(text):
    cues = []
    auto = 0
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip():
            auto += 1
            cues.append(_cue(auto, None, None, line))
    return cues


_PARSERS = {
    "srt": parse_srt,
    "vtt": parse_vtt,
    "ass": parse_ass,
    "lrc": parse_lrc,
    "plain": parse_plain,
}


# --------------------------------------------------------------------------
# Cue post-processing: clean, dedupe, unroll
# --------------------------------------------------------------------------

def normalize_cues(raw_cues, drop_filler=False, dedupe=True):
    cues = []
    for c in raw_cues:
        text = clean_text(c["text"])
        if not text:
            continue
        if drop_filler and is_filler(text):
            continue
        cues.append({**c, "text": text})

    if not dedupe:
        for i, c in enumerate(cues, 1):
            c["index"] = i
        return cues

    out = []
    for c in cues:
        if out:
            prev = out[-1]
            if c["text"] == prev["text"]:
                if c["end"] is not None:
                    prev["end"] = c["end"]
                continue
            # rolled-up caption: current fully contains the previous one
            if c["text"].startswith(prev["text"]) and len(prev["text"]) >= 2:
                prev["text"] = c["text"]
                if c["end"] is not None:
                    prev["end"] = c["end"]
                continue
        out.append(dict(c))

    for i, c in enumerate(out, 1):
        c["index"] = i
    return out


# --------------------------------------------------------------------------
# Paragraph segmentation (order preserving)
# --------------------------------------------------------------------------

_TERMINAL = "。！？!?…；;"
_TOPIC_MARKERS = (
    "那", "那么", "那咱们", "接下来", "接下来咱们", "下面", "下面咱们",
    "然后", "首先", "其次", "再次", "另外", "但是", "不过", "所以",
    "此时", "现在", "这里", "ok", "好", "咱们接下来", "咱再", "再看",
)


def _starts_topic(text):
    low = text.lower()
    for marker in _TOPIC_MARKERS:
        if low.startswith(marker):
            return True
    return False


def segment(cues, gap=0.9, max_chars=140, soft_min=40):
    """Merge fragmentary cues into paragraphs, preserving order."""
    paragraphs = []
    cur = []
    cur_len = 0
    flags = set()

    def flush():
        nonlocal cur, cur_len, flags
        if cur:
            paragraphs.append({"cues": cur, "flags": sorted(flags)})
        cur = []
        cur_len = 0
        flags = set()

    for cue in cues:
        if cur:
            prev = cur[-1]
            break_here = None
            if cue["start"] is not None and prev["end"] is not None:
                if cue["start"] - prev["end"] >= gap:
                    break_here = "gap"
            if break_here is None and cur_len >= max_chars and prev["text"][-1:] in _TERMINAL:
                break_here = "max_chars"
            if break_here is None and cur_len >= max_chars * 1.5:
                break_here = "max_chars_hard"
            if break_here is None and _starts_topic(cue["text"]) and cur_len >= soft_min:
                break_here = "topic_marker"
            if break_here is None and prev["text"][-1:] in "。！？!?…" and cur_len >= soft_min:
                break_here = "terminal_punct"
            if break_here:
                flags.add(break_here)
                flush()

        cur.append(cue)
        cur_len += len(cue["text"])

    flush()

    results = []
    for i, para in enumerate(paragraphs, 1):
        cues_in = para["cues"]
        text = "".join(c["text"] for c in cues_in)
        starts = [c["start"] for c in cues_in if c["start"] is not None]
        ends = [c["end"] for c in cues_in if c["end"] is not None]
        results.append({
            "id": "P%03d" % i,
            "start": min(starts) if starts else None,
            "end": max(ends) if ends else None,
            "text": text,
            "cues": [c["index"] for c in cues_in],
            "flags": para["flags"],
        })
    return results


# --------------------------------------------------------------------------
# ASR term candidate discovery (evidence only -- never a replacement table)
# --------------------------------------------------------------------------

_STOP_TERMS = {
    "咱们", "这个", "那个", "就是", "那么", "然后", "所以", "但是", "可以",
    "一个", "都是", "我们", "他们", "什么", "怎么", "这样", "那样", "的话",
    "时候", "现在", "这里", "那里", "不是", "还是", "如果", "因为", "而且",
    "其实", "已经", "应该", "可能", "觉得", "知道", "看到", "起来", "出来",
}


def _hamming1(a, b):
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        if x != y:
            diff += 1
            if diff > 1:
                return False
    return diff == 1


_BOUNDARY_STOP = set(
    "的了吧呢哈哎呀嘛哦啊嗯那这就都也和在有一个来去还要不我你他她它们会能可"
    "以为对于上下里中时说看让咱然后所但既只没很多么什这样次又再更最之与及或"
    "而则若把被从给到过着地得是"
) | set("0123456789")

# a form only counts as a candidate when it is this rare somewhere in the text
_RARE_MAX = 12
# ignore ultra-rare pairs where neither side has any traction at all
_MIN_TOP_FREQ = 2


def _looks_like_term(term):
    """Reject n-grams that begin/end on a function character (fragment noise)."""
    if len(term) < 2:
        return False
    if term in _STOP_TERMS:
        return False
    if term[0] in _BOUNDARY_STOP or term[-1] in _BOUNDARY_STOP:
        return False
    return True


def _context(full_text, term, width=9):
    m = re.search(r".{0,%d}" % width + re.escape(term) + r".{0,%d}" % width, full_text)
    return m.group(0) if m else None


def find_suspicious(paragraphs, top=20):
    """Surface near-identical term forms as ASR candidates.

    Reports DIRECT pairs only -- never a transitive closure, which would chain
    unrelated common phrases into one useless blob. Output is EVIDENCE for a
    model to adjudicate; the script never decides which form is correct.

    Note: this can only surface a wrong form when the correct form also occurs
    somewhere in the text. If the correct form never appears (a real and common
    ASR failure), the model must catch it from domain knowledge instead.
    """
    full_text = "".join(p["text"] for p in paragraphs)
    counter = Counter()
    for run in re.findall(r"[\u4e00-\u9fff]+", full_text):
        n = len(run)
        for size in range(2, 7):
            for i in range(n - size + 1):
                counter[run[i:i + size]] += 1

    by_len = defaultdict(list)
    for term in counter:
        if _looks_like_term(term):
            by_len[len(term)].append(term)
    for members in by_len.values():
        members.sort()

    pairs = []
    for members in by_len.values():
        n = len(members)
        for i in range(n):
            a = members[i]
            fa = counter[a]
            for j in range(i + 1, n):
                b = members[j]
                fb = counter[b]
                if fa <= fb:
                    rare, rare_f, top_f = a, fa, fb
                else:
                    rare, rare_f, top_f = b, fb, fa
                if rare_f > _RARE_MAX or top_f < _MIN_TOP_FREQ:
                    continue
                if _hamming1(a, b):
                    pairs.append((rare, rare_f, top_f, top_f - rare_f))

    # pinyin-identical pairs (catches 权值/全值, 连通/联通 when shapes differ more)
    if _HAS_PYPINYIN:
        groups = defaultdict(list)
        for members in by_len.values():
            for term in members:
                try:
                    groups[tuple(lazy_pinyin(term))].append(term)
                except Exception:
                    pass
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda t: -counter[t])
            top_f = counter[members[0]]
            for other in members[1:]:
                if counter[other] > _RARE_MAX:
                    continue
                pairs.append((other, counter[other], top_f, top_f - counter[other]))

    seen = set()
    anchor_used = Counter()
    results = []
    for rare, rare_f, top_f, gap in sorted(pairs, key=lambda p: (p[1], -p[2])):
        if rare in seen:
            continue
        peer = None
        for members in by_len.values():
            for term in members:
                if term != rare and _hamming1(term, rare) and counter[term] == top_f:
                    peer = term
                    break
            if peer:
                break
        if peer is None:
            continue
        # one high-frequency anchor must not flood the report with fragments
        if anchor_used[peer] >= 2:
            continue
        seen.add(rare)
        anchor_used[peer] += 1
        results.append({
            "rare_form": rare,
            "rare_count": rare_f,
            "likely_form": peer,
            "likely_count": top_f,
            "context": _context(full_text, rare),
        })
        if len(results) >= top:
            break
    return results


_LATIN_STOP = {
    "ok", "ppt", "cpu", "gpu", "ai", "ui", "api", "id", "vs", "etc", "no",
    "yes", "and", "or", "the", "of", "to", "in", "is", "it", "a", "b", "c",
    "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "q",
    "r", "s", "t", "u", "v", "w", "x", "y", "z",
}


def find_latin_terms(paragraphs, min_len=2, max_len=12):
    full_text = "".join(p["text"] for p in paragraphs)
    tokens = re.findall(r"[A-Za-z]{%d,%d}" % (min_len, max_len), full_text)
    counter = Counter(t.lower() for t in tokens)
    out = []
    for token, count in counter.items():
        if token in _LATIN_STOP:
            continue
        ctxs = []
        for m in re.finditer(r".{0,10}" + re.escape(token) + r".{0,10}", full_text, re.IGNORECASE):
            ctxs.append(m.group(0))
            if len(ctxs) >= 2:
                break
        out.append({"token": token, "count": count, "contexts": ctxs})
    out.sort(key=lambda r: (r["count"], r["token"]))
    return out


# --------------------------------------------------------------------------
# Key-point discovery (mechanical signals only -- candidates, not decisions)
# --------------------------------------------------------------------------

# The speaker telling the listener that something matters.
_STRESS_MARKERS = (
    "非常重要", "很重要", "重要的是", "特别重要", "一定要注意", "特别注意",
    "注意", "一定要", "必须要", "必须", "记住", "牢记", "记好", "别忘了",
    "不能忘", "重点", "关键", "核心", "本质", "难点", "考点", "常考",
    "考试会", "容易混", "容易搞错", "别搞混", "区分开", "区别是", "总结一下",
)

# The speaker naming or defining a term.
_DEFINITION_MARKERS = (
    "叫做", "叫作", "称为", "称作", "定义为", "是指", "指的是", "称之为",
    "简称为", "简称", "又称为", "又称", "也叫", "条件是", "当且仅当",
    "就是", "意味着", "即为",
)

# The speaker stating a bound -- NOT 重点 by this project's spec, kept as context.
_BOUND_MARKERS = (
    "至少", "最多", "不超过", "不小于", "大于等于", "小于等于", "必然",
    "必定", "一定有", "必须要", "至少要有", "必须要有",
)

# The speaker waving something through -- feeds the reading-order hints.
_LIGHTWEIGHT_MARKERS = (
    "了解一下就可以", "了解一下就行", "了解一下", "先了解一下", "有个印象",
    "先有个印象", "以后会讲", "后面会讲", "后面再说", "后面会提到",
    "暂时不用", "不需要掌握", "不用掌握", "不是重点", "这里先不展开",
)

_WINDOW_BEFORE = 20
_WINDOW_AFTER = 34


def _window(text, pos, length):
    lo = max(0, pos - _WINDOW_BEFORE)
    hi = min(len(text), pos + length + _WINDOW_AFTER)
    frag = text[lo:hi].strip()
    return ("…" if lo > 0 else "") + frag + ("…" if hi < len(text) else "")


def _scan(paragraphs, markers, top):
    """Report one window per (paragraph, marker) -- first occurrence only.

    Windows, not sentences: many ASR transcripts carry no punctuation at all,
    so "split on 。" collapses to whole paragraphs and tells you nothing.

    Overlapping markers in the same paragraph collapse to the longest one, so
    「了解一下就可以」 does not also report a redundant 「了解一下」.
    """
    hits = []
    ordered = sorted(markers, key=len, reverse=True)
    for p in paragraphs:
        text = p["text"]
        taken = []
        for marker in ordered:
            pos = text.find(marker)
            if pos < 0:
                continue
            if any(lo <= pos < hi for lo, hi in taken):
                continue
            taken.append((pos, pos + len(marker)))
            hits.append({
                "paragraph": p["id"],
                "start": p["start"],
                "marker": marker,
                "excerpt": _window(text, pos, len(marker)),
            })
    return hits[:top]


def find_key_candidates(paragraphs, top=90):
    """Surface the mechanical signals for 重点, kept in three separate buckets.

    The skill treats exactly two things as 重点: what the speaker stressed
    (_STRESS_MARKERS) and what the speaker defined (_DEFINITION_MARKERS).
    Bounds (_BOUND_MARKERS) are reported for context only and must stay in
    prose, not in conclusion cards.

    Output is EVIDENCE. A marker does not make a sentence important, and a
    missing marker does not make one unimportant -- the model still decides.
    """
    return {
        "stress": _scan(paragraphs, _STRESS_MARKERS, top),
        "definitions": _scan(paragraphs, _DEFINITION_MARKERS, top),
        "bounds": _scan(paragraphs, _BOUND_MARKERS, top // 2),
        "lightweight": _scan(paragraphs, _LIGHTWEIGHT_MARKERS, top // 3),
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_text(meta, paragraphs, clusters, latin, video_ref=None,
                keys=None, quiet=False):
    lines = []
    if not quiet:
        lines.append("# source: %s" % meta["path"])
        lines.append(
            "# format: %s | encoding: %s | cues: %d | duration: %s | paragraphs: %d"
            % (
                meta["format"],
                meta["encoding"],
                meta["cue_count"],
                format_timestamp(meta["duration_s"]),
                len(paragraphs),
            )
        )
        if video_ref:
            lines.append(
                "# video: %s %s | deep link template: "
                "https://www.bilibili.com/video/%s?t=<seconds>"
                % (video_ref["platform"], video_ref["video_id"], video_ref["video_id"])
            )
            lines.append(
                "# Confirm the id really is this video before emitting any link; "
                "a wrong id sends every timestamp somewhere else."
            )
        else:
            lines.append("# video: no BV/av id found near the input -- no jump links built.")
        if meta.get("warning"):
            lines.append("# warning: %s" % meta["warning"])
        lines.append("#")
    for p in paragraphs:
        cue_range = "%d-%d" % (p["cues"][0], p["cues"][-1]) if p["cues"] else "-"
        if p["start"] is None:
            header = "[%s] (no timestamps)  (cues %s)" % (p["id"], cue_range)
        elif p["end"] is None or p["end"] == p["start"]:
            header = "[%s] %s  (cues %s)" % (p["id"], format_timestamp(p["start"]), cue_range)
        else:
            header = "[%s] %s-%s  (cues %s)" % (
                p["id"], format_timestamp(p["start"]), format_timestamp(p["end"]), cue_range,
            )
        link = deeplink(video_ref, p["start"])
        if link:
            header += "  -> " + link
        lines.append(header)
        lines.append(p["text"])
        lines.append("")
    if not quiet and clusters:
        lines.append("# --- ASR term candidates (EVIDENCE ONLY, not corrections) ---")
        lines.append("# Adjudicate by context: self-consistency, domain fit, "
                     "protect legitimate words, else keep and annotate.")
        lines.append("# Absence of a pair does NOT mean a term is correct: a wrong form "
                     "whose correct form never appears cannot be paired.")
        for c in clusters:
            peer = c["likely_form"]
            peer_txt = "%s x%d" % (peer, c["likely_count"]) if peer else "(no counterpart in text)"
            lines.append(
                "- %s x%d  vs  %s" % (c["rare_form"], c["rare_count"], peer_txt)
            )
            if c.get("context"):
                lines.append("    e.g. ...%s..." % c["context"])
    if not quiet and latin:
        lines.append("# --- Latin tokens (check abbreviation/term contexts) ---")
        lines.append("- " + ", ".join("%s x%d" % (t["token"], t["count"]) for t in latin))
    if not quiet and keys:
        if keys["stress"]:
            lines.append("# --- Speaker stress signals (EVIDENCE ONLY) ---")
            lines.append("# One of the only two legitimate sources of 重点. Do not invent others.")
            for e in keys["stress"]:
                lines.append("- [%s] (%s) %s" % (e["paragraph"], e["marker"], e["excerpt"]))
        if keys["definitions"]:
            lines.append("# --- Definition sentences (EVIDENCE ONLY) ---")
            lines.append("# The other legitimate source of 重点: candidate 结论卡 / 关键点 material.")
            for d in keys["definitions"]:
                lines.append("- [%s] (%s) %s" % (d["paragraph"], d["marker"], d["excerpt"]))
        if keys["bounds"]:
            lines.append("# --- Bound / criterion statements (CONTEXT ONLY) ---")
            lines.append("# These are NOT 重点 by spec. Keep them in prose, never in a conclusion card.")
            for b in keys["bounds"]:
                lines.append("- [%s] (%s) %s" % (b["paragraph"], b["marker"], b["excerpt"]))
        if keys["lightweight"]:
            lines.append("# --- Speaker waved-through passages (READING-ORDER HINTS ONLY) ---")
            lines.append("# The speaker said these need only a glance; mark them as such in the map.")
            for w in keys["lightweight"]:
                lines.append("- [%s] (%s) %s" % (w["paragraph"], w["marker"], w["excerpt"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Segment a subtitle/transcript file into an order-preserving transcript."
    )
    parser.add_argument("file")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gap", type=float, default=0.9)
    parser.add_argument("--max-chars", type=int, default=140)
    parser.add_argument("--soft-min", type=int, default=40)
    parser.add_argument("--drop-filler", action="store_true")
    parser.add_argument("--no-dedupe", action="store_true")
    parser.add_argument("--no-suspicious", action="store_true")
    parser.add_argument("--no-keys", action="store_true",
                        help="skip stress-signal / definition sentence discovery")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--video-id", default=None,
                        help="override the auto-detected BV/av id used for jump links")
    parser.add_argument("--no-links", action="store_true",
                        help="do not build jump links even when an id is found")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print("[ERROR] file not found: %s" % path, file=sys.stderr)
        return 1

    raw = path.read_bytes()
    text, encoding, warning = decode_bytes(raw)
    fmt = detect_format(text)

    raw_cues = _PARSERS[fmt](text)
    if not raw_cues:
        print("[ERROR] no cues parsed (format detected: %s)" % fmt, file=sys.stderr)
        return 1

    cues = normalize_cues(raw_cues, drop_filler=args.drop_filler, dedupe=not args.no_dedupe)
    paragraphs = segment(cues, gap=args.gap, max_chars=args.max_chars, soft_min=args.soft_min)

    ends = [c["end"] for c in cues if c["end"] is not None]
    starts = [c["start"] for c in cues if c["start"] is not None]
    candidates = starts + ends
    duration = max(candidates) if candidates else None

    meta = {
        "path": str(path),
        "format": fmt,
        "encoding": encoding,
        "cue_count": len(cues),
        "raw_cue_count": len(raw_cues),
        "duration_s": duration,
        "warning": warning,
    }

    clusters = [] if args.no_suspicious else find_suspicious(paragraphs, top=args.top)
    latin = [] if args.no_suspicious else find_latin_terms(paragraphs)

    if args.video_id:
        video_ref = {"platform": "bilibili", "video_id": args.video_id}
    elif args.no_links:
        video_ref = None
    else:
        video_ref = detect_video_ref(path)

    if args.no_keys:
        keys = None
    else:
        keys = find_key_candidates(paragraphs)

    if args.json:
        payload = {
            "source": meta,
            "video_ref": video_ref,
            "paragraph_count": len(paragraphs),
            "paragraphs": paragraphs,
            "suspicious_terms": clusters,
            "latin_tokens": latin,
            "key_signals": keys or {},
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(
            meta, paragraphs, clusters, latin,
            video_ref=video_ref, keys=keys, quiet=args.quiet,
        ))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
