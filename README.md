# Raaz Kahani bot 🌙

A growth-first, fully automated channel of **original animated Hindi mystery/fantasy stories** for
**YouTube Shorts**, **Instagram Reels** and a **weekly long YouTube story**, built to cross the monetization
thresholds as fast as possible while staying eligible. It runs free on GitHub Actions.

## What happens every day

| Time (IST) | What | Where |
|---|---|---|
| 10:00 | 3 original story ideas arrive in Telegram with buttons: pick one, ask for new ones, or send your own idea (+ an optional note). | `director.py` |
| every 2 h | The next video is made up to 8 h before its slot: story, pictures and voice at the same time, then a parallel render. The preview arrives in Telegram with **✅ Post / 🔁 Remake / 🗑 Skip**. | `main.py` |
| 08:00, 19:30 | YouTube Shorts (when YouTube is connected) | `publisher.py` |
| 20:30 | Instagram Reel: the better Short of the day with its own end card, cover, caption, and a Story card. A fresh story while YouTube isn't connected. | `publisher.py` |
| hourly | Stats from both platforms; Hindi replies to genuine comments (max 12/run, never the same reply twice) | `growth.py` |
| Sun 11:00 | One ORIGINAL 8–12 min long story in 16:9 with chapters and a thumbnail (made Saturday evening) | `longform.py` |
| Sun 20:53 | Weekly report: totals, best/worst, threshold progress + projected dates, A/B results, token warnings | `weekly.py` |

**Review:** for the first 30 days (`REVIEW_MODE=phase`) every video waits for your ✅. If you don't answer by the
slot it posts anyway, so a day is never missed (`APPROVAL_TIMEOUT=true`), and the log notes it as automatic. After
day 30 videos post unless you tap 🗑 Skip. Every decision you make is logged in `decisions.json`, which is
proof of human creative direction.

## Growth targets (tracked in the weekly report)

- **YouTube early tier:** 500 subscribers + 3 uploads in 90 days + 3M Shorts views in 90 days.
- **YouTube Partner Program:** 1,000 subscribers + 10M Shorts views in 90 days **or** 4,000 long-form watch hours
  in 12 months. Shorts views never count toward watch hours, which is why the weekly long story exists. Per the
  brief, thresholds double for new applicants on 2027-02-01; the report flags dates after that.
- **Instagram:** 500 followers (Gifts), 1,000 (Creator Marketplace), 10,000 (Subscriptions).

## Monetization safety (built in)

- **Every story is new.** Each premise is embedded and compared with the last 200 stories, and a near-duplicate is
  rejected before anything is rendered. Gemini's embedding scale is compressed: unrelated stories score ~0.75 and
  reworded copies ~0.90. So the threshold is calibrated per method: 0.88 for Gemini embeddings, 0.80 for the
  offline n-gram method.
- **No factory look.** 5 visual style bibles, 6 narrator voices, 7 story structures, 3 music beds, 3 caption
  styles and 3 end cards rotate, and no dimension repeats the previous video. 7 themes and 7 hook types rotate too.
- **A human directs it:** the daily premise pick, notes and approvals (see above).
- **Original only:** no copyrighted characters, films, brands, real people or religious figures (prompt rules, a
  blocklist and an editor pass). Music is generated in code, or your licensed tracks from `assets/music` (see
  its LICENSE notes).
- **AI disclosure:** captions say "AI-animated story"; YouTube uploads set `containsSyntheticMedia=true`.
- **13+ general audience, not made for kids:** `selfDeclaredMadeForKids=false`. Suspense yes; gore, sexual,
  hateful or dangerous-to-copy content never.
- **Official APIs only.** No bought engagement, follow-for-follow, pods, auto-likes/follows or unofficial
  Instagram APIs.

### What the official APIs cannot do (do these by hand)
- **Pin / heart a YouTube comment.** The bot posts the story question as a comment; pin it in YouTube Studio.
- **Instagram story stickers.** The Story card has the question written on the image instead.
- **Swipe-away rate.** This isn't in the API; the experiment engine uses average % viewed and views per hour.

## How it gets smarter

- **Days 1–30:** explore. Fixed slots, rotation of everything above.
- **Day 31+:** the experiment engine (Thompson sampling, 20% exploration) moves posting slots, video length
  (45–60 s, 60–90 s, and 120–170 s on YouTube once 20 Shorts have data), theme, hook type and voice toward the
  best 72-hour results. That's views per hour, average % viewed, shares and follows per 1,000 views. Max 3 Shorts a
  day; it drops to 1 a day while average % viewed stays under 60% for a week.
- The best hooks and themes are also fed straight into the story writer.

---

## Setup (beginner-friendly, in order)

