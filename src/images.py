"""One 1080x1920 picture per scene. Free providers, best first (IMAGE_PROVIDERS):

  gemini       - Gemini image models (free tier on GEMINI_API_KEY; model names discovered live). The first good
                 frame is passed back as a reference so characters and style stay the same.
  pollinations - free, no key. Same seed for every scene of a story.
  placeholder  - drawn in code (offline tests only, or to fill a gap).

The channel style_bible + each character's visual_bible are prepended to EVERY prompt (visual consistency).
Prompts go through a safety word filter. 3 tries per provider. If a scene still fails, the previous good frame
is reused (with a different camera move), so one bad image never kills a video."""
import base64, io, os, random, re, time, urllib.parse, requests
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import config as C
from util import log

W, H = 1080, 1920
GEM_URL = "https://generativelanguage.googleapis.com/v1beta"
GEM_FALLBACK = ["gemini-2.5-flash-image", "gemini-2.0-flash-preview-image-generation"]
_gem_models = None
_disabled = set()           # providers that are out of quota for this run
_no_seed = set()            # gemini models that reject a seed

SAFE_WORDS = [  # (pattern, softer replacement) - keeps image models (and viewers) happy
    (r"\bblood(y|ied)?\b", "shadowy"), (r"\bgore|gory\b", "dramatic"), (r"\bcorpses?|dead bod(y|ies)\b", "sleeping figure"),
    (r"\b(kill(s|ed|ing)?|murder\w*|stab\w*|behead\w*)\b", "confront"), (r"\b(knife|knives|gun|pistol|rifle|sword|axe|weapon)s?\b", "lantern"),
    (r"\b(nude|naked|topless|sexy|lingerie|bikini)\b", "fully dressed"), (r"\bskulls?\b", "old stones"),
    (r"\bdemon(ic|s)?\b", "mysterious spirit"), (r"\bsatan\w*|devil\w*\b", "mysterious spirit"),
    (r"\b(cigarette|alcohol|beer|wine|drugs?)\b", "tea"), (r"\bwounds?|injur\w*\b", "tired"),
]
NEGATIVE = ("No text, no letters, no words, no captions, no watermark, no logo, no signature. "
            "Family-friendly, not gory, no blood, no weapons, everyone fully clothed.")


