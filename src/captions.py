"""Instagram caption + YouTube metadata. Every text says the video is AI-generated."""
import re
import config as C

AI_NOTE_IG = "🤖 AI-generated: yeh kahani, visuals aur awaaz AI se banaye gaye hain. Kahani aur kirdaar kaalpanik hain."
AI_NOTE_HI = "🤖 यह वीडियो AI से बनाया गया है (AI-generated story, visuals and voice). कहानी और किरदार काल्पनिक हैं।"
KEYWORDS = "Hindi Kahani | Hindi Story | Mystery Story | Fantasy Story | Moral Story | Raaz Kahani | AI Story"


def hashtag_set(idx, n=5):
    """5 tags: #hindikahani + #raazkahani always, the other 3 rotate through the rest of the list."""
    core = ["#hindikahani", C.BRAND_TAG if C.BRAND_TAG in C.HASHTAGS else "#raazkahani"]
    rest = [t for t in C.HASHTAGS if t not in core]
    k = (idx * 3) % len(rest)
    pick = (rest[k:] + rest[:k])[:n - len(core)]
    return core + pick


def _part(vs):
    return f" (Part {vs['part']})" if vs.get("part") else ""


def ig_caption(vs, idx=0):
    title = re.sub(r"\s+", " ", vs["title"]).strip()
    lines = [f"{title}{_part(vs)} | Hindi Kahani | {C.CHANNEL} | AI Story", ""]
    if vs.get("part") == 1:
        lines += ["Part 2 kal isi waqt! Follow karo taaki raaz chhoot na jaaye 🔔", ""]
    lines += [vs.get("cta_question") or "Aap hote toh kya karte? 👇", ""]
    if vs.get("part") != 1 and vs.get("moral"):
        lines += [f"✨ Seekh: {vs['moral']}", ""]
    lines += [AI_NOTE_IG, "", " ".join(hashtag_set(idx))]
    return "\n".join(lines)[:2200]


def yt_title(vs):
    t = re.sub(r"[<>]", "", re.sub(r"\s+", " ", vs["title"])).strip()
    suffix = _part(vs) + " #shorts"
    if len(t) + len(suffix) > 100:
        t = t[:100 - len(suffix) - 1].rstrip(" ,.!-") + "…"
    return t + suffix


def yt_meta(vs, idx=0):
    desc = [vs["title"] + _part(vs), ""]
    if vs.get("summary_hi"):
        desc += [vs["summary_hi"], ""]
    if vs.get("part") == 1:
        desc += ["पार्ट 2 कल इसी समय! चैनल को सब्सक्राइब करें ताकि राज़ छूट न जाए 🔔", ""]
    desc += [f"💬 {vs.get('cta_question') or 'Aap hote toh kya karte? 👇'}", ""]
    if vs.get("part") != 1 and vs.get("moral"):
        desc += [f"✨ सीख: {vs['moral']}", ""]
    desc += [f"{C.CHANNEL} - रोज़ एक नई रहस्य और जादू भरी हिंदी कहानी। हिंदी कहानियाँ, मिस्ट्री स्टोरी, फैंटेसी स्टोरी, नैतिक कहानियाँ।",
             KEYWORDS, "", AI_NOTE_HI, "", " ".join(hashtag_set(idx) + ["#shorts"])]
    tags = list(dict.fromkeys(C.YT_TAGS + [vs.get("theme", "").replace("_", " ")]))
    tags = [t for t in tags if t][:15]
    return {
        "snippet": {"title": yt_title(vs), "description": "\n".join(desc)[:4900], "tags": tags,
                    "categoryId": C.YT_CATEGORY, "defaultLanguage": "hi", "defaultAudioLanguage": "hi"},
        "status": {"privacyStatus": C.YT_PRIVACY, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True,
                   "embeddable": True},
    }
