"""Offline acceptance test - NO API keys, NO network. Run:  python tests/offline_test.py
(needs ffmpeg, numpy, pillow with raqm; on macOS: brew install fribidi and
 DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib python tests/offline_test.py)

Checks: per-platform schedule, story rules + series split, similarity filter, variety rotation, captions and
YouTube metadata, the approval rules + never-post-twice publishing, the Telegram creative-director flow, the
experiment engine, and renders a FULL Short (main.py dry run) and a 2-minute 16:9 long-form sample, verifying
resolution, duration, captions and loudness."""
import datetime as dt, json, os, random, shutil, subprocess, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "IG_USER_ID", "IG_ACCESS_TOKEN", "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
          "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.pop(k, None)
os.environ.update(OFFLINE="true", ALLOW_SILENT_TTS="true", YT_POST_TIMES="08:00,19:30", IG_POST_TIMES="20:30",
                  TIMEZONE="Asia/Kolkata", WORK_DIR=os.path.join(ROOT, "test_work"), MUSIC="true", SERIES_EVERY="3",
                  REVIEW_MODE="phase", START_DATE=dt.date.today().isoformat(), APPROVAL_TIMEOUT="true")
sys.path.insert(0, os.path.join(ROOT, "src"))
import config as C, slots, story as S, captions, render, publisher, postqueue, director, similarity, variety as V
import experiments, storage, notify, main



