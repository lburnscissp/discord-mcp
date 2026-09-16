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
    monkeypatch.setenv("DISCORD_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("DISCORD_GUILD_ID", GUILD)
    # force a fresh httpx client per test so respx intercepts it
    client._client = None
    yield
    client._client = None


@pytest.fixture
def api():
    with respx.mock(base_url=client.API_BASE, assert_all_called=False) as mock:
        yield mock


def msg(mid: str = MSG, content: str = "hello world", author_id: str = USER, **extra) -> dict:
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
