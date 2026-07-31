"""Chat allowlist and content sanitising — the security core of the server.

Everything that decides "may this chat be touched at all" lives here, away from
Telethon and MCP, so it can be tested without network access or a real account.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TEXT_LIMIT = 4000

#: Environment variable pointing at the allowlist file.
ALLOWLIST_ENV = "TG_ALLOWED_CHATS_FILE"

# Invisible characters: zero-width spaces, bidi overrides, word joiners, BOM.
# They get stripped — prompt injection uses them to hide instructions in text.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


class ChatNotAllowed(Exception):
    """The chat is not in the allowlist and must not be touched.

    Deliberately derived from Exception rather than PermissionError:
    PermissionError is an OSError, and the MCP stdio transport mistakes it for a
    broken pipe, so the client gets no answer at all instead of a clear refusal.
    """


def default_allowlist_path() -> Path:
    """Where the allowlist lives unless told otherwise.

    ``$TG_ALLOWED_CHATS_FILE`` wins; otherwise ``~/.config/telegram-mcp/
    allowed_chats.json`` (``$XDG_CONFIG_HOME`` is honoured). Keeping the file
    outside the installation directory is what makes it safe to run this server
    straight from a git URL — the real chat ids never live next to the code.
    """
    from_env = os.environ.get(ALLOWLIST_ENV)
    if from_env:
        return Path(from_env).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "telegram-mcp" / "allowed_chats.json"


def normalize_chat_id(value) -> int:
    """Coerce a chat id to int. Strings are fine, anything else is a ValueError."""
    if isinstance(value, bool):
        raise ValueError(f"Invalid chat id: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"Invalid chat id: {value!r}") from exc
    raise ValueError(f"Invalid chat id: {value!r}")


@dataclass(frozen=True)
class ChatEntry:
    alias: str
    id: int
    title: str
    note: str = ""


class AllowList:
    """A closed list of chats: anything missing from the config is denied."""

    def __init__(self, entries: list[ChatEntry]):
        self.entries = entries
        self._by_id = {entry.id: entry for entry in entries}
        self._by_alias = {entry.alias.casefold(): entry for entry in entries}
        self._by_title = {entry.title.casefold(): entry for entry in entries if entry.title}

    def resolve(self, ref) -> int:
        """Chat reference (id, alias or exact title) → id from the allowlist."""
        entry = self.entry(ref)
        return entry.id

    def entry(self, ref) -> ChatEntry:
        try:
            chat_id = normalize_chat_id(ref)
        except ValueError:
            key = str(ref).strip().casefold()
            found = self._by_alias.get(key) or self._by_title.get(key)
            if found is None:
                raise self._denied(ref) from None
            return found

        found = self._by_id.get(chat_id)
        if found is None:
            raise self._denied(ref)
        return found

    def contains(self, chat_id) -> bool:
        try:
            return normalize_chat_id(chat_id) in self._by_id
        except ValueError:
            return False

    def _denied(self, ref) -> ChatNotAllowed:
        allowed = ", ".join(sorted(entry.alias for entry in self.entries)) or "(the list is empty)"
        return ChatNotAllowed(
            f"Chat {ref!r} is not in the allowlist — access denied. "
            f"Allowed chats: {allowed}. "
            "The list is edited by hand in allowed_chats.json."
        )


def load_allowlist(path) -> AllowList:
    """Read allowed_chats.json. Any ambiguity is a startup error, never a guess."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Allowlist file not found: {path}. Without it the server has no idea which chats are allowed."
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    raw_chats = data.get("chats", [])
    if not isinstance(raw_chats, list):
        raise ValueError(f"{path}: 'chats' must be a list")

    entries: list[ChatEntry] = []
    seen_aliases: set[str] = set()
    seen_ids: set[int] = set()
    for index, raw in enumerate(raw_chats):
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: entry #{index} must be an object")
        if "id" not in raw:
            raise ValueError(f"{path}: entry #{index} has no 'id' field")
        chat_id = normalize_chat_id(raw["id"])
        alias = str(raw.get("alias") or chat_id).strip()
        if alias.casefold() in seen_aliases:
            raise ValueError(f"{path}: alias {alias!r} is used twice")
        if chat_id in seen_ids:
            raise ValueError(f"{path}: id {chat_id} is used twice")
        seen_aliases.add(alias.casefold())
        seen_ids.add(chat_id)
        entries.append(
            ChatEntry(
                alias=alias,
                id=chat_id,
                title=str(raw.get("title") or "").strip(),
                note=str(raw.get("note") or "").strip(),
            )
        )

    return AllowList(entries)


def sanitize_text(text, limit: int = DEFAULT_TEXT_LIMIT) -> str:
    """Clean text coming from Telegram: it is untrusted input, not model instructions."""
    if not text:
        return ""
    text = _INVISIBLE.sub("", str(text))
    text = "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    if len(text) > limit:
        text = text[:limit] + "…"
    return text
