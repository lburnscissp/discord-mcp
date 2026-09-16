"""Channel and thread tools: list, inspect, create, edit, delete."""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import Field

from discord_mcp.client import request, require_guild
from discord_mcp.formatting import CHANNEL_TYPE_IDS, CHANNEL_TYPES, channel_label, md_table, slim_channel, to_json
from discord_mcp.tools._common import DESTRUCTIVE, JSON, MD, READ, UPDATE, WRITE, Fmt, GuildId, OptSnowflake, Reason, Snowflake, mcp, tool_errors

ChannelKind = Literal["text", "voice", "category", "announcement", "forum", "stage", "media"]
CreatableKind = Literal["text", "voice", "category", "announcement", "forum", "stage"]


def _channel_rows(channels: list[dict]) -> str:
    # Group by category so the listing reads like Discord's sidebar.
    by_id = {c["id"]: c for c in channels}
    channels = sorted(channels, key=lambda c: (c.get("position", 0), c["id"]))
    rows = []
    for c in channels:
        parent = by_id.get(c.get("parent_id") or "", {}).get("name")
        rows.append([c.get("name"), CHANNEL_TYPES.get(c.get("type"), c.get("type")), c["id"], parent or "—", (c.get("topic") or "")[:60]])
    return md_table(["Name", "Type", "ID", "Category", "Topic"], rows)


@mcp.tool(name="discord_list_channels", annotations=READ)
@tool_errors
async def discord_list_channels(
    guild_id: GuildId = None,
    type: Annotated[Optional[ChannelKind], Field(description="Only return channels of this kind.")] = None,
    name_contains: Annotated[Optional[str], Field(max_length=100, description="Case-insensitive substring filter on the name, e.g. 'general'.")] = None,
    response_format: Fmt = MD,
) -> str:
    """List a server's channels and categories, optionally filtered by kind or name.

    The usual way to find a channel_id before reading or posting. Threads are not
    included — use discord_list_threads.

    Returns: Markdown table (name, type, ID, category, topic) or JSON list of channel objects
    with id, name, type, parent_id, position, topic.
    """
    gid = require_guild(guild_id)
    channels = await request("GET", f"/guilds/{gid}/channels")
    if type:
        channels = [c for c in channels if c.get("type") == CHANNEL_TYPE_IDS[type]]
    if name_contains:
        needle = name_contains.lower()
        channels = [c for c in channels if needle in (c.get("name") or "").lower()]
    if response_format == JSON:
        return to_json({"count": len(channels), "channels": [slim_channel(c) for c in channels]})
    if not channels:
        return "No channels matched." + (" Try without the filters." if (type or name_contains) else "")
    return f"# Channels ({len(channels)})\n\n" + _channel_rows(channels)


@mcp.tool(name="discord_get_channel", annotations=READ)
@tool_errors
async def discord_get_channel(
    channel_id: Annotated[Snowflake, Field(description="Channel or thread ID.")],
    response_format: Fmt = MD,
) -> str:
    """Get one channel or thread: type, topic, parent, slowmode, and for threads the archive/lock state.

    Returns: Markdown bullet list or JSON channel object.
    """
    c = await request("GET", f"/channels/{channel_id}")
    data = slim_channel(c)
    if response_format == JSON:
        return to_json(data)
    return "\n".join([f"# {channel_label(c)}"] + [f"- {k}: {v}" for k, v in data.items() if k not in ("id", "name", "type")])


@mcp.tool(name="discord_create_channel", annotations=WRITE)
@tool_errors
async def discord_create_channel(
    name: Annotated[str, Field(min_length=1, max_length=100, description="Discord lowercases and hyphenates text channel names.")],
    guild_id: GuildId = None,
    type: Annotated[CreatableKind, Field(description="Channel kind.")] = "text",
    topic: Annotated[Optional[str], Field(max_length=1024, description="Channel topic (text channels only).")] = None,
    parent_id: Annotated[OptSnowflake, Field(description="Category ID to place the channel under.")] = None,
    nsfw: bool = False,
    reason: Reason = None,
) -> str:
    """Create a channel or category in a server. Needs the Manage Channels permission.

    Returns: Confirmation with the new channel's label and ID.
    """
    gid = require_guild(guild_id)
    body: dict = {"name": name, "type": CHANNEL_TYPE_IDS[type], "nsfw": nsfw}
    if topic:
        body["topic"] = topic
    if parent_id:
        body["parent_id"] = parent_id
    c = await request("POST", f"/guilds/{gid}/channels", json=body, reason=reason)
    return f"Created {channel_label(c)}."


