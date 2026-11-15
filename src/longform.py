"""Weekly long-form YouTube video: ONE new, ORIGINAL 8-12 minute story (never a compilation of Shorts), made with
the same pipeline in 16:9, with chapters, its own thumbnail and the "Full Stories" playlist. Long videos build the
4,000 watch-hours path (Shorts views don't count toward it); every Short links to the latest one.

Runs from longform.yml (Saturday evening + backup); queues the video for the LONGFORM_SLOT (Sunday 11:00 IST),
with the same approval buttons as the Shorts."""
import datetime as dt, json, os, re, shutil, sys, time
import config as C
import story as S, images, tts, voice, render, covers, captions, storage, notify, slots, postqueue, publisher
import variety as V, similarity, youtube, llm
from util import log, truthy

WORK = os.environ.get("LONG_WORK_DIR", "work_long")

PROMPT = """You are the head writer of "{channel}", a Hindi storytelling channel. Write ONE brand-new, 100% ORIGINAL,
LONG animated mystery/fantasy story in natural spoken Hindi for an 8-12 minute YouTube video.

THEME: {theme}
STORY SHAPE: {structure}
It is a full story, not a compilation: one world, one hero, one mystery, building over {n_ch} chapters.
- Chapter 1 starts with a shocking hook line (scene 1, max 14 words), then the world and its strange rule.
- Middle chapters: the danger grows, a greedy or cruel antagonist, a false solution that fails, real stakes.
- The second-to-last or last chapter: the clever twist (kindness or wit wins, set up earlier). Mark its scene number
  (counting all scenes from 1) in "twist_scene".
- The last scene: an emotional payoff, a one-line moral and a comment question to the viewer.
LENGTH: {n_ch} chapters, {s_lo}-{s_hi} scenes in total, {w_lo}-{w_hi} words of narration. Every scene 15-35 words.
LANGUAGE: Devanagari Hindi like a dadi telling a story, short sentences, vivid sounds and feelings, no English words.
RULES: completely original; never copyrighted characters, films, cartoons, books, brands, celebrities, real people
or religious figures; 13+: suspense yes, but no gore, blood, weapons, sexual content, hate or dangerous acts.
Never resemble these past stories: {past}
VISUALS: pictures are drawn in this style: {style}. 2-4 characters with fixed English "visual_bible"; each scene's
"image_prompt" (English) describes only that scene, wide cinematic 16:9 framing, no text in the picture.

Return ONLY JSON:
{{"title": "hook line in Roman Hinglish, max 80 chars", "hook": "same hook", "premise": "one English sentence",
  "hook_type": "one of {hook_types}", "narrator": "male or female", "summary_hi": "spoiler-free Devanagari line",
  "moral": "Roman Hinglish moral", "cta_question": "Roman Hinglish question ending with 👇", "twist_scene": 40,
  "characters": [{{"id": "meera", "name": "Meera", "gender": "female", "visual_bible": "..."}}],
  "style_bible": "one English line of palette and mood",
  "chapters": [{{"title_hi": "अध्याय का नाम", "title_roman": "Chapter name in Roman Hinglish",
                 "scenes": [{{"narration_hi": "...", "speaker": "narrator", "image_prompt": "...",
                              "emotion": "one of {emotions}", "seconds": 7}}]}}]}}"""


def sizes(minutes):
    """(chapters, min scenes, max scenes, min words, max words) for a target length."""
    if minutes <= 3:                                    # test sample
        return 2, 10, 16, 200, 330
    return 5, 45, 62, 1250, 1800


