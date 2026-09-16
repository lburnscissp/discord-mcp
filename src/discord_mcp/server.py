"""Entry point: `discord-mcp` (stdio) or `discord-mcp --http` (streamable HTTP on 127.0.0.1)."""

from __future__ import annotations

import argparse
import logging
import sys

from discord_mcp.tools import mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="discord-mcp", description="Discord MCP server")
    parser.add_argument("--http", action="store_true", help="Serve over streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    # stdout is the MCP transport — all logging goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.http:
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
