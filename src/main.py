"""Production run (daily.yml, every 2 hours): makes the video for the earliest platform slot that still needs one
(up to PRODUCE_AHEAD_HOURS before it), queues it and sends the preview with approval buttons to Telegram.

  * YouTube Shorts slots (YT_POST_TIMES) get a fresh, original story every time.
  * The Instagram slot (IG_POST_TIMES) gets the better YouTube Short of the day, re-cut with its own Instagram end
    card, cover, Story card and caption; while YouTube is not connected it gets a fresh story of its own.
  * New stories use the owner's premise pick from the 10:00 creative-director message (director.py).
A dry run renders one video and sends it to Telegram only, leaving story.json, video.mp4, cover.jpg, caption.txt
and yt_meta.json in work/."""
import datetime as dt, json, os, shutil, subprocess, sys, time
import config as C
import story as S, images, tts, voice, render, covers, captions, storage, notify, slots, publisher, postqueue
import variety as V, similarity, youtube, experiments
from util import log, truthy, duration

WORK = os.environ.get("WORK_DIR", "work")
STEP = "start"


def step(name):
    global STEP
    STEP = name
    log(f"▶ {name}")


def have_storage():
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def have_llm():
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY"))


def platforms():
    out = []
    if youtube.enabled() and C.PLATFORM in ("both", "yt"):
        out.append("yt")
    if C.ig_enabled() and C.PLATFORM in ("both", "ig"):
        out.append("ig")
    return out


def plat_history(history, plat):
    return [h for h in history if h.get("platform", "ig") == plat and h.get("kind", "story") == "story"]


def long_link(history):
    lf = [h for h in history if h.get("platform") == "ytlong" and h.get("youtube_id")]
    return f"https://youtu.be/{lf[-1]['youtube_id']}" if lf else None