def run_tests():
    global W
    W = os.environ["WORK_DIR"]
    shutil.rmtree(W, ignore_errors=True)

    # ---------------------------------------------------------------- per-platform schedule
    IST = slots.TZ
    at = lambda y, mo, d, h, mi: dt.datetime(y, mo, d, h, mi, tzinfo=IST)
    ks = lambda p, *a, done=(), ahead=8: [slots.key(s, p) for s in slots.upcoming(p, at(*a), set(done), ahead)]
    assert ks("yt", 2026, 11, 16, 1, 0) == ["2026-11-16-yt-0800"]                     # night run -> morning Short
    assert ks("yt", 2026, 11, 16, 12, 0) == ["2026-11-16-yt-1930"]                    # midday -> evening Short
    assert ks("ig", 2026, 11, 16, 13, 0) == ["2026-11-16-ig-2030"]
    assert ks("ig", 2026, 11, 16, 13, 0, done=["2026-11-16-ig-2030"]) == []
    assert ks("ig", 2026, 11, 16, 21, 30) == ["2026-11-16-ig-2030"]                   # a bit late: still today's slot
    assert slots.key(slots.next_slot("ig", at(2026, 11, 16, 21, 0)), "ig") == "2026-11-17-ig-2030"
    assert slots.times("ytlong", dt.date(2026, 11, 22)) == [dt.time(11, 0)] and slots.times("ytlong", dt.date(2026, 11, 21)) == []
    assert slots.hindi_time(at(2026, 11, 17, 20, 30)) == "कल रात 8:30 बजे"
    print("schedule ok")

    # ---------------------------------------------------------------- story rules
    s = S.sample()
    assert len(s["scenes"]) == 13 and s["twist_scene"] == 9 and s["length"] == "long" and s["voice"] in V.VOICES["female"]
    for bad in (dict(s, title="Doraemon ka naya raaz aur jaadu"),):
        try:
            S.validate(json.loads(json.dumps(bad, ensure_ascii=False)), (), length="long"); raise AssertionError("banned accepted")
        except ValueError:
            pass
    eng = json.loads(json.dumps(s, ensure_ascii=False)); eng["scenes"][3]["narration_hi"] = "Meera was ten and she asked questions."
    try:
        S.validate(eng, (), length="long"); raise AssertionError("English narration was accepted")
    except ValueError:
        pass
    ser = json.loads(json.dumps(s, ensure_ascii=False))
    ser["scenes"] = ser["scenes"][:8] + ser["scenes"][8:13] + [dict(ser["scenes"][2]), dict(ser["scenes"][3])]
    ser.update(twist_scene=9, recap_hi="पिछली बार मीरा लालटेन लेकर कुएँ तक पहुँची, और अंदर से एक आवाज़ आई। अब आगे...",
               part2_title="Kuan bola - mujhe kisi ne dhanyavaad nahi kaha! Part 2 😱", premise="A different premise for the series test.")
    ser = S.validate(ser, (), series=True); ser = S.finish(dict(ser, series=True, theme="village_legend"), V.pick([]), "mid")
    p1, p2 = S.make_parts(ser)
    assert p1["part"] == 1 and p2["part"] == 2 and p1["series_id"] == p2["series_id"] and p1["end_card"] == "part2"
    assert len(p1["scenes"]) == 8 and p2["scenes"][0]["narration_hi"].startswith("पिछली") and p2["twist_scene"] == 2
    hist = [{"part": None, "theme": "night_mystery"}, {"part": None, "theme": "magic_rule"}]
    assert S.is_series_turn(hist) and not S.is_series_turn(hist[:1])
    assert S.pending_part2(hist + [{"part": 1, "series_id": "s1"}]) == "s1"
    assert S.pick_theme(hist) not in ("night_mystery", "magic_rule")
    prompt = S.build_prompt("cursed_object", [], False, "short", V.pick([]), {"premise": "A mirror shows tomorrow", "note": "hero is an old tailor"})
    assert "old tailor" in prompt and "A mirror shows tomorrow" in prompt and "45-60 seconds" in prompt
    print("story rules + series split ok")

    # ---------------------------------------------------------------- similarity filter (offline n-gram method)
    a = "A village well sings on full moon nights and steals happy memories until a curious girl thanks it."
    bank = similarity.remember([], a, "t", similarity.embed(a))
    ok, sim, _, _ = similarity.check("On every full moon a village well sings and takes away happy memories, until a girl thanks it.", bank)
    assert not ok and sim > 0.8, sim
    ok, sim, _, _ = similarity.check("A greedy landlord buys a talking clock that eats lies, and a poor tailor tricks it.", bank)
    assert ok and sim < 0.6, sim
    print(f"similarity filter ok (near-copy rejected, different story {sim:.2f} accepted)")

    # ---------------------------------------------------------------- variety rotation
    h, prev = [], None
    for i in range(8):
        v = V.pick(h)
        if prev:
            assert all(v[d] != prev[d] for d in ("style", "structure", "caption", "end", "music")), (v, prev)
        h.append({"variety": v}); prev = v
    assert len({x["variety"]["style"] for x in h[:5]}) == 5 and len({x["variety"]["structure"] for x in h[:7]}) == 7
    assert V.pick(h, {"style": "papercut"})["style"] == "papercut"
    print("variety rotation ok")

    # ---------------------------------------------------------------- captions / YouTube metadata
    vs = S.make_parts(s)[0]
    cap = captions.ig_caption(vs, 4)
    assert cap.splitlines()[0].endswith("| Hindi Kahani | Raaz Kahani | AI Story")
    tags = [w for w in cap.split() if w.startswith("#")]
    assert len(tags) == 5 and "#hindikahani" in tags and "#raazkahani" in tags and "AI-animated story" in cap
    assert "Part 2 kal raat 8:30 baje" in captions.ig_caption(p1, 1, "kal raat 8:30 baje")
    y = captions.yt_meta(dict(p1), 1, "https://youtu.be/LONG1")
    t = y["snippet"]["title"]
    assert t.endswith(" #shorts") and len(t[:-len(" #shorts")]) <= 70 and "(Part 1)" in t
    assert y["status"]["containsSyntheticMedia"] is True and y["status"]["selfDeclaredMadeForKids"] is False
    assert y["snippet"]["categoryId"] == "24" and "youtu.be/LONG1" in y["snippet"]["description"]
    lm = captions.long_meta(dict(s), [(0, "अध्याय 1"), (190.5, "अध्याय 2"), (400, "अध्याय 3")])
    assert "0:00 अध्याय 1" in lm["snippet"]["description"] and "3:10 अध्याय 2" in lm["snippet"]["description"]
    print("captions + YouTube metadata ok")

    # ---------------------------------------------------------------- queue, approvals, never post twice
    store = {}
    real = {k: getattr(storage, k) for k in ("get_json", "put_json", "delete", "list_names")}
    storage.get_json = lambda k, d=None: json.loads(json.dumps(store[k])) if k in store else d
    storage.put_json = lambda k, v: store.__setitem__(k, json.loads(json.dumps(v)))
    storage.delete = lambda k: store.pop(k, None)
    storage.list_names = lambda p: [k.split("/", 1)[1] for k in store if k.startswith(p + "/")]
    _sleep = publisher.time.sleep
    publisher.time.sleep = lambda s: None
    msgs = []
    notify.text = msgs.append
    notify.edit = lambda *a, **k: None
    calls = {"ig": 0}
    publisher.instagram.already_posted = lambda c: None
    publisher.instagram.post_story = lambda u: "story1"


    def ig_post(*a):
        calls["ig"] += 1
        if calls.get("fail"):
            raise RuntimeError("Instagram is down")
        return f"m{calls['ig']}"


    publisher.instagram.post_reel = ig_post
    C.PLATFORM = "both"
    assert C.review_mode() == "approve"                                                # day 0 of 30
    now = time.time()
    mk = lambda i, **kw: dict({"id": i, "platform": "ig", "post_at": int(now - 10), "video_url": "x", "caption": cap,
                               "title": i, "created_ts": int(now), "approval": "pending", "attempts": 0}, **kw)
    postqueue.put(mk("a-later", post_at=int(now + 9 * 3600)))                          # not due yet
    postqueue.put(mk("b-timeout"))                                                      # due, owner silent
    postqueue.put(mk("c-approved", approval="approved"))
    postqueue.put(mk("d-skip", approval="skipped"))
    postqueue.put(mk("e-redo", approval="redo"))
    publisher.run(wait=False)
    left = {i["id"] for i in postqueue.items()}
    assert left == {"a-later"}, left
    posted = {x["slot"]: x for x in store["history.json"]}
    assert posted["b-timeout"]["approval"] == "auto" and posted["c-approved"]["media_id"]
    assert posted["d-skip"]["kind"] == "skip" and "e-redo" not in posted and calls["ig"] == 2
    assert any(d["by"] == "auto" for d in store["decisions.json"])
    calls["fail"] = True
    postqueue.put(mk("f-fail", approval="approved"))
    publisher.run(wait=False)
    assert postqueue.get("f-fail")["attempts"] == 1 and any("Nothing was posted" in m for m in msgs)
    calls["fail"] = False
    postqueue.update("f-fail", done="m-already")                                          # it went out before a crash
    before = calls["ig"]
    publisher.run(wait=False)
    assert calls["ig"] == before and "f-fail" not in {i["id"] for i in postqueue.items()}  # never posted twice
    print("approval rules + never-post-twice ok")

    # ---------------------------------------------------------------- creative director (Telegram buttons)
    C.telegram_enabled = lambda: True
    C.TELEGRAM_CHAT_ID = lambda: "1"
    sent = []
    notify.send = lambda m, b=None: sent.append((m, b)) or 77
    notify.answer = lambda *a: None
    today = director._today()
    store["director.json"] = {"date": today, "status": "open", "sent_ts": int(time.time()), "msg_id": 5, "choice": None,
                              "options": [{"hook": f"Hook {i}", "premise": f"Premise {i}", "theme": "magic_rule"} for i in range(3)]}
    assert director.waiting()
    ups = [{"update_id": 10, "callback_query": {"id": "c1", "data": f"rk:pick:{today}:2", "message": {"chat": {"id": 1}}}},
           {"update_id": 11, "message": {"chat": {"id": 1}, "text": "hero ek budhi naani ho"}}]
    notify.updates = lambda off=None: ups if off is None else []
    assert director.poll() == 2
    ch = director.take_choice()
    assert ch["by"] == "owner" and ch["premise"] == "Premise 2" and "naani" in ch["note"]
    assert director.take_choice() is None                                                # used once
    store["director.json"] = dict(store["director.json"], status="open", choice=None, note="")
    assert director.take_choice()["by"] == "auto"                                        # owner silent -> best option
    postqueue.put(mk("g-ok", post_at=int(now + 3600)))
    ups[:] = [{"update_id": 12, "callback_query": {"id": "c2", "data": "rk:ok:g-ok", "message": {"chat": {"id": 1}}}}]
    store.pop("telegram.json", None)
    director.poll()
    assert postqueue.get("g-ok")["approval"] == "approved"
    assert sum(1 for d in store["decisions.json"] if d["by"] == "owner") >= 3
    C.telegram_enabled = lambda: False
    publisher.time.sleep = _sleep
    print("creative director flow ok")

    # ---------------------------------------------------------------- experiment engine
    rows = {}
    rng = random.Random(1)
    for i in range(40):
        good = i % 2 == 0
        rows[f"r{i}"] = {"platform": "yt", "slot": f"2026-12-0{i % 9 + 1}-yt-{'1930' if good else '0800'}",
                         "theme": "magic_rule" if good else "family_love", "length": "mid", "posted_ts": time.time() - 9 * 86400,
                         "m72": {"views": 20000 if good else 2000, "avg_pct": 80 if good else 50,
                                 "shares": 60 if good else 3, "follows": 30 if good else 1}}
    experiments._cache.clear(); experiments._cache["stats"] = rows; experiments._cache["schedule"] = {}
    C.START_DATE = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    assert experiments.phase() == 2
    picks = [experiments.thompson("yt", "theme", ["magic_rule", "family_love"], rng) for _ in range(200)]
    assert picks.count("magic_rule") > 150, picks.count("magic_rule")                    # winner exploited, 20% explore
    storage.put_json = lambda k, v: None
    sched = experiments.schedule("yt", dt.date(2027, 1, 5))
    assert 1 <= len(sched) <= C.MAX_SHORTS_PER_DAY and all(t in C.YT_SLOT_CANDIDATES for t in sched)
    for r in rows.values():
        r["posted_ts"] = time.time() - 2 * 86400; r["m72"]["avg_pct"] = 40
    assert experiments.quality_low() and experiments.shorts_per_day() == 1
    assert experiments.report_lines()
    C.START_DATE = dt.date.today().isoformat()
    experiments._cache.clear()
    for k, f in real.items():
        setattr(storage, k, f)
    print("experiment engine ok")

    # ---------------------------------------------------------------- full Short (main.py dry run)
    os.environ["DRY_RUN"] = "true"
    out = main.run()
    need = ["story.json", "video.mp4", "cover.jpg", "caption.txt", "yt_meta.json"]
    missing = [f for f in need if not os.path.exists(os.path.join(W, f))]
    assert not missing, f"dry run did not produce {missing}"


    def probe(path):
        p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                       "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate",
                                       "-of", "json", path], capture_output=True, text=True, check=True).stdout)
        v = next(x for x in p["streams"] if x["codec_type"] == "video")
        a = next(x for x in p["streams"] if x["codec_type"] == "audio")
        with open(path, "rb") as f:
            head = f.read(200_000)
        assert v["codec_name"] == "h264" and a["codec_name"] == "aac" and v["r_frame_rate"] == "30/1", (v, a)
        assert 0 <= head.find(b"moov") < max(head.find(b"mdat"), 1), "moov atom not at the front (faststart)"
        return v["width"], v["height"], float(p["format"]["duration"])


    def colour_share(video, t, box):
        from PIL import Image
        import numpy as np
        png = os.path.join(W, f"probe_{t:.2f}.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", png], check=True)
        a = np.asarray(Image.open(png).convert("RGB").crop(box), dtype=np.int16)
        yel = (a[..., 0] > 200) & (a[..., 1] > 170) & (a[..., 2] < 90)
        return yel.mean(), (a > 225).all(axis=-1).mean()


    video = os.path.join(W, "video.mp4")
    w, hgt, dur = probe(video)
    m = out["meta"]
    assert (w, hgt) == (1080, 1920) and 45 <= dur <= 172, (w, hgt, dur)
    assert m["caption_groups"] >= 13 and m["caption_frames"] > m["frames"] * 0.5 and abs(m["lufs"] + 14) <= 1.5, m
    st, d = m["timeline"][3]
    cy, cw = colour_share(video, st + d * 0.5, (100, render.FORMATS["short"]["CAP_Y"] - 90, 980, render.FORMATS["short"]["CAP_Y"] + 90))
    assert cy > 0.002 and cw > 0.005, f"no karaoke caption (yellow {cy:.4f}, white {cw:.4f})"
    assert colour_share(video, 1.0, render.FORMATS["short"]["HOOK_BOX"])[0] > 0.01, "no hook text in the first 2 s"
    print(f"Short ok: {dur:.1f}s 1080x1920, {m['caption_groups']} caption groups, {m['lufs']} LUFS, "
          f"{m['workers']} render workers, raqm={m['raqm']}")

    # ---------------------------------------------------------------- 2-minute 16:9 long-form sample
    import longform
    lo = longform.produce(longform.sample_long(), os.path.join(W, "long"))
    w, hgt, dur = probe(lo["video"])
    lm_ = lo["meta"]
    assert (w, hgt) == (1920, 1080) and 100 <= dur <= 200, (w, hgt, dur)
    assert lm_["caption_frames"] > lm_["frames"] * 0.5 and abs(lm_["lufs"] + 14) <= 1.5, lm_
    assert lo["chapters"][0][0] == 0 and len(lo["chapters"]) == 2 and os.path.exists(lo["thumb"])
    st, d = lm_["timeline"][3]
    cy, _ = colour_share(lo["video"], st + d * 0.5, (200, render.FORMATS["long"]["CAP_Y"] - 80, 1720, render.FORMATS["long"]["CAP_Y"] + 80))
    assert cy > 0.001, f"no long-form captions ({cy:.4f})"
    print(f"long-form ok: {dur:.1f}s 1920x1080, chapters {[round(t) for t, _ in lo['chapters']]}, {lm_['lufs']} LUFS")
    print("ALL OFFLINE TESTS PASSED")


if __name__ == "__main__":      # render workers re-import this file on macOS
    run_tests()
