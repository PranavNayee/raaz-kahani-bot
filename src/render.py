"""The video: Pillow draws every frame, ffmpeg encodes it (30 fps, 1080x1920, H.264 + AAC, faststart).

  * slow Ken Burns zoom/pan on each scene image (a reused frame gets a different move)
  * 0.3 s crossfades between scenes; a hard cut + 5-frame white flash + boom/whoosh on the twist
  * the hook in big text for the first ~2 s (with a PART 1 / PART 2 badge for series)
  * word-by-word karaoke captions in Devanagari (Noto Sans Devanagari Bold), inside the Reels/Shorts safe area
  * an end card: "Part 2 kal!" for Part 1 of a series, otherwise the comment question
"""
import os, re, subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features
import config as C, soundtrack
from util import log

W, H, FPS = 1080, 1920, 30
SRC_SCALE = 1.2                 # scene images are kept 20% larger than the frame so the camera can move
GAP = 0.35                      # breath after each scene's narration
XFADE = 0.3
HOOK_T = 2.3                    # hook text on screen for the first 2.3 s
ENDCARD = 2.8
FLASH = [0.95, 0.75, 0.5, 0.28, 0.1]
CAP_SIZE = 92
CAP_Y = 1265                    # caption centre: above the Reels/Shorts bottom UI, below the face area
CAP_MAXW = 820                  # 130 px clear of the left edge and the right-side buttons
HOOK_BOX = (90, 430, 990, 900)  # hook text area (below the top bar)
YELLOW, WHITE = (255, 214, 10), (255, 255, 255)
MOVES = ["in", "pan_r", "out", "up", "pan_l", "in"]

_fonts = {}
RAQM = features.check("raqm")


def font(size, devanagari=True):
    key = (size, devanagari)
    if key not in _fonts:
        name = "NotoSansDevanagari-Bold.ttf" if devanagari else "NotoSans-Bold.ttf"
        path = os.path.join(C.FONT_DIR, name)
        engine = ImageFont.Layout.RAQM if RAQM else ImageFont.Layout.BASIC
        _fonts[key] = ImageFont.truetype(path, size, layout_engine=engine)
    return _fonts[key]


def check_shaping():
    """Devanagari needs complex text shaping (libraqm + libfribidi), or matras land in the wrong place."""
    if RAQM:
        return True
    msg = ("libraqm text shaping is not available - Hindi captions would render incorrectly. Install libfribidi "
           "(Ubuntu: apt install libfribidi0, macOS: brew install fribidi and set DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib)")
    if os.environ.get("REQUIRE_RAQM", "true" if not C.OFFLINE else "false").lower() == "true":
        raise RuntimeError(msg)
    log("⚠️", msg)
    return False


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def no_emoji(t):
    return re.sub(r"\s+", " ", EMOJI.sub("", t or "")).strip()


def is_hindi(t):
    return bool(re.search(r"[ऀ-ॿ]", t))


def wrap(text, maxw, size, max_lines=4, min_size=40):
    """Largest font size at which `text` wraps into <= max_lines lines of width <= maxw."""
    dev = is_hindi(text)
    while True:
        f = font(size, dev)
        lines, cur = [], ""
        for w in text.split():
            t = (cur + " " + w).strip()
            if f.getlength(t) <= maxw or not cur:
                cur = t
            else:
                lines.append(cur); cur = w
        if cur:
            lines.append(cur)
        if len(lines) <= max_lines or size <= min_size:
            return f, lines, size
        size -= 4


