"""Human-sounding Hindi narration. Providers, best first (TTS_PROVIDER=auto picks the first one configured):

  gemini     - Google Gemini TTS (free with GEMINI_API_KEY), voice Charon (male) / GEMINI_VOICE_FEMALE for
               female-led stories, directed like a storyteller. The WHOLE video is voiced in ONE request
               (one consistent performance, ~10x less free quota), then split into scenes at the pauses.
  elevenlabs - most human (paid; set ELEVENLABS_API_KEY, optional ELEVENLABS_VOICE_ID). Exact word timings.
  edge       - Microsoft hi-IN-MadhurNeural / SwaraNeural (voice.py), the free safety net.

A video is voiced by ONE provider from start to finish. STRICT_VOICE=true (manual "post now" runs) never falls
back to the robotic Edge voice; scheduled runs may, so a day is never missed. Every clip is sped up by
VOICE_SPEED (default 1.1x, pitch kept). Returns [(audio_path, seconds, [(start, end, caption_word)])]."""
import base64, concurrent.futures, os, re, subprocess, time, requests
import numpy as np
import config as C, voice, soundtrack
from util import log

GEM_URL = "https://generativelanguage.googleapis.com/v1beta"
EL_URL = "https://api.elevenlabs.io/v1"
LAST = ""


# ---------------------------------------------------------------- timing helpers
def _trim(x, sr, lead=0.12, tail=0.3, thr=0.012):
    idx = np.where(np.abs(x) > thr)[0]
    if not len(idx):
        return x
    a = max(0, idx[0] - int(lead * sr)); b = min(len(x), idx[-1] + int(tail * sr))
    return x[a:b]


def _env(x, sr):
    hop = int(0.01 * sr)
    return np.array([np.sqrt(np.mean(x[i:i + hop] ** 2)) for i in range(0, max(len(x) - hop, 1), hop)])


def pause_words(text, x, sr):
    """Word timings without TTS timestamps: split the text into phrases at punctuation, find the matching
    pauses in the audio, then spread each phrase's words over its own speech span by word length."""
    words = text.split()
    if not words:
        return []
    dur = len(x) / sr
    env = _env(x, sr)
    voiced = env > max(0.02 * float(env.max() or 1), 0.004)
    vi = np.where(voiced)[0]
    if not len(vi):
        return voice.estimate_words(text, dur)
    s0, s1 = vi[0] * 0.01, (vi[-1] + 1) * 0.01
    gaps, run = [], None
    for i in range(vi[0], vi[-1] + 1):
        if not voiced[i]:
            run = i if run is None else run
        elif run is not None:
            if (i - run) * 0.01 >= 0.14:
                gaps.append((run * 0.01, i * 0.01))
            run = None
    phrases, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[,.?!;:।]$", w):
            phrases.append(cur); cur = []
    if cur:
        phrases.append(cur)
    k = len(phrases) - 1
    cuts = sorted(sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)[:k]) if k and len(gaps) >= k else []
    if len(cuts) != k:
        phrases, cuts = [words], []
    spans, st = [], s0
    for g in cuts:
        spans.append((st, g[0])); st = g[1]
    spans.append((st, s1))
    out = []
    for ph, (a, b) in zip(phrases, spans):
        wts = [len(w) + 2 for w in ph]
        tot, t = float(sum(wts)), a
        for w, k2 in zip(ph, wts):
            d = (b - a) * k2 / tot
            out.append((t, t + d * 0.92, w)); t += d
    return out


def _save(x, sr, path):
    soundtrack.write_wav(path, x, sr)
    return path, len(x) / sr


# ---------------------------------------------------------------- Gemini TTS
_gem_models = None


def gemini_tts_models():
    global _gem_models
    if _gem_models is not None:
        return _gem_models
    forced = [m.strip() for m in os.environ.get("GEMINI_TTS_MODELS", "").split(",") if m.strip()]
    names = []
    try:
        r = requests.get(f"{GEM_URL}/models", headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                         params={"pageSize": 200}, timeout=30)
        if r.status_code == 200:
            names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                     if "tts" in m["name"].lower() and "generateContent" in m.get("supportedGenerationMethods", [])]
    except Exception as e:
        log("Gemini TTS model discovery failed:", e)

    def rank(n):                                        # newest version first, flash before pro (quota)
        v = re.search(r"(\d+(?:\.\d+)?)", n)
        return (-(float(v.group(1)) if v else 0), "lite" in n, "pro" in n, "preview" in n, n)
    ranked = sorted(names, key=rank)
    full = [n for n in ranked if "lite" not in n]
    _gem_models = forced or (full or ranked)[:3] or ["gemini-2.5-flash-preview-tts"]
    log("Gemini TTS models:", _gem_models)
    return _gem_models


