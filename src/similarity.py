"""Never the same story twice (YouTube 'inauthentic content' safety).

Every premise is embedded and compared with the last 200 stories and rejected before anything is rendered when it
is too similar. Embeddings come from Gemini (free); without a key (tests) a local character n-gram vector is used.
Thresholds are per method because the scales differ: n-gram vectors use SIM_THRESHOLD (0.80, as briefed); Gemini
embeddings score unrelated stories ~0.75 and reworded copies ~0.90, so they use GEMINI_SIM_THRESHOLD (0.88,
calibrated on real premises). The bank lives in storage as premises.json."""
import hashlib, math, os, re, requests
import config as C
from util import log

GEM_URL = "https://generativelanguage.googleapis.com/v1beta"
DIM = 768
_model = []


def _gemini_model():
    if _model:
        return _model[0]
    name = os.environ.get("GEMINI_EMBED_MODEL", "")
    if not name:
        try:
            r = requests.get(f"{GEM_URL}/models", headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                             params={"pageSize": 200}, timeout=30)
            names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                     if "embedContent" in m.get("supportedGenerationMethods", [])] if r.status_code == 200 else []
            name = next((n for n in names if n.startswith("gemini-embedding")), None) or (names[0] if names else "")
        except Exception as e:
            log("Embedding model discovery failed:", e)
    _model.append(name or "gemini-embedding-001")
    return _model[0]


def _gemini(text):
    r = requests.post(f"{GEM_URL}/models/{_gemini_model()}:embedContent", timeout=30,
                      headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                      json={"content": {"parts": [{"text": text}]}, "taskType": "SEMANTIC_SIMILARITY",
                            "outputDimensionality": DIM})
    if r.status_code != 200:
        raise RuntimeError(f"embedding {r.status_code}: {r.text[:120]}")
    return _unit(r.json()["embedding"]["values"])


def ngram(text, dim=512):
    """Offline fallback: hashed character 3-5-grams of the normalised text."""
    t = " " + re.sub(r"[^a-z0-9 ]", " ", text.lower()) + " "
    t = re.sub(r"\s+", " ", t)
    v = [0.0] * dim
    for n in (3, 4, 5):
        for i in range(len(t) - n + 1):
            v[int(hashlib.md5(t[i:i + n].encode()).hexdigest()[:8], 16) % dim] += 1.0
    return _unit(v)


def _unit(v):
    s = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / s, 4) for x in v]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def embed(text):
    """(kind, vector). kind tells which vectors are comparable."""
    if os.environ.get("GEMINI_API_KEY") and not C.OFFLINE:
        try:
            return "gem", _gemini(text)
        except Exception as e:
            log("Embedding failed, using the n-gram fallback:", str(e)[:120])
    return "ngram", ngram(text)


def check(premise, bank, threshold=None):
    """(ok, best_similarity, closest_premise, (kind, vector)). Compares with the newest 200 bank entries."""
    kind, vec = embed(premise)
    mine = ngram(premise)
    ok, best, closest, worst_margin = True, 0.0, "", -1.0
    for e in bank[-200:]:
        if e.get("kind") == kind and e.get("vec") and len(e["vec"]) == len(vec):
            s = cos(vec, e["vec"])
            thr = threshold if threshold is not None else (C.GEMINI_SIM_THRESHOLD if kind == "gem" else C.SIM_THRESHOLD)
        else:                                         # different vector kinds: compare texts the offline way
            s = cos(mine, ngram(e.get("premise", "")))
            thr = threshold if threshold is not None else C.SIM_THRESHOLD
        if s - thr > worst_margin:                    # the closest call relative to its own scale
            worst_margin, best, closest = s - thr, s, e.get("premise", "")
        if s > thr:
            ok = False
    return ok, best, closest, (kind, vec)


def remember(bank, premise, title, emb):
    kind, vec = emb
    bank.append({"premise": premise, "title": title, "kind": kind, "vec": vec})
    return bank[-200:]