def draw_block(d, text, box, size, fill=WHITE, stroke=8, max_lines=4, spacing=1.18):
    x0, y0, x1, y1 = box
    f, lines, sz = wrap(text, x1 - x0, size, max_lines)
    lh = sz * spacing
    y = (y0 + y1) / 2 - lh * len(lines) / 2 + lh / 2
    for ln in lines:
        d.text(((x0 + x1) / 2, y), ln, font=f, fill=fill, anchor="mm", stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += lh
    return len(lines)


# ---------------------------------------------------------------- overlays
def vignette_mask():
    """L-mode mask: 255 in the middle, darker towards the edges + a soft band behind the captions."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H * 0.46) / (H * 0.62)) ** 2)
    m = 1 - 0.55 * np.clip(r - 0.55, 0, 1) ** 1.4
    band = np.exp(-((yy - CAP_Y) / 170) ** 2) * 0.22
    m = np.clip(m - band, 0, 1)
    return Image.fromarray((m * 255).astype(np.uint8), "L")


def hook_overlay(text, badge=None):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = HOOK_BOX
    d.rounded_rectangle((x0 - 20, y0 - 20, x1 + 20, y1 + 20), 40, fill=(0, 0, 0, 120))
    if badge:
        f = font(46, False)
        bw = f.getlength(badge) + 60
        d.rounded_rectangle((W / 2 - bw / 2, y0 - 95, W / 2 + bw / 2, y0 - 25), 34, fill=(220, 30, 60, 255))
        d.text((W / 2, y0 - 60), badge, font=f, fill=WHITE, anchor="mm")
    draw_block(d, no_emoji(text), (x0 + 20, y0, x1 - 20, y1), 96, fill=YELLOW, stroke=9, max_lines=4)
    return img


def end_card(bg, kind, story):
    img = bg.filter(ImageFilter.GaussianBlur(14)).convert("RGBA")
    img = Image.alpha_composite(img, Image.new("RGBA", (W, H), (0, 0, 0, 150)))
    d = ImageDraw.Draw(img)
    if kind == "part2":
        when = C.POST_TIMES[0] if C.POST_TIMES else "19:00"
        h, m = (int(x) for x in when.split(":"))
        draw_block(d, "Part 2 kal!", (80, 560, 1000, 820), 150, fill=YELLOW, stroke=10, max_lines=1)
        draw_block(d, f"कल शाम {h % 12 or 12} बजे", (80, 860, 1000, 1000), 84, stroke=7, max_lines=1)
        draw_block(d, "Follow karo taaki raaz chhoot na jaaye", (100, 1060, 980, 1260), 62, stroke=6, max_lines=2)
    else:
        draw_block(d, no_emoji(story.get("cta_question") or "Aap hote toh kya karte?"), (90, 560, 990, 900), 100,
                   fill=YELLOW, stroke=9, max_lines=3)
        draw_block(d, "Comment mein batao", (100, 960, 980, 1080), 70, stroke=7, max_lines=1)
        draw_block(d, "Roz ek nayi raaz bhari kahani - Follow karo", (100, 1120, 980, 1300), 54, stroke=6, max_lines=2)
    draw_block(d, C.CHANNEL.upper(), (100, 330, 980, 430), 58, fill=(230, 230, 255), stroke=6, max_lines=1)
    return img.convert("RGB")


# ---------------------------------------------------------------- captions
def caption_groups(words, scene_bounds, max_words=4):
    """Splits timed words into short on-screen groups (never across scenes). [(t0, t1, [(s, e, w)])]"""
    groups = []
    f = font(CAP_SIZE)
    for a, b in scene_bounds:
        ws = [w for w in words if a <= w[0] < b]
        cur = []
        for w in ws:
            t = " ".join(x[2] for x in cur + [w])
            if cur and (len(cur) >= max_words or f.getlength(t) > CAP_MAXW or re.search(r"[,.?!।]$", cur[-1][2])):
                groups.append(cur); cur = []
            cur.append(w)
        if cur:
            groups.append(cur)
    out = []
    for i, g in enumerate(groups):
        t0 = g[0][0] - 0.05
        nxt = groups[i + 1][0][0] - 0.05 if i + 1 < len(groups) else g[-1][1] + 0.6
        t1 = min(nxt, g[-1][1] + 0.6)
        out.append((t0, t1, g))
    return out


class CaptionCache:
    def __init__(self):
        self.cache = {}

    def get(self, gi, group, active):
        key = (gi, active)
        if key not in self.cache:
            if len(self.cache) > 64:
                self.cache.clear()
            f = font(CAP_SIZE)
            ws = [w[2] for w in group]
            space = f.getlength(" ")
            widths = [f.getlength(w) for w in ws]
            total = sum(widths) + space * (len(ws) - 1)
            img = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            x = (W - total) / 2
            for k, (w, wd) in enumerate(zip(ws, widths)):
                d.text((x, 100), w, font=f, fill=YELLOW if k == active else WHITE, anchor="lm",
                       stroke_width=8, stroke_fill=(0, 0, 0))
                x += wd + space
            self.cache[key] = img
        return self.cache[key]


# ---------------------------------------------------------------- camera
def ease(p):
    p = min(max(p, 0.0), 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * p)


def camera(move, p, punch=0.0):
    """(zoom, cx, cy) in frame coordinates for progress p in [0, 1]."""
    e = ease(p)
    if move == "in":
        z, cx, cy = 1.0 + 0.12 * e, W / 2, H / 2
    elif move == "out":
        z, cx, cy = 1.12 - 0.12 * e, W / 2, H / 2
    elif move == "close":
        z, cx, cy = 1.14 + 0.06 * e, W / 2, H * 0.45
    else:
        z = 1.1
        hx, hy = W / 2 - W / (2 * z), H / 2 - H / (2 * z)
        cx, cy = W / 2, H / 2
        if move == "pan_r":
            cx = W / 2 - hx + 2 * hx * e
        elif move == "pan_l":
            cx = W / 2 + hx - 2 * hx * e
        elif move == "up":
            cy = H / 2 + hy - 2 * hy * e
    z += punch
    hx, hy = W / (2 * z), H / (2 * z)
    cx, cy = min(max(cx, hx), W - hx), min(max(cy, hy), H - hy)
    return z, cx, cy


def shot(src, move, p, punch=0.0, dx=0.0, dy=0.0):
    z, cx, cy = camera(move, p, punch)
    hx, hy = W / (2 * z), H / (2 * z)
    cx, cy = min(max(cx + dx, hx), W - hx), min(max(cy + dy, hy), H - hy)
    hw, hh = W / (2 * z) * SRC_SCALE, H / (2 * z) * SRC_SCALE
    box = (cx * SRC_SCALE - hw, cy * SRC_SCALE - hh, cx * SRC_SCALE + hw, cy * SRC_SCALE + hh)
    return src.transform((W, H), Image.EXTENT, box, Image.BILINEAR)


# ---------------------------------------------------------------- animation layers
SHAKE_EMO = {"shock", "fear"}
FLICKER_EMO = {"eerie", "fear"}
RAIN_RE = re.compile(r"rain|बारिश|बरसात")


def particle_kind(sc):
    txt = (sc.get("narration_hi", "") + " " + sc.get("image_prompt", "")).lower()
    emo = sc.get("emotion")
    if RAIN_RE.search(txt):
        return "rain"
    if emo in ("joy", "triumph", "warm", "hopeful"):
        return "sparkle"
    if emo in ("eerie", "fear", "tense", "shock", "sad"):
        return "mist"
    return "fireflies"


def _rand(seed, k):
    """Deterministic pseudo-random numbers in [0, 1) - identical in every render worker."""
    return np.random.default_rng(seed).random(k)


def draw_particles(frame, kind, t, seed):
    d = ImageDraw.Draw(frame, "RGBA")
    if kind == "rain":
        r = _rand(seed, 3 * 70).reshape(70, 3)
        for x0, y0, sp in r:
            y = (y0 * (H + 200) + t * (1500 + 900 * sp)) % (H + 200) - 100
            x = (x0 * W + y * 0.12) % W
            d.line((x, y, x - 10, y - 60 - 40 * sp), fill=(200, 215, 255, 70), width=3)
        return
    n = {"fireflies": 26, "sparkle": 34, "mist": 22}[kind]
    r = _rand(seed, 5 * n).reshape(n, 5)
    for x0, y0, sz, ph, sp in r:
        if kind == "mist":                                       # slow drifting dust motes
            x = (x0 * W + 25 * t * (sp - 0.5)) % W
            y = (y0 * H - 18 * t * (0.3 + sp)) % H
            rad, col, a = 2 + 3 * sz, (210, 220, 255), int(60 + 50 * np.sin(t * 1.5 + ph * 6))
        else:
            x = (x0 * W + 40 * np.sin(t * (0.4 + sp) + ph * 6)) % W
            y = (y0 * H - (30 + 50 * sp) * t) % H
            tw = 0.5 + 0.5 * np.sin(t * (2 + 3 * sp) + ph * 6)
            if kind == "fireflies":
                rad, col, a = 3 + 4 * sz, (255, 214, 120), int(40 + 200 * tw)
            else:
                rad, col, a = 2 + 4 * sz, (255, 245, 200), int(30 + 220 * tw ** 3)
        if a <= 8:
            continue
        g = rad * 3.2
        d.ellipse((x - g, y - g, x + g, y + g), fill=col + (max(0, a // 6),))
        d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=col + (min(255, a),))


def fog_layer(seed):
    """A soft fog band (RGBA) that drifts across eerie scenes."""
    rng = np.random.default_rng(seed)
    small = (rng.random((24, 60)) * 255).astype(np.uint8)
    fog = Image.fromarray(small, "L").resize((int(W * 1.6), 620), Image.BICUBIC).filter(ImageFilter.GaussianBlur(40))
    a = np.asarray(fog, np.float32)
    yy = np.linspace(0, np.pi, a.shape[0], dtype=np.float32)[:, None]
    a = (a * np.sin(yy) * 0.42).astype(np.uint8)
    layer = Image.new("RGBA", fog.size, (200, 210, 235, 0))
    layer.putalpha(Image.fromarray(a, "L"))
    return layer


def flicker(t, seed):
    """0..0.35 darkening blips (a candle / lantern struggling) for eerie scenes."""
    k = int(t * 12)
    r = np.random.default_rng(seed * 1000 + k).random()
    return 0.0 if r > 0.18 else 0.12 + 0.23 * np.random.default_rng(seed * 7000 + k).random()


def shake(t0, seed):
    """Camera shake (dx, dy) during the first 0.6 s of a shock/fear scene."""
    if t0 >= 0.6:
        return 0.0, 0.0
    amp = 16 * (1 - t0 / 0.6) ** 2
    return amp * np.sin(t0 * 71 + seed), amp * np.cos(t0 * 53 + seed * 2)


# ---------------------------------------------------------------- main
def timeline(voices):
    """Scene start times and durations from the voice clips."""
    t, out = 0.0, []
    for i, (_, d, _) in enumerate(voices):
        dur = max(d + GAP, HOOK_T + 0.2 if i == 0 else 1.5)
        out.append((t, dur)); t += dur
    return out, t


class Frames:
    """Draws any frame of the video from a picklable plan (so worker processes can render chunks in parallel)."""

    def __init__(self, plan):
        self.p = plan
        self.srcs = {}
        self.mask = vignette_mask()
        self.black = Image.new("RGB", (W, H), (0, 0, 0))
        self.white = Image.new("RGB", (W, H), (255, 255, 255))
        hook = hook_overlay(plan["hook"], plan["badge"])
        self.hook_a, self.hook_rgb = hook.getchannel("A"), hook.convert("RGB")
        self.card = end_card(shot(self.src(len(plan["tl"]) - 1), plan["moves"][-1], 1.0), plan["end_card"], plan["vs"])
        self.caps = CaptionCache()
        self.fog = fog_layer(plan["seed"]) if "mist" in plan["particles"] else None
        self.gi = 0
        self.cap_frames = 0

    def src(self, i):
        if i not in self.srcs:
            if len(self.srcs) > 4:                               # keep memory small: scenes come in order
                self.srcs.pop(min(self.srcs))
            img = Image.open(self.p["images"][i]).convert("RGB")
            if img.size != (W, H):
                from images import cover_fit
                img = cover_fit(img)
            self.srcs[i] = img.resize((int(W * SRC_SCALE), int(H * SRC_SCALE)), Image.BICUBIC)
        return self.srcs[i]

    def scene(self, i, t):
        p = self.p
        st, d = p["tl"][i]
        t0 = t - st
        punch = 0.05 * (1 - t0 / 0.5) if (p["twist_i"] == i and 0 <= t0 < 0.5) else \
            (0.025 * (1 - t0 / 0.35) if (i > 0 and 0 <= t0 < 0.35) else 0.0)    # beat punch on every cut
        dx, dy = shake(t0, i) if p["emotions"][i] in SHAKE_EMO and t0 >= 0 else (0.0, 0.0)
        if (dx or dy) and t0 < 0.6:
            punch = max(punch, 0.035 * (1 - t0 / 0.6))           # room for the shake at the frame edges
        return shot(self.src(i), p["moves"][i], t0 / d, punch, dx, dy)

    def frame(self, f):
        p = self.p
        t = f / FPS
        tl, story_end = p["tl"], p["story_end"]
        if t >= story_end:                                          # end card (fades in)
            k = min(1.0, (t - story_end) / 0.4)
            frame = self.card.copy() if k >= 1 else Image.blend(self.scene(len(tl) - 1, t), self.card, k)
            draw_particles(frame, "sparkle", t, p["seed"] + 99)
            return frame
        i = max(j for j, (st, _) in enumerate(tl) if st <= t + 1e-9)
        frame = self.scene(i, t)
        nxt, twist_i = i + 1, p["twist_i"]
        if nxt < len(tl) and nxt != twist_i and t > tl[nxt][0] - XFADE / 2:
            frame = Image.blend(frame, self.scene(nxt, t), (t - (tl[nxt][0] - XFADE / 2)) / XFADE)
        elif i > 0 and i != twist_i and t < tl[i][0] + XFADE / 2:
            frame = Image.blend(self.scene(i - 1, t), frame, 0.5 + (t - tl[i][0]) / XFADE)
        kind = p["particles"][i]
        if kind == "mist" and self.fog is not None:                 # drifting fog band low in the frame
            x = int(-((t * 30) % (self.fog.width - W)))
            frame.paste(self.fog, (x, 1180), self.fog)
        draw_particles(frame, kind, t, p["seed"] + i)
        if p["emotions"][i] in FLICKER_EMO:
            a = flicker(t, p["seed"] + i)
            if a:
                frame = Image.blend(frame, self.black, a)
        if twist_i == i:
            fi = int(round((t - tl[i][0]) * FPS))
            if 0 <= fi < len(FLASH):
                frame = Image.blend(frame, self.white, FLASH[fi])
        frame = Image.composite(frame, self.black, self.mask)
        if t < HOOK_T:                                              # hook text: pops in, fades out
            a = min(1.0, t / 0.15, (HOOK_T - t) / 0.35)
            frame.paste(self.hook_rgb, (0, 0), self.hook_a if a >= 1 else self.hook_a.point(lambda v, a=a: int(v * a)))
            return frame
        groups = p["groups"]
        while self.gi < len(groups) - 1 and t >= groups[self.gi + 1][0]:
            self.gi += 1
        if groups:
            g0, g1, g = groups[self.gi]
            if g0 <= t < g1:
                active = next((k for k, w in enumerate(g) if w[0] - 0.03 <= t < (g[k + 1][0] if k + 1 < len(g) else w[1] + 0.3)), -1)
                cap = self.caps.get(self.gi, g, active)
                age = t - g0
                if age < 0.14:                                      # caption pop: 80% -> 106% -> 100%
                    sc = 0.8 + 0.26 * (age / 0.1) if age < 0.1 else 1.06 - 0.06 * ((age - 0.1) / 0.04)
                    cw, ch = int(W * sc), int(200 * sc)
                    cap = cap.resize((cw, ch), Image.BILINEAR)
                    frame.paste(cap, ((W - cw) // 2, CAP_Y - ch // 2), cap)
                else:
                    frame.paste(cap, (0, CAP_Y - 100), cap)
                self.cap_frames += 1
        return frame


ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", "9M", "-bufsize", "18M",
       "-profile:v", "high", "-pix_fmt", "yuv420p", "-g", "60", "-r", str(FPS)]


def render_chunk(plan, f0, f1, out):
    """Renders frames [f0, f1) to a silent H.264 segment. Runs in a worker process."""
    fr = Frames(plan)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", *ENC, "-an", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for f in range(f0, f1):
            proc.stdin.write(fr.frame(f).tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg encode failed for frames {f0}-{f1}")
    return fr.cap_frames


def workers():
    want = int(os.environ.get("RENDER_WORKERS", "0") or 0)
    return max(1, want or min(4, os.cpu_count() or 1))


def render(vs, images, voices, work, out_path, music=None):
    """vs: a video story from story.make_parts(); images: images.make_all(); voices: tts.speak_scenes().
    Returns metadata about the render (duration, frames, captions...)."""
    check_shaping()
    music = C.MUSIC if music is None else music
    scenes = vs["scenes"]
    tl, story_end = timeline(voices)
    total = story_end + ENDCARD
    tw = vs.get("twist_scene")
    twist_i = (tw - 1) if tw and 1 <= tw <= len(scenes) else None
    twist_t = tl[twist_i][0] if twist_i is not None else None
    words = [(st + float(a), st + float(b), w) for (st, _), (_, _, ws) in zip(tl, voices) for a, b, w in ws]
    groups = caption_groups(words, [(st, st + d) for st, d in tl])

    log(f"🎵 Soundtrack: {len(voices)} clips, {total:.1f}s, music + sound effects")
    mix, norm = os.path.join(work, "mix.wav"), os.path.join(work, "mix_norm.wav")
    amb, hits = soundtrack.plan_sfx(scenes, tl, twist_t, story_end)
    soundtrack.build([v[0] for v in voices], [s for s, _ in tl], total, mix, music=music,
                     voice_af=C.VOICE_FILTER if C.VOICE_FX and not C.OFFLINE and soundtrack_voice_is_edge() else None,
                     whoosh_at=[t for t in (twist_t, story_end) if t], boom_at=[twist_t] if twist_t else [],
                     seed=int(vs.get("seed", 7)) % 1000, lift_at=twist_t, ambience=amb, hits=hits)
    lufs = soundtrack.loudnorm(mix, norm)
    log(f"   {len(amb)} ambience beds + {len(hits)} effects · loudness {lufs} LUFS (target {C.TARGET_LUFS})")

    plan = {"images": [im["path"] for im in images],
            "moves": ["close" if im.get("reused") else MOVES[i % len(MOVES)] for i, im in enumerate(images)],
            "emotions": [sc.get("emotion") for sc in scenes], "particles": [particle_kind(sc) for sc in scenes],
            "tl": tl, "story_end": story_end, "twist_i": twist_i, "groups": groups,
            "hook": vs.get("hook") or vs["title"], "badge": {1: "PART 1", 2: "PART 2"}.get(vs.get("part")),
            "end_card": vs.get("end_card", "cta"), "seed": int(vs.get("seed", 7)) % 10000,
            "vs": {k: vs.get(k) for k in ("cta_question", "title")}}
    n = int(round(total * FPS))
    k = min(workers(), max(1, n // 300))
    bounds = [round(n * j / k) for j in range(k + 1)]
    segs = [os.path.join(work, f"seg_{j:02d}.mp4") for j in range(k)]
    log(f"🖌️ Rendering {n} frames on {k} CPU core{'s' if k > 1 else ''} ({total:.1f}s, {len(scenes)} scenes, "
        f"{len(groups)} caption groups)...")
    if k == 1:
        cap_frames = render_chunk(plan, 0, n, segs[0])
    else:
        import concurrent.futures, multiprocessing
        ctx = multiprocessing.get_context("fork" if hasattr(os, "fork") and os.uname().sysname == "Linux" else "spawn")
        with concurrent.futures.ProcessPoolExecutor(max_workers=k, mp_context=ctx) as ex:
            cap_frames = sum(ex.map(render_chunk, [plan] * k, bounds[:-1], bounds[1:], segs))
    lst = os.path.join(work, "segments.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{os.path.abspath(s)}'\n" for s in segs)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-i", norm,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-movflags", "+faststart", "-shortest", out_path], check=True)
    for s in segs:
        os.remove(s)
    log(f"✅ Video {total:.1f}s, {os.path.getsize(out_path) / 1e6:.1f} MB")
    return {"duration": total, "frames": n, "caption_groups": len(groups), "caption_frames": cap_frames,
            "words": len(words), "timeline": tl, "twist_t": twist_t, "story_end": story_end, "lufs": lufs,
            "raqm": RAQM, "words_timed": words, "workers": k}


def soundtrack_voice_is_edge():
    try:
        import tts
        return tts.LAST in ("edge", "")
    except Exception:
        return True
