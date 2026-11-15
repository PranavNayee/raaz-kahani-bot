"""Growth engine (engage.yml, hourly):
  * stats   - Instagram insights + YouTube statistics for every posted video -> stats.json
  * learn   - the best hooks, themes and hook types are fed back into the story writer (story.WINNERS / THEME_STATS)
  * replies - answers GENUINE viewer comments in Hindi/Hinglish (max MAX_REPLIES_PER_RUN, default 12, per run)
Only official APIs. No auto-likes, no follows, no fake engagement. Anything a token can't do is skipped with one
clear Telegram note per day instead of crashing."""
import os, re, sys, time, requests
import config as C, storage, notify, slots, llm, youtube
from util import log

MAX_REPLIES = int(os.environ.get("MAX_REPLIES_PER_RUN", "12") or 12)
YT_DAILY_REPLIES = int(os.environ.get("YT_DAILY_REPLIES", "40") or 40)    # 40 x 50 units = 2,000 of 10,000/day
FALLBACK_METRICS = ["reach", "likes", "comments", "shares", "saved"]


def _get(path, **params):
    from instagram import graph, _check
    params["access_token"] = C.IG_ACCESS_TOKEN()
    return _check(requests.get(f"{graph()}/{path}", params=params, timeout=60))


def media_insights(mid):
    last = None
    for metrics in (METRICS, METRICS[:-1], FALLBACK_METRICS):      # 'follows' isn't available on every account
        try:
            j = _get(f"{mid}/insights", metric=",".join(metrics))
            return {d["name"]: (d.get("values") or [{}])[0].get("value", 0) for d in j.get("data", [])}
        except RuntimeError as e:
            last = e
    raise last


def score(s):
    """Reach-weighted engagement over both platforms: shares/saves/comments count most."""
    ig, yt = s.get("ig") or {}, s.get("yt") or {}
    return (ig.get("shares", 0) * 4 + ig.get("saved", 0) * 3 + ig.get("comments", 0) * 2 + ig.get("likes", 0)
            + ig.get("views", ig.get("reach", 0)) / 50 + yt.get("comments", 0) * 2 + yt.get("likes", 0)
            + yt.get("views", 0) / 50)


def views(s):
    return (s.get("ig") or {}).get("views", (s.get("ig") or {}).get("reach", 0)) + (s.get("yt") or {}).get("views", 0)


METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions", "ig_reels_avg_watch_time",
           "follows"]


def yt_retention(ids, start):
    """{video_id: {avg_pct, avg_secs, shares, subs}} from the YouTube Analytics API (empty without the scope)."""
    out = {}
    ids = [i for i in ids if i]
    for k in range(0, len(ids), 40):
        rows = youtube.analytics(start, time.strftime("%Y-%m-%d"),
                                 "views,averageViewPercentage,averageViewDuration,shares,subscribersGained",
                                 dimensions="video", filters="video==" + ",".join(ids[k:k + 40]))
        for r in rows:
            out[r["video"]] = {"avg_pct": r.get("averageViewPercentage"), "avg_secs": r.get("averageViewDuration"),
                               "shares": r.get("shares"), "follows": r.get("subscribersGained")}
    return out


