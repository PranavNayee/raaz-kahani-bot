"""ONE continuous audio track for the whole video (no clicks/gaps at scene cuts): voice clips at their scene start
times + a background music bed (a royalty-free file from assets/music, or one synthesised here) that is ducked
under the voice, light SFX (whoosh, a low boom on the twist), then loudness-normalised to -14 LUFS (2-pass)."""
import glob, json, os, random, re, subprocess, wave
import numpy as np
import config as C
from util import log

SR = 44100


def load_pcm(path, sr=SR, af=None):
    """Decode any audio file to mono float32 in [-1, 1] (optionally through an ffmpeg filter chain)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path] + (["-af", af] if af else []) +
                       ["-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def write_wav(path, x, sr=SR):
    x = np.clip(np.nan_to_num(x), -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def _smooth(x, n):
    if n <= 1:
        return x
    k = np.ones(n, dtype=np.float32) / n
    return np.convolve(x, k, mode="same")


def synth_bed(seconds, sr=SR, seed=7, lift_at=None, mood="mystery"):
    """Mysterious minor pad + low drone + sparse music-box plucks; after `lift_at` seconds it lifts to a warm
    major progression for the payoff. Generated here, so it is royalty-free."""
    rng = random.Random(seed)
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float32) / sr
    out = np.zeros(n, dtype=np.float32)
    dark = [[57, 60, 64], [53, 57, 60], [50, 53, 57], [52, 56, 59]]          # Am F Dm E
    warm = [[48, 52, 55], [55, 59, 62], [57, 60, 64], [53, 57, 60]]          # C G Am F
    bar = 4.0
    if mood == "folk":                                                       # pentatonic, lighter and quicker
        dark = [[50, 53, 57], [48, 52, 55], [50, 55, 57], [45, 50, 53]]
        bar = 3.0
    elif mood == "suspense":                                                 # low, tight, with a pulse
        dark = [[52, 55, 59], [52, 55, 60], [50, 53, 57], [52, 55, 59]]
        bar = 3.4
        pulse_hz = 440.0 * 2 ** ((40 - 69) / 12)
        for k in range(int(seconds / (bar / 8))):
            st = int(k * bar / 8 * sr)
            if st >= n or (lift_at is not None and k * bar / 8 >= lift_at):
                break
            ln = min(int(0.16 * sr), n - st); tt = np.arange(ln, dtype=np.float32) / sr
            out[st:st + ln] += 0.05 * np.sin(2 * np.pi * pulse_hz * tt) * np.exp(-tt * 18)
    hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
    out += 0.018 * np.sin(2 * np.pi * hz(33) * t) * (0.7 + 0.3 * np.sin(2 * np.pi * 0.07 * t))   # A1 drone
    nb = int(seconds / bar) + 2
    for b in range(nb):
        lifted = lift_at is not None and b * bar >= lift_at
        prog = warm if lifted else dark
        chord = prog[b % len(prog)]
        s0, s1 = int(max(0, (b * bar - 0.5) * sr)), int(min(n, ((b + 1) * bar + 0.5) * sr))
        if s0 >= n:
            break
        seg = t[s0:s1]
        L = len(seg)
        env = np.clip(np.sin(np.linspace(0, np.pi, L, dtype=np.float32)), 0, 1) ** 0.8
        pad = np.zeros(L, dtype=np.float32)
        for m in chord:
            for det in (-0.003, 0.003):
                f = hz(m - 12) * (1 + det)
                pad += np.sin(2 * np.pi * f * seg) + 0.2 * np.sin(4 * np.pi * f * seg)
        out[s0:s1] += (0.03 if lifted else 0.026) * pad * env
        steps = 8 if (lifted or mood == "folk") else 4
        for k in range(steps):                                                 # music-box plucks
            if rng.random() < (0.45 if steps == 4 else 0.75):
                st = int((b * bar + k * bar / steps) * sr)
                if st >= n:
                    break
                m = chord[rng.randrange(3)] + 24
                ln = min(int(1.2 * sr), n - st)
                tt = np.arange(ln, dtype=np.float32) / sr
                out[st:st + ln] += 0.035 * np.sin(2 * np.pi * hz(m) * tt) * np.exp(-tt * 3.5)
    fade = min(int(1.5 * sr), n // 4)
    if fade:
        out[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        out[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return out


def music_files(music_dir=None):
    music_dir = music_dir or C.MUSIC_DIR
    return sorted(f for ext in ("mp3", "wav", "m4a", "ogg") for f in glob.glob(os.path.join(music_dir, f"*.{ext}")))


def _music(seconds, music_dir, seed, lift_at, mood="mystery"):
    files = music_files(music_dir)
    n = int(seconds * SR)
    if files:
        try:
            pick = random.Random(seed).choice(files)
            x = load_pcm(pick)
            if len(x) < n:
                x = np.tile(x, n // max(len(x), 1) + 1)
            x = x[:n]
            x = x / (np.sqrt(np.mean(x ** 2)) + 1e-6) * 0.05            # normalise to a quiet bed
            fade = min(SR * 2, n // 4)
            x[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
            log(f"  🎵 music: {os.path.basename(pick)}")
            return x
        except Exception as e:
            log("Music file failed, using generated bed:", e)
    return synth_bed(seconds, seed=seed, lift_at=lift_at, mood=mood)


def whoosh(sr=SR, dur=0.5, seed=3):
    """Transition whoosh: band-limited noise with a quick swell (generated, royalty-free)."""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    spec = np.fft.rfft(rng.standard_normal(n).astype(np.float32))
    f = np.fft.rfftfreq(n, 1 / sr)
    spec *= np.exp(-((np.log(f + 1) - np.log(1400)) ** 2) / 0.9)
    x = np.fft.irfft(spec, n).astype(np.float32)
    tt = np.linspace(0, 1, n, dtype=np.float32)
    env = (tt ** 1.6) * np.exp(-((tt - 0.62) ** 2) / 0.05) / 0.62 ** 1.6
    x *= env
    return 0.14 * x / (np.max(np.abs(x)) + 1e-6)


def boom(sr=SR, dur=1.4):
    """Low cinematic hit for the twist: a falling sine with a soft noise transient."""
    n = int(dur * sr)
    tt = np.arange(n, dtype=np.float32) / sr
    f = 70 * np.exp(-tt * 1.4) + 32
    ph = 2 * np.pi * np.cumsum(f) / sr
    x = np.sin(ph) * np.exp(-tt * 2.6)
    x[:int(0.03 * sr)] += np.random.default_rng(5).standard_normal(int(0.03 * sr)).astype(np.float32) * 0.3
    return 0.32 * x.astype(np.float32)


def build(voice_clips, starts, total, out_wav, music=True, music_dir=None, voice_af=None, whoosh_at=(), boom_at=(),
          seed=7, lift_at=None, ambience=(), hits=(), mood="mystery"):
    """voice_clips: audio paths; starts: start second of each clip; total: video length. Writes a mono wav."""
    n = int(round(total * SR))
    voice = np.zeros(n, dtype=np.float32)
    for path, st in zip(voice_clips, starts):
        x = load_pcm(path, af=voice_af)
        s = int(round(st * SR))
        e = min(n, s + len(x))
        if e > s:
            voice[s:e] += x[:e - s]
    mix = voice.copy()
    bed = _music(total, music_dir, seed, lift_at, mood) if music else np.zeros(n, np.float32)
    bed = np.pad(bed, (0, max(0, n - len(bed))))[:n]
    for st, x in ambience:                                                # night/wind/rain/heartbeat beds
        a = int(st * SR); e = min(n, a + len(x))
        if e > a:
            fade = min(int(0.3 * SR), (e - a) // 2)
            x = x[:e - a].copy()
            if fade:
                x[:fade] *= np.linspace(0, 1, fade, dtype=np.float32); x[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
            bed[a:e] += x
    if music or ambience:
        env = _smooth(np.abs(voice), int(0.25 * SR))
        env = env / (np.percentile(env[env > 1e-4], 90) + 1e-6) if np.any(env > 1e-4) else env
        duck = 1.0 - 0.7 * np.clip(env, 0, 1)                             # music dips while the narrator talks
        mix += bed * duck
    for st, x in hits:                                                    # one-shot effects on top
        a = int(max(0.0, st) * SR); e = min(n, a + len(x))
        if e > a:
            mix[a:e] += x[:e - a]
    w, b = whoosh(), boom()
    for st in whoosh_at:
        a = int(max(0.0, st - 0.35) * SR); e = min(n, a + len(w))
        mix[a:e] += w[:e - a]
    for st in boom_at:
        a = int(max(0.0, st) * SR); e = min(n, a + len(b))
        mix[a:e] += b[:e - a]
    peak = float(np.max(np.abs(mix))) or 1.0
    if peak > 0.98:
        mix *= 0.98 / peak
    write_wav(out_wav, mix)
    return out_wav


def loudnorm(src, dst, target=None):
    """Two-pass EBU R128 normalisation to `target` LUFS (default -14, the Shorts/Reels norm). Returns measured LUFS."""
    target = C.TARGET_LUFS if target is None else target
    spec = f"I={target}:TP=-1.5:LRA=11"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", src, "-af", f"loudnorm={spec}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    af = f"loudnorm={spec}"
    if m:
        j = json.loads(m.group(0))
        if float(j.get("input_i", "-70") if j.get("input_i") not in ("-inf", None) else -70) > -60:
            af += (f":measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}"
                   f":measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af", af, "-ar", "48000", "-ac", "2", dst], check=True)
    return measure(dst)


def measure(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"\"input_i\"\s*:\s*\"([-\d.inf]+)\"", r.stderr)
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


# ---------------------------------------------------------------- sound effects (all generated here: royalty-free)
def _noise(n, seed=0):
    return np.random.default_rng(seed).standard_normal(n).astype(np.float32)


def _band(x, lo, hi, sr=SR):
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / sr)
    spec[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(spec, len(x)).astype(np.float32)


def _norm(x, peak):
    return (peak * x / (np.max(np.abs(x)) + 1e-6)).astype(np.float32)


def crickets(dur, sr=SR, seed=11):
    """Night ambience: soft chirp trains at ~4.6 kHz."""
    n = int(dur * sr); out = np.zeros(n, np.float32); rng = np.random.default_rng(seed)
    t = 0.0
    while t < dur:
        f = 4300 + rng.uniform(0, 700)
        for k in range(rng.integers(2, 5)):
            st = int((t + k * 0.055) * sr); ln = int(0.03 * sr)
            if st + ln >= n:
                break
            tt = np.arange(ln, dtype=np.float32) / sr
            out[st:st + ln] += np.sin(2 * np.pi * f * tt) * np.sin(np.pi * tt / 0.03)
        t += rng.uniform(0.35, 0.9)
    return _norm(out, 0.035)


def wind(dur, sr=SR, seed=12):
    n = int(dur * sr)
    x = _band(_noise(n, seed), 150, 900, sr)
    t = np.arange(n, dtype=np.float32) / sr
    lfo = 0.55 + 0.45 * np.sin(2 * np.pi * 0.13 * t + 1.0) * np.sin(2 * np.pi * 0.05 * t)
    return _norm(x * lfo, 0.07)


def rain(dur, sr=SR, seed=13):
    n = int(dur * sr)
    x = _band(_noise(n, seed), 1500, 9000, sr) * 0.6
    rng = np.random.default_rng(seed)
    for st in rng.integers(0, max(1, n - 800), int(dur * 25)):
        x[st:st + 600] += _noise(600, int(st)) * np.exp(-np.arange(600) / 90) * 0.8
    return _norm(x, 0.08)


def heartbeat(dur, bpm=72, sr=SR):
    n = int(dur * sr); out = np.zeros(n, np.float32)
    beat = 60.0 / bpm
    def thump(gain):
        ln = int(0.18 * sr); tt = np.arange(ln, dtype=np.float32) / sr
        return gain * np.sin(2 * np.pi * (55 + 25 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 22)
    t = 0.2
    while t < dur - 0.4:
        for off, g in ((0.0, 1.0), (0.24, 0.7)):
            st = int((t + off) * sr); th = thump(g)
            e = min(n, st + len(th)); out[st:e] += th[:e - st]
        t += beat
    return _norm(out, 0.22)


def riser(dur=2.2, sr=SR, seed=14):
    """Tension build that peaks right at the twist."""
    n = int(dur * sr); t = np.arange(n, dtype=np.float32) / sr
    sweep = np.sin(2 * np.pi * np.cumsum(200 + 1400 * (t / dur) ** 2) / sr)
    x = 0.6 * sweep + _band(_noise(n, seed), 800, 6000, sr) * 0.5
    return _norm(x * (t / dur) ** 2.2, 0.16)


def hit(sr=SR):
    """Short cinematic impact (hook, shocks)."""
    b = boom(sr, 0.9)
    n = int(0.25 * sr)
    b[:n] += _band(_noise(n, 15), 200, 5000, sr) * np.exp(-np.arange(n, dtype=np.float32) / (0.04 * sr)) * 0.5
    return _norm(b, 0.3)


def chime(sr=SR, seed=16):
    """Magical sparkle for happy / warm moments."""
    rng = np.random.default_rng(seed); n = int(1.8 * sr); out = np.zeros(n, np.float32)
    for k, f in enumerate((1568, 1976, 2349, 3136)):
        st = int(k * 0.09 * sr); tt = np.arange(n - st, dtype=np.float32) / sr
        out[st:] += np.sin(2 * np.pi * f * tt * (1 + rng.uniform(-0.002, 0.002))) * np.exp(-tt * 3.2)
    return _norm(out, 0.09)


def bell(sr=SR):
    n = int(3.0 * sr); tt = np.arange(n, dtype=np.float32) / sr
    x = sum(a * np.sin(2 * np.pi * f * tt) * np.exp(-tt * d) for f, a, d in
            ((392, 1.0, 1.1), (784, 0.5, 1.6), (1046, 0.35, 2.2), (1371, 0.25, 2.8), (1960, 0.12, 3.5)))
    return _norm(x, 0.14)


def thunder(sr=SR, seed=17):
    n = int(3.0 * sr); tt = np.arange(n, dtype=np.float32) / sr
    x = _band(_noise(n, seed), 30, 260, sr) * (np.exp(-tt * 1.3) + 0.6 * np.exp(-((tt - 0.5) ** 2) / 0.05))
    return _norm(x, 0.3)


def footsteps(count=4, gap=0.48, sr=SR):
    n = int((count * gap + 0.4) * sr); out = np.zeros(n, np.float32)
    for k in range(count):
        st = int(k * gap * sr); ln = int(0.12 * sr); tt = np.arange(ln, dtype=np.float32) / sr
        out[st:st + ln] += (_band(_noise(ln, 20 + k), 80, 700, sr) * np.exp(-tt * 40) + 
                            0.6 * np.sin(2 * np.pi * 70 * tt) * np.exp(-tt * 30))
    return _norm(out, 0.12)


def creak(sr=SR):
    n = int(1.3 * sr); tt = np.arange(n, dtype=np.float32) / sr
    f = 180 + 120 * np.sin(2 * np.pi * 0.6 * tt) + 40 * np.sin(2 * np.pi * 7 * tt)
    x = np.sign(np.sin(2 * np.pi * np.cumsum(f) / sr)) * (0.5 + 0.5 * np.sin(2 * np.pi * 23 * tt))
    return _norm(_band(x, 200, 3000, sr) * np.sin(np.pi * tt / 1.3), 0.06)


KEYWORD_SFX = [  # (pattern over narration + image prompt, kind)
    (r"rain|बारिश|बरसात|बूँद", "rain"), (r"thunder|lightning|storm|बिजली|तूफ़ान|तूफान|गरज", "thunder"),
    (r"\bbells?\b|ghanti|घंटी|घंटा", "bell"), (r"\bdoors?\b|darwaz|दरवाज़|दरवाज", "creak"),
    (r"footstep|walking|sneak|कदम|चुपके", "steps"), (r"\bwind\b|storm|हवा|आँधी|आंधी", "wind"),
    (r"night|moon|dark|रात|चाँद|चांद|अंधेर|पूर्णिमा", "crickets"),
]


def plan_sfx(scenes, tl, twist_t, story_end):
    """Which sounds go where. Returns (ambience, hits): lists of (start_seconds, samples).
    Ambience sits under the music and is ducked with it; hits are added on top."""
    import re as _re
    amb, hits = [], [(0.0, hit())]                              # the hook lands with an impact
    for i, (sc, (st, d)) in enumerate(zip(scenes, tl)):
        txt = (sc.get("narration_hi", "") + " " + sc.get("image_prompt", "")).lower()
        kinds = {k for pat, k in KEYWORD_SFX if _re.search(pat, txt)}
        emo = sc.get("emotion")
        if "rain" in kinds:
            amb.append((st, rain(d)))
        if "wind" in kinds or emo == "eerie":
            amb.append((st, wind(d)))
        if "crickets" in kinds and "rain" not in kinds:
            amb.append((st, crickets(d)))
        if emo in ("tense", "fear"):
            amb.append((st, heartbeat(d, 80 if emo == "fear" else 70)))
        if "thunder" in kinds:
            hits.append((st + 0.2, thunder()))
        if "bell" in kinds:
            hits.append((st + 0.3, bell()))
        if "creak" in kinds:
            hits.append((st + 0.4, creak()))
        if "steps" in kinds:
            hits.append((st + 0.5, footsteps()))
        if emo in ("joy", "triumph", "warm", "hopeful"):
            hits.append((st + 0.1, chime()))
        if emo == "shock" and i > 0 and st != twist_t:
            hits.append((st, hit() * 0.7))
        if i > 0 and st != twist_t:
            hits.append((max(0.0, st - 0.3), whoosh() * 0.55))     # soft whoosh on every cut
    if twist_t:
        r = riser()
        hits.append((max(0.0, twist_t - len(r) / SR), r))
    return amb, hits
