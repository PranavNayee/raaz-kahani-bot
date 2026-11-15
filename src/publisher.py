"""The ONLY code path that publishes. Shared by the generator (main.py) and the poster workflow (post_pending.py).

Guarantees:
  * posts at the slot time (sleeps until it if the job started early - GitHub cron is often late or skipped)
  * never posts twice: a storage claim-lock + a check of each account's latest posts
  * per-platform progress is saved the moment a platform succeeds, so a retry only does the platform that is
    still missing (Instagram and YouTube are never re-posted)
  * history is written only after the posts went out, so a failed/skipped day never burns a story or series part
  * every failure sends a clear Telegram message saying what is posted, what is not, and what happens next
"""
import os, tempfile, time, uuid, traceback, requests
import config as C, storage, instagram, notify, slots, youtube
from util import log, redact

MAX_ATTEMPTS = 3
CLAIM_TTL = 20 * 60
NAMES = {"ig": "Instagram", "yt": "YouTube"}


def targets():
    out = []
    if C.ig_enabled():
        out.append("ig")
    if youtube.enabled():
        out.append("yt")
    return out


def due_at(p):
    t = float(p.get("post_at") or p.get("created_ts") or 0)
    if C.REVIEW_MODE == "auto_unless_skip":
        t = max(t, float(p.get("created_ts", 0)) + C.REVIEW_HOURS * 3600)
    return t


def _command(p):
    try:
        return notify.latest_command(int(p.get("created_ts", 0)))
    except Exception as e:
        log("Telegram command check failed (ignored):", e)
        return None


def _record(p):
    history = storage.get_json("history.json", [])
    done = p.get("done", {})
    history.append({
        "slot": p.get("slot"), "kind": "story", "title": p.get("title"), "theme": p.get("theme"),
        "hook": p.get("hook", ""), "hook_type": p.get("hook_type"), "premise": p.get("premise"),
        "part": p.get("part"), "series_id": p.get("series_id"), "series_title": p.get("series_title"),
        "duration": p.get("duration"), "media_id": done.get("ig"), "youtube_id": done.get("yt"),
        "posted_ts": int(time.time()), "seq": p.get("seq"),
    })
    storage.put_json("history.json", history[-500:])


def _claim(slot_key):
    """Optimistic lock in Supabase so two overlapping jobs can't both publish."""
    cur = storage.get_json("pending.json", None)
    if not cur or cur.get("slot") != slot_key:
        log("Pending video changed or already handled by another job.")
        return None
    if cur.get("status") == "publishing" and time.time() - cur.get("claim_ts", 0) < CLAIM_TTL:
        log("Another job is publishing this video right now - leaving it to that job.")
        return None
    me = uuid.uuid4().hex
    cur.update(status="publishing", claim=me, claim_ts=int(time.time()))
    storage.put_json("pending.json", cur)
    time.sleep(5)
    back = storage.get_json("pending.json", None)
    if not back or back.get("claim") != me:
        log("Lost the publish lock to another job.")
        return None
    return back


def _save(p):
    storage.put_json("pending.json", p)


def _post_ig(p):
    mid = instagram.already_posted(p["caption"])
    if mid:
        log("Already on Instagram - not posting again.")
        return mid
    return instagram.post_reel(p["video_url"], p["caption"], p.get("cover_url"))


def _post_yt(p):
    meta = p["yt_meta"]
    vid = youtube.already_posted(meta["snippet"]["title"])
    if vid:
        log("Already on YouTube - not uploading again.")
        return vid
    vid = youtube.upload(p["video_url"], meta)
    p.setdefault("done", {})["yt"] = vid
    _save(p)                                              # saved before the optional extras below
    youtube.add_to_playlist(vid, *youtube.MAIN_PLAYLIST)
    if p.get("series_id"):
        youtube.add_to_playlist(vid, f"{C.CHANNEL}: {p.get('series_title', '')}"[:150],
                                "Is series ke saare parts ek jagah.")
    if p.get("cover_url"):
        try:
            jpg = os.path.join(tempfile.mkdtemp(), "cover.jpg")
            with open(jpg, "wb") as f:
                f.write(requests.get(p["cover_url"], timeout=60).content)
            youtube.set_thumbnail(vid, jpg)
        except Exception as e:
            log("Thumbnail skipped:", e)
    return vid


