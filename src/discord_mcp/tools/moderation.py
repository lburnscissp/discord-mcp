"""Moderation tools: timeout, kick, ban, bulk delete, audit log."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from pydantic import Field

from discord_mcp.client import request, require_guild
from discord_mcp.formatting import fmt_time, md_table, snowflake_time, to_json, user_label
from discord_mcp.tools._common import DESTRUCTIVE, JSON, MD, READ, UPDATE, Fmt, GuildId, OptSnowflake, Reason, Snowflake, mcp, tool_errors

MAX_TIMEOUT_MINUTES = 28 * 24 * 60  # Discord's hard cap

# Audit-log action types worth naming; anything else shows as its number.
AUDIT_ACTIONS: dict[int, str] = {
    1: "guild_update", 10: "channel_create", 11: "channel_update", 12: "channel_delete",
    20: "member_kick", 21: "member_prune", 22: "member_ban_add", 23: "member_ban_remove",
    24: "member_update", 25: "member_role_update", 26: "member_move", 27: "member_disconnect",
    28: "bot_add", 30: "role_create", 31: "role_update", 32: "role_delete",
    40: "invite_create", 42: "invite_delete", 60: "emoji_create", 61: "emoji_update", 62: "emoji_delete",
    72: "message_delete", 73: "message_bulk_delete", 74: "message_pin", 75: "message_unpin",
    110: "thread_create", 111: "thread_update", 112: "thread_delete",
    140: "auto_moderation_rule_create", 143: "auto_moderation_block_message",
}
AUDIT_ACTION_IDS = {v: k for k, v in AUDIT_ACTIONS.items()}


@mcp.tool(name="discord_timeout_member", annotations=UPDATE)
@tool_errors
async def discord_timeout_member(
    user_id: Snowflake,
    minutes: Annotated[int, Field(ge=0, le=MAX_TIMEOUT_MINUTES, description="Length in minutes (max 28 days = 40320). 0 lifts an existing timeout.")],
    guild_id: GuildId = None,
    reason: Reason = None,
) -> str:
    """Time out a member (no posting, reacting or voice) for up to 28 days, or lift a timeout with minutes=0.

    Needs Moderate Members. Confirm with the user before applying.

    Returns: Confirmation with the time the timeout ends.
    """
    gid = require_guild(guild_id)
    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat() if minutes else None
    await request("PATCH", f"/guilds/{gid}/members/{user_id}", json={"communication_disabled_until": until}, reason=reason)
    if not until:
        return f"Removed timeout from user {user_id}."
    return f"Timed out user {user_id} until {fmt_time(until)} ({minutes} min)."


@mcp.tool(name="discord_kick_member", annotations=DESTRUCTIVE)
@tool_errors
async def discord_kick_member(user_id: Snowflake, guild_id: GuildId = None, reason: Reason = None) -> str:
    """Remove a member from the server. They can rejoin with an invite. Needs Kick Members.

    Confirm with the user before calling.

    Returns: Confirmation.
    """
    gid = require_guild(guild_id)
    await request("DELETE", f"/guilds/{gid}/members/{user_id}", reason=reason)
    return f"Kicked user {user_id}."


@mcp.tool(name="discord_ban_member", annotations=DESTRUCTIVE)
@tool_errors
async def discord_ban_member(
    user_id: Snowflake,
    guild_id: GuildId = None,
    delete_message_days: Annotated[int, Field(ge=0, le=7, description="Also delete this user's messages from the last N days (0–7).")] = 0,
    reason: Reason = None,
) -> str:
    """Ban a user from the server (they cannot rejoin until unbanned), optionally deleting their recent messages.

    Needs Ban Members. Confirm with the user before calling.

    Returns: Confirmation.
    """
    gid = require_guild(guild_id)
    await request("PUT", f"/guilds/{gid}/bans/{user_id}", json={"delete_message_seconds": delete_message_days * 86400}, reason=reason)
    extra = f", deleted their messages from the last {delete_message_days} day(s)" if delete_message_days else ""
    return f"Banned user {user_id}{extra}."


@mcp.tool(name="discord_unban_member", annotations=UPDATE)
@tool_errors
async def discord_unban_member(user_id: Snowflake, guild_id: GuildId = None, reason: Reason = None) -> str:
    """Lift a ban so the user can rejoin. Needs Ban Members.

    Returns: Confirmation.
    """
    gid = require_guild(guild_id)
    await request("DELETE", f"/guilds/{gid}/bans/{user_id}", reason=reason)
    return f"Unbanned user {user_id}."


@mcp.tool(name="discord_list_bans", annotations=READ)
@tool_errors
async def discord_list_bans(guild_id: GuildId = None, limit: Annotated[int, Field(ge=1, le=1000)] = 50, response_format: Fmt = MD) -> str:
    """List banned users and the reason recorded for each. Needs Ban Members.

    Returns: Markdown table (user, reason) or JSON list of {user_id, username, reason}.
    """
    gid = require_guild(guild_id)
    bans = await request("GET", f"/guilds/{gid}/bans", params={"limit": limit})
    if response_format == JSON:
        return to_json({"count": len(bans), "bans": [{"user_id": b["user"]["id"], "username": b["user"].get("username"), "reason": b.get("reason")} for b in bans]})
    if not bans:
        return "No banned users."
    return f"# Bans ({len(bans)})\n\n" + md_table(["User", "Reason"], [[user_label(b["user"]), b.get("reason") or "—"] for b in bans])


@mcp.tool(name="discord_bulk_delete_messages", annotations=DESTRUCTIVE)
@tool_errors
async def discord_bulk_delete_messages(
    channel_id: Snowflake,
    message_ids: Annotated[list[Snowflake], Field(min_length=2, max_length=100, description="2–100 message IDs, all younger than 14 days.")],
    reason: Reason = None,
) -> str:
    """Delete 2–100 messages from one channel in a single call. Irreversible. Needs Manage Messages.

    Discord refuses messages older than 14 days — delete those one at a time with
    discord_delete_message. Confirm with the user before calling.

    Returns: Confirmation with the count deleted.
    """
    ids = list(dict.fromkeys(message_ids))  # Discord rejects duplicates
    if len(ids) < 2:
        return "Error: need at least 2 distinct message IDs; use discord_delete_message for one."
    await request("POST", f"/channels/{channel_id}/messages/bulk-delete", json={"messages": ids}, reason=reason)
    return f"Deleted {len(ids)} messages from {channel_id}."


@mcp.tool(name="discord_get_audit_log", annotations=READ)
@tool_errors
async def discord_get_audit_log(
    guild_id: GuildId = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 25,
    user_id: Annotated[OptSnowflake, Field(description="Only actions performed by this user or bot.")] = None,
    action: Annotated[Optional[str], Field(description="Filter by action name, e.g. 'member_ban_add', 'message_delete', 'channel_create'.")] = None,
    response_format: Fmt = MD,
) -> str:
    """Read the server's audit log — who did what (bans, kicks, deletions, channel/role changes) and why.

    Needs View Audit Log. Use to answer "who deleted that channel" or "why was X banned".

    Returns: Markdown table (time, actor, action, target, reason) or JSON list of entries
    {id, time, actor{id, username}, action, target_id, reason, changes}.
    """
    gid = require_guild(guild_id)
    action_type = None
    if action:
        if action not in AUDIT_ACTION_IDS:
            return f"Error: unknown action '{action}'. Known: {', '.join(sorted(AUDIT_ACTION_IDS))}."
        action_type = AUDIT_ACTION_IDS[action]
    data = await request("GET", f"/guilds/{gid}/audit-logs", params={"limit": limit, "user_id": user_id, "action_type": action_type})
    users = {u["id"]: u for u in data.get("users", [])}
    entries = []
    for e in data.get("audit_log_entries", []):
        actor = users.get(e.get("user_id"), {"id": e.get("user_id"), "username": "?"})
        entries.append({
            "id": e["id"],
            "time": snowflake_time(e["id"]).isoformat(),
            "actor": {"id": actor.get("id"), "username": actor.get("username")},
            "action": AUDIT_ACTIONS.get(e.get("action_type"), str(e.get("action_type"))),
            "target_id": e.get("target_id"),
            "reason": e.get("reason"),
            "changes": e.get("changes"),
        })
    if response_format == JSON:
        return to_json({"count": len(entries), "entries": entries})
    if not entries:
        return "No audit log entries matched."
    rows = [[fmt_time(x["time"]), f"@{x['actor']['username']}", x["action"], x["target_id"] or "—", x["reason"] or "—"] for x in entries]
    return f"# Audit log ({len(entries)}, newest first)\n\n" + md_table(["Time", "Actor", "Action", "Target", "Reason"], rows)
