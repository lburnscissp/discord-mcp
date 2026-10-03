# Adding a tool

A worked example: suppose you want `discord_list_invites`.

## 1. Find the endpoint

Discord's reference: <https://discord.com/developers/docs/resources/guild>. Invites for a
server are `GET /guilds/{guild.id}/invites`, and it needs the **Manage Server** permission.
Note the permission — it belongs in the docstring.

## 2. Pick the module

One file per Discord concept (`guilds`, `channels`, `messages`, `members`, `roles`,
`moderation`). Invites are server-level, so `tools/guilds.py`. A genuinely new concept
gets a new module — add it to the import line in `tools/__init__.py`, or its tools will
never register.

## 3. Write it

```python
@mcp.tool(name="discord_list_invites", annotations=READ)
@tool_errors
async def discord_list_invites(guild_id: GuildId = None, response_format: Fmt = MD) -> str:
    """List a server's active invite links, with uses and who created each one.

    Needs the Manage Server permission.

    Returns: Markdown table (code, channel, creator, uses, expires) or JSON list of
    {code, url, channel_id, inviter_id, uses, max_uses, expires_at}.
    """
    gid = require_guild(guild_id)
    invites = await request("GET", f"/guilds/{gid}/invites")
    if response_format == JSON:
        return to_json({"count": len(invites), "invites": invites})
    if not invites:
        return "No active invites."
    rows = [[i["code"], i.get("channel", {}).get("name"), user_label(i["inviter"]), i.get("uses")]
            for i in invites]
    return f"# Invites ({len(invites)})\n\n" + md_table(["Code", "Channel", "Created by", "Uses"], rows)
```

Five conventions in there, each for a reason:

1. **`discord_` prefix.** Tool names share one namespace with every other MCP server the
   user has connected. `list_invites` would be ambiguous; `discord_list_invites` isn't.
2. **The right annotation** — `READ`, `WRITE`, `UPDATE` or `DESTRUCTIVE` from `_common.py`.
   Clients use these to decide what to auto-approve.
3. **`@tool_errors` under `@mcp.tool`.** Order matters: the MCP decorator must see the
   wrapped function so `functools.wraps` has already restored the name and docstring.
4. **Shared argument aliases** — `GuildId`, `Snowflake`, `Fmt`, `Reason`. Flat keyword
   arguments, never a single Pydantic model (see [architecture.md](architecture.md)).
5. **`require_guild`**, so the tool works with `DISCORD_GUILD_ID` set *or* an explicit
   `guild_id`. That is what makes one running server usable across several Discords.

## 4. The docstring is the interface

It is what a model reads to decide whether to call your tool, and it is the highest-leverage
text you will write. Cover, in order:

- what it does, in one line;
- the permission or privileged intent it needs;
- anything to confirm with the user first, if it changes or deletes something;
- what the return value looks like in **both** formats;
- the name of the tool to use instead, if there's an easy confusion ("for 2–100 messages
  at once use `discord_bulk_delete_messages`").

Vague descriptions are the single biggest cause of an agent picking the wrong tool.

## 5. Test it

Add to `tests/test_tools.py`, going through `mcp.call_tool` so the schema is exercised too:

```python
async def test_list_invites(api):
    api.get(f"/guilds/{GUILD}/invites").mock(return_value=httpx.Response(200, json=[
        {"code": "abc123", "channel": {"name": "general"},
         "inviter": {"id": USER, "username": "tester"}, "uses": 4},
    ]))
    out = await call("discord_list_invites")
    assert "abc123" in out and "general" in out
```

For a tool that writes, assert on the request body — that is where unit conversions and
field names get caught:

```python
    body = json.loads(route.calls[0].request.content)
    assert body["delete_message_seconds"] == 172800
```

Then bump the expected count in `test_tool_inventory`. That test failing is the reminder
to update the README's tool table too.

```bash
uv run pytest
```

## 6. Document it

Add the tool to the table in [README.md](../README.md). If it needs a permission the
default invite URL doesn't grant, say so there as well.

## Checklist

- [ ] `discord_`-prefixed name
- [ ] correct annotation preset
- [ ] `@tool_errors` applied under `@mcp.tool`
- [ ] flat arguments using the shared aliases
- [ ] docstring: purpose, permission, both return formats
- [ ] test through `mcp.call_tool`
- [ ] `test_tool_inventory` count bumped
- [ ] README table updated
