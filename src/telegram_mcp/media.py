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

from .core import AllowList, MediaTooLarge, sanitize_text
from .handlers import _display_name, _entity_of, _media_type, file_info

#: Where the desktop clients keep downloads. Overridable per call — nothing here is mandatory.
DEFAULT_LOOKUP_DIRS = ("~/Downloads/Telegram Lite", "~/Downloads/Telegram Desktop")

#: Below this size, matching by byte count alone risks a false positive: two
#: unrelated voice notes or Telegram's round, 60-second-capped "video circles"
#: can easily share a byte count, and both carry no filename at all — unlike a
#: document, so they always reach this fallback rather than the exact match
#: above. The floor sits comfortably above what a circle produces even at a
#: generous bitrate (well under a minute of video), and far below any video
#: actually worth fetching this way. Filename matching is not affected by it.
MIN_SIZE_FOR_SIZE_ONLY_FALLBACK = 20_000_000  # bytes

#: Default ceiling for pulling a file over the network on the model's behalf.
#: Screenshots and documents are far below it; a long screen recording is above,
#: and fetching one is a decision worth making explicitly.
DEFAULT_MAX_SIZE = 50_000_000  # bytes

#: An image above this size is not worth pushing through the model's context:
#: the API caps a single picture at five megabytes, and a screenshot is rarely
#: over one. Bigger pictures still land on disk and can be opened from there.
INLINE_MAX_BYTES = 4_000_000  # bytes

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def inline_verdict(mime, size) -> tuple[bool, str | None]:
    """Whether the downloaded file should ride along as a picture in the answer.

    Returns the verdict and, when it is negative, the reason in a form the model
    can act on — everything not inlined is still on disk at the returned path.
    """
    if not mime or not str(mime).startswith("image/"):
        return False, f"not an image (mime: {mime or 'unknown'}) — read it from path"
    if size is None:
        return False, "size unknown — read it from path"
    if size > INLINE_MAX_BYTES:
        return False, (
            f"image of {size} bytes is above the {INLINE_MAX_BYTES}-byte inline limit — "
            "read it from path"
        )
    return True, None


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

    Below ``MIN_SIZE_FOR_SIZE_ONLY_FALLBACK``, the size-only fallback is
    skipped entirely and only a filename match (above) is trusted: voice
    notes and video circles have no filename and so always reach the
    fallback, and short clips collide on size far more easily than a
    gigabyte-scale screen recording does.
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

    if size < MIN_SIZE_FOR_SIZE_ONLY_FALLBACK:
        return None

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


async def download_message_media(
    client,
    allowlist: AllowList,
    chat,
    message_id: int,
    out_dir,
    lookup_dirs=None,
    progress_callback=None,
    *,
    max_size: int | None = None,
) -> dict:
    """The media file of one message, on disk, plus everything known about it.

    The allowlist is checked before anything touches the network, exactly as in
    the read tools. Downloading is a read: no write capability is added anywhere.

    ``progress_callback`` only ever reaches Telethon on the network branch
    below: a cache hit returns before anything is read, and a local copy is
    hard linked (or copied) in one call with nothing to report progress on.

    ``max_size`` caps a *network* download only: a file already in ``out_dir``
    and a copy hard linked from the desktop client cost nothing and are never
    refused. ``None`` means no cap at all, which is what the CLI passes.
    """
    entry, entity = await _entity_of(client, allowlist, chat)
    message = await client.get_messages(entity, ids=int(message_id))
    if isinstance(message, list):
        message = message[0] if message else None
    if message is None:
        raise ValueError(f"Message {message_id} not found in {entry.alias!r}.")

    meta = file_info(message) or {}
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
            if max_size is not None and size > max_size:
                raise MediaTooLarge(
                    f"Message {message_id} in {entry.alias!r} carries "
                    f"{meta.get('name') or _media_type(message) or 'a file'} of {size} bytes, "
                    f"above the max_size limit of {max_size} bytes for a network download. "
                    "Call again with a bigger max_size if it is worth fetching."
                )
            await client.download_media(
                message, file=str(target), progress_callback=progress_callback
            )

    date = getattr(message, "date", None)
    return {
        "path": str(target),
        "chat": {"alias": entry.alias, "id": entry.id, "title": _display_name(entity, entry.title)},
        "message_id": int(message_id),
        "date": date.isoformat() if date is not None else None,
        "sender": _display_name(getattr(message, "sender", None)),
        "caption": sanitize_text(getattr(message, "text", None) or ""),
        "media_type": _media_type(message),
        "mime": meta.get("mime"),
        "size": size,
        "duration": meta.get("duration"),
        "file_name": meta.get("name"),
        "source": source,
        "origin": origin,
    }
