"""Writes the day's story: an ORIGINAL Hindi mystery/fantasy short with a hook, strange rule, escalation,
clever twist, payoff, moral and a comment question.

  * strict JSON, validated against SCHEMA + content checks (Devanagari, length, safety, originality); retried on failure
  * 2 candidates, then a second LLM pass scores hook strength / twist surprise / clarity and keeps the best
  * themes rotate; the best-performing themes and hooks (growth.py -> stats.json) are fed back in
  * premise history so a story idea never repeats
  * every SERIES_EVERY-th story is a 2-part cliffhanger: Part 1 stops right before the twist, Part 2 posts next day
"""
import hashlib, json, re
import config as C, llm
from util import log

try:
    import jsonschema
except ImportError:                     # validated by hand below as well
    jsonschema = None

WINNERS = ""          # set by main.py from growth.winners(): the best hooks so far
THEME_STATS = {}      # set by main.py from growth.theme_scores(): theme -> average score

THEMES = {
    "night_mystery": "Raat ka rahasya: kuch ajeeb sirf raat ko, ek tay waqt par hota hai (ghadi, chaand, lantern, sadak).",
    "magic_rule": "Jaadui niyam ya contract: gaon/shehar mein ek ajeeb niyam ya sauda jiski keemat chhupi hai.",
    "cursed_object": "Shraapit cheez: ek purani cheez (aaina, sikka, ghanti, chappal, chabi) jo ichha poori karti hai par badle mein kuch leti hai.",
    "greedy_rich": "Laalchi ameer aadmi: ek laalchi seth/zamindar ko ek garib par chaalak insaan buddhi se harata hai.",
    "family_love": "Bhai-behen ya maa ka pyaar: pyaar aur tyaag se ek shraap ya musibat tootti hai.",
    "friendly_monster": "Dost rakshas: jise sab darawna samajhte hain woh asal mein madad kar raha hota hai.",
    "village_legend": "Gaon ki kahaani/lok-katha: ek purani manyata jiske peeche ka sach koi nahi jaanta.",
}
HOOK_TYPES = ["time_rule", "count_limit", "forbidden_act", "strange_trade", "vanishing", "object_power", "creature"]
EMOTIONS = ["calm", "curious", "eerie", "tense", "fear", "shock", "sad", "hopeful", "joy", "triumph", "warm"]
DRAMA = {"shock": 5, "fear": 4, "tense": 3, "eerie": 3, "curious": 2, "triumph": 2, "sad": 1}

SCHEMA = {
    "type": "object",
    "required": ["title", "hook", "premise", "hook_type", "narrator", "moral", "cta_question", "twist_scene",
                 "characters", "style_bible", "scenes"],
    "properties": {
        "title": {"type": "string", "minLength": 12, "maxLength": 95},
        "hook": {"type": "string", "minLength": 12, "maxLength": 140},
        "premise": {"type": "string", "minLength": 15, "maxLength": 300},
        "hook_type": {"type": "string"},
        "narrator": {"enum": ["male", "female"]},
        "summary_hi": {"type": "string"},
        "moral": {"type": "string", "minLength": 6, "maxLength": 160},
        "cta_question": {"type": "string", "minLength": 6, "maxLength": 120},
        "twist_scene": {"type": "integer", "minimum": 2},
        "part2_title": {"type": "string"},
        "recap_hi": {"type": "string"},
        "characters": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
            "type": "object", "required": ["id", "name", "visual_bible"],
            "properties": {"id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,20}$"},
                           "name": {"type": "string", "minLength": 2},
                           "gender": {"enum": ["male", "female", "other"]},
                           "visual_bible": {"type": "string", "minLength": 20, "maxLength": 400}}}},
        "style_bible": {"type": "string"},
        "scenes": {"type": "array", "minItems": 8, "maxItems": 24, "items": {
            "type": "object", "required": ["id", "narration_hi", "speaker", "image_prompt", "emotion", "seconds"],
            "properties": {"id": {"type": "integer", "minimum": 1},
                           "narration_hi": {"type": "string", "minLength": 8, "maxLength": 400},
                           "speaker": {"type": "string"},
                           "image_prompt": {"type": "string", "minLength": 20, "maxLength": 600},
                           "emotion": {"type": "string"},
                           "seconds": {"type": "number", "minimum": 1, "maximum": 30}}}},
    },
}

