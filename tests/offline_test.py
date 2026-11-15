"""Offline acceptance test - NO API keys, NO network. Run:  python tests/offline_test.py
(needs ffmpeg, numpy, pillow with raqm; on macOS: brew install fribidi and
 DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib python tests/offline_test.py)

Checks: schedule maths, story validation + series split + theme rotation, captions/YouTube metadata rules,
never-post-twice publishing (Instagram ok + YouTube failing -> the retry only uploads YouTube), and renders a
FULL sample video through main.py's dry run (placeholder images, silent TTS) and verifies the outputs."""
import datetime as dt, json, os, shutil, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "IG_USER_ID", "IG_ACCESS_TOKEN", "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
          "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.pop(k, None)
os.environ.update(OFFLINE="true", ALLOW_SILENT_TTS="true", POST_TIMES="19:00", TIMEZONE="Asia/Kolkata",
                  DRY_RUN="true", WORK_DIR=os.path.join(ROOT, "test_work"), MUSIC="true", SERIES_EVERY="3",
                  REVIEW_MODE="auto")
sys.path.insert(0, os.path.join(ROOT, "src"))
import slots, story as S, captions, render, publisher, main

W = os.environ["WORK_DIR"]
shutil.rmtree(W, ignore_errors=True)

# ---------------------------------------------------------------- scheduling (one post a day at 19:00 IST)
IST = slots.TZ
at = lambda y, mo, d, h, mi: dt.datetime(y, mo, d, h, mi, tzinfo=IST)
up = lambda *a, done=(): slots.key(slots.upcoming_slot(at(*a), done=set(done)))
assert up(2026, 11, 14, 23, 7, done=["2026-11-14"]) == "2026-11-15"     # night-before run -> tomorrow's video
assert up(2026, 11, 14, 23, 7) == "2026-11-15"                          # yesterday's grace is over -> tomorrow
assert up(2026, 11, 15, 8, 13) == "2026-11-15"                          # morning backup -> today 19:00
assert up(2026, 11, 15, 20, 30) == "2026-11-15"                         # a bit late -> still today's slot
assert up(2026, 11, 15, 20, 30, done=["2026-11-15"]) == "2026-11-16"
print("schedule ok")

# ---------------------------------------------------------------- story rules
s = S.sample()
assert 8 <= len(s["scenes"]) <= 14 and s["twist_scene"] == 9
for bad, why in ((dict(s, title="Doraemon ka naya raaz aur jaadu"), "banned"),):
    try:
        S.validate(json.loads(json.dumps(bad, ensure_ascii=False)), ())
        raise AssertionError(f"{why} story was accepted")
    except ValueError:
        pass
eng = json.loads(json.dumps(s, ensure_ascii=False)); eng["scenes"][3]["narration_hi"] = "Meera was ten and she asked questions."
try:
    S.validate(eng, ()); raise AssertionError("English narration was accepted")
except ValueError:
    pass
try:
    S.validate(json.loads(json.dumps(s, ensure_ascii=False)), [{"premise": s["premise"]}]); raise AssertionError("repeat premise accepted")
except ValueError:
    pass
# a 2-part series: Part 1 stops right before the twist, Part 2 = recap + twist onwards
ser = json.loads(json.dumps(s, ensure_ascii=False))
extra = [dict(x) for x in ser["scenes"][9:12]] + [dict(x) for x in ser["scenes"][2:4]]
ser["scenes"] = ser["scenes"][:9] + [dict(ser["scenes"][2])] + ser["scenes"][9:] + extra[:3]
ser.update(twist_scene=11, recap_hi="पिछली बार मीरा लालटेन लेकर कुएँ तक पहुँची, और अंदर से एक आवाज़ आई। अब आगे...",
           part2_title="Kuan bola - mujhe kisi ne dhanyavaad nahi kaha! Part 2 😱", premise="A different premise for the series test only.")
