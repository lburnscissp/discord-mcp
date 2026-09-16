"""Thin async client for Discord's REST API (v10).

One module-level client, bot-token auth from the environment, automatic retry on
429 rate limits, and Discord error bodies turned into messages an agent can act on.
Nothing here writes to stdout — stdio is the MCP transport.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

from discord_mcp import __version__

log = logging.getLogger("discord_mcp")

API_BASE = "https://discord.com/api/v10"
USER_AGENT = f"DiscordBot (https://github.com/divergentfreq/discord-mcp, {__version__})"
REQUEST_TIMEOUT = 30.0
MAX_RATE_LIMIT_RETRIES = 3

# Discord JSON error codes worth translating into something actionable.
_ERROR_HINTS: dict[int, str] = {
    10003: "Unknown channel. Check the channel ID with discord_list_channels.",
    10004: "Unknown guild. Check the guild ID with discord_list_guilds.",
    10007: "Unknown member. The user may not be in this server — try discord_search_members.",
    10008: "Unknown message. It may have been deleted, or the channel ID is wrong.",
    10011: "Unknown role. Check the role ID with discord_list_roles.",
    10013: "Unknown user. Check the user ID.",
    10026: "Unknown ban. The user is not banned.",
    20028: "Rate limited on this resource. Wait a moment and retry.",
    30007: "Channel limit reached (500 per server).",
    30013: "Message pin limit reached (50 per channel).",
    40005: "Request entity too large.",
    50001: "Missing access. The bot is not in this server or can't see this channel.",
    50005: "Cannot edit a message authored by another user. Bots can only edit their own messages.",
    50013: "Missing permissions. Give the bot's role the needed permission in Server Settings → Roles.",
    50021: "Cannot execute this action on a system message.",
    50024: "Cannot execute this action on this channel type.",
    50034: "Bulk delete only works on messages younger than 14 days. Delete older ones one at a time.",
    50035: "Invalid form body. One of the fields is malformed — see the details.",
    50083: "Thread is archived. Unarchive it with discord_edit_channel first.",
    160002: "Cannot reply without permission to read message history.",
}


class DiscordError(Exception):
    """A Discord API call failed. `message` is safe to show the agent."""

    def __init__(self, message: str, status: int | None = None, code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def _load_env() -> None:
    # Search from the package root upward so `.env` next to pyproject.toml is found
    # regardless of the cwd the MCP client launches us from.
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        candidate = os.path.join(here, ".env")
        if os.path.exists(candidate):
            load_dotenv(candidate)
            return
        here = os.path.dirname(here)
    load_dotenv()  # fall back to cwd


_load_env()


def bot_token() -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise DiscordError(
            "DISCORD_BOT_TOKEN is not set. Put it in the .env file next to pyproject.toml "
            "(see .env.example) and restart the MCP server."
        )
    return token


def default_guild_id() -> str | None:
    value = os.environ.get("DISCORD_GUILD_ID", "").strip()
    return value or None


def require_guild(guild_id: str | None) -> str:
    """Resolve a guild ID from the argument or DISCORD_GUILD_ID."""
    resolved = guild_id or default_guild_id()
    if not resolved:
        raise DiscordError(
            "No guild_id given and DISCORD_GUILD_ID is not set in .env. "
            "Pass guild_id explicitly, or run discord_list_guilds to find it."
        )
    return resolved


def encode_emoji(emoji: str) -> str:
    """Reaction endpoints want unicode emoji percent-encoded and custom emoji as `name:id`.

    Accepts a bare unicode emoji, `name:id`, or the chat form `<:name:id>` / `<a:name:id>`.
    """
    e = emoji.strip()
    if e.startswith("<") and e.endswith(">"):
        e = e[1:-1]
        if e.startswith("a:"):
            e = e[2:]
        return e.lstrip(":")
    return quote(e, safe=":")


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
    return _client


def _error_from_response(response: httpx.Response) -> DiscordError:
    status = response.status_code
    code: int | None = None
    detail = ""
    try:
        body = response.json()
        code = body.get("code")
        detail = body.get("message", "")
        if "errors" in body:
            detail += f" Details: {_flatten_errors(body['errors'])}"
    except ValueError:
        detail = response.text[:300]

    if status == 401:
        msg = "Invalid bot token. Check DISCORD_BOT_TOKEN in .env and reset the token in the developer portal if needed."
    elif code in _ERROR_HINTS:
        msg = _ERROR_HINTS[code]
        if code == 50035 and detail:
            msg += f" {detail}"
    elif status == 403:
        msg = f"Forbidden: {detail or 'the bot lacks permission for this action.'}"
    elif status == 404:
        msg = f"Not found: {detail or 'check the IDs.'}"
    else:
        msg = f"Discord API error {status}: {detail or 'no details.'}"
    return DiscordError(msg, status=status, code=code)


def _flatten_errors(errors: Any, path: str = "") -> str:
    """Discord nests validation errors by field; flatten to `field: message`."""
    parts: list[str] = []
    if isinstance(errors, dict):
        if "_errors" in errors:
            parts.append(f"{path or 'body'}: " + "; ".join(e.get("message", "") for e in errors["_errors"]))
        for key, value in errors.items():
            if key != "_errors":
                parts.append(_flatten_errors(value, f"{path}.{key}" if path else key))
    return " ".join(p for p in parts if p)


async def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: Any | None = None,
    reason: str | None = None,
) -> Any:
    """Call the Discord API and return the parsed JSON body (None for 204).

    `reason` is sent as X-Audit-Log-Reason so moderation actions show up in the
    server's audit log with context.
    """
    headers = {"Authorization": f"Bot {bot_token()}"}
    if reason:
        headers["X-Audit-Log-Reason"] = quote(reason[:512])

    client = _get_client()
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            response = await client.request(method, path, params=clean_params, json=json, headers=headers)
        except httpx.TimeoutException as e:
            raise DiscordError("Discord API request timed out. Try again.") from e
        except httpx.HTTPError as e:
            raise DiscordError(f"Network error talking to Discord: {type(e).__name__}.") from e

        if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
            try:
                wait = float(response.json().get("retry_after", 1.0))
            except ValueError:
                wait = float(response.headers.get("Retry-After", "1"))
            log.warning("rate limited on %s %s, sleeping %.2fs", method, path, wait)
            await asyncio.sleep(min(wait, 10.0))
            continue

        if response.status_code >= 400:
            raise _error_from_response(response)

        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    raise DiscordError("Rate limited repeatedly. Wait a minute and retry.")


async def paginate(path: str, *, params: dict[str, Any] | None = None, limit: int, page_size: int = 100, cursor_key: str = "after") -> list[dict]:
    """Walk an `after`/`before`-cursor endpoint until `limit` items are collected."""
    items: list[dict] = []
    cursor: str | None = None
    while len(items) < limit:
        page_params = dict(params or {})
        page_params["limit"] = min(page_size, limit - len(items))
        if cursor:
            page_params[cursor_key] = cursor
        page = await request("GET", path, params=page_params)
        if not page:
            break
        items.extend(page)
        if len(page) < page_params["limit"]:
            break
        cursor = page[-1]["id"]
    return items[:limit]
