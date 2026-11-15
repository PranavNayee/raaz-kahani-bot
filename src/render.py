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


def shot(src, move, p, punch=0.0):
    z, cx, cy = camera(move, p, punch)
    hw, hh = W / (2 * z) * SRC_SCALE, H / (2 * z) * SRC_SCALE
    box = (cx * SRC_SCALE - hw, cy * SRC_SCALE - hh, cx * SRC_SCALE + hw, cy * SRC_SCALE + hh)
    return src.transform((W, H), Image.EXTENT, box, Image.BILINEAR)


# ---------------------------------------------------------------- main
def timeline(voices):
    """Scene start times and durations from the voice clips."""
    t, out = 0.0, []
    for i, (_, d, _) in enumerate(voices):
        dur = max(d + GAP, HOOK_T + 0.2 if i == 0 else 1.5)
        out.append((t, dur)); t += dur
    return out, t


def render(vs, images, voices, work, out_path, music=None):
    """vs: a video story from story.make_parts(); images: images.make_all(); voices: voice.speak_scenes().
    Returns metadata about the render (duration, frames, captions...)."""
    check_shaping()
    music = C.MUSIC if music is None else music
    scenes = vs["scenes"]
    tl, story_end = timeline(voices)
    total = story_end + ENDCARD
    tw = vs.get("twist_scene")
    twist_i = (tw - 1) if tw and 1 <= tw <= len(scenes) else None
    twist_t = tl[twist_i][0] if twist_i is not None else None

    words = [(st + a, st + b, w) for (st, _), (_, _, ws) in zip(tl, voices) for a, b, w in ws]
    groups = caption_groups(words, [(st, st + d) for st, d in tl])

    log(f"🎵 Soundtrack: {len(voices)} clips, {total:.1f}s")
    mix, norm = os.path.join(work, "mix.wav"), os.path.join(work, "mix_norm.wav")
    soundtrack.build([v[0] for v in voices], [s for s, _ in tl], total, mix, music=music,
                     voice_af=C.VOICE_FILTER if C.VOICE_FX and not C.OFFLINE else None,
                     whoosh_at=[t for t in (twist_t, story_end) if t], boom_at=[twist_t] if twist_t else [],
                     seed=int(vs.get("seed", 7)) % 1000, lift_at=twist_t)
    lufs = soundtrack.loudnorm(mix, norm)
    log(f"   loudness {lufs} LUFS (target {C.TARGET_LUFS})")

    # scene sources (kept 20% larger for the camera) and moves
    srcs, moves = [], []
    for i, im in enumerate(images):
        img = Image.open(im["path"]).convert("RGB")
        if img.size != (W, H):
            from images import cover_fit
            img = cover_fit(img)
        srcs.append(img.resize((int(W * SRC_SCALE), int(H * SRC_SCALE)), Image.BICUBIC))
        moves.append("close" if im.get("reused") else MOVES[i % len(MOVES)])
    mask = vignette_mask()
    black = Image.new("RGB", (W, H), (0, 0, 0))
    white = Image.new("RGB", (W, H), (255, 255, 255))
    badge = {1: "PART 1", 2: "PART 2"}.get(vs.get("part"))
    hook = hook_overlay(vs.get("hook") or vs["title"], badge)
    hook_a, hook_rgb = hook.getchannel("A"), hook.convert("RGB")
    last_img = shot(srcs[-1], moves[-1], 1.0)
    card = end_card(last_img, vs.get("end_card", "cta"), vs)
    caps = CaptionCache()

    def scene_frame(i, t):
        st, d = tl[i]
        p = (t - st) / d
        punch = 0.0
        if twist_i == i and t - st < 0.5:
            punch = 0.05 * (1 - (t - st) / 0.5)
        return shot(srcs[i], moves[i], p, punch)

    n = int(round(total * FPS))
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-i", norm, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-maxrate", "9M", "-bufsize", "18M", "-profile:v", "high", "-pix_fmt", "yuv420p", "-g", "60",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-shortest", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    log(f"🖌️ Rendering {n} frames ({total:.1f}s, {len(scenes)} scenes, {len(groups)} caption groups)...")
    gi, cap_frames = 0, 0
    try:
        for f in range(n):
            t = f / FPS
            if t >= story_end:                                          # end card (fades in)
                k = min(1.0, (t - story_end) / 0.4)
                frame = card if k >= 1 else Image.blend(scene_frame(len(scenes) - 1, t), card, k)
            else:
                i = max(j for j, (st, _) in enumerate(tl) if st <= t + 1e-9)
                frame = scene_frame(i, t)
                nxt = i + 1
                # crossfade into the next scene (not into the twist: that is a hard cut + flash)
                if nxt < len(tl) and nxt != twist_i and t > tl[nxt][0] - XFADE / 2:
                    frame = Image.blend(frame, scene_frame(nxt, t), (t - (tl[nxt][0] - XFADE / 2)) / XFADE)
                elif i > 0 and i != twist_i and t < tl[i][0] + XFADE / 2:
                    frame = Image.blend(scene_frame(i - 1, t), frame, 0.5 + (t - tl[i][0]) / XFADE)
                if twist_i == i:
                    fi = int(round((t - tl[i][0]) * FPS))
                    if 0 <= fi < len(FLASH):
                        frame = Image.blend(frame, white, FLASH[fi])
            frame = Image.composite(frame, black, mask)
            if t < HOOK_T:                                              # hook text, fades out
                a = min(1.0, t / 0.15, (HOOK_T - t) / 0.35)
                frame.paste(hook_rgb, (0, 0), hook_a if a >= 1 else hook_a.point(lambda v, a=a: int(v * a)))
            elif t < story_end:                                         # karaoke captions
                while gi < len(groups) - 1 and t >= groups[gi + 1][0]:
                    gi += 1
                g0, g1, g = groups[gi] if groups else (0, 0, [])
                if g and g0 <= t < g1:
                    active = next((k for k, w in enumerate(g) if w[0] - 0.03 <= t < (g[k + 1][0] if k + 1 < len(g) else w[1] + 0.3)), -1)
                    cap = caps.get(gi, g, active)
                    frame.paste(cap, (0, CAP_Y - 100), cap)
                    cap_frames += 1
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg encode failed")
    log(f"✅ Video {total:.1f}s, {os.path.getsize(out_path) / 1e6:.1f} MB")
    return {"duration": total, "frames": n, "caption_groups": len(groups), "caption_frames": cap_frames,
            "words": len(words), "timeline": tl, "twist_t": twist_t, "story_end": story_end, "lufs": lufs,
            "raqm": RAQM, "words_timed": words}
