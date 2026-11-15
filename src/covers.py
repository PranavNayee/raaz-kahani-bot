"""Cover image (Reels cover_url / YouTube thumbnail attempt): the most dramatic scene + the title.
Everything important stays inside the centre 3:4 crop that the Instagram profile grid shows (1080x1440, y 240-1680)."""
import numpy as np
from PIL import Image, ImageDraw
import config as C, render as R, story as S

GRID = (0, 240, 1080, 1680)           # 3:4 profile-grid crop of a 1080x1920 cover


def make(vs, images, out_path):
    i = S.dramatic_scene(vs["scenes"])
    img = Image.open(images[i]["path"]).convert("RGB")
    if img.size != (R.W, R.H):
        from images import cover_fit
        img = cover_fit(img)
    img = img.convert("RGBA")
    # darken the lower part of the grid crop so the title pops
    y = np.linspace(0, 1, R.H, dtype=np.float32)[:, None]
    a = np.clip((y - 0.45) / 0.35, 0, 1) * 200
    shade = np.zeros((R.H, R.W, 4), np.uint8)
    shade[..., 3] = np.repeat(a, R.W, axis=1).astype(np.uint8)
    img = Image.alpha_composite(img, Image.fromarray(shade, "RGBA"))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = GRID
    title = R.no_emoji(vs["title"])
    if vs.get("part"):
        f = R.font(52, False)
        badge = f"PART {vs['part']}"
        bw = f.getlength(badge) + 70
        d.rounded_rectangle((R.W / 2 - bw / 2, 1010, R.W / 2 + bw / 2, 1090), 40, fill=(220, 30, 60, 255))
        d.text((R.W / 2, 1050), badge, font=f, fill=(255, 255, 255), anchor="mm")
    R.draw_block(d, title, (x0 + 80, 1110, x1 - 80, 1560), 104, fill=R.YELLOW, stroke=10, max_lines=4)
    R.draw_block(d, C.CHANNEL.upper(), (x0 + 100, 1580, x1 - 100, 1660), 46, fill=(240, 240, 255), stroke=5, max_lines=1)
    img.convert("RGB").save(out_path, quality=92)
    return out_path, i