# never: real brands / famous characters / films (copyright), and nothing gory, sexual, hateful or dangerous to copy
BANNED = re.compile(r"(?i)\b(doraemon|shinchan|chhota bheem|motu patlu|pokemon|naruto|harry potter|hogwarts|marvel|disney|"
                    r"batman|superman|spider-?man|avengers|bollywood|shah rukh|salman|amitabh|netflix|youtube|instagram|"
                    r"coca[- ]?cola|pepsi|blood|gore|gory|corpse|dead body|decapitat\w*|suicide|self[- ]harm|nude|naked|sexy|"
                    r"kiss\w*|gun|pistol|bomb|knife|drug\w*|alcohol|sharaab)\b|"
                    r"(खून|लाश|आत्महत्या|हत्या|बंदूक|चाकू|बम|शराब|नशा|नंगा)")

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
LATIN = re.compile(r"[A-Za-z]")


# ---------------------------------------------------------------- prompt
PROMPT = """You are the head writer of "{channel}", a hugely popular Hindi storytelling channel on YouTube Shorts and
Instagram Reels. Write ONE brand-new, 100% ORIGINAL animated mystery/fantasy story in natural spoken Hindi.

THEME for this story: {theme_label}
{series_block}
STRUCTURE (strict):
1. HOOK - scene 1, under 2.5 seconds spoken (max 14 words): one shocking line. Pattern:
   "<impossible thing happens regularly>! Lekin <ordinary hero> ne <discovered/broke> <the secret>!"
   (Examples of the PATTERN only, never reuse them: "Har raat 9 baje chaand bachchon ko utha le jaata tha!",
   "Is gaon mein sirf 5 baar 'haan' bol sakte the!")
2. SETUP - 2 to 3 scenes: the world and its strange rule or curse.
3. ESCALATION - 3 to 4 scenes: the danger grows, a greedy or cruel antagonist appears.
4. CLEVER TWIST - the ordinary hero (a child, sibling, poor shopkeeper, old woman...) solves it with kindness or wit.
   Mark this scene's id in "twist_scene". The twist must be a real surprise but fair (set up earlier).
5. PAYOFF - an emotional resolution, then the last scene speaks a one-line moral AND ends with a comment question
   to the viewer in Hindi (like "आप होते तो क्या करते? कमेंट में बताइए!").

LANGUAGE: narration_hi in Devanagari, simple everyday spoken Hindi (like a dadi telling a story), short sentences,
vivid sounds and feelings, no English words, no difficult Sanskrit. Every scene 10-28 words. {length_rule}

HARD RULES:
- Completely original world, names and plot. NEVER use or mention copyrighted characters, films, cartoons, books,
  brands, celebrities, real people or real religious figures. Never retell any movie or show.
- For a general 13+ audience: spooky and suspenseful is great, but NO gore, blood, death details, weapons, sexual
  content, hate, or anything dangerous a kid could copy. Villains are defeated by wit, never by violence.
- Never repeat or closely resemble these past premises: {past}
{winners}
VISUALS (an AI image model draws one picture per scene, the SAME characters must look the same every time):
- characters: 1-4 characters, each with a fixed "visual_bible" in English (age, face, hair, skin tone, exact clothes
  and colours, one signature item). Use simple lowercase ids like "meera", "seth".
- image_prompt (English): describe ONLY this scene's action, setting, camera angle (close-up / wide / low angle),
  lighting and mood. Refer to characters by name. No text, letters or signs in the picture.
- style_bible: one short English line for this story's colour palette and mood (the channel look is added for you).

Return ONLY this JSON:
{{"title": "the hook line in Roman Hinglish, max 90 chars, may end with one emoji like 😱",
  "hook": "same hook in Roman Hinglish (on-screen text in the first 2 seconds)",
  "premise": "one English sentence summarising the core idea (used to avoid repeats)",
  "hook_type": "one of {hook_types}",
  "narrator": "male or female (female for stories centred on a girl/woman/mother)",
  "summary_hi": "one spoiler-free Devanagari line for the description",
  "moral": "one-line moral in Roman Hinglish",
  "cta_question": "comment question in Roman Hinglish ending with 👇",
  "twist_scene": 9,{series_fields}
  "characters": [{{"id": "meera", "name": "Meera", "gender": "female", "visual_bible": "..."}}],
  "style_bible": "...",
  "scenes": [{{"id": 1, "narration_hi": "...", "speaker": "narrator or a character id when the line is that character speaking",
              "image_prompt": "...", "emotion": "one of {emotions}", "seconds": 6}}]}}"""

