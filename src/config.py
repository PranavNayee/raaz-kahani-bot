import os, re

def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(f"Missing secret/env var: {name}")
    return v

def flag(name, default="true"):
    return str(env(name, default) or default).strip().lower() in ("1", "true", "yes", "on")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# ---------------- Secrets (GitHub: Settings > Secrets and variables > Actions) ----------------
GEMINI_API_KEY   = lambda: env("GEMINI_API_KEY", required=True)
TELEGRAM_TOKEN   = lambda: env("TELEGRAM_BOT_TOKEN", required=True)
TELEGRAM_CHAT_ID = lambda: str(env("TELEGRAM_CHAT_ID", required=True))
SUPABASE_URL     = lambda: env("SUPABASE_URL", required=True).rstrip("/")      # https://xxxx.supabase.co
SUPABASE_KEY     = lambda: env("SUPABASE_SERVICE_KEY", required=True)         # secret / service_role key
SUPABASE_BUCKET  = lambda: env("SUPABASE_BUCKET", "raaz-kahani")              # must be a PUBLIC bucket
IG_USER_ID       = lambda: env("IG_USER_ID", required=True)
IG_ACCESS_TOKEN  = lambda: env("IG_ACCESS_TOKEN", required=True)

def ig_enabled():
    return bool(env("IG_USER_ID") and env("IG_ACCESS_TOKEN"))

