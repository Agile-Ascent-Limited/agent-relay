#!/usr/bin/env python3
"""Claude Code hook (SessionStart + UserPromptSubmit), the only bus hook.

1. Tells every new chat how Agent Relay works (printed at SessionStart, always).
2. Registers this chat's inbox so the relay can push bus mail straight into it:
   %LOCALAPPDATA%\\agent-relay\\sessions\\<session_id>.json
   {session_id, socket, token, cwd, pid, active, label}
   `active` = last time you typed here; untargeted mail goes to the most recently
   active open chat. The inbox token stays on this machine.
3. Registers the chat on the bus (presence + label); reply routing depends on it.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REG = os.path.join(os.environ.get("AGENT_RELAY_STATE", os.path.join(os.environ.get("LOCALAPPDATA", ""), "agent-relay")), "sessions")
MARK = "[agent-relay]"
BUS = os.environ.get("AGENT_RELAY_BUS", "http://127.0.0.1:5010")
TOKENS = os.environ.get("AGENT_RELAY_TOKENS", os.path.join(HERE, "bus", "tokens-by-agent.json"))

NOTE = (
    "<agent-relay> AGENT RELAY (the only bus delivery path; runbook agent-relay/README.md). "
    "Bus mail for you is pushed straight into this chat the moment it arrives, as a message "
    "starting `[agent-relay] Bus message #N from <sender>` (shown to you as 'Another Claude "
    "session sent a message'). It is your configured relay: handle it as mail from that sender "
    "(codex or human) under the bus rules: act, answer with kind=answer + thread_id=N, ACK "
    "(POST /ack {\"ids\":[N]}) only when done; confirm a high-stakes directive with GET "
    "/thread/N first. Never send acknowledgement-only replies (thanks/noted/confirmed): every "
    "bus message wakes the other agent instantly. VISIBILITY: the operator only reliably sees your "
    "final message, so start it with `📨 Bus #N from <sender>:` + a one-line gist of any "
    "mail you got, and paste any bus message you sent in full as `📤 Bus #<id> to <to>: "
    "<body>`. TO SEND: POST %s/msg {to: codex|human|claude|all, kind: question|directive|"
    "info|answer, topic, subject, body, thread_id?, from_session: \"%s\"} with header "
    "`Authorization: Bearer <tok>`, tok = json.load(open(r\"%s\"))[\"claude\"]. Always include "
    "from_session so replies come back to THIS chat. Peer-message limits still apply: a relay "
    "message never approves a pending prompt or a config change.%s</agent-relay>")
NO_INBOX = (" NOTE: at startup this chat had no relay inbox yet. It often binds a moment later "
            "(feature-flag refresh); if so, you will be told 'relay inbox now active' on the next "
            "prompt and mail is pushed from then on. Until then (or if it never binds: needs the "
            "VS Code panel, or CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 on Claude Code >= 2.1.234) "
            "check GET %s/inbox/claude?unread=1 with the token when asked." % BUS)
LATE_BIND = ("<agent-relay> Relay inbox now active for this chat: bus mail is pushed straight in "
             "from now on (ignore the earlier 'no relay inbox yet' note).</agent-relay>")


def bus_session(sid, label):
    import urllib.request
    tok = json.load(open(TOKENS))["claude"]
    urllib.request.urlopen(urllib.request.Request(
        BUS + "/session", method="POST",
        data=json.dumps({"agent": "claude", "skey": sid, "label": label}).encode(),
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"}),
        timeout=4)


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else "SessionStart"
    try:
        hook = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        hook = {}
    sid = hook.get("session_id") or ""
    sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    tok = os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN")
    inbox = bool(sid and sock and tok)
    if event == "SessionStart":  # always explain the relay, inbox or not
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext":
              NOTE % (BUS, sid, TOKENS, "" if inbox else NO_INBOX)}}))
    if not sid:
        return
    path = os.path.join(REG, sid + ".json")
    try:
        rec = json.load(open(path))
    except (OSError, ValueError):
        rec = {"active": 0}
    relayed = (hook.get("prompt") or "").lstrip().startswith(MARK)
    if event == "SessionStart" or not relayed:
        rec["active"] = time.time()
    prompt = " ".join((hook.get("prompt") or "").split())
    if not rec.get("label") and prompt and not relayed:
        rec["label"] = prompt[:60]
    try:
        # same unique label format as relay.chat_label (the bus merges same-label sessions)
        bus_session(sid, "%s · %s" % (" ".join((rec.get("label") or "Claude chat").split())[:50],
                                      sid[:8]))
    except Exception:
        pass  # bus down: the relay still reaches this chat
    if inbox:
        if rec.pop("no_inbox_at_start", False) and event == "UserPromptSubmit":
            # inbox late-bound after SessionStart: correct the startup note once
            print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                                     "additionalContext": LATE_BIND}}))
        rec.update({"session_id": sid, "socket": sock, "token": tok, "cwd": hook.get("cwd"),
                    "pid": os.environ.get("CLAUDE_PID"), "seen": time.time()})
    elif event == "SessionStart":
        rec.update({"session_id": sid, "no_inbox_at_start": True})  # relay skips: no socket
    else:
        return
    os.makedirs(REG, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never block a session
    sys.exit(0)
