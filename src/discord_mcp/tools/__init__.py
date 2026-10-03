"""Tool registry.

Importing this package is what registers the tools: each module below decorates its
functions with the shared `mcp` instance from `_common`, and a decorator only runs when
its module is imported. `server.py` imports `mcp` from here, so adding a new module means
adding it to this import line — otherwise its tools silently never appear.

The split is by Discord concept, which is also how the README's tool table is grouped:

    guilds      servers themselves: identity, which ones, details
    channels    channels, categories and threads
    messages    reading, searching, posting, reacting, pinning, DMs
    members     finding people and changing their roles
    roles       the roles themselves
    moderation  timeout, kick, ban, bulk delete, audit log
"""

from discord_mcp.tools import channels, guilds, members, messages, moderation, roles  # noqa: F401
from discord_mcp.tools._common import mcp

__all__ = ["mcp"]
