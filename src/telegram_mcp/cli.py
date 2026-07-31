"""Command line entry point: serve, login, dialogs, check.

    telegram-mcp serve     run the MCP server over stdio (this is what the client starts)
    telegram-mcp login     issue a session string (asks for phone, code and 2FA password)
    telegram-mcp dialogs   list your chats with their ids, to fill in the allowlist
    telegram-mcp check     verify that every allowed chat resolves and reads

`dialogs` deliberately lives here and not in the MCP server: the full list of
your conversations is for your terminal only, the assistant sees allowlisted
chats and nothing else.

Credentials are read from the environment; failing that, from the env block of a
Telegram MCP server in ~/.claude.json. They are never printed, except by `login`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from . import handlers
from .client import CachedClient
from .core import ALLOWLIST_ENV, default_allowlist_path, load_allowlist

REPO_URL = "https://github.com/nskondratev/telegram-mcp"


def read_env(name: str) -> str | None:
    """Environment variable, falling back to a Telegram MCP server in ~/.claude.json.

    Claude Code keeps MCP credentials in ~/.claude.json rather than in the shell,
    so `check` and `dialogs` keep working right after the server is wired up,
    with no extra exports. Any server whose name mentions "telegram" counts.
    """
    value = os.environ.get(name)
    if value:
        return value
    config = Path.home() / ".claude.json"
    if not config.exists():
        return None
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    servers = data.get("mcpServers") or {}
    if not isinstance(servers, dict):
        return None
    for server_name, server in servers.items():
        if "telegram" not in str(server_name).lower() or not isinstance(server, dict):
            continue
        found = (server.get("env") or {}).get(name)
        if found:
            return found
    return None


def credentials() -> tuple[int, str]:
    api_id = read_env("TELEGRAM_API_ID")
    api_hash = read_env("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH are not set. Create an application at "
            "https://my.telegram.org/apps and pass them as environment variables."
        )
    return int(api_id), api_hash


def build_client(session: str | None):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id, api_hash = credentials()
    return TelegramClient(
        StringSession(session or ""),
        api_id,
        api_hash,
        receive_updates=False,
        device_model="Claude MCP (read-only)",
        system_version="1.0",
        app_version="1.0",
    )


def session_string() -> str:
    session = read_env("TELEGRAM_SESSION_STRING")
    if not session:
        raise SystemExit("TELEGRAM_SESSION_STRING is not set — run `telegram-mcp login` first.")
    return session


async def _connected(session: str):
    client = build_client(session)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit("The session string is invalid — issue a new one with `telegram-mcp login`.")
    return client


async def cmd_login() -> None:
    client = build_client(None)
    await client.start()  # Telethon asks for the phone number, the code and the 2FA password
    me = await client.get_me()
    session = client.session.save()
    await client.disconnect()

    print(f"\nSigned in as: {me.first_name or ''} (@{me.username or 'no username'}, id {me.id})")
    print("\nSession string (grants full access to the account — never commit it):\n")
    print(session)
    print(
        "\nNext:\n"
        "  claude mcp add telegram -s user \\\n"
        "    -e TELEGRAM_API_ID=<api_id> -e TELEGRAM_API_HASH=<api_hash> \\\n"
        "    -e TELEGRAM_SESSION_STRING=<the string above> \\\n"
        "    -e TG_ALLOWED_CHATS_FILE=<path to allowed_chats.json> \\\n"
        f"    -- uvx --from git+{REPO_URL} telegram-mcp serve"
    )


async def cmd_dialogs(pattern: str | None, limit: int, as_json: bool, allowlist_path: Path) -> None:
    client = await _connected(session_string())

    from telethon.utils import get_peer_id

    rows = []
    async for dialog in client.iter_dialogs(limit=limit):
        title = dialog.name or ""
        if pattern and pattern.casefold() not in title.casefold():
            continue
        kind = "channel" if dialog.is_channel else "group" if dialog.is_group else "direct"
        rows.append({"id": get_peer_id(dialog.entity), "title": title, "kind": kind})
    await client.disconnect()

    if as_json:
        print(
            json.dumps(
                {"chats": [{"alias": "", "id": r["id"], "title": r["title"]} for r in rows]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    for row in rows:
        print(f"{row['id']:>16}  {row['kind']:<8} {row['title']}")
    print(f"\nTotal: {len(rows)}. Copy the ones you need into {allowlist_path}, giving each an alias.")


async def cmd_check(allowlist_path: Path) -> None:
    allowlist = load_allowlist(allowlist_path)
    print(f"Allowlist: {allowlist_path} — chats: {len(allowlist.entries)}")
    if not allowlist.entries:
        raise SystemExit("The list is empty: the server would start, but there would be nothing to read.")

    client = await _connected(session_string())
    cached = CachedClient(client)
    ok = True
    for entry in allowlist.entries:
        try:
            info = await handlers.get_chat_info(cached, allowlist, entry.id)
            messages = await handlers.get_messages(cached, allowlist, entry.id, limit=1)
            last = messages["messages"][0]["date"] if messages["messages"] else "no messages"
            print(f"  OK   {entry.alias:<16} {info['title']}  (latest: {last})")
        except Exception as exc:
            ok = False
            print(f"  FAIL {entry.alias:<16} {type(exc).__name__}: {exc}")

    print("\nDenial check:")
    try:
        await handlers.get_messages(cached, allowlist, -1000000000001, limit=1)
    except Exception as exc:
        print(f"  OK   a chat outside the allowlist was rejected ({type(exc).__name__})")
    else:
        ok = False
        print("  FAIL a chat outside the allowlist was NOT rejected — this is a bug, do not use the server")

    await client.disconnect()
    if not ok:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram-mcp",
        description="Read-only MCP server for Telegram with a per-chat allowlist.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--allowlist",
        metavar="PATH",
        help=f"path to allowed_chats.json (default: ${ALLOWLIST_ENV} or {default_allowlist_path()})",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the MCP server over stdio")
    sub.add_parser("login", help="issue a session string")
    dialogs = sub.add_parser("dialogs", help="list chats with their ids for the allowlist")
    dialogs.add_argument("--filter", dest="pattern", help="substring of the chat title")
    dialogs.add_argument("--limit", type=int, default=200, help="how many dialogs to scan")
    dialogs.add_argument("--json", action="store_true", help="print an allowed_chats.json skeleton")
    sub.add_parser("check", help="verify the allowlist against live Telegram")
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    allowlist_path = Path(args.allowlist).expanduser() if args.allowlist else default_allowlist_path()

    if args.command == "serve":
        from .server import serve

        serve(allowlist_path)
    elif args.command == "login":
        asyncio.run(cmd_login())
    elif args.command == "dialogs":
        asyncio.run(cmd_dialogs(args.pattern, args.limit, args.json, allowlist_path))
    else:
        asyncio.run(cmd_check(allowlist_path))


if __name__ == "__main__":
    main()
