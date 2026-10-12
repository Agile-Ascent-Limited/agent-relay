#!/usr/bin/env python3
"""agent-relay: push bus mail into the open chats the moment it arrives.

  bus (:5010) --poll 1 s--> relay --> Claude: the chat's inbox pipe
                                     --> Codex:  turn/start | turn/steer on the
                                                 shared app-server (the panel's)

No model in the loop and no tokens while idle. Targeting:
  - message.session matches a registered Claude chat / a Codex thread id -> that chat
  - untargeted -> the most recently active open chat of that agent
  - no open chat -> held in a local pending queue for up to 24 hours
State + log: %LOCALAPPDATA%\\agent-relay\\
"""
import glob
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

HOME = os.environ.get("AGENT_RELAY_STATE", os.path.join(os.environ["LOCALAPPDATA"], "agent-relay"))
REG = os.path.join(HOME, "sessions")
STATE = os.path.join(HOME, "state.json")
LEDGER = os.path.join(HOME, "delivered.json")
LOG = os.path.join(HOME, "relay.log")
BUS = os.environ.get("AGENT_RELAY_BUS", "http://127.0.0.1:5010")
TOKENS = os.environ.get("AGENT_RELAY_TOKENS", os.path.join(HERE, "bus", "tokens-by-agent.json"))
POLL = float(os.environ.get("AGENT_RELAY_POLL_SECS", "1"))
BACKLOG_SECS = 600      # on restart, never replay mail older than this
BURST = 30              # max pushes per target per 10 min (loop brake; excess is queued)
PENDING_SECS = 86400    # retry queued mail for up to 24 hours
PRESENCE_SECS = 30      # how often the relay tells the bus which chats are open
MARK = "[agent-relay]"


def log(s):
    t = time.time()
    line = "%s.%03d %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)),
                             int(t % 1 * 1000), s)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def bus_get(path, tok):
    req = urllib.request.Request(BUS + path, headers={"Authorization": "Bearer " + tok})
    return json.load(urllib.request.urlopen(req, timeout=8))