def refresh_stats(limit=40):
    """Pulls every posted video's numbers into stats.json, keyed by slot, and freezes a 72-hour snapshot (m72)
    that the experiment engine learns from."""
    history = storage.get_json("history.json", [])
    stats = storage.get_json("stats.json", {})
    posted = [h for h in history if h.get("kind", "story") == "story" and (h.get("media_id") or h.get("youtube_id"))][-limit:]
    ok, err = 0, None
    yt, ret = {}, {}
    if youtube.enabled():
        ids = [h.get("youtube_id") for h in posted]
        try:
            yt = youtube.video_stats(ids)
        except Exception as e:
            err = f"YouTube stats: {e}"
        try:
            first = min((h.get("posted_ts") or time.time()) for h in posted) if posted else time.time()
            ret = yt_retention(ids, time.strftime("%Y-%m-%d", time.gmtime(first - 86400)))
        except Exception as e:
            err = f"YouTube Analytics (re-run tools/youtube_auth.py to grant it): {str(e)[:150]}"
    for h in posted:
        key = h.get("slot") or str(h.get("media_id"))
        plat = h.get("platform", "ig")
        row = {**stats.get(key, {}), "title": h.get("title"), "hook": h.get("hook", ""), "theme": h.get("theme"),
               "hook_type": h.get("hook_type"), "part": h.get("part"), "posted_ts": h.get("posted_ts"),
               "media_id": h.get("media_id"), "youtube_id": h.get("youtube_id"), "platform": plat, "slot": key,
               "variety": h.get("variety"), "length": h.get("length"), "narrator": h.get("narrator"),
               "duration": h.get("duration")}
        got = False
        if plat == "ig" and C.ig_enabled() and str(h.get("media_id", "")).isdigit():
            try:
                row["ig"] = media_insights(str(h["media_id"])); got = True
            except RuntimeError as e:
                err = str(e)
        if h.get("youtube_id") in yt:
            row["yt"] = {**yt[h["youtube_id"]], **ret.get(h["youtube_id"], {})}; got = True
        if got:
            row["updated"] = int(time.time()); row["score"] = round(score(row), 1)
            age = time.time() - (h.get("posted_ts") or time.time())
            if age >= 72 * 3600 and not row.get("m72"):            # freeze the 72-hour result once
                m = row.get("yt") if plat in ("yt", "ytlong") else row.get("ig") or {}
                watch = (m.get("ig_reels_avg_watch_time") or 0) / 1000.0
                pct = m.get("avg_pct") or (100.0 * watch / h["duration"] if watch and h.get("duration") else 0)
                row["m72"] = {"views": m.get("views", m.get("reach", 0)) or 0, "avg_pct": round(pct or 0, 1),
                              "shares": m.get("shares", 0) or 0, "follows": m.get("follows", 0) or 0}
            stats[key] = row; ok += 1
    if ok:
        storage.put_json("stats.json", stats)
    log(f"Stats refreshed for {ok}/{len(posted)} videos" + (f" (last error: {err[:150]})" if err else ""))
    return stats, (err if not ok and posted else None)


def snapshot_followers():
    """Once a day: followers / subscribers / channel totals, for the weekly projections."""
    snaps = storage.get_json("growth_log.json", [])
    day = slots.now().strftime("%Y-%m-%d")
    if snaps and snaps[-1].get("day") == day:
        return snaps
    row = {"day": day}
    if C.ig_enabled():
        try:
            import instagram
            row["ig_followers"], row["ig_media"] = instagram.followers()
        except Exception as e:
            log("IG followers unavailable:", e)
    if youtube.enabled():
        try:
            row.update({f"yt_{k}": v for k, v in youtube.channel_stats().items()})
        except Exception as e:
            log("YT channel stats unavailable:", e)
    snaps.append(row)
    storage.put_json("growth_log.json", snaps[-400:])
    return snaps


def _rows():
    try:
        return [s for s in storage.get_json("stats.json", {}).values() if s.get("updated")]
    except Exception:
        return []


def winners(n=3):
    """Text for the story writer: the best hooks so far (needs >= 4 videos with stats)."""
    rows = [s for s in _rows() if s.get("hook")]
    if len(rows) < 4:
        return ""
    best = sorted(rows, key=score, reverse=True)[:n]
    return " | ".join(f'"{s["hook"]}" ({views(s)} views)' for s in best)


def group_scores(field, min_n=2):
    """{value: average score} for a field (theme / hook_type) with at least `min_n` videos."""
    g = {}
    for s in _rows():
        if s.get(field):
            g.setdefault(s[field], []).append(score(s))
    return {k: sum(v) / len(v) for k, v in g.items() if len(v) >= min_n}


