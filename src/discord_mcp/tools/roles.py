"""The roles themselves — create, recolour, re-permission, delete.

Permissions are a **bitfield**, passed as a decimal string (Discord uses strings because
the value exceeds what JavaScript can hold in a number). `"8"` is Administrator; a
read-and-write-messages role is the sum of several bits. The tools take the string as-is
rather than offering named flags: inventing a second vocabulary for 50-odd permissions
would be more to get wrong than to look up. Discord's table:
https://discord.com/developers/docs/topics/permissions

Colours are accepted as familiar hex (`"#5865F2"`) and converted to the integer Discord
wants. New roles always land at the bottom of the hierarchy; ordering them is a separate
endpoint this server does not wrap, because dragging them in the UI is easier than
describing positions to a model.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field

from discord_mcp.client import request, require_guild
from discord_mcp.formatting import md_table, slim_role, to_json
from discord_mcp.tools._common import DESTRUCTIVE, JSON, MD, READ, UPDATE, WRITE, Fmt, GuildId, Reason, Snowflake, mcp, tool_errors

HexColor = Annotated[Optional[str], Field(pattern=r"^#?[0-9a-fA-F]{6}$", description="Hex color like '#5865F2'.")]
Permissions = Annotated[Optional[str], Field(pattern=r"^\d+$", description="Permission bitfield as a decimal string, e.g. '8' for Administrator.")]


def _color_int(value: str) -> int:
    return int(value.lstrip("#"), 16)


@mcp.tool(name="discord_list_roles", annotations=READ)
@tool_errors
async def discord_list_roles(guild_id: GuildId = None, response_format: Fmt = MD) -> str:
    """List a server's roles from highest to lowest, with IDs, colors and flags.

    Use to find role_id for discord_set_member_role. Permission bitfields are in the JSON output.

    Returns: Markdown table (name, ID, color, hoisted, mentionable, managed) or JSON list.
    """
    gid = require_guild(guild_id)
    roles = sorted(await request("GET", f"/guilds/{gid}/roles"), key=lambda r: -r.get("position", 0))
    if response_format == JSON:
        return to_json({"count": len(roles), "roles": [slim_role(r) for r in roles]})
    rows = [[r["name"], r["id"], slim_role(r)["color"], "yes" if r.get("hoist") else "", "yes" if r.get("mentionable") else "", "bot/integration" if r.get("managed") else ""] for r in roles]
    return f"# Roles ({len(roles)}, highest first)\n\n" + md_table(["Name", "ID", "Color", "Hoisted", "Mentionable", "Managed"], rows)


@mcp.tool(name="discord_create_role", annotations=WRITE)
@tool_errors
async def discord_create_role(
    name: Annotated[str, Field(min_length=1, max_length=100)],
    guild_id: GuildId = None,
    color: HexColor = None,
    hoist: Annotated[bool, Field(description="Show members with this role separately in the sidebar.")] = False,
    mentionable: Annotated[bool, Field(description="Let anyone @mention the role.")] = False,
    permissions: Permissions = None,
    reason: Reason = None,
) -> str:
    """Create a role. Needs Manage Roles. New roles appear at the bottom of the hierarchy.

    Returns: Confirmation with the role ID.
    """
    gid = require_guild(guild_id)
    body: dict = {"name": name, "hoist": hoist, "mentionable": mentionable}
    if color:
        body["color"] = _color_int(color)
    if permissions:
        body["permissions"] = permissions
    r = await request("POST", f"/guilds/{gid}/roles", json=body, reason=reason)
    return f"Created role {r['name']} ({r['id']})."


@mcp.tool(name="discord_edit_role", annotations=UPDATE)
@tool_errors
async def discord_edit_role(
    role_id: Snowflake,
    guild_id: GuildId = None,
    name: Annotated[Optional[str], Field(min_length=1, max_length=100)] = None,
    color: HexColor = None,
    hoist: Optional[bool] = None,
    mentionable: Optional[bool] = None,
    permissions: Permissions = None,
    reason: Reason = None,
) -> str:
    """Change a role's name, color, hoist/mentionable flags, or permissions. Only passed fields change.

    Returns: Confirmation listing the fields changed.
    """
    gid = require_guild(guild_id)
    given = {"name": name, "hoist": hoist, "mentionable": mentionable, "permissions": permissions, "color": _color_int(color) if color else None}
    body = {k: v for k, v in given.items() if v is not None}
    if not body:
        return "Error: nothing to change — pass at least one field besides role_id."
    r = await request("PATCH", f"/guilds/{gid}/roles/{role_id}", json=body, reason=reason)
    return f"Updated role {r['name']} ({r['id']}): changed {', '.join(body)}."


@mcp.tool(name="discord_delete_role", annotations=DESTRUCTIVE)
@tool_errors
async def discord_delete_role(role_id: Snowflake, guild_id: GuildId = None, reason: Reason = None) -> str:
    """Delete a role, removing it from every member. Irreversible — confirm with the user first.

    Returns: Confirmation.
    """
    gid = require_guild(guild_id)
    await request("DELETE", f"/guilds/{gid}/roles/{role_id}", reason=reason)
    return f"Deleted role {role_id}."