def safe(text):
    for pat, rep in SAFE_WORDS:
        text = re.sub(pat, rep, text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def build_prompt(scene, story):
    """style bible + visual bible of each character in this scene + the scene itself."""
    p = scene["image_prompt"]
    chars = [c for c in story.get("characters", [])
             if re.search(rf"\b({re.escape(c['name'].split()[0])}|{re.escape(c['id'])})\b", p, re.I)
             or scene.get("speaker") == c["id"]]
    who = " ".join(f"{c['name']}: {c['visual_bible']}." for c in chars)
    mood = scene.get("emotion", "")
    txt = f"{story.get('style_bible', C.STYLE_BIBLE)}. {('Characters - ' + who + ' ') if who else ''}Scene: {p}. " \
          f"Mood: {mood}. Vertical 9:16 frame, main subject in the centre. {NEGATIVE}"
    return safe(txt)


# ---------------------------------------------------------------- image helpers
def cover_fit(img, w=W, h=H):
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def looks_bad(img):
    """Blank, flat or tiny images (failed generations, rate-limit placeholders)."""
    a = np.asarray(img.convert("L").resize((64, 114)), dtype=np.float32)
    return img.width < 256 or a.std() < 10


def placeholder(scene, story, idx=0):
    """A moody painted-looking backdrop drawn in code (tests / last resort). Never uses caption yellow."""
    pal = {"fear": ((10, 14, 40), (60, 30, 90)), "shock": ((20, 8, 30), (120, 40, 70)), "joy": ((40, 60, 120), (240, 150, 90)),
           "warm": ((50, 40, 90), (230, 130, 80)), "triumph": ((30, 50, 110), (220, 120, 70)), "sad": ((15, 25, 50), (70, 90, 130))}
    top, bot = pal.get(scene.get("emotion"), ((12, 20, 48), (40, 70, 120)))
    y = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    arr = np.array(top, np.float32) + (np.array(bot, np.float32) - np.array(top, np.float32)) * y
    img = Image.fromarray(np.repeat(arr, W, axis=1).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img, "RGBA")
    rng = random.Random(story.get("seed", 1) + idx)
    mx, my, mr = rng.randint(250, 830), rng.randint(300, 650), rng.randint(110, 170)
    for k in range(6, 0, -1):                                    # moon glow
        d.ellipse((mx - mr - k * 30, my - mr - k * 30, mx + mr + k * 30, my + mr + k * 30), fill=(200, 220, 255, 10))
    d.ellipse((mx - mr, my - mr, mx + mr, my + mr), fill=(226, 236, 255, 255))
    for layer, (base, col) in enumerate(((1250, (20, 24, 50)), (1450, (12, 14, 32)), (1650, (6, 7, 18)))):
        pts = [(0, H)] + [(x, base - rng.randint(0, 160) - (60 if 400 < x < 700 else 0)) for x in range(0, W + 120, 120)] + [(W, H)]
        d.polygon(pts, fill=col + (255,))
    # a little house + lit window (story-ish silhouette)
    hx = rng.randint(180, 760)
    d.polygon([(hx, 1500), (hx + 110, 1420), (hx + 220, 1500)], fill=(4, 5, 12))
    d.rectangle((hx + 20, 1500, hx + 200, 1640), fill=(4, 5, 12))
    d.rectangle((hx + 90, 1540, hx + 130, 1580), fill=(255, 150, 60))
    noise = np.random.default_rng(idx).normal(0, 6, (H, W, 1)).astype(np.float32)
    out = np.clip(np.asarray(img, np.float32) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB").filter(ImageFilter.GaussianBlur(0.6))


# ---------------------------------------------------------------- Gemini
def gemini_models():
    global _gem_models
    if _gem_models is not None:
        return _gem_models
    forced = [m.strip() for m in os.environ.get("GEMINI_IMAGE_MODELS", "").split(",") if m.strip()]
    names = []
    try:
        r = requests.get(f"{GEM_URL}/models", headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                         params={"pageSize": 200}, timeout=30)
        if r.status_code == 200:
            names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                     if "image" in m["name"] and "generateContent" in m.get("supportedGenerationMethods", [])
                     and not m["name"].split("/")[-1].startswith("imagen")]
    except Exception as e:
        log("Gemini image model discovery failed:", e)

    def rank(n):                                         # newest first, flash before pro (free quota)
        v = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return ("pro" in n, -(float(v.group(1)) if v else 0), "preview" in n, n)
    _gem_models = forced or sorted(names, key=rank)[:3] or GEM_FALLBACK
    log("Gemini image models:", _gem_models)
    return _gem_models


def _gemini(prompt, seed, ref=None):
    parts = []
    if ref is not None:
        buf = io.BytesIO(); ref.resize((360, 640)).save(buf, "JPEG", quality=85)
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}})
        prompt = ("Reference image: use it ONLY to keep the same characters' faces, clothes and the same art style. "
                  "Draw a NEW picture of this scene: " + prompt)
    parts.append({"text": prompt})
    last = None
    for model in gemini_models():
        r = None
        for use_seed in ((True, False) if model not in _no_seed else (False,)):
            cfg = {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "9:16"}}
            if use_seed:
                cfg["seed"] = seed
            r = requests.post(f"{GEM_URL}/models/{model}:generateContent", timeout=(10, 120),
                              headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                              json={"contents": [{"parts": parts}], "generationConfig": cfg})
            if r.status_code == 400 and use_seed and "seed" in r.text.lower():
                _no_seed.add(model)                          # this model rejects a seed: ask again without it
                continue
            break
        if r.status_code == 200:
            for cand in r.json().get("candidates", []):
                for p in (cand.get("content") or {}).get("parts", []):
                    d = p.get("inlineData") or p.get("inline_data")
                    if d and d.get("data"):
                        return Image.open(io.BytesIO(base64.b64decode(d["data"])))
            last = f"{model}: no image in reply (safety block?)"
            continue
        last = f"{model} {r.status_code}: {r.text[:160]}"
        if r.status_code == 429 and ("quota" in r.text.lower() or "limit: 0" in r.text):
            continue                                         # this model has no free quota; try the next
    if last and ("429" in last):
        _disabled.add("gemini")                              # all models out of quota: stop wasting time this run
    raise RuntimeError(last or "gemini image failed")


# ---------------------------------------------------------------- Pollinations
_last_poll = [0.0]