# ---------------------------------------------------------------- rendering one video
def produce(vs, work=WORK, seq=0, platform="ig", history=()):
    """Renders one 9:16 video story into `work`. Returns paths, metadata and texts."""
    render.set_format("short")
    os.makedirs(work, exist_ok=True)
    vs = dict(vs, platform=platform)
    with open(os.path.join(work, "story.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in vs.items() if not k.startswith("_")}, f, ensure_ascii=False, indent=2)
    step(f"images + voice in parallel ({len(vs['scenes'])} scenes)")
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:      # the voice never waits for the pictures
        f_img = ex.submit(images.make_all, vs, os.path.join(work, "images"))
        f_voice = ex.submit(tts.speak_scenes, vs["scenes"], vs, work)
        voices = f_voice.result()
        log(f"🗣️ voice ready ({tts.LAST}) - waiting for the pictures")
        imgs = f_img.result()
    provs = [i["provider"] for i in imgs]
    max_s = min(C.MAX_SECONDS, float(vs.get("max_seconds") or C.MAX_SECONDS))
    voices = voice.fit_tempo(voices, max_s - render.ENDCARD - render.GAP * len(voices))
    step("render")
    video = os.path.join(work, "video.mp4")
    meta = render.render(vs, imgs, voices, work, video)
    if meta["duration"] > C.MAX_SECONDS + 1:
        raise RuntimeError(f"video is {meta['duration']:.0f}s - longer than MAX_SECONDS={C.MAX_SECONDS:.0f}")
    step("cover")
    cover, ci = covers.make(vs, imgs, os.path.join(work, "cover.jpg"), style=platform)
    story_card = covers.story_card(vs, imgs, os.path.join(work, "story_card.jpg")) if platform == "ig" else None
    st, d = meta["timeline"][ci]
    caption = captions.ig_caption(vs, seq, vs.get("part2_when_roman"))
    ytm = captions.yt_meta(vs, seq, long_link(history), vs.get("part2_when"))
    with open(os.path.join(work, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(caption)
    with open(os.path.join(work, "yt_meta.json"), "w", encoding="utf-8") as f:
        json.dump(ytm, f, ensure_ascii=False, indent=2)
    meta.pop("words_timed", None)
    return {"video": video, "cover": cover, "story_card": story_card, "caption": caption, "yt_meta": ytm,
            "meta": meta, "providers": provs, "voice": tts.LAST, "cover_t": st + d * 0.5, "vs": vs}


# ---------------------------------------------------------------- choosing the story
def new_story(history, plat, dry=False):
    """A brand-new story (or Part 2 of this platform's running series). Returns (video story, part2 or None)."""
    ph = plat_history(history, plat)
    sid = S.pending_part2(ph)
    if sid and not truthy("FORCE_NEW_STORY"):
        p2 = storage.get_json(f"series/{sid}.json", None) if have_storage() else None
        if p2:
            log(f"📺 Part 2 of the series '{p2.get('series_title')}'")
            return p2, None
    if not have_llm():
        if not (dry or C.OFFLINE):
            raise RuntimeError("no AI key: set GEMINI_API_KEY and/or GROQ_API_KEY")
        log("No AI key - using the built-in sample story.")
        return S.make_parts(S.sample())[0], None
    try:
        import growth
        S.WINNERS = growth.winners()
        S.THEME_STATS = growth.theme_scores()
    except Exception as e:
        log("Learning loop skipped:", e)
    import director
    direction = None if dry else director.take_choice()
    ov = experiments.override(plat)
    v = V.pick(ph, ov)
    bank = storage.get_json("premises.json", []) if have_storage() else []
    if not bank:                                              # first run: seed from what was already posted
        bank = [{"premise": h["premise"], "title": h.get("title")} for h in history if h.get("premise")]
    s = S.write(ph, ov.get("theme") or (direction or {}).get("theme"), series=S.is_series_turn(ph),
                length=ov.get("length") or "mid", variety=v, direction=direction, bank=bank, hook_type=ov.get("hook_type"))
    if have_storage() and not dry:
        storage.put_json("premises.json", similarity.remember(bank, s["premise"], s["title"], s.pop("_emb")))
    s.pop("_emb", None)
    parts = S.make_parts(s)
    return parts[0], (parts[1] if len(parts) > 1 else None)


# ---------------------------------------------------------------- Instagram re-cut of the best Short
def best_short_today(history, day):
    """The better YouTube Short of `day` (posted ones by views/hour, else the newest made)."""
    stats = storage.get_json("stats.json", {})
    posted = [h for h in history if h.get("platform") == "yt" and (h.get("slot") or "").startswith(day.isoformat())
              and h.get("video_key")]
    queued = [i for i in postqueue.items() if i.get("platform") == "yt" and i["id"].startswith(day.isoformat())
              and i.get("video_key")]

    def vph(h):
        r = stats.get(h["slot"], {})
        age = max(1.0, (time.time() - (h.get("posted_ts") or time.time())) / 3600)
        return ((r.get("yt") or {}).get("views", 0)) / age
    if posted:
        return max(posted, key=vph)
    return queued[-1] if queued else None


def ig_recut(src, slot, item_id, work, seq, history):
    """Instagram version of a YouTube Short: same story, Instagram end card, cover, Story card and caption."""
    os.makedirs(work, exist_ok=True)
    raw = os.path.join(work, "src.mp4")
    import requests
    with open(raw, "wb") as f:
        f.write(requests.get(storage.public_url(src["video_key"]), timeout=300).content)
    end = float(src["story_end"])
    render.set_format("short")
    from PIL import Image
    fr = os.path.join(work, "last.jpg")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0, end - 0.2):.2f}", "-i", raw, "-frames:v", "1", fr],
                   check=True)
    vs = {k: src.get(k) for k in ("title", "hook", "cta_question", "moral", "part", "theme", "summary_hi")}
    vs.update(platform="ig", scenes=[{"emotion": "shock"}])
    kind = "part2" if src.get("part") == 1 else ((src.get("variety") or {}).get("end") or "cta_a")
    nxt = slots.next_slot("ig", slot)
    vs["part2_when"] = slots.hindi_time(nxt) if nxt else None
    card = render.end_card(Image.open(fr).convert("RGB"), kind, vs)
    cpath = os.path.join(work, "card.png"); card.save(cpath)
    out = os.path.join(work, "video.mp4")
    total = duration(raw)
    fc = (f"[0:v]trim=0:{end:.3f},setpts=PTS-STARTPTS[a];[1:v]fps={render.FPS},format=yuv420p,setsar=1,"
          f"fade=t=in:st=0:d=0.4[b];[a][b]concat=n=2:v=1:a=0[v]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", raw, "-loop", "1", "-t", f"{total - end:.3f}", "-i", cpath,
                    "-filter_complex", fc, "-map", "[v]", "-map", "0:a", *render.ENC, "-c:a", "copy",
                    "-movflags", "+faststart", out], check=True)
    cf = os.path.join(work, "frame.jpg")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{float(src.get('cover_t') or 3):.2f}", "-i", raw,
                    "-frames:v", "1", cf], check=True)
    imgs = [{"path": cf}]
    cover, _ = covers.make(dict(vs, scenes=[{"emotion": "shock"}]), imgs, os.path.join(work, "cover.jpg"), style="ig")
    story_card = covers.story_card(dict(vs, scenes=[{"emotion": "shock"}]), imgs, os.path.join(work, "story_card.jpg"))
    vs["part2_when_roman"] = _roman_when(nxt)
    caption = captions.ig_caption(vs, seq, vs["part2_when_roman"])
    return {"video": out, "cover": cover, "story_card": story_card, "caption": caption, "yt_meta": None,
            "meta": {"duration": total, "story_end": end, "lufs": None}, "providers": ["re-cut"], "voice": "same",
            "cover_t": src.get("cover_t"), "vs": dict(src, **vs)}


