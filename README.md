# Raaz Kahani bot 🌙

Every day this bot writes, illustrates, voices, edits and publishes **one original animated Hindi
mystery/fantasy story** (60–150 s, vertical 1080×1920) to **Instagram Reels** and **YouTube Shorts**,
on free tiers only, from GitHub Actions.

```
story.py ─▶ images.py ─▶ voice.py ─▶ render.py (+ soundtrack.py) ─▶ covers.py ─▶ captions.py
   │                                                                              │
   └── growth.py (stats, winning hooks/themes) ◀── engage.yml        main.py queues ▼
                                                                 publisher.py ─▶ Instagram + YouTube
```

| Step | Module | What it does |
|---|---|---|
| Story | `src/story.py` | LLM (Gemini → Groq fallback) writes strict JSON, validated by a schema + content checks, retried with the error fed back. 2 candidates; a second LLM pass scores hook / twist / clarity and keeps the best. Themes rotate (best performers favoured), premises never repeat, every 3rd story is a 2-part cliffhanger. |
| Images | `src/images.py` | 1080×1920 per scene: Gemini image models (first good frame passed back as a character/style reference) → Pollinations (same seed) → drawn placeholder. Channel style bible + every character's visual bible prepended to every prompt, safety word filter, 3 tries, a failed scene reuses the previous good frame. |
| Voice | `src/tts.py`, `src/voice.py` | **Gemini TTS** (free with `GEMINI_API_KEY`): voice *Charon* (male) or *Kore* (female-led stories), directed as a Hindi horror storyteller, the whole video in ONE request, sped up 1.1×. ElevenLabs if `ELEVENLABS_API_KEY` is set. Edge `hi-IN-MadhurNeural` is the free fallback for scheduled runs; manual "post now" runs refuse it (`STRICT_VOICE`). |
| Sound | `src/soundtrack.py` | Mystery music bed (or your tracks in `assets/music`) ducked under the voice, plus generated royalty-free effects chosen per scene: night crickets, wind, rain, heartbeat on tense scenes, a riser into the twist, impacts, whooshes on cuts, chimes on happy moments, bells, thunder, footsteps and door creaks from the scene's words. 2-pass loudness to −14 LUFS. |
| Edit | `src/render.py` | 30 fps Ken Burns, beat zoom-punch on every cut, camera shake on shocks, floating fireflies / dust / sparkles / rain, drifting fog and lantern flicker in eerie scenes, a flash on the twist, big hook text in the first 2 s, pop-in word-by-word karaoke captions in Devanagari inside the safe area, "Part 2 kal!" end card. Rendered in parallel chunks on every CPU core (`RENDER_WORKERS`), H.264 + AAC, faststart. |
| Cover | `src/covers.py` | Most dramatic frame + title, kept inside the 3:4 profile-grid crop. |
| Post | `src/publisher.py` | Instagram Graph API Reels (video from a public Supabase bucket) + YouTube Data API v3 resumable upload, playlists. |
| Grow | `src/growth.py`, `src/weekly.py` | Insights from both platforms, Hindi replies to genuine comments (max 12/run), weekly Telegram report; winners feed back into the story writer. |