def validate(s, bank, minutes=10):
    n_ch, s_lo, s_hi, w_lo, w_hi = sizes(minutes)
    if not isinstance(s, dict) or not isinstance(s.get("chapters"), list) or len(s["chapters"]) < max(2, n_ch - 1):
        raise ValueError(f"need {n_ch} chapters")
    scenes, chapters = [], []
    for ch in s["chapters"]:
        sc = ch.get("scenes") or []
        if not sc:
            raise ValueError("a chapter has no scenes")
        chapters.append((len(scenes), ch.get("title_hi") or ch.get("title_roman") or f"Adhyay {len(chapters) + 1}"))
        scenes += sc
    for i, x in enumerate(scenes):
        x["id"] = i + 1
        x.setdefault("speaker", "narrator"); x.setdefault("seconds", 7)
    flat = dict(s, scenes=scenes)
    flat.pop("chapters", None)
    if not s_lo <= len(scenes) <= s_hi + 4:
        raise ValueError(f"need {s_lo}-{s_hi} scenes in total, got {len(scenes)}")
    words = sum(len(x.get("narration_hi", "").split()) for x in scenes)
    if not w_lo * 0.9 <= words <= w_hi * 1.1:
        raise ValueError(f"story has {words} words - needs {w_lo}-{w_hi}")
    tw = int(s.get("twist_scene") or 0)
    if not len(scenes) // 2 <= tw <= len(scenes) - 1:
        raise ValueError(f"twist_scene must be in the second half (got {tw} of {len(scenes)})")
    for x in scenes:                                     # reuse the Shorts checks (schema, Devanagari, safety)
        t = x.get("narration_hi", "")
        if len(S.DEVANAGARI.findall(t)) < max(5, 0.85 * (len(S.DEVANAGARI.findall(t)) + len(S.LATIN.findall(t)))):
            raise ValueError(f"scene {x['id']} must be Devanagari Hindi")
        if x.get("emotion") not in S.EMOTIONS:
            x["emotion"] = "tense"
    m = S.BANNED.search(json.dumps(flat, ensure_ascii=False))
    if m:
        raise ValueError(f"not allowed: '{m.group(0)}'")
    ok, sim, closest, emb = similarity.check(s["premise"], bank)
    if not ok:
        raise ValueError(f"premise {sim:.2f} similar to an earlier story ({closest[:70]}) - invent something new")
    flat["chapters"] = chapters
    flat["_emb"] = emb
    return flat


def write(history, bank, minutes=10):
    v = V.pick([h for h in history if h.get("platform") == "ytlong"])
    theme = S.pick_theme(history)
    n_ch, s_lo, s_hi, w_lo, w_hi = sizes(minutes)
    past = "; ".join(h["premise"] for h in history if h.get("premise"))[-1500:] or "(none)"
    base = PROMPT.format(channel=C.CHANNEL, theme=S.THEMES.get(theme, theme), structure=V.STRUCTURES[v["structure"]],
                         n_ch=n_ch, s_lo=s_lo, s_hi=s_hi, w_lo=w_lo, w_hi=w_hi, past=past,
                         style=V.STYLES[v["style"]], hook_types=", ".join(S.HOOK_TYPES), emotions=", ".join(S.EMOTIONS))
    prompt, last = base, None
    for attempt in range(4):
        try:
            s = validate(llm.generate_json(prompt, temperature=0.9), bank, minutes)
            s["theme"], s["series"] = theme, False
            s["seed"] = abs(hash(s["premise"])) % 2_000_000_000
            s["mood_line"] = (s.get("style_bible") or "")[:160]
            s = S.finish(s, v, "long")
            s["style_bible"] = s["style_bible"].replace("vertical 9:16 composition", "wide cinematic 16:9 composition")
            s["end_card"] = "long"
            log(f"📖 Long story ({llm.LAST}): {s['title']} - {len(s['scenes'])} scenes, {len(s['chapters'])} chapters")
            return s
        except ValueError as e:
            last = e
            log(f"  long story attempt {attempt + 1} invalid: {e}")
            prompt = base + f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: {e}\nFix that and return the full JSON again."
    raise RuntimeError(f"could not write a valid long story: {last}")


