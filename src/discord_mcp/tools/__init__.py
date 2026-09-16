"""Importing this package registers every tool on the shared MCPServer instance."""

from discord_mcp.tools import channels, guilds, members, messages, moderation, roles  # noqa: F401
from discord_mcp.tools._common import mcp

__all__ = ["mcp"]
