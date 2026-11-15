"""Narration with Microsoft Edge neural voices (free): hi-IN-MadhurNeural (male) / hi-IN-SwaraNeural (female),
an optional second voice for characters' dialogue lines, and WORD TIMINGS so the karaoke captions highlight each
word exactly when it is spoken. ALLOW_SILENT_TTS (offline test only) writes silence with estimated timings."""
import asyncio, os, re, subprocess, time
import config as C
from util import log, duration

SILENT_WPS = 2.4                     # Hindi storytelling pace, words per second (estimates / silent fallback)
LAST = ""


def clean_for_tts(text):
    text = re.sub(r"[^ऀ-ॿ\w\s,.?!।'\-:;]", " ", text)   # drop emojis / odd symbols
    return re.sub(r"\s+", " ", text).strip()


def voice_for(speaker, story):
    """(voice, rate, pitch) for the narrator or a character."""
    narr_f = story.get("narrator") == "female"
    narr = C.VOICE_FEMALE if narr_f else C.VOICE_MALE
    base = {"voice": narr, "rate": C.VOICE_RATE, "pitch": C.VOICE_PITCH}
    if speaker in (None, "", "narrator") or not C.DIALOGUE_VOICE:
        return base
    ch = next((c for c in story.get("characters", []) if c["id"] == speaker), None)
    if not ch:
        return base
    g = ch.get("gender")
    v = C.VOICE_FEMALE if g == "female" else C.VOICE_MALE
    if g == "other":
        return {"voice": narr, "rate": "-10%", "pitch": "-14Hz"}            # creatures / spirits: slow and deep
    if v == narr:                                                         # same voice as narrator: shift it
        return {"voice": v, "rate": "+2%", "pitch": "+10Hz" if g == "female" else "-12Hz"}
    return {"voice": v, "rate": "+0%", "pitch": "+0Hz"}


async def _tts(text, cfg, out):
    import edge_tts
    try:
        com = edge_tts.Communicate(text, cfg["voice"], rate=cfg["rate"], pitch=cfg["pitch"], boundary="WordBoundary")
    except TypeError:                                   # edge-tts < 7 always sends word boundaries
        com = edge_tts.Communicate(text, cfg["voice"], rate=cfg["rate"], pitch=cfg["pitch"])
    words = []
    with open(out, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                st = ch["offset"] / 1e7
                words.append((st, st + ch["duration"] / 1e7, ch["text"]))
    return words


def estimate_words(text, dur):
    """Fallback timing when the TTS gives no word boundaries: spread words by their length."""
    ws = text.split()
    if not ws:
        return []
    lead, tail = 0.1, 0.25
    span = max(dur - lead - tail, 0.5)
    weights = [len(w) + 2 for w in ws]
    tot, t, out = sum(weights), lead, []
    for w, k in zip(ws, weights):
        d = span * k / tot
        out.append((t, t + d * 0.9, w)); t += d
    return out


def silent(text, out):
    """Silence of the estimated spoken length (offline test). Returns (wav_path, words)."""
    out = out.rsplit(".", 1)[0] + ".wav"
    d = max(1.2, len(text.split()) / SILENT_WPS + 0.4)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{d:.2f}",
                    out], check=True)
    return out, estimate_words(text, d)


def synth(text, cfg, out):
    """Writes the audio and returns (path, [(start_s, end_s, spoken_word)])."""
    global LAST
    text = clean_for_tts(text)
    if C.OFFLINE and C.ALLOW_SILENT_TTS:
        LAST = "silent"
        return silent(text, out)
    last = None
    for attempt in range(4):
        try:
            words = asyncio.run(_tts(text, cfg, out))
            d = duration(out)
            if d > 0.3:
                LAST = "edge"
                return out, words or estimate_words(text, d)
            last = "empty audio"
        except Exception as e:
            last = e
        log(f"  TTS retry {attempt+1}: {last}")
        time.sleep(3 * (attempt + 1))
    if C.ALLOW_SILENT_TTS:
        log("  ⚠️ TTS unavailable - silent fallback (ALLOW_SILENT_TTS)")
        LAST = "silent"
        return silent(text, out)
    raise RuntimeError(f"Voice generation failed: {last}")


def align(caption_text, tts_words, dur):
    """Map the on-screen words onto the spoken word timings (same count -> 1:1, else by relative position)."""
    cap = caption_text.split()
    if not cap:
        return []
    if not tts_words:
        return estimate_words(caption_text, dur)
    if len(cap) == len(tts_words):
        return [(s, e, w) for (s, e, _), w in zip(tts_words, cap)]
    spoken = [len(w[2]) + 1 for w in tts_words]
    tot_s = float(sum(spoken))
    marks, acc = [], 0.0
    for (s, e, _), k in zip(tts_words, spoken):
        marks.append((acc / tot_s, s, e, (acc + k) / tot_s)); acc += k

    def time_at(frac):
        for f0, s, e, f1 in marks:
            if frac <= f1:
                return s + (e - s) * ((frac - f0) / max(f1 - f0, 1e-6))
        return marks[-1][2]
    lens = [len(w) + 1 for w in cap]
    tot_c = float(sum(lens))
    out, acc = [], 0.0
    for w, k in zip(cap, lens):
        a, b = acc / tot_c, (acc + k) / tot_c
        out.append((time_at(a), max(time_at(a) + 0.05, time_at(b) - 0.02), w)); acc += k
    return out


def speak_scenes(scenes, story, work):
    """Voices every scene. Returns [(audio_path, seconds, [(start, end, caption_word)])].
    Caption words are the scene's own Devanagari words (emoji-free)."""
    out = []
    for i, sc in enumerate(scenes):
        cfg = voice_for(sc.get("speaker"), story)
        if i == 0 and cfg["rate"] == C.VOICE_RATE:
            cfg = dict(cfg, rate=C.HOOK_RATE)
        path, words = synth(sc["narration_hi"], cfg, os.path.join(work, f"voice_{i + 1:02d}.mp3"))
        d = duration(path)
        cap = clean_for_tts(sc["narration_hi"])
        out.append((path, d, align(cap, words, d)))
        log(f"  🗣️ scene {i + 1}: {d:.1f}s ({cfg['voice'] if LAST != 'silent' else 'silent fallback'}{'' if cfg['pitch'] in ('+0Hz', C.VOICE_PITCH) else ' ' + cfg['pitch']})")
    return out


def fit_tempo(voices, max_voice_seconds):
    """If the narration is longer than allowed, speed every clip up evenly (max 1.25x) and rescale timings."""
    tot = sum(d for _, d, _ in voices)
    if tot <= max_voice_seconds:
        return voices
    k = min(1.25, tot / max_voice_seconds)
    log(f"  ⏩ narration {tot:.0f}s > {max_voice_seconds:.0f}s allowed - speeding up {k:.2f}x")
    out = []
    for p, d, ws in voices:
        q = p.rsplit(".", 1)[0] + "_fast.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", p, "-af", f"atempo={k:.4f}", q], check=True)
        out.append((q, duration(q), [(a / k, b / k, w) for a, b, w in ws]))
    return out
