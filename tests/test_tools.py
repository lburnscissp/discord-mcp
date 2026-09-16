"""Tool-level tests: call through MCPServer so schemas, validation and formatting are all exercised."""

import json

import httpx
import pytest

from discord_mcp.tools import mcp
from tests.conftest import CHANNEL, GUILD, MSG, ROLE, USER, msg


async def call(tool: str, **args) -> str:
    result = await mcp.call_tool(tool, args)
    return "".join(getattr(b, "text", "") for b in result.content)


async def test_tool_inventory():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert len(names) == 35
    assert all(n.startswith("discord_") for n in names)
    for t in tools:
        assert t.description, f"{t.name} has no description"
        assert t.annotations is not None, f"{t.name} has no annotations"
        assert "params" not in t.input_schema.get("properties", {}), f"{t.name} is nested under params"


async def test_whoami(api):
    api.get("/users/@me").mock(return_value=httpx.Response(200, json={"id": "1000000000000000000", "username": "brainbot", "bot": True}))
    out = await call("discord_whoami")
    assert "brainbot" in out and "[bot]" in out


async def test_read_messages_markdown_oldest_first(api):
    api.get(f"/channels/{CHANNEL}/messages").mock(return_value=httpx.Response(200, json=[msg("2", "newer"), msg("1", "older")]))
    out = await call("discord_read_messages", channel_id=CHANNEL, limit=2)
    assert out.index("older") < out.index("newer")
    assert "before=`1`" in out  # cursor to page further back


async def test_read_messages_json_envelope(api):
    api.get(f"/channels/{CHANNEL}/messages").mock(return_value=httpx.Response(200, json=[msg()]))
    out = json.loads(await call("discord_read_messages", channel_id=CHANNEL, limit=25, response_format="json"))
    assert out["count"] == 1 and out["has_more"] is False and out["next_before"] is None
    assert out["items"][0]["content"] == "hello world"


async def test_read_messages_rejects_two_cursors(api):
    out = await call("discord_read_messages", channel_id=CHANNEL, before=MSG, after=MSG)
    assert out.startswith("Error: pass only one")


async def test_invalid_snowflake_is_rejected():
    with pytest.raises(Exception):
        await mcp.call_tool("discord_read_messages", {"channel_id": "not-an-id"})