def _links(done):
    out = []
    if done.get("ig"):
        out.append("📸 Instagram Reel posted")
    if done.get("yt"):
        out.append(f"▶️ https://youtube.com/shorts/{done['yt']}")
    return "\n".join(out)


def publish(p, force=False):
    if p.get("attempts", 0) >= MAX_ATTEMPTS and not force:
        log("Too many failed attempts; run 'Post queued video' manually with force_post = true.")
        return "gave_up"
    p = _claim(p.get("slot"))
    if not p:
        return "busy"
    want = p.get("platforms") or targets()
    if not want:
        notify.text("❌ No platform is connected (set the Instagram and/or YouTube secrets). Nothing was posted.")
        p["status"] = "queued"; p.pop("claim", None); _save(p)
        return "no_platforms"
    done = p.setdefault("done", {})
    errors = {}
    for plat in want:
        if done.get(plat):
            continue
        try:
            done[plat] = _post_ig(p) if plat == "ig" else _post_yt(p)
            _save(p)                                     # progress is durable: a retry never re-posts this platform
        except Exception:
            errors[plat] = redact(traceback.format_exc()[-900:])
            log(errors[plat])
    if errors:
        p["attempts"] = p.get("attempts", 0) + 1
        p["status"] = "queued"; p.pop("claim", None)
        ok = [NAMES[k] for k in want if done.get(k)]
        bad = ", ".join(NAMES[k] for k in errors)
        last = p["attempts"] >= MAX_ATTEMPTS
        state = (f"✅ Already live on: {', '.join(ok)} (will NOT be posted again)\n" if ok else "Nothing is live yet.\n")
        nxt = ("Giving up on it. Fix the problem, then run 'Post queued video' with force_post = true."
               if last else f"I will retry only {bad} on the next run ({p['attempts']}/{MAX_ATTEMPTS}).")
        if last and ok:                                  # live on one platform: keep the story/series history right
            nxt = (f"Giving up on {bad}. Recorded as posted on {', '.join(ok)} only; the video and its metadata are "
                   f"kept in storage as failed/{p.get('slot')}.json so you can upload it to {bad} by hand.")
            _record(p)
            storage.put_json(f"failed/{p.get('slot')}.json", p)
            storage.delete("pending.json")
        else:
            _save(p)
        notify.text(f"❌ Posting to {bad} failed: {p.get('title', '')}\n{state}{nxt}\n\n" +
                    "\n\n".join(f"{NAMES[k]}: {v[-600:]}" for k, v in errors.items()))
        if not ok:
            raise RuntimeError(f"publish failed on {bad}")
        return "partial"
    _record(p)
    storage.delete("pending.json")
    notify.text(f"✅ Posted ({slots.fmt(time.time())}): {p.get('title', '')}\n{_links(done)}")
    return "posted"


def run(force=False, wait=True):
    """Post the pending video if it is due (sleeping until the slot when it is close). Returns a status string."""
    p = storage.get_json("pending.json", None)
    if not p:
        log("Nothing pending.")
        return "nothing"
    if force:
        return publish(p, force=True)

    while True:
        cmd = _command(p)
        if cmd == "skip":
            if p.get("done") and any(p["done"].values()):
                notify.text("⚠️ /skip ignored: this video is already live on one platform; finishing the other one.")
            else:
                storage.delete("pending.json")
                notify.text(f"🗑️ Discarded: {p.get('title', 'pending video')}")
                return "skipped"
        if cmd == "post_now":
            return publish(p)
        partly_live = p.get("done") and any(p["done"].values())
        if C.REVIEW_MODE == "approve" and cmd != "post" and not partly_live:
            log(f"Waiting for your /post in Telegram (slot {slots.fmt(due_at(p))}).")
            return "awaiting_approval"
        wait_s = due_at(p) - time.time()
        if wait_s <= 0:
            return publish(p)
        if not wait or wait_s > C.MAX_WAIT_MINUTES * 60:
            log(f"Not due yet - scheduled for {slots.fmt(due_at(p))} ({wait_s/3600:.1f} h from now).")
            return "not_due"
        nap = min(wait_s, 600)                     # wake every <=10 min to honour a late /skip
        log(f"Sleeping {nap/60:.1f} min - posting at {slots.fmt(due_at(p))}.")
        time.sleep(nap)
        p = storage.get_json("pending.json", None)
        if not p:
            log("Pending video was handled elsewhere while waiting.")
            return "nothing"