def gemini_voice(story):
    return C.GEMINI_VOICE_FEMALE if story.get("narrator") == "female" else C.GEMINI_VOICE


def _direction(text, minimal=False, paragraphs=False, female=False):
    """Gemini TTS speaks the TRANSCRIPT only; the notes above it steer the performance."""
    if minimal:
        return f"Hindi mein ek rahasyamay kahani sunaiye, dheere aur asar ke saath: {text}"
    who = "a warm, captivating young Indian woman" if female else "a deep-voiced, captivating Indian man"
    return ("Read ONLY the text under TRANSCRIPT aloud, in Hindi. Never read these notes.\n"
            f"# AUDIO PROFILE: {who}, a master storyteller narrating a spooky Hindi mystery for Instagram Reels "
            "and YouTube Shorts - like the best Hindi horror/fantasy story channels.\n"
            "# DIRECTOR'S NOTES\n"
            "Style: gripping and cinematic. Open with a shocked, urgent hook. Build suspense: slow down and lower "
            "the voice on eerie lines, almost whisper the scariest moments, speed up when danger grows, hit the "
            "twist with surprise, and turn warm and emotional for the happy ending and the moral. When a character "
            "speaks, act the line. Real human, never robotic, never monotone.\n"
            "Accent: natural Hindi as spoken in North India, perfect pronunciation.\n"
            "Pace: storyteller pace with dramatic pauses at commas and full stops."
            + (" Leave a clear pause of about one second between paragraphs." if paragraphs else "") + "\n"
            f"# TRANSCRIPT\n{text}")


def _max_secs(text):
    return 2.5 + 0.6 * len(text.split())           # natural Hindi ~0.4 s/word; reading the notes is far longer