SERIES_BLOCK = """
THIS IS A 2-PART SERIES. Write the WHOLE story ({n_min}-{n_max} scenes) in one go. It will be cut in two videos:
Part 1 = scenes before "twist_scene" (must be 8-13 scenes and end on a nail-biting cliffhanger right before the twist),
Part 2 = a short recap scene ("recap_hi") + the twist and everything after it (at least 7 scenes: twist, how the hero's
plan works, the antagonist's downfall, the payoff, the moral + comment question).
"""
SERIES_FIELDS = """
  "part2_title": "Part 2 hook in Roman Hinglish (max 80 chars)",
  "recap_hi": "Part 2 opening, Devanagari, 14-24 words: 'पिछली बार...' quick recap that ends on the cliffhanger","""


def _past_premises(history, n=40):
    return [h["premise"] for h in history if h.get("premise")][-n:]


def build_prompt(theme, history, series=False):
    past = _past_premises(history)
    n_min, n_max = (16, 22) if series else (8, 14)
    length = ("Whole story: 16-22 scenes, 300-480 words in total." if series else
              f"Whole story: {n_min}-{n_max} scenes, 150-250 words in total (about 70-{int(C.MAX_SECONDS) - 20} seconds).")
    winners = ""
    if WINNERS:
        winners = (f"\nOur audience loved these hooks most - match their energy and specificity (do NOT copy them): "
                   f"{WINNERS}\n")
    return PROMPT.format(channel=C.CHANNEL, theme_label=THEMES.get(theme, theme),
                         series_block=SERIES_BLOCK.format(n_min=n_min, n_max=n_max) if series else "",
                         length_rule=length, past="; ".join(past) if past else "(none yet)", winners=winners,
                         hook_types=", ".join(HOOK_TYPES), emotions=", ".join(EMOTIONS),
                         series_fields=SERIES_FIELDS if series else "")


# ---------------------------------------------------------------- validation
def _words(t):
    return len(re.findall(r"\S+", t or ""))


def _tokens(t):
    return {w for w in re.findall(r"[a-z]{4,}", (t or "").lower())}


def similar(a, b):
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / max(1, len(ta | tb))


