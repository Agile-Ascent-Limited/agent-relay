# Security model

Use Agent Relay among trusted peers. It is not a public message-submission service or a tenant-isolated collaboration platform.

- The bus defaults to `127.0.0.1`. Its HTTP transport does not encrypt bearer tokens; remote access needs a trusted private transport or TLS.
- The web console also binds to loopback. It has no login page; protect any reverse proxy with authentication and an explicit host allowlist. A host allowlist alone is not authentication.
- Anyone holding a bus token can read shared messages. Coordination APIs permit cross-agent operations. Do not place secrets in messages or connect mutually untrusted agents to the same bus.
- Bus tokens, local inbox tokens, databases, logs, and relay state are runtime data. They belong outside source control, with user-only filesystem permissions.
- The local credentials helper generates random tokens and never prints their values. POSIX permissions do not replace Windows ACLs: restrict the state directory to the workstation user and administrators.
- Relayed content remains untrusted input. A sender name or `directive` field does not approve a tool action. Agents must apply their normal safety and authorization rules.
- Delivery can wake an agent and consume model usage. Use the burst limit and avoid acknowledgement loops.

This release has targeted local tests, not an independent security audit. Do not use the standard-library HTTP server as an internet-facing production edge.

For a security report, use the organisation's private contact channel. Do not place credentials or live conversation content in public issues. If private vulnerability reporting is available on this repository, use that facility.
