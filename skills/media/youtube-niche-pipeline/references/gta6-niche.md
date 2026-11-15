# GTA VI Niche Preset

Default settings and angles for a Grand Theft Auto VI channel. For any other
niche, copy this file's shape: subreddits, keywords, phases, angles, and facts to verify.

## Scout settings

| Setting | Value |
|---|---|
| Core subreddits | `GTA6,GTA,rockstar` |
| Leak and rumor sources | `GamingLeaksAndRumours`, used with `--keyword` |
| Online angle (post-launch) | `gtaonline` |
| Keywords | `GTA 6,GTA VI,GTA6,GTAVI,Rockstar,Leonida,Vice City` |
| Title keyword | `GTA 6` (most searched form), then `GTA VI` |

```bash
python3 scripts/reddit_scout.py scout --subs GTA6,GTA,rockstar --window day --top 10 --format md
python3 scripts/reddit_scout.py scout --subs GamingLeaksAndRumours --window week \
  --keyword "GTA 6,GTA VI,GTA6,Rockstar" --format md
```

## Facts to verify before stating them

As of this skill's writing, Rockstar has announced:

- Release date: **November 19, 2026**, on PlayStation 5 and Xbox Series X|S
- Setting: the state of **Leonida**, including **Vice City**
- Protagonists: **Lucia Caminos** and **Jason Duval**

Check each one on the Rockstar Newswire (`rockstargames.com/newswire`) with
`web_search` / `web_extract` before putting it in a script. Rockstar has moved
the date before. Never present a leak or a Reddit claim as one of these facts.

## Angles by launch phase

**Pre-launch (until release day)**
- Trailer frame-by-frame: hidden details, license plates, billboards
- "Everything we know" round-ups. Refresh these often, because they get searched.
- Leak and rumor breakdowns, clearly labelled as unconfirmed
- Predictions: map size, Online at launch, price, editions
- Countdown urgency: "X things to do before GTA 6 launches"

**Launch week**
- First-hours impressions and things the game doesn't tell you
- Map size compared with GTA V, RDR2, and real Florida
- Easter eggs and callbacks to Vice City (2002) and GTA V
- Console performance comparisons. Only use footage you captured or official footage.
- Early money methods and missable content

**Post-launch (this is where the long tail of new angles is)**
- Lore and character deep dives: Lucia, Jason, Leonida's factions
- Ending explained and story theories, with **SPOILERS** in the packaging
- Community mysteries and hunts. Track the Reddit threads as they unfold.
- Online mode news, updates, patch notes, and DLC rumors
- "100 details" compilations, records, and speedruns
- Comparisons and rankings across the series

## Subreddit etiquette

Paraphrase posts and credit "a player on r/GTA6". Don't name usernames in
videos. Don't link-spam the subreddits with your videos.
