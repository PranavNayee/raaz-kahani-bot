"""The ONLY code path that publishes. Shared by the generator (main.py) and the poster workflow (post_pending.py).

Guarantees:
  * each queue item (one platform, one slot) posts at its slot time - the job sleeps until then when it is close
  * review rules: approve (✅ Post button; with APPROVAL_TIMEOUT it also posts at the slot when you did not answer),
    auto_unless_skip, auto. 🗑 Skip drops the slot, 🔁 Remake makes a new video for it at the next production run
  * never posts twice: a per-item claim lock + a check of the account's latest posts
  * history is written only after a successful post; every failure sends a clear Telegram message
"""
import os, re, tempfile, time, traceback, requests
import config as C, storage, instagram, notify, slots, youtube, postqueue
from util import log, redact

MAX_ATTEMPTS = 3
NAMES = {"ig": "Instagram", "yt": "YouTube Shorts", "ytlong": "YouTube"}


def due_at(it):
    t = float(it.get("post_at") or it.get("created_ts") or 0)
    if C.review_mode() == "auto_unless_skip":
        t = max(t, float(it.get("created_ts", 0)) + C.REVIEW_HOURS * 3600)
    return t


def _record(it, kind="story", media=None):
    history = storage.get_json("history.json", [])
    history.append({
        "slot": it["id"], "platform": it.get("platform"), "kind": kind, "title": it.get("title"),
        "theme": it.get("theme"), "hook": it.get("hook", ""), "hook_type": it.get("hook_type"),
        "premise": it.get("premise"), "part": it.get("part"), "series_id": it.get("series_id"),
        "series_title": it.get("series_title"), "duration": it.get("duration"), "variety": it.get("variety"),
        "length": it.get("length"), "direction_by": (it.get("direction") or {}).get("by"),
        "approval": it.get("approval"), "story_end": it.get("story_end"), "video_key": it.get("video_key"),
        "cover_t": it.get("cover_t"), "seq": it.get("seq"), "posted_ts": int(time.time()),
        "cta_question": it.get("cta_question"), "moral": it.get("moral"), "summary_hi": it.get("summary_hi"),
        "narrator": it.get("narrator"),
        "media_id": media if it.get("platform") == "ig" else None,
        "youtube_id": media if it.get("platform") in ("yt", "ytlong") else None,
    })
    storage.put_json("history.json", history[-800:])


def _post(it):
    plat = it.get("platform", "ig")
    if plat == "ig":
        mid = instagram.already_posted(it["caption"])
        if mid:
            log("Already on Instagram - not posting again.")
            return mid
        mid = instagram.post_reel(it["video_url"], it["caption"], it.get("cover_url"))
        if it.get("story_url"):
            try:
                instagram.post_story(it["story_url"])
                log("Shared to Instagram Stories")
            except Exception as e:
                log("Story share skipped:", e)
        return mid
    meta = it["yt_meta"]
    vid = youtube.already_posted(meta["snippet"]["title"])
    if vid:
        log("Already on YouTube - not uploading again.")
        return vid
    vid = youtube.upload(it["video_url"], meta)
    postqueue.update(it["id"], done=vid)                     # durable before the optional extras below
    for title, desc in it.get("playlists") or []:
        youtube.add_to_playlist(vid, title, desc)
    if it.get("yt_comment"):
        youtube.comment(vid, it["yt_comment"])
    if it.get("cover_url") and plat == "ytlong":
        try:
            jpg = os.path.join(tempfile.mkdtemp(), "thumb.jpg")
            with open(jpg, "wb") as f:
                f.write(requests.get(it["cover_url"], timeout=60).content)
            youtube.set_thumbnail(vid, jpg)
        except Exception as e:
            log("Thumbnail skipped:", e)
    return vid


def _link(it, mid):
    if it.get("platform") == "ig":
        return "📸 Instagram Reel posted"
    return f"▶️ https://youtube.com/{'shorts/' if it.get('platform') == 'yt' else 'watch?v='}{mid}"


