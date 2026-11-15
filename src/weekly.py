"""Weekly Telegram report (weekly.yml, Sunday evening):
  * this week's totals on both platforms, best and worst video
  * progress to every monetization threshold with a projected date at the current pace:
      YouTube early tier  500 subs + 3 uploads (90 days) + 3M Shorts views (90 days)
      YouTube full YPP    1,000 subs + 10M Shorts views (90 days) OR 4,000 long-form watch hours (12 months)
      Instagram           500 (Gifts), 1,000 (Creator Marketplace), 10,000 (Subscriptions) followers
    (Shorts views never count toward watch hours; per the brief, thresholds double for new applicants on 2027-02-01)
  * A/B results from the experiment engine, what to make more / less of, token expiry warnings, and how many
    creative decisions the owner made this week."""
import datetime as dt, os, sys, time
import config as C, storage, notify, slots, youtube, growth, experiments
from util import log

DEADLINE = dt.date(2027, 2, 1)
NICE = {"night_mystery": "Raat ke rahasya", "magic_rule": "Jaadui niyam", "cursed_object": "Shraapit cheezein",
        "greedy_rich": "Laalchi ameer vs chaalak garib", "family_love": "Bhai-behen / maa ka pyaar",
        "friendly_monster": "Dost rakshas", "village_legend": "Gaon ki kahaaniyan"}


def label_of(k):
    return NICE.get(k, str(k).replace("_", " "))


def project(current, target, per_day, today=None):
    """Projected date to reach `target` at `per_day`, or None."""
    today = today or dt.date.today()
    if current >= target:
        return today
    if not per_day or per_day <= 0:
        return None
    return today + dt.timedelta(days=int((target - current) / per_day) + 1)


def bar(current, target, width=10):
    k = min(width, int(width * current / target)) if target else 0
    return "▓" * k + "░" * (width - k) + f" {current:,}/{target:,}"


def when(d):
    if d is None:
        return "no pace yet"
    flag = " ✅" if d <= dt.date.today() else (" ⚠️ after 2027-02-01" if d > DEADLINE else " (before 2027-02-01)")
    return d.strftime("%d %b %Y") + flag


def _pace(snaps, key, days=7):
    rows = [s for s in snaps if s.get(key) is not None]
    if len(rows) < 2:
        return None, rows[-1][key] if rows else None
    cur, old = rows[-1], rows[max(0, len(rows) - 1 - days)]
    span = max(1, (dt.date.fromisoformat(cur["day"]) - dt.date.fromisoformat(old["day"])).days)
    return (cur[key] - old[key]) / span, cur[key]


def yt_section(history, snaps):
    if not youtube.enabled():
        return ["▶️ YouTube: not connected yet - connect it (tools/youtube_auth.py) to start the YPP clock."]
    out = ["▶️ YouTube Partner Program"]
    today = dt.date.today()
    st = youtube.channel_stats()
    subs = st["subscribers"]
    sub_pace, _ = _pace(snaps, "yt_subscribers")
    uploads90 = sum(1 for h in history if h.get("platform") in ("yt", "ytlong") and
                    (h.get("posted_ts") or 0) >= time.time() - 90 * 86400)
    shorts90 = shorts7 = hours365 = None
    try:
        rows = youtube.analytics((today - dt.timedelta(days=90)).isoformat(), today.isoformat(), "views",
                                 dimensions="creatorContentType")
        shorts90 = int(sum(r["views"] for r in rows if r.get("creatorContentType") == "SHORTS"))
        rows = youtube.analytics((today - dt.timedelta(days=7)).isoformat(), today.isoformat(), "views",
                                 dimensions="creatorContentType")
        shorts7 = int(sum(r["views"] for r in rows if r.get("creatorContentType") == "SHORTS"))
        rows = youtube.analytics((today - dt.timedelta(days=365)).isoformat(), today.isoformat(),
                                 "estimatedMinutesWatched", dimensions="creatorContentType")
        hours365 = int(sum(r["estimatedMinutesWatched"] for r in rows
                           if r.get("creatorContentType") != "SHORTS") / 60)
    except Exception as e:
        out.append(f"  (Analytics unavailable: {str(e)[:120]} - re-run tools/youtube_auth.py)")
    sv_pace = shorts7 / 7 if shorts7 is not None else None
    out.append(f"  Subscribers {bar(subs, 1000)} · +{sub_pace or 0:.1f}/day")
    out.append(f"  Early tier: 500 subs → {when(project(subs, 500, sub_pace))} · uploads (90 d) {uploads90}/3"
               + (f" · Shorts views (90 d) {bar(shorts90, 3_000_000)} → {when(project(shorts90, 3_000_000, sv_pace))}"
                  if shorts90 is not None else ""))
    out.append(f"  Full YPP: 1,000 subs → {when(project(subs, 1000, sub_pace))}")
    if shorts90 is not None:
        out.append(f"    path A: Shorts views (90 d) {bar(shorts90, 10_000_000)} → "
                   f"{when(project(shorts90, 10_000_000, sv_pace))}")
    if hours365 is not None:
        lf = [h for h in history if h.get("platform") == "ytlong" and (h.get("posted_ts") or 0) >= time.time() - 28 * 86400]
        out.append(f"    path B: long-form watch hours (12 m) {bar(hours365, 4000)} · {len(lf)} long videos in 4 weeks")
    return out


