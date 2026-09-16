# discord-mcp

An MCP server that lets Claude (or any MCP client) operate a Discord bot: read channels,
search history, post and reply, manage channels/threads/roles, and moderate.

Python 3.12+, `mcp` 2.x, `httpx` against Discord's REST API v10. No gateway connection —
the server is stateless and runs over stdio as a subprocess of the client.

## What it can and can't do

- **Can:** everything the bot's role is allowed to do in servers it has been **invited to**.
- **Can't:** see servers the bot isn't in. A bot cannot be pointed at someone else's server
  from the outside — an admin there has to add it. Automating a personal account instead
  ("self-bot") is against Discord's ToS.

## 35 tools

| Area | Tools |
|---|---|
| Server | `discord_whoami` · `discord_list_guilds` · `discord_get_guild` |
| Channels | `discord_list_channels` · `discord_get_channel` · `discord_create_channel` · `discord_edit_channel` · `discord_delete_channel` · `discord_list_threads` · `discord_create_thread` |
| Messages | `discord_read_messages` · `discord_get_message` · `discord_search_messages` · `discord_send_message` · `discord_edit_message` · `discord_delete_message` · `discord_react` · `discord_list_pins` · `discord_pin_message` · `discord_send_dm` |
| Members | `discord_search_members` · `discord_list_members` · `discord_get_member` · `discord_set_member_role` |
| Roles | `discord_list_roles` · `discord_create_role` · `discord_edit_role` · `discord_delete_role` |
| Moderation | `discord_timeout_member` · `discord_kick_member` · `discord_ban_member` · `discord_unban_member` · `discord_list_bans` · `discord_bulk_delete_messages` · `discord_get_audit_log` |

Every tool carries MCP annotations (read-only / destructive), so a client can gate the
dangerous ones. Read tools take `response_format` = `markdown` (default) or `json`.
Moderation tools take a `reason` that lands in the server's audit log.

## Setup

### 1. Create the bot (Discord Developer Portal, ~3 minutes)

1. <https://discord.com/developers/applications> → **New Application** → name it.
2. **Bot** tab → **Reset Token** → copy it. You only see it once.
3. Same tab, **Privileged Gateway Intents** → turn on **Message Content Intent**
   (without it every message reads as empty text) and **Server Members Intent**
   (needed only for `discord_list_members`; search works without it).
4. **OAuth2** tab → **OAuth2 URL Generator** → tick `bot` → in the permissions grid tick
   what you want the bot able to do. Or use this URL, which grants everything the tools
   need (View/Send/Manage Messages, Read History, Reactions, Threads, Manage Channels &
   Roles, Kick/Ban/Timeout, View Audit Log) — replace `CLIENT_ID` with the Application ID
   from the General Information tab:

   ```
   https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot&permissions=1494917180630
   ```

5. Open that URL, pick your server, authorize. The bot appears in the member list.

### 2. Configure

```bash
cp .env.example .env
```

Put the token in `.env` as `DISCORD_BOT_TOKEN`. Optionally set `DISCORD_GUILD_ID` to your
server's ID (Discord → User Settings → Advanced → Developer Mode, then right-click the
server → Copy Server ID) so you never have to pass `guild_id`.

`.env` is git-ignored. Never commit it.

### 3. Install and check

```bash
uv sync
uv run pytest
```

### 4. Register with Claude Code

```bash
claude mcp add discord -- uv --directory "/Users/lsh/Projects/AI/discord-mcp" run discord-mcp
```

Restart Claude Code, then try: *"discord whoami"* and *"list my discord channels"*.

For Claude Desktop, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "discord": {
      "command": "uv",
      "args": ["--directory", "/Users/YOU/Projects/AI/discord-mcp", "run", "discord-mcp"]
    }
  }
}
```

### Debugging

- `uv run discord-mcp -v` — verbose logs on stderr.
- `npx @modelcontextprotocol/inspector uv --directory . run discord-mcp` — poke tools by hand.
- `uv run discord-mcp --http --port 8765` — streamable HTTP on 127.0.0.1 instead of stdio.

## Layout

```
src/discord_mcp/
  client.py       REST client: auth, 429 retry, Discord error codes → actionable messages
  formatting.py   snowflake timestamps, Markdown/JSON renderers, pagination envelope
  server.py       entry point (stdio / --http)
  tools/          one module per area; importing the package registers all tools
tests/            respx-mocked API; every tool exercised through MCPServer.call_tool
```

## Design notes

- **Discord's message search endpoint is user-only.** `discord_search_messages` pages
  through recent history (100/call, `max_scan` cap) and filters locally.
- **Pings are off by default.** `discord_send_message` sets `allowed_mentions` to nothing
  unless `allow_mentions=true`, so a stray `@everyone` in generated text can't mass-ping.
- **Rate limits** are retried up to 3 times using Discord's `retry_after`.
- **Errors** map Discord's numeric codes (50001, 50013, 50034, …) to a sentence saying what
  to do next, not just what failed.
