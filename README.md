# discord-mcp

An [MCP](https://modelcontextprotocol.io) server that lets Claude — or any MCP client —
operate a Discord bot. Read channels, search history, post and reply, manage channels,
threads and roles, moderate members, and read the audit log.

35 tools. Python 3.12+, no framework beyond the MCP SDK and `httpx`. Runs as a local
subprocess of your client, holds no state, and makes plain HTTPS calls to Discord's REST
API v10 — no gateway connection, nothing running in the background.

```
You:    "What's been discussed in #trading-ideas this week?"
Claude: [discord_read_messages] → summarises the last 40 messages
You:    "Reply to Sam's question about position sizing."
Claude: [discord_send_message] → posts as your bot, mentions suppressed by default
```

- [What it can and can't do](#what-it-can-and-cant-do)
- [Setup](#setup) ← the Discord portal bit, in detail
- [Tools](#tools)
- [Using it with several servers](#using-it-with-several-servers)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [How it works](docs/architecture.md) · [Adding a tool](docs/adding-a-tool.md) · [Contributing](CONTRIBUTING.md)

## What it can and can't do

**Can:** anything the bot's role is permitted to do, in any server the bot has been
invited to. One running server handles as many Discords as the bot is in.

**Can't, and these are not bugs:**

| | Why |
|---|---|
| See a server the bot isn't in | Discord has no such thing. Someone with **Manage Server** has to add the bot. There is no way in from outside. |
| Act as *you* | It acts as the bot, with the bot's name and avatar. Automating a human account ("self-botting") breaks Discord's ToS and gets accounts banned — don't ask it to, it can't. |
| React to events live | No gateway connection, by design. It answers questions; it isn't notified of new messages. See [architecture.md](docs/architecture.md). |
| Use Discord's message search | That endpoint is user-accounts-only. `discord_search_messages` pages back through history and filters locally instead. |
| Read message text without the intent | **Message Content** is a privileged intent. Switch it on or every message comes back blank — see step 3 of Setup. |

## Setup

Four steps, about five minutes. Step 1 is the one people get wrong.

### 1. Create the bot

1. Go to <https://discord.com/developers/applications> → **New Application** → give it a
   name (this becomes the bot's display name).
2. **Bot** tab → **Reset Token** → copy it somewhere safe. **Discord shows it once.** If
   you lose it, reset again — resetting invalidates the old one.
3. Still on the **Bot** tab, scroll to **Privileged Gateway Intents** and enable:
   - **Message Content Intent** — required. Without it, every message's text arrives as an
     empty string, with no error. This catches almost everyone.
   - **Server Members Intent** — only needed for `discord_list_members`. Member *search*
     works without it.

   Click **Save Changes**.

### 2. Invite it to your server

**OAuth2** tab → copy your **Client ID** (also shown as Application ID on the General
Information tab), then open one of these URLs with `CLIENT_ID` replaced:

**Everything the tools need** — read, post, manage channels and roles, moderate:

```
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot&permissions=1494917180630
```

**Read and post only** — no moderation, no channel or role management. A sensible place to
start:

```
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot&permissions=274878024896
```

**Read only** — view channels, read history, read the audit log:

```
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot&permissions=66688
```

Open the URL, choose your server, authorise. The bot appears in the member list, offline —
that's correct, it has no gateway connection.

You can widen permissions later in **Server Settings → Roles** without re-inviting. If you
prefer to pick by hand, the **OAuth2 URL Generator** on that tab builds the URL for you.

### 3. Install and configure

```bash
git clone https://github.com/lburnscissp/discord-mcp.git
cd discord-mcp
uv sync
cp .env.example .env
```

Put your token in `.env`:

```ini
DISCORD_BOT_TOKEN=your-token-here
# Optional: a default server, so tools don't need guild_id every call
DISCORD_GUILD_ID=
```

To get a server ID: Discord → **User Settings → Advanced → Developer Mode** on, then
right-click the server icon → **Copy Server ID**. Leaving it blank is fine — see
[Using it with several servers](#using-it-with-several-servers).

Check it works, with no network calls and no token needed:

```bash
uv run pytest
```

(No [uv](https://docs.astral.sh/uv/)? `pip install -e ".[dev]"` in a 3.12+ virtualenv does
the same job; replace `uv run` with your venv's python below.)

### 4. Register with your MCP client

**Claude Code:**

```bash
claude mcp add discord -- uv --directory "$(pwd)" run discord-mcp
```

**Claude Desktop** — add to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "discord": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/discord-mcp", "run", "discord-mcp"]
    }
  }
}
```

Restart the client, then ask it: **"discord whoami"**. You should get the bot's name back.
Then **"list my discord channels"**.

## Tools

Read tools take `response_format`: `markdown` (default, compact and readable) or `json`
(full structured data). Tools that change a server take `reason`, which Discord records in
the audit log. Every tool is annotated read-only / destructive so your client can decide
what to confirm.

### Servers

| Tool | |
|---|---|
| `discord_whoami` | Identify the bot. The cheapest check that your token works. |
| `discord_list_guilds` | Every server the bot is in. Where you find a `guild_id`. |
| `discord_get_guild` | Name, owner, member and online counts, creation date. |

### Channels and threads

| Tool | |
|---|---|
| `discord_list_channels` | Channels and categories, filterable by kind or name. Where you find a `channel_id`. |
| `discord_get_channel` | One channel or thread in detail. |
| `discord_create_channel` | New channel or category. Needs Manage Channels. |
| `discord_edit_channel` | Rename, retopic, move, set slowmode; archive or lock a thread. |
| `discord_delete_channel` | **Destructive.** Deletes the channel and all its messages. |
| `discord_list_threads` | Active (unarchived) threads. |
| `discord_create_thread` | From a message, or standalone. |

### Messages

| Tool | |
|---|---|
| `discord_read_messages` | Recent messages with cursor pagination. |
| `discord_get_message` | One message, with attachments, embeds and reactions. |
| `discord_search_messages` | Find a phrase in a channel's recent history, optionally by author. |
| `discord_send_message` | Post or reply. Mentions suppressed unless `allow_mentions=true`. |
| `discord_edit_message` | Edit a message the bot sent (Discord won't let bots edit others'). |
| `discord_delete_message` | **Destructive.** One message. |
| `discord_react` | Add or remove the bot's reaction. |
| `discord_list_pins` | Pinned messages. |
| `discord_pin_message` | Pin or unpin. |
| `discord_send_dm` | Direct message a user. |

### Members

| Tool | |
|---|---|
| `discord_search_members` | Prefix search on username or nickname. Turns a name into an ID. |
| `discord_list_members` | Full member list. Needs the Server Members intent. |
| `discord_get_member` | Nickname, roles, join date, timeout status. |
| `discord_set_member_role` | Add or remove a role. |

### Roles

| Tool | |
|---|---|
| `discord_list_roles` | All roles, highest first, with IDs and permission bitfields. |
| `discord_create_role` | New role, with colour and permissions. |
| `discord_edit_role` | Change name, colour, flags or permissions. |
| `discord_delete_role` | **Destructive.** Removes it from every member. |

### Moderation

| Tool | |
|---|---|
| `discord_timeout_member` | Mute for up to 28 days. `minutes=0` lifts it. |
| `discord_kick_member` | **Destructive.** Removes them; they can rejoin with an invite. |
| `discord_ban_member` | **Destructive.** Blocks rejoining; can delete their recent messages. |
| `discord_unban_member` | Lift a ban. |
| `discord_list_bans` | Who's banned, and the reason recorded. |
| `discord_bulk_delete_messages` | **Destructive.** 2–100 messages at once, under 14 days old. |
| `discord_get_audit_log` | Who did what, and why. Answers "who deleted that channel?" |

## Using it with several servers

Every server-scoped tool takes an optional `guild_id`. The resolution order is simple:

1. the `guild_id` you pass, if you pass one;
2. otherwise `DISCORD_GUILD_ID` from `.env`;
3. otherwise an error telling you to run `discord_list_guilds`.

So one installation covers every server the bot is in. Set `DISCORD_GUILD_ID` to whichever
you use most and it becomes the default — "post this in #general" needs no ID — while
*"list the channels in my other server, ID 123…"* still works. Leave it unset if you treat
several equally; the client will pass the ID each time.

Running several *bots* (different identities, different servers) means registering the
server twice with different `.env` files — or two clones. The process reads its token at
startup.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Error: Invalid bot token` | Wrong or stale token. Reset it in the portal (**Bot → Reset Token**) and update `.env`. Note it's the *bot* token, not the client secret. |
| `Error: DISCORD_BOT_TOKEN is not set` | No `.env`, or the file is somewhere else. It must sit next to `pyproject.toml`. The server walks up from its own location to find it — the client's working directory doesn't matter. |
| Every message's text is empty | **Message Content Intent** is off. Portal → Bot → Privileged Gateway Intents → enable → Save, then restart the client. |
| `Error: Missing access` (50001) | The bot isn't in that server, or can't see that channel. Check channel-level permission overrides, not just the role. |
| `Error: Missing permissions` (50013) | The role lacks the permission. For *role* changes, also check hierarchy: a bot can't touch a role positioned above its own. Drag its role higher in **Server Settings → Roles**. |
| `Error: Missing access` on `discord_list_members` | **Server Members Intent** is off. Or use `discord_search_members`, which doesn't need it. |
| Bulk delete fails | Messages older than 14 days — Discord's limit, not ours. Delete them individually. |
| Bot shows as offline | Expected. No gateway connection; it works over HTTP. |
| Tools don't appear in the client | Check the client's MCP logs. Run `uv run discord-mcp -v` by hand — it should sit there silently waiting for input on stdin. |

Deeper debugging:

```bash
uv run discord-mcp -v                                            # verbose logs on stderr
npx @modelcontextprotocol/inspector uv --directory . run discord-mcp   # poke tools by hand
uv run discord-mcp --http --port 8765                            # HTTP instead of stdio
```

## Security

- **The token is the whole bot.** Anyone holding it can do everything the bot can, in every
  server it's in. `.env` is git-ignored; `*.key`, `*.pem`, `*credentials*` and `*secrets*`
  are too. Never paste it into an issue, a chat, or a commit.
- **If a token leaks, reset it.** Deleting the file isn't enough — a committed secret stays
  in git history. Portal → Bot → Reset Token invalidates the old one immediately.
- **Give the bot the narrowest permissions you can live with.** Start with the read-only or
  read-and-post invite above; widen later in Server Settings when something fails.
- **Mentions are suppressed by default** so model-generated text can't accidentally
  `@everyone`. Overriding that is explicit (`allow_mentions=true`).
- **The HTTP transport binds to 127.0.0.1 only** — this process holds a token and must not
  be reachable from your network.
- **Destructive tools are annotated** so your client can require confirmation. Treat that
  as UX, not a security boundary: a client is free to ignore annotations, so don't give the
  bot Ban Members in a server where a wrong call would be a disaster.

## Project layout

```
src/discord_mcp/
  client.py       every HTTP call: auth, 429 retry, Discord errors → actionable messages
  formatting.py   snowflake timestamps, Markdown/JSON renderers, pagination envelopes
  server.py       entry point: stdio (default) or --http
  tools/
    _common.py    shared server instance, argument types, annotations — read this first
    guilds.py channels.py messages.py members.py roles.py moderation.py
tests/            34 tests, Discord mocked with respx, no token required
docs/             architecture.md, adding-a-tool.md
```

Every module opens with a docstring explaining the Discord behaviour it's built around.
[docs/architecture.md](docs/architecture.md) is the guided tour.

## Licence

[MIT](LICENSE). Not affiliated with or endorsed by Discord.
