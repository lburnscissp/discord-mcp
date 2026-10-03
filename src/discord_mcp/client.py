"""Everything that talks to Discord's HTTP API.

Why REST and not a gateway connection
-------------------------------------
Most Discord bots open a persistent WebSocket ("gateway") connection and react to events
as they arrive. This server deliberately does not. An MCP server is a request/response
tool provider: the client (Claude, an IDE, a script) asks a question, we answer it, and
nothing needs to be remembered in between. Discord exposes almost everything a bot can do
over plain HTTP, so skipping the gateway buys us:

  * a process that starts in milliseconds and costs nothing while idle,
  * no reconnect/resume/sharding logic to get wrong,
  * no dependency on discord.py or any other framework — just httpx.

The trade-off: we cannot be *notified* of things (a new message, someone joining). We can
only look. If you ever need push behaviour, that is a second, separate program — don't
bolt a gateway onto this one.

What lives here
---------------
`request()` is the single door to Discord. Every tool goes through it, which is what makes
the cross-cutting concerns (auth, retries, error translation, audit-log reasons) exist in
exactly one place rather than 35.
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

# All logging goes to stderr (configured in server.py). Never print to stdout: when this
# server runs over stdio, stdout IS the MCP protocol channel and stray output corrupts it.
log = logging.getLogger("discord_mcp")

# v10 is the current stable Discord API version. Discord keeps old versions working for a
# long time but deprecates them eventually; bumping this is a deliberate decision, so the
# version is pinned here rather than left to a default.
API_BASE = "https://discord.com/api/v10"

# Discord asks bots to send a User-Agent naming the project and version. It is not merely
# polite — Discord has been known to contact maintainers of misbehaving bots through it.
USER_AGENT = f"DiscordBot (https://github.com/lburnscissp/discord-mcp, {__version__})"

REQUEST_TIMEOUT = 30.0
MAX_RATE_LIMIT_RETRIES = 3

# Discord returns a numeric `code` in its JSON error bodies, separate from the HTTP status.
# The codes are far more specific than the statuses, so we translate the ones a user of
# this server will actually hit into a sentence that says what to DO about it. An agent
# reading "Missing permissions" learns nothing; "give the bot's role the needed permission
# in Server Settings → Roles" tells it where to go next.
# Full list: https://discord.com/developers/docs/topics/opcodes-and-status-codes#json
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
    50001: "Missing access. The bot is not in this server, or it can't see this channel.",
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
    """A Discord call failed in a way worth telling the agent about.

    `message` is written to be read by a language model: it says what went wrong and what
    to try next. Tools catch this (see `tools._common.tool_errors`) and return it as an
    "Error: ..." string rather than letting it propagate as a protocol-level failure —
    an agent can recover from a message it can read, but not from a crash.
    """

    def __init__(self, message: str, status: int | None = None, code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status  # HTTP status, e.g. 403
        self.code = code  # Discord's own error code, e.g. 50013


def _load_env() -> None:
    """Find and load the project's `.env`.

    MCP clients launch this server as a subprocess from an arbitrary working directory —
    often the user's home folder or the client's install path, not the project. So we walk
    up from this file looking for `.env` instead of trusting the cwd. Without this, the
    token would mysteriously fail to load depending on who started the server.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):  # src/discord_mcp -> src -> project root is 2 hops; 4 is slack
        candidate = os.path.join(here, ".env")
        if os.path.exists(candidate):
            load_dotenv(candidate)
            return
        here = os.path.dirname(here)
    load_dotenv()  # last resort: whatever the cwd offers


_load_env()


def bot_token() -> str:
    """Read the bot token, or explain exactly how to set it.

    Read fresh on every call rather than cached at import: it keeps tests simple
    (monkeypatch the env var) and means a user who fixes a typo in `.env` only has to
    restart the server, not rebuild anything.

    The token is never logged, never echoed in a tool response, and never put in a URL.
    """
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise DiscordError(
            "DISCORD_BOT_TOKEN is not set. Put it in the .env file next to pyproject.toml "
            "(see .env.example) and restart the MCP server."
        )
    return token


def default_guild_id() -> str | None:
    """The optional 'my usual server' setting. Returns None when unset."""
    value = os.environ.get("DISCORD_GUILD_ID", "").strip()
    return value or None


def require_guild(guild_id: str | None) -> str:
    """Resolve which server a tool should act on.

    Every guild-scoped tool takes an optional `guild_id` and calls this. The explicit
    argument always wins, so one server can be configured as the default in `.env` while
    the same running server still operates on any other server the bot is in — which is
    what makes this usable for someone who owns several.
    """
    resolved = guild_id or default_guild_id()
    if not resolved:
        raise DiscordError(
            "No guild_id given and DISCORD_GUILD_ID is not set in .env. "
            "Pass guild_id explicitly, or run discord_list_guilds to find it."
        )
    return resolved


