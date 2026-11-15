"""Covers: the most dramatic scene + the title.
  make()        9:16 cover; everything important inside the centre 3:4 crop that the Instagram profile grid shows
                (1080x1440, y 240-1680). Instagram and YouTube get different layouts (style="ig" | "yt").
  story_card()  Instagram Story image: the question written on the image (the API cannot add stickers).
  long_thumb()  1280x720 thumbnail for the weekly long-form video."""
import numpy as np
from PIL import Image, ImageDraw
import config as C, render as R, story as S

GRID = (0, 240, 1080, 1680)           # 3:4 profile-grid crop of a 1080x1920 cover
VW, VH = 1080, 1920


def _load(path, w, h):
    from images import cover_fit
    return cover_fit(Image.open(path).convert("RGB"), w, h).convert("RGBA")


def _shade(img, start=0.45, span=0.35, top=False, alpha=200):
    w, h = img.size
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    a = np.clip(((1 - y) if top else (y - start)) / span, 0, 1) * alpha
    sh = np.zeros((h, w, 4), np.uint8)
    sh[..., 3] = np.repeat(a, w, axis=1).astype(np.uint8)
    return Image.alpha_composite(img, Image.fromarray(sh, "RGBA"))


def make(vs, images, out_path, style="yt"):
    i = S.dramatic_scene(vs["scenes"])
    keep = (R.W, R.H)
    R.W, R.H = VW, VH                                     # covers are always drawn 9:16
    try:
        img = _shade(_load(images[i]["path"], VW, VH))
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = GRID
        title = R.no_emoji(vs["title"])
        if vs.get("part"):
            f = R.font(52, False)
            badge = f"PART {vs['part']}"
            bw = f.getlength(badge) + 70
            d.rounded_rectangle((VW / 2 - bw / 2, 1010, VW / 2 + bw / 2, 1090), 40, fill=(220, 30, 60, 255))
            d.text((VW / 2, 1050), badge, font=f, fill=(255, 255, 255), anchor="mm")
        if style == "ig":                                 # Instagram: boxed title, white text, channel on top
            d.rounded_rectangle((x0 + 60, 1110, x1 - 60, 1570), 44, fill=(10, 10, 25, 170))
            R.draw_block(d, title, (x0 + 100, 1130, x1 - 100, 1550), 96, fill=(255, 255, 255), stroke=6, max_lines=4)
            R.draw_block(d, C.CHANNEL.upper(), (x0 + 100, 290, x1 - 100, 370), 46, fill=R.YELLOW, stroke=5, max_lines=1)
        else:
            R.draw_block(d, title, (x0 + 80, 1110, x1 - 80, 1560), 104, fill=R.YELLOW, stroke=10, max_lines=4)
            R.draw_block(d, C.CHANNEL.upper(), (x0 + 100, 1580, x1 - 100, 1660), 46, fill=(240, 240, 255), stroke=5,
                         max_lines=1)
        img.convert("RGB").save(out_path, quality=92)
    finally:
        R.W, R.H = keep
    return out_path, i


def story_card(vs, images, out_path):
    """Instagram Story: the image, the question in big text, and a pointer to the new Reel."""
    i = S.dramatic_scene(vs["scenes"])
    keep = (R.W, R.H)
    R.W, R.H = VW, VH
    try:
        img = _shade(_shade(_load(images[i]["path"], VW, VH), 0.35, 0.4, alpha=210), top=True, span=0.3, alpha=150)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((90, 300, 990, 420), 50, fill=R.YELLOW + (255,))
        R.draw_block(d, "NAYI KAHANI", (110, 300, 970, 420), 64, fill=(20, 20, 20), stroke=0, max_lines=1)
        R.draw_block(d, R.no_emoji(vs["title"]), (110, 470, 970, 820), 80, fill=(255, 255, 255), stroke=7, max_lines=4)
        R.draw_block(d, R.no_emoji(vs.get("cta_question") or "Aap hote toh kya karte?"), (110, 1180, 970, 1480), 84,
                     fill=R.YELLOW, stroke=8, max_lines=3)
        R.draw_block(d, "Reel dekho aur comment mein jawab do", (110, 1520, 970, 1640), 52, stroke=5, max_lines=2)
        img.convert("RGB").save(out_path, quality=92)
    finally:
        R.W, R.H = keep
    return out_path


def long_thumb(ls, image_path, out_path):
    """1280x720 thumbnail: the image on the right, the hook big on the left."""
    TW, TH = 1280, 720
    keep = (R.W, R.H)
    R.W, R.H = TW, TH
    try:
        img = _load(image_path, TW, TH)
        x = np.linspace(0, 1, TW, dtype=np.float32)[None, :]
        a = (np.clip((0.75 - x) / 0.5, 0, 1) * 215).astype(np.uint8)
        sh = np.zeros((TH, TW, 4), np.uint8)
        sh[..., 3] = np.repeat(a, TH, axis=0)
        img = Image.alpha_composite(img, Image.fromarray(sh, "RGBA"))
        d = ImageDraw.Draw(img)
        R.draw_block(d, R.no_emoji(ls.get("hook") or ls["title"]), (40, 90, 720, 560), 86, fill=R.YELLOW, stroke=8,
                     max_lines=4)
        d.rounded_rectangle((40, 600, 420, 680), 30, fill=(220, 30, 60, 255))
        R.draw_block(d, "POORI KAHANI", (40, 600, 420, 680), 44, fill=(255, 255, 255), stroke=0, max_lines=1)
        img.convert("RGB").save(out_path, quality=92)
    finally:
        R.W, R.H = keep
    return out_path
