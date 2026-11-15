"""Experiment engine (phase 2, from day 31): Thompson sampling re-allocates posting slots, video length, theme,
hook type and narrator voice toward the best 72-hour results, keeping EXPLORATION (20%) random choices.

The 72-hour score of a video (growth.py stores it once a video is 72 h old) combines views per hour, average %
viewed (YouTube Analytics / Instagram watch time), shares and follows per 1,000 views. An option "wins" when its
video beats the platform's median score. Phase 1 (days 1-30) only explores: the plan's fixed slots, rotation.
Quality rule: max MAX_SHORTS_PER_DAY Shorts a day, and 1 a day while average % viewed stays below 60% for a week."""
import math, random, time
import config as C, storage
from util import log

DIMENSIONS = ("slot", "length", "theme", "hook_type", "voice")
_cache = {}


def phase():
    return 2 if C.days_live() >= C.REVIEW_DAYS else 1


def _rows(platform):
    if "stats" not in _cache:
        try:
            _cache["stats"] = storage.get_json("stats.json", {})
        except Exception:
            _cache["stats"] = {}
    return [r for r in _cache["stats"].values() if r.get("platform", "ig") == platform and r.get("m72")]


def score72(m):
    """One number from a video's 72-hour metrics."""
    views = m.get("views", 0) or 0
    vph = views / 72.0
    per_k = lambda x: min(20.0, (x or 0) * 1000.0 / views) if views >= 300 else 0.0   # tiny samples are noise
    return (math.log1p(vph) + 2.0 * (m.get("avg_pct", 0) or 0) / 100.0 +
            0.5 * per_k(m.get("shares")) + 1.0 * per_k(m.get("follows")))


def _value(r, dim):
    if dim == "slot":
        return (r.get("slot") or "")[-4:]
    if dim == "voice":
        v = r.get("variety") or {}
        return v.get("voice_female") if r.get("narrator") == "female" else v.get("voice_male")
    return r.get(dim)


def arm_stats(platform, dim):
    rows = _rows(platform)
    if not rows:
        return {}
    scores = sorted(score72(r["m72"]) for r in rows)
    med = scores[len(scores) // 2]
    out = {}
    for r in rows:
        v = _value(r, dim)
        if v:
            w, l = out.get(v, (0, 0))
            out[v] = (w + 1, l) if score72(r["m72"]) >= med else (w, l + 1)
    return out


def thompson(platform, dim, options, rng=random):
    """Phase 2: pick an option by Thompson sampling (Beta(1+wins, 1+losses)); 20% of the time pick at random."""
    if phase() < 2 or not options:
        return None
    if rng.random() < C.EXPLORATION:
        return rng.choice(options)
    st = arm_stats(platform, dim)
    draws = {o: rng.betavariate(1 + st.get(o, (0, 0))[0], 1 + st.get(o, (0, 0))[1]) for o in options}
    return max(draws, key=draws.get)


def override(platform):
    """Choices for the next video (None = let the normal rotation decide)."""
    import story as S, variety as V
    lengths = ["short", "mid", "long"] if platform == "yt" and len(_rows("yt")) >= 20 else ["short", "mid"]
    out = {"theme": thompson(platform, "theme", list(S.THEMES)),
           "hook_type": thompson(platform, "hook_type", S.HOOK_TYPES),
           "length": thompson(platform, "length", lengths),
           "voice_male": thompson(platform, "voice", V.VOICES["male"]),
           "voice_female": thompson(platform, "voice", V.VOICES["female"])}
    return {k: v for k, v in out.items() if v}


def quality_low():
    """YouTube Shorts' average % viewed over the last 7 days is under QUALITY_MIN_PCT."""
    week = time.time() - 7 * 86400
    pcts = [r["m72"].get("avg_pct") for r in _rows("yt") if (r.get("posted_ts") or 0) >= week and r["m72"].get("avg_pct")]
    return len(pcts) >= 4 and sum(pcts) / len(pcts) < C.QUALITY_MIN_PCT


def shorts_per_day():
    if quality_low():
        return 1
    return min(C.MAX_SHORTS_PER_DAY, len(C.YT_POST_TIMES))


def schedule(platform, day):
    """Phase 2: the day's posting times chosen by Thompson sampling over the candidate times (cached per day so
    every run agrees). Phase 1 / no data: None (the configured times)."""
    if phase() < 2 or platform not in ("yt", "ig"):
        return None
    key = day.isoformat()
    sched = _cache.setdefault("schedule", storage.get_json("schedule.json", {}))
    if key in sched and platform in sched[key]:
        return sched[key][platform]
    if not _rows(platform):
        return None
    cands = C.YT_SLOT_CANDIDATES if platform == "yt" else C.IG_SLOT_CANDIDATES
    n = shorts_per_day() if platform == "yt" else len(C.IG_POST_TIMES)
    st = arm_stats(platform, "slot")
    rng = random.Random(f"{key}-{platform}")
    draws = {t: (rng.random() if rng.random() < C.EXPLORATION else
                 rng.betavariate(1 + st.get(t.replace(":", ""), (0, 0))[0], 1 + st.get(t.replace(":", ""), (0, 0))[1]))
             for t in cands}
    chosen = sorted(sorted(draws, key=draws.get, reverse=True)[:n])
    sched.setdefault(key, {})[platform] = chosen
    try:
        storage.put_json("schedule.json", dict(list(sched.items())[-60:]))
    except Exception as e:
        log("Schedule cache not saved:", e)
    log(f"🧪 {platform} times for {key}: {', '.join(chosen)}")
    return chosen


def report_lines():
    """A/B results for the weekly report."""
    lines = []
    for plat in ("yt", "ig"):
        for dim in DIMENSIONS:
            st = arm_stats(plat, dim)
            if len(st) < 2:
                continue
            ranked = sorted(st.items(), key=lambda kv: (kv[1][0] + 1) / (sum(kv[1]) + 2), reverse=True)
            txt = ", ".join(f"{k} {w}/{w + l}" for k, (w, l) in ranked[:4])
            lines.append(f"{'YT' if plat == 'yt' else 'IG'} {dim}: {txt}")
    if quality_low():
        lines.append("⚠️ Shorts average % viewed < 60% this week - posting 1 Short a day until it recovers.")
    return lines
