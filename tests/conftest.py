"""Shared test fixtures.

The whole suite runs offline. `respx` intercepts httpx at the transport layer, so the code
under test makes what it believes are real HTTP calls and we assert on the exact request
it produced — URL, headers, JSON body. That catches the bugs that matter in an API
wrapper (wrong field name, unconverted units, a missing header) without ever needing a
Discord token or a test server.

The fake IDs below are valid-looking snowflakes, because the tools' schemas reject
anything that isn't 17-20 digits.
"""

import os

import pytest
import respx

from discord_mcp import client

TOKEN = "test-token-not-real"
GUILD = "111111111111111111"
CHANNEL = "222222222222222222"
USER = "333333333333333333"
MSG = "444444444444444444"
ROLE = "555555555555555555"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """Give every test a token and a default server, and a fresh HTTP client.

    `client._client` is a module-level singleton (see client._get_client). It has to be
    reset around each test or the first test's httpx client — created while respx was
    mocking — leaks into the next one.
    """
    monkeypatch.setenv("DISCORD_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("DISCORD_GUILD_ID", GUILD)
    # force a fresh httpx client per test so respx intercepts it
    client._client = None
    client._channel_guild_cache.clear()
    yield
    client._client = None
    client._channel_guild_cache.clear()


@pytest.fixture
def api():
    """Intercept all calls to the Discord API base URL.

    assert_all_called=False because most tests mock one endpoint and exercise one path;
    we assert on the calls we care about rather than requiring every mock to fire.
    """
    with respx.mock(base_url=client.API_BASE, assert_all_called=False) as mock:
        yield mock


def msg(mid: str = MSG, content: str = "hello world", author_id: str = USER, **extra) -> dict:
    """Build a minimal but realistic Discord message object.

    Only the fields the code actually reads. Pass **extra to add whatever a given test
    needs (e.g. guild_id, reactions, message_reference).
    """
    return {
        "id": mid,
        "channel_id": CHANNEL,
        "author": {"id": author_id, "username": "tester", "global_name": "Tester"},
        "content": content,
        "timestamp": "2026-09-10T12:00:00.000000+00:00",
        "edited_timestamp": None,
        "pinned": False,
        "attachments": [],
        "embeds": [],
        **extra,
    }
