"""Instagram captions + YouTube metadata. Every text says the video is an AI-animated story."""
import re
import config as C

AI_NOTE_IG = "🤖 AI-animated story: kahani, chitra aur awaaz AI se banaye gaye hain. Kirdaar aur ghatnayein kaalpanik hain."
AI_NOTE_HI = "🤖 AI-animated story - यह कहानी, चित्र और आवाज़ AI से बनाए गए हैं। सभी किरदार और घटनाएँ काल्पनिक हैं।"
KEYWORDS_ROMAN = "hindi kahani, hindi story, mystery story, fantasy story, moral story, raaz kahani, kahaniyan"
TITLE_VISIBLE = 70


def hashtag_set(idx, n=5):
    """5 tags: #hindikahani + #raazkahani always, the other 3 rotate through the rest of the list."""
    core = ["#hindikahani", "#raazkahani"]
    rest = [t for t in C.HASHTAGS if t not in core]
    k = (idx * 3) % len(rest)
    return core + (rest[k:] + rest[:k])[:n - len(core)]


def _part(vs):
    return f" (Part {vs['part']})" if vs.get("part") else ""


def _clean(t):
    return re.sub(r"[<>]", "", re.sub(r"\s+", " ", t or "")).strip()


def ig_caption(vs, idx=0, part2_when=None):
    title = _clean(vs["title"])
    lines = [f"{title}{_part(vs)} | Hindi Kahani | {C.CHANNEL} | AI Story", ""]
    if vs.get("part") == 1:
        lines += [f"Part 2 {part2_when or 'kal isi waqt'}! Follow karo taaki raaz chhoot na jaaye 🔔", ""]
    lines += [vs.get("cta_question") or "Aap hote toh kya karte? 👇", ""]
    if vs.get("part") != 1 and vs.get("moral"):
        lines += [f"✨ Seekh: {vs['moral']}", ""]
    lines += [AI_NOTE_IG, "", " ".join(hashtag_set(idx))]
    return "\n".join(lines)[:2200]


def yt_title(vs, shorts=True):
    """The hook, kept within the ~70 characters YouTube shows, then #shorts."""
    t, part = _clean(vs["title"]), _part(vs)
    room = TITLE_VISIBLE - len(part)                      # the part label always stays visible
    if len(t) > room:
        t = t[:room - 1].rstrip(" ,.!-") + "…"
    t += part
    return (t + " #shorts") if shorts else t


def yt_comment(vs):
    return f"💬 {vs.get('cta_question') or 'Aap hote toh kya karte? 👇'} Apna jawab comment mein likho - best jawab pin hoga! 👇"


def yt_meta(vs, idx=0, long_link=None, part2_when=None):
    desc = [yt_title(vs, shorts=False), ""]
    if vs.get("summary_hi"):
        desc.append(vs["summary_hi"])
    desc += [f"{_clean(vs['title'])} - {KEYWORDS_ROMAN}.", ""]
    if vs.get("part") == 1:
        desc += [f"पार्ट 2 {part2_when or 'कल इसी समय'}! चैनल को सब्सक्राइब करें ताकि राज़ छूट न जाए 🔔", ""]
    desc += [f"💬 {vs.get('cta_question') or 'Aap hote toh kya karte? 👇'}", ""]
    if vs.get("part") != 1 and vs.get("moral"):
        desc += [f"✨ सीख: {vs['moral']}", ""]
    if long_link:
        desc += [f"📺 पूरी लंबी कहानी देखें (Full story): {long_link}", ""]
    desc += [f"{C.CHANNEL} - रोज़ नई रहस्य और जादू भरी हिंदी कहानियाँ।", "", AI_NOTE_HI, "",
             " ".join(hashtag_set(idx) + ["#shorts"])]
    tags = list(dict.fromkeys(C.YT_TAGS + [vs.get("theme", "").replace("_", " ")]))
    return {
        "snippet": {"title": yt_title(vs), "description": "\n".join(desc)[:4900], "tags": [t for t in tags if t][:15],
                    "categoryId": C.YT_CATEGORY, "defaultLanguage": "hi", "defaultAudioLanguage": "hi"},
        "status": {"privacyStatus": C.YT_PRIVACY, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True,
                   "embeddable": True},
    }


def ts(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def long_meta(ls, chapters):
    """Long-form metadata. chapters: [(start_seconds, title)] - YouTube needs the first at 0:00, 3+ chapters,
    each at least 10 s long."""
    title = _clean(ls["title"])
    title = (title[:95].rstrip() + "…") if len(title) > 96 else title
    lines = [f"{ts(t)} {c}" for t, c in chapters]
    desc = [ls.get("summary_hi", ""), f"{title} - poori kahani | {KEYWORDS_ROMAN}.", "", "📖 अध्याय (Chapters):",
            *lines, "", f"💬 {ls.get('cta_question') or 'Aapko kahani kaisi lagi? 👇'}", "",
            f"✨ सीख: {ls.get('moral', '')}" if ls.get("moral") else "", "",
            f"{C.CHANNEL} - हर हफ्ते एक पूरी, नई, ओरिजिनल हिंदी कहानी।", "", AI_NOTE_HI, "",
            "#hindikahani #raazkahani #hindistory #mysterystory #kahaniyan"]
    return {
        "snippet": {"title": f"{title} | पूरी कहानी | {C.CHANNEL}"[:100], "description": "\n".join(desc)[:4900],
                    "tags": C.YT_TAGS + ["full story", "hindi kahani full", "long story"],
                    "categoryId": C.YT_CATEGORY, "defaultLanguage": "hi", "defaultAudioLanguage": "hi"},
        "status": {"privacyStatus": C.YT_PRIVACY, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True,
                   "embeddable": True},
    }
