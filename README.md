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
| Tools exposed | 5 | 40–80 |

## Security model

- **Closed list.** `allowed_chats.json` is the single source of truth. Anything not in
  it is denied — by id, by alias and by title.
- **Denial happens before the network.** Every handler asks the allowlist first, so a
  forbidden chat never becomes a Telegram request. There is a test that asserts exactly
  this over real stdio.
- **The answer is re-checked.** After Telegram resolves an entity, its real id is matched
  against the allowlist again — a renamed or substituted chat cannot slip through.
- **No write path.** There is no `send_message` to disable: the code does not contain one.
  All five tools are annotated `readOnlyHint`.
- **Text is data, not instructions.** Message texts, names and titles are sanitised
  (zero-width characters, bidi overrides, control characters) and truncated, and the tool
  descriptions tell the model to treat them as untrusted input.
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

A chat is referenced by its alias (`team`), its exact title, or its id.

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
> config (for Claude Code, `~/.claude.json`), which is not in git.

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

**This is still read-only.** Downloading is a read; no tool that writes to
Telegram exists here. `download` is a CLI command and is deliberately not exposed
as an MCP tool — the assistant reads chats, the human fetches files.

## Configuration

| Variable | Meaning |
|---|---|
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | credentials from <https://my.telegram.org/apps> |
| `TELEGRAM_SESSION_STRING` | Telethon `StringSession`, issued by `telegram-mcp login` |
| `TG_ALLOWED_CHATS_FILE` | path to the allowlist; default `~/.config/telegram-mcp/allowed_chats.json` (`$XDG_CONFIG_HOME` is honoured) |

Every command also accepts `--allowlist PATH`. Editing the allowlist takes effect when the
MCP client restarts the server.

For convenience, `login`, `dialogs` and `check` fall back to the `env` block of a Telegram
MCP server in `~/.claude.json` when the variables are not exported — so they keep working
right after the server is wired into Claude Code.

## Development

```bash
git clone https://github.com/nskondratev/telegram-mcp
cd telegram-mcp
uv run --extra dev pytest      # 111 tests, no account or network required
uv run --extra dev ruff check .
```

The tests replace Telethon with a fake, so the allowlist logic, the sanitiser and the
refusal path are all covered offline. `tests/test_server_integration.py` additionally
starts the real server over stdio and asserts that a forbidden chat is rejected *before*
any Telegram credentials are even looked at.

Layout: `core.py` — allowlist and sanitising; `handlers.py` — the five read operations;
`links.py` — parsing t.me message links; `media.py` — locating and fetching a message's
media file; `client.py` — a Telethon wrapper that warms the dialog cache; `server.py` — MCP
tool definitions; `cli.py` — `serve` / `login` / `dialogs` / `check` / `download`.

The installable distribution is named `telegram-allowlist-mcp`; the import package and the
command are both `telegram_mcp` / `telegram-mcp`.

## License

[MIT](LICENSE)
