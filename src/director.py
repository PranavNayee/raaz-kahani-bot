"""Human creative director (the owner) in Telegram - proof that a person directs the channel.

  propose  (10:00 IST, director.yml): 3 original premise options with buttons
           [1] [2] [3] / [🔁 New ideas] [✍️ My own idea]. After a pick the owner may reply with a note
           ("hero ek budhi naani ho") that steers the story.
  poll     (every 20 min, post.yml): reads button taps and messages: premise picks, own ideas, notes, and the
           video approval buttons [✅ Post] [🔁 Remake] [🗑 Skip] (plus /post /postnow /skip).
  take_choice (main.py): the next new story uses today's pick. No pick by production time -> the bot takes the
           best option itself and logs it as "auto".
Every human decision is appended to decisions.json (date, what, choice, note)."""
import datetime as dt, os, sys, time
import config as C, storage, notify, slots, llm, similarity, postqueue, story as S
from util import log

STATE, LOG = "director.json", "decisions.json"


def log_decision(kind, ref, choice, by="owner", note=""):
    d = storage.get_json(LOG, [])
    d.append({"ts": int(time.time()), "when": slots.fmt(time.time()), "kind": kind, "ref": ref,
              "choice": str(choice)[:300], "note": note[:500], "by": by})
    storage.put_json(LOG, d[-3000:])


# ---------------------------------------------------------------- premise options
OPTIONS = """You are the story editor of "{channel}", a Hindi animated mystery/fantasy story channel (13+ audience).
Propose 3 brand-new, completely ORIGINAL story ideas for today, each from a DIFFERENT theme:
{themes}
Each idea: a scroll-stopping Roman-Hinglish hook in the pattern "<impossible thing that happens regularly>! Lekin
<ordinary hero> ne <discovered/broke> <the secret>!", a one-sentence English premise (world, rule, antagonist, twist
idea) and the theme key. No copyrighted characters, films, brands, real people or religious figures; no gore.
Avoid anything like these past stories: {past}
Return ONLY JSON: {{"options": [{{"hook": "...", "premise": "...", "theme": "theme_key"}}]}}"""


def _bank():
    return storage.get_json("premises.json", [])


def make_options(history, n=3):
    past = "; ".join(h["premise"] for h in history if h.get("premise"))[-1500:] or "(none)"
    themes = "\n".join(f"- {k}: {v}" for k, v in S.THEMES.items())
    bank, out = _bank(), []
    for attempt in range(3):
        d = llm.generate_json(OPTIONS.format(channel=C.CHANNEL, themes=themes, past=past), temperature=1.0)
        for o in d.get("options") or []:
            if not (o.get("hook") and o.get("premise")) or len(out) >= n:
                continue
            ok, sim, _, _ = similarity.check(o["premise"], bank + [{"premise": x["premise"]} for x in out])
            if ok:
                o["theme"] = o.get("theme") if o.get("theme") in S.THEMES else S.pick_theme(history)
                out.append({k: o[k] for k in ("hook", "premise", "theme")})
            else:
                log(f"  option dropped (similarity {sim:.2f}): {o['premise'][:70]}")
        if len(out) >= n:
            break
    return out[:n]


def _today():
    return slots.now().date().isoformat()


def option_text(opts):
    em = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    return "\n\n".join(f"{em[i]} {o['hook']}\n    {o['premise']}" for i, o in enumerate(opts))


def propose(force=False):
    st = storage.get_json(STATE, {})
    day = _today()
    if st.get("date") == day and st.get("options") and not force:
        log("Today's premise options were already sent.")
        return st
    history = storage.get_json("history.json", [])
    opts = make_options(history)
    if not opts:
        raise RuntimeError("could not write premise options")
    msg = (f"🎬 Aaj ki kahani chuno ({day})\n\n{option_text(opts)}\n\n"
           "Tap a number to pick. 🔁 = 3 new ideas. ✍️ = send your own idea. After picking you can reply with a note "
           "to steer the story. No answer by the next video's production = I pick the best one myself.")
    buttons = [[(f"{i + 1}", f"rk:pick:{day}:{i}") for i in range(len(opts))],
               [("🔁 New ideas", f"rk:regen:{day}"), ("✍️ My own idea", f"rk:own:{day}")]]
    mid = notify.send(msg, buttons)
    st = {"date": day, "options": opts, "msg_id": mid, "status": "open", "choice": None, "note": "", "awaiting": None,
          "sent_ts": int(time.time())}
    storage.put_json(STATE, st)
    log(f"Sent {len(opts)} premise options to Telegram")
    return st


