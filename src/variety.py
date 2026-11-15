"""Varies the 'factory signals' between videos so the channel never looks mass-produced (YouTube inauthentic
content policy): visual style bible, narrator voice, story structure, caption style, end card and music bed.
Each dimension takes the least-recently-used option and never repeats the previous video's choice."""

STYLES = {
    "film3d": "stylised 3D animated film look, warm cinematic lighting, soft volumetric light, expressive faces, "
              "rich saturated colours, Indian village and town settings, high detail",
    "storybook": "hand-painted storybook illustration, soft watercolour and gouache textures, gentle paper grain, "
                 "warm earthy palette, whimsical rounded shapes, Indian settings",
    "folkink": "Indian folk-art inspired ink and gouache painting, bold outlines, decorative patterns, deep indigo, "
               "vermilion and gold palette, flat perspective with rich detail",
    "papercut": "layered paper-cut diorama look, handcrafted card textures, miniature set lighting, shallow depth of "
                "field, muted pastel palette with one glowing accent colour",
    "cel2d": "2D cel-shaded animation look, clean line art, dramatic rim light, bold flat colours, cinematic framing, "
             "moody night skies",
}
STRUCTURES = {
    "classic": "Classic order: hook -> setup (the strange rule) -> escalation -> clever twist -> payoff.",
    "in_medias_res": "Open in the middle of the danger: right after the hook, scene 2 is the scariest moment; then "
                     "rewind ('teen din pehle...') to show how it began, return to the danger and solve it with the twist.",
    "countdown": "A countdown drives the story: a fixed number of nights/days/chances is left, and the narration "
                 "keeps saying how many remain until the twist.",
    "diary": "The hero finds an old diary, letter or carving; its lines reveal the rule bit by bit while the danger "
             "grows in the present.",
    "two_timelines": "Two timelines: the same place long ago and today, told in alternating scenes; they connect at "
                     "the twist.",
    "secret_helper": "Everyone fears a strange being, but the twist reveals it was secretly protecting them; the real "
                     "threat was a greedy or cruel person.",
    "riddle": "The curse can only be broken by answering a riddle stated early; the hero answers it through kindness, "
              "not just cleverness.",
}
CAPTIONS = ["yellow", "box", "pink"]
END_CARDS = ["cta_a", "cta_b", "cta_c"]
VOICES = {"male": ["Charon", "Fenrir", "Orus"], "female": ["Kore", "Aoede", "Leda"]}
MUSIC = ["mystery", "folk", "suspense"]

DIMENSIONS = {"style": list(STYLES), "structure": list(STRUCTURES), "caption": CAPTIONS, "end": END_CARDS,
              "voice_male": VOICES["male"], "voice_female": VOICES["female"], "music": MUSIC}


def pick(history, override=None):
    """{dimension: option} for the next video. `override` (experiment engine / owner) wins where valid."""
    recent = [h.get("variety") or {} for h in history if h.get("variety")]
    out = {}
    for dim, options in DIMENSIONS.items():
        want = (override or {}).get(dim)
        if want in options:
            out[dim] = want
            continue
        last_used = {}
        for i, v in enumerate(recent):
            if v.get(dim) in options:
                last_used[v[dim]] = i
        prev = recent[-1].get(dim) if recent else None
        ranked = sorted(options, key=lambda o: last_used.get(o, -1))      # least recently used first
        out[dim] = next((o for o in ranked if o != prev), ranked[0])
    return out


def voice_for(variety, narrator):
    v = variety or {}
    return v.get("voice_female", "Kore") if narrator == "female" else v.get("voice_male", "Charon")