def publish(it, force=False):
    if it.get("attempts", 0) >= MAX_ATTEMPTS and not force:
        return "gave_up"
    it = postqueue.claim(it["id"])
    if not it:
        return "busy"
    if it.get("done"):
        mid = it["done"]
    else:
        try:
            mid = _post(it)
        except Exception:
            err = redact(traceback.format_exc()[-900:]); log(err)
            it["attempts"] = it.get("attempts", 0) + 1
            name = NAMES.get(it.get("platform"), it.get("platform"))
            if it["attempts"] >= MAX_ATTEMPTS:
                storage.put_json(f"failed/{it['id']}.json", it)
                postqueue.delete(it["id"])
                notify.text(f"❌ {name}: giving up on '{it.get('title')}' after {MAX_ATTEMPTS} tries. Nothing was posted "
                            f"for this slot. Data kept as failed/{it['id']}.json.\n\n{err[-600:]}")
            else:
                postqueue.release(it)
                notify.text(f"❌ {name} posting failed ({it['attempts']}/{MAX_ATTEMPTS}): {it.get('title')}\n"
                            f"Nothing was posted. I retry on the next run.\n\n{err[-600:]}")
            return "failed"
    _record(it, media=mid)
    postqueue.delete(it["id"])
    notify.text(f"✅ Posted ({slots.fmt(time.time())}): {it.get('title', '')}\n{_link(it, mid)}")
    return "posted"


def decide(it, force=False):
    """'post' | 'wait' | 'skip' | 'redo' for one queue item right now."""
    a = it.get("approval")
    if a == "skipped":
        return "skip"
    if a == "redo":
        return "redo"
    if force or it.get("done"):
        return "post"
    if time.time() < due_at(it):
        return "wait"
    mode = C.review_mode()
    if mode == "approve" and a not in ("approved", "auto"):
        return "post_timeout" if C.APPROVAL_TIMEOUT else "wait_approval"
    return "post"


def run(force=False, wait=True):
    """Posts every due queue item (sleeping until the next slot when it is close). Returns a status string."""
    import director
    last = "nothing"
    while True:
        try:
            director.poll()                                    # see button taps before deciding
        except Exception as e:
            log("Telegram poll failed (ignored):", e)
        items = [i for i in postqueue.items() if C.PLATFORM in ("both", i.get("platform"))
                 or (C.PLATFORM == "yt" and i.get("platform") == "ytlong")]
        if not items:
            log("Nothing queued.")
            return last
        next_due = None
        for it in items:
            what = decide(it, force)
            if what == "skip":
                _record(it, kind="skip")
                postqueue.delete(it["id"])
                notify.text(f"🗑️ Skipped: {it.get('title', it['id'])} (nothing posted for {it['id']})")
                last = "skipped"
            elif what == "redo":
                postqueue.delete(it["id"])
                notify.text(f"🔁 '{it.get('title')}' discarded - a new video for {slots.fmt(it['post_at'])} is made at "
                            f"the next production run (every 2 hours).")
                last = "redo"
            elif what in ("post", "post_timeout"):
                if what == "post_timeout":
                    it["approval"] = "auto"
                    postqueue.update(it["id"], approval="auto")
                    import director as D
                    D.log_decision("video", it["id"], "auto-posted", by="auto", note="no answer before the slot")
                    notify.edit(it.get("tg_msg"), buttons=[])
                last = publish(it, force)
            elif what == "wait_approval":
                log(f"{it['id']}: waiting for your ✅ Post in Telegram.")
                last = "awaiting_approval"
            else:
                d = due_at(it)
                next_due = d if next_due is None else min(next_due, d)
        if force or not wait or next_due is None:
            return last
        wait_s = next_due - time.time()
        if wait_s > C.MAX_WAIT_MINUTES * 60:
            log(f"Next post {slots.fmt(next_due)} ({wait_s / 3600:.1f} h from now) - not waiting.")
            return last if last != "nothing" else "not_due"
        nap = max(5, min(wait_s, 600))                          # wake every <=10 min to see taps
        log(f"Sleeping {nap / 60:.1f} min - next post at {slots.fmt(next_due)}.")
        time.sleep(nap)