You need a computer with [GitHub CLI](https://cli.github.com) (`gh auth login` done) and Python 3.11+.

### 1. Free accounts
1. **Instagram:** a Professional (Creator or Business) account.
2. **YouTube:** a channel named *Raaz Kahani* (YouTube Studio → settings → verify your phone to unlock custom
   thumbnails and longer videos).
3. **Meta developer app** (<https://developers.facebook.com>) with the *Instagram API* use case.
4. **Google Cloud project** with **YouTube Data API v3** and **YouTube Analytics API** enabled.
5. **Supabase** project (<https://supabase.com>); the bot creates its public bucket `raaz-kahani` itself.
6. **Telegram bot:** message @BotFather → `/newbot`. Use a bot only this channel uses.
7. **AI keys:** Gemini (<https://aistudio.google.com/apikey>) and optionally Groq (<https://console.groq.com/keys>).

### 2. Keys → GitHub secrets (hidden input, never shown)
```bash
cd raaz-kahani-bot
bash tools/set_secrets.sh      # asks for GEMINI_API_KEY, GROQ_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY,
                               # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, IG_USER_ID, IG_ACCESS_TOKEN ...
```
- **Supabase URL:** Project Settings → API → Project URL (`https://xxxx.supabase.co`, without `/rest/v1`).
  **Key:** Settings → API keys → the *secret* key (`sb_secret_…`), not the publishable one.
- **Telegram chat ID:** open your bot, tap **Start**, send "hi", then open
  `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
- **Instagram:** Meta app → Use cases → Instagram API → *API setup with Unified login* → **Generate token** next to
  the account. The account ID is shown there too. The token lasts 60 days, so record the day you saved it:
  `gh variable set IG_TOKEN_SET --body YYYY-MM-DD` (the bot warns you 10 days before expiry).
- **YouTube:** Google Cloud → OAuth consent screen (External; add yourself as test user; *Publish app*) →
  Credentials → OAuth client ID → *Desktop app* → download the JSON, then:
  ```bash
  python tools/youtube_auth.py client_secret_XXXX.json      # pick the Raaz Kahani channel in the browser
  ```
  It grants upload, comments/playlists and **Analytics** (retention, watch hours) and saves the 3 secrets.
  Uploads from an unverified project stay **private** until Google's free API audit passes: set
  `YT_PRIVACY=private` until then and apply at <https://support.google.com/youtube/contact/yt_api_form>.

### 3. First run
1. **Actions → Pre-flight check → Run workflow.** It tests every service and token and sends a report to Telegram.
2. **Actions → Make videos → Run workflow** with **dry_run = true**. You get a real video in Telegram and as a
   download (`story.json`, `video.mp4`, `cover.jpg`, `caption.txt`, `yt_meta.json`). Nothing is posted.
3. Leave the schedules on. Ideas arrive at 10:00, and videos post at their slots after your ✅.

### Telegram cheat sheet
Buttons do everything. Text commands also work: `/post` (approve the next video), `/postnow`, `/skip`, `/ideas`
(3 new story ideas). After picking an idea, any message you send becomes a note for the writer.

### Variables (Settings → Secrets and variables → Actions → Variables)
| Variable | Default | |
|---|---|---|
| `START_DATE` | `2026-11-15` | launch day: phase 1 = first `REVIEW_DAYS` (30) days |
| `REVIEW_MODE` | `phase` | `phase` · `approve` · `auto_unless_skip` · `auto` |
| `APPROVAL_TIMEOUT` | `true` | in approve mode, post at the slot when you didn't answer |
| `YT_POST_TIMES` / `IG_POST_TIMES` | `08:00,19:30` / `20:30` | phase-1 slots (IST) |
| `LONGFORM_SLOT` | `sun 11:00` | weekly long story |
| `DIRECTOR_TIME` | `10:00` | daily premise options |
| `YT_PRIVACY` | `public` | `private` until the API audit passes |
| `SERIES_EVERY` | `3` | every Nth story is a 2-part cliffhanger |
| `GEMINI_VOICE` / `GEMINI_VOICE_FEMALE` | `Charon` / `Kore` | default voices (6 voices rotate) |
| `TELEGRAM_COMMANDS` | `true` | `false` = notifications only |
| `IMAGE_PROVIDERS` | `gemini,pollinations` | picture sources in order |

Workflow inputs on **Make videos**: `post_now`, `dry_run`, `force_new`, `theme`, `platform` (`yt` / `ig` / `both`).

## External trigger (backup for GitHub's timer)
GitHub's scheduled runs can lag for hours (they didn't start at all on this repository's first day), so a free
**Neon Function** (`tools/neon-trigger/index.mjs`, project `raaz-kahani-trigger`) runs the same schedules and asks
GitHub to start each workflow. It only accepts calls from Neon's own scheduler. A workflow that runs twice is
harmless: every job checks slot locks / "already done" first. It needs `GH_DISPATCH_TOKEN`: a **fine-grained**
GitHub token for this repository only, permission *Actions: Read and write*, set as the function's environment
variable in Neon.

| Neon trigger | Cron (UTC) | Starts |
|---|---|---|
| make-videos | `17 */2 * * *` | Make videos |
| post-queued | `4,24,44 * * * *` | Post queued videos |
| creative-director | `30 4 * * *` | Creative director (10:00 IST) |
| grow-channel | `37 * * * *` | Grow channel |
| weekly-report | `23 15 * * 0` | Weekly report |
| weekly-long-video | `30 14 * * 6` | Weekly long video |

## Run locally
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
brew install ffmpeg fribidi            # macOS (Ubuntu: apt install ffmpeg libfribidi0)
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python tests/offline_test.py   # no keys, no network
```
The offline test renders a full Short and a 2-minute 16:9 long-form sample. It checks resolution, duration,
captions, loudness, the similarity filter, the approval rules, the Telegram flow and the experiment engine.

## Files
See [PLAN.md](PLAN.md) for the full file map and what was reused from
[aiverse-bot](https://github.com/PranavNayee/aiverse-bot).

## Known limits
- **Free pictures are the slow step.** Gemini image models have no free quota on most keys, and Pollinations'
  free tier allows 1 picture per 45 s, so a Short's pictures take ~8–10 min. A free `POLLINATIONS_TOKEN` (secret)
  speeds this up.
- **Character consistency** is good, not perfect: same visual bible and seed in every prompt.
- **Gemini TTS free quota** is limited per day; if it runs out, scheduled runs fall back to the Edge voice and
  manual post-now runs fail instead (`STRICT_VOICE`).