def _poll_gap():
    """Seconds between Pollinations requests: the anonymous free tier allows 1 image per 45 s per IP."""
    default = "8" if os.environ.get("POLLINATIONS_TOKEN") else "46"
    return float(os.environ.get("POLLINATIONS_GAP", default) or default)


def _pace():
    wait = _last_poll[0] + _poll_gap() - time.time()
    if wait > 0:
        log(f"    waiting {wait:.0f}s (Pollinations free-tier pace)")
        time.sleep(wait)
    _last_poll[0] = time.time()


def _pollinations(prompt, seed):
    url = C.POLLINATIONS_URL + urllib.parse.quote(prompt[:1400], safe="")
    # ask for ~7% extra height: the anonymous tier stamps a small logo in the bottom corner, which is cropped off
    params = {"width": W, "height": int(H * 1.07), "seed": seed, "nologo": "true", "model": C.POLLINATIONS_MODEL,
              "safe": "true"}
    headers = {}
    if os.environ.get("POLLINATIONS_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['POLLINATIONS_TOKEN']}"
    _pace()
    r = requests.get(url, params=params, headers=headers, timeout=(15, 180))
    _last_poll[0] = time.time()                  # the limit counts from when the image finished
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
        raise RuntimeError(f"pollinations {r.status_code}: {r.text[:120] if 'image' not in r.headers.get('content-type', '') else ''}")
    if len(r.content) < 15_000:
        raise RuntimeError("pollinations returned a tiny image")
    img = Image.open(io.BytesIO(r.content))
    return img.crop((0, 0, img.width, int(img.height * 0.93)))


def _providers():
    if C.OFFLINE:
        return []
    out = []
    for p in C.IMAGE_PROVIDERS:
        if p in _disabled:
            continue
        if p == "gemini" and not os.environ.get("GEMINI_API_KEY"):
            continue
        out.append(p)
    return out


def one(scene, story, ref=None, tries=3):
    """Returns (PIL image 1080x1920, provider) or raises."""
    prompt = build_prompt(scene, story)
    seed = int(story.get("seed", 42))
    last = None
    for prov in _providers():
        if prov in _disabled:
            continue
        for attempt in range(tries):
            try:
                img = _gemini(prompt, seed, ref if C.IMAGE_REFERENCE else None) if prov == "gemini" else _pollinations(prompt, seed + attempt)
                img = cover_fit(img)
                if looks_bad(img):
                    raise RuntimeError("blank/flat image")
                return img, prov
            except Exception as e:
                last = e
                log(f"    {prov} try {attempt + 1}/{tries}: {str(e)[:160]}")
                if prov in _disabled:
                    break
                if prov == "gemini":
                    time.sleep(2 + 3 * attempt)      # Pollinations paces itself (_pace)
    raise RuntimeError(f"no provider drew this scene ({last})")


def make_all(story, out_dir):
    """Draws every scene. Returns [{path, provider, reused}] - one per scene, never fails on a single bad image.
    Raises only when too few real images exist (MIN_REAL_IMAGES) outside offline mode."""
    os.makedirs(out_dir, exist_ok=True)
    scenes = story["scenes"]
    res = [None] * len(scenes)
    ref = None
    for i, sc in enumerate(scenes):
        log(f"  🎨 scene {i + 1}/{len(scenes)}")
        path = os.path.join(out_dir, f"scene_{i + 1:02d}.jpg")
        try:
            img, prov = one(sc, story, ref)
            img.save(path, quality=93)
            res[i] = {"path": path, "provider": prov, "reused": False}
            ref = ref or img
        except Exception as e:
            if _providers():
                log(f"    scene {i + 1} failed - reusing the previous good frame: {str(e)[:160]}")
    real = [r for r in res if r]
    if not C.OFFLINE and _providers() and len(real) < C.MIN_REAL_IMAGES * len(scenes):
        raise RuntimeError(f"only {len(real)}/{len(scenes)} images could be drawn - not making a poor video")
    if not real:                                             # offline / no provider: drawn placeholders
        for i, sc in enumerate(scenes):
            path = os.path.join(out_dir, f"scene_{i + 1:02d}.jpg")
            placeholder(sc, story, i).save(path, quality=92)
            res[i] = {"path": path, "provider": "placeholder", "reused": False}
        return res
    first = next(r for r in res if r)
    prev = None
    for i in range(len(res)):                                # fill gaps with the previous (or first) good frame
        if res[i]:
            prev = res[i]
        else:
            res[i] = dict(prev or first, reused=True)
    return res
