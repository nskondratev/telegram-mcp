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
from pathlib import Path

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
            if candidate.is_file() and candidate.stat().st_size == size:
                return candidate

    found: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        for path in base.iterdir():
            if not path.is_file() or path.stat().st_size != size:
                continue
            if ext and path.suffix.lower() != ext.lower():
                continue
            found.append(path)

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
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"
