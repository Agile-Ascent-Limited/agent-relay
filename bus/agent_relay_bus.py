#!/usr/bin/env python3
"""Agent Relay bus — durable async message store for the agent fleet.

Agent Relay (Windows, agent-relay/relay.py) pushes new mail from here into the open
Claude/Codex chats; this server only stores, authenticates, filters and mirrors.

Zero-dependency (stdlib only). SQLite store, HTTP JSON API, per-agent
bearer tokens, Telegram mirror, content filter.

Agents: claude, codex, human, watcher (plus 'all' broadcast target). 'watcher' = external
scripts (e.g. the transfer watcher); it only sends, nothing reads its inbox.

API:
  POST /msg    {from, to, kind, subject, body, thread_id?, ack_required?}
  GET  /inbox/<agent>?unread=1&limit=50
  POST /ack    {agent, ids: [..]}         (marks seen; directives need explicit ack)
  GET  /messages?limit=100                (viewer feed, newest first)
  GET  /thread/<id>
  GET  /health

Auth: Authorization: Bearer <token>; tokens in tokens.json next to this file.
"""
import json
import os
import sqlite3
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("AGENT_RELAY_BUS_DB", os.path.join(BASE, "agent-relay-bus.sqlite3"))
TOKENS_FILE = os.environ.get("AGENT_RELAY_BUS_TOKENS", os.path.join(BASE, "tokens.json"))
PORT = int(os.environ.get("AGENT_RELAY_BUS_PORT", "5010"))
HOST = os.environ.get("AGENT_RELAY_BUS_HOST", "127.0.0.1")
NOTIFY = os.environ.get("AGENT_RELAY_BUS_NOTIFY", "")

