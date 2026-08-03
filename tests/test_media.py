"""Tests for locating and linking a message's media file."""
import os

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