async def test_search_messages_paginates_and_filters(api):
    page1 = [msg(str(300 - i), f"msg {i}") for i in range(100)]
    page2 = [msg("150", "the needle is here"), msg("149", "nothing")]
    route = api.get(f"/channels/{CHANNEL}/messages").mock(side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
    out = await call("discord_search_messages", channel_id=CHANNEL, query="NEEDLE", max_scan=500)
    assert "needle is here" in out and "scanned 102" in out
    assert route.calls[1].request.url.params["before"] == "201"


async def test_send_message_suppresses_pings_by_default(api):
    route = api.post(f"/channels/{CHANNEL}/messages").mock(return_value=httpx.Response(200, json=msg(guild_id=GUILD)))
    out = await call("discord_send_message", channel_id=CHANNEL, content="hi @everyone", reply_to=MSG)
    body = json.loads(route.calls[0].request.content)
    assert body["allowed_mentions"]["parse"] == []
    assert body["message_reference"]["message_id"] == MSG
    assert f"https://discord.com/channels/{GUILD}/{CHANNEL}/{MSG}" in out


async def test_edit_channel_requires_a_change(api):
    assert (await call("discord_edit_channel", channel_id=CHANNEL)).startswith("Error: nothing to change")


async def test_edit_channel_maps_slowmode(api):
    route = api.patch(f"/channels/{CHANNEL}").mock(return_value=httpx.Response(200, json={"id": CHANNEL, "name": "general", "type": 0}))
    out = await call("discord_edit_channel", channel_id=CHANNEL, slowmode_seconds=30)
    assert json.loads(route.calls[0].request.content) == {"rate_limit_per_user": 30}
    assert "rate_limit_per_user" in out


async def test_list_channels_filters(api):
    api.get(f"/guilds/{GUILD}/channels").mock(return_value=httpx.Response(200, json=[
        {"id": "1", "name": "General", "type": 4, "position": 0},
        {"id": "2", "name": "general-chat", "type": 0, "position": 1, "parent_id": "1"},
        {"id": "3", "name": "voice", "type": 2, "position": 2},
    ]))
    out = await call("discord_list_channels", type="text", name_contains="GENERAL")
    assert "general-chat" in out and "| voice" not in out
    assert "| General |" not in out  # category filtered out by type


async def test_timeout_member_sets_iso_until(api):
    route = api.patch(f"/guilds/{GUILD}/members/{USER}").mock(return_value=httpx.Response(200, json={}))
    out = await call("discord_timeout_member", user_id=USER, minutes=10, reason="cool off")
    body = json.loads(route.calls[0].request.content)
    assert body["communication_disabled_until"].endswith("+00:00")
    assert "10 min" in out


async def test_timeout_zero_clears(api):
    route = api.patch(f"/guilds/{GUILD}/members/{USER}").mock(return_value=httpx.Response(200, json={}))
    await call("discord_timeout_member", user_id=USER, minutes=0)
    assert json.loads(route.calls[0].request.content) == {"communication_disabled_until": None}


async def test_ban_converts_days_to_seconds(api):
    route = api.put(f"/guilds/{GUILD}/bans/{USER}").mock(return_value=httpx.Response(204))
    await call("discord_ban_member", user_id=USER, delete_message_days=2, reason="spam")
    assert json.loads(route.calls[0].request.content) == {"delete_message_seconds": 172800}
    assert route.calls[0].request.headers["X-Audit-Log-Reason"] == "spam"


async def test_bulk_delete_dedupes(api):
    route = api.post(f"/channels/{CHANNEL}/messages/bulk-delete").mock(return_value=httpx.Response(204))
    out = await call("discord_bulk_delete_messages", channel_id=CHANNEL, message_ids=[MSG, MSG, USER])
    assert json.loads(route.calls[0].request.content)["messages"] == [MSG, USER]
    assert "Deleted 2" in out


async def test_bulk_delete_old_messages_hint(api):
    api.post(f"/channels/{CHANNEL}/messages/bulk-delete").mock(return_value=httpx.Response(400, json={"code": 50034, "message": "You can only bulk delete messages that are under 14 days old."}))
    out = await call("discord_bulk_delete_messages", channel_id=CHANNEL, message_ids=[MSG, USER])
    assert "younger than 14 days" in out


async def test_role_color_and_permissions(api):
    route = api.post(f"/guilds/{GUILD}/roles").mock(return_value=httpx.Response(200, json={"id": ROLE, "name": "Mod"}))
    out = await call("discord_create_role", name="Mod", color="#5865F2", permissions="8")
    body = json.loads(route.calls[0].request.content)
    assert body["color"] == 0x5865F2 and body["permissions"] == "8"
    assert ROLE in out


async def test_role_bad_color_rejected():
    with pytest.raises(Exception):
        await mcp.call_tool("discord_create_role", {"name": "x", "color": "blue"})


async def test_audit_log_unknown_action(api):
    out = await call("discord_get_audit_log", action="nope")
    assert out.startswith("Error: unknown action")


async def test_audit_log_renders_actor(api):
    api.get(f"/guilds/{GUILD}/audit-logs").mock(return_value=httpx.Response(200, json={
        "users": [{"id": USER, "username": "modperson"}],
        "audit_log_entries": [{"id": "1300000000000000000", "user_id": USER, "action_type": 22, "target_id": "9", "reason": "spam"}],
    }))
    out = await call("discord_get_audit_log", action="member_ban_add")
    assert "@modperson" in out and "member_ban_add" in out and "spam" in out


async def test_react_encodes_emoji(api):
    route = api.put(f"/channels/{CHANNEL}/messages/{MSG}/reactions/%F0%9F%91%8D/@me").mock(return_value=httpx.Response(204))
    await call("discord_react", channel_id=CHANNEL, message_id=MSG, emoji="👍")
    assert route.called


async def test_send_dm_two_step(api):
    api.post("/users/@me/channels").mock(return_value=httpx.Response(200, json={"id": "777777777777777777", "recipients": [{"id": USER, "username": "tester"}]}))
    route = api.post("/channels/777777777777777777/messages").mock(return_value=httpx.Response(200, json=msg()))
    out = await call("discord_send_dm", user_id=USER, content="psst")
    assert route.called and "777777777777777777" in out


async def test_missing_access_hint(api):
    api.get(f"/guilds/{GUILD}/members").mock(return_value=httpx.Response(403, json={"code": 50001, "message": "Missing Access"}))
    out = await call("discord_list_members")
    assert out.startswith("Error: Missing access")