# ---------------------------------------------------------------- reading taps and messages
def approval_buttons(item_id):
    return [[("✅ Post", f"rk:ok:{item_id}"), ("🔁 Remake", f"rk:redo:{item_id}"), ("🗑 Skip", f"rk:skip:{item_id}")]]


def _set_approval(item_id, value, cb=None, how="button"):
    it = postqueue.get(item_id)
    if not it:
        if cb:
            notify.answer(cb, "That video is already posted or gone.")
        return False
    if it.get("done"):
        if cb:
            notify.answer(cb, "Already posted.")
        return False
    fields = {"approval": value, "approval_ts": int(time.time())}
    if value == "approved_now":
        fields.update(approval="approved", post_at=int(time.time()))
    postqueue.update(item_id, **fields)
    label = {"approved": "✅ Approved - posts at the slot", "approved_now": "🚀 Posting now", "skipped": "🗑 Skipped",
             "redo": "🔁 Remaking it with a new story"}[value]
    if cb:
        notify.answer(cb, label)
    notify.edit(it.get("tg_msg"), buttons=[])
    notify.send(f"{label}: {it.get('title', item_id)}")
    log_decision("video", item_id, value, note=how)
    return True


def _next_pending():
    pend = [i for i in postqueue.items() if not i.get("done") and i.get("approval") in (None, "pending")]
    return pend[0] if pend else None


def poll():
    """Processes new Telegram updates once. Returns the number handled."""
    if not (C.telegram_enabled() and C.TELEGRAM_COMMANDS):
        return 0
    tg = storage.get_json("telegram.json", {})
    ups = notify.updates(tg.get("offset"))
    if not ups:
        return 0
    st = storage.get_json(STATE, {})
    handled = 0
    for u in ups:
        tg["offset"] = u["update_id"] + 1
        cq = u.get("callback_query")
        if cq:
            data = cq.get("data") or ""
            parts = data.split(":")
            if parts[0] != "rk":
                continue
            kind = parts[1] if len(parts) > 1 else ""
            if kind in ("ok", "redo", "skip"):
                _set_approval(":".join(parts[2:]), {"ok": "approved", "redo": "redo", "skip": "skipped"}[kind], cq["id"])
            elif kind == "pick" and len(parts) == 4 and parts[2] == st.get("date"):
                i = int(parts[3])
                if st.get("status") == "used":
                    notify.answer(cq["id"], "Today's idea is already being made.")
                elif 0 <= i < len(st.get("options", [])):
                    st.update(status="picked", choice=dict(st["options"][i], id=f"{st['date']}#{i + 1}"),
                              awaiting="note")
                    notify.answer(cq["id"], f"Picked #{i + 1}")
                    notify.edit(st.get("msg_id"), f"✅ You picked #{i + 1}:\n{st['options'][i]['hook']}\n"
                                f"{st['options'][i]['premise']}", buttons=[])
                    notify.send("📝 Want to steer it? Reply with a note (e.g. 'hero ek budhi naani ho', 'end mein "
                                "emotional twist'). Or do nothing - the story will be made from this idea.")
                    log_decision("premise", st["date"], st["options"][i]["premise"])
            elif kind == "regen" and parts[2:] == [st.get("date")]:
                notify.answer(cq["id"], "Writing 3 new ideas...")
                notify.edit(st.get("msg_id"), "🔁 Replaced with new ideas below.", buttons=[])
                log_decision("premise", st.get("date"), "regenerate")
                st = propose(force=True)
            elif kind == "own" and parts[2:] == [st.get("date")]:
                st["awaiting"] = "idea"
                notify.answer(cq["id"], "Send your idea")
                notify.send("✍️ Send your story idea in one message (Hindi, Hinglish or English). Example: 'ek gaon "
                            "jahan har baarish mein log apni awaaz kho dete hain, aur ek gungi ladki raaz kholti hai'")
            handled += 1
            continue
        m = u.get("message") or {}
        text = (m.get("text") or "").strip()
        if not text:
            continue
        handled += 1
        cmd = text.lower().split("@")[0]
        if cmd in ("/post", "/approve", "/yes", "/postnow", "/now", "/skip", "/reject", "/no"):
            it = _next_pending()
            if not it:
                notify.send("Nothing is waiting for approval right now.")
                continue
            val = "approved_now" if cmd in ("/postnow", "/now") else "skipped" if cmd in ("/skip", "/reject", "/no") \
                else "approved"
            _set_approval(it["id"], val, how="command")
        elif cmd in ("/ideas", "/idea"):
            st = propose(force=True)
        elif text.startswith("/"):
            notify.send("Commands: /post /postnow /skip (next video), /ideas (3 new story ideas).")
        elif st.get("awaiting") == "idea" and st.get("date") == _today():
            st.update(status="picked", choice={"idea": text, "premise": text, "id": f"{st['date']}#own"},
                      awaiting="note")
            notify.send("✅ Got your idea - the next story will be made from it. Reply again to add a note, if you like.")
            log_decision("premise", st["date"], "own idea", note=text)
        elif st.get("status") == "picked" and st.get("date") == _today():
            st["note"] = (st.get("note", "") + " " + text).strip()[:500]
            notify.send("📝 Note added - the writer will follow it.")
            log_decision("note", st["date"], (st.get("choice") or {}).get("premise", ""), note=text)
        elif st.get("status") == "used":
            notify.send("Today's idea is already being made. Tomorrow's options come at 10:00.")
    storage.put_json("telegram.json", tg)
    storage.put_json(STATE, st)
    return handled


