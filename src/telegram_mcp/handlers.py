"""Reading Telegram on top of the allowlist: these operations, reads only.

Every function asks the allowlist first and only then touches the client, so a
chat outside the list never reaches the network at all. The client is passed in
(Telethon in production, a fake in tests), which keeps the logic testable
without an account.
"""
from __future__ import annotations

from .core import AllowList, ChatNotAllowed, sanitize_emoji, sanitize_text

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
#: Telegram hands out at most this many reactions per page.
REACTIONS_MAX_LIMIT = 100


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


def file_info(message) -> dict | None:
    """What is worth knowing about an attachment before deciding to fetch it.

    None when the message carries no file at all: an empty object here would
    read as "a file nothing is known about", which is a different thing.

    ``name`` and ``mime`` are uploader-controlled free-form strings, exactly
    like a message text, so they are sanitised the same way before reaching
    the model.
    """
    file = getattr(message, "file", None)
    if file is None:
        return None
    name = getattr(file, "name", None)
    mime = getattr(file, "mime_type", None)
    return {
        "size": getattr(file, "size", None),
        # Absent stays absent. A photo carries no file name at all, and sanitising
        # None into "" would claim it carries an empty one — the same distinction
        # this function makes between no file and a file nothing is known about.
        "name": sanitize_text(name, limit=200) if name else None,
        "ext": getattr(file, "ext", None) or "",
        "mime": sanitize_text(mime, limit=200) if mime else None,
        "duration": getattr(file, "duration", None),
    }


def _reaction_identity(reaction) -> tuple | None:
    """What tells one reaction from another, or None for an empty one."""
    emoticon = getattr(reaction, "emoticon", None)
    if emoticon:
        return ("emoji", emoticon)
    document_id = getattr(reaction, "document_id", None)
    if document_id is not None:
        return ("custom", document_id)
    if type(reaction).__name__ == "ReactionPaid":
        return ("paid",)
    return None


def _reaction_label(reaction, custom_emoji) -> dict | None:
    """A reaction as the model reads it.

    A custom emoji carries the emoji it stands for, when known, and its id as a
    string: document ids run past 2**53, and a JavaScript client would round
    them as numbers.
    """
    identity = _reaction_identity(reaction)
    if identity is None:
        return None
    if identity[0] == "emoji":
        return {"emoji": sanitize_emoji(identity[1])}
    if identity[0] == "custom":
        alt = custom_emoji.get(identity[1])
        return {"emoji": sanitize_emoji(alt) or None, "custom_emoji_id": str(identity[1])}
    return {"emoji": "⭐", "paid": True}


def _reactor(entry, names) -> dict:
    peer_id = _marked_id(getattr(entry, "peer_id", None))
    date = getattr(entry, "date", None)
    return {
        "id": peer_id,
        "name": names.get(peer_id),
        "date": date.isoformat() if date is not None else None,
    }


def reactions_info(message, names=None, custom_emoji=None, reactors=True) -> list[dict] | None:
    """The reactions on a message: each emoji, its count, and whether one of them is mine.

    None when there are none, for the same reason as file_info. ``by`` lists who
    reacted, as far as Telegram says in the message itself — usually only the
    latest few, and nobody at all in a channel. Names are looked up by the
    caller, see _reactor_names.
    """
    reactions = getattr(message, "reactions", None)
    results = getattr(reactions, "results", None) or []
    recent = (getattr(reactions, "recent_reactions", None) or []) if reactors else []
    names = names or {}
    custom_emoji = custom_emoji or {}
    items = []
    for result in results:
        label = _reaction_label(result.reaction, custom_emoji)
        if label is None:
            continue
        item = {**label, "count": result.count}
        if result.chosen_order is not None:
            item["mine"] = True
        identity = _reaction_identity(result.reaction)
        by = [_reactor(r, names) for r in recent if _reaction_identity(r.reaction) == identity]
        if by:
            item["by"] = by
        items.append(item)
    return items or None


def _names_of(entities) -> dict:
    return {_marked_id(entity): _display_name(entity) or None for entity in entities}


