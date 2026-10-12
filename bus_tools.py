#!/usr/bin/env python3
"""Stdio MCP server: bus tools for agents without a shell habit (Codex).

Tools: bus_send, bus_ack, bus_inbox, bus_thread, bus_sessions. Identity from argv[1]
(default codex). Incoming mail is NOT read here: agent-relay pushes it into the chat.
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BUS = os.environ.get("AGENT_RELAY_BUS", "http://127.0.0.1:5010")
AGENT = sys.argv[1] if len(sys.argv) > 1 else "codex"
TOKENS = os.environ.get("AGENT_RELAY_TOKENS", os.path.join(HERE, "bus", "tokens-by-agent.json"))
TOK = json.load(open(TOKENS))[AGENT]
SKEY = "%s-%d" % (AGENT, os.getpid())  # one process per chat = stable identity

TOOLS = [
    {"name": "bus_send",
     "description": "Post a bus message (to: claude|codex|human|watcher|all; kind: "
                    "directive|question|info|answer). Answer mail with kind=answer and "
                    "thread_id=<its id>. Never send acknowledgement-only replies: every "
                    "message wakes the recipient instantly. the operator sees only your FINAL "
                    "message (tool calls and 'Worked for' steps are hidden), so paste what "
                    "you sent there: `📤 Bus #<id> to <to>: <subject>` + the full body.",
     "inputSchema": {"type": "object", "properties": {
         "to": {"type": "string"}, "kind": {"type": "string"},
         "topic": {"type": "string"}, "subject": {"type": "string"},
         "body": {"type": "string"}, "thread_id": {"type": "integer"},
         "session": {"type": "string", "description": "optional: one target chat"}},
         "required": ["to", "kind", "subject", "body"]}},
    {"name": "bus_ack",
     "description": "ACK message ids. ACK means COMPLETED: answer questions/finish "
                    "directives (kind=answer reply with thread_id) first.",
     "inputSchema": {"type": "object", "properties": {
         "ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["ids"]}},
    {"name": "bus_inbox",
     "description": "List unACKed mail for this agent (catch-up only; new mail is pushed).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "bus_thread",
     "description": "Full text of a message and its replies (verify a directive).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}},
                     "required": ["id"]}},
    {"name": "bus_sessions",
     "description": "List live chat sessions across agents (for session targeting).",
     "inputSchema": {"type": "object", "properties": {}}},
]


def api(path, data=None):
    req = urllib.request.Request(
        BUS + path, method="POST" if data is not None else "GET",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=8))


def call_tool(name, args):
    if name == "bus_send":
        payload = {k: args[k] for k in ("to", "kind", "topic", "subject", "body",
                                        "thread_id", "session") if k in args}
        payload["from_session"] = SKEY
        return api("/msg", payload)
    if name == "bus_ack":
        return api("/ack", {"agent": AGENT, "ids": args["ids"]})
    if name == "bus_inbox":
        return api("/inbox/%s?unread=1&limit=30" % AGENT)
    if name == "bus_thread":
        return api("/thread/%d" % int(args["id"]))
    if name == "bus_sessions":
        return api("/sessions")
    raise ValueError("unknown tool " + name)


def reply(mid, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        out["error"] = {"code": -32000, "message": str(error)}
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        meth, mid = m.get("method"), m.get("id")
        if meth == "initialize":
            reply(mid, {"protocolVersion": m["params"].get("protocolVersion", "2025-03-26"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "agent-relay", "version": "1.0"}})
        elif meth == "tools/list":
            reply(mid, {"tools": TOOLS})
        elif meth == "tools/call":
            try:
                res = call_tool(m["params"]["name"], m["params"].get("arguments") or {})
                reply(mid, {"content": [{"type": "text", "text": json.dumps(res, indent=1)}]})
            except Exception as e:
                reply(mid, error=e)
        elif meth == "ping":
            reply(mid, {})
        elif mid is not None:
            reply(mid, error="unsupported method " + str(meth))


if __name__ == "__main__":
    main()