ser = S.validate(ser, (), series=True); ser.update(series=True, theme="village_legend")
p1, p2 = S.make_parts(ser)
assert p1["part"] == 1 and p2["part"] == 2 and p1["series_id"] == p2["series_id"]
assert len(p1["scenes"]) == 10 and p1["end_card"] == "part2" and p1["twist_scene"] is None
assert p2["scenes"][0]["narration_hi"].startswith("पिछली") and p2["twist_scene"] == 2
assert p2["scenes"][1]["narration_hi"] == ser["scenes"][10]["narration_hi"]
hist = [{"part": None, "theme": "night_mystery"}, {"part": None, "theme": "magic_rule"}]
assert S.is_series_turn(hist) and not S.is_series_turn(hist[:1])
assert S.pending_part2(hist + [{"part": 1, "series_id": "s1"}]) == "s1"
assert S.pending_part2(hist + [{"part": 1, "series_id": "s1"}, {"part": 2, "series_id": "s1"}]) is None
assert S.pick_theme(hist) not in ("night_mystery", "magic_rule")
print("story rules + series split ok")

# ---------------------------------------------------------------- captions / YouTube metadata
vs = S.make_parts(s)[0]
cap = captions.ig_caption(vs, 4)
first = cap.splitlines()[0]
assert first.endswith("| Hindi Kahani | Raaz Kahani | AI Story") and first.startswith(vs["title"]), first
tags = [w for w in cap.split() if w.startswith("#")]
assert len(tags) == 5 and len(set(tags)) == 5 and "#hindikahani" in tags, tags
assert "AI-generated" in cap and vs["cta_question"] in cap
assert captions.hashtag_set(0) != captions.hashtag_set(1)
y = captions.yt_meta(dict(p1), 1)
assert len(y["snippet"]["title"]) <= 100 and y["snippet"]["title"].endswith("#shorts")
assert "(Part 1)" in y["snippet"]["title"]
assert y["status"]["containsSyntheticMedia"] is True and y["status"]["selfDeclaredMadeForKids"] is False
assert y["snippet"]["categoryId"] == "24" and y["snippet"]["defaultLanguage"] == "hi"
assert "AI" in y["snippet"]["description"] and "hindi kahani" in y["snippet"]["tags"]
long_t = captions.yt_title(dict(vs, title="Ek " * 60))
assert len(long_t) <= 100 and long_t.endswith("#shorts")
print("captions + YouTube metadata ok")

# ---------------------------------------------------------------- never post twice
store, calls = {}, {"ig": 0, "yt": 0}
publisher.storage.get_json = lambda k, d=None: json.loads(json.dumps(store[k])) if k in store else d
publisher.storage.put_json = lambda k, v: store.__setitem__(k, json.loads(json.dumps(v)))
publisher.storage.delete = lambda k: store.pop(k, None)
_sleep = publisher.time.sleep
publisher.time.sleep = lambda s: None
msgs = []
publisher.notify.text = msgs.append
publisher.notify.latest_command = lambda ts: None
publisher.instagram.already_posted = lambda c: None
publisher.youtube.already_posted = lambda t: None
publisher.youtube.add_to_playlist = lambda *a, **k: True


def ig_post(*a):
    calls["ig"] += 1; return "1789"


def yt_fail(*a):
    calls["yt"] += 1; raise RuntimeError("quota exceeded")


def yt_ok(*a):
    calls["yt"] += 1; return "abcDEF12345"


publisher.instagram.post_reel = ig_post
publisher.youtube.upload = yt_fail
store["pending.json"] = {"slot": "2026-11-15", "post_at": 0, "created_ts": 0, "video_url": "x", "caption": cap,
                         "yt_meta": y, "platforms": ["ig", "yt"], "title": vs["title"], "done": {}, "attempts": 0}
assert publisher.run(wait=False) == "partial"
assert store["pending.json"]["done"] == {"ig": "1789"} and calls == {"ig": 1, "yt": 1}
assert any("Already live on: Instagram" in m for m in msgs), msgs
publisher.youtube.upload = yt_ok
assert publisher.run(wait=False) == "posted"
assert calls == {"ig": 1, "yt": 2}, calls                  # Instagram was NOT posted again
assert "pending.json" not in store and store["history.json"][-1]["youtube_id"] == "abcDEF12345"
assert store["history.json"][-1]["media_id"] == "1789"
assert publisher.run(wait=False) == "nothing"
publisher.time.sleep = _sleep
print("never-post-twice publishing ok")

