#!/usr/bin/env python3
"""Bridge the VS Code Codex panel's stdio (JSON lines) to the shared app-server.

Started by codex-shared.exe when the panel launches `codex app-server`. Starts
the shared server on the control socket if it is not running yet.
"""
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from codex_rpc import WSPipe, find_codex  # noqa: E402

LOG = os.path.join(os.environ.get("AGENT_RELAY_STATE", os.path.join(os.environ["LOCALAPPDATA"], "agent-relay")), "codex-bridge.log")


def note(s):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s [%d] %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), os.getpid(), s))
    except OSError:
        pass


def connect():
    codex = find_codex()
    try:
        return WSPipe([codex, "app-server", "proxy"])
    except ConnectionError:
        note("shared server down; starting it")
    # CREATE_NO_WINDOW gives the server a hidden console that its children (MCP tools,
    # node, python) share. DETACHED_PROCESS would leave it consoleless, and then every
    # console child pops up its own visible window.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([codex, "-c", "features.code_mode_host=true", "app-server",
                      "--listen", "unix://"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True)
    for _ in range(40):
        time.sleep(0.25)
        try:
            return WSPipe([codex, "app-server", "proxy"])
        except ConnectionError:
            pass
    raise ConnectionError("shared Codex app-server did not come up")


def main():
    ws = connect()
    note("panel bridged to shared server")
    out = sys.stdout.buffer

    def upstream():
        for line in sys.stdin.buffer:
            line = line.strip()
            if line:
                ws.send(line.decode("utf-8"))
        note("panel closed stdin")
        ws.close()

    threading.Thread(target=upstream, daemon=True).start()
    while True:
        msg = ws.recv()
        if msg is None:
            break
        out.write(msg.encode("utf-8") + b"\n")
        out.flush()
    note("shared server connection closed")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # the panel shows stderr as the CLI error
        note("bridge failed: %s" % e)
        sys.stderr.write("codex-shared bridge: %s\n" % e)
        sys.exit(1)