AGENTS = set(os.environ.get("AGENT_RELAY_AGENTS", "claude,codex,human,watcher").split(","))
KINDS = {"directive", "question", "info", "answer", "ack-note"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS msg (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  sender TEXT NOT NULL,
  target TEXT NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  thread_id INTEGER,
  ack_required INTEGER NOT NULL DEFAULT 0,
  topic TEXT NOT NULL DEFAULT 'general'
);
CREATE TABLE IF NOT EXISTS seen (
  msg_id INTEGER NOT NULL,
  agent TEXT NOT NULL,
  ts REAL NOT NULL,
  PRIMARY KEY (msg_id, agent)
);
CREATE TABLE IF NOT EXISTS session (
  agent TEXT NOT NULL,
  skey TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  last_seen REAL NOT NULL,
  PRIMARY KEY (agent, skey)
);
CREATE TABLE IF NOT EXISTS pair (
  a TEXT NOT NULL,
  b TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  ts REAL NOT NULL,
  PRIMARY KEY (a, b)
);
CREATE INDEX IF NOT EXISTS idx_msg_target ON msg(target, id);
CREATE TABLE IF NOT EXISTS delivery (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  msg_id INTEGER NOT NULL,
  agent TEXT NOT NULL,
  skey TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '',
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delivery_msg ON delivery(msg_id);
"""


def db():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for ddl in (
        "ALTER TABLE msg ADD COLUMN topic TEXT NOT NULL DEFAULT 'general'",
        "ALTER TABLE msg ADD COLUMN session TEXT",
        "ALTER TABLE msg ADD COLUMN claimed_by TEXT",
        "ALTER TABLE msg ADD COLUMN claimed_until REAL",
        "ALTER TABLE session ADD COLUMN last_sent REAL NOT NULL DEFAULT 0",
    ):
        try:
            con.execute(ddl)
            con.commit()
        except sqlite3.OperationalError:
            pass
    return con


def load_tokens():
    try:
        with open(TOKENS_FILE) as f:
            return json.load(f)  # {token: agent}
    except OSError:
        return {}


def mirror_to_telegram(m):
    if not os.path.exists(NOTIFY):
        return
    text = "BUS %s->%s [%s] %s: %s" % (
        m["sender"], m["target"], m["kind"], m["subject"], m["body"][:400])
    try:
        subprocess.Popen([NOTIFY, text],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def partner_of(con, skey):
    """Return (partner_skey, partner_agent, partner_label) or None."""
    r = con.execute("SELECT a,b FROM pair WHERE a=? OR b=?",
                    (skey, skey)).fetchone()
    if not r:
        return None
    other = r["b"] if r["a"] == skey else r["a"]
    s = con.execute("SELECT agent,label FROM session WHERE skey=?",
                    (other,)).fetchone()
    return (other, s["agent"] if s else "?", s["label"] if s else other)


def row_out(r, con=None, agent=None):
    d = {k: r[k] for k in r.keys()}
    if con is not None and agent:
        s = con.execute("SELECT 1 FROM seen WHERE msg_id=? AND agent=?",
                        (r["id"], agent)).fetchone()
        d["seen"] = bool(s)
    return d


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentRelayBus/1.0"

    def _agent(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return load_tokens().get(auth[7:].strip())
        return None

    def _send(self, code, obj):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass

    def do_GET(self):
        agent = self._agent()
        if not agent:
            return self._send(401, {"error": "bad token"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        con = db()
        try:
            if u.path == "/health":
                n = con.execute("SELECT COUNT(*) c FROM msg").fetchone()["c"]
                return self._send(200, {"ok": True, "messages": n})
            if u.path.startswith("/inbox/"):
                who = u.path.split("/")[2]
                if who not in AGENTS:
                    return self._send(404, {"error": "unknown agent"})
                limit = int(q.get("limit", ["50"])[0])
                unread = q.get("unread", ["1"])[0] == "1"
                sql = ("SELECT m.* FROM msg m WHERE (m.target=? OR m.target='all') "
                       "AND m.sender != ?")
                topic = q.get("topic", [None])[0]
                skey = q.get("session", [None])[0]
                args_extra = []
                if topic:
                    sql += " AND m.topic=?"
                    args_extra.append(topic)
                # stale-DM fallback: a DM whose target identity has not pulled
                # since the message arrived (15-min grace) is delivered
                # agent-wide so it can never sit in a dead mailbox forever.
                stale = ("(m.ts < ? AND NOT EXISTS (SELECT 1 FROM session sx "
                         "WHERE sx.skey = m.session AND sx.last_seen >= m.ts))")
                now = time.time()
                if skey:
                    sql += (" AND (m.session IS NULL OR m.session=? OR "
                            + stale + ")")
                    args_extra += [skey, now - 900]
                    con.execute("INSERT INTO session (agent,skey,last_seen) "
                                "VALUES (?,?,?) ON CONFLICT(agent,skey) DO "
                                "UPDATE SET last_seen=excluded.last_seen",
                                (who, skey, now))
                    con.commit()
                else:
                    sql += " AND (m.session IS NULL OR " + stale + ")"
                    args_extra.append(now - 900)
                if unread:
                    sql += (" AND NOT EXISTS (SELECT 1 FROM seen s WHERE "
                            "s.msg_id=m.id AND s.agent=?)")
                    rows = con.execute(sql + " ORDER BY m.id LIMIT ?",
                                       (who, who, *args_extra, who, limit)).fetchall()
                else:
                    rows = con.execute(sql + " ORDER BY m.id DESC LIMIT ?",
                                       (who, who, *args_extra, limit)).fetchall()
                out_rows = []
                for r in rows:
                    d = row_out(r)
                    if d.get("session") and d["session"] != (skey or ""):
                        d["stale_dm"] = ("originally a DM to dead session %s; "
                                         "re-routed agent-wide" % d["session"])
                    if con.execute("SELECT 1 FROM msg WHERE thread_id=? "
                                   "LIMIT 1", (d["id"],)).fetchone():
                        d["has_reply"] = True
                    out_rows.append(d)
                out = {"agent": who, "messages": out_rows}
                if skey:
                    p = partner_of(con, skey)
                    if p:
                        out["partner"] = {"skey": p[0], "agent": p[1],
                                          "label": p[2]}
                return self._send(200, out)
            if u.path == "/sessions":
                cutoff = time.time() - 86400
                rows = con.execute(
                    "SELECT * FROM session WHERE last_seen > ? "
                    "ORDER BY last_seen DESC", (cutoff,)).fetchall()
                return self._send(200, {"sessions": [row_out(r) for r in rows]})
            if u.path == "/pairs":
                rows = con.execute("SELECT * FROM pair ORDER BY ts DESC").fetchall()
                out = []
                for r in rows:
                    d = {"a": r["a"], "b": r["b"], "label": r["label"]}
                    for side in ("a", "b"):
                        s = con.execute("SELECT agent,label FROM session "
                                        "WHERE skey=?", (d[side],)).fetchone()
                        d[side + "_info"] = ({"agent": s["agent"],
                                              "label": s["label"]} if s else {})
                    out.append(d)
                return self._send(200, {"pairs": out})
            if u.path == "/messages":
                limit = int(q.get("limit", ["100"])[0])
                rows = con.execute("SELECT * FROM msg ORDER BY id DESC LIMIT ?",
                                   (limit,)).fetchall()
                out = []
                for r in rows:
                    d = row_out(r)
                    d["seen_by"] = [s["agent"] for s in con.execute(
                        "SELECT agent FROM seen WHERE msg_id=?", (r["id"],))]
                    out.append(d)
                return self._send(200, {"messages": out})
            if u.path.startswith("/thread/"):
                tid = int(u.path.split("/")[2])
                rows = con.execute(
                    "SELECT * FROM msg WHERE id=? OR thread_id=? ORDER BY id",
                    (tid, tid)).fetchall()
                return self._send(200, {"messages": [row_out(r) for r in rows]})
            if u.path == "/deliveries":
                # Agent Relay receipts: which chat each message was pushed into, when
                limit = min(int(q.get("limit", ["400"])[0]), 2000)
                rows = con.execute("SELECT * FROM delivery ORDER BY id DESC LIMIT ?",
                                   (limit,)).fetchall()
                return self._send(200, {"deliveries": [row_out(r) for r in rows]})
            return self._send(404, {"error": "not found"})
        finally:
            con.close()

    def do_POST(self):
        agent = self._agent()
        if not agent:
            return self._send(401, {"error": "bad token"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                return self._send(413, {"error": "body must be 1..65536 bytes"})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                return self._send(400, {"error": "expected JSON object"})
        except (ValueError, TypeError):
            return self._send(400, {"error": "bad json"})
        con = db()
        try:
            if self.path == "/msg":
                target = body.get("to", "")
                kind = body.get("kind", "info")
                subject = (body.get("subject") or "").strip()
                text = (body.get("body") or "").strip()
                if target not in AGENTS and target != "all":
                    return self._send(400, {"error": "bad target"})
                if kind not in KINDS:
                    return self._send(400, {"error": "bad kind"})
                if not subject or not text:
                    return self._send(400, {"error": "subject and body required"})
                ack = 1 if (body.get("ack_required") or kind == "directive") else 0
                topic = (body.get("topic") or "general").strip().lower()[:40]
                skey = (body.get("session") or "").strip()[:80] or None
                if not skey and body.get("from_session"):
                    # paired-session auto-routing: default to the sender
                    # session's partner when it belongs to the target agent
                    p = partner_of(con, body["from_session"].strip()[:80])
                    if p and p[1] == target:
                        skey = p[0]
                cur = con.execute(
                    "INSERT INTO msg (ts,sender,target,kind,subject,body,"
                    "thread_id,ack_required,topic,session) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), agent, target, kind, subject, text,
                     body.get("thread_id"), ack, topic, skey))
                if body.get("from_session"):
                    con.execute("UPDATE session SET last_sent=? WHERE skey=?",
                                (time.time(), body["from_session"].strip()[:80]))
                con.commit()
                m = {"id": cur.lastrowid, "sender": agent, "target": target,
                     "kind": kind, "subject": "[%s] %s" % (topic, subject),
                     "body": text}
                mirror_to_telegram(m)
                return self._send(200, {"ok": True, "id": cur.lastrowid})
            if self.path == "/delivered":
                # posted by Agent Relay after it pushes a message into a chat
                mid = int(body.get("msg_id") or 0)
                who = (body.get("agent") or "")[:20]
                if not mid or who not in AGENTS:
                    return self._send(400, {"error": "msg_id and agent required"})
                cur = con.execute(
                    "INSERT INTO delivery (msg_id,agent,skey,label,ts) VALUES (?,?,?,?,?)",
                    (mid, who, (body.get("skey") or "")[:80],
                     (body.get("label") or "")[:120], time.time()))
                con.commit()
                return self._send(200, {"ok": True, "id": cur.lastrowid})
            if self.path == "/session":
                skey = (body.get("skey") or "").strip()[:80]
                who = body.get("agent", agent)
                if not skey:
                    return self._send(400, {"error": "skey required"})
                label = (body.get("label") or "")[:120]
                if label:
                    # label continuity: migrate pairs + undelivered DMs from
                    # any staler same-agent session that held this label
                    for old in con.execute(
                            "SELECT skey FROM session WHERE agent=? AND "
                            "label=? AND skey != ?", (who, label, skey)):
                        o = old["skey"]
                        con.execute("UPDATE pair SET a=? WHERE a=?", (skey, o))
                        con.execute("UPDATE pair SET b=? WHERE b=?", (skey, o))
                        con.execute("UPDATE msg SET session=? WHERE session=?",
                                    (skey, o))
                        con.execute("DELETE FROM session WHERE skey=?", (o,))
                con.execute("INSERT INTO session (agent,skey,label,last_seen) "
                            "VALUES (?,?,?,?) ON CONFLICT(agent,skey) DO UPDATE "
                            "SET label=excluded.label, last_seen=excluded.last_seen",
                            (who, skey, (body.get("label") or "")[:120], time.time()))
                con.commit()
                return self._send(200, {"ok": True})
            if self.path == "/pair":
                a = (body.get("a") or "").strip()[:80]
                b = (body.get("b") or "").strip()[:80]
                if not a or not b or a == b:
                    return self._send(400, {"error": "need two distinct skeys"})
                a, b = sorted((a, b))
                con.execute("DELETE FROM pair WHERE a IN (?,?) OR b IN (?,?)",
                            (a, b, a, b))  # a session belongs to one pair
                con.execute("INSERT INTO pair (a,b,label,ts) VALUES (?,?,?,?)",
                            (a, b, (body.get("label") or "")[:120], time.time()))
                con.commit()
                return self._send(200, {"ok": True})
            if self.path == "/unpair":
                a, b = sorted(((body.get("a") or "")[:80], (body.get("b") or "")[:80]))
                con.execute("DELETE FROM pair WHERE a=? AND b=?", (a, b))
                con.commit()
                return self._send(200, {"ok": True})
            if self.path == "/claim":
                # leased work pickup: retries after expiry can execute work again
                ids = body.get("ids") or []
                worker = (body.get("worker") or agent)[:80]
                ttl = min(int(body.get("ttl_secs") or 1200), 7200)
                now = time.time()
                granted, held = [], {}
                for i in ids:
                    cur = con.execute(
                        "UPDATE msg SET claimed_by=?, claimed_until=? WHERE "
                        "id=? AND (claimed_by IS NULL OR claimed_until < ? "
                        "OR claimed_by = ?)",
                        (worker, now + ttl, int(i), now, worker))
                    if cur.rowcount:
                        granted.append(int(i))
                    else:
                        r = con.execute("SELECT claimed_by, claimed_until FROM "
                                        "msg WHERE id=?", (int(i),)).fetchone()
                        if r:
                            held[int(i)] = {"by": r["claimed_by"],
                                            "until": r["claimed_until"]}
                con.commit()
                return self._send(200, {"granted": granted,
                                        "already_claimed": held})
            if self.path == "/ack":
                ids = body.get("ids") or []
                who = body.get("agent", agent)
                now = time.time()
                acked, refused = [], {}
                for i in ids:
                    i = int(i)
                    m = con.execute("SELECT kind, sender FROM msg WHERE id=?",
                                    (i,)).fetchone()
                    if (m and m["kind"] in ("directive", "question")
                            and m["sender"] != who):
                        # ACK means COMPLETED: enforced, not advisory — a
                        # threaded reply from the acking agent must exist
                        reply = con.execute(
                            "SELECT 1 FROM msg WHERE thread_id=? AND sender=? "
                            "LIMIT 1", (i, who)).fetchone()
                        if not reply:
                            refused[i] = ("ACK refused: post your kind=answer "
                                          "reply with thread_id=%d FIRST, "
                                          "then ACK" % i)
                            continue
                    con.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?)",
                                (i, who, now))
                    con.execute("UPDATE msg SET claimed_until=? WHERE id=?",
                                (now, i))  # done => release claim
                    acked.append(i)
                con.commit()
                return self._send(200, {"ok": True, "acked": acked,
                                        "refused": refused})
            return self._send(404, {"error": "not found"})
        finally:
            con.close()


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