def theme_scores():
    return group_scores("theme")


# ---------------------------------------------------------------- comment replies
REPLY_PROMPT = """You manage "{name}", a channel of original animated Hindi mystery/fantasy stories (AI-generated).
Video: "{title}"{part}
A viewer ({user}) commented: "{text}"

Decide if the channel should reply. Reply to genuine comments: answers to the story's question, guesses, praise,
thanks, questions about the story, story requests. Do NOT reply to spam, links, self-promotion, hate, abuse,
politics, religion, personal-information requests, or anything you are unsure about.
If you reply: max 2 short sentences, warm and human, in Hindi - in the SAME script the viewer used (Devanagari if
they wrote Devanagari, Roman Hinglish if they wrote Roman). No links, no promises, at most 1 emoji.
{spoiler}
Return ONLY JSON: {{"reply": true|false, "text": "..."}}"""


def _write_reply(h, user, text):
    part = f" (Part {h['part']} of a 2-part series)" if h.get("part") else ""
    spoiler = ("Never reveal or hint at what happens in Part 2 - say 'Part 2 mein pata chalega!' style things."
               if h.get("part") == 1 else "")
    d = llm.generate_json(REPLY_PROMPT.format(name=C.CHANNEL, title=h.get("title", ""), part=part, user=user,
                                              text=text[:500], spoiler=spoiler), temperature=0.6)
    msg = re.sub(r"https?://\S+", "", str(d.get("text", ""))).strip()[:300]
    if not (d.get("reply") and msg):
        return None
    eng = storage.get_json("engage.json", {})
    sent = set(eng.get("reply_texts", []))
    norm = re.sub(r"\W+", "", msg.lower())
    if norm in sent:                                      # never the same reply twice
        d = llm.generate_json(REPLY_PROMPT.format(name=C.CHANNEL, title=h.get("title", ""), part=part, user=user,
                                                  text=text[:500], spoiler=spoiler) +
                              f"\nDo NOT use this wording again: {msg}", temperature=0.9)
        msg = re.sub(r"https?://\S+", "", str(d.get("text", ""))).strip()[:300]
        norm = re.sub(r"\W+", "", msg.lower())
        if not d.get("reply") or not msg or norm in sent:
            return None
    eng["reply_texts"] = (eng.get("reply_texts", []) + [norm])[-2000:]
    storage.put_json("engage.json", eng)
    return msg


def _ig_reply(cid, message):
    from instagram import graph, _check
    _check(requests.post(f"{graph()}/{cid}/replies", data={"message": message, "access_token": C.IG_ACCESS_TOKEN()},
                         timeout=60))


def reply_comments(budget, days=14):
    eng = storage.get_json("engage.json", {})
    done = set(eng.get("replied", []))
    me = (eng.get("username") or "").lower()
    if not me:
        me = str(_get(C.IG_USER_ID(), fields="username").get("username", "")).lower()
        eng["username"] = me
    history = storage.get_json("history.json", [])
    cutoff = time.time() - days * 86400
    recent = [h for h in history if str(h.get("media_id", "")).isdigit() and (h.get("posted_ts") or 0) >= cutoff][-10:]
    sent, err = 0, None
    for h in reversed(recent):
        if sent >= budget:
            break
        try:
            cs = _get(f"{h['media_id']}/comments", fields="id,text,username,timestamp,replies{username}", limit=50)
        except RuntimeError as e:
            err = str(e); break
        for c in cs.get("data", []):
            if sent >= budget:
                break
            cid, user, text = c["id"], (c.get("username") or "").lower(), (c.get("text") or "").strip()
            if cid in done or not text or user == me:
                continue
            done.add(cid)
            if any((r.get("username") or "").lower() == me for r in (c.get("replies") or {}).get("data", [])):
                continue
            if re.search(r"https?://|www\.", text) or len(text) > 600:
                continue
            try:
                msg = _write_reply(h, "@" + user, text)
            except Exception as e:
                log("Reply writer failed:", e); continue
            if not msg:
                continue
            try:
                _ig_reply(cid, msg)
                sent += 1; log(f"  IG replied to @{user}: {msg[:80]}"); time.sleep(4)
            except RuntimeError as e:
                err = str(e); break
    eng["replied"] = list(done)[-3000:]
    storage.put_json("engage.json", eng)
    log(f"Instagram: replied to {sent} comments")
    return sent, err