def telegram_enabled():
    return bool(env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"))

# false = notifications only: the bot never reads /post /postnow /skip (use this when the Telegram bot is shared
# with another bot, so a command meant for that bot can never skip or post a Raaz Kahani video)
TELEGRAM_COMMANDS = flag("TELEGRAM_COMMANDS", "true")

# ---------------- Behaviour (GitHub: Settings > Secrets and variables > Actions > Variables) ----------------
# REVIEW_MODE: auto             -> posts by itself at the slot time
#              auto_unless_skip -> posts at the slot unless you send /skip in Telegram
#              approve          -> posts at/after the slot only once you send /post in Telegram
REVIEW_MODE  = (env("REVIEW_MODE", "auto_unless_skip") or "auto_unless_skip").strip().lower()
REVIEW_HOURS = float(env("REVIEW_HOURS", "2") or 2)   # auto_unless_skip: minimum time you get to /skip after the preview

# ---------------- Schedule ----------------
POST_TIMES = [t.strip() for t in (env("POST_TIMES", "") or "19:00").split(",") if t.strip()]
TIMEZONE           = env("TIMEZONE", "Asia/Kolkata") or "Asia/Kolkata"
LATE_GRACE_MINUTES = int(env("LATE_GRACE_MINUTES", "180") or 180)   # a late video still posts up to 3 h after its slot
MAX_WAIT_MINUTES   = int(env("MAX_WAIT_MINUTES", "330") or 330)     # a job may sleep this long to hit the slot exactly

# ---------------- The channel ----------------
CHANNEL      = env("CHANNEL_NAME", "Raaz Kahani") or "Raaz Kahani"
SERIES_EVERY = int(env("SERIES_EVERY", "3") or 3)           # every Nth story is a 2-part cliffhanger series (0 = never)
MAX_SECONDS  = float(env("MAX_SECONDS", "150") or 150)
MIN_SECONDS  = float(env("MIN_SECONDS", "60") or 60)
THEME        = (env("THEME", "auto") or "auto").strip().lower()  # workflow_dispatch override
# one look for the whole channel, prepended to EVERY image prompt (visual consistency)
STYLE_BIBLE = env("STYLE_BIBLE", "") or (
    "stylised 3D animated film look, warm cinematic lighting, soft volumetric light, expressive faces, "
    "rich saturated colours, Indian village and town settings, vertical 9:16 composition, high detail")

# ---------------- Images ----------------
IMAGE_PROVIDERS = [p.strip() for p in (env("IMAGE_PROVIDERS", "gemini,pollinations") or "gemini,pollinations").split(",") if p.strip()]
POLLINATIONS_URL = env("POLLINATIONS_URL", "https://image.pollinations.ai/prompt/") or "https://image.pollinations.ai/prompt/"
POLLINATIONS_MODEL = env("POLLINATIONS_MODEL", "flux") or "flux"
IMAGE_REFERENCE = flag("IMAGE_REFERENCE")      # Gemini: pass the first good frame as a style/character reference
MIN_REAL_IMAGES = float(env("MIN_REAL_IMAGES", "0.6") or 0.6)   # below this share of real images the video is not made
OFFLINE = flag("OFFLINE", "false")            # tests: no network at all (placeholder images, silent TTS)

# ---------------- Voice ----------------
VOICE_MALE   = env("VOICE_MALE", "hi-IN-MadhurNeural") or "hi-IN-MadhurNeural"
VOICE_FEMALE = env("VOICE_FEMALE", "hi-IN-SwaraNeural") or "hi-IN-SwaraNeural"
VOICE_RATE   = env("VOICE_RATE", "+4%") or "+4%"          # brisk Shorts storytelling pace
HOOK_RATE    = env("HOOK_RATE", "+12%") or "+12%"         # the first line lands fast (0-2 s hook)
VOICE_PITCH  = env("VOICE_PITCH", "-4Hz") or "-4Hz"
DIALOGUE_VOICE = flag("DIALOGUE_VOICE")                  # second voice for characters' spoken lines
ALLOW_SILENT_TTS = flag("ALLOW_SILENT_TTS", "false")     # offline test only: silence instead of failing
VOICE_FX = flag("VOICE_FX")

# ---------------- Human-like voice (tts.py) ----------------
# auto = ElevenLabs if ELEVENLABS_API_KEY is set, else Gemini TTS (free with GEMINI_API_KEY), else Edge.
TTS_PROVIDER = (env("TTS_PROVIDER", "auto") or "auto").strip().lower()
GEMINI_VOICE = env("GEMINI_VOICE", "Charon") or "Charon"                 # deep, clear male storyteller
GEMINI_VOICE_FEMALE = env("GEMINI_VOICE_FEMALE", "Kore") or "Kore"       # female-led stories
VOICE_SPEED = float(env("VOICE_SPEED", "1.1") or 1.1)                    # 1.1 = 10% faster, pitch kept
STRICT_VOICE = flag("STRICT_VOICE", "false")                             # true: never fall back to Edge
ELEVENLABS_MODEL = env("ELEVENLABS_MODEL", "eleven_multilingual_v2") or "eleven_multilingual_v2"
VOICE_FILTER = ("highpass=f=70,equalizer=f=120:t=q:w=1:g=3,equalizer=f=3200:t=q:w=1.5:g=2,"
                "acompressor=threshold=-20dB:ratio=3:attack=5:release=90:makeup=2,alimiter=limit=0.95")

# ---------------- Audio / video ----------------
MUSIC = flag("MUSIC")
MUSIC_DIR = os.path.join(ROOT, "assets", "music")
FONT_DIR = os.path.join(ROOT, "assets", "fonts")
TARGET_LUFS = float(env("TARGET_LUFS", "-14") or -14)

# ---------------- YouTube ----------------
YT_PRIVACY = env("YT_PRIVACY", "public") or "public"
YT_CATEGORY = "24"                                       # Entertainment

# ---------------- Captions & keywords ----------------
HASHTAGS = ["#hindikahani", "#hindistory", "#raazkahani", "#mysterystory", "#fantasystory",
            "#aistory", "#kahaniyan", "#moralstory"]
YT_TAGS = ["hindi story", "hindi kahani", "mystery story", "fantasy story", "moral story", "ai animation",
           "raaz kahani", "hindi kahaniyan"]
BRAND_TAG = "#" + re.sub(r"[^a-z0-9]", "", CHANNEL.lower())
