"""Telegram: previews, status messages and the /post /postnow /skip review commands."""
import os, subprocess, requests
import config as C
from util import log, redact

TG_LIMIT = 48 * 1024 * 1024          # bots may upload up to 50 MB


def _tag(msg):
    """Every message starts with the channel name, so a Telegram chat shared with another bot stays readable."""
    return f"[{C.CHANNEL}] {msg}"


def _api(method):
    return f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN()}/{method}"


def text(msg):
    if not C.telegram_enabled():
        log("[telegram off]", msg[:300]); return
    try:
        requests.post(_api("sendMessage"), data={"chat_id": C.TELEGRAM_CHAT_ID(), "text": _tag(redact(msg))[:4000]}, timeout=60)
    except Exception as e:
        log("Telegram text failed:", e)


def _small_copy(path):
    """A 540x960 copy for Telegram when the full video is over the bot upload limit."""
    out = os.path.splitext(path)[0] + "_tg.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vf", "scale=540:960", "-c:v", "libx264", "-preset",
                    "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out], check=True)
    return out


def video(path, caption, fallback_url=""):
    if not C.telegram_enabled():
        log("[telegram off] video:", path); return
    try:
        send = path if os.path.getsize(path) <= TG_LIMIT else _small_copy(path)
        with open(send, "rb") as f:
            r = requests.post(_api("sendVideo"), data={"chat_id": C.TELEGRAM_CHAT_ID(), "caption": _tag(redact(caption))[:1000],
                              "supports_streaming": "true"}, files={"video": f}, timeout=300)
        if r.status_code == 200:
            return
        log("Telegram video failed:", r.text[:200])
    except Exception as e:
        log("Telegram video failed:", e)
    text(f"{caption[:600]}\n\n(Video upload to Telegram failed. Watch it here: {fallback_url or 'n/a'})")


def photo(path, caption=""):
    if not C.telegram_enabled():
        return
    try:
        with open(path, "rb") as f:
            requests.post(_api("sendPhoto"), data={"chat_id": C.TELEGRAM_CHAT_ID(), "caption": _tag(redact(caption))[:1000]},
                          files={"photo": f}, timeout=120)
    except Exception as e:
        log("Telegram photo failed:", e)


def latest_command(since_ts):
    """Reads recent Telegram messages from YOUR chat only (latest command wins). Returns:
    'post' (/post: publish at the slot time), 'post_now' (/postnow: publish immediately), 'skip' (/skip) or None."""
    if not C.telegram_enabled() or not C.TELEGRAM_COMMANDS:
        return None
    r = requests.get(_api("getUpdates"), params={"limit": 100, "timeout": 0}, timeout=60)
    if r.status_code != 200:
        log("getUpdates failed:", r.text[:200]); return None
    cmd = None
    for u in r.json().get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        if str(m.get("chat", {}).get("id")) != C.TELEGRAM_CHAT_ID() or m.get("date", 0) < since_ts:
            continue
        t = (m.get("text") or "").strip().lower().split("@")[0]
        if t in ("/post", "/approve", "/yes"):
            cmd = "post"
        elif t in ("/postnow", "/now"):
            cmd = "post_now"
        elif t in ("/skip", "/reject", "/no"):
            cmd = "skip"
    return cmd
