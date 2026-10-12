# Installation

Start with `python examples/demo.py`. It exercises a temporary loopback bus and leaves your editor and live relay alone.

## Bus and local console

Python 3.10+ is required. From the repository root:

```powershell
python examples/init_local.py --state .local
$env:AGENT_RELAY_BUS_DB = "$PWD/.local/bus.sqlite3"
$env:AGENT_RELAY_BUS_TOKENS = "$PWD/.local/tokens.json"
python bus/agent_relay_bus.py
```

The helper refuses an existing destination. It creates random per-agent tokens; it does not print them. `.local` is ignored by Git. Restrict that directory's ACL to its owner and administrators.

In a second terminal:

```powershell
$env:AGENT_RELAY_VIEW_TOKEN_FILE = "$PWD/.local/human-token.txt"
python web/serve.py
```

Open `http://127.0.0.1:5031`. The console can store a message before chat delivery is configured. That does not mean an agent received it.

On Linux or a Pi, use the same Python commands and POSIX environment assignment (`export NAME=value`). Neither a Pi nor Cloudflare is required. If you place the bus on a separate host, configure a private network or authenticated TLS proxy and set `AGENT_RELAY_BUS` in all clients. The web console uses `AGENT_RELAY_BUS_URL`. Do not expose the bare bus or console publicly.

## MCP tools

Run `python /absolute/path/bus_tools.py codex` as a stdio MCP server in your agent client. Supply `AGENT_RELAY_BUS` and `AGENT_RELAY_TOKENS` in that client's environment; the latter points to the generated `tokens-by-agent.json`.

The MCP server provides outbound tools and catch-up reads. It does not push new messages into chats by itself.

## Windows live delivery

The adapters are experimental integrations. Use a separate test profile first and retain your previous settings. All related processes must see the same bus URL, credentials file, and relay state directory.

```powershell
$env:AGENT_RELAY_BUS = 'http://127.0.0.1:5010'
$env:AGENT_RELAY_TOKENS = "$PWD/.local/tokens-by-agent.json"
$env:AGENT_RELAY_STATE = "$PWD/.local/relay-state"
python relay.py
```

This foreground command enables delivery and may connect to or start a shared Codex backend. Do not run it against your normal sessions merely to try the transport: use the isolated demo for that.

### Claude Code

Configure `register_session.py SessionStart` and `register_session.py UserPromptSubmit` as the corresponding hooks, using absolute paths. The installed Claude runtime must expose `CLAUDE_CODE_MESSAGING_SOCKET` and `CLAUDE_CODE_MESSAGING_TOKEN` to the hook. The original integration used the experimental agent-teams feature. Availability is version-dependent; this package does not enable it automatically.

The hook writes local pipe credentials to the relay state directory. Those credentials never belong on the bus. Confirm a session is registered, send one test message, and check a delivery receipt before relying on the adapter.

### Codex

Build the wrapper without activating it:

```powershell
powershell -NoProfile -File codex-shared/build.ps1
```

It writes `bin/codex-shared.exe` inside this checkout. Its default bridge path is the adjacent checkout's `codex_bridge.py`; move the directory as a unit. Optional overrides: `AGENT_RELAY_BRIDGE`, `AGENT_RELAY_PYTHON`, and `AGENT_RELAY_CODEX_EXE`.

In the chosen VS Code test profile, set `chatgpt.cliExecutable` to that executable's absolute path. Restart that profile when no work is active. The setting is [development-only](https://learn.chatgpt.com/docs/developer-settings#editor-settings-reference), and this wrapper is not an official extension interface.

The wrapper discovers a Windows x64 bundled Codex binary by extension-directory modification time. Check the selected runtime after upgrades, especially if several extension versions are installed. A running shared server can remain on its old version until it is deliberately stopped.

### Smoke test and rollback

1. Open one disposable Claude chat and one disposable Codex chat.
2. Send a harmless message to each, then a reply with the original `thread_id`.
3. Confirm the chosen chat, delivery receipt, and visible response agree.
4. Repeat while a harmless turn is active, then with the target chat closed.
5. Verify the burst brake and queue behaviour before operational use.

To roll back, stop the relay, restore/remove `chatgpt.cliExecutable` in the affected profile, remove the two Claude hooks, and restart the editor when idle. Stop only the shared backend you started; do not kill unrelated Codex processes. Preserve the bus database and delivery records if needed for diagnosis.

For unattended use, supervise the bus with your service manager and the relay in the workstation user's session. This release intentionally does not overwrite scheduled tasks, editor settings, existing hooks, or running services.

## Configuration

| Variable | Default / purpose |
|---|---|
| `AGENT_RELAY_BUS_HOST` | `127.0.0.1` bus bind address |
| `AGENT_RELAY_BUS_PORT` | `5010` |
| `AGENT_RELAY_BUS_DB` | SQLite file beside the bus module |
| `AGENT_RELAY_BUS_TOKENS` | Server token-to-agent JSON map |
| `AGENT_RELAY_AGENTS` | `claude,codex,human,watcher` |
| `AGENT_RELAY_BUS_NOTIFY` | Empty; optional local notification executable |
| `AGENT_RELAY_BUS` | Client URL, loopback by default |
| `AGENT_RELAY_TOKENS` | Client agent-to-token JSON map |
| `AGENT_RELAY_STATE` | Windows relay state; otherwise `%LOCALAPPDATA%/agent-relay` |
| `AGENT_RELAY_POLL_SECS` | `1` |
| `AGENT_RELAY_VIEW_PORT` | `5031` |
| `AGENT_RELAY_BUS_URL` | Web console's bus URL |
| `AGENT_RELAY_VIEW_TOKEN_FILE` | File containing the human token; or systemd `bus-token` credential |
| `AGENT_RELAY_VIEW_HOSTS` | `127.0.0.1,localhost`; explicit comma-separated host allowlist |
| `AGENT_RELAY_CODEX_PRIVATE` | `1` makes wrapper calls use the bundled CLI directly |
