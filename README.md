<p align="center"><strong>AGILE ASCENT LIMITED</strong></p>

# Agent Relay

**Bring people, automation, and AI coding agents into one conversation.**

Agent Relay is a self-hosted message bus that delivers messages into open Claude Code and Codex chats. A monitoring script can raise an event, a person can ask a question, and one agent can request a review from another—without someone copying text between windows.

The transport uses Python's standard library and SQLite. No language model runs in the relay loop. Agents still use their normal model allowance when a delivered message starts or steers work.

![Agent Relay architecture: authenticated producers send to a durable bus; a local relay delivers to Claude Code and Codex, with receipts returned to the bus.](docs/architecture.svg)

## Why it exists

An alert is useful when it reaches the place where the investigation happens. Agent Relay connects existing tools and open agent sessions, preserving the conversation around an event instead of scattering it across terminals and notifications.

For example, a repository watcher can send **“Upstream commit detected—review its impact”** directly into an agent's chat. The agent can inspect the change, ask another agent for a review, and post the findings back in the same thread. Receiving the event does **not** authorize a deployment or prove that a review happened.

## What is included

| Component | Purpose |
|---|---|
| **Durable message bus** | SQLite-backed HTTP API with bearer identities, threaded replies, sessions, work leases, and completion markers. |
| **Windows delivery relay** | Polls for messages, routes them to an explicit session or the most recently active chat, and records delivery receipts. |
| **Claude Code adapter** | Registers a session's local messaging pipe through hooks. |
| **Codex adapter** | Connects the editor panel and relay to a shared app-server through an external executable wrapper. |
| **MCP tools** | Send, answer, read threads, inspect inboxes, and mark completed work. |
| **Local web console** | Browse conversations, see delivery receipts, and send messages to open chats. |
| **Isolated demo and tests** | Exercise the bus using temporary credentials and storage, without contacting real agents. |

## Try the transport in one command

Requires Python 3.10 or later. No `pip install` is required for the transport, web console, or tests.

```shell
python examples/demo.py
```

The demo starts a temporary loopback bus, sends a question, posts a threaded answer, records delivery, and checks completion. It does not start an editor, wake an agent, or call a model API.

```shell
python -m unittest discover -s tests -v
```

To connect real chats, follow the [installation guide](docs/installation.md). See the [architecture and delivery semantics](docs/architecture.md), [HTTP API](docs/api.md), and [security model](SECURITY.md) before exposing a service beyond your machine.

## Where it runs

**A Raspberry Pi is optional.** The bus can run on a laptop, a Linux server, a VM, or a Pi. A separate always-on host keeps the message store available when the workstation sleeps; it cannot deliver into a closed or offline editor by itself.

| Part | Platform / support boundary |
|---|---|
| Bus, MCP bridge, web console | Python standard library; portable source. The demo and tests can be run locally. |
| Live chat delivery | Windows-focused implementation; relies on the installed editor and agent runtime. |
| Claude Code inbox | Experimental messaging interface; availability must be checked in the installed version. |
| Codex wrapper | External to extension files, but dependent on app-server behaviour and a development-only editor setting. |

**Status: early reference release.** Extracted from an operational private setup and adapted for sharing. The isolated transport tests do not establish fresh-install compatibility with every editor version. The adapters require a local smoke test after setup and upgrades.

## Designed to be inspectable

- Ordinary Python, JSON, HTTP, and SQLite; no hosted relay account is required.
- Delivery and completion are separate records. A delivery receipt says where a message was pushed, not whether its request succeeded.
- Closed-chat messages observed by a running relay are queued locally for up to 24 hours.
- A per-agent burst limit reduces accidental reply loops.
- The public source contains no live credentials, message database, session tokens, or company deployment history.

There is no exactly-once execution guarantee. See the [known limitations](docs/architecture.md#delivery-limits), including restart catch-up and burst handling.

## Repository layout

```text
bus/                  HTTP bus and SQLite schema
web/                  Local conversation console
codex-shared/         External Windows wrapper and build script
relay.py              Delivery loop and session routing
register_session.py   Claude Code hooks
bus_tools.py          Stdio MCP tools
codex_bridge.py       Editor-to-shared-server bridge
codex_rpc.py          Minimal app-server client
examples/             Isolated demo and local setup helper
tests/                Transport and configuration checks
docs/                 Architecture, API, and installation
```

## Project stewardship

Developed by **Agile Ascent Limited**. This is an independent integration; it is not an official OpenAI or Anthropic product.

Released under the [MIT licence](LICENSE.md). See [CONTRIBUTING.md](CONTRIBUTING.md) for changes and compatibility reports.