# ---------------------------------------------------------------- full dry run render (main.py)
import importlib
for m in ("storage", "notify"):
    importlib.reload(sys.modules[m])
importlib.reload(main)
out = main.run()
need = ["story.json", "video.mp4", "cover.jpg", "caption.txt", "yt_meta.json"]
missing = [f for f in need if not os.path.exists(os.path.join(W, f))]
assert not missing, f"dry run did not produce {missing}"
video = os.path.join(W, "video.mp4")
probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                   "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate",
                                   "-of", "json", video], capture_output=True, text=True, check=True).stdout)
vst = next(x for x in probe["streams"] if x["codec_type"] == "video")
ast = next(x for x in probe["streams"] if x["codec_type"] == "audio")
dur = float(probe["format"]["duration"])
assert (vst["width"], vst["height"]) == (1080, 1920), vst
assert vst["codec_name"] == "h264" and ast["codec_name"] == "aac" and vst["r_frame_rate"] == "30/1", (vst, ast)
assert 60 <= dur <= 150, f"duration {dur:.1f}s not in 60-150"
with open(video, "rb") as f:
    head = f.read(200_000)
assert 0 <= head.find(b"moov") < max(head.find(b"mdat"), 1), "moov atom not at the front (faststart)"
meta = out["meta"]
assert meta["caption_groups"] >= len(s["scenes"]) and meta["caption_frames"] > meta["frames"] * 0.5, meta
assert meta["lufs"] is not None and abs(meta["lufs"] - (-14)) <= 1.5, meta["lufs"]


def yellow_share(t, box):
    jpg = os.path.join(W, f"probe_{t:.2f}.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", jpg], check=True)
    from PIL import Image
    import numpy as np
    a = np.asarray(Image.open(jpg).convert("RGB").crop(box), dtype=np.int16)
    yel = (a[..., 0] > 200) & (a[..., 1] > 170) & (a[..., 2] < 90)
    wht = (a > 225).all(axis=-1)
    return yel.mean(), wht.mean()


st, d = meta["timeline"][3]
cy, cw = yellow_share(st + d * 0.5, (100, render.CAP_Y - 90, 980, render.CAP_Y + 90))
assert cy > 0.002 and cw > 0.005, f"no karaoke caption found (yellow {cy:.4f}, white {cw:.4f})"
hy, _ = yellow_share(1.0, render.HOOK_BOX)
assert hy > 0.01, f"no hook text in the first 2 s ({hy:.4f})"
ey, _ = yellow_share(dur - 0.5, (90, 500, 990, 950))
assert ey > 0.005, "no end card"
cap_txt = open(os.path.join(W, "caption.txt"), encoding="utf-8").read()
assert cap_txt.splitlines()[0].endswith("AI Story")
ytm = json.load(open(os.path.join(W, "yt_meta.json"), encoding="utf-8"))
assert ytm["status"]["containsSyntheticMedia"] is True
for i, (st, d) in enumerate(meta["timeline"]):          # one frame per scene for eyeballing
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{st + d * 0.6:.2f}", "-i", video, "-frames:v", "1",
                    os.path.join(W, f"frame_{i + 1:02d}.jpg")], check=True)
print(f"render ok: {dur:.1f}s 1080x1920 h264/aac 30fps, {meta['caption_groups']} caption groups, "
      f"{meta['lufs']} LUFS, raqm={meta['raqm']}, {os.path.getsize(video) / 1e6:.1f} MB -> {video}")
if not meta["raqm"]:
    print("⚠️ raqm text shaping was OFF: Hindi letters may look wrong locally (CI and check.py require it)")
print("ALL OFFLINE TESTS PASSED")
