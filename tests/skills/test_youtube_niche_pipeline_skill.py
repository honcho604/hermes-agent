"""Tests for skills/media/youtube-niche-pipeline/scripts/{reddit_scout,script_tools}.py"""

import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills" / "media" / "youtube-niche-pipeline" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import reddit_scout as rs  # noqa: E402
import script_tools as st  # noqa: E402

NOW = 1_800_000_000.0  # fixed clock for deterministic ages


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _post(pid, title, *, score=100, comments=10, hours=5, flair="", selftext="",
          stickied=False, nsfw=False, removed=None, ratio=0.95, sub="GTA6"):
    return {
        "kind": "t3",
        "data": {
            "id": pid,
            "subreddit": sub,
            "title": title,
            "score": score,
            "num_comments": comments,
            "upvote_ratio": ratio,
            "created_utc": NOW - hours * 3600,
            "link_flair_text": flair,
            "selftext": selftext,
            "stickied": stickied,
            "over_18": nsfw,
            "removed_by_category": removed,
            "permalink": f"/r/{sub}/comments/{pid}/slug/",
            "url": f"https://www.reddit.com/r/{sub}/comments/{pid}/slug/",
            "is_self": True,
        },
    }


def _listing(*children):
    return {"kind": "Listing", "data": {"children": list(children)}}


def _comment(body, score, *, author="someone", stickied=False, distinguished=None):
    return {"kind": "t1", "data": {"body": body, "score": score, "author": author,
                                   "stickied": stickied, "distinguished": distinguished}}


def _thread():
    post = _post("abc123", "Huge GTA 6 leak about the map size", score=5000, comments=900,
                 hours=10, selftext="An insider claims the map is twice the size of GTA V.")
    comments = _listing(
        _comment("Mod note: be civil", 1, author="AutoModerator", stickied=True),
        _comment("Take this with a grain of salt.", 800),
        _comment("[deleted]", 900),
        _comment("Rockstar always delivers on scale.", 1200),
        _comment("Official mod reminder", 5, distinguished="moderator"),
        _comment("Source?", 50),
    )
    return [_listing(post), comments]


# ── reddit_scout: parsing ────────────────────────────────────────────────────

class TestParsePostId:
    @pytest.mark.parametrize("value", [
        "https://www.reddit.com/r/GTA6/comments/1abcde/some_title/",
        "https://old.reddit.com/r/GTA6/comments/1abcde",
        "https://redd.it/1abcde",
        "t3_1abcde",
        "1ABCDE",
        "  1abcde  ",
    ])
    def test_accepts_common_forms(self, value):
        assert rs.parse_post_id(value) == "1abcde"

    @pytest.mark.parametrize("value", ["", "https://example.com/foo", "not an id!"])
    def test_rejects_garbage(self, value):
        with pytest.raises(rs.ScoutError) as exc:
            rs.parse_post_id(value)
        assert exc.value.error == "bad_post_id"


class TestParseSubs:
    def test_mixed_separators_and_prefix(self):
        assert rs.parse_subs("r/GTA6, GTA+rockstar") == ["GTA6", "GTA", "rockstar"]

    def test_invalid_name(self):
        with pytest.raises(rs.ScoutError):
            rs.parse_subs("GTA6,../etc")

    def test_empty(self):
        with pytest.raises(rs.ScoutError):
            rs.parse_subs(" , ")


class TestClassifyAngle:
    def test_leak_beats_news_when_both_match(self):
        angle, weight, tags = rs.classify_angle("Leaked trailer confirms Vice City")
        assert angle == "leak"
        assert {"leak", "news"} <= set(tags)
        assert weight == max(w for _n, w, _k in rs.ANGLES)

    def test_theory(self):
        assert rs.classify_angle("My theory about the hidden ending")[0] == "theory"

    def test_flair_counts(self):
        assert rs.classify_angle("Look at this", flair="Fan Art")[0] == "meme"

    def test_word_boundaries(self):
        # "start" must not match "art"; "update" inside another word must not match.
        angle, _w, tags = rs.classify_angle("Where should we start")
        assert angle == "discussion"
        assert tags == []

    def test_multiword_keyword_tolerates_extra_spaces(self):
        assert rs.classify_angle("GTA 6 release   date leak?")[0] == "leak"
        assert "news" in rs.classify_angle("Official release   date")[2]


