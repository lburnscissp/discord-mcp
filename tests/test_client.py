"""Tests for the HTTP layer: auth, error translation, retries, helpers.

These are the tests that protect users from confusing failures. Each one pins down a
promise the README makes — that a missing token says so plainly, that a 403 explains
which permission to grant, that a rate limit is waited out rather than thrown.
"""

import httpx
import pytest

from discord_mcp import client
from discord_mcp.client import DiscordError, encode_emoji, request, require_guild
from tests.conftest import GUILD, TOKEN


async def test_sends_bot_auth_header(api):
    route = api.get("/users/@me").mock(return_value=httpx.Response(200, json={"id": "1", "username": "bot"}))
    await request("GET", "/users/@me")
    assert route.calls[0].request.headers["Authorization"] == f"Bot {TOKEN}"


async def test_missing_token_is_actionable(api, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "")
    with pytest.raises(DiscordError, match="DISCORD_BOT_TOKEN is not set"):
        await request("GET", "/users/@me")


async def test_401_maps_to_invalid_token(api):
    api.get("/users/@me").mock(return_value=httpx.Response(401, json={"message": "401: Unauthorized", "code": 0}))
    with pytest.raises(DiscordError, match="Invalid bot token"):
        await request("GET", "/users/@me")


async def test_discord_error_code_hint(api):
    api.get("/channels/1").mock(return_value=httpx.Response(403, json={"message": "Missing Permissions", "code": 50013}))
    with pytest.raises(DiscordError, match="Missing permissions"):
        await request("GET", "/channels/1")


async def test_validation_errors_are_flattened(api):
    body = {"code": 50035, "message": "Invalid Form Body", "errors": {"name": {"_errors": [{"code": "BASE_TYPE_BAD_LENGTH", "message": "Must be between 1 and 100 in length."}]}}}
    api.post("/guilds/1/channels").mock(return_value=httpx.Response(400, json=body))
    with pytest.raises(DiscordError, match="name: Must be between 1 and 100"):
        await request("POST", "/guilds/1/channels", json={})


async def test_rate_limit_retries_then_succeeds(api, monkeypatch):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(client.asyncio, "sleep", fake_sleep)
    api.get("/users/@me").mock(side_effect=[
        httpx.Response(429, json={"retry_after": 0.25, "global": False}),
        httpx.Response(200, json={"id": "1"}),
    ])
    assert await request("GET", "/users/@me") == {"id": "1"}
    assert sleeps == [0.25]


async def test_204_returns_none(api):
    api.delete("/channels/1/messages/2").mock(return_value=httpx.Response(204))
    assert await request("DELETE", "/channels/1/messages/2") is None


async def test_audit_reason_header(api):
    route = api.delete("/guilds/1/members/2").mock(return_value=httpx.Response(204))
    await request("DELETE", "/guilds/1/members/2", reason="spam & scams")
    assert route.calls[0].request.headers["X-Audit-Log-Reason"] == "spam%20%26%20scams"


def test_require_guild_falls_back_to_env():
    assert require_guild(None) == GUILD
    assert require_guild("999999999999999999") == "999999999999999999"


def test_require_guild_without_default(monkeypatch):
    monkeypatch.setenv("DISCORD_GUILD_ID", "")
    with pytest.raises(DiscordError, match="No guild_id given"):
        require_guild(None)


def test_encode_emoji():
    assert encode_emoji("👍") == "%F0%9F%91%8D"
    assert encode_emoji("party:123456") == "party:123456"
    assert encode_emoji("<:party:123456>") == "party:123456"
    assert encode_emoji("<a:spin:123456>") == "spin:123456"
