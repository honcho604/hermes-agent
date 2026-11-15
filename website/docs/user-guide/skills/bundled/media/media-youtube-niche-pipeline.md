---
title: "Youtube Niche Pipeline — Turn Reddit buzz into YouTube titles, scripts, voiceover"
sidebar_label: "Youtube Niche Pipeline"
description: "Turn Reddit buzz into YouTube titles, scripts, voiceover"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Youtube Niche Pipeline

Turn Reddit buzz into YouTube titles, scripts, voiceover.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/media/youtube-niche-pipeline` |
| Version | `1.0.0` |
| Author | honcho604 |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `youtube`, `reddit`, `scriptwriting`, `faceless-channel`, `voiceover`, `gta6`, `content-pipeline` |
| Related skills | [`youtube-content`](/docs/user-guide/skills/bundled/media/media-youtube-content), [`humanizer`](/docs/user-guide/skills/bundled/creative/creative-humanizer), [`kanban-video-orchestrator`](/docs/user-guide/skills/optional/creative/creative-kanban-video-orchestrator) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# YouTube Niche Pipeline Skill

Takes a niche from the newest Reddit buzz to a publish-ready long-form
YouTube package: a ranked list of angles, a source brief, three tested titles,
a checked 2,500-word retention script, a clean voiceover text, and an editor
shot list. GTA VI is the built-in preset (`references/gta6-niche.md`), and any
niche with an active subreddit works.

It does not render video or upload anything. It hands the voiceover and
shot list to Vidrush, the `text_to_speech` tool, or `kanban-video-orchestrator`.

## When to Use

- "Find me GTA 6 video ideas", "what's trending on r/GTA6", or "start the YouTube build"
- Turning a Reddit post into video titles or a voiceover script
- Checking or fixing a script for hooks, pacing, and length
- Setting up a daily scouting job for a faceless channel

Use `youtube-content` instead to summarize an existing YouTube video.

## Prerequisites

- Python 3.8+ (stdlib only). Runs through the `terminal` tool. On native
  Windows, run `python` (or `py -3`) wherever these docs say `python3`.
- Optional: `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`. Anonymous Reddit
  JSON works but is rate-limited and is sometimes blocked on servers and VPNs.
  With both set, the scout uses Reddit's OAuth API.
- Optional: the `tts` toolset (`text_to_speech`) for an in-house voiceover, or
  a Vidrush account. Vidrush's site advertises an MCP server. If the user has
  it configured in Hermes (`mcp_servers` in config.yaml), use its tools.
  Otherwise hand the files over for the user to paste into Vidrush.

Scripts live in `${HERMES_SKILL_DIR}/scripts/` (the skill directory).

## How to Run

```bash
S=${HERMES_SKILL_DIR}/scripts
python3 $S/reddit_scout.py scout --subs GTA6,GTA,rockstar --window day --format md
python3 $S/reddit_scout.py brief <post-url-or-id> --out $WORK/source_brief.md
python3 $S/script_tools.py titles --file $WORK/titles.md --keyword "GTA 6,GTA VI"
python3 $S/script_tools.py check $WORK/script.md
python3 $S/script_tools.py export $WORK/script.md --out $WORK --title "<title>"
```

Use a separate workspace for each video:
`$WORK = <cwd>/youtube/<YYYY-MM-DD>-<slug>/`.

## Quick Reference

| Command | Purpose | Key flags |
|---|---|---|
| `reddit_scout.py scout` | Rank posts by video potential | `--subs`, `--sort top\|hot\|rising`, `--window day\|week`, `--keyword`, `--min-score`, `--top`, `--from-file` |
| `reddit_scout.py brief` | Post + top comments → source brief | `--comments 15`, `--out`, `--from-file`, `--format md\|json` |
| `script_tools.py titles` | Title gate (≤60 chars, keyword, curiosity) | `--file`, `--keyword`, `--max-chars` |
| `script_tools.py check` | Script gate | `--target-words 2500`, `--tolerance 0.1`, `--wpm 150`, `--max-gap 450` |
| `script_tools.py export` | voiceover.txt, TTS chunks, shotlist.md | `--out`, `--chunk-chars 4000`, `--title` |

`script_tools.py` exits 0 when the gate passes, 2 when it fails, and 1 on bad
input. Each scout candidate has an `angle` (leak / theory / news / comparison /
gameplay / discussion / meme). Leak and theory posts have `hedge_required: true`.

Script markers: `[HOOK]`, `[SUB-HOOK]`, `[PATTERN INTERRUPT]`,
`[EMOTIONAL PEAK]`, `[CTA]`. Any other `[...]` (`[B-ROLL: ...]`,
`[VISUAL: ...]`, `[SFX: ...]`) is a stage direction and is never read aloud.

## Procedure

1. **Scout.** Run `scout` with the niche preset (`references/gta6-niche.md`
   for GTA VI). Show the user the top 5: title, angle, potential, and why
   each would make a strong video. Let the user pick one, or pick the highest
   potential that isn't a meme.
2. **Brief.** Run `brief <id> --out $WORK/source_brief.md`. If the post links
   an article or video, read it with `web_extract` and add the key facts under
   a `## Linked source` heading. Check official claims against the facts list
   in the niche preset.
