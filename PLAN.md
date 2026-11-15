# Raaz Kahani: growth-first plan

The bot already posts daily to Instagram (since 2026-11-15). This plan covers the upgrade to the
growth-first brief: monetization safety, a human creative director, per-platform schedules, a weekly
long-form story, an experiment engine and threshold tracking.

## File map

| File | Status | What it does / what is reused from aiverse-bot |
|---|---|---|
| `src/llm.py` | reused as is | Gemini → Groq JSON with model discovery and retries |
| `src/slots.py` | adapted | slot maths, now per platform (`YT_POST_TIMES`, `IG_POST_TIMES`) |
| `src/storage.py` | reused + `ensure_bucket` | Supabase public bucket: state JSON, videos, covers |
| `src/notify.py` | adapted | Telegram text/video/photo + **inline buttons** and update polling |
| `src/instagram.py` | adapted | Graph API Reels + **Stories**; both token kinds |
| `src/youtube.py` | adapted | Data API upload, playlists, story-question comment, **Analytics API** |
| `src/tts.py` | reused (reference tts.py) | Gemini TTS whole-video voice, ElevenLabs, Edge fallback |
| `src/voice.py` | reused | Edge voices + word timings + alignment |
| `src/soundtrack.py` | adapted | music beds (rotating), generated SFX, ducking, −14 LUFS |
| `src/images.py` | new (reference has none) | Gemini image → Pollinations (paced) → placeholder; 9:16 and 16:9 |
| `src/story.py` | extended | schema, 2 candidates + judge (hook, **curiosity gap**, twist, clarity), **6 structures**, **4 style bibles**, director premise, series |
| `src/similarity.py` | **new** | premise embeddings (Gemini embeddings; offline n-gram fallback), reject cosine > 0.80 vs last 200 |
| `src/variety.py` | **new** | rotates style bible, narrator voice, structure, caption style, end card, music bed; never repeats yesterday's |
| `src/director.py` | **new** | 10:00 IST: 3 premise options with buttons (pick / regenerate / my own idea + note); video approval buttons; decision log |
| `src/render.py` | extended | 9:16 Shorts and 16:9 long-form, caption styles, animations, parallel chunks |
| `src/covers.py`, `src/captions.py` | extended | platform-specific covers, captions, YT titles ≤70 visible chars, related-video link |
| `src/postqueue.py` | **new** | one file per scheduled post (`queue/<id>.json`): platform, slot, approval, per-item lock |
| `src/main.py` | rewritten | produces the next missing video for any platform slot, IG re-cut of the best Short |
| `src/publisher.py` | rewritten | posts due queue items per platform, approval rules, never twice |
| `src/longform.py` | **new** | weekly ORIGINAL 8–12 min 16:9 story, chapters, thumbnail, "Full Stories" playlist |
| `src/experiments.py` | **new** | Thompson sampling over slot, length, theme, hook type, voice (phase 2, 20% exploration), quality cap |
| `src/growth.py` | extended | IG insights + YT Analytics into stats.json, Hindi replies (never the same reply twice) |
| `src/weekly.py` | extended | report: totals, best/worst, YPP / IG threshold progress + projected dates, A/B results, token expiry |
| `tests/offline_test.py` | extended | full Short + 2-min 16:9 long-form sample, loudness, captions, similarity filter, queue/approval |

## What is NOT possible through official APIs (handled honestly)
- **Pinning / hearting YouTube comments**: the Data API has no endpoint. The bot posts the story question as
  a comment; pin it in YouTube Studio (one tap).
- **Instagram story stickers**: the API cannot add a question sticker. The bot posts a Story image with the
  question written on it.
- **Swipe-away rate**: not in the YouTube Analytics API; the engine uses average % viewed and views/hour.

## Schedule (Phase 1, days 1–30, configurable)
YouTube Shorts 08:00 + 19:30 IST · Instagram Reel 20:30 IST (the better Short of the day with its own end
card, cover and caption; a fresh video while YouTube is not connected) · YouTube long-form Sunday 11:00 IST ·
creative director 10:00 IST. Phase 2 (day 31+): the experiment engine moves slots/lengths/themes/hooks/voices
toward the best 72-hour results, max 3 Shorts/day, 1/day if average % viewed < 60% for a week.
