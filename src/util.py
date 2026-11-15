import functools, re, subprocess, time

_SECRET = re.compile(r"(access_token=|key=|bot\d+:)[A-Za-z0-9_\-:.]+")


def redact(s):
    """Never let a token reach the logs or Telegram (URLs with ?access_token=..., bot tokens, ?key=...)."""
    return _SECRET.sub(lambda m: m.group(1) + "***", str(s))


def log(*a):
    print(*(redact(x) for x in a), flush=True)


def retry(tries=4, delay=5):
    """Retry a function with growing waits. Re-raises the last error."""
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **k):
            last = None
            for i in range(tries):
                try:
                    return fn(*a, **k)
                except Exception as e:
                    last = e
                    log(f"[retry] {fn.__name__} attempt {i+1}/{tries} failed: {e}")
                    if i < tries - 1:
                        time.sleep(delay * (i + 1))
            raise last
        return wrap
    return deco


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def truthy(name, default="false"):
    import os
    return str(os.environ.get(name, default) or default).strip().lower() in ("1", "true", "yes", "on")