3. **Titles.** Fill `templates/title_prompt.md` with the niche, keyword, and
   brief. Write three titles to `$WORK/titles.md` and run `titles`. Rewrite
   any title with errors, then recommend one.
4. **Script.** Fill `templates/script_prompt.md` with the chosen title,
   subreddit, and brief. Write it to `$WORK/script.md`. Run `check`, fix every
   error, and address the warnings. Repeat until `ok` is true. Revise only the
   flagged parts, and keep the passages that already work. See
   `references/retention-playbook.md` for fixes.
5. **Polish.** Read the script aloud in your head. Cut filler and anything
   that sounds machine-written (the `humanizer` skill helps). Then re-run `check`.
6. **Export.** Run `export`. This writes `voiceover.txt`,
   `voiceover_chunks/part_NN.txt` (sized for `text_to_speech`), and `shotlist.md`.
7. **Hand off.** Do one of the following:
   - Vidrush: use its MCP tools if configured. Otherwise give the user
     `voiceover.txt`, the title, and `shotlist.md` to paste in.
   - In-house: call `text_to_speech` once per chunk, in order. Then hand the
     audio and `shotlist.md` to `kanban-video-orchestrator`.
8. **Package.** Write `$WORK/description.md`. Include a two-line summary,
   timestamps from `shotlist.md` (sub-hooks make good chapters), a source
   credit ("Discussion via r/GTA6"), and an "unconfirmed" note for leaks.

**Daily scouting (optional):** use the `cronjob` tool. For example, set
schedule `every day 9am`, skills `[youtube-niche-pipeline]`, and the prompt
"Scout r/GTA6, r/GTA, r/rockstar for the last day and send me the top 5
angles with potential scores." Deliver it to the user's messaging platform.

## Pitfalls

- **Reddit 403/429:** anonymous access is blocked. Set the Reddit app
  credentials. Or have the user open the `.json` URL (for example
  `https://www.reddit.com/r/GTA6/top.json?t=day`) in a browser, save it, and
  pass `--from-file`.
- **Stating leaks as fact:** keep `hedge_required` posts hedged in both the
  title and the script. `check` warns when leak talk has no hedging.
- **Leaked footage:** describe it, never show it. Use official trailers or
  your own capture. See the platform rules in `references/retention-playbook.md`.
- **Mass-produced feel:** YouTube demonetizes "inauthentic" repetitive
  content. Every script needs original analysis, a counterpoint, and a voice.
  Don't just narrate the Reddit thread.
- **Stale facts:** release dates and editions change. Re-verify the preset's
  facts list before each script. Don't trust the preset blindly.
- **Spoilers after launch:** add "SPOILERS" to the packaging for any story
  or ending content.
- **Rewriting from scratch on a failed check:** fix only the flagged parts.
  A full rewrite usually breaks something that already passed.

## Verification

- `scout` returns `"ok": true` with candidates. If it doesn't, follow the
  error `message`.
- `titles` returns `"ok": true`, with every title at 60 characters or fewer.
- `check` returns `"ok": true`, with `words` within 10% of the target and
  `runtime_minutes` around 15-18 at 2,500 words.
- After `export`, `voiceover.txt` contains no `[` brackets, and every chunk is
  within `--chunk-chars`.
- Tests: `scripts/run_tests.sh tests/skills/test_youtube_niche_pipeline_skill.py -q`
