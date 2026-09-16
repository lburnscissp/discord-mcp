"""Member tools: find people, inspect them, assign roles."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from discord_mcp.client import paginate, request, require_guild
from discord_mcp.formatting import fmt_time, md_table, member_label, slim_member, to_json
from discord_mcp.tools._common import JSON, MD, READ, UPDATE, Fmt, GuildId, OptSnowflake, Reason, Snowflake, mcp, tool_errors


def _members_md(title: str, members: list[dict], roles_by_id: dict[str, str], footer: str = "") -> str:
    rows = [
        [
            member_label(m),
            ", ".join(roles_by_id.get(r, r) for r in m.get("roles", [])) or "—",
            fmt_time(m.get("joined_at")),
            "⏳ timed out" if m.get("communication_disabled_until") else "",
        ]
        for m in members
    ]
    return f"# {title}\n\n" + md_table(["Member", "Roles", "Joined", ""], rows) + (f"\n\n{footer}" if footer else "")


async def _role_names(gid: str) -> dict[str, str]:
    return {r["id"]: r["name"] for r in await request("GET", f"/guilds/{gid}/roles")}


@mcp.tool(name="discord_search_members", annotations=READ)
@tool_errors
async def discord_search_members(
    query: Annotated[str, Field(min_length=1, max_length=100, description="Username or nickname prefix, e.g. 'jam' finds 'James'.")],
    guild_id: GuildId = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    response_format: Fmt = MD,
) -> str:
    """Find server members whose username or nickname starts with a string.

    The quickest way to turn a name into a user_id. Does not need the privileged
    Server Members intent (unlike discord_list_members).

    Returns: Markdown table (member, roles, joined) or JSON list of {user_id, username,
    display_name, nick, bot, roles, joined_at, timed_out_until}.
    """
    gid = require_guild(guild_id)
    members = await request("GET", f"/guilds/{gid}/members/search", params={"query": query, "limit": limit})
    if response_format == JSON:
        return to_json({"count": len(members), "members": [slim_member(m) for m in members]})
    if not members:
        return f"No members matching '{query}'. The search is prefix-only — try the first few letters."
    return _members_md(f"Members matching '{query}'", members, await _role_names(gid))


@mcp.tool(name="discord_list_members", annotations=READ)
@tool_errors
async def discord_list_members(
    guild_id: GuildId = None,
    limit: Annotated[int, Field(ge=1, le=1000, description="How many members to return.")] = 50,
    after: Annotated[OptSnowflake, Field(description="User ID cursor from the previous page's `next_after`.")] = None,
    response_format: Fmt = MD,
) -> str:
    """List server members in join order with cursor pagination.

    Requires the Server Members privileged intent to be enabled for the bot in the
    developer portal; otherwise Discord returns Missing Access.

    Returns: Markdown table or JSON envelope {count, limit, has_more, next_after, items}.
    """
    gid = require_guild(guild_id)
    members = await paginate(f"/guilds/{gid}/members", params={"after": after}, limit=limit, page_size=1000, cursor_key="after")
    has_more = len(members) >= limit
    cursor = members[-1]["user"]["id"] if members and has_more else None
    if response_format == JSON:
        return to_json({"count": len(members), "limit": limit, "has_more": has_more, "next_after": cursor, "items": [slim_member(m) for m in members]})
    return _members_md(f"Members ({len(members)})", members, await _role_names(gid), f"_More: pass after=`{cursor}`._" if cursor else "")


@mcp.tool(name="discord_get_member", annotations=READ)
@tool_errors
async def discord_get_member(user_id: Snowflake, guild_id: GuildId = None, response_format: Fmt = MD) -> str:
    """Get one member's profile in this server: nickname, roles, join date, timeout status.

    Returns: Markdown bullets or JSON member object.
    """
    gid = require_guild(guild_id)
    m = await request("GET", f"/guilds/{gid}/members/{user_id}")
    data = slim_member(m)
    if response_format == JSON:
        return to_json(data)
    roles = await _role_names(gid)
    lines = [
        f"# {member_label(m)}",
        f"- Roles: {', '.join(roles.get(r, r) for r in data['roles']) or '—'}",
        f"- Joined: {fmt_time(data['joined_at'])}",
    ]
    if data["timed_out_until"]:
        lines.append(f"- Timed out until: {fmt_time(data['timed_out_until'])}")
    if data["pending"]:
        lines.append("- Has not passed membership screening yet")
    return "\n".join(lines)


@mcp.tool(name="discord_set_member_role", annotations=UPDATE)
@tool_errors
async def discord_set_member_role(
    user_id: Snowflake,
    role_id: Annotated[Snowflake, Field(description="Role to add or remove (find with discord_list_roles).")],
    action: Literal["add", "remove"] = "add",
    guild_id: GuildId = None,
    reason: Reason = None,
) -> str:
    """Give a role to a member, or take it away.

    Needs Manage Roles, and the bot's own top role must sit above the role being assigned.

    Returns: Confirmation.
    """
    gid = require_guild(guild_id)
    await request("PUT" if action == "add" else "DELETE", f"/guilds/{gid}/members/{user_id}/roles/{role_id}", reason=reason)
    return f"{'Added' if action == 'add' else 'Removed'} role {role_id} {'to' if action == 'add' else 'from'} user {user_id}."