@mcp.tool(name="discord_edit_channel", annotations=UPDATE)
@tool_errors
async def discord_edit_channel(
    channel_id: Annotated[Snowflake, Field(description="Channel or thread ID.")],
    name: Annotated[Optional[str], Field(min_length=1, max_length=100)] = None,
    topic: Annotated[Optional[str], Field(max_length=1024)] = None,
    parent_id: Annotated[OptSnowflake, Field(description="Move under this category.")] = None,
    position: Annotated[Optional[int], Field(ge=0, description="Sort position within its category.")] = None,
    nsfw: Optional[bool] = None,
    slowmode_seconds: Annotated[Optional[int], Field(ge=0, le=21600, description="Per-user message cooldown; 0 disables.")] = None,
    archived: Annotated[Optional[bool], Field(description="Threads only: archive (true) or unarchive (false).")] = None,
    locked: Annotated[Optional[bool], Field(description="Threads only: lock so only moderators can unarchive.")] = None,
    reason: Reason = None,
) -> str:
    """Rename, retopic, move, set slowmode on a channel — or archive/lock a thread.

    Only the fields you pass are changed. Needs Manage Channels (Manage Threads for threads).

    Returns: Confirmation listing the fields changed.
    """
    given = {
        "name": name, "topic": topic, "parent_id": parent_id, "position": position, "nsfw": nsfw,
        "rate_limit_per_user": slowmode_seconds, "archived": archived, "locked": locked,
    }
    body = {k: v for k, v in given.items() if v is not None}
    if not body:
        return "Error: nothing to change — pass at least one field besides channel_id."
    c = await request("PATCH", f"/channels/{channel_id}", json=body, reason=reason)
    return f"Updated {channel_label(c)}: changed {', '.join(body)}."


@mcp.tool(name="discord_delete_channel", annotations=DESTRUCTIVE)
@tool_errors
async def discord_delete_channel(
    channel_id: Annotated[Snowflake, Field(description="Channel or thread ID to delete. Cannot be undone.")],
    reason: Reason = None,
) -> str:
    """Permanently delete a channel or thread and every message in it. Irreversible.

    Confirm with the user before calling. Needs Manage Channels.

    Returns: Confirmation naming what was deleted.
    """
    c = await request("DELETE", f"/channels/{channel_id}", reason=reason)
    return f"Deleted {channel_label(c)}."


@mcp.tool(name="discord_list_threads", annotations=READ)
@tool_errors
async def discord_list_threads(
    guild_id: GuildId = None,
    channel_id: Annotated[OptSnowflake, Field(description="Only threads whose parent is this channel.")] = None,
    response_format: Fmt = MD,
) -> str:
    """List active (unarchived) threads in a server, optionally only under one channel.

    Returns: Markdown table (name, ID, parent, messages, members) or JSON list.
    Archived threads are not included.
    """
    gid = require_guild(guild_id)
    data = await request("GET", f"/guilds/{gid}/threads/active")
    threads = data.get("threads", [])
    if channel_id:
        threads = [t for t in threads if t.get("parent_id") == channel_id]
    if response_format == JSON:
        return to_json({"count": len(threads), "threads": [slim_channel(t) for t in threads]})
    if not threads:
        return "No active threads."
    rows = [[t.get("name"), t["id"], t.get("parent_id"), t.get("message_count"), t.get("member_count")] for t in threads]
    return f"# Active threads ({len(threads)})\n\n" + md_table(["Name", "ID", "Parent channel", "Messages", "Members"], rows)


@mcp.tool(name="discord_create_thread", annotations=WRITE)
@tool_errors
async def discord_create_thread(
    channel_id: Annotated[Snowflake, Field(description="Text/announcement channel to create the thread in.")],
    name: Annotated[str, Field(min_length=1, max_length=100)],
    message_id: Annotated[OptSnowflake, Field(description="Start the thread from this message. Omit for a standalone thread.")] = None,
    private: Annotated[bool, Field(description="Standalone threads only: make it invite-only.")] = False,
    auto_archive_minutes: Annotated[Literal[60, 1440, 4320, 10080], Field(description="Auto-archive after this much inactivity.")] = 1440,
    reason: Reason = None,
) -> str:
    """Start a thread — from an existing message, or standalone in a channel.

    Returns: Confirmation with the thread ID. Post into it with discord_send_message using
    the thread ID as channel_id.
    """
    body: dict = {"name": name, "auto_archive_duration": auto_archive_minutes}
    if message_id:
        path = f"/channels/{channel_id}/messages/{message_id}/threads"
    else:
        path = f"/channels/{channel_id}/threads"
        body["type"] = CHANNEL_TYPE_IDS["private_thread" if private else "public_thread"]
    t = await request("POST", path, json=body, reason=reason)
    return f"Created thread {channel_label(t)}."