# ---------------------------------------------------------------- used by main.py
WAIT_HOURS = float(os.environ.get("DIRECTOR_WAIT_HOURS", "3") or 3)


def waiting():
    """True while today's options are open and the owner still has time to pick (sent < WAIT_HOURS ago)."""
    st = storage.get_json(STATE, {})
    return (st.get("date") == _today() and st.get("status") == "open" and
            time.time() - st.get("sent_ts", 0) < WAIT_HOURS * 3600)


def take_choice():
    """The owner's direction for the next new story (or the bot's own pick when the owner didn't answer).
    Returns a dict {premise, hook?, theme?, note?, idea?, id, by} or None (no options today)."""
    st = storage.get_json(STATE, {})
    if st.get("date") != _today() or st.get("status") == "used" or not st.get("options") and not st.get("choice"):
        return None
    if st.get("status") == "picked" and st.get("choice"):
        ch = dict(st["choice"], note=st.get("note", ""), by="owner")
    else:
        ch = dict(st["options"][0], id=f"{st['date']}#1", by="auto", note="")
        log_decision("premise", st["date"], ch["premise"], by="auto", note="owner did not answer in time")
        notify.edit(st.get("msg_id"), f"⏱️ No pick in time - I chose #1:\n{ch['hook']}", buttons=[])
    st.update(status="used", used_ts=int(time.time()))
    storage.put_json(STATE, st)
    return ch


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "poll"
    try:
        if what == "propose":
            hhmm = slots.now().strftime("%H:%M")
            if hhmm < C.DIRECTOR_TIME and os.environ.get("FORCE", "false") != "true":
                log(f"Too early ({hhmm} < {C.DIRECTOR_TIME})")
            else:
                propose(force=os.environ.get("FORCE", "false") == "true")
        log("Handled", poll(), "Telegram updates")
    except Exception as e:
        import traceback; traceback.print_exc()
        notify.text(f"❌ Creative director step '{what}' failed: {str(e)[:500]}")
        sys.exit(1)
