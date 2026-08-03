"""Tests for locating and linking a message's media file."""
import os
from unittest import mock

from telegram_mcp.media import canonical_name, find_local_copy, link_or_copy


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
