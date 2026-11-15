"""YouTube Shorts via the official Data API v3 (resumable upload). Enabled when YT_CLIENT_ID, YT_CLIENT_SECRET and
YT_REFRESH_TOKEN exist (get the refresh token once with: python tools/youtube_auth.py).
Every upload: categoryId 24 (Entertainment), language hi, selfDeclaredMadeForKids=false (the stories are for a
general 13+ audience, NOT made for kids) and containsSyntheticMedia=true (AI disclosure).
NOTE: Google keeps uploads from an UNVERIFIED API project PRIVATE until the project passes the free YouTube API
compliance audit - see README."""
import json, os, re, time, requests
import config as C
from util import log, retry, redact

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API = "https://www.googleapis.com/youtube/v3"
MAIN_PLAYLIST = (C.CHANNEL, f"{C.CHANNEL} - roz ek nayi rahasya aur jaadu bhari Hindi kahani. Original AI animated stories.")


def enabled():
    return all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


_tok = {}


def _access_token():
    if _tok.get("exp", 0) > time.time() + 60:
        return _tok["v"]
    r = requests.post(TOKEN_URL, data={"client_id": os.environ["YT_CLIENT_ID"],
                                       "client_secret": os.environ["YT_CLIENT_SECRET"],
                                       "refresh_token": os.environ["YT_REFRESH_TOKEN"],
                                       "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"YouTube auth failed {r.status_code}: {r.text[:200]} (re-run tools/youtube_auth.py)")
    j = r.json()
    _tok.update(v=j["access_token"], exp=time.time() + int(j.get("expires_in", 3000)))
    return _tok["v"]


def _api(method, path, tok=None, **kw):
    tok = tok or _access_token()
    r = requests.request(method, f"{API}/{path}", headers={"Authorization": f"Bearer {tok}"}, timeout=60, **kw)
    if r.status_code >= 300:
        raise RuntimeError(redact(f"YouTube {path} {r.status_code}: {r.text[:200]}"))
    return r.json() if r.text else {}


def channel():
    """(channel id, title, uploads playlist id)"""
    it = _api("GET", "channels", params={"part": "id,snippet,contentDetails", "mine": "true"}).get("items", [])
    if not it:
        raise RuntimeError("no YouTube channel on this Google account")
    c = it[0]
    return c["id"], c["snippet"]["title"], c["contentDetails"]["relatedPlaylists"]["uploads"]


def recent_titles(n=10):
    """[(title, video_id, published_ts)] of the newest uploads (1 quota unit each call)."""
    _, _, uploads = channel()
    j = _api("GET", "playlistItems", params={"part": "snippet", "playlistId": uploads, "maxResults": n})
    out = []
    import datetime as dt
    for it in j.get("items", []):
        sn = it["snippet"]
        try:
            ts = dt.datetime.fromisoformat(sn.get("publishedAt", "").replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = 0
        out.append((sn.get("title", ""), sn.get("resourceId", {}).get("videoId"), ts))
    return out


def already_posted(title, within_hours=30):
    """Video id of an upload with the same title in the last `within_hours`, else None (never-post-twice)."""
    cutoff = time.time() - within_hours * 3600
    try:
        return next((vid for t, vid, ts in recent_titles() if t.strip() == title.strip() and ts >= cutoff), None)
    except Exception as e:
        log("Could not read recent YouTube uploads (continuing):", e)
        return None


def _playlist_id(title, desc, tok):
    mine = _api("GET", "playlists", tok, params={"part": "snippet", "mine": "true", "maxResults": 50}).get("items", [])
    pid = next((p["id"] for p in mine if p["snippet"]["title"] == title), None)
    if not pid:
        pid = _api("POST", "playlists", tok, params={"part": "snippet,status"},
                   json={"snippet": {"title": title, "description": desc, "defaultLanguage": "hi"},
                         "status": {"privacyStatus": "public"}})["id"]
        log("Created YouTube playlist:", title)
    return pid


def add_to_playlist(video_id, title, desc="", tok=None):
    """Adds the Short to a playlist (created the first time). Never raises."""
    try:
        tok = tok or _access_token()
        pid = _playlist_id(title[:150], desc, tok)
        for attempt in range(8):                         # a just-uploaded video is still processing (409)
            try:
                _api("POST", "playlistItems", tok, params={"part": "snippet"},
                     json={"snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": video_id}}})
                log("Added to playlist:", title)
                return True
            except RuntimeError as e:
                if " 409" not in str(e) or attempt == 7:
                    raise
                time.sleep(20)
    except Exception as e:
        log("Playlist step skipped:", e)
    return False


def recent_comment_threads(tok, max_results=50):
    """Top-level comments on the channel's videos, newest first."""
    me = _api("GET", "channels", tok, params={"part": "id", "mine": "true"}).get("items", [{}])[0].get("id")
    if not me:
        return me, []
    j = _api("GET", "commentThreads", tok, params={"part": "snippet,replies", "allThreadsRelatedToChannelId": me,
                                                    "maxResults": max_results, "order": "time", "textFormat": "plainText"})
    return me, j.get("items", [])


def reply(tok, parent_id, text):
    _api("POST", "comments", tok, params={"part": "snippet"},
         json={"snippet": {"parentId": parent_id, "textOriginal": text[:2000]}})


def video_stats(ids):
    """{video_id: {views, likes, comments}} - 1 quota unit per 50 videos."""
    out = {}
    ids = [i for i in ids if i]
    for k in range(0, len(ids), 50):
        j = _api("GET", "videos", params={"part": "statistics", "id": ",".join(ids[k:k + 50])})
        for it in j.get("items", []):
            s = it.get("statistics", {})
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0))}
    return out


@retry(tries=3, delay=10)
def upload(video, meta):
    """Uploads a local file or storage URL with the given {snippet, status} metadata. Returns the video id."""
    if os.path.exists(video):
        with open(video, "rb") as f:
            data = f.read()
    else:
        data = requests.get(video, timeout=300).content
    if len(data) < 50_000:
        raise RuntimeError("could not read the video for YouTube")
    tok = _access_token()
    r = requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                      headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                               "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(data))},
                      data=json.dumps(meta), timeout=60)
    if r.status_code != 200 or "Location" not in r.headers:
        raise RuntimeError(f"YouTube upload init failed {r.status_code}: {r.text[:300]}")
    up = requests.put(r.headers["Location"], data=data, headers={"Content-Type": "video/mp4"}, timeout=900)
    if up.status_code not in (200, 201):
        raise RuntimeError(f"YouTube upload failed {up.status_code}: {up.text[:300]}")
    vid = up.json().get("id")
    log("YouTube Short uploaded:", vid)
    return vid


def set_thumbnail(video_id, jpg_path, tok=None):
    """Custom thumbnails need a phone-verified channel (and may be ignored for Shorts); failure is harmless."""
    try:
        tok = tok or _access_token()
        with open(jpg_path, "rb") as f:
            r = requests.post("https://www.googleapis.com/upload/youtube/v3/thumbnails/set", params={"videoId": video_id},
                              headers={"Authorization": f"Bearer {tok}", "Content-Type": "image/jpeg"}, data=f.read(),
                              timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
        return True
    except Exception as e:
        log("Thumbnail not set (harmless):", e)
        return False
