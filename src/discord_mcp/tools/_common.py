"""The shared scaffolding every tool module builds on.

Read this file first if you want to understand how the server is put together — the six
tool modules are repetitive once you know what is happening here.

How a Python function becomes an MCP tool
-----------------------------------------
`MCPServer` (the ergonomic layer in the `mcp` SDK, called `FastMCP` before v2) builds the
tool's wire schema by introspecting the decorated function:

    @mcp.tool(name="discord_react", annotations=UPDATE)
    @tool_errors
    async def discord_react(
        channel_id: ChannelId,
        emoji: Annotated[str, Field(description="Unicode emoji like '👍'...")],
        action: Literal["add", "remove"] = "add",
    ) -> str:
        '''Add or remove the bot's own reaction on a message. ...'''

  * the **function name** (or the explicit `name=`) becomes the tool name,
  * the **docstring** becomes the tool description the model reads to decide whether to
    call it — this is the single highest-leverage text in the whole project,
  * each **parameter** becomes an input property; type hints become JSON Schema types,
    `Annotated[..., Field(...)]` adds descriptions and constraints (patterns, min/max),
    and a default makes it optional,
  * the **return type** `str` means we hand back plain text and control the formatting
    ourselves rather than letting the SDK serialise an object.

Keep arguments flat
-------------------
A tempting alternative is one Pydantic model per tool (`async def tool(params: MyInput)`).
Don't: the SDK then generates a schema nested under a single `params` object, and models
routinely get that wrong — they send the fields at the top level and the call fails
validation. Flat keyword arguments produce a flat schema, which is what clients expect.
The shared `Annotated` aliases below are how we keep that flat style from becoming
copy-paste.

Writing a good tool description
-------------------------------
The docstring is read by a model that cannot see Discord. It should say, in order:
what the tool does in one line; which permission or privileged intent it needs; what to
confirm with the user before calling it if it is destructive; and what the return value
looks like in each format. When two tools are easy to confuse, name the other one
("for 2-100 messages at once use discord_bulk_delete_messages"). That cross-reference is
often what stops a wrong call.
"""

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

# The one server instance. Each tool module imports this and decorates its functions with
# it; `tools/__init__.py` imports all the modules, which is what actually registers them.
#
# `instructions` is sent to the client once at connection time, ahead of any tool call.
# It is the place for facts that apply to every tool — here: that IDs are snowflakes,
# where to start looking for them, and that some tools change a live server.
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

# Snowflakes are 17–20 digits today (they grow by one digit roughly every few years as the
# timestamp bits advance). Validating the shape in the schema means a malformed ID is
# rejected by the client before a request is made, and the model gets a precise complaint
# instead of a Discord 404.
SNOWFLAKE = r"^\d{17,20}$"

# Reusable argument types. Defaults belong on the parameter (`guild_id: GuildId = None`),
# not inside Field(), so the generated schema marks them optional correctly.
Snowflake = Annotated[str, Field(pattern=SNOWFLAKE)]
OptSnowflake = Annotated[Optional[str], Field(pattern=SNOWFLAKE)]
GuildId = Annotated[
    Optional[str],
    Field(pattern=SNOWFLAKE, description="Server (guild) ID. Omit to use DISCORD_GUILD_ID from .env."),
]
Fmt = Annotated[ResponseFormat, Field(description="'markdown' for reading, 'json' for full structured data.")]
Reason = Annotated[Optional[str], Field(max_length=512, description="Reason recorded in the server's audit log.")]

# MCP tool annotations: hints that let a client decide how much ceremony an action needs —
# typically auto-approving read-only calls and prompting before destructive ones. They are
# advisory, not enforcement: nothing stops a client ignoring them, so they are a UX signal,
# never a security boundary. Four presets cover every tool here:
#
#   READ         safe to call speculatively; changes nothing.
#   WRITE        creates something new (a message, a channel, a role).
#   UPDATE       changes or toggles existing state; calling twice lands in the same place.
#   DESTRUCTIVE  deletes data or removes a person. Confirm with the user first.
#
# openWorldHint is True everywhere because every call reaches a third-party service whose
# state we don't control.
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
UPDATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

# Short aliases so `response_format: Fmt = MD` reads cleanly in 16 signatures.
MD = ResponseFormat.MARKDOWN
JSON = ResponseFormat.JSON


def tool_errors(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    """Convert exceptions into readable "Error: ..." text.

    Why not let them raise? An exception becomes a protocol-level error, which most
    clients surface as a dead end. A returned string stays in the conversation, so the
    model can read "Missing permissions. Give the bot's role ... in Server Settings" and
    either fix the call or tell the user what to change. Recoverable beats correct-looking.

    Three layers:
      * DiscordError — already carries an actionable message (see client._ERROR_HINTS).
      * ValueError — Pydantic and our own validation; the text is already specific.
      * everything else — logged with a traceback to stderr for the developer, reported to
        the model as a one-liner. Tracebacks never go into a tool response: they are noise
        to a model and can contain URLs we would rather not echo.
    """

    @functools.wraps(fn)  # preserves __name__/__doc__, which the SDK reads for the schema
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