def bus_post(path, data, tok):
    req = urllib.request.Request(BUS + path, method="POST", data=json.dumps(data).encode(),
                                 headers={"Authorization": "Bearer " + tok,
                                          "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=8))


def how_to_answer(agent, m):
    """Self-contained reply instructions, so a zero-context chat can answer."""
    if agent == "codex":
        return ("use the agent-relay MCP tool bus_send (to=%s, kind=answer, thread_id=%s)"
                % (m["sender"], m["id"]))
    return ("POST %s/msg {to:\"%s\", kind:\"answer\", thread_id:%s, subject, body, "
            "from_session:\"<your session id>\"} with `Authorization: Bearer <tok>`, "
            "tok = json.load(open(r\"%s\"))[\"claude\"]" % (BUS, m["sender"], m["id"], TOKENS))


def render(m, agent="claude"):
    head = "%s Bus message #%s from %s -> %s (%s, topic %s): %s" % (
        MARK, m["id"], m["sender"], m["target"], m["kind"], m.get("topic") or "general",
        m["subject"])
    if m.get("thread_id"):
        head += " [reply in thread #%s]" % m["thread_id"]
    if m["kind"] in ("answer", "info"):
        tail = ("No reply unless this needs action from you (if it does: %s). Never send "
                "acknowledgements (thanks/noted/confirmed); they only wake the other agent "
                "for nothing." % how_to_answer(agent, m))
    else:
        tail = ("Answer on the bus: %s. Do not reply to that answer's acknowledgement."
                % how_to_answer(agent, m))
    tail += ("\nTHE OPERATOR ONLY SEES YOUR FINAL MESSAGE (tool calls and 'Worked for' steps are "
             "hidden): start it with `📨 Bus #%s from %s:` + a one-line gist, and paste any "
             "bus message you sent in full as `📤 Bus #<id> to <to>: <body>`."
             % (m["id"], m["sender"]))
    return "%s\n\n%s\n\n%s" % (head, m["body"], tail)


def chat_label(text, key):
    """Readable, unique chat label (the bus merges same-agent sessions sharing a label)."""
    text = " ".join((text or "").split())[:50] or "chat"
    return "%s · %s" % (text, key[:8])


# ---- Claude: session inbox pipes --------------------------------------------

def claude_sessions():
    out = []
    for p in glob.glob(os.path.join(REG, "*.json")):
        rec = load(p, None)
        if rec and rec.get("socket") and rec.get("token"):
            rec["_path"] = p
            out.append(rec)
    return out


def pipe_post(rec, text):
    with open(rec["socket"], "r+b", buffering=0) as pipe:
        pipe.write((json.dumps({"type": "auth", "token": rec["token"]}) + "\n").encode())
        pipe.write((json.dumps({"type": "user", "message": {
            "role": "user", "content": text}}) + "\n").encode())


def deliver_claude(m, text, want=None):
    live = claude_sessions()
    want = m.get("session") or want
    if want:
        cands = [r for r in live if r["session_id"] == want]
    else:
        cands = sorted(live, key=lambda r: r.get("active", 0), reverse=True)
    for rec in cands:
        try:
            pipe_post(rec, text)
            return {"skey": rec["session_id"], "note": "",
                    "label": chat_label(rec.get("label") or "Claude chat", rec["session_id"])}
        except OSError as e:  # chat closed: its pipe is gone
            log("claude chat %s unreachable (%s); unregistering" % (rec["session_id"][:8], e))
            try:
                os.remove(rec["_path"])
            except OSError:
                pass
            if want:
                break
    return None


def asker_session(m, tok):
    """The Claude chat that posted m: the bus stamps session.last_sent with the post
    time when the sender includes from_session, but stores no sender session on m."""
    best, gap = None, 3.0
    for s in bus_get("/sessions", tok).get("sessions", []):
        d = abs((s.get("last_sent") or 0) - m["ts"])
        if s.get("agent") == m["sender"] and d < gap:
            best, gap = s["skey"], d
    return best


# ---- Codex: shared app-server -------------------------------------------------

_codex = None


def codex():
    global _codex
    if _codex is None or _codex.ws.proc.poll() is not None:
        from codex_bridge import connect  # starts the shared server if it is down
        from codex_rpc import CodexRPC
        _codex = CodexRPC("agent-relay", ws=connect())
    while not _codex.events.empty():  # we only need replies, not the event stream
        _codex.events.get_nowait()
    return _codex


def codex_threads(rpc):
    """Open Codex chats: threads loaded on the shared server (no subagents, no ephemerals)."""
    out = []
    for tid in rpc.call("thread/loaded/list", {}).get("data", []):
        t = rpc.call("thread/read", {"threadId": tid, "includeTurns": False})["thread"]
        if not (t.get("ephemeral") or t.get("parentThreadId")):
            out.append(t)
    return out


def codex_label(t):
    return chat_label(t.get("name") or t.get("preview") or "Codex chat", t["id"])


def deliver_codex(m, text):
    rpc = codex()
    threads = codex_threads(rpc)
    if m.get("session"):
        threads = [t for t in threads if t["id"] == m["session"]]
    threads.sort(key=lambda t: t.get("recencyAt") or t.get("updatedAt") or 0, reverse=True)
    if not threads:
        return None
    t = threads[0]
    busy = (t.get("status") or {}).get("type") == "active"
    # turn/start steers the running turn when one is active in the original tested integration.
    # Never read turns here: thread/read(includeTurns) and thread/resume block
    # until an active turn ends.
    rpc.call("turn/start", {"threadId": t["id"],
                            "input": [{"type": "text", "text": text}]})
    return {"skey": t["id"], "label": codex_label(t),
            "note": " (steered running turn)" if busy else ""}


def safe_presence(tok):
    global _codex
    try:
        publish_presence(tok)
    except Exception as e:
        log("presence update failed: %s" % e)
        _codex = None  # reconnect to the shared Codex server next time


def publish_presence(tok):
    """Tell the bus which chats are open right now (the website's 'To:' picker reads it)."""
    for rec in claude_sessions():
        if os.path.exists(rec["socket"]):
            bus_post("/session", {"agent": "claude", "skey": rec["session_id"], "label":
                                  chat_label(rec.get("label") or "Claude chat", rec["session_id"])}, tok)
    for t in codex_threads(codex()):
        bus_post("/session", {"agent": "codex", "skey": t["id"], "label": codex_label(t)}, tok)


# ---- main loop ------------------------------------------------------------------

def main():
    os.makedirs(REG, exist_ok=True)
    toks = load(TOKENS, {})
    tok = toks["claude"]
    state = load(STATE, {})
    ledger = load(LEDGER, {})
    recent = {"claude": [], "codex": []}
    while True:  # the bus can blip; never die at startup
        try:
            feed = bus_get("/messages?limit=1", tok)["messages"]
            break
        except Exception as e:
            log("startup: bus unreachable (%s); retrying" % e)
            time.sleep(5)
    top = feed[0]["id"] if feed else 0
    hwm = state.get("hwm", top)
    log("relay up: bus %s, resuming after #%s (bus top #%s)" % (BUS, hwm, top))
    def push(agent, m, reply_to):
        """Deliver m to agent now. True = delivered; False = keep it pending."""
        global _codex
        now = time.time()
        recent[agent] = [t for t in recent[agent] if now - t < 600]
        if len(recent[agent]) >= BURST:
            return False  # loop brake: held, retried once the window frees up
        try:
            text = render(m, agent)
            if agent == "claude":
                where = deliver_claude(m, text, reply_to)
            else:
                where = deliver_codex(m, text)
        except Exception as e:
            where = None
            log("#%s -> %s failed: %s" % (m["id"], agent, e))
            if agent == "codex":
                _codex = None
        if not where:
            return False
        recent[agent].append(now)
        addressed = m.get("session") or (agent == "claude" and reply_to)
        # untargeted mail stays unread so other chats can still see it on the bus
        if addressed and not m.get("ack_required") and toks.get(agent):
            try:  # info mail: delivered = seen, as the hooks do
                bus_post("/ack", {"agent": agent, "ids": [m["id"]]}, toks[agent])
            except Exception as e:
                log("#%s seen-mark failed: %s" % (m["id"], e))
        ledger.setdefault(str(m["id"]), []).append(where["skey"])
        log("#%s %s->%s pushed into %s%s" % (m["id"], m["sender"], agent, where["label"],
                                             where["note"]))
        try:  # receipt for the website: which chat got it, when
            bus_post("/delivered", {"msg_id": m["id"], "agent": agent, "skey": where["skey"],
                                    "label": where["label"] + where["note"]}, tok)
        except Exception as e:
            log("#%s receipt failed: %s" % (m["id"], e))
        return True

    pending = state.get("pending", [])  # [{agent, m, reply_to}] awaiting an open chat
    last_presence = 0.0
    while True:
        if time.time() - last_presence > PRESENCE_SECS:
            last_presence = time.time()
            safe_presence(tok)
        try:
            # 1. held mail: push the moment a chat is open (kept up to PENDING_SECS)
            keep = []
            for p in pending:
                if time.time() - p["m"]["ts"] > PENDING_SECS:
                    log("#%s -> %s dropped from queue after %d min with no open chat"
                        % (p["m"]["id"], p["agent"], PENDING_SECS // 60))
                elif not push(p["agent"], p["m"], p.get("reply_to")):
                    keep.append(p)
            if len(keep) != len(pending):
                pending = keep
                state["pending"] = pending
                save(STATE, state)
            # 2. new mail
            rows = bus_get("/messages?limit=50", tok)["messages"]
            for m in sorted((r for r in rows if r["id"] > hwm), key=lambda r: r["id"]):
                hwm = m["id"]
                if time.time() - m["ts"] > BACKLOG_SECS or m["kind"] == "ack-note":
                    continue
                if m["sender"] == "claude":  # remember which chat asked, for replies
                    try:
                        who = asker_session(m, tok)
                        if who:
                            askers = state.setdefault("askers", {})
                            askers[str(m["id"])] = who
                            for k in sorted(askers, key=int)[:-300]:
                                askers.pop(k)
                    except Exception as e:
                        log("#%s asker lookup failed: %s" % (m["id"], e))
                reply_to = state.get("askers", {}).get(str(m.get("thread_id")))
                targets = {"claude": ["claude"], "codex": ["codex"],
                           "all": ["claude", "codex"]}.get(m["target"], [])
                for agent in targets:
                    if agent != m["sender"] and not push(agent, m, reply_to):
                        log("#%s -> %s: no open chat yet; queued" % (m["id"], agent))
                        pending.append({"agent": agent, "m": m, "reply_to": reply_to})
                state["hwm"] = hwm
                state["pending"] = pending
                save(STATE, state)
                if len(ledger) > 500:
                    for k in sorted(ledger, key=int)[:-300]:
                        ledger.pop(k)
                save(LEDGER, ledger)
        except Exception as e:
            log("poll error: %s" % e)
            time.sleep(5)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
