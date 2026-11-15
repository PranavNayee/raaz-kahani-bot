"""Posting-slot maths, per platform. Every slot has a unique key "<date>-<platform>-<HHMM>" (e.g.
"2026-11-16-yt-0800"), which makes the bot idempotent: one video per slot, ever.

Platforms: yt (Shorts, YT_POST_TIMES), ig (Reels, IG_POST_TIMES), ytlong (weekly long-form, LONGFORM_SLOT).
In phase 2 the experiment engine may choose the day's times (experiments.schedule)."""
import datetime as dt
import config as C

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(C.TIMEZONE)
except Exception:                                   # no tz database: assume IST
    TZ = dt.timezone(dt.timedelta(hours=5, minutes=30))

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def now():
    return dt.datetime.now(TZ)


def _parse(times):
    out = []
    for t in times:
        h, m = (int(x) for x in t.split(":"))
        out.append(dt.time(h, m))
    return sorted(set(out))


def times(platform="ig", day=None):
    """Posting times of `platform` on local date `day`."""
    if platform == "ytlong":
        d, t = C.LONGFORM_SLOT
        return _parse([t]) if day is None or DAYS[day.weekday()] == d else []
    if day is not None:
        try:
            import experiments
            chosen = experiments.schedule(platform, day)
            if chosen:
                return _parse(chosen)
        except Exception:
            pass
    return _parse(C.YT_POST_TIMES if platform == "yt" else C.IG_POST_TIMES)


def key(slot, platform="ig"):
    return f"{slot.date().isoformat()}-{platform}-{slot.strftime('%H%M')}"


def slots_between(platform, start, end):
    """All slot datetimes of `platform` from `start` to `end` (tz-aware), oldest first."""
    out, day = [], start.date() - dt.timedelta(days=1)
    while day <= end.date():
        for t in times(platform, day):
            s = dt.datetime.combine(day, t, tzinfo=TZ)
            if start <= s <= end:
                out.append(s)
        day += dt.timedelta(days=1)
    return out


def grace_minutes():
    return C.LATE_GRACE_MINUTES


def upcoming(platform, at=None, done=(), ahead_hours=None):
    """Slots of `platform` that still need a video: not in `done`, not more than the grace period in the past,
    and at most `ahead_hours` in the future. Oldest first."""
    at = at or now()
    ahead = C.PRODUCE_AHEAD_HOURS if ahead_hours is None else ahead_hours
    start = at - dt.timedelta(minutes=grace_minutes())
    return [s for s in slots_between(platform, start, at + dt.timedelta(hours=ahead)) if key(s, platform) not in done]


def next_slot(platform, after=None):
    """The next slot of `platform` strictly after `after` (searches two weeks)."""
    after = after or now()
    later = slots_between(platform, after + dt.timedelta(seconds=1), after + dt.timedelta(days=14))
    return later[0] if later else None


def fmt(ts_or_dt):
    d = ts_or_dt if isinstance(ts_or_dt, dt.datetime) else dt.datetime.fromtimestamp(ts_or_dt, TZ)
    return d.astimezone(TZ).strftime("%a %d %b, %I:%M %p")


def hindi_time(slot):
    """'कल रात 8:30 बजे' style text for a slot (used on Part 1 end cards)."""
    h, m = slot.hour, slot.minute
    part = "सुबह" if h < 12 else "दोपहर" if h < 16 else "शाम" if h < 20 else "रात"
    hh = h % 12 or 12
    return f"कल {part} {hh}{':%02d' % m if m else ''} बजे"
