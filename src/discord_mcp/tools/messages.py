"""Reading and writing messages — the heart of the server.

Four things about Discord shape this module:

1. **Message Content is a privileged intent.** Unless it is switched on for the bot in the
   developer portal, Discord returns messages with `content` as an empty string — no
   error, just blank text. It applies to the HTTP API, not only to gateway events, so
   this affects every read tool here. If someone reports "all the messages are empty",
   that is always the cause.

2. **Bots cannot use Discord's search.** The `/guilds/{id}/messages/search` endpoint is
   available to user accounts only. `discord_search_messages` therefore pages backwards
   through history (100 messages per call, capped by `max_scan`) and filters locally.
   It is honest about what it scanned so the model can decide whether to look further.

3. **Mentions are opt-in here.** Discord pings whoever a message mentions unless the
   request says otherwise. Since the text often comes from a model, `discord_send_message`
   sends `allowed_mentions: {parse: []}` by default — a stray `@everyone` in generated
   copy then renders as plain text instead of notifying the whole server. Passing
   `allow_mentions=true` is a deliberate act.

4. **A bot can only edit its own messages.** It can *delete* other people's (with Manage
   Messages), but editing is restricted, which is why `discord_edit_message` says so in
   its description rather than letting the model discover it through a 50005 error.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import Field

from discord_mcp.client import encode_emoji, request
from discord_mcp.formatting import ResponseFormat, envelope, fmt_time, render_message_md, render_messages_md, slim_message, to_json, user_label
from discord_mcp.tools._common import DESTRUCTIVE, JSON, MD, READ, UPDATE, WRITE, Fmt, OptSnowflake, Reason, Snowflake, mcp, tool_errors

MAX_CONTENT = 2000
PAGE = 100

ChannelId = Annotated[Snowflake, Field(description="Channel, thread, or DM channel ID.")]
Content = Annotated[str, Field(min_length=1, max_length=MAX_CONTENT, description="Message text; Discord Markdown allowed, max 2000 chars.")]


def _render(messages: list[dict], fmt: ResponseFormat, title: str, *, limit: int) -> str:
    cursor = messages[-1]["id"] if messages else None
    if fmt == JSON:
        return to_json(envelope([slim_message(m) for m in messages], limit=limit, cursor=cursor))
    footer = f"_Older messages: pass before=`{cursor}`._" if messages and len(messages) >= limit else ""
    # Discord returns messages newest-first. A model summarising a conversation reads it
    # far better oldest-first, the way the channel actually looked, so flip it for the
    # Markdown view. JSON keeps Discord's order, since callers there may rely on it.
    return render_messages_md(title, reversed(messages), footer)


@mcp.tool(name="discord_read_messages", annotations=READ)
@tool_errors
async def discord_read_messages(
    channel_id: ChannelId,
    limit: Annotated[int, Field(ge=1, le=100, description="How many messages, newest first.")] = 25,
    before: Annotated[OptSnowflake, Field(description="Only messages older than this message ID — pass `next_before` from the previous page.")] = None,
    after: Annotated[OptSnowflake, Field(description="Only messages newer than this message ID.")] = None,
    around: Annotated[OptSnowflake, Field(description="Messages surrounding this message ID.")] = None,
    response_format: Fmt = MD,
) -> str:
    """Read recent messages from a channel or thread, newest first, with cursor pagination.

    Needs the Message Content intent enabled for the bot or every message reads as empty.
    Pass only one of before / after / around.

    Returns: Markdown — each message as author, time, ID, text, attachments, reactions,
    oldest at top; a footer gives the `before` cursor for the next page. JSON — envelope
    {count, limit, has_more, next_before, items:[{id, channel_id, author{id, username,
    display_name, bot}, content, timestamp, edited_timestamp, pinned, attachments?,
    embeds?, reactions?, reply_to?, thread_id?}]}.
    """
    if sum(x is not None for x in (before, after, around)) > 1:
        return "Error: pass only one of before, after, around."
    msgs = await request("GET", f"/channels/{channel_id}/messages", params={"limit": limit, "before": before, "after": after, "around": around})
    return _render(msgs, response_format, f"Messages in {channel_id}", limit=limit)


@mcp.tool(name="discord_get_message", annotations=READ)
@tool_errors
async def discord_get_message(channel_id: ChannelId, message_id: Snowflake, response_format: Fmt = MD) -> str:
    """Fetch one message by ID, including attachments, embeds and reactions.

    Returns: Markdown block or JSON message object (same shape as discord_read_messages items).
    """
    m = await request("GET", f"/channels/{channel_id}/messages/{message_id}")
    return to_json(slim_message(m)) if response_format == JSON else render_message_md(m)


@mcp.tool(name="discord_search_messages", annotations=READ)
@tool_errors
async def discord_search_messages(
    channel_id: ChannelId,
    query: Annotated[str, Field(min_length=1, max_length=200, description="Case-insensitive substring to look for in message text.")],
    author_id: Annotated[OptSnowflake, Field(description="Only messages by this user.")] = None,
    max_scan: Annotated[int, Field(ge=1, le=2000, description="How many recent messages to scan, newest first. Larger = slower.")] = 500,
    limit: Annotated[int, Field(ge=1, le=100, description="Max matches to return.")] = 20,
    response_format: Fmt = MD,
) -> str:
    """Find messages in a channel containing a phrase, optionally by one author.

    Discord's search API is not available to bots, so this scans the most recent
    `max_scan` messages page by page (100 per API call) and filters locally. For "what
    did X say about Y last week" this is the tool; for older history raise max_scan.

    Returns: Matching messages (Markdown or JSON, same shape as discord_read_messages) plus
    how many messages were scanned and the oldest one reached.
    """
    needle = query.lower()
    matches: list[dict] = []
    scanned = 0
    cursor: str | None = None
    oldest: dict | None = None
    while scanned < max_scan and len(matches) < limit:
        page = await request("GET", f"/channels/{channel_id}/messages", params={"limit": min(PAGE, max_scan - scanned), "before": cursor})
        if not page:
            break
        scanned += len(page)
        oldest = page[-1]
        cursor = page[-1]["id"]
        for m in page:
            if author_id and m.get("author", {}).get("id") != author_id:
                continue
            if needle in (m.get("content") or "").lower():
                matches.append(m)
                if len(matches) >= limit:
                    break
        if len(page) < PAGE:
            break
    reached = f"scanned {scanned} messages back to {fmt_time(oldest['timestamp']) if oldest else 'n/a'}"
    if response_format == JSON:
        return to_json({"query": query, "scanned": scanned, "count": len(matches), "items": [slim_message(m) for m in matches]})
    if not matches:
        return f"No messages containing '{query}' — {reached}. Raise max_scan to look further back."
    return render_messages_md(f"{len(matches)} match(es) for '{query}'", reversed(matches), f"_{reached}._")


@mcp.tool(name="discord_send_message", annotations=WRITE)
@tool_errors
async def discord_send_message(
    channel_id: ChannelId,
    content: Content,
    reply_to: Annotated[OptSnowflake, Field(description="Message ID to reply to.")] = None,
    mention_reply_author: Annotated[bool, Field(description="When replying, ping the original author.")] = False,
    allow_mentions: Annotated[bool, Field(description="Let @user/@role/@everyone in the text actually ping. Off by default to avoid accidental mass pings.")] = False,
) -> str:
    """Post a message to a channel, thread, or DM channel as the bot. Optionally as a reply.

    Visible to everyone in the channel immediately — confirm wording with the user first.
    Mentions do not ping unless allow_mentions is true.

    Returns: Confirmation with the new message ID and a jump link.
    """
    body: dict = {
        "content": content,
        # An empty `parse` list is what makes mentions inert: @everyone in the text still
        # renders, but nobody is notified. See the module docstring.
        "allowed_mentions": {"parse": ["users", "roles", "everyone"] if allow_mentions else [], "replied_user": mention_reply_author},
    }
    if reply_to:
        # fail_if_not_exists=False: if the message being replied to was deleted meanwhile,
        # post it as a normal message rather than erroring out.
        body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
    m = await request("POST", f"/channels/{channel_id}/messages", json=body)
    guild = m.get("guild_id") or "@me"
    return f"Sent message {m['id']} in {channel_id}. https://discord.com/channels/{guild}/{channel_id}/{m['id']}"


@mcp.tool(name="discord_edit_message", annotations=UPDATE)
@tool_errors
async def discord_edit_message(
    channel_id: ChannelId,
    message_id: Annotated[Snowflake, Field(description="Must be a message the bot itself sent.")],
    content: Content,
) -> str:
    """Replace the text of a message the bot sent. Cannot edit other users' messages.

    Returns: Confirmation.
    """
    await request("PATCH", f"/channels/{channel_id}/messages/{message_id}", json={"content": content})
    return f"Edited message {message_id}."


@mcp.tool(name="discord_delete_message", annotations=DESTRUCTIVE)
@tool_errors
async def discord_delete_message(channel_id: ChannelId, message_id: Snowflake, reason: Reason = None) -> str:
    """Delete one message. Deleting others' messages needs Manage Messages. Irreversible.

    For 2–100 recent messages at once use discord_bulk_delete_messages.

    Returns: Confirmation.
    """
    await request("DELETE", f"/channels/{channel_id}/messages/{message_id}", reason=reason)
    return f"Deleted message {message_id}."


@mcp.tool(name="discord_react", annotations=UPDATE)
@tool_errors
async def discord_react(
    channel_id: ChannelId,
    message_id: Snowflake,
    emoji: Annotated[str, Field(min_length=1, max_length=100, description="Unicode emoji like '👍', or a custom emoji as 'name:id' or '<:name:id>'.")],
    action: Literal["add", "remove"] = "add",
) -> str:
    """Add or remove the bot's own reaction on a message.

    Returns: Confirmation.
    """
    path = f"/channels/{channel_id}/messages/{message_id}/reactions/{encode_emoji(emoji)}/@me"
    await request("PUT" if action == "add" else "DELETE", path)
    return f"{'Added' if action == 'add' else 'Removed'} reaction {emoji} on {message_id}."


@mcp.tool(name="discord_list_pins", annotations=READ)
@tool_errors
async def discord_list_pins(channel_id: ChannelId, response_format: Fmt = MD) -> str:
    """List the pinned messages in a channel (max 50).

    Returns: Markdown messages or JSON list, same shape as discord_read_messages items.
    """
    pins = await request("GET", f"/channels/{channel_id}/pins")
    if response_format == JSON:
        return to_json({"count": len(pins), "items": [slim_message(m) for m in pins]})
    return render_messages_md(f"Pinned in {channel_id} ({len(pins)})", pins)


@mcp.tool(name="discord_pin_message", annotations=UPDATE)
@tool_errors
async def discord_pin_message(channel_id: ChannelId, message_id: Snowflake, action: Literal["pin", "unpin"] = "pin", reason: Reason = None) -> str:
    """Pin or unpin a message in its channel. Needs Manage Messages.

    Returns: Confirmation.
    """
    await request("PUT" if action == "pin" else "DELETE", f"/channels/{channel_id}/pins/{message_id}", reason=reason)
    return f"{'Pinned' if action == 'pin' else 'Unpinned'} message {message_id}."


@mcp.tool(name="discord_send_dm", annotations=WRITE)
@tool_errors
async def discord_send_dm(
    user_id: Annotated[Snowflake, Field(description="User to message. They must share a server with the bot and allow DMs from it.")],
    content: Content,
) -> str:
    """Send a direct message from the bot to a user.

    Fails if the user has DMs from server members/bots disabled. Confirm with the user
    before sending — DMs feel more personal than channel posts.

    Returns: Confirmation with the DM channel ID (reuse it with discord_read_messages to see replies).
    """
    # DMs need a channel first. This endpoint is idempotent — calling it again for the
    # same user returns the existing DM channel rather than creating a second one.
    dm = await request("POST", "/users/@me/channels", json={"recipient_id": user_id})
    m = await request("POST", f"/channels/{dm['id']}/messages", json={"content": content, "allowed_mentions": {"parse": []}})
    recipient = next(iter(dm.get("recipients", [])), {"id": user_id})
    return f"Sent DM {m['id']} to {user_label(recipient)} (DM channel {dm['id']})."
