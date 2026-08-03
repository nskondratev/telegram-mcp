"""Getting the media file of a message: the copy on disk first, the network second.

The desktop client usually has already downloaded the video the user is asking
about, and these files run into hundreds of megabytes. Matching what Telegram
reports (exact byte size, and the document's own file name when there is one)
against the client's download folder turns a re-download into a hard link.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from .core import AllowList, sanitize_text
from .handlers import _display_name, _entity_of, _media_type

#: Where the desktop clients keep downloads. Overridable per call — nothing here is mandatory.
DEFAULT_LOOKUP_DIRS = ("~/Downloads/Telegram Lite", "~/Downloads/Telegram Desktop")

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def canonical_name(alias: str, message_id: int, ext: str) -> str:
    """Deterministic file name — this is what makes a repeated download a no-op."""
    suffix = ""
    if ext:
        suffix = ext if ext.startswith(".") else f".{ext}"
    return f"{_UNSAFE.sub('_', str(alias))}-{int(message_id)}{suffix}"


def find_local_copy(dirs, size: int, filename: str | None = None, ext: str | None = None) -> Path | None:
    """A file the desktop client has already downloaded, or None.

    The document's own name wins when Telegram reports one; otherwise the search
    falls back to an exact byte size, which for a video is unambiguous in
    practice. Several candidates of the same size — the most recent one.
    """
    bases = [Path(raw).expanduser() for raw in dirs]

    if filename:
        for base in bases:
            candidate = base / filename
            try:
                if candidate.is_file() and candidate.stat().st_size == size:
                    return candidate
            except OSError:
                continue

    # Normalize ext to have leading dot, matching canonical_name behavior
    normalized_ext = None
    if ext:
        normalized_ext = ext if ext.startswith(".") else f".{ext}"

    found: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        try:
            for path in base.iterdir():
                try:
                    if not path.is_file() or path.stat().st_size != size:
                        continue
                    if normalized_ext and path.suffix.lower() != normalized_ext.lower():
                        continue
                    found.append(path)
                except OSError:
                    continue
        except OSError:
            continue

    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)


def link_or_copy(src: Path, dst: Path) -> str:
    """Put the local copy into the destination, cheaply if possible.

    A hard link costs no disk space and survives cache cleanup gracefully:
    removing one name only drops a reference, the client's own file stays.
    Across volumes hard links are impossible, so it degrades to a copy.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file first, then atomically replace the destination.
    # This ensures dst is only removed once the replacement is complete.
    fd, tmp_name = tempfile.mkstemp(dir=dst.parent)
    os.close(fd)  # Close the file descriptor; we only need the path
    tmp_path = Path(tmp_name)
    try:
        tmp_path.unlink()  # Remove the empty file created by mkstemp
        try:
            os.link(src, tmp_path)
            result = "link"
        except OSError:
            shutil.copy2(src, tmp_path)
            result = "copy"
        os.replace(tmp_path, dst)
        return result
    except Exception:
        # Clean up the temp file if the operation failed
        tmp_path.unlink(missing_ok=True)
        raise


def _file_meta(message) -> dict:
    """Size, name, extension and duration of the message's media, if any."""
    file = getattr(message, "file", None)
    if file is None:
        return {}
    return {
        "size": getattr(file, "size", None),
        "name": getattr(file, "name", None),
        "ext": getattr(file, "ext", None) or "",
        "duration": getattr(file, "duration", None),
    }


async def download_message_media(
    client,
    allowlist: AllowList,
    chat,
    message_id: int,
    out_dir,
    lookup_dirs=None,
) -> dict:
    """The media file of one message, on disk, plus everything known about it.

    The allowlist is checked before anything touches the network, exactly as in
    the read tools. Downloading is a read: no write capability is added anywhere.
    """
    entry, entity = await _entity_of(client, allowlist, chat)
    message = await client.get_messages(entity, ids=int(message_id))
    if isinstance(message, list):
        message = message[0] if message else None
    if message is None:
        raise ValueError(f"Message {message_id} not found in {entry.alias!r}.")

    meta = _file_meta(message)
    size = meta.get("size")
    if not size:
        raise ValueError(
            f"Message {message_id} in {entry.alias!r} carries no downloadable media "
            f"(media: {_media_type(message) or 'none'})."
        )

    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / canonical_name(entry.alias, message_id, meta.get("ext") or "")

    source = "network"
    origin = None
    if target.is_file() and target.stat().st_size == size:
        source = "cache"
    else:
        local = find_local_copy(
            lookup_dirs if lookup_dirs is not None else DEFAULT_LOOKUP_DIRS,
            size=size,
            filename=meta.get("name"),
            ext=meta.get("ext") or None,
        )
        if local is not None:
            link_or_copy(local, target)
            source = "local"
            origin = str(local)
        else:
            await client.download_media(message, file=str(target))

    date = getattr(message, "date", None)
    return {
        "path": str(target),
        "chat": {"alias": entry.alias, "id": entry.id, "title": _display_name(entity, entry.title)},
        "message_id": int(message_id),
        "date": date.isoformat() if date is not None else None,
        "sender": _display_name(getattr(message, "sender", None)),
        "caption": sanitize_text(getattr(message, "text", None) or ""),
        "media_type": _media_type(message),
        "size": size,
        "duration": meta.get("duration"),
        "file_name": meta.get("name"),
        "source": source,
        "origin": origin,
    }
