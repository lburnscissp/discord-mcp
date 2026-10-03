"""Turning Discord's JSON into something a language model reads well.

Two output shapes, one job
-------------------------
Every read tool takes `response_format`:

  * **markdown** (default) — compact, human-shaped prose and tables. Timestamps are
    rendered as readable UTC, IDs are shown next to names so the model can use them in a
    follow-up call, and noisy metadata is dropped. This is what you want when the answer
    is going to be summarised or shown to a person.
  * **json** — the full structured object, for when the model needs to filter, count or
    feed the data into something else.

Why the `slim_*` functions exist
--------------------------------
A single Discord message object is ~40 fields deep once you include the author, member,
attachments, embeds, reactions, mentions and flags. Twenty-five of those in a tool
response is thousands of tokens of mostly-null noise, and context is the scarcest
resource an agent has. Each `slim_*` keeps the fields that answer real questions and
drops the rest. If you add a tool and find yourself needing a dropped field, add it to
the relevant `slim_*` rather than returning the raw object.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable

# Discord's epoch: 2015-01-01T00:00:00Z in milliseconds. Snowflake IDs count from here
# rather than from the Unix epoch, which is why `snowflake_time` adds it back.
DISCORD_EPOCH_MS = 1420070400000

# Discord sends channel kinds as integers. These names are ours (they match Discord's
# documented constants, lowercased) and appear in both tool arguments and output, so a
# model can say type="forum" instead of type=15.
# The gaps in the numbering are Discord's: 6-9 were removed over the years.
CHANNEL_TYPES: dict[int, str] = {
    0: "text",
    1: "dm",
    2: "voice",
    3: "group_dm",
    4: "category",
    5: "announcement",
    10: "announcement_thread",
    11: "public_thread",
    12: "private_thread",
    13: "stage",
    14: "directory",
    15: "forum",
    16: "media",
}
# Reverse map, for turning a tool argument back into the integer Discord wants.
CHANNEL_TYPE_IDS: dict[str, int] = {v: k for k, v in CHANNEL_TYPES.items()}


# Discord message "types". Most are system notices Discord posts on your behalf — someone
# joined, a channel was renamed, a message was pinned — and they legitimately carry no
# text. Only the types in CONTENT_MESSAGE_TYPES are things a person actually wrote.
# Distinguishing them matters: three join notices in a channel should not look like three
# empty messages. Full list:
# https://discord.com/developers/docs/resources/message#message-object-message-types
MESSAGE_TYPES: dict[int, str] = {
    0: "default",
    1: "recipient_add",
    2: "recipient_remove",
    3: "call",
    4: "channel_name_change",
    5: "channel_icon_change",
    6: "channel_pinned_message",
    7: "user_join",
    8: "guild_boost",
    9: "guild_boost_tier_1",
    10: "guild_boost_tier_2",
    11: "guild_boost_tier_3",
    12: "channel_follow_add",
    18: "thread_created",
    19: "reply",
    20: "chat_input_command",
    21: "thread_starter_message",
    22: "guild_invite_reminder",
    23: "context_menu_command",
    24: "auto_moderation_action",
    25: "role_subscription_purchase",
    31: "guild_incident_alert_mode_enabled",
    32: "guild_incident_alert_mode_disabled",
    46: "poll_result",
}

# Message types whose text was typed by a human (or a bot acting like one).
CONTENT_MESSAGE_TYPES = frozenset({0, 19, 20, 21, 23})

# Human-readable renderings for the system notices people actually see in a channel.
SYSTEM_MESSAGE_LABELS: dict[int, str] = {
    6: "pinned a message to this channel",
    7: "joined the server",
    8: "boosted the server",
    9: "boosted the server (tier 1)",
    10: "boosted the server (tier 2)",
    11: "boosted the server (tier 3)",
    12: "followed another channel into this one",
    18: "created a thread",
    4: "changed the channel name",
    5: "changed the channel icon",
    22: "invite reminder",
    24: "message blocked by AutoMod",
    25: "purchased a role subscription",
    46: "poll ended",
}


def is_system_message(m: dict[str, Any]) -> bool:
    """True when Discord generated this message, not a person.

    System messages have no `content` by design, so they must be told apart from a real
    message whose text is missing — which usually means the Message Content Intent is off.
    """
    return m.get("type", 0) not in CONTENT_MESSAGE_TYPES


def empty_content_note(m: dict[str, Any]) -> str:
    """Explain why a message has no text, instead of leaving the reader guessing.

    Three different situations produce an empty `content`, and conflating them sent at
    least one person hunting for a bug that wasn't there:

      * a system notice (someone joined) — never had text;
      * a message that really is just an image or a link embed;
      * **the Message Content Intent is disabled**, in which case Discord blanks the text,
        the attachments and the embeds all at once, with no error anywhere.

    The third is the common setup mistake, so when a human-authored message arrives with
    nothing in it at all, say so plainly rather than reporting it as an empty message.
    """
    if is_system_message(m):
        label = SYSTEM_MESSAGE_LABELS.get(m.get("type", 0), MESSAGE_TYPES.get(m.get("type", 0), "system message"))
        return f"_(system: {label})_"
    if not (m.get("attachments") or m.get("embeds")):
        return "_(no text, no attachments — the bot's Message Content Intent is probably disabled; see README troubleshooting)_"
    return "_(no text — see attachments/embeds below)_"


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


def snowflake_time(snowflake: str | int) -> datetime:
    """Extract the creation time baked into any Discord ID.

    Discord IDs ("snowflakes") are 64-bit integers whose top 42 bits are a millisecond
    timestamp. That means *every* object — message, channel, server, user — tells you when
    it was created without an extra API call, and that IDs sort chronologically. This is
    also why cursor pagination works on IDs (see `client.paginate`).
    """
    ms = (int(snowflake) >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def fmt_time(value: str | datetime | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def to_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def envelope(items: list[Any], *, limit: int, cursor: str | None, cursor_name: str = "before") -> dict[str, Any]:
    """Wrap a page of results with the metadata needed to ask for the next one.

    Discord paginates by cursor, not offset, so "page 3" is not a thing you can request —
    you can only continue from the last ID you saw. The envelope therefore reports the
    cursor to pass next (`next_before` / `next_after`) rather than an offset.

    `has_more` is a best guess: a full page usually means more exist, but a page that is
    exactly the last page also looks full. Following the cursor once more costs one call
    and settles it, which is cheaper than Discord offering a total count (it doesn't).
    """
    return {
        "count": len(items),
        "limit": limit,
        "has_more": len(items) >= limit,
        f"next_{cursor_name}": cursor if len(items) >= limit else None,
        "items": items,
    }


# --- entity renderers ---------------------------------------------------------

def user_label(user: dict[str, Any]) -> str:
    """`display (@username, ID)` — humans read the first, agents need the last."""
    name = user.get("global_name") or user.get("username", "?")
    tag = user.get("username", "?")
    bot = " [bot]" if user.get("bot") else ""
    return f"{name} (@{tag}, {user.get('id')}){bot}"


def member_label(member: dict[str, Any]) -> str:
    user = member.get("user", {})
    nick = member.get("nick")
    base = user_label(user)
    return f"{nick} — {base}" if nick else base


def channel_label(channel: dict[str, Any]) -> str:
    kind = CHANNEL_TYPES.get(channel.get("type", -1), f"type{channel.get('type')}")
    return f"#{channel.get('name', '?')} ({kind}, {channel.get('id')})"


def slim_message(m: dict[str, Any]) -> dict[str, Any]:
    """The fields an agent actually needs from a message object."""
    out: dict[str, Any] = {
        "id": m.get("id"),
        "channel_id": m.get("channel_id"),
        # A caller filtering for real conversation wants to drop the join notices.
        "type": MESSAGE_TYPES.get(m.get("type", 0), m.get("type")),
        "system": is_system_message(m),
        "author": {
            "id": m.get("author", {}).get("id"),
            "username": m.get("author", {}).get("username"),
            "display_name": m.get("author", {}).get("global_name"),
            "bot": bool(m.get("author", {}).get("bot")),
        },
        "content": m.get("content", ""),
        "timestamp": m.get("timestamp"),
        "edited_timestamp": m.get("edited_timestamp"),
        "pinned": m.get("pinned", False),
    }
    if m.get("attachments"):
        out["attachments"] = [{"filename": a.get("filename"), "url": a.get("url"), "size": a.get("size")} for a in m["attachments"]]
    if m.get("embeds"):
        out["embeds"] = [{"title": e.get("title"), "description": e.get("description"), "url": e.get("url")} for e in m["embeds"]]
    if m.get("reactions"):
        out["reactions"] = [{"emoji": r["emoji"].get("name"), "count": r.get("count")} for r in m["reactions"]]
    ref = m.get("message_reference")
    if ref:
        out["reply_to"] = ref.get("message_id")
    if m.get("thread"):
        out["thread_id"] = m["thread"].get("id")
    return out


def render_message_md(m: dict[str, Any]) -> str:
    author = user_label(m.get("author", {}))
    lines = [f"**{author}** · {fmt_time(m.get('timestamp'))} · `{m.get('id')}`"]
    if m.get("message_reference"):
        lines[0] += f" · reply to `{m['message_reference'].get('message_id')}`"
    if m.get("pinned"):
        lines[0] += " · 📌"
    content = m.get("content") or ""
    lines.append(content if content else empty_content_note(m))
    for a in m.get("attachments") or []:
        lines.append(f"  📎 {a.get('filename')} — {a.get('url')}")
    for e in m.get("embeds") or []:
        title = e.get("title") or e.get("url") or "embed"
        lines.append(f"  🔗 {title}" + (f": {e.get('description')[:200]}" if e.get("description") else ""))
    if m.get("reactions"):
        lines.append("  " + "  ".join(f"{r['emoji'].get('name')} {r.get('count')}" for r in m["reactions"]))
    return "\n".join(lines)


def render_messages_md(title: str, messages: Iterable[dict[str, Any]], footer: str = "") -> str:
    body = "\n\n".join(render_message_md(m) for m in messages)
    return f"# {title}\n\n{body or '_No messages._'}" + (f"\n\n{footer}" if footer else "")


def slim_channel(c: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": c.get("id"),
        "name": c.get("name"),
        "type": CHANNEL_TYPES.get(c.get("type", -1), c.get("type")),
        "parent_id": c.get("parent_id"),
        "position": c.get("position"),
    }
    for key in ("topic", "nsfw", "rate_limit_per_user", "last_message_id", "owner_id", "message_count", "member_count"):
        if c.get(key) not in (None, "", False):
            out[key] = c[key]
    if c.get("thread_metadata"):
        out["archived"] = c["thread_metadata"].get("archived")
        out["locked"] = c["thread_metadata"].get("locked")
    return out


def slim_role(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "color": f"#{r.get('color', 0):06x}",
        "position": r.get("position"),
        "hoist": r.get("hoist"),
        "mentionable": r.get("mentionable"),
        "managed": r.get("managed"),
        "permissions": r.get("permissions"),
    }


def slim_member(m: dict[str, Any]) -> dict[str, Any]:
    user = m.get("user", {})
    return {
        "user_id": user.get("id"),
        "username": user.get("username"),
        "display_name": user.get("global_name"),
        "nick": m.get("nick"),
        "bot": bool(user.get("bot")),
        "roles": m.get("roles", []),
        "joined_at": m.get("joined_at"),
        "timed_out_until": m.get("communication_disabled_until"),
        "pending": m.get("pending", False),
    }


def slim_guild(g: dict[str, Any]) -> dict[str, Any]:
    out = {"id": g.get("id"), "name": g.get("name"), "owner_id": g.get("owner_id")}
    for key in ("description", "approximate_member_count", "approximate_presence_count", "preferred_locale", "verification_level", "premium_tier"):
        if g.get(key) is not None:
            out[key] = g[key]
    if g.get("id"):
        out["created_at"] = snowflake_time(g["id"]).isoformat()
    return out


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join("| " + " | ".join(str(c) if c is not None else "—" for c in row) + " |" for row in rows)
    return f"{head}\n{sep}\n{body}" if rows else "_None._"
