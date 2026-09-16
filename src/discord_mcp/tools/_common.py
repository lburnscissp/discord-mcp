"""Bits every tool module shares: the server instance, argument types, annotations, error wrapping."""

from __future__ import annotations

import functools
import logging
from typing import Annotated, Awaitable, Callable, Optional

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import Field

from discord_mcp.client import DiscordError
from discord_mcp.formatting import ResponseFormat

log = logging.getLogger("discord_mcp")

mcp = MCPServer(
    "discord_mcp",
    instructions=(
        "Tools for operating a Discord bot in servers it has been invited to. "
        "IDs are Discord snowflakes (17–20 digit strings). Start with discord_list_guilds "
        "and discord_list_channels to find IDs. If DISCORD_GUILD_ID is set in .env, guild_id "
        "can be omitted. Posting, deleting and moderation tools change the live server — "
        "confirm with the user before calling them."
    ),
)

SNOWFLAKE = r"^\d{17,20}$"

# Reusable argument types. Defaults go on the parameter, not in Field, so the
# generated schema shows them and callers can omit them.
Snowflake = Annotated[str, Field(pattern=SNOWFLAKE)]
OptSnowflake = Annotated[Optional[str], Field(pattern=SNOWFLAKE)]
GuildId = Annotated[Optional[str], Field(pattern=SNOWFLAKE, description="Server (guild) ID. Omit to use DISCORD_GUILD_ID from .env.")]
Fmt = Annotated[ResponseFormat, Field(description="'markdown' for reading, 'json' for full structured data.")]
Reason = Annotated[Optional[str], Field(max_length=512, description="Reason recorded in the server's audit log.")]

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
UPDATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

MD = ResponseFormat.MARKDOWN
JSON = ResponseFormat.JSON


def tool_errors(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    """Turn DiscordError into an `Error: …` string so the agent sees guidance, not a traceback."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs) -> str:
        try:
            return await fn(*args, **kwargs)
        except DiscordError as e:
            return f"Error: {e.message}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:  # noqa: BLE001 — last resort; never leak a traceback over stdio
            log.exception("unexpected error in %s", fn.__name__)
            return f"Error: unexpected {type(e).__name__} in {fn.__name__}. Check the server log (stderr)."

    return wrapper
