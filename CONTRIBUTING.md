# Contributing

Issues and pull requests are welcome. This is a small project; nothing here is heavy.

## Getting set up

```bash
git clone https://github.com/lburnscissp/discord-mcp.git
cd discord-mcp
uv sync
uv run pytest
```

The tests need no Discord token and make no network calls, so a clone-and-test takes about
ten seconds. If you don't have [uv](https://docs.astral.sh/uv/), a plain
`pip install -e ".[dev]"` in a 3.12+ virtualenv works too.

## Before opening a PR

- `uv run pytest` passes.
- New or changed tools follow [docs/adding-a-tool.md](docs/adding-a-tool.md) — especially
  the docstring conventions, since that text is what an AI model reads to decide whether
  to call your tool.
- New behaviour has a test. For an API wrapper the valuable assertion is usually on the
  *request* that was produced, not just the response that was parsed.
- Comments explain **why**, not what. The code says what it does; a comment earns its place
  by recording a Discord quirk, a trade-off, or a non-obvious constraint.

## What fits here

Good fits: more REST endpoint coverage (invites, emoji, scheduled events, forum posts,
webhooks, voice state), better error messages, clearer docs.

Out of scope: a gateway/WebSocket connection (that's a different program — see
[docs/architecture.md](docs/architecture.md)), anything that automates a human Discord
account rather than a bot (against Discord's terms of service), and features that need
state kept between calls.

## Security

Never commit a token. `.env`, `*.key`, `*.pem` and anything matching `*credentials*` or
`*secrets*` are git-ignored; check with `git check-ignore -v <file>` if you're unsure. If
you think you've exposed a token, reset it in the Discord developer portal immediately —
a committed secret stays in git history even after the file is deleted.

Found a security problem? Open a draft security advisory on the repo rather than a public
issue.

## Code of conduct

Be decent. Assume good faith, keep it about the code.