def yt_reply_comments(budget):
    if not youtube.enabled() or budget <= 0:
        return 0, None
    eng = storage.get_json("engage.json", {})
    today = slots.now().strftime("%Y-%m-%d")
    if eng.get("yt_day") != today:
        eng["yt_day"], eng["yt_replies_today"] = today, 0
    budget = min(budget, max(0, YT_DAILY_REPLIES - eng.get("yt_replies_today", 0)))
    if not budget:
        log("YouTube reply budget for today used up (keeps the bot inside the free API quota)")
        return 0, None
    tok = youtube._access_token()
    me, threads = youtube.recent_comment_threads(tok)
    done = set(eng.get("yt_replied", []))
    by_vid = {h.get("youtube_id"): h for h in storage.get_json("history.json", []) if h.get("youtube_id")}
    sent, err = 0, None
    for th in threads:
        if sent >= budget:
            break
        tid = th["id"]; top = th["snippet"]["topLevelComment"]["snippet"]
        if tid in done or (top.get("authorChannelId") or {}).get("value") == me:
            continue
        done.add(tid)
        if any(((r["snippet"].get("authorChannelId") or {}).get("value")) == me for r in (th.get("replies") or {}).get("comments", [])):
            continue
        text, user = (top.get("textOriginal") or "").strip(), (top.get("authorDisplayName") or "")
        if not text or re.search(r"https?://|www\.", text) or len(text) > 600:
            continue
        try:
            msg = _write_reply(by_vid.get(th["snippet"].get("videoId"), {}), user, text)
        except Exception as e:
            log("YT reply writer failed:", e); continue
        if msg:
            try:
                youtube.reply(tok, tid, msg); sent += 1; time.sleep(3)
                log(f"  YT replied to {user}: {msg[:60]}")
            except Exception as e:
                err = str(e); break
    eng["yt_replied"] = list(done)[-3000:]
    eng["yt_replies_today"] = eng.get("yt_replies_today", 0) + sent
    storage.put_json("engage.json", eng)
    log(f"YouTube: replied to {sent} comments ({eng['yt_replies_today']}/{YT_DAILY_REPLIES} today)")
    return sent, err


def main():
    notes = []
    try:
        snapshot_followers()
    except Exception as e:
        log("Follower snapshot skipped:", e)
    _, err = refresh_stats()
    if err:
        notes.append(f"Video stats unavailable: {err[:200]}\n→ Instagram needs 'instagram_manage_insights'.")
    if os.environ.get("AUTO_REPLY", "true").lower() == "true":
        sent = 0
        if C.ig_enabled():
            try:
                sent, err = reply_comments(MAX_REPLIES)
                if err:
                    notes.append(f"Instagram replies unavailable: {err[:200]}\n→ needs 'instagram_manage_comments'.")
            except Exception as e:
                notes.append(f"Instagram replies failed: {str(e)[:200]}")
        try:
            _, err = yt_reply_comments(MAX_REPLIES - sent)
            if err:
                notes.append(f"YouTube replies unavailable: {err[:200]}")
        except Exception as e:
            notes.append(f"YouTube replies failed: {str(e)[:200]}")
    if notes:
        eng = storage.get_json("engage.json", {})
        key = slots.now().strftime("%Y-%m-%d")
        if eng.get("notes_day") != key:              # at most one warning per day
            notify.text("⚠️ Growth engine:\n\n" + "\n\n".join(notes))
            eng["notes_day"] = key; storage.put_json("engage.json", eng)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback; traceback.print_exc()
        sys.exit(1)
