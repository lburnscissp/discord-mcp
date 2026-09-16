"""Shared formatting: snowflake timestamps, Markdown/JSON renderers, pagination envelopes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable

DISCORD_EPOCH_MS = 1420070400000

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
CHANNEL_TYPE_IDS: dict[str, int] = {v: k for k, v in CHANNEL_TYPES.items()}


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


def snowflake_time(snowflake: str | int) -> datetime:
    """Every Discord ID encodes its creation time."""
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
    """Pagination metadata shared by every list tool.

    Discord paginates by snowflake cursor, not offset, so we return the next cursor
    the caller should pass and whether more is likely.
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
    lines.append(content if content else "_(no text — see attachments/embeds)_")
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
