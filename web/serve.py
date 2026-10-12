#!/usr/bin/env python3
"""Agent Relay website: read the agent conversations and send the operator's messages into a chosen chat.

Loopback only (127.0.0.1:5031), use an authenticated reverse proxy for remote access. It talks to the Agent Relay bus
API on 127.0.0.1:5010 with the operator's bus token (systemd credential `bus-token`); it never opens
the bus database. Reading: GET /api/state. Sending: POST /api/send, which only accepts a chat
the relay currently reports as open (or "any open chat" of that agent).
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
BUS = os.environ.get("AGENT_RELAY_BUS_URL", "http://127.0.0.1:5010")
PORT = int(os.environ.get("AGENT_RELAY_VIEW_PORT", "5031"))
TOKEN_FILE = os.environ.get("AGENT_RELAY_VIEW_TOKEN_FILE") or os.path.join(
    os.environ.get("CREDENTIALS_DIRECTORY", "/nonexistent"), "bus-token")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
HOSTS = set(os.environ.get("AGENT_RELAY_VIEW_HOSTS", "127.0.0.1,localhost").split(","))  # anything else: 421 (DNS rebinding)
AGENTS = {"claude", "codex"}
KINDS = {"question", "directive", "info"}
FRESH_SECS = 90          # the relay republishes open chats every 30 s
MAX_BODY = 8000
MAX_REQUEST = 32 * 1024
CHAT_KEY = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,}$")
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                               "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
RECENT = 50              # newest messages re-read every refresh (catches new ACKs)
FULL_EVERY = 120         # seconds between full re-reads of the last FULL messages
FULL = 400
# In-memory copy of the conversation, refreshed from the bus every 2 s by one background
# thread; a full 400-message read costs seconds on the Pi, a 50-message read is instant.
STORE = {"msgs": {}, "dels": [], "sessions": [], "at": 0.0, "full_at": 0.0}
_lock = threading.Lock()


def token():
    return Path(TOKEN_FILE).read_text().strip()


def bus(path, data=None):
    req = urllib.request.Request(
        BUS + path, method="POST" if data is not None else "GET",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.load(err)
        except ValueError:
            return err.code, {"error": "bus HTTP %d" % err.code}


def open_chats(sessions, now):
    """Chats the relay reported open in the last FRESH_SECS (Claude sessions, Codex threads)."""
    out = []
    for s in sessions:
        if (s.get("agent") in AGENTS and CHAT_KEY.match(s.get("skey") or "")
                and now - float(s.get("last_seen") or 0) < FRESH_SECS):
            out.append({"agent": s["agent"], "skey": s["skey"], "label": s.get("label") or s["skey"][:8],
                        "last_seen": s.get("last_seen")})
    return sorted(out, key=lambda c: (c["agent"], c["label"].lower()))


def refresh():
    """Pull recent bus state into STORE (a full re-read every FULL_EVERY seconds)."""
    full = time.time() - STORE["full_at"] > FULL_EVERY
    code1, msgs = bus("/messages?limit=%d" % (FULL if full else RECENT))
    code2, dels = bus("/deliveries?limit=%d" % FULL)
    code3, sess = bus("/sessions")
    if code1 != 200 or code2 != 200 or code3 != 200:
        raise RuntimeError("bus unavailable")
    with _lock:
        for m in msgs.get("messages", []):
            STORE["msgs"][m["id"]] = m
        if len(STORE["msgs"]) > 2 * FULL:
            for mid in sorted(STORE["msgs"])[:-FULL]:
                del STORE["msgs"][mid]
        STORE["dels"] = dels.get("deliveries", [])
        STORE["sessions"] = sess.get("sessions", [])
        STORE["at"] = time.time()
        if full:
            STORE["full_at"] = STORE["at"]


def state(since=0):
    """Messages newer than `since`, plus the newest RECENT (their ACK state may change)."""
    if time.time() - STORE["at"] > 5:  # background thread absent or stalled: refresh inline
        refresh()
    with _lock:
        ids = sorted(STORE["msgs"])
        keep = set(ids[-RECENT:]) | {i for i in ids if i > since}
        msgs = [STORE["msgs"][i] for i in ids if i in keep] if since else [STORE["msgs"][i] for i in ids]
        now = time.time()
        return {"now": now, "messages": msgs, "deliveries": list(STORE["dels"]),
                "chats": open_chats(STORE["sessions"], now)}


def refresher():
    while True:
        try:
            refresh()
        except (RuntimeError, OSError, ValueError):
            pass
        time.sleep(2)


def validate_send(payload, chats):
    """Return the bus /msg body for a web send, or raise ValueError with a readable reason."""
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    to = payload.get("to")
    if to not in AGENTS:
        raise ValueError("choose Claude or Codex")
    kind = payload.get("kind") or "question"
    if kind not in KINDS:
        raise ValueError("kind must be question, directive or info")
    body = (payload.get("body") or "").strip()
    if not body:
        raise ValueError("message is empty")
    if len(body) > MAX_BODY:
        raise ValueError("message is longer than %d characters" % MAX_BODY)
    subject = " ".join((payload.get("subject") or body.splitlines()[0]).split())[:140]
    session = (payload.get("session") or "").strip()
    if session and not any(c["agent"] == to and c["skey"] == session for c in chats):
        raise ValueError("that chat is no longer open; pick another")
    msg = {"to": to, "kind": kind, "topic": "web", "subject": subject, "body": body,
           "from_session": "human-web"}
    if session:
        msg["session"] = session
    thread_id = payload.get("thread_id")
    if thread_id not in (None, "", 0):
        if not isinstance(thread_id, int) or thread_id < 1:
            raise ValueError("bad thread id")
        msg["thread_id"] = thread_id
    return msg


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentRelayView/1.0"

    def log_message(self, *args):
        pass

    def _headers(self, code, ctype, length, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self._headers(code, "application/json", len(data))
        self.wfile.write(data)

    def _host_ok(self):
        if (self.headers.get("Host") or "").split(":")[0].lower() in HOSTS:
            return True
        self._json(421, {"error": "unknown host"})
        return False

    def do_GET(self):
        if not self._host_ok():
            return
        path = urlparse(self.path).path
        if path in STATIC:
            name, ctype = STATIC[path]
            data = (HERE / name).read_bytes()
            self._headers(200, ctype, len(data), "no-cache")
            self.wfile.write(data)
        elif path == "/robots.txt":
            data = b"User-agent: *\nDisallow: /\n"
            self._headers(200, "text/plain", len(data))
            self.wfile.write(data)
        elif path == "/api/state":
            try:
                since = int((parse_qs(urlparse(self.path).query).get("since") or ["0"])[0])
                self._json(200, state(max(0, since)))
            except (RuntimeError, OSError, ValueError):
                self._json(503, {"error": "Agent Relay bus unavailable"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return
        if urlparse(self.path).path != "/api/send":
            return self._json(404, {"error": "not found"})
        origin = self.headers.get("Origin")
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if origin and (urlparse(origin).hostname or "").lower() != host:
            return self._json(403, {"error": "cross-origin request refused"})
        if self.headers.get("X-Agent-Relay") != "send":
            return self._json(403, {"error": "missing page header; reload the page"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(415, {"error": "JSON only"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_REQUEST:
            return self._json(413, {"error": "request too large"})
        try:
            payload = json.loads(self.rfile.read(length))
            live = state()
            msg = validate_send(payload, live["chats"])
        except (ValueError, RuntimeError, OSError) as err:
            return self._json(400, {"error": str(err)})
        code, result = bus("/msg", msg)
        try:
            refresh()  # the new message shows on the page's next poll
        except (RuntimeError, OSError, ValueError):
            pass
        self._json(code if code in (200, 400, 422) else 502, result)


if __name__ == "__main__":
    threading.Thread(target=refresher, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
