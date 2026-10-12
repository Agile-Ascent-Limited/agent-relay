#!/usr/bin/env python3
"""Show what agent-relay can reach right now: open Claude chats and loaded Codex threads."""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from codex_rpc import CodexRPC  # noqa: E402

home = os.environ.get("AGENT_RELAY_STATE", os.path.join(os.environ["LOCALAPPDATA"], "agent-relay"))
print("Claude chats registered:")
for p in sorted(glob.glob(os.path.join(home, "sessions", "*.json"))):
    r = json.load(open(p))
    print("  %s  active %4.0f min ago  pipe %s" % (
        r["session_id"][:8], (time.time() - r.get("active", 0)) / 60,
        "ok" if os.path.exists(r["socket"]) else "GONE"))
print("Codex threads loaded on the shared server:")
c = CodexRPC("agent-relay-status")
for tid in c.call("thread/loaded/list", {})["data"]:
    t = c.call("thread/read", {"threadId": tid, "includeTurns": False})["thread"]
    print("  %s  %-7s  %s%s" % (tid, (t.get("status") or {}).get("type"),
                              (t.get("preview") or "")[:60].replace("\n", " "),
                              "  [subagent]" if t.get("parentThreadId") else ""))
c.close()