The code style, config pattern, scheduling, slot lock, storage and Telegram flow are adapted from
[aiverse-bot](https://github.com/PranavNayee/aiverse-bot).

## Content rules (built in)

- **100% original** stories and characters. The prompt forbids copyrighted characters, films, cartoons,
  brands, celebrities and real people, and forbids retelling any movie (no "movie recap" content). A
  blocklist in `story.py` rejects known names, and the editor pass rejects anything unoriginal.
- **General audience 13+**: spooky and suspenseful, never gory, sexual, hateful or dangerous to copy
  (prompt rules + word filters on the story and the image prompts + the editor's `safe` flag).
- **Not made for kids.** The stories are for a general 13+ audience, so every YouTube upload sets
  `status.selfDeclaredMadeForKids = false`. If you change the channel to target children, you must change
  this (and YouTube then turns off comments and personalised ads).
- **AI disclosure everywhere**: every Instagram caption and YouTube description says the video is
  AI-generated, and every YouTube upload sets `status.containsSyntheticMedia = true`.
- **Official APIs only**: Instagram Graph API and YouTube Data API v3. No instagrapi/InstaPy/private APIs,
  no auto-likes, no auto-follows, no fake engagement. Comment replies go only to genuine comments.
- **Free tiers only**: Groq, Gemini, Pollinations, edge-tts, Supabase free, GitHub Actions.
- **Secrets only in GitHub Secrets.** Tokens are redacted from logs and Telegram messages.

---

## Setup, in order

### 1. Accounts (all free)

1. **Instagram**: a **Professional** account (Business or Creator) linked to a **Facebook Page**.
2. **YouTube**: a channel named **Raaz Kahani** on a Google account.
3. **Meta developer app** at <https://developers.facebook.com> (type *Business*) with the Instagram Graph API.
4. **Google Cloud project** with **YouTube Data API v3** enabled.
5. **Supabase** project at <https://supabase.com>. Under Storage, create a bucket named `raaz-kahani` and make
   it **Public** (Instagram downloads the video from its public URL).
6. **Telegram bot**: talk to @BotFather → `/newbot` → keep the token. Send your bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat id.
7. **AI keys**: Gemini at <https://aistudio.google.com/apikey> and/or Groq at <https://console.groq.com/keys>.

### 2. Tokens

**Instagram (`IG_USER_ID`, `IG_ACCESS_TOKEN`)**
1. In the [Graph API Explorer](https://developers.facebook.com/tools/explorer) pick your app and generate a user
   token with: `instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`,
   `instagram_manage_comments`, `pages_show_list`, `pages_read_engagement`.
2. Exchange it for a long-lived (60-day) token:
   ```bash
   curl "https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN"
   ```
3. Your IG user id: `GET /me/accounts?fields=instagram_business_account` in the Explorer.
4. The token lasts **60 days**. `check.py` reports the days left, and the daily run warns in Telegram 10 days
   before expiry. Repeat steps 1–2 to renew it.
5. Alternative without a Facebook Page: in your Meta app → Use cases → Instagram API → *API setup with Unified
   login* → *Generate token* next to the account. Both token kinds work: `EAA…` tokens use graph.facebook.com,
   `IG…` tokens use graph.instagram.com (picked automatically, or force it with the variable `IG_GRAPH_HOST`).
6. When Meta won't report a token's expiry, the bot estimates it from the variable **`IG_TOKEN_SET`**
   (`YYYY-MM-DD`, the day you saved the token). Update it whenever you save a new token:
   `gh variable set IG_TOKEN_SET --body 2026-11-15`.

**YouTube (`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`)**
1. Google Cloud → OAuth consent screen (External, add yourself as a test user, then **Publish app**) →
   Credentials → *Create OAuth client ID* → type **Desktop app** → download the JSON.
2. On your computer (with the [GitHub CLI](https://cli.github.com) logged in to this repo):
   ```bash
   python tools/youtube_auth.py client_secret_XXXX.json
   ```
   Pick the **Raaz Kahani** channel in the browser. The script saves all three secrets with `gh secret set`;
   the refresh token is never printed.
3. ⚠️ Google keeps uploads from an **unverified** API project **private** until the project passes the free
   [YouTube API compliance audit](https://support.google.com/youtube/contact/yt_api_form). Until then set the
   variable `YT_PRIVACY=private` and publish from YouTube Studio, or apply for the audit straight away.

### 3. GitHub secrets and variables

Settings → Secrets and variables → Actions.

| Secret | |
|---|---|
| `GEMINI_API_KEY`, `GROQ_API_KEY` | at least one; both recommended (fallback) |
| `IG_USER_ID`, `IG_ACCESS_TOKEN` | Instagram (optional if you only post to YouTube) |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_BUCKET` | `SUPABASE_BUCKET` defaults to `raaz-kahani` |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | YouTube (optional if you only post to Instagram) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | previews, approvals, errors, reports |
| `POLLINATIONS_TOKEN` | optional: a free Pollinations token gives higher limits and no logo |

| Variable | Default | |
|---|---|---|
| `POST_TIMES` | `19:00` | comma-separated daily slots, 24 h, in `TIMEZONE` |
| `TIMEZONE` | `Asia/Kolkata` | |
| `REVIEW_MODE` | `auto_unless_skip` | `auto` · `auto_unless_skip` (posts unless you `/skip`) · `approve` (needs `/post`) |
| `REVIEW_HOURS` | `2` | minimum time you get to `/skip` |
| `YT_PRIVACY` | `public` | `private` until the API audit passes |
| `SERIES_EVERY` | `3` | every Nth story is a 2-part series (`0` = never) |
| `MAX_SECONDS` | `150` | narration is sped up (max 1.25×) to fit |
| `IMAGE_PROVIDERS` | `gemini,pollinations` | order of image providers |
| `GEMINI_VOICE` / `GEMINI_VOICE_FEMALE` | `Charon` / `Kore` | Gemini TTS voices (Puck, Fenrir, Orus, Aoede, Leda, Zephyr...) |
| `VOICE_SPEED` | `1.1` | narration speed-up, pitch kept |
| `TELEGRAM_COMMANDS` | `true` | `false` = notifications only (when the Telegram bot is shared with another bot) |
| `STYLE_BIBLE` | 3D animated film look… | the channel's fixed visual style |

### 4. First run

1. **Actions → Pre-flight check → Run workflow.** It runs the offline test, then checks every service and token
   (AI writer, images, Telegram, Supabase public URL, voice, Hindi text shaping, Instagram token days left and
   permissions, YouTube channel) and sends the report to Telegram. Fix anything marked ❌.
2. **Actions → Daily Raaz Kahani video → Run workflow** with **dry_run = true**. It writes a real story, renders
   it, sends the preview and cover to Telegram and attaches `story.json`, `video.mp4`, `cover.jpg`,
   `caption.txt` and `yt_meta.json` as the `dry-run` artifact. Nothing is queued or posted.
3. Happy with it? Leave the schedules on. Tomorrow's video is made around 23:07 IST and posts at 19:00 IST.

`workflow_dispatch` inputs on the daily workflow: `post_now`, `dry_run`, `force_new`, `theme`.

---

## How the schedule works

- `daily.yml` makes the video **the night before** (23:07 IST) and queues it in Supabase (`pending.json`).
  Five backup crons cover GitHub's late or skipped runs. Each run checks history and the queue first, so
  only one video is ever made per slot.
- `post.yml` runs every 20 minutes. A run that starts within 5.5 h of the slot sleeps and posts **on time**.
- **Telegram**: `/post` approve · `/postnow` post immediately · `/skip` discard (before anything is live).
- `engage.yml` (hourly): stats + up to 12 Hindi replies per run. `weekly.yml` (Sunday 20:53 IST): report.

### Never posting twice

1. A claim lock in `pending.json` stops two jobs from publishing together.
2. Progress is saved **per platform** the moment it succeeds. If YouTube fails after Instagram posted, the
   retry uploads **only** to YouTube.
3. Before posting, each platform's latest posts are checked for the same caption / title.
4. History is written only after posting, so a failed day never burns a story or a series part.

A post cannot be made atomic across two different platforms. If one platform keeps failing (3 attempts),
Telegram says exactly what is live and what is not, the story is recorded as posted on the platform that
worked (so Part 2 still follows), and the video data is kept as `failed/<slot>.json` for a manual upload.
Every failed step sends a Telegram message naming the step.

---

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
brew install ffmpeg fribidi            # macOS (Ubuntu: apt install ffmpeg libfribidi0)

# offline acceptance test: full sample video, no keys, no network
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python tests/offline_test.py

# dry run with real AI (set keys in your shell first); files land in work/
DRY_RUN=true PYTHONPATH=src DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python src/main.py
```

Hindi captions need Pillow's **libraqm** shaping, which loads **libfribidi** at runtime. Without it, matras
render in the wrong place, so production runs refuse to render (`REQUIRE_RAQM`). On macOS, Homebrew's lib folder
must be on `DYLD_FALLBACK_LIBRARY_PATH`.

## Files

```
src/          config, util, llm, story, images, voice, soundtrack, render, covers, captions,
              storage, notify, instagram, youtube, publisher, slots, main, post_pending, growth, weekly, check
tests/        offline_test.py
tools/        youtube_auth.py
assets/fonts  Noto Sans Devanagari Bold + Noto Sans Bold (SIL Open Font License, OFL.txt)
assets/music  your royalty-free tracks (optional; a mystery bed is synthesised otherwise)
.github/      workflows: daily, post, engage, weekly, check · setup-media.sh
```

Supabase keeps: `history.json` (posted videos, premises, themes), `pending.json` (the queue),
`series/<id>.json` (Part 2 waiting), `stats.json`, `engage.json`, `videos/`, `covers/`.

## Known limits

- **Gemini image models** may have little or no free quota on your key. The bot then falls back to
  Pollinations by itself (and stops trying Gemini for the rest of that run).
- **Pollinations'** anonymous tier adds a small logo in one corner. The bot requests a taller image and crops
  that strip off; a free `POLLINATIONS_TOKEN` avoids it.
- Character consistency across scenes is good, not perfect: same visual bible, same seed, and (with Gemini)
  the first frame as a reference.
- YouTube custom thumbnails on Shorts are not always applied; the bot tries, and failures are harmless.