class TestVideoPotential:
    def test_more_engagement_scores_higher(self):
        low = rs.video_potential(100, 10, 5, 0.9, 0.7)
        high = rs.video_potential(10_000, 2_000, 5, 0.9, 0.7)
        assert high > low

    def test_angle_weight_matters(self):
        leak = rs.video_potential(500, 100, 5, 0.9, 1.0)
        meme = rs.video_potential(500, 100, 5, 0.9, 0.2)
        assert leak > meme

    def test_fresher_post_scores_higher(self):
        fresh = rs.video_potential(1000, 200, 2, 0.9, 0.9)
        stale = rs.video_potential(1000, 200, 200, 0.9, 0.9)
        assert fresh > stale

    def test_degenerate_inputs(self):
        value = rs.video_potential(0, 0, 0, None, 0.5)
        assert value >= 0


class TestRankListing:
    def test_filters_and_orders(self):
        listing = _listing(
            _post("a1", "Rules and FAQ", score=50_000, comments=5_000, stickied=True),
            _post("a2", "Removed thing", score=9_000, removed="moderator"),
            _post("a3", "NSFW thing", score=9_000, nsfw=True),
            _post("a4", "Low score leak", score=3),
            _post("a5", "Fan art I drew of Lucia", score=4_000, comments=100),
            _post("a6", "New leak: GTA 6 map is massive", score=4_000, comments=100),
            _post("a6", "New leak: GTA 6 map is massive", score=4_000, comments=100),
            _post("a7", "Deleted text", selftext="[removed]", score=9_000),
        )
        ranked = rs.rank_listing(listing, now=NOW, min_score=10)
        ids = [p["id"] for p in ranked]
        assert ids == ["a6", "a5"]
        top = ranked[0]
        assert top["angle"] == "leak" and top["hedge_required"] is True
        assert top["url"] == "https://www.reddit.com/r/GTA6/comments/a6/slug/"
        assert top["link_url"] == ""  # self-post URL is not a separate source
        assert top["age_hours"] == 5.0

    def test_opt_in_flags(self):
        listing = _listing(_post("s1", "Rules", stickied=True), _post("n1", "x", nsfw=True))
        ranked = rs.rank_listing(listing, now=NOW, include_stickied=True, include_nsfw=True)
        assert {p["id"] for p in ranked} == {"s1", "n1"}

    def test_keyword_filter(self):
        listing = _listing(
            _post("k1", "Elden Ring DLC leak", sub="GamingLeaksAndRumours"),
            _post("k2", "GTA VI pricing leak", sub="GamingLeaksAndRumours"),
            _post("k3", "Rumor thread", selftext="Rockstar is hiring", sub="GamingLeaksAndRumours"),
        )
        ranked = rs.rank_listing(listing, now=NOW, keywords=["GTA 6", "GTA VI", "Rockstar"])
        assert {p["id"] for p in ranked} == {"k2", "k3"}

    def test_top_limit(self):
        listing = _listing(*[_post(f"p{i}", f"Post {i}", score=i * 10) for i in range(1, 8)])
        assert len(rs.rank_listing(listing, now=NOW, top=3)) == 3

    def test_accepts_thread_payload(self):
        ranked = rs.rank_listing(_thread(), now=NOW)
        assert [p["id"] for p in ranked] == ["abc123"]

    def test_rejects_non_listing(self):
        with pytest.raises(rs.ScoutError):
            rs.rank_listing({"foo": "bar"}, now=NOW)


class TestBrief:
    def test_build_brief_filters_and_sorts_comments(self):
        brief = rs.build_brief(_thread(), max_comments=10, now=NOW)
        bodies = [c["body"] for c in brief["top_comments"]]
        assert bodies == [
            "Rockstar always delivers on scale.",
            "Take this with a grain of salt.",
            "Source?",
        ]
        assert brief["selftext"].startswith("An insider claims")
        assert brief["hedge_required"] is True

    def test_max_comments(self):
        assert len(rs.build_brief(_thread(), max_comments=1, now=NOW)["top_comments"]) == 1

    def test_markdown(self):
        md = rs.brief_markdown(rs.build_brief(_thread(), now=NOW))
        assert md.startswith("# Source brief: Huge GTA 6 leak about the map size")
        assert "label claims as unconfirmed" in md
        assert "1. (+1,200) Rockstar always delivers on scale." in md
        assert "AutoModerator" not in md

    def test_bad_payload(self):
        with pytest.raises(rs.ScoutError):
            rs.build_brief({"not": "a thread"})


