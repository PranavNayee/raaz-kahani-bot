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


def synth_bed(seconds, sr=SR, seed=7, lift_at=None):
    """Mysterious minor pad + low drone + sparse music-box plucks; after `lift_at` seconds it lifts to a warm
    major progression for the payoff. Generated here, so it is royalty-free."""
    rng = random.Random(seed)
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float32) / sr
    dark = [[57, 60, 64], [53, 57, 60], [50, 53, 57], [52, 56, 59]]          # Am F Dm E
    warm = [[48, 52, 55], [55, 59, 62], [57, 60, 64], [53, 57, 60]]          # C G Am F
    bar = 4.0
    hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
    out = np.zeros(n, dtype=np.float32)
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
        for k in range(4 if not lifted else 8):                                # music-box plucks
            if rng.random() < (0.45 if not lifted else 0.8):
                st = int((b * bar + k * bar / (4 if not lifted else 8)) * sr)
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


def _music(seconds, music_dir, seed, lift_at):
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
    return synth_bed(seconds, seed=seed, lift_at=lift_at)


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
          seed=7, lift_at=None):
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
    if music:
        bed = _music(total, music_dir, seed, lift_at)
        bed = np.pad(bed, (0, max(0, n - len(bed))))[:n]
        env = _smooth(np.abs(voice), int(0.25 * SR))
        env = env / (np.percentile(env[env > 1e-4], 90) + 1e-6) if np.any(env > 1e-4) else env
        duck = 1.0 - 0.7 * np.clip(env, 0, 1)                             # music dips while the narrator talks
        mix += bed * duck
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