def _gemini_one(text, voice_name, female=False, max_secs=None, paragraphs=False):
    last = None
    for model in gemini_tts_models():
        for attempt in range(3):
            try:
                r = requests.post(f"{GEM_URL}/models/{model}:generateContent",
                                  headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, timeout=(10, 180),
                                  json={"contents": [{"parts": [{"text": _direction(
                                            text, minimal=attempt == 2 and not paragraphs, paragraphs=paragraphs,
                                            female=female)}]}],
                                        "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                                            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}}}})
            except requests.exceptions.RequestException as e:
                last = e; time.sleep(5); continue
            if r.status_code == 200:
                try:
                    part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                except (KeyError, IndexError, TypeError, ValueError):
                    cand = (r.json().get("candidates") or [{}])[0] if r.text.startswith("{") else {}
                    last = f"{model}: no audio returned (finishReason {cand.get('finishReason', '?')})"
                    time.sleep(3); continue
                rate = int((re.search(r"rate=(\d+)", part.get("mimeType", "")) or [0, 24000])[1])
                pcm = np.frombuffer(base64.b64decode(part["data"]), dtype=np.int16).astype(np.float32) / 32768.0
                if len(pcm) < rate * 0.4:
                    last = "empty audio"; continue
                pcm = _trim(pcm, rate)
                if len(pcm) / rate > (max_secs or _max_secs(text)):         # it read the director's notes too
                    last = f"audio too long ({len(pcm) / rate:.1f}s for {len(text.split())} words)"
                    log(f"  Gemini TTS {model}: {last} - retrying")
                    continue
                return model, pcm, rate
            last = f"{model} {r.status_code}: {r.text[:160]}"
            if r.status_code == 429:
                m = re.search(r'"retryDelay":\s*"(\d+)', r.text)
                wait = int(m.group(1)) + 2 if m else 20 * (attempt + 1)
                if wait > 70:                           # daily quota gone: try the next model
                    break
                time.sleep(wait); continue
            if r.status_code >= 500:
                time.sleep(6 * (attempt + 1)); continue
            break                                       # 400/403/404: this model can't be used
    raise RuntimeError(f"Gemini TTS failed: {last}")


def _gaps(x, sr, min_gap=0.32):
    env = _env(x, sr)
    voiced = env > max(0.02 * float(env.max() or 1), 0.004)
    gaps, run = [], None
    for i, v in enumerate(voiced):
        if not v:
            run = i if run is None else run
        elif run is not None:
            if (i - run) * 0.01 >= min_gap:
                gaps.append((run * 0.01, i * 0.01))
            run = None
    return gaps


def _match_gaps(gaps, expected, pos=lambda t: t):
    """Pick one pause per scene boundary, in order, closest to where the script says the boundary should be
    (longer pauses preferred). Dynamic programming over the sorted pauses."""
    m, k = len(gaps), len(expected)
    INF = float("inf")
    cost = lambda gi, ki: abs(pos((gaps[gi][0] + gaps[gi][1]) / 2) - expected[ki]) - 0.15 * (gaps[gi][1] - gaps[gi][0])
    best = [[INF] * m for _ in range(k)]
    back = [[-1] * m for _ in range(k)]
    for gi in range(m):
        best[0][gi] = cost(gi, 0)
    for ki in range(1, k):
        run, arg = INF, -1
        for gi in range(m):
            if gi - 1 >= 0 and best[ki - 1][gi - 1] < run:
                run, arg = best[ki - 1][gi - 1], gi - 1
            if arg >= 0:
                best[ki][gi] = run + cost(gi, ki); back[ki][gi] = arg
    gi = min(range(m), key=lambda g: best[k - 1][g])
    picked = []
    for ki in range(k - 1, -1, -1):
        picked.append(gaps[gi]); gi = back[ki][gi]
    return picked[::-1]


def _gemini_video(scenes, story, work):
    """ONE request for the whole video, split into scenes at the paragraph pauses. Raises if the split doesn't
    match the script (then each scene is voiced on its own)."""
    texts = [voice.clean_for_tts(sc["narration_hi"]) for sc in scenes]
    words = [len(t.split()) for t in texts]
    female = story.get("narrator") == "female"
    model, x, sr = _gemini_one("\n\n".join(texts), gemini_voice(story), female, paragraphs=True,
                               max_secs=3.0 + 0.6 * sum(words) + 1.5 * len(scenes))
    n = len(scenes)
    gaps = [g for g in _gaps(x, sr) if g[0] > 0.3 and g[1] < len(x) / sr - 0.3]
    if len(gaps) < n - 1:
        raise RuntimeError(f"whole-video voice: found {len(gaps)} pauses for {n} scenes")
    total, tw = len(x) / sr, float(sum(words))
    env = _env(x, sr)                                    # work in SPEECH time so pause lengths can't fool us
    voiced = env > max(0.02 * float(env.max() or 1), 0.004)
    v_tot = float(voiced.sum()) or 1.0
    cum = np.concatenate([[0], np.cumsum(voiced)])
    spoken_at = lambda t: float(cum[min(len(cum) - 1, int(t * 100))]) / 100
    cuts = _match_gaps(gaps, [v_tot / 100 * sum(words[:k + 1]) / tw for k in range(n - 1)], spoken_at)
    bounds = [0.0] + [(a + b) / 2 for a, b in cuts] + [total]
    for i in range(n):
        v_i = float(voiced[int(bounds[i] * 100):int(bounds[i + 1] * 100)].sum())
        ratio = (v_i / v_tot) / (words[i] / tw)
        if not 0.55 <= ratio <= 1.7:
            raise RuntimeError(f"whole-video voice: scene {i + 1} split looks wrong (speech ratio {ratio:.2f})")
    out = []
    for i in range(n):
        seg = _trim(x[int(bounds[i] * sr):int(bounds[i + 1] * sr)], sr, lead=0.08, tail=0.25)
        path, d = _save(seg, sr, os.path.join(work, f"voice_{i + 1:02d}.wav"))
        out.append((path, d, voice.align(texts[i], pause_words(texts[i], seg, sr), d)))
    log(f"  whole-video voice OK ({model}, voice {gemini_voice(story)}, 1 request)")
    return out


def _gemini_scene(i, sc, story, work):
    text = voice.clean_for_tts(sc["narration_hi"])
    female = story.get("narrator") == "female"
    _, x, sr = _gemini_one(text, gemini_voice(story), female)
    p, d = _save(x, sr, os.path.join(work, f"voice_{i + 1:02d}.wav"))
    return p, d, pause_words(text, x, sr)


# ---------------------------------------------------------------- ElevenLabs
_el_voice = None


def _el_voice_id():
    global _el_voice
    if _el_voice:
        return _el_voice
    _el_voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    if not _el_voice:
        r = requests.get(f"{EL_URL}/voices", headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=30)
        vs = r.json().get("voices", []) if r.status_code == 200 else []
        male = [v for v in vs if (v.get("labels") or {}).get("gender") == "male"]
        _el_voice = (male or vs or [{}])[0].get("voice_id")
        if not _el_voice:
            raise RuntimeError(f"ElevenLabs: no voice available ({r.status_code} {r.text[:120]})")
    return _el_voice


def _el_scene(i, sc, story, work):
    text = voice.clean_for_tts(sc["narration_hi"])
    for attempt in range(3):
        r = requests.post(f"{EL_URL}/text-to-speech/{_el_voice_id()}/with-timestamps",
                          params={"output_format": "mp3_44100_128"}, timeout=(10, 120),
                          headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                          json={"text": text, "model_id": C.ELEVENLABS_MODEL,
                                "voice_settings": {"stability": 0.4, "similarity_boost": 0.8, "style": 0.45,
                                                   "use_speaker_boost": True}})
        if r.status_code == 200:
            break
        if r.status_code in (429, 500, 502, 503) and attempt < 2:
            time.sleep(8 * (attempt + 1)); continue
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    j = r.json()
    path = os.path.join(work, f"voice_{i + 1:02d}.mp3")
    with open(path, "wb") as f:
        f.write(base64.b64decode(j["audio_base64"]))
    al = j.get("alignment") or {}
    spoken, cur, st, prev_end = [], "", None, 0
    for ch, a, b in zip(al.get("characters", []), al.get("character_start_times_seconds", []),
                        al.get("character_end_times_seconds", [])):
        if ch.isspace():
            if cur:
                spoken.append((st, prev_end, cur)); cur = ""
            continue
        if not cur:
            st = a
        cur += ch; prev_end = b
    if cur:
        spoken.append((st, prev_end, cur))
    d = voice.duration(path)
    return path, d, voice.align(text, spoken, d)


# ---------------------------------------------------------------- Edge (safety net)
def _edge_all(scenes, story, work):
    return voice.speak_scenes(scenes, story, work)


def order():
    if C.OFFLINE:
        return ["edge"]
    avail = [p for p, ok in (("elevenlabs", bool(os.environ.get("ELEVENLABS_API_KEY"))),
                             ("gemini", bool(os.environ.get("GEMINI_API_KEY"))), ("edge", True)) if ok]
    if C.TTS_PROVIDER in avail:
        avail.remove(C.TTS_PROVIDER); avail.insert(0, C.TTS_PROVIDER)
    return avail


def _speed_up(res):
    """Speed every clip up by VOICE_SPEED (ffmpeg atempo keeps the pitch) and rescale the word timings."""
    k = C.VOICE_SPEED
    if abs(k - 1.0) < 0.01 or LAST == "silent":
        return res
    out = []
    for path, d, words in res:
        fast = path.rsplit(".", 1)[0] + "_sp.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-filter:a", f"atempo={k}", fast], check=True)
        out.append((fast, voice.duration(fast), [(a / k, b / k, w) for a, b, w in words]))
    return out


