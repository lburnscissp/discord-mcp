"""Process entry point.

Two transports, same tools:

  * **stdio** (default) — the client launches this file as a subprocess and speaks MCP
    over its stdin/stdout. This is how Claude Code, Claude Desktop and most editors run
    local MCP servers, and what the README's install command uses.
  * **streamable HTTP** (`--http`) — serves on 127.0.0.1 instead, which is handy for
    debugging with curl or for a client that wants to connect over a socket. Bound to
    localhost deliberately: this process holds a bot token and must not be reachable
    from the network.

The one rule that matters here: **stdout belongs to the protocol**. A stray `print()`
anywhere in the codebase corrupts the JSON-RPC stream and the client disconnects with a
parse error that points nowhere useful. Hence logging is configured to stderr below, and
`client.py` uses the logger rather than printing.
"""

from __future__ import annotations

import argparse
import logging
import sys

from discord_mcp.tools import mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="discord-mcp", description="Discord MCP server")
    parser.add_argument("--http", action="store_true", help="Serve over streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8765, help="Port for --http (default: 8765)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Log every request at DEBUG level to stderr")
    args = parser.parse_args()

    # stderr, never stdout — see the module docstring.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.http:
        mcp.settings.host = "127.0.0.1"  # localhost only: this process holds a bot token
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