def _roman_when(slot):
    if not slot:
        return None
    return f"kal {'raat' if slot.hour >= 20 else 'shaam' if slot.hour >= 16 else 'subah' if slot.hour < 12 else 'dopahar'} " \
           f"{slot.hour % 12 or 12}{':%02d' % slot.minute if slot.minute else ''} baje"


# ---------------------------------------------------------------- one slot, end to end
def make_for_slot(slot, plat, history, item_id, post_now=False):
    seq = len(plat_history(history, plat))
    work = os.path.join(WORK, item_id)
    shutil.rmtree(work, ignore_errors=True)
    out, part2 = None, None
    if plat == "ig" and "yt" in platforms():
        src = best_short_today(history, slot.date())
        ph = plat_history(history, "ig")
        need = S.pending_part2(ph)
        if src and (not need or (src.get("series_id") == need and src.get("part") == 2)) and \
                not (src.get("part") == 2 and need != src.get("series_id")):
            step(f"Instagram re-cut of today's best Short: {src.get('title')}")
            out = ig_recut(src, slot, item_id, work, seq, history)
    if out is None:
        step("story")
        vs, part2 = new_story(history, plat)
        if vs.get("part") == 1:
            nxt = slots.next_slot(plat, slot + dt.timedelta(hours=12))
            vs["part2_when"], vs["part2_when_roman"] = (slots.hindi_time(nxt), _roman_when(nxt)) if nxt else (None, None)
        out = produce(vs, work, seq, plat, history)
    vs, m = out["vs"], out["meta"]
    real = sum(p not in ("placeholder",) for p in out["providers"])
    info = (f"{m['duration']:.0f}s · {len(vs.get('scenes') or [])} scenes · images {real}/{len(out['providers'])} · voice "
            f"{out['voice']} · {m.get('lufs')} LUFS · theme {vs.get('theme')} · style {(vs.get('variety') or {}).get('style')}")
    log("ℹ️", info)

    step("upload to storage")
    stamp = slots.now().strftime("%H%M%S")
    vkey = f"videos/{item_id}-{stamp}.mp4"
    url = storage.upload_file(out["video"], vkey)
    cover_url = story_url = None
    try:
        cover_url = storage.upload_file(out["cover"], f"covers/{item_id}-{stamp}.jpg", "image/jpeg")
        if out.get("story_card"):
            story_url = storage.upload_file(out["story_card"], f"covers/{item_id}-{stamp}-story.jpg", "image/jpeg")
    except Exception as e:
        log("Cover upload failed (posting without cover):", e)
    if part2:
        storage.put_json(f"series/{part2['series_id']}.json", part2)
    for prefix, keep in (("videos", 40), ("covers", 80)):
        try:
            storage.cleanup(prefix, keep=keep)
        except Exception as e:
            log("Cleanup skipped:", e)

    step("queue")
    post_at = time.time() if post_now else slot.timestamp()
    playlists = [("Raaz Kahani Shorts", f"{C.CHANNEL} - roz nayi rahasya aur jaadu bhari Hindi kahaniyan (AI-animated).")]
    if vs.get("series_id"):
        playlists.append((f"{C.CHANNEL}: {vs.get('series_title', '')}"[:150], "Is series ke saare parts ek jagah."))
    item = {
        "id": item_id, "platform": plat, "post_at": int(post_at), "video_url": url, "video_key": vkey,
        "cover_url": cover_url, "story_url": story_url, "caption": out["caption"], "yt_meta": out["yt_meta"],
        "yt_comment": captions.yt_comment(vs) if plat == "yt" else None, "playlists": playlists if plat == "yt" else [],
        "title": vs.get("title"), "theme": vs.get("theme"), "hook": vs.get("hook"), "hook_type": vs.get("hook_type"),
        "premise": vs.get("premise"), "part": vs.get("part"), "series_id": vs.get("series_id"),
        "series_title": vs.get("series_title"), "variety": vs.get("variety"), "length": vs.get("length"),
        "narrator": vs.get("narrator"), "direction": vs.get("direction"), "cta_question": vs.get("cta_question"),
        "moral": vs.get("moral"), "summary_hi": vs.get("summary_hi"), "duration": round(m["duration"], 1),
        "story_end": m.get("story_end"), "cover_t": out.get("cover_t"), "seq": seq,
        "created_ts": int(time.time()), "attempts": 0, "status": "queued", "approval": "pending",
    }
    import director
    mode = C.review_mode()
    when = "right now" if post_at <= time.time() + 60 else slots.fmt(post_at)
    how = {"approve": f"Posts {when} after you tap ✅ Post" +
                      (" (no answer = it posts anyway)." if C.APPROVAL_TIMEOUT else "."),
           "auto_unless_skip": f"Posts {when} unless you tap 🗑 Skip.",
           "auto": f"Posts automatically {when}."}[mode if mode in ("approve", "auto_unless_skip") else "auto"]
    who = {"owner": "your idea", "auto": "bot's pick (no answer)"}.get((vs.get("direction") or {}).get("by"), "bot's idea")
    cap = f"🎬 {NAME.get(plat, plat)} · {vs.get('title')}\n{info}\nStory: {who}\n{how}"
    postqueue.put(item)
    buttons = director.approval_buttons(item_id) if mode != "auto" and C.TELEGRAM_COMMANDS else None
    msg = notify.video_buttons(out["video"], cap, buttons, url) if buttons else notify.video(out["video"], cap, url)
    if msg:
        postqueue.update(item_id, tg_msg=msg)
    return item


