"""Instagram Reels via the official Graph API (video_url from the public Supabase bucket). No private APIs."""
import time, requests
import config as C
from util import log, redact

FB_GRAPH = "https://graph.facebook.com/v21.0"
IG_GRAPH = "https://graph.instagram.com/v21.0"
TOKEN_DAYS = 60                               # long-lived tokens last 60 days


def is_ig_login():
    """Tokens from 'Instagram API with Instagram/Unified login' start with IG and use graph.instagram.com;
    Facebook-login tokens (EAA...) use graph.facebook.com. Same endpoints for posting, comments and insights."""
    return C.IG_ACCESS_TOKEN().startswith("IG")


def graph():
    return C.env("IG_GRAPH_HOST") or (IG_GRAPH if is_ig_login() else FB_GRAPH)


def _check(r):
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(redact(f"Instagram API non-JSON response {r.status_code}: {r.text[:200]}"))
    if r.status_code != 200 or "error" in j:
        raise RuntimeError(redact(f"Instagram API error: {j.get('error', j)}"))
    return j

def check_token():
    """Returns the account username if the token and user id work."""
    j = _check(requests.get(f"{graph()}/{C.IG_USER_ID()}", params={"fields": "username",
               "access_token": C.IG_ACCESS_TOKEN()}, timeout=60))
    return j.get("username", "?")

def post_reel(video_url, caption, cover_url=None):
    """Publishes a Reel. Returns the media id."""
    uid, tok = C.IG_USER_ID(), C.IG_ACCESS_TOKEN()
    data = {"media_type": "REELS", "video_url": video_url, "caption": caption[:2200],
            "share_to_feed": "true", "access_token": tok}
    if cover_url:
        data["cover_url"] = cover_url
    cid = _check(requests.post(f"{graph()}/{uid}/media", data=data, timeout=60))["id"]
    log("Instagram container created:", cid)
    for _ in range(48):                       # up to ~12 minutes
        time.sleep(15)
        s = _check(requests.get(f"{graph()}/{cid}", params={"fields": "status_code,status",
                   "access_token": tok}, timeout=60))
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram processing failed: {s}")
    else:
        raise RuntimeError("Instagram processing timed out")
    media_id = _check(requests.post(f"{graph()}/{uid}/media_publish",
                      data={"creation_id": cid, "access_token": tok}, timeout=60))["id"]
    log("Published media id:", media_id)
    return media_id

def recent_media(limit=6):
    """[(caption, unix_ts, media_id)] of the account's newest posts. Used to avoid ever posting the same reel twice."""
    import datetime as dt
    j = _check(requests.get(f"{graph()}/{C.IG_USER_ID()}/media", params={"fields": "id,caption,timestamp",
               "limit": limit, "access_token": C.IG_ACCESS_TOKEN()}, timeout=60))
    out = []
    for m in j.get("data", []):
        try:
            ts = dt.datetime.strptime(m.get("timestamp", ""), "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except ValueError:
            ts = 0
        out.append((m.get("caption") or "", ts, m.get("id")))
    return out


def already_posted(caption, within_hours=30):
    """Media id of a post with the same first caption line from the last `within_hours` (dedupe across
    overlapping/retried jobs), else None."""
    head = caption.strip().splitlines()[0].strip() if caption.strip() else ""
    if not head:
        return None
    cutoff = time.time() - within_hours * 3600
    try:
        return next((mid for c, ts, mid in recent_media() if c.strip().startswith(head) and ts >= cutoff), None)
    except Exception as e:
        log("Could not read recent posts (continuing):", e)
        return None


def token_days_left():
    """Days until the access token expires (None = unknown). When Meta's debug_token lookup is unavailable (always
    for Instagram-login tokens, sometimes for business tokens) it is estimated from the date the token was saved
    (variable IG_TOKEN_SET=YYYY-MM-DD) assuming the usual 60 days."""
    tok = C.IG_ACCESS_TOKEN()
    if is_ig_login():
        return _estimate()
    try:
        j = requests.get(f"{graph()}/debug_token", params={"input_token": tok, "access_token": tok}, timeout=30).json()
        exp = (j.get("data") or {}).get("expires_at") or (j.get("data") or {}).get("data_access_expires_at")
        if exp:
            return (exp - time.time()) / 86400
    except Exception as e:
        log("Token expiry check failed:", e)
    return _estimate()


def _estimate():
    """Days left counted from the date the token was saved (variable IG_TOKEN_SET), when Meta won't say."""
    import datetime as dt
    try:
        set_on = dt.date.fromisoformat((C.env("IG_TOKEN_SET") or "").strip())
        return TOKEN_DAYS - (dt.date.today() - set_on).days
    except ValueError:
        return None


def token_scopes():
    tok = C.IG_ACCESS_TOKEN()
    if is_ig_login():
        return set()                          # no debug endpoint; permissions are proven by the calls themselves
    try:
        j = requests.get(f"{graph()}/debug_token", params={"input_token": tok, "access_token": tok}, timeout=30).json()
        return set((j.get("data") or {}).get("scopes") or [])
    except Exception:
        return set()


def post_story(image_url):
    """Shares an image to the account's Stories (business/creator accounts with content publishing).
    The API cannot add stickers, so the question is written on the image itself."""
    uid, tok = C.IG_USER_ID(), C.IG_ACCESS_TOKEN()
    cid = _check(requests.post(f"{graph()}/{uid}/media", data={"media_type": "STORIES", "image_url": image_url,
                                                             "access_token": tok}, timeout=60))["id"]
    for _ in range(20):
        time.sleep(6)
        s = _check(requests.get(f"{graph()}/{cid}", params={"fields": "status_code", "access_token": tok}, timeout=60))
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"story processing failed: {s}")
    return _check(requests.post(f"{graph()}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok},
                                timeout=60))["id"]


def followers():
    j = _check(requests.get(f"{graph()}/{C.IG_USER_ID()}", params={"fields": "followers_count,media_count",
               "access_token": C.IG_ACCESS_TOKEN()}, timeout=60))
    return j.get("followers_count"), j.get("media_count")