def speak_scenes(scenes, story, work):
    """Voices every scene with the best provider that can do ALL of them. Returns [(path, secs, words)]."""
    global LAST
    errors = []
    provs = order()
    if C.STRICT_VOICE and len(provs) > 1:
        provs = [p for p in provs if p != "edge"]
    for prov in provs:
        log(f"🎙️ Voicing {len(scenes)} scenes with {prov}...")
        try:
            if prov == "edge":
                res = _edge_all(scenes, story, work)
                LAST = voice.LAST
                return res                                   # Edge already speaks at the brisk VOICE_RATE
            if prov == "gemini":
                try:
                    res = _gemini_video(scenes, story, work)
                except Exception as e:
                    if "429" in str(e) and "quota" in str(e).lower():
                        raise
                    log(f"  whole-video voice failed ({str(e)[:160]}) - voicing scene by scene")
                    res = [_gemini_scene(i, sc, story, work) for i, sc in enumerate(scenes)]
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                    res = list(ex.map(lambda a: _el_scene(a[0], a[1], story, work), enumerate(scenes)))
            LAST = prov
            for i, (p, d, _) in enumerate(res):
                log(f"  🗣️ scene {i + 1}: {d:.1f}s")
            return _speed_up(res)
        except Exception as e:
            errors.append(f"{prov}: {str(e)[:200]}")
            log(f"  ⚠️ {prov} voice failed, trying the next provider: {str(e)[:200]}")
    raise RuntimeError(("No human-like voice available (STRICT_VOICE): " if C.STRICT_VOICE else
                        "All voice providers failed: ") + " | ".join(errors))
