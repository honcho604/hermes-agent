#!/usr/bin/env python3
"""Reddit scout for the youtube-niche-pipeline skill.

Finds the Reddit posts in a niche with the most video potential (leaks,
theories, news) and turns a chosen post into a source brief that the title
and script steps build on. Python stdlib only.

Commands
--------
  scout   Rank recent posts from one or more subreddits by video potential.
  brief   Turn one post (URL or id) plus its top comments into a source brief.

Auth
----
Without credentials the public ``www.reddit.com/....json`` endpoints are used.
Reddit rate-limits and sometimes blocks anonymous traffic; set
``REDDIT_CLIENT_ID`` + ``REDDIT_CLIENT_SECRET`` (a free "script" app at
https://www.reddit.com/prefs/apps) to use the OAuth API instead. When Reddit is
unreachable, save the JSON from a browser and pass ``--from-file``.

Every command prints JSON (or Markdown with ``--format md``) to stdout. On
failure it prints ``{"ok": false, "error": ..., "message": ...}`` and exits 1.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_USER_AGENT = "python:hermes-youtube-niche-pipeline:1.0 (Hermes Agent skill)"
PUBLIC_BASE = "https://www.reddit.com"
OAUTH_BASE = "https://oauth.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
HTTP_TIMEOUT = 20

VALID_SORTS = ("hot", "new", "top", "rising", "controversial")
VALID_WINDOWS = ("hour", "day", "week", "month", "year", "all")

# Angle taxonomy, in priority order. The first angle whose keywords match
# becomes the post's primary angle; every match is kept in ``tags``.
# Weight = how well that kind of post converts into a long-form video.
ANGLES: Tuple[Tuple[str, float, Tuple[str, ...]], ...] = (
    ("leak", 1.0, ("leak", "leaks", "leaked", "leaker", "datamine", "datamined",
                   "dataminer", "insider", "rumor", "rumors", "rumour", "rumours")),
    ("theory", 0.9, ("theory", "theories", "speculation", "speculate", "predict",
                     "prediction", "predictions", "hint", "hints", "clue", "clues",
                     "easter egg", "easter eggs", "hidden", "secret", "secrets",
                     "foreshadow", "foreshadowing")),
    ("news", 0.9, ("official", "officially", "announced", "announcement", "trailer",
                   "newswire", "confirmed", "confirms", "release date", "pre-order",
                   "preorder", "pre-orders", "price", "pricing", "delay", "delayed",
                   "screenshots", "patch notes", "update")),
    ("comparison", 0.75, ("vs", "versus", "compared", "comparison", "side by side",
                          "gta 5", "gta v", "gtav", "rdr2", "red dead")),
    ("gameplay", 0.7, ("gameplay", "mechanic", "mechanics", "mission", "missions",
                       "heist", "heists", "weapon", "weapons", "vehicle", "vehicles",
                       "map", "online", "multiplayer", "character", "characters",
                       "story", "ending", "guide", "tips", "money")),
    ("meme", 0.2, ("meme", "memes", "lol", "lmao", "fan art", "fanart", "shitpost",
                   "drew", "drawing", "my art")),
)
DEFAULT_ANGLE = ("discussion", 0.5)
HEDGE_ANGLES = frozenset({"leak", "theory"})


def _compile(keywords: Iterable[str]) -> re.Pattern:
    parts = []
    for keyword in keywords:
        words = keyword.split()
        if not words:
            continue
        body = r"\s+".join(re.escape(w) for w in words)
        # \b only makes sense next to word characters ("C++" has none at the end).
        start = r"\b" if re.match(r"\w", words[0]) else ""
        end = r"\b" if re.search(r"\w$", words[-1]) else ""
        parts.append(start + body + end)
    if not parts:
        return re.compile(r"(?!x)x")  # matches nothing
    return re.compile("|".join(parts), re.IGNORECASE)


_ANGLE_PATTERNS = [(name, weight, _compile(words)) for name, weight, words in ANGLES]


class ScoutError(Exception):
    def __init__(self, error: str, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.error = error
        self.message = message
        self.status = status


# ── Parsing helpers ──────────────────────────────────────────────────────────

_ID_RE = re.compile(r"^(?:t3_)?([a-z0-9]{4,10})$", re.IGNORECASE)
_URL_ID_RES = (
    re.compile(r"/comments/([a-z0-9]{4,10})(?:/|$|\?)", re.IGNORECASE),
    re.compile(r"redd\.it/([a-z0-9]{4,10})(?:/|$|\?)", re.IGNORECASE),
)


def parse_post_id(value: str) -> str:
    """Return the base-36 post id from a Reddit URL, ``t3_`` fullname or id."""
    value = (value or "").strip()
    for rx in _URL_ID_RES:
        m = rx.search(value)
        if m:
            return m.group(1).lower()
    m = _ID_RE.match(value)
    if m:
        return m.group(1).lower()
    raise ScoutError("bad_post_id", f"Could not find a Reddit post id in: {value!r}")


def parse_subs(value: str) -> List[str]:
    subs = []
    for raw in re.split(r"[,+\s]+", value or ""):
        name = raw.strip()
        if name.lower().startswith("r/"):
            name = name[2:]
        if not name:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", name):
            raise ScoutError("bad_subreddit", f"Invalid subreddit name: {raw!r}")
        subs.append(name)
    if not subs:
        raise ScoutError("bad_subreddit", "No subreddit given (use --subs GTA6,GTA)")
    return subs


def parse_keywords(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [k.strip() for k in value.split(",") if k.strip()]


def classify_angle(title: str, flair: str = "", body: str = "") -> Tuple[str, float, List[str]]:
    """Return (primary_angle, weight, all_matching_angles)."""
    text = " ".join(x for x in (title, flair, (body or "")[:600]) if x)
    tags = [name for name, _w, rx in _ANGLE_PATTERNS if rx.search(text)]
    for name, weight, _rx in _ANGLE_PATTERNS:
        if name in tags:
            return name, weight, tags
    return DEFAULT_ANGLE[0], DEFAULT_ANGLE[1], tags


def video_potential(score: int, num_comments: int, age_hours: float,
                    upvote_ratio: float, angle_weight: float) -> float:
    """Score 0-100ish: how much long-form video potential a post has.

    Blends total engagement, comment depth (debate = watch time), velocity
    (still climbing = still trending) and the angle weight. Log scales keep a
    single mega-post from flattening everything else.
    """
    score = max(int(score or 0), 0)
    num_comments = max(int(num_comments or 0), 0)
    age = max(float(age_hours or 0.0), 1.0)
    engagement = 9.0 * math.log10(1 + score) + 11.0 * math.log10(1 + num_comments)
    velocity = 6.0 * math.log10(1 + (score + 2 * num_comments) / age)
    ratio = upvote_ratio if upvote_ratio is not None else 0.9
    quality = 4.0 * min(max(float(ratio), 0.0), 1.0)
    return round((engagement + velocity + quality) * (0.5 + angle_weight), 1)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _preview(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _matches_keywords(post: Dict[str, Any], keywords: List[str]) -> bool:
    if not keywords:
        return True
    hay = " ".join(str(post.get(k) or "") for k in ("title", "selftext", "link_flair_text"))
    rx = _compile(keywords)
    return bool(rx.search(hay))


def normalize_post(data: Dict[str, Any], now: float) -> Dict[str, Any]:
    created = float(data.get("created_utc") or now)
    age_hours = max((now - created) / 3600.0, 0.0)
    flair = data.get("link_flair_text") or ""
    selftext = data.get("selftext") or ""
    angle, weight, tags = classify_angle(data.get("title") or "", flair, selftext)
    score = int(data.get("score") or 0)
    num_comments = int(data.get("num_comments") or 0)
    ratio = data.get("upvote_ratio")
    permalink = data.get("permalink") or ""
    link_url = data.get("url_overridden_by_dest") or data.get("url") or ""
    if link_url and permalink and link_url.endswith(permalink):
        link_url = ""
    return {
        "id": data.get("id") or "",
        "subreddit": data.get("subreddit") or "",
        "title": data.get("title") or "",
        "url": (PUBLIC_BASE + permalink) if permalink else "",
        "link_url": link_url,
        "flair": flair,
        "score": score,
        "num_comments": num_comments,
        "upvote_ratio": ratio,
        "created_utc": _iso(created),
        "age_hours": round(age_hours, 1),
        "angle": angle,
        "tags": tags,
        "hedge_required": angle in HEDGE_ANGLES,
        "nsfw": bool(data.get("over_18")),
        "is_self": bool(data.get("is_self")),
        "selftext_preview": _preview(selftext, 280),
        "potential": video_potential(score, num_comments, age_hours, ratio, weight),
    }


def _is_usable(data: Dict[str, Any], include_stickied: bool) -> bool:
    if data.get("stickied") and not include_stickied:
        return False
    if data.get("removed_by_category"):
        return False
    if (data.get("selftext") or "").strip() in ("[removed]", "[deleted]"):
        return False
    return bool(data.get("title"))


def rank_listing(listing: Any, *, now: Optional[float] = None, keywords: Optional[List[str]] = None,
                 min_score: int = 0, include_stickied: bool = False,
                 include_nsfw: bool = False, top: Optional[int] = None) -> List[Dict[str, Any]]:
    """Normalize, filter and rank the posts in a Reddit listing payload."""
    now = time.time() if now is None else now
    keywords = keywords or []
    children = _listing_children(listing)
    seen = set()
    ranked = []
    for child in children:
        if child.get("kind") not in (None, "t3"):
            continue
        data = child.get("data") or {}
        if not _is_usable(data, include_stickied):
            continue
        if data.get("over_18") and not include_nsfw:
            continue
        if int(data.get("score") or 0) < min_score:
            continue
        if not _matches_keywords(data, keywords):
            continue
        post_id = data.get("id")
        if post_id in seen:
            continue
        seen.add(post_id)
        ranked.append(normalize_post(data, now))
    ranked.sort(key=lambda p: (p["potential"], p["score"]), reverse=True)
    return ranked[:top] if top else ranked


def _listing_children(listing: Any) -> List[Dict[str, Any]]:
    # A subreddit listing is {"kind": "Listing", "data": {"children": [...]}}.
    # A saved thread is [post_listing, comment_listing]; accept both.
    if isinstance(listing, list):
        out: List[Dict[str, Any]] = []
        for part in listing:
            out.extend(_listing_children(part))
        return out
    if isinstance(listing, dict):
        data = listing.get("data")
        if isinstance(data, dict) and isinstance(data.get("children"), list):
            return data["children"]
    raise ScoutError("bad_payload", "Expected a Reddit listing JSON payload")


def build_brief(thread: Any, *, max_comments: int = 15, comment_chars: int = 600,
                now: Optional[float] = None) -> Dict[str, Any]:
    """Turn a thread payload ([post_listing, comment_listing]) into a brief."""
    now = time.time() if now is None else now
    if not (isinstance(thread, list) and len(thread) >= 1):
        raise ScoutError("bad_payload", "Expected a Reddit thread payload: [post, comments]")
    posts = [c for c in _listing_children(thread[0]) if c.get("kind") in (None, "t3")]
    if not posts:
        raise ScoutError("bad_payload", "Thread payload contains no post")
    raw_post = posts[0].get("data") or {}
    post = normalize_post(raw_post, now)
    post["selftext"] = (raw_post.get("selftext") or "").strip()

    comments: List[Dict[str, Any]] = []
    if len(thread) > 1:
        for child in _listing_children(thread[1]):
            if child.get("kind") != "t1":
                continue
            data = child.get("data") or {}
            body = (data.get("body") or "").strip()
            if not body or body in ("[removed]", "[deleted]"):
                continue
            if data.get("stickied") or data.get("distinguished") == "moderator":
                continue
            if (data.get("author") or "").lower() == "automoderator":
                continue
            comments.append({"score": int(data.get("score") or 0),
                             "body": _preview(body, comment_chars)})
    comments.sort(key=lambda c: c["score"], reverse=True)
    post["top_comments"] = comments[:max_comments]
    return post


def brief_markdown(brief: Dict[str, Any]) -> str:
    angle = brief["angle"]
    angle_note = f"{angle} — label claims as unconfirmed on-air" if brief["hedge_required"] else angle
    lines = [
        f"# Source brief: {brief['title']}",
        "",
        f"- Subreddit: r/{brief['subreddit']} · Score: {brief['score']:,} · "
        f"Comments: {brief['num_comments']:,} · Angle: {angle_note}",
        f"- Thread: {brief['url']}",
    ]
    if brief.get("link_url"):
        lines.append(f"- Linked source: {brief['link_url']}")
    if brief.get("flair"):
        lines.append(f"- Flair: {brief['flair']}")
    lines.append(f"- Posted: {brief['created_utc']} ({brief['age_hours']} h ago)")
    lines += ["", "## Post", ""]
    lines.append(brief.get("selftext") or "(link/media post — open the linked source for details)")
    lines += ["", "## Top comments (community reaction)", ""]
    if brief["top_comments"]:
        for i, c in enumerate(brief["top_comments"], 1):
            lines.append(f"{i}. (+{c['score']:,}) {c['body']}")
    else:
        lines.append("(no comments fetched)")
    return "\n".join(lines) + "\n"


def scout_markdown(result: Dict[str, Any]) -> str:
    lines = [f"# Reddit scout: r/{'+'.join(result['subs'])} — {result['sort']}"
             + (f" ({result['window']})" if result["sort"] in ("top", "controversial") else ""),
             ""]
    if not result["candidates"]:
        lines.append("No posts matched. Widen --window, lower --min-score, or drop --keyword.")
    for i, p in enumerate(result["candidates"], 1):
        hedge = " ⚠ unconfirmed" if p["hedge_required"] else ""
        lines.append(f"{i}. **{p['title']}**")
        lines.append(f"   potential {p['potential']} · {p['angle']}{hedge} · "
                     f"▲{p['score']:,} · 💬{p['num_comments']:,} · {p['age_hours']} h · r/{p['subreddit']}")
        lines.append(f"   {p['url']}  (id: {p['id']})")
    return "\n".join(lines) + "\n"


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _user_agent() -> str:
    return os.environ.get("REDDIT_USER_AGENT") or DEFAULT_USER_AGENT


def _http_json(url: str, headers: Dict[str, str], data: Optional[bytes] = None) -> Any:
    req = urllib.request.Request(url, data=data, headers=headers)
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 1:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait = min(float(retry_after or 5), 30.0)
                except ValueError:
                    wait = 5.0
                time.sleep(wait)
                continue
            hint = ""
            if exc.code in (401, 403, 429):
                hint = (" Reddit refused anonymous access — set REDDIT_CLIENT_ID and "
                        "REDDIT_CLIENT_SECRET, or open the .json URL in a browser, save it, "
                        "and pass --from-file.")
            raise ScoutError("http_error", f"HTTP {exc.code} from {url}.{hint}", exc.code)
        except urllib.error.URLError as exc:
            raise ScoutError("network_error", f"Could not reach {url}: {exc.reason}. "
                             "Save the JSON in a browser and pass --from-file.")
        except json.JSONDecodeError:
            raise ScoutError("bad_payload", f"{url} did not return JSON (blocked or login wall?)")
    raise ScoutError("http_error", f"Rate limited by {url}", 429)


class RedditClient:
    def __init__(self) -> None:
        self.client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
        self._token: Optional[str] = None

    @property
    def uses_oauth(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _auth_headers(self) -> Dict[str, str]:
        headers = {"User-Agent": _user_agent(), "Accept": "application/json"}
        if not self.uses_oauth:
            return headers
        if self._token is None:
            basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            payload = _http_json(
                TOKEN_URL,
                {"User-Agent": _user_agent(), "Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
                data=b"grant_type=client_credentials",
            )
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if not token:
                raise ScoutError("auth_error", "Reddit OAuth did not return an access token — "
                                 "check REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET")
            self._token = token
        headers["Authorization"] = f"bearer {self._token}"
        return headers

    def _url(self, path: str, params: Dict[str, Any]) -> str:
        params = {k: v for k, v in params.items() if v is not None}
        params["raw_json"] = 1
        query = urllib.parse.urlencode(params)
        if self.uses_oauth:
            return f"{OAUTH_BASE}{path}?{query}"
        return f"{PUBLIC_BASE}{path}.json?{query}"

    def listing(self, subs: List[str], sort: str, window: str, limit: int) -> Any:
        params: Dict[str, Any] = {"limit": limit}
        if sort in ("top", "controversial"):
            params["t"] = window
        url = self._url(f"/r/{'+'.join(subs)}/{sort}", params)
        return _http_json(url, self._auth_headers())

    def thread(self, post_id: str, max_comments: int) -> Any:
        url = self._url(f"/comments/{post_id}", {"limit": max(max_comments * 2, 20),
                                                  "sort": "top", "depth": 1})
        return _http_json(url, self._auth_headers())


# ── CLI ──────────────────────────────────────────────────────────────────────

def _load_file(path: str) -> Any:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ScoutError("file_not_found", f"No such file: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScoutError("bad_payload", f"{p} is not valid JSON: {exc}")


def cmd_scout(args: argparse.Namespace) -> Tuple[Dict[str, Any], str]:
    subs = parse_subs(args.subs)
    if args.from_file:
        listing = _load_file(args.from_file)
        auth = "file"
    else:
        client = RedditClient()
        listing = client.listing(subs, args.sort, args.window, args.limit)
        auth = "oauth" if client.uses_oauth else "public"
    candidates = rank_listing(listing, keywords=parse_keywords(args.keyword),
                              min_score=args.min_score, include_stickied=args.include_stickied,
                              include_nsfw=args.include_nsfw, top=args.top)
    result = {
        "ok": True,
        "source": auth,
        "subs": subs,
        "sort": args.sort,
        "window": args.window,
        "fetched_at": _iso(time.time()),
        "count": len(candidates),
        "candidates": candidates,
    }
    return result, scout_markdown(result)


def cmd_brief(args: argparse.Namespace) -> Tuple[Dict[str, Any], str]:
    if args.from_file:
        thread = _load_file(args.from_file)
    else:
        if not args.post:
            raise ScoutError("bad_post_id", "Give a post URL/id or --from-file")
        thread = RedditClient().thread(parse_post_id(args.post), args.comments)
    brief = build_brief(thread, max_comments=args.comments)
    result = {"ok": True, "brief": brief}
    return result, brief_markdown(brief)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sc = sub.add_parser("scout", help="Rank recent posts by video potential")
    sc.add_argument("--subs", default="GTA6", help="Comma-separated subreddits (default: GTA6)")
    sc.add_argument("--sort", choices=VALID_SORTS, default="top")
    sc.add_argument("--window", choices=VALID_WINDOWS, default="day",
                    help="Time window for top/controversial (default: day)")
    sc.add_argument("--limit", type=int, default=50, help="Posts to fetch (max 100)")
    sc.add_argument("--top", type=int, default=10, help="Candidates to return (default: 10)")
    sc.add_argument("--min-score", type=int, default=0, help="Drop posts below this upvote score")
    sc.add_argument("--keyword", help="Only keep posts matching any of these comma-separated keywords")
    sc.add_argument("--include-stickied", action="store_true")
    sc.add_argument("--include-nsfw", action="store_true")
    sc.add_argument("--from-file", help="Read a saved listing JSON instead of calling Reddit")
    sc.add_argument("--format", choices=("json", "md"), default="json")

    br = sub.add_parser("brief", help="Build a source brief from one post + top comments")
    br.add_argument("post", nargs="?", help="Post URL, redd.it link, t3_ fullname or id")
    br.add_argument("--comments", type=int, default=15, help="Top comments to include (default: 15)")
    br.add_argument("--from-file", help="Read a saved thread JSON instead of calling Reddit")
    br.add_argument("--format", choices=("json", "md"), default="md")
    br.add_argument("--out", help="Also write the Markdown brief to this file")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "limit", None) is not None:
        args.limit = min(max(args.limit, 1), 100)
    try:
        if args.command == "scout":
            result, markdown = cmd_scout(args)
        else:
            result, markdown = cmd_brief(args)
            if args.out:
                out = Path(args.out).expanduser()
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(markdown, encoding="utf-8")
                result["written_to"] = str(out)
    except ScoutError as exc:
        err: Dict[str, Any] = {"ok": False, "error": exc.error, "message": exc.message}
        if exc.status is not None:
            err["status"] = exc.status
        print(json.dumps(err, indent=2))
        return 1
    if args.format == "md":
        sys.stdout.write(markdown)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
