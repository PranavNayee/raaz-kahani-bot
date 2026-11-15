"""Pre-flight check: tests every service and token the bot uses and says exactly what is wrong
(printed and sent to Telegram). Run: python src/check.py   (check.yml runs it with your GitHub secrets)."""
import os, shutil, subprocess, sys, tempfile, time
import config as C

results = []


def check(name, fn, critical=True):
    try:
        info = fn()
        results.append((True, name, info or ""))
    except Exception as e:
        from util import redact
        results.append((False if critical else None, name, redact(str(e))[:300]))


def need(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError("missing secrets: " + ", ".join(missing))


def c_secrets():
    need("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SUPABASE_URL", "SUPABASE_SERVICE_KEY")
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")):
        raise RuntimeError("set GEMINI_API_KEY and/or GROQ_API_KEY")
    plats = [p for p, ok in (("Instagram", C.ig_enabled()), ("YouTube", _yt())) if ok]
    if not plats:
        raise RuntimeError("no platform connected: set IG_USER_ID + IG_ACCESS_TOKEN and/or the YT_* secrets")
    return f"core secrets present; posting to: {', '.join(plats)}"


def _yt():
    return all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def c_schedule():
    import slots
    parts = []
    for p, ok in (("yt", _yt()), ("ig", C.ig_enabled())):
        if ok:
            n = slots.next_slot(p)
            parts.append(f"{p} next {slots.fmt(n)}" if n else f"{p} no slot")
    return (f"{'; '.join(parts) or 'no platform'} ({C.TIMEZONE}); review mode today '{C.review_mode()}' "
            f"(day {C.days_live()} since {C.START_DATE}); Telegram buttons {'on' if C.TELEGRAM_COMMANDS else 'off'}")


def c_ffmpeg():
    for b in ("ffmpeg", "ffprobe"):
        if not shutil.which(b):
            raise RuntimeError(f"{b} not installed")
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    if "loudnorm" not in out:
        raise RuntimeError("ffmpeg is missing the loudnorm filter")
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "libx264" not in enc:
        raise RuntimeError("ffmpeg has no libx264 encoder")
    return "ffmpeg OK (libx264, loudnorm)"


def c_text():
    import render
    if not os.path.exists(os.path.join(C.FONT_DIR, "NotoSansDevanagari-Bold.ttf")):
        raise RuntimeError("assets/fonts/NotoSansDevanagari-Bold.ttf missing")
    if not render.RAQM:
        raise RuntimeError("Pillow has no libraqm shaping - Hindi captions would break (install libfribidi0)")
    return "Noto Sans Devanagari + raqm text shaping OK"


def c_llm():
    import llm
    r = llm.generate_json('Return only this JSON: {"ok": true}', temperature=0)
    if not r.get("ok"):
        raise RuntimeError(f"unexpected reply {r}")
    return f"AI writer reachable ({llm.LAST})"


def c_images():
    import images, story
    s = story.sample()
    img, prov = images.one(s["scenes"][1], s, tries=1)
    return f"image OK via {prov} ({img.width}x{img.height}); providers {','.join(images._providers()) or 'none'}"


def c_telegram():
    import notify
    notify.text("✅ Pre-flight check: Telegram works.")
    return "message sent to your chat (check Telegram)"


def c_storage():
    import storage, requests
    made = storage.ensure_bucket()
    storage.put_json("healthcheck.json", {"ok": True, "ts": time.time()})
    if not storage.get_json("healthcheck.json", None):
        raise RuntimeError("write worked but read failed")
    url = storage.public_url("healthcheck.json")
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"upload works, but the PUBLIC url returns {r.status_code}. Make the bucket PUBLIC in Supabase")
    p = storage.get_json("pending.json", None)
    q = f"; queued video for {p.get('slot')}" if p else "; nothing queued"
    return f"Supabase bucket '{C.SUPABASE_BUCKET()}' {made}, read/write + public URL OK" + q


def c_voice():
    import tts, story
    d = tempfile.mkdtemp()
    s = story.sample()
    (p, dur, words), = tts.speak_scenes(s["scenes"][:1], s, d)
    who = f"{C.GEMINI_VOICE}/{C.GEMINI_VOICE_FEMALE}" if tts.LAST == "gemini" else f"{C.VOICE_MALE}/{C.VOICE_FEMALE}"
    warn = " - ⚠️ robotic fallback voice, Gemini TTS unavailable" if tts.LAST == "edge" and tts.order()[0] != "edge" else ""
    return f"voice OK via {tts.LAST} ({dur:.1f}s, {len(words)} timed words, voices {who}){warn}"


def c_ig():
    if not C.ig_enabled():
        raise RuntimeError("Instagram not connected (IG_USER_ID / IG_ACCESS_TOKEN missing)")
    import instagram
    user = instagram.check_token()
    days = instagram.token_days_left()
    left = (f", token expires in {days:.0f} days" if days is not None else
            ", token expiry unknown (set the variable IG_TOKEN_SET=YYYY-MM-DD when you save a new token)")
    if days is not None and days < 7:
        raise RuntimeError(f"token for @{user} expires in {days:.1f} days - renew it now (README)")
    scopes = instagram.token_scopes()
    want = {"instagram_content_publish": "posting", "instagram_manage_insights": "stats + learning",
            "instagram_manage_comments": "comment replies"}
    miss = [f"{k} ({v})" for k, v in want.items() if scopes and k not in scopes]
    extra = f"; MISSING permissions: {', '.join(miss)}" if miss else ""
    return f"Instagram token OK for @{user}{left}{extra}"


def c_yt():
    if not _yt():
        raise RuntimeError("YouTube not connected (run tools/youtube_auth.py)")
    import youtube
    cid, title, _ = youtube.channel()
    return f"YouTube OK: channel '{title}' ({cid}), uploads as {C.YT_PRIVACY}"


if __name__ == "__main__":
    check("Secrets", c_secrets); check("Schedule", c_schedule); check("FFmpeg", c_ffmpeg)
    check("Hindi text", c_text); check("AI writer", c_llm); check("Images", c_images, critical=False)
    check("Telegram", c_telegram); check("Supabase storage", c_storage); check("Voice", c_voice)
    check("Instagram", c_ig, critical=C.ig_enabled()); check("YouTube", c_yt, critical=_yt())
    lines = [f"{'✅' if ok else ('⚠️' if ok is None else '❌')} {name}: {info}" for ok, name, info in results]
    report = "\n".join(lines)
    print(report)
    try:
        import notify
        notify.text("Pre-flight report\n\n" + report)
    except Exception:
        pass
    sys.exit(0 if all(r[0] is not False for r in results) else 1)
