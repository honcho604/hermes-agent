#!/usr/bin/env python3
"""Title + script quality gates and voiceover export for youtube-niche-pipeline.

Commands
--------
  titles   Check title candidates (length, keyword, curiosity gap, caps/!).
  check    Check a marked-up script (word count, hook, sub-hooks, pattern
           interrupts, emotional peaks, retention gaps, hedging, sentence length).
  export   Strip markers/stage directions into voiceover text, TTS-sized chunks
           and a timestamped shot list for the editor.

Script markup (one marker per line, before the passage it labels):
  [HOOK] [SUB-HOOK] [PATTERN INTERRUPT] [EMOTIONAL PEAK] [CTA]
Any other ``[...]`` is an editor stage direction (``[B-ROLL: ...]``,
``[VISUAL: ...]``, ``[SFX: ...]``) and is never read aloud. Markdown headings
(``# ...``) and rules (``---``) are ignored too.

Output is JSON. Exit codes: 0 = passed, 2 = ran but the quality gate failed
(see ``errors``), 1 = bad input.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_TARGET_WORDS = 2500
DEFAULT_TOLERANCE = 0.10
DEFAULT_WPM = 150
DEFAULT_MAX_GAP = 450
DEFAULT_MAX_TITLE_CHARS = 60
DEFAULT_CHUNK_CHARS = 4000
LONG_SENTENCE_WORDS = 30
SUB_HOOK_EVERY = 700
PATTERN_INTERRUPT_EVERY = 450
MIN_EMOTIONAL_PEAKS = 2
HOOK_MAX_OFFSET = 50

_MARKER_ALIASES = {
    "HOOK": "HOOK",
    "SUB-HOOK": "SUB-HOOK",
    "SUBHOOK": "SUB-HOOK",
    "SUB HOOK": "SUB-HOOK",
    "PATTERN INTERRUPT": "PATTERN INTERRUPT",
    "PATTERN-INTERRUPT": "PATTERN INTERRUPT",
    "EMOTIONAL PEAK": "EMOTIONAL PEAK",
    "EMOTIONAL HIGH POINT": "EMOTIONAL PEAK",
    "CTA": "CTA",
}
RETENTION_DEVICES = frozenset({"HOOK", "SUB-HOOK", "PATTERN INTERRUPT", "EMOTIONAL PEAK"})

_BRACKET_RE = re.compile(r"\[([^\[\]\n]*)\]")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
_RULE_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_SPEAKER_RE = re.compile(r"^\s*(?:narrator|vo|v\.o\.|voiceover)\s*:\s*", re.IGNORECASE)
_PAREN_CUE_RE = re.compile(
    r"\(\s*(?:pause|beat|long pause|dramatic pause|whisper(?:ing|s)?|laughs?|sighs?)\s*\)",
    re.IGNORECASE,
)
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*)(?=\S)(.+?)(?<=\S)\1")
_SENTENCE_RE = re.compile(r"(?<=[.!?…])[\"”’)]*\s+")

_LEAK_RE = re.compile(r"\b(?:leak(?:s|ed|er|ers)?|insiders?|datamin(?:e|ed|er|ers|ing))\b", re.IGNORECASE)
_HEDGE_RE = re.compile(
    r"\b(?:reportedly|allegedly|unconfirmed|not (?:been )?confirmed|(?:hasn'?t|has not|haven'?t|have not) "
    r"(?:been )?confirmed|rumou?r(?:s|ed)?|according to|if (?:this|it'?s|that'?s) true|"
    r"grain of salt|speculation|unverified|can'?t verify|cannot verify)\b",
    re.IGNORECASE,
)
_WEAK_OPENERS = ("welcome back", "in this video", "hey guys", "what's up guys", "hello everyone",
                 "before we start", "don't forget to subscribe")

_CURIOSITY_RE = re.compile(
    r"\?|\.\.\.|…|\d|\b(?:secret|secrets|hidden|nobody|no one|never|finally|why|how|what|this|"
    r"truth|real|actually|revealed|exposed|mystery|weird|insane|crazy|changes everything|"
    r"didn'?t|don'?t|won'?t|until|before|after|only)\b",
    re.IGNORECASE,
)


class InputError(Exception):
    pass


# ── Parsing ──────────────────────────────────────────────────────────────────

def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def marker_name(inner: str) -> Optional[str]:
    key = re.sub(r"\s+", " ", (inner or "").strip().upper())
    return _MARKER_ALIASES.get(key)


def _clean_spoken(segment: str) -> str:
    segment = _PAREN_CUE_RE.sub(" ", segment)
    segment = _EMPHASIS_RE.sub(r"\2", segment)
    return re.sub(r"[ \t]+", " ", segment).strip()


def parse_script(text: str) -> Dict[str, Any]:
    """Split a marked-up script into spoken paragraphs, markers and directions.

    Marker/direction positions are recorded as the spoken-word offset at which
    they appear, so they can be turned into timestamps.
    """
    paragraphs: List[str] = []
    current: List[str] = []
    markers: List[Dict[str, Any]] = []
    directions: List[Dict[str, Any]] = []
    words = 0

    def flush() -> None:
        if current:
            paragraphs.append(" ".join(current))
            current.clear()

    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("#") or _RULE_RE.match(stripped):
            flush()
            continue
        line = _SPEAKER_RE.sub("", _BULLET_RE.sub("", line))
        pieces: List[str] = []
        pos = 0
        for m in _BRACKET_RE.finditer(line):
            seg = _clean_spoken(line[pos:m.start()])
            if seg:
                pieces.append(seg)
                words += count_words(seg)
            inner = m.group(1).strip()
            name = marker_name(inner)
            if name:
                markers.append({"marker": name, "word": words})
            elif inner:
                directions.append({"text": inner, "word": words})
            pos = m.end()
        seg = _clean_spoken(line[pos:])
        if seg:
            pieces.append(seg)
            words += count_words(seg)
        spoken = " ".join(pieces).strip()
        if spoken:
            current.append(spoken)
    flush()
    return {"paragraphs": paragraphs, "markers": markers, "directions": directions, "words": words}


def split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text or "") if s.strip()]


def fmt_ts(word_offset: int, wpm: int) -> str:
    seconds = int(round(word_offset / max(wpm, 1) * 60))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ── Titles ───────────────────────────────────────────────────────────────────

def check_titles(titles: List[str], *, max_chars: int = DEFAULT_MAX_TITLE_CHARS,
                 keywords: Optional[List[str]] = None) -> Dict[str, Any]:
    keywords = [k for k in (keywords or []) if k]
    results = []
    seen: Dict[str, int] = {}
    for idx, raw in enumerate(titles, 1):
        title = (raw or "").strip()
        errors: List[str] = []
        warnings: List[str] = []
        chars = len(title)
        if not title:
            errors.append("empty title")
        if chars > max_chars:
            errors.append(f"{chars} chars — over the {max_chars}-char limit (YouTube truncates in feeds)")
        if keywords and not any(k.lower() in title.lower() for k in keywords):
            warnings.append(f"missing niche keyword ({' / '.join(keywords)}) — searchers won't match it")
        elif keywords:
            first_hit = min(title.lower().find(k.lower()) for k in keywords if k.lower() in title.lower())
            if first_hit > 30:
                warnings.append("niche keyword sits late in the title — front-load it if it reads naturally")
        caps = [w for w in re.findall(r"\b[A-Z]{2,}\b", title) if not re.fullmatch(r"[IVXLC]+|GTA|DLC|PS\d|PC|NPC|RDR\d?", w)]
        if len(caps) > 1:
            warnings.append(f"{len(caps)} ALL-CAPS words ({', '.join(caps)}) — keep it to one")
        if title.count("!") > 1:
            warnings.append("more than one '!' reads as spam")
        if title and not _CURIOSITY_RE.search(title):
            warnings.append("no curiosity gap — add a question, number, or open loop")
        norm = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        if norm and norm in seen:
            errors.append(f"duplicate of title {seen[norm]}")
        elif norm:
            seen[norm] = idx
        results.append({"index": idx, "title": title, "chars": chars,
                        "errors": errors, "warnings": warnings})
    ok = bool(results) and all(not r["errors"] for r in results)
    return {"ok": ok, "max_chars": max_chars, "titles": results}


# ── Script check ─────────────────────────────────────────────────────────────

def check_script(text: str, *, target_words: int = DEFAULT_TARGET_WORDS,
                 tolerance: float = DEFAULT_TOLERANCE, wpm: int = DEFAULT_WPM,
                 max_gap: int = DEFAULT_MAX_GAP) -> Dict[str, Any]:
    parsed = parse_script(text)
    words = parsed["words"]
    markers = parsed["markers"]
    errors: List[str] = []
    warnings: List[str] = []

    counts: Dict[str, int] = {name: 0 for name in sorted(set(_MARKER_ALIASES.values()))}
    for m in markers:
        counts[m["marker"]] += 1

    if words == 0:
        errors.append("script has no spoken text")

    lo = int(target_words * (1 - tolerance))
    hi = int(math.ceil(target_words * (1 + tolerance)))
    if words and not lo <= words <= hi:
        errors.append(f"{words} spoken words — target is {target_words} (accepts {lo}-{hi})")

    hooks = [m for m in markers if m["marker"] == "HOOK"]
    if not hooks:
        errors.append("no [HOOK] — the first 15 seconds must open on one")
    elif hooks[0]["word"] > HOOK_MAX_OFFSET:
        errors.append(f"[HOOK] starts at word {hooks[0]['word']} — it must open the video")

    need_sub = max(1, words // SUB_HOOK_EVERY)
    need_pi = max(1, words // PATTERN_INTERRUPT_EVERY)
    if words and counts["SUB-HOOK"] < need_sub:
        errors.append(f"{counts['SUB-HOOK']} [SUB-HOOK] — need at least {need_sub} "
                      f"(one per ~{SUB_HOOK_EVERY} words)")
    if words and counts["PATTERN INTERRUPT"] < need_pi:
        errors.append(f"{counts['PATTERN INTERRUPT']} [PATTERN INTERRUPT] — need at least {need_pi} "
                      f"(one per ~{PATTERN_INTERRUPT_EVERY} words)")
    if words and counts["EMOTIONAL PEAK"] < MIN_EMOTIONAL_PEAKS:
        errors.append(f"{counts['EMOTIONAL PEAK']} [EMOTIONAL PEAK] — need at least {MIN_EMOTIONAL_PEAKS}")

    # Longest stretch of narration with no retention device.
    offsets = sorted({0, words} | {m["word"] for m in markers if m["marker"] in RETENTION_DEVICES})
    longest = (0, 0)
    for a, b in zip(offsets, offsets[1:]):
        if b - a > longest[1] - longest[0]:
            longest = (a, b)
    gap = longest[1] - longest[0]
    if words and gap > max_gap:
        warnings.append(f"{gap}-word stretch with no retention device (words {longest[0]}-{longest[1]}, "
                        f"~{fmt_ts(longest[0], wpm)}-{fmt_ts(longest[1], wpm)}) — add a sub-hook or "
                        "pattern interrupt")

    ctas = [m for m in markers if m["marker"] == "CTA"]
    if words and not ctas:
        warnings.append("no [CTA] — ask for the like/subscribe once, after delivering value")
    elif ctas and ctas[0]["word"] < wpm:
        warnings.append("first [CTA] lands in the first minute — move it after the first payoff")

    spoken = "\n\n".join(parsed["paragraphs"])
    if _LEAK_RE.search(spoken) and not _HEDGE_RE.search(spoken):
        warnings.append("talks about leaks/insiders but never signals they're unconfirmed — "
                        "say 'reportedly' / 'not confirmed by Rockstar' on-air")

    opening = " ".join(spoken.split()[:15]).lower()
    for opener in _WEAK_OPENERS:
        if opener in opening:
            warnings.append(f"weak opener ('{opener}') — open on the hook, not a greeting")
            break

    long_sentences = [s for s in split_sentences(spoken) if count_words(s) > LONG_SENTENCE_WORDS]
    if long_sentences:
        example = long_sentences[0]
        example = example if len(example) <= 120 else example[:117] + "..."
        warnings.append(f"{len(long_sentences)} sentence(s) over {LONG_SENTENCE_WORDS} words — "
                        f"hard to narrate. First: \"{example}\"")

    return {
        "ok": not errors,
        "words": words,
        "target_words": target_words,
        "runtime_minutes": round(words / max(wpm, 1), 1),
        "wpm": wpm,
        "markers": counts,
        "stage_directions": len(parsed["directions"]),
        "longest_gap_words": gap,
        "errors": errors,
        "warnings": warnings,
    }


# ── Export ───────────────────────────────────────────────────────────────────

def _hard_split(text: str, limit: int) -> List[str]:
    out, cur = [], ""
    for word in text.split():
        while len(word) > limit:  # pathological unbroken token
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:limit])
            word = word[limit:]
        candidate = f"{cur} {word}".strip()
        if len(candidate) > limit and cur:
            out.append(cur)
            cur = word
        else:
            cur = candidate
    if cur:
        out.append(cur)
    return out


def chunk_paragraphs(paragraphs: List[str], limit: int) -> List[str]:
    """Greedily pack paragraphs into chunks of at most ``limit`` characters.

    Breaks on paragraph boundaries first, then sentences, then words, so TTS
    never cuts a sentence in half unless one sentence alone exceeds the limit.
    """
    if limit < 50:
        raise InputError("--chunk-chars must be at least 50")
    units: List[str] = []
    for para in paragraphs:
        if len(para) <= limit:
            units.append(para)
            continue
        for sentence in split_sentences(para):
            units.extend([sentence] if len(sentence) <= limit else _hard_split(sentence, limit))

    chunks: List[str] = []
    cur = ""
    for unit in units:
        sep = "\n\n" if cur else ""
        if cur and len(cur) + len(sep) + len(unit) > limit:
            chunks.append(cur)
            cur = unit
        else:
            cur = cur + sep + unit
    if cur:
        chunks.append(cur)
    return chunks


def shotlist_markdown(parsed: Dict[str, Any], wpm: int, title: str = "") -> str:
    events = [(m["word"], 0, f"**{m['marker']}**") for m in parsed["markers"]]
    events += [(d["word"], 1, d["text"]) for d in parsed["directions"]]
    events.sort(key=lambda e: (e[0], e[1]))
    lines = [f"# Shot list{': ' + title if title else ''}", "",
             f"Timestamps estimated at {wpm} words/min of narration.", "",
             "| Time | Cue |", "|---|---|"]
    for word, _order, cue in events:
        lines.append(f"| {fmt_ts(word, wpm)} | {cue.replace('|', '/')} |")
    if not events:
        lines.append("| — | (no markers or stage directions found) |")
    return "\n".join(lines) + "\n"


def export_script(text: str, out_dir: Path, *, chunk_chars: int = DEFAULT_CHUNK_CHARS,
                  wpm: int = DEFAULT_WPM, title: str = "") -> Dict[str, Any]:
    parsed = parse_script(text)
    if not parsed["paragraphs"]:
        raise InputError("script has no spoken text to export")
    out_dir.mkdir(parents=True, exist_ok=True)

    voiceover = out_dir / "voiceover.txt"
    voiceover.write_text("\n\n".join(parsed["paragraphs"]) + "\n", encoding="utf-8")

    chunk_dir = out_dir / "voiceover_chunks"
    chunk_dir.mkdir(exist_ok=True)
    for stale in chunk_dir.glob("part_*.txt"):
        stale.unlink()
    chunks = chunk_paragraphs(parsed["paragraphs"], chunk_chars)
    chunk_paths = []
    for i, chunk in enumerate(chunks, 1):
        p = chunk_dir / f"part_{i:02d}.txt"
        p.write_text(chunk + "\n", encoding="utf-8")
        chunk_paths.append(str(p))

    shotlist = out_dir / "shotlist.md"
    shotlist.write_text(shotlist_markdown(parsed, wpm, title), encoding="utf-8")

    return {
        "ok": True,
        "words": parsed["words"],
        "runtime_minutes": round(parsed["words"] / max(wpm, 1), 1),
        "voiceover": str(voiceover),
        "chunks": chunk_paths,
        "chunk_chars": chunk_chars,
        "shotlist": str(shotlist),
        "stage_directions": len(parsed["directions"]),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise InputError(f"No such file: {p}")
    return p.read_text(encoding="utf-8")


def _titles_from_args(args: argparse.Namespace) -> List[str]:
    titles = list(args.title or [])
    if args.file:
        for line in _read(args.file).splitlines():
            line = _BULLET_RE.sub("", line).strip().strip('"').strip("“”").strip()
            if line and not line.startswith("#"):
                titles.append(line)
    if not titles:
        raise InputError("Give titles as arguments or with --file")
    return titles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("titles", help="Check title candidates")
    t.add_argument("title", nargs="*", help="Title candidates")
    t.add_argument("--file", help="File with one title per line")
    t.add_argument("--max-chars", type=int, default=DEFAULT_MAX_TITLE_CHARS)
    t.add_argument("--keyword", help="Comma-separated niche keywords, e.g. 'GTA 6,GTA VI'")

    c = sub.add_parser("check", help="Check a marked-up script")
    c.add_argument("script", help="Path to the script (Markdown/text)")
    c.add_argument("--target-words", type=int, default=DEFAULT_TARGET_WORDS)
    c.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                   help="Allowed word-count deviation as a fraction (default 0.10)")
    c.add_argument("--wpm", type=int, default=DEFAULT_WPM, help="Narration speed (default 150)")
    c.add_argument("--max-gap", type=int, default=DEFAULT_MAX_GAP,
                   help="Max words between retention devices before warning (default 450)")

    e = sub.add_parser("export", help="Export voiceover text, TTS chunks and shot list")
    e.add_argument("script", help="Path to the script (Markdown/text)")
    e.add_argument("--out", required=True, help="Output directory")
    e.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS,
                   help="Max characters per TTS chunk (default 4000)")
    e.add_argument("--wpm", type=int, default=DEFAULT_WPM)
    e.add_argument("--title", default="", help="Video title for the shot list header")
    return parser


def _utf8_stdout() -> None:
    """Print UTF-8 everywhere. Windows pipes default to the ANSI code page,
    which can't encode the emoji that fill Reddit titles."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _utf8_stdout()
    try:
        if args.command == "titles":
            keywords = [k.strip() for k in (args.keyword or "").split(",") if k.strip()]
            result = check_titles(_titles_from_args(args), max_chars=args.max_chars, keywords=keywords)
        elif args.command == "check":
            if args.target_words <= 0 or args.wpm <= 0 or not 0 <= args.tolerance < 1:
                raise InputError("--target-words and --wpm must be positive; --tolerance in [0, 1)")
            result = check_script(_read(args.script), target_words=args.target_words,
                                  tolerance=args.tolerance, wpm=args.wpm, max_gap=args.max_gap)
        else:
            result = export_script(_read(args.script), Path(args.out).expanduser(),
                                   chunk_chars=args.chunk_chars, wpm=args.wpm, title=args.title)
    except InputError as exc:
        print(json.dumps({"ok": False, "error": "bad_input", "message": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
