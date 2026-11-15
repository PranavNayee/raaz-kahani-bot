"""Daily generator: story -> images -> voice -> render -> cover -> captions -> queue (-> publisher posts at the slot).

Runs the evening BEFORE the posting day (daily.yml) plus several backup crons; every run after the first sees the
video is already made/posted and exits in seconds. A dry run renders and sends the preview to Telegram only and
leaves story.json, video.mp4, cover.jpg, caption.txt and yt_meta.json in work/."""
import json, os, shutil, sys, time
import config as C
import story as S, images, voice, render, covers, captions, storage, notify, slots, publisher
from util import log, truthy

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


def produce(vs, work=WORK, seq=0):
    """Renders one video story into `work`. Returns a dict with paths, metadata and texts."""
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "story.json"), "w", encoding="utf-8") as f:
        json.dump(vs, f, ensure_ascii=False, indent=2)
    step(f"images ({len(vs['scenes'])} scenes)")
    imgs = images.make_all(vs, os.path.join(work, "images"))
    provs = [i["provider"] for i in imgs]
    step("voice")
    voices = voice.speak_scenes(vs["scenes"], vs, work)
    overhead = render.ENDCARD + render.GAP * len(voices)
    voices = voice.fit_tempo(voices, C.MAX_SECONDS - overhead)
    step("render")
    video = os.path.join(work, "video.mp4")
    meta = render.render(vs, imgs, voices, work, video)
    if meta["duration"] > C.MAX_SECONDS + 1:
        raise RuntimeError(f"video is {meta['duration']:.0f}s - longer than MAX_SECONDS={C.MAX_SECONDS:.0f}")
    step("cover")
    cover, cover_scene = covers.make(vs, imgs, os.path.join(work, "cover.jpg"))
    caption = captions.ig_caption(vs, seq)
    ytm = captions.yt_meta(vs, seq)
    with open(os.path.join(work, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(caption)
    with open(os.path.join(work, "yt_meta.json"), "w", encoding="utf-8") as f:
        json.dump(ytm, f, ensure_ascii=False, indent=2)
    meta.pop("words_timed", None)
    return {"video": video, "cover": cover, "cover_scene": cover_scene, "caption": caption, "yt_meta": ytm,
            "meta": meta, "providers": provs, "voice": voice.LAST}


def choose(history, dry):
    """What to make: Part 2 of yesterday's series, or a new story (every SERIES_EVERY-th one is a series)."""
    sid = S.pending_part2(history)
    if sid and not truthy("FORCE_NEW_STORY"):
        try:
            p2 = storage.get_json(f"series/{sid}.json", None)
        except Exception as e:
            p2 = None; log("Series file unreadable:", e)
        if p2:
            log(f"📺 Part 2 of the series '{p2.get('series_title')}'")
            return p2, None
        log("Part 2 data missing - making a new story instead.")
    if not have_llm():
        if not (dry or C.OFFLINE):
            raise RuntimeError("no AI key: set GEMINI_API_KEY and/or GROQ_API_KEY")
        log("No AI key - using the built-in sample story (dry run).")
        s = S.sample()
    else:
        try:
            import growth
            S.WINNERS = growth.winners()
            S.THEME_STATS = growth.theme_scores()
            if S.WINNERS:
                log("🧠 Learning from best hooks:", S.WINNERS[:200])
        except Exception as e:
            log("Learning loop skipped:", e)
        s = S.write(history, S.pick_theme(history), series=S.is_series_turn(history))
    parts = S.make_parts(s)
    return parts[0], (parts[1] if len(parts) > 1 else None)


def run():
    t0 = time.time()
    force_new, post_now, dry = truthy("FORCE_NEW"), truthy("POST_NOW"), truthy("DRY_RUN")
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    step("state")
    history = storage.get_json("history.json", []) if have_storage() else []
    pending = storage.get_json("pending.json", None) if have_storage() and not dry else None
    if post_now:
        slot = slots.now(); key = slot.strftime("%Y-%m-%d-now%H%M")
    else:
        slot = slots.upcoming_slot(done={h.get("slot") for h in history})
        key = slots.key(slot)
    log(f"🗓️ Target slot: {slots.fmt(slot)} (key {key}, review mode: {C.REVIEW_MODE}{', DRY RUN' if dry else ''})")
    if not dry:
        if pending and pending.get("done") and any(pending["done"].values()):
            log("A video is half-posted - finishing it before making anything new.")
            publisher.run(wait=True); return
        if (pending and not post_now and not force_new and pending.get("slot") != key
                and time.time() < float(pending.get("post_at", 0)) + 3 * 3600):
            log(f"✅ An earlier video ({pending.get('slot')}) is still queued - handing over to the poster first.")
            publisher.run(wait=True); return
        if not force_new and not post_now:
            if any(h.get("slot") == key for h in history):
                log(f"✅ The {key} video is already posted - nothing to do."); return
            if pending and pending.get("slot") == key:
                log(f"✅ The {key} video is already made and queued - handing over to the poster.")
                publisher.run(wait=True); return
        if pending and pending.get("slot") != key:
            notify.text(f"ℹ️ The video for {pending.get('slot')} was never posted; replacing it with the {key} video.")

    step("story")
    vs, part2 = choose(history, dry)
    seq = len(history)
    out = produce(vs, WORK, seq)
    m = out["meta"]
    real = sum(p != "placeholder" for p in out["providers"])
    info = (f"{m['duration']:.0f}s · {len(vs['scenes'])} scenes · images {real}/{len(out['providers'])} AI "
            f"({', '.join(sorted(set(out['providers'])))}) · voice {out['voice']} · {m['lufs']} LUFS · theme {vs.get('theme')}")
    warn = "" if m["duration"] >= C.MIN_SECONDS else f"\n⚠️ Shorter than {C.MIN_SECONDS:.0f}s."
    log("ℹ️", info)

    if dry:
        log(f"🧪 DRY RUN - nothing queued or posted. Files in {WORK}/")
        notify.video(out["video"], f"🧪 Preview only (not posted)\n{vs['title']}\n{info}{warn}\n\n{out['caption'][:500]}")
        notify.photo(out["cover"], "Cover (the 3:4 centre is what the profile grid shows)")
        return out

    step("upload to storage")
    stamp = slots.now().strftime("%H%M%S")
    url = storage.upload_file(out["video"], f"videos/{key}-{stamp}.mp4")
    cover_url = None
    try:
        cover_url = storage.upload_file(out["cover"], f"covers/{key}-{stamp}.jpg", "image/jpeg")
    except Exception as e:
        log("Cover upload failed (posting without cover):", e)
    if part2:
        storage.put_json(f"series/{part2['series_id']}.json", part2)

    step("queue")
    post_at = time.time() if post_now else slot.timestamp()
    storage.put_json("pending.json", {
        "slot": key, "post_at": int(post_at), "video_url": url, "cover_url": cover_url,
        "caption": out["caption"], "yt_meta": out["yt_meta"], "platforms": publisher.targets(),
        "title": vs["title"], "theme": vs.get("theme"), "hook": vs.get("hook"), "hook_type": vs.get("hook_type"),
        "premise": vs.get("premise"), "part": vs.get("part"), "series_id": vs.get("series_id"),
        "series_title": vs.get("series_title"), "duration": round(m["duration"], 1), "seq": seq,
        "created_ts": int(time.time()), "attempts": 0, "status": "queued", "done": {},
    })
    for prefix, keep in (("videos", 20), ("covers", 30)):
        try:
            storage.cleanup(prefix, keep=keep)
        except Exception as e:
            log("Cleanup skipped:", e)
    when = "right now" if post_now or post_at <= time.time() else slots.fmt(post_at)
    how = {"auto": f"It will post automatically {when}.",
           "auto_unless_skip": f"It will post {when} unless you reply /skip.",
           "approve": f"Reply /post to publish it at {when} (or /postnow to publish immediately, /skip to discard)."}
    if not C.TELEGRAM_COMMANDS:
        how = dict.fromkeys(how, f"It will post automatically {when} (Telegram commands are off for this bot).")
    notify.video(out["video"], f"🎬 {vs['title']}\n{info}{warn}\n{how.get(C.REVIEW_MODE, how['approve'])}", url)
    _token_warning()
    log(f"✅ Video ready in {time.time() - t0:.0f}s. Queued for {when}.")
    publisher.run(force=post_now, wait=True)
    log(f"✅ Done in {(time.time() - t0) / 60:.1f} min")
    return out


def _token_warning():
    if not C.ig_enabled():
        return
    try:
        import instagram
        days = instagram.token_days_left()
        if days is not None and days < 10:
            notify.text(f"⚠️ Your Instagram access token expires in {days:.0f} days. Renew it (README) or posting stops.")
    except Exception as e:
        log("Token check skipped:", e)


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        import traceback
        traceback.print_exc()
        notify.text(f"❌ Video generation failed at step '{STEP}' (nothing was posted; a backup run will retry):\n"
                    f"{str(e)[:800]}")
        sys.exit(1)