def validate(s, history=(), series=False):
    """Raises ValueError with a clear reason (fed back to the model) or returns the cleaned story."""
    if not isinstance(s, dict):
        raise ValueError("answer is not a JSON object")
    if jsonschema:
        try:
            jsonschema.validate(s, SCHEMA)
        except jsonschema.ValidationError as e:
            where = "/".join(map(str, e.path)) or "root"
            if e.validator in ("minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "required", "enum"):
                raise ValueError(f"schema: {where} breaks {e.validator}={json.dumps(e.validator_value, ensure_ascii=False)[:80]}")
            raise ValueError(f"schema: {where}: {e.message[:160]}")
    for k in SCHEMA["required"]:
        if k not in s:
            raise ValueError(f"missing field {k}")
    sc = s["scenes"]
    n_min, n_max = (16, 24) if series else (8, 14)
    if not n_min <= len(sc) <= n_max:
        raise ValueError(f"need {n_min}-{n_max} scenes, got {len(sc)}")
    ids = [x["id"] for x in sc]
    if ids != list(range(1, len(sc) + 1)):
        for i, x in enumerate(sc):            # renumber quietly: ids are only labels
            x["id"] = i + 1
    chars = {c["id"]: c for c in s["characters"]}
    for x in sc:
        t = x["narration_hi"]
        letters = len(DEVANAGARI.findall(t)) + len(LATIN.findall(t))
        if letters and len(DEVANAGARI.findall(t)) / letters < 0.85:
            raise ValueError(f"scene {x['id']} narration must be Devanagari Hindi, got: {t[:60]}")
        if _words(t) > 40:
            raise ValueError(f"scene {x['id']} narration too long ({_words(t)} words, max 28)")
        if x["speaker"] not in chars:
            x["speaker"] = "narrator"
        if x["emotion"] not in EMOTIONS:
            x["emotion"] = "tense"
    if _words(sc[0]["narration_hi"]) > 18:
        raise ValueError("scene 1 is the hook: max 14 spoken words")
    tw = s["twist_scene"]
    if not 4 <= tw <= len(sc) - 1:
        raise ValueError(f"twist_scene {tw} must point at a scene in the second half (4..{len(sc) - 1})")
    total = sum(_words(x["narration_hi"]) for x in sc)
    if series:
        if not 9 <= tw <= 14:
            raise ValueError(f"series: twist_scene must be 9-14 so Part 1 has 8-13 scenes, got {tw}")
        if len(sc) - tw + 1 < 7:
            raise ValueError("series: Part 2 (twist onwards) needs at least 7 scenes")
        if not s.get("recap_hi") or len(DEVANAGARI.findall(s["recap_hi"])) < 10:
            raise ValueError("series: recap_hi (Devanagari) is required")
        for part in (sc[:tw - 1], sc[tw - 1:]):
            w = sum(_words(x["narration_hi"]) for x in part)
            if w > C.MAX_SECONDS * 1.95:
                raise ValueError(f"series part too long ({w} words)")
    elif not 125 <= total <= C.MAX_SECONDS * 1.95:
        raise ValueError(f"story length {total} words - needs 150-250 words")
    blob = json.dumps(s, ensure_ascii=False)
    m = BANNED.search(blob)
    if m:
        raise ValueError(f"not allowed (copyright/brand/unsafe): '{m.group(0)}' - rewrite without it")
    for h in history:
        if h.get("premise") and similar(h["premise"], s["premise"]) > 0.5:
            raise ValueError(f"premise too close to an old story: {h['premise'][:80]}")
    s["hook_type"] = s["hook_type"] if s["hook_type"] in HOOK_TYPES else "object_power"
    s["title"] = re.sub(r"\s+", " ", s["title"]).strip()
    s["style_bible"] = (C.STYLE_BIBLE + ("; " + s["style_bible"][:160] if s.get("style_bible") else "")).strip()
    s["seed"] = int(hashlib.sha1(s["premise"].encode()).hexdigest()[:8], 16) % 2_000_000_000
    return s


# ---------------------------------------------------------------- judge
JUDGE = """You are a strict editor for a viral Hindi story channel (YouTube Shorts / Reels, 13+ audience).
Score each candidate from 1-10 on: hook (would a scroller stop in 2 seconds?), twist (surprising yet fair?),
clarity (easy to follow by ear?). Also set safe=false if anything is gory, sexual, hateful or dangerous to copy,
and original=false if it copies or closely resembles any known film, cartoon, book or famous character.

{cands}

Return ONLY JSON: {{"scores": [{{"hook": 0, "twist": 0, "clarity": 0, "safe": true, "original": true, "note": "..."}}]}}
(one entry per candidate, same order)"""


def _brief(s):
    return (f"Title: {s['title']}\nPremise: {s['premise']}\nTwist at scene {s['twist_scene']}\nStory: " +
            " / ".join(x["narration_hi"] for x in s["scenes"]))


def judge(cands):
    """Returns the index of the best candidate and its scores."""
    txt = "\n\n".join(f"CANDIDATE {i + 1}\n{_brief(s)}" for i, s in enumerate(cands))
    try:
        d = llm.generate_json(JUDGE.format(cands=txt), temperature=0.2)
        sc = d.get("scores") or []
    except Exception as e:
        log("Judge failed, keeping candidate 1:", e)
        return 0, {}
    best, best_v = 0, -1
    for i, s in enumerate(sc[:len(cands)]):
        v = sum(float(s.get(k, 0) or 0) for k in ("hook", "twist", "clarity"))
        if s.get("safe") is False or s.get("original") is False:
            v = -1
        log(f"  candidate {i + 1}: hook {s.get('hook')} twist {s.get('twist')} clarity {s.get('clarity')} "
            f"safe {s.get('safe')} original {s.get('original')} - {str(s.get('note', ''))[:100]}")
        if v > best_v:
            best, best_v = i, v
    if best_v < 0:
        raise RuntimeError("the editor rejected every candidate as unsafe or unoriginal")
    return best, (sc[best] if best < len(sc) else {})


# ---------------------------------------------------------------- theme rotation + series plan
def pick_theme(history, override=None):
    want = (override or C.THEME or "auto").strip().lower()
    if want and want != "auto":
        if want in THEMES:
            return want
        THEMES[want] = want                    # free-text theme from workflow_dispatch
        return want
    last = {}
    for i, h in enumerate(history):
        if h.get("theme") and h.get("part") != 2:
            last[h["theme"]] = i
    order = sorted(THEMES, key=lambda t: last.get(t, -1))          # least recently used first
    fresh = order[:3]
    avg = (sum(THEME_STATS.values()) / len(THEME_STATS)) if THEME_STATS else 0
    return max(fresh, key=lambda t: (THEME_STATS.get(t, avg), -fresh.index(t)))


def stories_started(history):
    return sum(1 for h in history if h.get("part") != 2 and h.get("kind", "story") == "story")


def is_series_turn(history):
    return bool(C.SERIES_EVERY) and (stories_started(history) + 1) % C.SERIES_EVERY == 0


def pending_part2(history):
    """The series id whose Part 2 is due (last posted video was a Part 1 whose Part 2 is not out yet)."""
    if not history:
        return None
    last = history[-1]
    if last.get("part") == 1 and last.get("series_id"):
        if not any(h.get("series_id") == last["series_id"] and h.get("part") == 2 for h in history):
            return last["series_id"]
    return None


# ---------------------------------------------------------------- public
def write(history, theme=None, series=False, candidates=2):
    """Generates `candidates` valid stories, judges them and returns the best one."""
    theme = theme or pick_theme(history)
    base = build_prompt(theme, history, series)
    log(f"✍️ Writing {'a 2-part series' if series else 'a story'} - theme '{theme}'")
    cands = []
    for c in range(candidates):
        prompt, last = base, None
        for attempt in range(3):
            try:
                raw = llm.generate_json(prompt, temperature=0.95 - 0.1 * c)
                s = validate(raw, history, series)
                s["theme"], s["series"] = theme, series
                cands.append(s)
                log(f"  candidate {len(cands)} ok ({llm.LAST}): {s['title']}")
                break
            except ValueError as e:
                last = e
                log(f"  candidate {c + 1} attempt {attempt + 1} invalid: {e}")
                prompt = base + f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: {e}\nFix that and return the full JSON again."
        if last and len(cands) <= c:
            log(f"  candidate {c + 1} gave up: {last}")
    if not cands:
        raise RuntimeError("the AI writer could not produce a valid story")
    idx, scores = judge(cands) if len(cands) > 1 else (0, {})
    best = cands[idx]
    best["scores"] = scores
    log(f"🏆 Chosen: {best['title']}")
    return best


def dramatic_scene(scenes):
    """Index of the most dramatic scene (cover image)."""
    ranked = sorted(range(len(scenes)), key=lambda i: (DRAMA.get(scenes[i].get("emotion"), 0), -abs(i - len(scenes) * 0.6)))
    return ranked[-1] if ranked else 0


def make_parts(s):
    """Turns a story into the list of videos it becomes (1 video, or Part 1 + Part 2 for a series)."""
    common = {k: s.get(k) for k in ("characters", "style_bible", "seed", "theme", "hook_type", "premise", "narrator",
                                    "moral", "cta_question", "summary_hi")}
    sid = "s" + hashlib.sha1(s["premise"].encode()).hexdigest()[:8]
    if not s.get("series"):
        return [dict(common, title=s["title"], hook=s["hook"], scenes=s["scenes"], twist_scene=s["twist_scene"],
                     part=None, series_id=None, end_card="cta")]
    tw = s["twist_scene"]
    p1 = s["scenes"][:tw - 1]
    cliff = p1[-1]
    recap = {"id": 1, "narration_hi": s["recap_hi"], "speaker": "narrator", "image_prompt": cliff["image_prompt"],
             "emotion": "tense", "seconds": 6}
    p2 = [recap] + [dict(x, id=i + 2) for i, x in enumerate(s["scenes"][tw - 1:])]
    t2 = s.get("part2_title") or s["title"]
    return [dict(common, title=s["title"], hook=s["hook"], scenes=p1, twist_scene=None, part=1, series_id=sid,
                 end_card="part2", series_title=s["title"]),
            dict(common, title=t2, hook=t2, scenes=p2, twist_scene=2, part=2, series_id=sid, end_card="cta",
                 series_title=s["title"])]


# ---------------------------------------------------------------- offline sample (tests, keyless dry runs)
def sample():
    """A complete hand-written ORIGINAL story so the whole pipeline runs without any API key."""
    s = {
        "title": "Har poornima kuan gaana gaata tha! 😱 Lekin nanhi Meera ne raaz khol diya!",
        "hook": "Har poornima kuan gaana gaata tha! 😱 Lekin nanhi Meera ne raaz khol diya!",
        "premise": "A village well sings on full moon nights and steals happy memories until a curious girl thanks it.",
        "hook_type": "time_rule", "narrator": "female",
        "summary_hi": "सूरजपुर का गाता हुआ कुआँ, एक लालची सेठ, और एक सवाल पूछने वाली बच्ची।",
        "moral": "Jo hamara saath dete hain, unhe dhanyavaad kehna kabhi mat bhoolo.",
        "cta_question": "Aap Meera ki jagah hote toh kya karte? 👇",
        "twist_scene": 9,
        "characters": [
            {"id": "meera", "name": "Meera", "gender": "female", "visual_bible":
             "10-year-old Indian girl, big curious brown eyes, two braided pigtails with red ribbons, mustard-yellow "
             "kurta with a green dupatta, carries a small brass lantern"},
            {"id": "seth", "name": "Seth Dhanraj", "gender": "male", "visual_bible":
             "plump middle-aged moneylender, thin curled moustache, shiny bald head, cream silk kurta with gold buttons, "
             "red shawl, always clutching a thick account book"},
            {"id": "kuan", "name": "the old well", "gender": "other", "visual_bible":
             "ancient round stone village well covered in moss and tiny glowing blue flowers, carved face-like stones, "
             "soft blue light rising from inside"}],
        "style_bible": "moonlit blues with warm lantern oranges, gentle mist",
        "scenes": [
            {"narration_hi": "हर पूर्णिमा पर सूरजपुर का कुआँ गाना गाता था! लेकिन नन्ही मीरा ने राज़ खोल दिया!",
             "image_prompt": "wide low-angle shot of the old well glowing blue under a huge full moon, mist, a village behind",
             "emotion": "shock"},
            {"narration_hi": "सूरजपुर में एक अजीब नियम था। पूर्णिमा की रात कोई भी कुएँ के पास नहीं जाता था।",
             "image_prompt": "villagers hurrying home at dusk, closing wooden doors, the well alone in the square",
             "emotion": "eerie"},
            {"narration_hi": "जो उसका गाना सुनता, अगली सुबह अपनी एक खुशी भूल जाता। कोई अपनी शादी का दिन, कोई अपने बच्चे की पहली हँसी।",
             "image_prompt": "an old man sitting on a charpai at sunrise looking confused, holding an old photo frame",
             "emotion": "sad"},
            {"narration_hi": "मीरा दस साल की थी। उसके पिताजी की चाय की छोटी सी दुकान थी, और वो हर बात पर सवाल पूछती थी।",
             "image_prompt": "Meera at her father's tiny roadside tea stall, steam rising from kettles, warm morning light",
             "emotion": "curious"},
            {"narration_hi": "फिर गाँव में आया सेठ धनराज। बोला, मैं इस शापित कुएँ को मिट्टी से भर दूँगा, बस हर घर मुझे अपनी ज़मीन का एक टुकड़ा दे दे।",
             "image_prompt": "Seth Dhanraj standing on a cart addressing a worried crowd, waving his account book",
             "emotion": "tense", "speaker": "seth"},
            {"narration_hi": "डरे हुए गाँव वाले मान गए। लेकिन उसी पूर्णिमा पर गाना और भी ऊँचा हो गया। इस बार पूरा गाँव अपनी हँसी भूल गया।",
             "image_prompt": "whole village at night, glowing blue musical notes swirling out of the well over the rooftops",
             "emotion": "fear"},
            {"narration_hi": "मीरा ने सोचा, अगर कुआँ सच में चोर है, तो वो रोता क्यों है? उसने चुपके से लालटेन उठाई।",
             "image_prompt": "close-up of Meera at a window at night, lantern in hand, determined face, moonlight",
             "emotion": "tense"},
            {"narration_hi": "कुएँ के अंदर से एक धीमी आवाज़ आई। मीरा डरी नहीं। उसने बस इतना पूछा, तुम उदास क्यों हो?",
             "image_prompt": "Meera leaning over the glowing well, lantern light on her face, blue light rising",
             "emotion": "eerie", "speaker": "meera"},
            {"narration_hi": "और तभी राज़ खुला! कुएँ में कोई भूत नहीं था। वो एक बूढ़ा जादुई कुआँ था, जिसे सौ साल से किसी ने धन्यवाद नहीं कहा था।",
             "image_prompt": "the carved stones of the well forming a gentle old smiling face, tiny blue flowers blooming",
             "emotion": "shock"},
            {"narration_hi": "वो खुशियाँ चुराता नहीं था, बस उधार लेता था, ताकि कोई उसे याद करे। और सेठ धनराज? वो रात में कुएँ में ज़मीन के कागज़ छुपा रहा था!",
             "image_prompt": "Seth Dhanraj sneaking at night dropping a bundle of papers into the well, caught in lantern light",
             "emotion": "shock"},
            {"narration_hi": "मीरा ने पूरे गाँव को बुलाया। सबने कुएँ को फूलों से सजाया और एक साथ धन्यवाद कहा। कुएँ ने सबकी खुशियाँ लौटा दीं।",
             "image_prompt": "villagers decorating the well with marigold garlands and diyas at dawn, Meera in front smiling",
             "emotion": "joy"},
            {"narration_hi": "और सेठ के छुपाए कागज़ भी उगल दिए। उस दिन से सेठ ने कभी किसी की ज़मीन नहीं छीनी।",
             "image_prompt": "papers flying out of the well like birds, Seth Dhanraj sitting in the dust, embarrassed",
             "emotion": "triumph"},
            {"narration_hi": "सीख यही है, जो हमारा साथ देते हैं, उन्हें धन्यवाद कहना कभी मत भूलो। आप मीरा की जगह होते तो क्या करते? कमेंट में बताइए!",
             "image_prompt": "Meera and the whole village singing together around the glowing well under the full moon",
             "emotion": "warm"},
        ],
    }
    for i, x in enumerate(s["scenes"]):
        x.update(id=i + 1, seconds=6)
        x.setdefault("speaker", "narrator")
    s = validate(s, ())
    s["theme"], s["series"] = "village_legend", False
    return s
