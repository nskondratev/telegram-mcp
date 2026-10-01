# telegram-mcp — read-only Telegram MCP server with a per-chat allowlist

[![CI](https://github.com/nskondratev/telegram-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/nskondratev/telegram-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An MCP server that lets an assistant read **only the chats you list by hand**, and
nothing else — no private conversations, no chats you forgot about, no writes at all.

## Why another Telegram MCP server

Existing servers expose your whole account. A Telegram MTProto session sees every
dialog, and none of the popular servers can narrow that down to a set of chats:
in [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp) that is open
[issue #116](https://github.com/chigwell/telegram-mcp/issues/116). Read-only modes
exist, but "read-only" there still means "read everything you have".

If you want an assistant in your work chats without handing it your family group,
that gap is the whole point of this server:

| | This server | Typical Telegram MCP server |
|---|---|---|
| Chats reachable | only those in `allowed_chats.json` | every dialog of the account |
| Write tools | none exist in the code | usually present, sometimes toggled off |
| Untrusted text | control and zero-width characters stripped, length capped | as-is |
| Tools exposed | 7 | 40–80 |

## Security model

- **Closed list.** `allowed_chats.json` is the single source of truth, together with the
  optional personal additions in `allowed_chats.local.json`. Anything not in them is
  denied — by id, by alias and by title.
- **Denial happens before the network.** Every handler asks the allowlist first, so a
  forbidden chat never becomes a Telegram request. There is a test that asserts exactly
  this over real stdio.
- **The answer is re-checked.** After Telegram resolves an entity, its real id is matched
  against the allowlist again — a renamed or substituted chat cannot slip through.
- **No write path.** There is no `send_message` to disable: the code does not contain one.
  All seven tools are annotated `readOnlyHint`.
- **Fetching a file is still reading.** `get_message_media` downloads an attachment
  through the same allowlist check, and pictures it hands back are untrusted data
  like any text: a screenshot can carry what looks like an instruction.
- **Text is data, not instructions.** Message texts, names, titles and attachment file
  names are sanitised (zero-width characters, bidi overrides, control characters) and
  truncated, and the tool descriptions tell the model to treat them as untrusted input.
  Reaction emoji go through the same filter, except that the zero-width joiner survives:
  without it ❤‍🔥 and 👨‍💻 would fall apart into two emoji each.
- **The allowlist lives outside the installation.** By default it is read from
  `~/.config/telegram-mcp/allowed_chats.json`, so real chat ids never end up next to the
  code — which is what makes running straight from a git URL safe.

## Tools

| Tool | What it returns |
|---|---|
| `list_chats` | the allowed chats with their current titles — start here |
| `get_chat_info` | title, `@username`, member count of one allowed chat |
| `get_messages` | latest messages, newest first, with paging via `before_id` |
| `get_message_context` | messages around a given id, to reconstruct a thread |
| `search_messages` | full-text search in one allowed chat or across all of them |
| `get_message_media` | the attachment of one message — a picture comes back inline, anything else as a path |
| `get_message_reactions` | who reacted to one message and with what, page by page, optionally narrowed to one emoji |

A chat is referenced by its alias (`team`), its exact title, or its id.

Every message that `get_messages`, `get_message_context` and `search_messages` return
carries a `reactions` field — `null` when there are none:

```json
"reactions": [
  {"emoji": "👀", "count": 2, "mine": true, "by": [
    {"id": 1001, "name": "Alice", "date": "2026-07-29T09:05:00+00:00"}
  ]},
  {"emoji": "🔥", "custom_emoji_id": "5000000000000000001", "count": 1}
]
```

`mine` marks the reaction you left. `by` is who reacted, as far as the message itself
tells: Telegram sends only the latest few people, and nobody in a channel, so a `count`
above the length of `by` means there are more — `get_message_reactions` has the full
list. A custom emoji carries the emoji it stands for and its id as a string: the ids run
past 2⁵³, where a JavaScript client would round a number. The counts cost no extra
request; the names behind `by` and the custom emoji cost one each per call, and a failed
lookup leaves the ids in place instead of failing the read.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended — no clone or virtualenv needed)
- A Telegram API application: <https://my.telegram.org/apps> → `api_id` and `api_hash`

## Setup

### 1. Issue a session string

```bash
export TELEGRAM_API_ID=… TELEGRAM_API_HASH=…
uvx --from git+https://github.com/nskondratev/telegram-mcp telegram-mcp login
```

Telethon asks for your phone number, the login code and the 2FA password — you type them
yourself, they are never stored. The command prints a session string.

> ⚠️ **The session string grants full access to your Telegram account.** Treat it like a
> password: never commit it, never paste it into a chat. It lives in your MCP client
> config (for Claude Code, `~/.claude.json` or the `env` block of `~/.claude/settings.json`),
> which is not in git.

### 2. Build the allowlist

```bash
export TELEGRAM_SESSION_STRING=…
uvx --from git+https://github.com/nskondratev/telegram-mcp telegram-mcp dialogs --filter acme
```

The output is `id  kind  title` per dialog. Put the ones you want into
`~/.config/telegram-mcp/allowed_chats.json`, using [allowed_chats.example.json](allowed_chats.example.json)
as the template:

```json
{
  "chats": [
    { "alias": "team",   "id": -1001111111111, "title": "Team chat", "note": "main work chat" },
    { "alias": "alerts", "id": -1002222222222, "title": "Alerts",    "note": "monitoring" }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | chat id as printed by `dialogs` (`-100…` for supergroups and channels) |
| `alias` | no | short latin handle used by the tools; defaults to the id |
| `title` | no | human-readable name; also accepted as a chat reference |
| `note` | no | free-form note, returned by `list_chats` and `get_chat_info` |

Duplicate aliases or ids are a startup error, not a warning. `dialogs --json` prints a
ready-made skeleton you can edit.

`dialogs` never reads the allowlist, so it works before the file exists — when the main
list ships inside a plugin, for instance. `check` and `download` do need the list and
refuse to run without one.

#### Personal additions to a shared allowlist

When the allowlist is shared — shipped inside a team plugin, for example, with
`TG_ALLOWED_CHATS_FILE` pointing into the plugin — editing it by hand is pointless: the
next update overwrites the file. Put your own chats into
`~/.config/telegram-mcp/allowed_chats.local.json` instead (`$XDG_CONFIG_HOME` is honoured,
`TG_ALLOWED_CHATS_LOCAL_FILE` overrides the path). The format is the same:

```json
{
  "chats": [
    { "alias": "side", "id": -1003333333333, "title": "Side project" }
  ]
}
```

- The file is optional: without it nothing changes.
- The main list wins. An entry whose id or alias (case-insensitive) is already in the
  main list is skipped and reported on stderr at startup, so an update of the shared list
  never stops the server from starting. A clashing title keeps pointing at the main entry.
- Inside the file the usual rules apply: a duplicate alias or id is a startup error.
- `check` shows how many chats the additions bring and which ones were skipped.
- The file is yours alone: the server never writes it, and the refusal message does not
  mention it — the model has no business editing its own allowlist.

### 3. Connect the server

Claude Code:

```bash
claude mcp add telegram -s user \
  -e TELEGRAM_API_ID=… \
  -e TELEGRAM_API_HASH=… \
  -e TELEGRAM_SESSION_STRING=… \
  -e TG_ALLOWED_CHATS_FILE=$HOME/.config/telegram-mcp/allowed_chats.json \
  -- uvx --from git+https://github.com/nskondratev/telegram-mcp telegram-mcp serve
```

Any MCP client works — the transport is stdio. The equivalent JSON:

```json
{
  "mcpServers": {
    "telegram": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/nskondratev/telegram-mcp",
        "telegram-mcp", "serve"
      ],
      "env": {
        "TELEGRAM_API_ID": "…",
        "TELEGRAM_API_HASH": "…",
        "TELEGRAM_SESSION_STRING": "…",
        "TG_ALLOWED_CHATS_FILE": "/home/you/.config/telegram-mcp/allowed_chats.json"
      }
    }
  }
}
```

### 4. Verify

```bash
uvx --from git+https://github.com/nskondratev/telegram-mcp telegram-mcp check
```

`check` reads every allowed chat and then tries a chat that is not on the list, so a
broken allowlist shows up here rather than mid-conversation.

### `download` — the media file of one message

```bash
telegram-mcp download "https://t.me/c/1234567890/8/4242" --out ~/Downloads
telegram-mcp download team --message 4242 --out ~/Downloads --json
```

Both private (`t.me/c/…`, forum topics included) and public (`t.me/name/…`) links
are understood; the chat still has to be on the allowlist, and that is checked
before anything touches the network.

Before downloading, the command looks for a copy the desktop client already has.
Matching is by the document's own file name, falling back to an exact byte size,
in the directories given by `--lookup-dir` (by default `~/Downloads/Telegram Lite`
and `~/Downloads/Telegram Desktop`). A match is hard linked into `--out`, which
costs no disk space and no traffic. `--json` prints everything known about the
message: chat, sender, date, caption, media type, size, duration, and whether the
file came from the cache, a local copy or the network.

**This is still read-only.** Fetching a file is a read either way: the `download`
command serves the human at the terminal, `get_message_media` serves the model,
and both go through the same allowlist check before anything touches the network.

## Configuration

| Variable | Meaning |
|---|---|
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | credentials from <https://my.telegram.org/apps> |
| `TELEGRAM_SESSION_STRING` | Telethon `StringSession`, issued by `telegram-mcp login` |
| `TG_ALLOWED_CHATS_FILE` | path to the allowlist; default `~/.config/telegram-mcp/allowed_chats.json` (`$XDG_CONFIG_HOME` is honoured) |
| `TG_ALLOWED_CHATS_LOCAL_FILE` | optional personal additions to the allowlist; default `~/.config/telegram-mcp/allowed_chats.local.json` (`$XDG_CONFIG_HOME` is honoured) |
| `TG_MEDIA_DIR` | where `get_message_media` keeps downloaded files; default `~/.cache/telegram-mcp/media` (`$XDG_CACHE_HOME` is honoured) |

Every command also accepts `--allowlist PATH`. Editing the allowlist or the personal
additions takes effect when the MCP client restarts the server.

### Where the command line finds the credentials

`login`, `dialogs`, `check` and `download` look each variable up in this order and use the
first one that is set:

1. the environment — a plain `export TELEGRAM_API_ID=…`;
2. the `env` block of Claude Code's user settings, `~/.claude/settings.json` (if
   `CLAUDE_CONFIG_DIR` is set, Claude Code keeps its settings there instead, so the file is
   `$CLAUDE_CONFIG_DIR/settings.json`);
3. the `env` block of a Telegram MCP server in `~/.claude.json` — any server whose name
   contains `telegram`, which is where `claude mcp add -e …` puts it.

The same lookup finds `TG_ALLOWED_CHATS_FILE` and `TG_ALLOWED_CHATS_LOCAL_FILE`. A missing or
malformed file is skipped silently, and nothing read from these files is ever printed — except
the session string that `login` issues, on purpose. The server itself (`serve`) reads its
credentials from its process environment only; Claude Code fills that from the same `env` blocks.

**Using a Claude Code plugin?** If the server comes from a plugin whose setup has you keep the
credentials in the `env` block of `~/.claude/settings.json`:

```json
{
  "env": {
    "TELEGRAM_API_ID": "12345",
    "TELEGRAM_API_HASH": "abcdef0123456789abcdef0123456789",
    "TELEGRAM_SESSION_STRING": "…"
  }
}
```

then the command line reads that block too, and `telegram-mcp login` and `telegram-mcp dialogs`
work in a plain terminal with no `export`. Put `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` there
first, run `login`, and paste the session string it prints as `TELEGRAM_SESSION_STRING`.
`dialogs` needs no allowlist file, so it runs even when the main list lives inside the plugin and
`~/.config/telegram-mcp/allowed_chats.json` does not exist.

## Development

```bash
git clone https://github.com/nskondratev/telegram-mcp
cd telegram-mcp
uv run --extra dev pytest      # 254 tests, no account or network required
uv run --extra dev ruff check .
```

The tests replace Telethon with a fake, so the allowlist logic, the sanitiser and the
refusal path are all covered offline. `tests/test_server_integration.py` additionally
starts the real server over stdio and asserts that a forbidden chat is rejected *before*
any Telegram credentials are even looked at.

Layout: `core.py` — allowlist and sanitising; `handlers.py` — the read operations;
`links.py` — parsing t.me message links; `media.py` — locating and fetching a message's
media file; `client.py` — a Telethon wrapper that warms the dialog cache and builds the raw
reaction requests; `server.py` — MCP
tool definitions; `cli.py` — `serve` / `login` / `dialogs` / `check` / `download`.

The installable distribution is named `telegram-allowlist-mcp`; the import package and the
command are both `telegram_mcp` / `telegram-mcp`.

## License

[MIT](LICENSE)
