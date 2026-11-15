"""The post queue: one storage file per scheduled post, queue/<slot key>.json.

An item: {id, platform, post_at, video_url, cover_url, caption | yt_meta, title, story meta..., approval
(pending | approved | skipped | redo | auto), tg_msg, created_ts, attempts, status, done}.
Separate files mean two posts can never overwrite each other, and each has its own claim lock."""
import json, time, uuid
import storage
from util import log

PREFIX = "queue"
CLAIM_TTL = 20 * 60


def _key(item_id):
    return f"{PREFIX}/{item_id}.json"


def put(item):
    storage.put_json(_key(item["id"]), item)


def get(item_id):
    return storage.get_json(_key(item_id), None)


def delete(item_id):
    storage.delete(_key(item_id))


def ids():
    try:
        return [n[:-5] for n in storage.list_names(PREFIX) if n.endswith(".json")]
    except Exception as e:
        log("Queue listing failed:", e)
        return []


def items():
    out = []
    for i in ids():
        it = get(i)
        if it:
            out.append(it)
    return sorted(out, key=lambda x: x.get("post_at", 0))


def update(item_id, **fields):
    it = get(item_id)
    if not it:
        return None
    it.update(fields)
    put(it)
    return it


def claim(item_id, sleep=5):
    """Optimistic lock so two overlapping jobs can't both publish the same item. Returns the item or None."""
    cur = get(item_id)
    if not cur:
        return None
    if cur.get("status") == "publishing" and time.time() - cur.get("claim_ts", 0) < CLAIM_TTL:
        log(f"{item_id}: another job is publishing it right now.")
        return None
    me = uuid.uuid4().hex
    cur.update(status="publishing", claim=me, claim_ts=int(time.time()))
    put(cur)
    time.sleep(sleep)
    back = get(item_id)
    if not back or back.get("claim") != me:
        log(f"{item_id}: lost the publish lock to another job.")
        return None
    return back


def release(item, **fields):
    item.update(fields)
    item["status"] = "queued"
    item.pop("claim", None)
    put(item)