NAME = {"yt": "YouTube Short", "ig": "Instagram Reel"}


def dry_run(history):
    plat = (platforms() or ["ig"])[0]
    step("story")
    vs, _ = new_story(history, plat, dry=True)
    out = produce(vs, WORK, len(history), plat, history)
    m = out["meta"]
    info = (f"{m['duration']:.0f}s · voice {out['voice']} · {m['lufs']} LUFS · style {(vs.get('variety') or {}).get('style')}"
            f" · structure {(vs.get('variety') or {}).get('structure')}")
    log(f"🧪 DRY RUN - nothing queued or posted. Files in {WORK}/ - {info}")
    notify.video(out["video"], f"🧪 Preview only (not posted)\n{vs['title']}\n{info}\n\n{out['caption'][:400]}")
    notify.photo(out["cover"], "Cover (the 3:4 centre is what the profile grid shows)")
    return out


def run():
    t0 = time.time()
    dry, post_now, force_new = truthy("DRY_RUN"), truthy("POST_NOW"), truthy("FORCE_NEW")
    os.makedirs(WORK, exist_ok=True)
    step("state")
    history = storage.get_json("history.json", []) if have_storage() else []
    if dry:
        return dry_run(history)
    plats = platforms()
    if not plats:
        raise RuntimeError("no platform connected (Instagram and/or YouTube secrets)")
    queued = {i["id"]: i for i in postqueue.items()}
    done = {h.get("slot") for h in history}
    if post_now:
        plat = plats[0]
        slot = slots.now()
        made = make_for_slot(slot, plat, history, f"{slot.date().isoformat()}-{plat}-now{slot.strftime('%H%M')}", True)
        publisher.run(force=False, wait=True)
        return made
    cands = sorted((s, p) for p in plats for s in slots.upcoming(p, done=done | set(queued)))
    if force_new and not cands:
        nq = [i for i in queued.values() if not i.get("done")]
        if nq:
            postqueue.delete(nq[0]["id"]); log(f"FORCE_NEW: re-making {nq[0]['id']}")
            return run_force(nq[0])
    if not cands:
        log("✅ Every slot in the next hours already has a video.")
        publisher.run(wait=True)
        return None
    slot, plat = cands[0]
    import director
    fresh = not (plat == "ig" and "yt" in plats)
    if fresh and director.waiting() and slot.timestamp() - time.time() > 4 * 3600:
        log("⏳ Waiting for the owner's premise pick (asked less than 3 h ago) - the next run makes the video.")
        publisher.run(wait=True)
        return None
    item_id = slots.key(slot, plat)
    log(f"🗓️ Making the {plat} video for {slots.fmt(slot)} ({item_id}), review mode {C.review_mode()}")
    made = make_for_slot(slot, plat, history, item_id)
    log(f"✅ Video ready in {time.time() - t0:.0f}s.")
    publisher.run(wait=True)
    return made


def run_force(item):
    history = storage.get_json("history.json", [])
    slot = dt.datetime.fromtimestamp(item["post_at"], slots.TZ)
    return make_for_slot(slot, item["platform"], history, item["id"])


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        import traceback
        traceback.print_exc()
        notify.text(f"❌ Video production failed at step '{STEP}' (nothing was posted; the next run in 2 hours retries):\n"
                    f"{str(e)[:800]}")
        sys.exit(1)