def ig_section(snaps):
    if not C.ig_enabled():
        return []
    pace, cur = _pace(snaps, "ig_followers")
    if cur is None:
        return ["📸 Instagram: follower count unavailable this week."]
    out = [f"📸 Instagram followers {cur:,} (+{pace or 0:.1f}/day)"]
    for target, what in ((500, "Gifts"), (1000, "Creator Marketplace"), (10000, "Subscriptions")):
        out.append(f"  {what}: {bar(cur, target)} → {when(project(cur, target, pace))}")
    return out


def report():
    stats, _ = growth.refresh_stats()
    snaps = growth.snapshot_followers()
    history = storage.get_json("history.json", [])
    week_ago = time.time() - 7 * 86400
    rows = [s for s in stats.values() if (s.get("posted_ts") or 0) >= week_ago and s.get("updated")]
    msg = [f"📊 Weekly report - {slots.now().strftime('%d %b %Y')} (day {C.days_live()} since launch, "
           f"phase {experiments.phase()})", ""]
    if rows:
        tot = lambda plat, k: sum(int((s.get(plat) or {}).get(k, 0) or 0) for s in rows)
        best, worst = max(rows, key=growth.score), min(rows, key=growth.score)
        msg += [f"This week: {len(rows)} videos",
                f"  📸 IG {tot('ig', 'views') or tot('ig', 'reach'):,} views · {tot('ig', 'likes'):,} likes · "
                f"{tot('ig', 'comments'):,} comments · {tot('ig', 'shares'):,} shares · {tot('ig', 'saved'):,} saves",
                f"  ▶️ YT {tot('yt', 'views'):,} views · {tot('yt', 'likes'):,} likes · {tot('yt', 'comments'):,} comments",
                f"  🏆 Best: {best.get('title')} ({growth.views(best):,} views, {label_of(best.get('theme') or '?')})",
                f"  🐢 Weakest: {worst.get('title')} ({growth.views(worst):,} views)", ""]
    else:
        msg += ["No video stats this week yet.", ""]
    msg += yt_section(history, snaps) + [""] + ig_section(snaps)
    ab = experiments.report_lines()
    if ab:
        msg += ["", "🧪 A/B (wins/videos at 72 h):"] + [f"  {l}" for l in ab]
    sc = growth.group_scores("theme", min_n=1)
    if len(sc) >= 2:
        order = sorted(sc.items(), key=lambda kv: kv[1], reverse=True)
        msg += ["", f"💡 Make more '{label_of(order[0][0])}', fewer '{label_of(order[-1][0])}' stories."]
    dec = [d for d in storage.get_json("decisions.json", []) if d.get("ts", 0) >= week_ago]
    own = sum(1 for d in dec if d.get("by") == "owner")
    msg += ["", f"🎬 Creative direction this week: {own} decisions by you, {len(dec) - own} automatic."]
    try:
        import instagram
        days = instagram.token_days_left() if C.ig_enabled() else None
        if days is not None and days < 14:
            msg.append(f"⚠️ Instagram token expires in about {days:.0f} days - renew it (README).")
    except Exception:
        pass
    notify.text("\n".join(msg))
    log("\n".join(msg))


def main():
    week = slots.now().strftime("%G-W%V")
    eng = storage.get_json("engage.json", {})
    if eng.get("report_week") == week and os.environ.get("FORCE_REPORT") != "true":
        log("This week's report was already sent."); return
    report()
    eng = storage.get_json("engage.json", {}); eng["report_week"] = week; storage.put_json("engage.json", eng)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback; traceback.print_exc(); sys.exit(1)
