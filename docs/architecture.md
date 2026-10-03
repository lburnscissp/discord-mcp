# How it works

A tour of the codebase for anyone who wants to change it — or who wants to build their own
MCP server and would like a worked example to read.

## The shape of the thing

```
MCP client (Claude Code, Claude Desktop, an editor, a script)
        │  JSON-RPC over stdin/stdout
        ▼
server.py            picks the transport, sends logs to stderr
        │
tools/__init__.py    imports the six tool modules, which registers ~35 tools
        │
tools/*.py           one function per tool: validate args, call Discord, format the answer
        │
client.py            the single door to Discord: auth, retries, error translation
        │  HTTPS
        ▼
Discord REST API v10
```

Roughly 1,500 lines, no framework beyond the MCP SDK and httpx.

## The five files that matter

| File | What it owns |
|---|---|
| [`client.py`](../src/discord_mcp/client.py) | Every HTTP call. Bot-token auth, 429 retry using Discord's own `retry_after`, Discord error codes translated into actionable sentences, audit-log reasons, cursor pagination. |
| [`formatting.py`](../src/discord_mcp/formatting.py) | Turning Discord JSON into Markdown or slimmed-down JSON. The `slim_*` functions are a context-budget decision, not cosmetics. |
| [`tools/_common.py`](../src/discord_mcp/tools/_common.py) | The shared server instance, reusable argument types, the four annotation presets, and the error-wrapping decorator. **Read this first.** |
| [`tools/*.py`](../src/discord_mcp/tools/) | Six modules, one per Discord concept. Each file's docstring covers the Discord quirks that apply to it. |
| [`server.py`](../src/discord_mcp/server.py) | Argument parsing, logging to stderr, `mcp.run()`. |

## Three decisions worth understanding

### REST, not a gateway

Most Discord bots hold a WebSocket open and react to events. This one makes plain HTTP
calls and holds nothing. An MCP server is asked questions; it doesn't need to be told
things. The result starts instantly, costs nothing idle, and has no reconnect logic to get
wrong. The cost is that it can only *look*, never be *notified* — if you need push
behaviour, that is a second program, not a flag on this one.

### Flat arguments, not a model per tool

The MCP SDK will happily build a tool from `async def tool(params: MyPydanticModel)`, but
the schema it generates nests everything under `params`, and models routinely send the
fields at the top level instead and fail validation. So every tool takes flat keyword
arguments with `Annotated[..., Field(...)]` types. The shared aliases in `_common.py`
(`Snowflake`, `GuildId`, `Fmt`, `Reason`) keep that from turning into copy-paste, and
`test_tool_inventory` fails the build if a `params` object ever sneaks back in.

### Errors are returned, not raised

A raised exception becomes a protocol error, which most clients present as a dead end. A
returned `"Error: ..."` string stays in the conversation, so the model can read *"Missing
permissions. Give the bot's role the needed permission in Server Settings → Roles"* and
either retry differently or tell the user what to change. `tool_errors` in `_common.py`
does this for every tool; `_ERROR_HINTS` in `client.py` is where Discord's numeric codes
become those sentences.

## Discord facts the code is built around

* **IDs are snowflakes** — 64-bit integers whose top bits are a millisecond timestamp.
  Every object therefore knows its own creation time (`formatting.snowflake_time`) and IDs
  sort chronologically, which is why pagination uses them as cursors.
* **Message Content is a privileged intent.** Without it switched on in the developer
  portal, message text comes back as an empty string — with no error. It applies to the
  HTTP API, not just gateway events.
* **Bots cannot use Discord's message search.** That endpoint is user-account only, so
  `discord_search_messages` pages backwards through history and filters locally.
* **Role hierarchy beats permissions.** A bot with Manage Roles still cannot touch a role
  positioned above its own highest role.
* **Bulk delete refuses anything older than 14 days**, and refuses batches under 2.
* **Threads and categories are channels** (types 10–12 and 4), which is why one set of
  tools covers all three.

## Testing

```bash
uv run pytest           # 34 tests, no network, no token needed
```

`respx` intercepts httpx at the transport layer, so the code makes what it thinks are real
calls and the tests assert on the exact request produced — URL, headers, JSON body. That
is where the bugs in an API wrapper live: a wrong field name, an unconverted unit, a
missing header. Tool tests go through `mcp.call_tool()` so the generated schema and
validation are exercised too.

## Adding a tool

See [adding-a-tool.md](adding-a-tool.md).
