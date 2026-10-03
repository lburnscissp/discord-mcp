"""Server-level tools: who the bot is, which servers it can see, details of one.

Start here when nothing works. `discord_whoami` is the cheapest possible check that the
token is valid, and `discord_list_guilds` answers the question people are most often
surprised by: **a bot can only see servers it has been invited to.** There is no way to
point it at an arbitrary server from the outside — someone with Manage Server has to add
it. (Driving a human account instead, a "self-bot", violates Discord's terms of service
and will get the account banned. Don't.)
"""

from __future__ import annotations

from discord_mcp.client import request, require_guild
from discord_mcp.formatting import fmt_time, md_table, slim_guild, snowflake_time, to_json, user_label
from discord_mcp.tools._common import JSON, MD, READ, Fmt, GuildId, mcp, tool_errors


@mcp.tool(name="discord_whoami", annotations=READ)
@tool_errors
async def discord_whoami() -> str:
    """Identify the bot account this server is running as.

    Use first to confirm the token works, and to learn the bot's own user ID (needed to
    tell the bot's messages apart from other people's).

    Returns: `Name (@username, ID) [bot]` plus the bot's creation date.
    Errors: `Error: Invalid bot token…` if DISCORD_BOT_TOKEN is wrong or missing.
    """
    me = await request("GET", "/users/@me")
    return f"Running as {user_label(me)} — account created {fmt_time(snowflake_time(me['id']))}."


@mcp.tool(name="discord_list_guilds", annotations=READ)
@tool_errors
async def discord_list_guilds(response_format: Fmt = MD) -> str:
    """List every server (guild) the bot has been invited to.

    A bot only sees servers where someone with Manage Server added it — it cannot browse
    servers it isn't in. Use this to find a guild_id.

    Returns: Markdown table of name and ID, or JSON list of {id, name, owner, permissions}
    where `owner` means the bot's owner owns the server.
    """
    guilds = await request("GET", "/users/@me/guilds")
    rows = [{"id": g["id"], "name": g["name"], "owner": g.get("owner", False), "permissions": g.get("permissions")} for g in guilds]
    if response_format == JSON:
        return to_json({"count": len(rows), "guilds": rows})
    if not rows:
        return "The bot is not in any servers yet. Invite it with the OAuth2 URL from the developer portal (see README)."
    return "# Servers the bot is in\n\n" + md_table(["Name", "ID", "Bot owner owns it"], [[r["name"], r["id"], "yes" if r["owner"] else "no"] for r in rows])


@mcp.tool(name="discord_get_guild", annotations=READ)
@tool_errors
async def discord_get_guild(guild_id: GuildId = None, response_format: Fmt = MD) -> str:
    """Get details for one server: name, owner, member and online counts, creation date.

    Returns: Markdown summary or JSON with id, name, owner_id, description,
    approximate_member_count, approximate_presence_count, created_at, premium_tier.
    """
    gid = require_guild(guild_id)
    g = await request("GET", f"/guilds/{gid}", params={"with_counts": "true"})
    data = slim_guild(g)
    if response_format == JSON:
        return to_json(data)
    lines = [
        f"# {data['name']} ({data['id']})",
        f"- Owner: {data.get('owner_id')}",
        f"- Members: {data.get('approximate_member_count', '?')} (online now: {data.get('approximate_presence_count', '?')})",
        f"- Created: {fmt_time(snowflake_time(data['id']))}",
        f"- Boost tier: {data.get('premium_tier', 0)}",
    ]
    if data.get("description"):
        lines.append(f"- Description: {data['description']}")
    return "\n".join(lines)