def produce(ls, work):
    """16:9 render with chapters. Returns paths + chapter list for the description."""
    render.set_format("long")
    try:
        os.makedirs(work, exist_ok=True)
        imgs = images.make_all(ls, os.path.join(work, "images"))
        voices = []
        bounds = [i for i, _ in ls["chapters"]] + [len(ls["scenes"])]
        for k in range(len(ls["chapters"])):                     # one voice request per chapter
            part = ls["scenes"][bounds[k]:bounds[k + 1]]
            sub = os.path.join(work, f"ch{k + 1}")
            os.makedirs(sub, exist_ok=True)
            voices += tts.speak_scenes(part, ls, sub)
        voices = voice.fit_tempo(voices, 12.5 * 60)
        video = os.path.join(work, "video.mp4")
        meta = render.render(dict(ls, part=None), imgs, voices, work, video, chapters=ls["chapters"])
        tl = meta["timeline"]
        chapters = [(0 if k == 0 else tl[i][0], title) for k, (i, title) in enumerate(ls["chapters"])]
        thumb = covers.long_thumb(ls, imgs[S.dramatic_scene(ls["scenes"])]["path"], os.path.join(work, "thumb.jpg"))
    finally:
        render.set_format("short")
    return {"video": video, "thumb": thumb, "meta": meta, "chapters": chapters}


def sample_long():
    """A 2-chapter, ~2-minute long-form sample from the built-in story (offline test)."""
    s = S.sample()
    ls = dict(s, chapters=[(0, "अध्याय 1: गाता हुआ कुआँ"), (7, "अध्याय 2: राज़")], end_card="long")
    ls["style_bible"] = ls["style_bible"].replace("vertical 9:16 composition", "wide cinematic 16:9 composition")
    return ls


def run():
    if not youtube.enabled():
        log("YouTube is not connected - no long-form video this week.")
        return None
    slot = slots.next_slot("ytlong", slots.now() - dt.timedelta(hours=2))
    if not slot or slot - slots.now() > dt.timedelta(hours=40):
        log("The long-form slot is more than 40 h away - nothing to do yet.")
        return None
    item_id = slots.key(slot, "ytlong")
    history = storage.get_json("history.json", [])
    if any(h.get("slot") == item_id for h in history) or postqueue.get(item_id):
        log(f"{item_id} is already made.")
        return None
    bank = storage.get_json("premises.json", [])
    ls = write(history, bank)
    storage.put_json("premises.json", similarity.remember(bank, ls["premise"], ls["title"], ls.pop("_emb")))
    shutil.rmtree(WORK, ignore_errors=True)
    out = produce(ls, WORK)
    meta = captions.long_meta(ls, out["chapters"])
    stamp = slots.now().strftime("%H%M%S")
    vkey = f"videos/{item_id}-{stamp}.mp4"
    url = storage.upload_file(out["video"], vkey)
    thumb_url = storage.upload_file(out["thumb"], f"covers/{item_id}-{stamp}.jpg", "image/jpeg")
    item = {"id": item_id, "platform": "ytlong", "post_at": int(slot.timestamp()), "video_url": url, "video_key": vkey,
            "cover_url": thumb_url, "yt_meta": meta, "yt_comment": captions.yt_comment(ls),
            "playlists": [("Full Stories", f"{C.CHANNEL} - poori, lambi, original Hindi kahaniyan (AI-animated).")],
            "title": ls["title"], "theme": ls.get("theme"), "hook": ls.get("hook"), "premise": ls["premise"],
            "variety": ls.get("variety"), "length": "longform", "duration": round(out["meta"]["duration"], 1),
            "created_ts": int(time.time()), "attempts": 0, "status": "queued", "approval": "pending"}
    postqueue.put(item)
    import director
    cap = (f"🎬 Weekly long story ({out['meta']['duration'] / 60:.1f} min, {len(out['chapters'])} chapters): {ls['title']}\n"
           f"Posts {slots.fmt(slot)} after you tap ✅ (no answer = posts anyway).")
    msg = notify.video_buttons(out["video"], cap, director.approval_buttons(item_id), url)
    if msg:
        postqueue.update(item_id, tg_msg=msg)
    return item


if __name__ == "__main__":
    try:
        run()
        publisher.run(wait=False)
    except Exception as e:
        import traceback; traceback.print_exc()
        notify.text(f"❌ Weekly long video failed (nothing posted): {str(e)[:700]}")
        sys.exit(1)
