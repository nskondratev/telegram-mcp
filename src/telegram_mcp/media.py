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