def encode_emoji(emoji: str) -> str:
    """Put an emoji into the shape Discord's reaction endpoints expect.

    Reaction URLs are the one awkward corner of the API. Discord wants:
      * a unicode emoji percent-encoded          '👍'            -> '%F0%9F%91%8D'
      * a custom emoji as bare `name:id`         'party:123'     -> 'party:123'
    and people naturally paste the chat syntax `<:party:123>` (or `<a:spin:123>` for an
    animated one), which has to be unwrapped. Handling all three here means no tool has to
    think about it.
    """
    e = emoji.strip()
    if e.startswith("<") and e.endswith(">"):
        e = e[1:-1]
        if e.startswith("a:"):  # animated custom emoji
            e = e[2:]
        return e.lstrip(":")
    # safe=":" keeps the colon in 'name:id' unescaped, which Discord requires.
    return quote(e, safe=":")


# One client for the process, created lazily and reused. httpx pools TCP connections, so
# reusing it avoids a fresh TLS handshake per call — noticeable when a tool like
# discord_search_messages makes five requests in a row.
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
    """Turn a failed HTTP response into a DiscordError with an actionable message."""
    status = response.status_code
    code: int | None = None
    detail = ""
    try:
        body = response.json()
        code = body.get("code")
        detail = body.get("message", "")
        # 50035 (Invalid Form Body) nests per-field complaints; flatten them or the
        # agent sees "Invalid Form Body" with no clue which field it got wrong.
        if "errors" in body:
            detail += f" Details: {_flatten_errors(body['errors'])}"
    except ValueError:
        detail = response.text[:300]  # Cloudflare pages and HTML error bodies land here

    if status == 401:
        # Worth special-casing: a bad token is the single most common setup mistake.
        msg = (
            "Invalid bot token. Check DISCORD_BOT_TOKEN in .env, and reset the token in the "
            "developer portal if needed."
        )
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
    """Flatten Discord's nested validation errors into `field: message` pairs.

    Discord shapes them like {"name": {"_errors": [{"message": "Must be 1-100 chars"}]}},
    nested arbitrarily deep for nested request bodies. We walk the tree and join the
    leaves so the result fits on one line.
    """
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
    """Call the Discord API and return the parsed JSON body (or None for an empty 204).

    Args:
        method: HTTP verb, e.g. "GET", "PATCH".
        path: Path below the API base, e.g. f"/guilds/{gid}/channels".
        params: Query parameters. Keys whose value is None are dropped, so callers can
            pass optional filters straight through without building a dict conditionally.
        json: Request body, serialised as JSON.
        reason: Sent as `X-Audit-Log-Reason`. Discord records it against the action in the
            server's audit log, so a human scrolling the log later sees *why* the bot
            banned someone, not just that it did. Always pass one for moderation actions.

    Raises:
        DiscordError: for any failure — auth, permissions, validation, network, timeout.
            The message is written for an agent to read and act on.
    """
    headers = {"Authorization": f"Bot {bot_token()}"}
    if reason:
        # The header must be URL-encoded (it may contain non-ASCII) and Discord caps it
        # at 512 characters.
        headers["X-Audit-Log-Reason"] = quote(reason[:512])

    client = _get_client()
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            response = await client.request(method, path, params=clean_params, json=json, headers=headers)
        except httpx.TimeoutException as e:
            raise DiscordError("Discord API request timed out. Try again.") from e
        except httpx.HTTPError as e:
            # Deliberately does not include the exception text: it can contain the full
            # request URL, and we never want a token or ID leaking into a tool response.
            raise DiscordError(f"Network error talking to Discord: {type(e).__name__}.") from e

        # 429 = rate limited. Discord tells us exactly how long to wait in the body
        # (`retry_after`, in seconds, often fractional). Honour it rather than guessing,
        # and cap the sleep so a long global limit surfaces as an error instead of a hang.
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

        # 204 No Content is Discord's answer to most DELETEs and some PUTs.
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    raise DiscordError("Rate limited repeatedly. Wait a minute and retry.")


# channel_id -> guild_id (or None for a DM channel). Messages returned by the REST API do
# not include guild_id — only gateway events do — but building a jump link needs it. A
# channel cannot move between servers, so this is safe to cache for the life of the
# process and costs one extra request per channel, once.
_channel_guild_cache: dict[str, str | None] = {}


async def guild_for_channel(channel_id: str) -> str | None:
    """Return the server ID a channel belongs to, or None if it is a DM.

    Cached. Callers that already have a `guild_id` from somewhere should use that instead
    of calling this.
    """
    if channel_id not in _channel_guild_cache:
        channel = await request("GET", f"/channels/{channel_id}")
        _channel_guild_cache[channel_id] = channel.get("guild_id")
    return _channel_guild_cache[channel_id]


async def paginate(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    limit: int,
    page_size: int = 100,
    cursor_key: str = "after",
) -> list[dict]:
    """Collect up to `limit` items from a snowflake-cursor endpoint.

    Discord does not paginate with offsets. Instead you say "give me the next N items
    after ID X", where X is the last ID you saw. This walks that chain until it has
    enough items or the endpoint runs out.

    Args:
        path: Endpoint returning a JSON array of objects with an "id".
        params: Extra query parameters, applied to every page.
        limit: Total items wanted across all pages.
        page_size: Max items per request (endpoint-specific; 100 for most, 1000 for members).
        cursor_key: "after" to walk forwards (oldest→newest), "before" to walk backwards.

    Returns:
        A list of raw Discord objects, at most `limit` long.
    """
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
        # A short page means we reached the end — stop rather than make a pointless call.
        if len(page) < page_params["limit"]:
            break
        cursor = page[-1]["id"]
    return items[:limit]
