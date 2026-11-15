"""Weekly Telegram report (weekly.yml, Sunday evening): views/likes/comments on both platforms, best and worst
video, follower growth, and which themes / hook types to make more of. The story writer already uses these
scores automatically (growth.winners / growth.theme_scores)."""
import os, sys, time
import config as C, storage, notify, slots, youtube, growth, story as S
from util import log

NICE = {"night_mystery": "Raat ke rahasya", "magic_rule": "Jaadui niyam", "cursed_object": "Shraapit cheezein",
        "greedy_rich": "Laalchi ameer vs chaalak garib", "family_love": "Bhai-behen / maa ka pyaar",
        "friendly_monster": "Dost rakshas", "village_legend": "Gaon ki kahaaniyan"}


def _followers():
    out = {}
    if C.ig_enabled():
        try:
            out["ig"] = growth._get(C.IG_USER_ID(), fields="followers_count").get("followers_count")
        except Exception as e:
            log("IG followers unavailable:", e)
    if youtube.enabled():
        try:
            it = youtube._api("GET", "channels", params={"part": "statistics", "mine": "true"}).get("items", [])
            out["yt"] = int(it[0]["statistics"].get("subscriberCount", 0)) if it else None
        except Exception as e:
            log("YT subscribers unavailable:", e)
    return out


def _rank(field, label):
    sc = growth.group_scores(field, min_n=1)
    if len(sc) < 2:
        return "", None, None
    order = sorted(sc.items(), key=lambda kv: kv[1], reverse=True)
    line = f"{label}: " + " > ".join(f"{label_of(k)} ({v:.0f})" for k, v in order[:5])
    return line, order[0][0], order[-1][0]


def label_of(k):
    return NICE.get(k, k.replace("_", " "))


def report():
    stats, _ = growth.refresh_stats()
    week_ago = time.time() - 7 * 86400
    rows = [s for s in stats.values() if (s.get("posted_ts") or 0) >= week_ago and s.get("updated")]
    fol = _followers()
    eng = storage.get_json("engage.json", {})
    prev = eng.get("followers_last_week") or {}
    eng["followers_last_week"] = fol
    storage.put_json("engage.json", eng)

    def growth_txt(k):
        if fol.get(k) is None:
            return "?"
        d = f" ({fol[k] - prev[k]:+d})" if isinstance(prev.get(k), int) else ""
        return f"{fol[k]:,}{d}"
    if not rows:
        notify.text(f"📊 Weekly report: no video stats this week yet.\n👥 IG {growth_txt('ig')} · YT {growth_txt('yt')}")
        return
    tot = lambda plat, k: sum(int((s.get(plat) or {}).get(k, 0) or 0) for s in rows)
    best, worst = max(rows, key=growth.score), min(rows, key=growth.score)
    themes, more, less = _rank("theme", "🎭 Themes")
    hooks, hmore, _ = _rank("hook_type", "🪝 Hook types")
    msg = [f"📊 Weekly report - {len(rows)} videos",
           f"📸 Instagram: {tot('ig', 'views') or tot('ig', 'reach'):,} views · {tot('ig', 'likes'):,} likes · "
           f"{tot('ig', 'comments'):,} comments · {tot('ig', 'shares'):,} shares · {tot('ig', 'saved'):,} saves",
           f"▶️ YouTube: {tot('yt', 'views'):,} views · {tot('yt', 'likes'):,} likes · {tot('yt', 'comments'):,} comments",
           f"👥 Followers: IG {growth_txt('ig')} · YT {growth_txt('yt')}", "",
           f"🏆 Best: {best.get('title')} - {growth.views(best):,} views (theme: {label_of(best.get('theme') or '?')})",
           f"🐢 Weakest: {worst.get('title')} - {growth.views(worst):,} views (theme: {label_of(worst.get('theme') or '?')})"]
    if themes or hooks:
        msg += ["", "All-time average score:"] + [x for x in (themes, hooks) if x]
    tips = []
    if more and less and more != less:
        tips.append(f"Make more '{label_of(more)}' stories, fewer '{label_of(less)}'. The writer already favours the "
                    f"best themes when it rotates.")
    if hmore:
        tips.append(f"Hooks of type '{label_of(hmore)}' work best - the writer sees your top hooks every day.")
    series = [s for s in rows if s.get("part")]
    if series and len(series) < len(rows):
        sv = sum(growth.score(s) for s in series) / len(series)
        nv = sum(growth.score(s) for s in rows if not s.get("part")) / max(1, len(rows) - len(series))
        tips.append(f"2-part series score {sv:.0f} vs single stories {nv:.0f}"
                    + (" - consider SERIES_EVERY=2." if sv > nv * 1.3 else "."))
    if tips:
        msg += [""] + [f"💡 {t}" for t in tips]
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
