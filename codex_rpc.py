#!/usr/bin/env python3
"""Minimal JSON-RPC client for the shared Codex app-server.

Path: `codex app-server proxy` pipes raw bytes to the control socket
(~/.codex/app-server-control/app-server-control.sock), which speaks WebSocket,
so this client does the handshake and frames each JSON-RPC message itself.
"""
import base64
import itertools
import json
import os
import queue
import struct
import subprocess
import threading

CODEX = os.environ.get("AGENT_RELAY_CODEX_EXE", "")


def find_codex():
    if CODEX:
        return CODEX
    root = os.path.expanduser(r"~\.vscode\extensions")
    exts = sorted((d for d in os.listdir(root) if d.startswith("openai.chatgpt-")),
                  key=lambda d: os.path.getmtime(os.path.join(root, d)), reverse=True)
    for d in exts:
        p = os.path.join(root, d, "bin", "windows-x86_64", "codex.exe")
        if os.path.exists(p):
            return p
    raise FileNotFoundError("no bundled codex.exe under the VS Code Codex extension")


class WSPipe:
    """WebSocket client framing over a child process's stdin/stdout."""

    def __init__(self, argv):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, bufsize=0, creationflags=flags)
        self.w = self.proc.stdin
        self.r = self.proc.stdout
        self.wlock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        self.w.write(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n" % key).encode())
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            c = self.r.read(1)
            if not c:
                raise ConnectionError("app-server socket closed during handshake "
                                      "(is the shared Codex server running?)")
            head += c
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError("handshake refused: %r" % head[:200])

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            c = self.r.read(n - len(buf))
            if not c:
                raise EOFError
            buf += c
        return buf

    def send(self, payload, opcode=0x1):
        data = payload.encode() if isinstance(payload, str) else payload
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.wlock:
            self.w.write(head + mask + body)
            self.w.flush()

    def recv(self):
        """Return one complete text message, or None when the socket closes."""
        parts = []
        while True:
            try:
                b1, b2 = self._read(2)
            except EOFError:
                return None
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x8:
                return None
            if op == 0x9:
                self.send(data, 0xA)
                continue
            if op == 0xA:
                continue
            parts.append(data)
            if fin:
                return b"".join(parts).decode("utf-8")

    def close(self):
        try:
            self.send(b"", 0x8)
            self.w.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


class CodexRPC:
    def __init__(self, name="agent-relay", ws=None):
        self.ws = ws or WSPipe([find_codex(), "app-server", "proxy"])
        self.ids = itertools.count(1)
        self.pending = {}
        self.events = queue.Queue()  # server notifications and server requests
        threading.Thread(target=self._reader, daemon=True).start()
        self.call("initialize", {"clientInfo": {"name": name, "version": "0.1"},
                                 "capabilities": {"experimentalApi": True}})
        self.notify("initialized")

    def _reader(self):
        while True:
            raw = self.ws.recv()
            if raw is None:
                break
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            if "id" in m and ("result" in m or "error" in m) and "method" not in m:
                q = self.pending.pop(m["id"], None)
                if q:
                    q.put(m)
            else:
                self.events.put(m)
        self.events.put({"method": "__closed__"})

    def call(self, method, params=None, timeout=30):
        mid = next(self.ids)
        q = queue.Queue()
        self.pending[mid] = q
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        m = q.get(timeout=timeout)
        if "error" in m:
            raise RuntimeError("%s failed: %s" % (method, m["error"]))
        return m.get("result")

    def notify(self, method, params=None):
        msg = {"method": method}
        if params is not None:
            msg["params"] = params
        self.ws.send(json.dumps(msg))

    def close(self):
        self.ws.close()
