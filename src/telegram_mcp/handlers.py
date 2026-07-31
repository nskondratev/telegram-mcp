"""Reading Telegram on top of the allowlist: these five operations, reads only.

Every function asks the allowlist first and only then touches the client, so a
chat outside the list never reaches the network at all. The client is passed in
(Telethon in production, a fake in tests), which keeps the logic testable
without an account.
"""
from __future__ import annotations

from .core import AllowList, ChatNotAllowed, sanitize_text

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


def _marked_id(entity) -> int | None:
    """Peer id in the same form the allowlist stores it (-100… for channels)."""
    try:
        from telethon.utils import get_peer_id  # noqa: PLC0415 — optional in tests

        return get_peer_id(entity)
    except Exception:
        return getattr(entity, "id", None)


def _display_name(entity, fallback: str = "") -> str:
    """Chat name or person name: groups have a title, users have first/last name."""
    if entity is None:
        return fallback
    title = getattr(entity, "title", None)
    if title:
        return sanitize_text(title, limit=120)
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
    name = " ".join(p for p in parts if p).strip()
    if not name:
        name = getattr(entity, "username", None) or ""
    return sanitize_text(name, limit=120) or fallback


def _chat_ref(entry, entity) -> dict:
    return {"alias": entry.alias, "id": entry.id, "title": _display_name(entity, entry.title)}


def _media_type(message) -> str | None:
    media = getattr(message, "media", None)
    if media is None:
        return None
    name = type(media).__name__
    return name[len("MessageMedia") :] if name.startswith("MessageMedia") else name


def _message_to_dict(message) -> dict:
    date = getattr(message, "date", None)
    return {
        "id": getattr(message, "id", None),
        "date": date.isoformat() if date is not None else None,
        "sender": _display_name(getattr(message, "sender", None)),
        "sender_id": getattr(message, "sender_id", None),
        "text": sanitize_text(getattr(message, "text", None) or getattr(message, "message", None)),
        "reply_to": getattr(message, "reply_to_msg_id", None),
        "media": _media_type(message),
    }


def _clamp(limit, default: int = DEFAULT_LIMIT) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_LIMIT))


async def _entity_of(client, allowlist: AllowList, chat):
    """Resolve a chat strictly through the allowlist and re-check the id we got back."""
    entry = allowlist.entry(chat)
    entity = await client.get_entity(entry.id)
    actual = _marked_id(entity)
    if not allowlist.contains(actual):
        raise ChatNotAllowed(
            f"Telegram returned chat id {actual} for {entry.alias!r}, which is not in the allowlist — "
            "request rejected."
        )
    return entry, entity


async def list_chats(client, allowlist: AllowList) -> dict:
    """Allowed chats together with their current titles."""
    chats = []
    for entry in allowlist.entries:
        item = {"alias": entry.alias, "id": entry.id, "title": entry.title, "note": entry.note}
        try:
            _, entity = await _entity_of(client, allowlist, entry.id)
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        else:
            item["title"] = _display_name(entity, entry.title)
            item["username"] = getattr(entity, "username", None)
        chats.append(item)
    return {"chats": chats}


async def get_chat_info(client, allowlist: AllowList, chat) -> dict:
    """Metadata of an allowed chat."""
    entry, entity = await _entity_of(client, allowlist, chat)
    return {
        "alias": entry.alias,
        "id": entry.id,
        "title": _display_name(entity, entry.title),
        "username": getattr(entity, "username", None),
        "participants_count": getattr(entity, "participants_count", None),
        "note": entry.note,
    }


async def get_messages(client, allowlist: AllowList, chat, limit=DEFAULT_LIMIT, before_id=None) -> dict:
    """Latest messages of an allowed chat (newest first)."""
    entry, entity = await _entity_of(client, allowlist, chat)
    kwargs = {"limit": _clamp(limit)}
    if before_id:
        kwargs["max_id"] = int(before_id)
    messages = await client.get_messages(entity, **kwargs)
    return {
        "chat": _chat_ref(entry, entity),
        "messages": [_message_to_dict(m) for m in messages],
    }


async def get_message_context(client, allowlist: AllowList, chat, message_id, around=5) -> dict:
    """Messages around a specific one — enough to reconstruct a thread."""
    entry, entity = await _entity_of(client, allowlist, chat)
    around = _clamp(around, default=5)
    messages = await client.get_messages(
        entity, limit=around * 2 + 1, offset_id=int(message_id), add_offset=-around
    )
    return {
        "chat": _chat_ref(entry, entity),
        "target_id": int(message_id),
        "messages": [_message_to_dict(m) for m in messages],
    }


async def search_messages(client, allowlist: AllowList, query, chat=None, limit=DEFAULT_LIMIT) -> dict:
    """Full-text search: inside one allowed chat, or across all of them."""
    entries = [allowlist.entry(chat)] if chat is not None else list(allowlist.entries)
    limit = _clamp(limit)

    found = []
    errors = []
    for entry in entries:
        try:
            _, entity = await _entity_of(client, allowlist, entry.id)
            messages = await client.get_messages(entity, limit=limit, search=str(query))
        except ChatNotAllowed:
            raise
        except Exception as exc:
            errors.append({"alias": entry.alias, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for message in messages:
            item = _message_to_dict(message)
            item["chat_alias"] = entry.alias
            item["chat_title"] = _display_name(entity, entry.title)
            found.append(item)

    found.sort(key=lambda m: m["date"] or "", reverse=True)
    result = {"query": sanitize_text(query, limit=200), "messages": found[:limit]}
    if errors:
        result["errors"] = errors
    return result
