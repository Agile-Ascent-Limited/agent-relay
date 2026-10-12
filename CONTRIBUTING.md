# Contributing

Open an issue describing the problem and the smallest reproducible example. Include Python, OS, editor, and agent-extension versions when relevant. Use fabricated messages and remove tokens, machine names, personal paths, and private URLs.

Run `python -m unittest discover -s tests -v` and `python examples/demo.py` before submitting a change. For adapter changes, also describe the manual chat smoke test and the rollback used. Never test by injecting messages into someone else's session.

Keep the transport independent of provider credentials and fleet-specific scripts. Include documentation for changed delivery semantics, configuration, or trust boundaries.