async def _reactor_names(client, entity, messages) -> dict:
    """Names of everyone listed under the reactions of these messages, in one request.

    Best-effort: a failed lookup leaves ids without names, it never fails the read.
    """
    message_ids = [
        m.id for m in messages if getattr(getattr(m, "reactions", None), "recent_reactions", None)
    ]
    if not message_ids:
        return {}
    try:
        return _names_of(await client.get_reaction_peers(entity, message_ids))
    except Exception:
        return {}


async def _custom_emoji_of(client, messages) -> dict:
    """The emoji behind every custom emoji reaction of these messages, in one request.

    Best-effort, like _reactor_names: without it a custom emoji keeps only its id.
    """
    document_ids = []
    for message in messages:
        reactions = getattr(message, "reactions", None)
        for result in getattr(reactions, "results", None) or []:
            identity = _reaction_identity(result.reaction)
            if identity is not None and identity[0] == "custom":
                document_ids.append(identity[1])
    if not document_ids:
        return {}
    try:
        return await client.get_custom_emoji(document_ids)
    except Exception:
        return {}


def _message_to_dict(message, names=None, custom_emoji=None) -> dict:
    date = getattr(message, "date", None)
    return {
        "id": getattr(message, "id", None),
        "date": date.isoformat() if date is not None else None,
        "sender": _display_name(getattr(message, "sender", None)),
        "sender_id": getattr(message, "sender_id", None),
        "text": sanitize_text(getattr(message, "text", None) or getattr(message, "message", None)),
        "reply_to": getattr(message, "reply_to_msg_id", None),
        "media": _media_type(message),
        "file": file_info(message),
        "reactions": reactions_info(message, names, custom_emoji),
    }


async def _messages_to_dicts(client, entity, messages) -> list[dict]:
    names = await _reactor_names(client, entity, messages)
    custom_emoji = await _custom_emoji_of(client, messages)
    return [_message_to_dict(m, names, custom_emoji) for m in messages]


def _clamp(limit, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


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
        "messages": await _messages_to_dicts(client, entity, messages),
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
        "messages": await _messages_to_dicts(client, entity, messages),
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
        for item in await _messages_to_dicts(client, entity, messages):
            item["chat_alias"] = entry.alias
            item["chat_title"] = _display_name(entity, entry.title)
            found.append(item)

    found.sort(key=lambda m: m["date"] or "", reverse=True)
    result = {"query": sanitize_text(query, limit=200), "messages": found[:limit]}
    if errors:
        result["errors"] = errors
    return result


async def get_message_reactions(
    client, allowlist: AllowList, chat, message_id, reaction=None, limit=DEFAULT_LIMIT, offset=None
) -> dict:
    """Who reacted to one message and with what, a page at a time.

    ``counts`` sums the reactions up; ``reactions`` lists the people behind them,
    narrowed to one emoji by ``reaction``. ``total`` is how many reactions match
    the request, so the list is complete once it holds that many. Telegram hides
    the list in channels and in chats that choose to: then only counts come back.
    """
    entry, entity = await _entity_of(client, allowlist, chat)
    message_id = int(message_id)
    message = await client.get_messages(entity, ids=message_id)
    if message is None:
        raise ValueError(f"Message {message_id} was not found in {entry.alias!r}.")

    custom_emoji = await _custom_emoji_of(client, [message])
    counts = reactions_info(message, custom_emoji=custom_emoji, reactors=False) or []
    result = {
        "chat": _chat_ref(entry, entity),
        "message_id": message_id,
        "counts": counts,
        "total": sum(item["count"] for item in counts),
        "reactions": [],
    }
    if not counts:
        return result
    if not getattr(message.reactions, "can_see_list", False):
        result["note"] = (
            "Telegram does not disclose who reacted to this message (a channel, or a chat "
            "that hides the list) — only the counts are known."
        )
        return result

    wanted = (str(reaction).strip() or None) if reaction is not None else None
    page = await client.get_reactions_list(
        entity,
        message_id,
        reaction=wanted,
        limit=_clamp(limit, maximum=REACTIONS_MAX_LIMIT),
        offset=offset or None,
    )
    names = _names_of([*page.users, *page.chats])
    result["total"] = page.count
    result["reactions"] = [
        {**_reactor(r, names), **(_reaction_label(r.reaction, custom_emoji) or {})} for r in page.reactions
    ]
    if page.next_offset:
        result["next_offset"] = page.next_offset
    return result
