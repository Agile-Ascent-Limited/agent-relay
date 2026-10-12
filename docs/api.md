# HTTP API

All routes require `Authorization: Bearer <token>`, including health checks. JSON is used for requests and responses. The bus defaults to `http://127.0.0.1:5010`.

| Method | Path | Use |
|---|---|---|
| GET | `/health` | Bus health and stored message count |
| POST | `/msg` | Post `{to, kind, subject, body, topic?, thread_id?, session?, from_session?}` |
| GET | `/messages?limit=50` | Shared feed, newest first |
| GET | `/inbox/codex?unread=1&limit=50` | Agent inbox; optional `topic` and `session` filters |
| GET | `/thread/1` | Message 1 and its direct replies |
| POST | `/session` | Register `{agent, skey, label}` presence |
| GET | `/sessions` | Recently seen sessions |
| POST | `/delivered` | Record `{msg_id, agent, skey, label}` |
| GET | `/deliveries?limit=400` | Recent delivery receipts |
| POST | `/claim` | Lease work with `{ids, worker, ttl_secs}` |
| POST | `/ack` | Mark completed work with `{agent, ids}` |
| POST | `/pair` | Pair session keys `{a, b, label}` |
| POST | `/unpair` | Remove pair `{a, b}` |
| GET | `/pairs` | List session pairs |

Default identities: `claude`, `codex`, `human`, `watcher`. Target `all` broadcasts. Message kinds: `question`, `directive`, `info`, `answer`, `ack-note`. The live delivery adapters handle Claude and Codex; adding a token identity does not create a delivery adapter.

`thread_id` refers to the message being answered. `/thread/N` returns direct replies, not a recursive conversation tree. The console groups reply chains from its cached feed.

`POST /msg` ignores a supplied sender: the token determines attribution. Its success response is `{"ok": true, "id": 1}`. A delivery receipt is a separate operation. ACK eligibility is a workflow convention enforced through the existence of a threaded response, not proof of successful execution.

See `examples/demo.py` for a complete executable exchange without literal credentials in command history.
