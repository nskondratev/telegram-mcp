"""Tests for locating and linking a message's media file."""
import asyncio
import datetime as dt
import os
from pathlib import Path
from unittest import mock

import pytest

from telegram_mcp.core import AllowList, ChatEntry, ChatNotAllowed, MediaTooLarge
from telegram_mcp.media import (
    canonical_name,
    download_message_media,
    find_local_copy,
    inline_verdict,
    link_or_copy,
)


def write(path, size, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestCanonicalName:
    def test_builds_name_from_alias_and_id(self):
        assert canonical_name("team", 4242, ".mp4") == "team-4242.mp4"

    def test_accepts_extension_without_dot(self):
        assert canonical_name("team", 4242, "mp4") == "team-4242.mp4"

    def test_tolerates_missing_extension(self):
        assert canonical_name("team", 4242, "") == "team-4242"

    def test_replaces_unsafe_characters_in_alias(self):
        assert canonical_name("team chat/2", 7, ".mov") == "team_chat_2-7.mov"


class TestFindLocalCopy:
    @pytest.fixture(autouse=True)
    def _no_size_floor(self, monkeypatch):
        """These tests exercise matching mechanics, not the anti-collision floor."""
        monkeypatch.setattr("telegram_mcp.media.MIN_SIZE_FOR_SIZE_ONLY_FALLBACK", 0)

    def test_finds_by_exact_document_name(self, tmp_path):
        target = write(tmp_path / "Screen Recording.mov", 1024)
        write(tmp_path / "other.mov", 2048)
        assert find_local_copy([tmp_path], size=1024, filename="Screen Recording.mov") == target

    def test_ignores_name_match_with_wrong_size(self, tmp_path):
        write(tmp_path / "Screen Recording.mov", 999)
        assert find_local_copy([tmp_path], size=1024, filename="Screen Recording.mov") is None

    def test_finds_by_exact_size_when_name_is_unknown(self, tmp_path):
        target = write(tmp_path / "video_2026-08-03.mp4", 4096)
        write(tmp_path / "unrelated.mp4", 4095)
        assert find_local_copy([tmp_path], size=4096, ext=".mp4") == target

    def test_size_match_respects_extension(self, tmp_path):
        write(tmp_path / "document.pdf", 4096)
        assert find_local_copy([tmp_path], size=4096, ext=".mp4") is None

    def test_picks_the_most_recent_of_several_candidates(self, tmp_path):
        write(tmp_path / "old.mp4", 4096, mtime=1_700_000_000)
        newer = write(tmp_path / "new.mp4", 4096, mtime=1_800_000_000)
        assert find_local_copy([tmp_path], size=4096, ext=".mp4") == newer

    def test_searches_every_given_directory(self, tmp_path):
        first = tmp_path / "a"
        second = tmp_path / "b"
        first.mkdir()
        target = write(second / "video.mp4", 4096)
        assert find_local_copy([first, second], size=4096, ext=".mp4") == target

    def test_missing_directory_is_not_an_error(self, tmp_path):
        assert find_local_copy([tmp_path / "nope"], size=4096, ext=".mp4") is None

    def test_unreadable_directory_is_skipped(self, tmp_path):
        # Create a file in an unreadable directory; should skip it and continue
        unreadable = tmp_path / "noaccess"
        unreadable.mkdir()
        write(unreadable / "file.mp4", 4096)
        # Create a matching file in a readable directory
        target = write(tmp_path / "readable" / "file.mp4", 4096)
        # Make the first directory unreadable
        try:
            unreadable.chmod(0o000)
            # Should skip the unreadable dir and find the file in readable dir
            assert find_local_copy([unreadable, tmp_path / "readable"], size=4096, ext=".mp4") == target
        finally:
            # Restore permissions so pytest can clean up
            unreadable.chmod(0o755)

    def test_broken_symlink_is_skipped(self, tmp_path):
        # Create a broken symlink
        broken = tmp_path / "broken.mp4"
        broken.symlink_to("/nonexistent/path")
        # Create a valid file with matching size
        target = write(tmp_path / "valid.mp4", 4096)
        # Should skip broken symlink and find the valid file
        assert find_local_copy([tmp_path], size=4096, ext=".mp4") == target

    def test_extension_without_dot_is_normalized(self, tmp_path):
        # Test that ext="mp4" (without dot) matches ".mp4" files, like canonical_name does
        target = write(tmp_path / "video.mp4", 4096)
        assert find_local_copy([tmp_path], size=4096, ext="mp4") == target

    def test_relative_traversal_in_filename_cannot_escape_the_search_dir(self, tmp_path):
        search_dir = tmp_path / "search"
        search_dir.mkdir()
        # One level above search_dir — reachable from it by "../outside.mov" if
        # the traversal were not stripped.
        write(tmp_path / "outside.mov", 4096)
        assert find_local_copy([search_dir], size=4096, filename="../outside.mov") is None

    def test_relative_traversal_in_filename_still_matches_the_final_component(self, tmp_path):
        search_dir = tmp_path / "search"
        target = write(search_dir / "outside.mov", 4096)
        assert find_local_copy([search_dir], size=4096, filename="../../outside.mov") == target

    def test_absolute_filename_cannot_escape_the_search_dir(self, tmp_path):
        search_dir = tmp_path / "search"
        search_dir.mkdir()
        elsewhere = write(tmp_path / "elsewhere" / "secret.mov", 4096)
        assert find_local_copy([search_dir], size=4096, filename=str(elsewhere)) is None


class TestFindLocalCopySizeFloor:
    """A short voice note or video circle must not be matched by size alone."""

    def test_size_only_match_is_skipped_below_the_floor(self, tmp_path, monkeypatch):
        monkeypatch.setattr("telegram_mcp.media.MIN_SIZE_FOR_SIZE_ONLY_FALLBACK", 1000)
        write(tmp_path / "voice-note-a.oga", 500)
        # A same-size file exists, but 500 bytes is below the floor, so it must
        # not be trusted as a match — unlike the filename-matched case above.
        assert find_local_copy([tmp_path], size=500) is None

    def test_size_only_match_still_works_at_or_above_the_floor(self, tmp_path, monkeypatch):
        monkeypatch.setattr("telegram_mcp.media.MIN_SIZE_FOR_SIZE_ONLY_FALLBACK", 1000)
        target = write(tmp_path / "screen-recording.mp4", 1000)
        assert find_local_copy([tmp_path], size=1000) == target

    def test_filename_match_ignores_the_floor(self, tmp_path, monkeypatch):
        monkeypatch.setattr("telegram_mcp.media.MIN_SIZE_FOR_SIZE_ONLY_FALLBACK", 1_000_000)
        target = write(tmp_path / "voice-note.oga", 500)
        # A tiny voice note, floor set high enough to forbid the size-only path
        # entirely — an exact filename match must still win.
        assert find_local_copy([tmp_path], size=500, filename="voice-note.oga") == target

    def test_default_floor_is_defensible(self):
        from telegram_mcp.media import MIN_SIZE_FOR_SIZE_ONLY_FALLBACK

        # Well above a 60-second Telegram video circle even at a generous
        # bitrate, and well below the multi-hundred-MB videos this exists for.
        assert 10_000_000 <= MIN_SIZE_FOR_SIZE_ONLY_FALLBACK <= 100_000_000


class TestLinkOrCopy:
    def test_hard_links_the_file(self, tmp_path):
        src = write(tmp_path / "src.mp4", 128)
        dst = tmp_path / "cache" / "team-1.mp4"
        assert link_or_copy(src, dst) == "link"
        assert dst.read_bytes() == src.read_bytes()
        assert dst.stat().st_ino == src.stat().st_ino

    def test_replaces_an_existing_destination(self, tmp_path):
        src = write(tmp_path / "src.mp4", 128)
        dst = write(tmp_path / "cache" / "team-1.mp4", 64)
        link_or_copy(src, dst)
        assert dst.stat().st_size == 128

    def test_deleting_the_link_keeps_the_original(self, tmp_path):
        src = write(tmp_path / "src.mp4", 128)
        dst = tmp_path / "cache" / "team-1.mp4"
        link_or_copy(src, dst)
        dst.unlink()
        assert src.exists()

    def test_falls_back_to_copy_when_link_fails(self, tmp_path):
        src = write(tmp_path / "src.mp4", 128)
        dst = tmp_path / "cache" / "team-1.mp4"
        # Monkeypatch os.link to raise OSError (e.g., cross-volume link)
        with mock.patch("os.link", side_effect=OSError("Cross-device link")):
            result = link_or_copy(src, dst)
        assert result == "copy"
        assert dst.read_bytes() == src.read_bytes()
        # Verify it's a copy, not a link (different inode)
        assert dst.stat().st_ino != src.stat().st_ino


TEAM = ChatEntry(alias="team", id=-1001111111111, title="Team chat")


class FakeEntity:
    def __init__(self, id, title):
        self.id = id
        self.title = title


class FakeFile:
    def __init__(self, size, name=None, ext=".mp4", duration=None):
        self.size = size
        self.name = name
        self.ext = ext
        self.mime_type = "video/mp4"
        self.duration = duration


class FakeMessage:
    def __init__(self, id=4242, file=None, text="here is the recording"):
        self.id = id
        self.text = text
        self.message = text
        self.sender = None
        self.sender_id = 42
        self.date = dt.datetime(2026, 8, 3, 9, 0, tzinfo=dt.timezone.utc)
        self.media = object() if file else None
        self.file = file


class FakeClient:
    """Telethon stand-in that records where it was asked to go."""

    def __init__(self, message=None, payload=b"video-bytes"):
        self.entities = {TEAM.id: FakeEntity(TEAM.id, TEAM.title)}
        self.message = message
        self.payload = payload
        self.calls = []
        self.last_progress_callback = "not-called"

    async def get_entity(self, chat_id):
        self.calls.append(("get_entity", chat_id))
        return self.entities[chat_id]

    async def get_messages(self, entity, **kwargs):
        self.calls.append(("get_messages", entity.id, kwargs))
        return self.message

    async def download_media(self, message, file, progress_callback=None):
        self.calls.append(("download_media", file))
        self.last_progress_callback = progress_callback
        Path(file).write_bytes(self.payload)
        return file


def allowlist():
    return AllowList([TEAM])


def run(client, chat, out_dir, lookup_dirs=(), message_id=4242, progress_callback=None, max_size=None):
    return asyncio.run(
        download_message_media(
            client,
            allowlist(),
            chat,
            message_id,
            out_dir,
            lookup_dirs,
            progress_callback=progress_callback,
            max_size=max_size,
        )
    )


class TestDownloadMessageMedia:
    def test_downloads_over_the_network_when_nothing_local(self, tmp_path):
        client = FakeClient(FakeMessage(file=FakeFile(size=11, duration=462)))
        result = run(client, "team", tmp_path)
        assert result["source"] == "network"
        assert Path(result["path"]) == tmp_path / "team-4242.mp4"
        assert Path(result["path"]).read_bytes() == b"video-bytes"
        assert result["duration"] == 462
        assert result["caption"] == "here is the recording"
        assert result["chat"]["alias"] == "team"

    def test_uses_the_local_copy_instead_of_the_network(self, tmp_path):
        downloads = tmp_path / "Telegram Lite"
        downloads.mkdir()
        (downloads / "Screen Recording.mov").write_bytes(b"x" * 11)
        client = FakeClient(FakeMessage(file=FakeFile(size=11, name="Screen Recording.mov", ext=".mov")))
        result = run(client, "team", tmp_path / "cache", [downloads])
        assert result["source"] == "local"
        assert result["origin"] == str(downloads / "Screen Recording.mov")
        assert ("download_media", str(tmp_path / "cache" / "team-4242.mov")) not in client.calls

    def test_local_lookup_uses_the_raw_name_but_returns_the_sanitised_one(self, tmp_path):
        # The real file on disk carries the zero-width space in its name, just
        # like the uploader named it. Matching it needs the raw name; what the
        # caller is told about it must be scrubbed, exactly like message text.
        downloads = tmp_path / "Telegram Lite"
        downloads.mkdir()
        raw_name = "Screen​ Recording.mov"
        (downloads / raw_name).write_bytes(b"x" * 11)
        client = FakeClient(FakeMessage(file=FakeFile(size=11, name=raw_name, ext=".mov")))
        result = run(client, "team", tmp_path / "cache", [downloads])
        assert result["source"] == "local"
        assert result["file_name"] == "Screen Recording.mov"

    def test_existing_file_of_the_right_size_is_reused(self, tmp_path):
        (tmp_path / "team-4242.mp4").write_bytes(b"x" * 11)
        client = FakeClient(FakeMessage(file=FakeFile(size=11)))
        result = run(client, "team", tmp_path)
        assert result["source"] == "cache"
        assert not any(call[0] == "download_media" for call in client.calls)

    def test_existing_file_of_a_different_size_is_refetched(self, tmp_path):
        (tmp_path / "team-4242.mp4").write_bytes(b"truncated")
        client = FakeClient(FakeMessage(file=FakeFile(size=11)))
        assert run(client, "team", tmp_path)["source"] == "network"

    def test_chat_outside_the_allowlist_never_reaches_the_network(self, tmp_path):
        client = FakeClient(FakeMessage(file=FakeFile(size=11)))
        with pytest.raises(ChatNotAllowed):
            run(client, -1009999999999, tmp_path)
        assert client.calls == []

    def test_message_without_media_is_a_clear_error(self, tmp_path):
        client = FakeClient(FakeMessage(file=None))
        with pytest.raises(ValueError, match="no downloadable media"):
            run(client, "team", tmp_path)

    def test_missing_message_is_a_clear_error(self, tmp_path):
        client = FakeClient(None)
        with pytest.raises(ValueError, match="not found"):
            run(client, "team", tmp_path)

    def test_forwards_the_progress_callback_on_a_network_download(self, tmp_path):
        def progress(current, total):
            pass

        client = FakeClient(FakeMessage(file=FakeFile(size=11, duration=462)))
        result = run(client, "team", tmp_path, progress_callback=progress)
        assert result["source"] == "network"
        assert client.last_progress_callback is progress

    def test_progress_callback_is_unused_when_a_local_copy_is_found(self, tmp_path):
        downloads = tmp_path / "Telegram Lite"
        downloads.mkdir()
        (downloads / "Screen Recording.mov").write_bytes(b"x" * 11)
        client = FakeClient(FakeMessage(file=FakeFile(size=11, name="Screen Recording.mov", ext=".mov")))

        result = run(
            client, "team", tmp_path / "cache", [downloads], progress_callback=lambda c, t: None
        )
        assert result["source"] == "local"
        assert client.last_progress_callback == "not-called"

    def test_result_reports_the_mime_type(self, tmp_path):
        client = FakeClient(FakeMessage(file=FakeFile(size=11)))
        assert run(client, "team", tmp_path)["mime"] == "video/mp4"


class TestMaxSize:
    def test_refuses_a_network_download_above_the_limit(self, tmp_path):
        client = FakeClient(FakeMessage(file=FakeFile(size=100)))
        with pytest.raises(MediaTooLarge, match="max_size"):
            run(client, "team", tmp_path, max_size=99)
        assert not any(call[0] == "download_media" for call in client.calls)

    def test_downloads_at_the_limit(self, tmp_path):
        client = FakeClient(FakeMessage(file=FakeFile(size=100)), payload=b"x" * 100)
        assert run(client, "team", tmp_path, max_size=100)["source"] == "network"

    def test_no_limit_by_default(self, tmp_path):
        client = FakeClient(FakeMessage(file=FakeFile(size=11)))
        assert run(client, "team", tmp_path)["source"] == "network"

    def test_cached_file_ignores_the_limit(self, tmp_path):
        (tmp_path / "team-4242.mp4").write_bytes(b"x" * 11)
        client = FakeClient(FakeMessage(file=FakeFile(size=11)))
        # Already on disk: the limit guards the network, not the cache.
        assert run(client, "team", tmp_path, max_size=1)["source"] == "cache"

    def test_local_copy_ignores_the_limit(self, tmp_path):
        downloads = tmp_path / "Telegram Lite"
        downloads.mkdir()
        (downloads / "Screen Recording.mov").write_bytes(b"x" * 11)
        client = FakeClient(FakeMessage(file=FakeFile(size=11, name="Screen Recording.mov", ext=".mov")))
        # A hard link from the desktop client costs nothing, whatever the size.
        result = run(client, "team", tmp_path / "cache", [downloads], max_size=1)
        assert result["source"] == "local"

    def test_the_refusal_names_the_file_and_both_sizes(self, tmp_path):
        client = FakeClient(FakeMessage(file=FakeFile(size=100, name="huge.mp4")))
        with pytest.raises(MediaTooLarge) as excinfo:
            run(client, "team", tmp_path, max_size=99)
        message = str(excinfo.value)
        assert "huge.mp4" in message and "100" in message and "99" in message

    def test_default_limit_is_defensible(self):
        from telegram_mcp.media import DEFAULT_MAX_SIZE

        # Comfortably above a screenshot or a document, well below the
        # multi-hundred-MB videos that belong to the CLI.
        assert 10_000_000 <= DEFAULT_MAX_SIZE <= 200_000_000


class TestInlineVerdict:
    def test_small_image_rides_along(self):
        assert inline_verdict("image/png", 148_213) == (True, None)

    def test_large_image_stays_on_disk(self):
        inlined, reason = inline_verdict("image/png", 9_000_000)
        assert inlined is False
        assert "inline limit" in reason and "path" in reason

    def test_document_stays_on_disk(self):
        inlined, reason = inline_verdict("application/pdf", 1024)
        assert inlined is False
        assert "not an image" in reason

    def test_unknown_mime_stays_on_disk(self):
        inlined, reason = inline_verdict(None, 1024)
        assert inlined is False
        assert "not an image" in reason

    def test_unknown_size_stays_on_disk(self):
        inlined, reason = inline_verdict("image/png", None)
        assert inlined is False
        assert "size" in reason

    def test_video_is_not_inlined_even_when_small(self):
        assert inline_verdict("video/mp4", 1024)[0] is False

    def test_image_type_the_api_cannot_read_stays_on_disk(self):
        # image/* but not one of the four types the Anthropic API accepts —
        # inlining it would only fail later, after the download.
        inlined, reason = inline_verdict("image/heic", 1024)
        assert inlined is False
        assert "image/heic" in reason and "path" in reason

    def test_jpeg_png_gif_webp_are_all_inlinable(self):
        for mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            assert inline_verdict(mime, 1024) == (True, None)

    def test_inline_limit_leaves_headroom_under_the_api_cap(self):
        from telegram_mcp.media import INLINE_MAX_BYTES

        # The API caps a single image at five megabytes. The limit is on the
        # raw byte size, but the image actually travels as base64, which
        # inflates it by a third — so it is the *encoded* size that has to
        # stay under the cap.
        assert INLINE_MAX_BYTES * 4 / 3 <= 5_000_000