# ── reddit_scout: HTTP + CLI ─────────────────────────────────────────────────

class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _json_response(payload):
    return _FakeResponse(json.dumps(payload).encode("utf-8"))


class TestRedditClient:
    def test_public_urls_without_credentials(self, monkeypatch):
        monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
        monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append(req)
            return _json_response(_listing(_post("x1", "GTA 6 news")))

        monkeypatch.setattr(rs.urllib.request, "urlopen", fake_urlopen)
        client = rs.RedditClient()
        assert not client.uses_oauth
        client.listing(["GTA6", "GTA"], "top", "day", 25)
        url = seen[0].full_url
        assert url.startswith("https://www.reddit.com/r/GTA6+GTA/top.json?")
        assert "t=day" in url and "limit=25" in url and "raw_json=1" in url
        assert seen[0].get_header("Authorization") is None
        assert "hermes" in seen[0].get_header("User-agent")

    def test_window_only_for_top_and_controversial(self, monkeypatch):
        monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
        seen = []
        monkeypatch.setattr(rs.urllib.request, "urlopen",
                            lambda req, timeout=None: seen.append(req) or _json_response(_listing()))
        rs.RedditClient().listing(["GTA6"], "hot", "day", 10)
        assert "t=day" not in seen[0].full_url

    def test_oauth_flow(self, monkeypatch):
        monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append(req)
            if req.full_url == rs.TOKEN_URL:
                return _json_response({"access_token": "tok", "token_type": "bearer"})
            return _json_response(_thread())

        monkeypatch.setattr(rs.urllib.request, "urlopen", fake_urlopen)
        client = rs.RedditClient()
        assert client.uses_oauth
        client.thread("abc123", 15)
        client.thread("abc123", 15)  # token is cached
        token_calls = [r for r in seen if r.full_url == rs.TOKEN_URL]
        assert len(token_calls) == 1
        assert token_calls[0].data == b"grant_type=client_credentials"
        assert token_calls[0].get_header("Authorization").startswith("Basic ")
        api_call = seen[1]
        assert api_call.full_url.startswith("https://oauth.reddit.com/comments/abc123?")
        assert api_call.get_header("Authorization") == "bearer tok"

    def test_oauth_without_token(self, monkeypatch):
        monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
        monkeypatch.setattr(rs.urllib.request, "urlopen",
                            lambda req, timeout=None: _json_response({"error": "invalid_grant"}))
        with pytest.raises(rs.ScoutError) as exc:
            rs.RedditClient().listing(["GTA6"], "top", "day", 5)
        assert exc.value.error == "auth_error"

    def test_403_gives_actionable_hint(self, monkeypatch, capsys):
        monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "Blocked", {}, None)

        monkeypatch.setattr(rs.urllib.request, "urlopen", fake_urlopen)
        assert rs.main(["scout", "--subs", "GTA6"]) == 1
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False and out["status"] == 403
        assert "--from-file" in out["message"] and "REDDIT_CLIENT_ID" in out["message"]

    def test_429_retries_once(self, monkeypatch):
        monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Slow down", {"Retry-After": "0"}, None)
            return _json_response(_listing(_post("r1", "GTA 6 trailer 3")))

        monkeypatch.setattr(rs.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(rs.time, "sleep", lambda s: None)
        payload = rs.RedditClient().listing(["GTA6"], "top", "day", 5)
        assert len(calls) == 2
        assert payload["data"]["children"][0]["data"]["id"] == "r1"

    def test_network_error(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("no route")

        monkeypatch.setattr(rs.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(rs.ScoutError) as exc:
            rs.RedditClient().listing(["GTA6"], "top", "day", 5)
        assert exc.value.error == "network_error"


class TestScoutCli:
    def test_scout_from_file_json(self, tmp_path, capsys):
        f = tmp_path / "listing.json"
        f.write_text(json.dumps(_listing(
            _post("c1", "GTA 6 trailer 3 theory", score=900, comments=300),
            _post("c2", "lol meme", score=900, comments=300),
        )))
        assert rs.main(["scout", "--subs", "GTA6", "--from-file", str(f)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True and out["source"] == "file"
        assert [c["id"] for c in out["candidates"]] == ["c1", "c2"]

    def test_scout_markdown(self, tmp_path, capsys):
        f = tmp_path / "listing.json"
        f.write_text(json.dumps(_listing(_post("m1", "Leak: Online at launch"))))
        assert rs.main(["scout", "--from-file", str(f), "--format", "md"]) == 0
        md = capsys.readouterr().out
        assert md.startswith("# Reddit scout: r/GTA6 — top (day)")
        assert "unconfirmed" in md and "(id: m1)" in md

    def test_scout_empty_markdown(self, tmp_path, capsys):
        f = tmp_path / "listing.json"
        f.write_text(json.dumps(_listing()))
        assert rs.main(["scout", "--from-file", str(f), "--format", "md"]) == 0
        assert "No posts matched" in capsys.readouterr().out

    def test_brief_from_file_writes_out(self, tmp_path, capsys):
        f = tmp_path / "thread.json"
        f.write_text(json.dumps(_thread()))
        out_file = tmp_path / "work" / "source_brief.md"
        assert rs.main(["brief", "--from-file", str(f), "--out", str(out_file)]) == 0
        printed = capsys.readouterr().out
        assert printed.startswith("# Source brief:")
        assert out_file.read_text() == printed

    def test_missing_file(self, capsys):
        assert rs.main(["scout", "--from-file", "/nonexistent/x.json"]) == 1
        assert json.loads(capsys.readouterr().out)["error"] == "file_not_found"

    def test_brief_needs_target(self, capsys):
        assert rs.main(["brief"]) == 1
        assert json.loads(capsys.readouterr().out)["error"] == "bad_post_id"


# ── script_tools: helpers ────────────────────────────────────────────────────

SENTENCE = "The map hides one more secret right here."  # 8 words


def _make_script(words=2500, *, sub_hooks=4, interrupts=6, peaks=2, cta=True,
                 hook=True, extra_spoken=""):
    """Build a script of ``words`` spoken words with evenly spread markers."""
    n_sentences = words // 8
    sentences = [SENTENCE] * n_sentences
    remainder = words - n_sentences * 8
    if remainder:
        sentences.append(" ".join(["Wow"] * remainder) + ".")
    devices = (["[SUB-HOOK]"] * sub_hooks + ["[PATTERN INTERRUPT]"] * interrupts
               + ["[EMOTIONAL PEAK]"] * peaks)
    slots = len(devices) + 1
    per_slot = max(len(sentences) // slots, 1)
    lines = ["# Title heading", ""]
    if hook:
        lines.append("[HOOK]")
    if extra_spoken:
        lines.append(extra_spoken)
    for i, sentence in enumerate(sentences):
        if i and i % per_slot == 0 and devices:
            lines.append("")
            lines.append(devices.pop(0))
            if cta and i // per_slot == 2:
                lines.append("[CTA]")
            lines.append("[B-ROLL: trailer shot]")
        lines.append(sentence)
    return "\n".join(lines) + "\n"


class TestParseScript:
    def test_markers_and_directions_are_not_spoken(self):
        text = (
            "# Heading\n"
            "---\n"
            "[HOOK]\n"
            "Narrator: **Thirty** seconds in, (pause) something *moves*.\n"
            "[VISUAL: freeze frame] Look closer.\n"
            "\n"
            "- A bullet point line.\n"
            "[sub hook]\n"
            "Not done yet.\n"
        )
        parsed = st.parse_script(text)
        assert parsed["paragraphs"] == [
            "Thirty seconds in, something moves. Look closer.",
            "A bullet point line. Not done yet.",
        ]
        assert parsed["words"] == 14
        assert parsed["markers"] == [{"marker": "HOOK", "word": 0},
                                     {"marker": "SUB-HOOK", "word": 11}]
        assert parsed["directions"] == [{"text": "VISUAL: freeze frame", "word": 5}]

    def test_marker_aliases(self):
        for alias in ("SUBHOOK", "Pattern-Interrupt", "emotional high point", "cta"):
            assert st.marker_name(alias) is not None
        assert st.marker_name("B-ROLL: x") is None

    def test_fmt_ts(self):
        assert st.fmt_ts(0, 150) == "00:00"
        assert st.fmt_ts(150, 150) == "01:00"
        assert st.fmt_ts(2500, 150) == "16:40"


class TestCheckScript:
    def test_well_formed_script_passes(self):
        script = _make_script()
        result = st.check_script(script)
        assert result["ok"] is True, result["errors"]
        assert result["words"] == 2500
        assert result["runtime_minutes"] == pytest.approx(16.7)
        assert result["markers"]["HOOK"] == 1
        assert result["markers"]["SUB-HOOK"] == 4
        assert result["stage_directions"] > 0

    def test_missing_hook(self):
        result = st.check_script(_make_script(hook=False))
        assert not result["ok"]
        assert any("[HOOK]" in e for e in result["errors"])

    def test_buried_hook(self):
        script = _make_script().replace("[HOOK]\n", "", 1)
        script = script.replace("[SUB-HOOK]", "[HOOK]", 1)
        result = st.check_script(script)
        assert any("must open the video" in e for e in result["errors"])

    def test_word_count_out_of_range(self):
        result = st.check_script(_make_script(1500, sub_hooks=2, interrupts=3))
        assert not result["ok"]
        assert any("target is 2500" in e for e in result["errors"])
        assert st.check_script(_make_script(1500, sub_hooks=2, interrupts=3),
                               target_words=1500)["ok"]

    def test_too_few_retention_devices(self):
        result = st.check_script(_make_script(sub_hooks=1, interrupts=1, peaks=0))
        errors = " ".join(result["errors"])
        assert "[SUB-HOOK]" in errors and "[PATTERN INTERRUPT]" in errors
        assert "[EMOTIONAL PEAK]" in errors
        assert any("stretch with no retention device" in w for w in result["warnings"])

    def test_cta_warnings(self):
        no_cta = st.check_script(_make_script(cta=False))
        assert any("no [CTA]" in w for w in no_cta["warnings"])
        early = st.check_script("[HOOK]\n[CTA]\n" + _make_script(cta=False).split("[HOOK]\n", 1)[1])
        assert any("first minute" in w for w in early["warnings"])

    def test_leak_without_hedge_warns(self):
        script = _make_script(extra_spoken="A leak shows the whole map.")
        assert any("leaks/insiders" in w for w in st.check_script(script)["warnings"])
        hedged = _make_script(extra_spoken="A leak, which Rockstar hasn't confirmed, shows the map.")
        assert not any("leaks/insiders" in w for w in st.check_script(hedged)["warnings"])

    def test_weak_opener_and_long_sentence(self):
        long_sentence = " ".join(["word"] * 40) + "."
        script = _make_script(extra_spoken="Welcome back to the channel. " + long_sentence)
        warnings = " ".join(st.check_script(script)["warnings"])
        assert "weak opener" in warnings
        assert "over 30 words" in warnings

    def test_empty_script(self):
        result = st.check_script("# Only a heading\n[HOOK]\n")
        assert not result["ok"]
        assert "script has no spoken text" in result["errors"]


class TestChunking:
    def test_chunks_respect_limit_and_preserve_text(self):
        paragraphs = [" ".join([SENTENCE] * n) for n in (3, 40, 1, 25)]
        chunks = st.chunk_paragraphs(paragraphs, 300)
        assert all(len(c) <= 300 for c in chunks)
        joined_words = " ".join(" ".join(chunks).split())
        assert joined_words == " ".join(" ".join(paragraphs).split())

    def test_giant_token_is_hard_split(self):
        chunks = st.chunk_paragraphs(["x" * 250], 100)
        assert all(len(c) <= 100 for c in chunks)
        assert "".join(chunks) == "x" * 250

    def test_rejects_tiny_limit(self):
        with pytest.raises(st.InputError):
            st.chunk_paragraphs(["hello"], 10)


class TestExport:
    def test_export_writes_clean_files(self, tmp_path):
        script = _make_script()
        result = st.export_script(script, tmp_path / "out", chunk_chars=1500, title="GTA 6 Map")
        voiceover = Path(result["voiceover"]).read_text()
        assert "[" not in voiceover and "#" not in voiceover
        assert st.count_words(voiceover) == 2500
        assert len(result["chunks"]) > 1
        assert all(len(Path(p).read_text().strip()) <= 1500 for p in result["chunks"])
        shotlist = Path(result["shotlist"]).read_text()
        assert shotlist.startswith("# Shot list: GTA 6 Map")
        assert "| 00:00 | **HOOK** |" in shotlist
        assert "B-ROLL: trailer shot" in shotlist

    def test_reexport_removes_stale_chunks(self, tmp_path):
        out = tmp_path / "out"
        first = st.export_script(_make_script(), out, chunk_chars=500)
        second = st.export_script(_make_script(), out, chunk_chars=4000)
        assert len(first["chunks"]) > len(second["chunks"])
        assert len(list((out / "voiceover_chunks").glob("part_*.txt"))) == len(second["chunks"])

    def test_export_empty_script(self, tmp_path):
        with pytest.raises(st.InputError):
            st.export_script("[HOOK]\n", tmp_path)


class TestTitles:
    def test_good_titles_pass(self):
        result = st.check_titles(
            ["GTA 6's Map Is Hiding Something Huge",
             "Nobody Noticed This in the GTA VI Trailer",
             "Is GTA 6 Online Already Delayed?"],
            keywords=["GTA 6", "GTA VI"],
        )
        assert result["ok"] is True
        assert all(not t["warnings"] for t in result["titles"])

    def test_too_long_is_error(self):
        title = "GTA 6 " + "x" * 60
        result = st.check_titles([title])
        assert not result["ok"]
        assert "over the 60-char limit" in result["titles"][0]["errors"][0]

    def test_duplicate_is_error(self):
        result = st.check_titles(["GTA 6 secret map?", "gta 6 secret MAP?"])
        assert result["titles"][1]["errors"] == ["duplicate of title 1"]

    def test_warnings(self):
        result = st.check_titles(["SHOCKING INSANE Map Reveal!!"], keywords=["GTA 6"])
        warnings = " ".join(result["titles"][0]["warnings"])
        assert "missing niche keyword" in warnings
        assert "ALL-CAPS" in warnings
        assert "'!'" in warnings
        assert result["ok"] is True  # warnings don't fail the gate

    def test_caps_exemptions(self):
        result = st.check_titles(["GTA VI Has a Secret DLC?"])
        assert not any("ALL-CAPS" in w for w in result["titles"][0]["warnings"])

    def test_no_curiosity_gap(self):
        result = st.check_titles(["Grand Theft Auto Gameplay Review"])
        assert any("curiosity" in w for w in result["titles"][0]["warnings"])


class TestScriptToolsCli:
    def test_titles_from_file(self, tmp_path, capsys):
        f = tmp_path / "titles.md"
        f.write_text('# Titles\n1. "GTA 6 Map Secret?"\n- GTA 6 Leak Changes Everything\n\n')
        assert st.main(["titles", "--file", str(f), "--keyword", "GTA 6"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert [t["title"] for t in out["titles"]] == ["GTA 6 Map Secret?", "GTA 6 Leak Changes Everything"]

    def test_titles_gate_failure_exit_code(self, capsys):
        assert st.main(["titles", "x" * 70]) == 2

    def test_titles_need_input(self, capsys):
        assert st.main(["titles"]) == 1
        assert json.loads(capsys.readouterr().out)["error"] == "bad_input"

    def test_check_and_export(self, tmp_path, capsys):
        script = tmp_path / "script.md"
        script.write_text(_make_script())
        assert st.main(["check", str(script)]) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
        assert st.main(["export", str(script), "--out", str(tmp_path / "pkg")]) == 0
        assert (tmp_path / "pkg" / "voiceover.txt").is_file()

    def test_check_failure_exit_code(self, tmp_path, capsys):
        script = tmp_path / "script.md"
        script.write_text("Just one line.\n")
        assert st.main(["check", str(script)]) == 2

    def test_bad_args(self, tmp_path, capsys):
        script = tmp_path / "script.md"
        script.write_text(_make_script())
        assert st.main(["check", str(script), "--tolerance", "1.5"]) == 1
        assert st.main(["check", str(tmp_path / "missing.md")]) == 1
