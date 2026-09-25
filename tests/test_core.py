"""Tests for the allowlist and text sanitising — the heart of the server."""
import json
from pathlib import Path

import pytest

from telegram_mcp.core import (
    ChatNotAllowed,
    MediaTooLarge,
    default_allowlist_path,
    default_local_allowlist_path,
    default_media_dir,
    load_allowlist,
    load_allowlists,
    normalize_chat_id,
    sanitize_text,
)


def write_config(tmp_path, chats):
    path = tmp_path / "allowed_chats.json"
    path.write_text(json.dumps({"chats": chats}, ensure_ascii=False), encoding="utf-8")
    return path


class TestNormalizeChatId:
    def test_keeps_supergroup_id_as_is(self):
        assert normalize_chat_id(-1001234567890) == -1001234567890

    def test_parses_string_id(self):
        assert normalize_chat_id("-1001234567890") == -1001234567890

    def test_rejects_non_numeric_string(self):
        with pytest.raises(ValueError):
            normalize_chat_id("not a number")


class TestAllowList:
    def test_resolves_chat_by_id(self, tmp_path):
        path = write_config(tmp_path, [{"alias": "team", "id": -1001234567890, "title": "Team"}])
        allowlist = load_allowlist(path)
        assert allowlist.resolve(-1001234567890) == -1001234567890

    def test_resolves_chat_by_alias(self, tmp_path):
        path = write_config(tmp_path, [{"alias": "team", "id": -1001234567890, "title": "Team"}])
        allowlist = load_allowlist(path)
        assert allowlist.resolve("team") == -1001234567890

    def test_resolves_chat_by_title(self, tmp_path):
        path = write_config(tmp_path, [{"alias": "team", "id": -1001234567890, "title": "Team"}])
        allowlist = load_allowlist(path)
        assert allowlist.resolve("Team") == -1001234567890

    def test_rejects_chat_outside_allowlist(self, tmp_path):
        path = write_config(tmp_path, [{"alias": "team", "id": -1001234567890, "title": "Team"}])
        allowlist = load_allowlist(path)
        with pytest.raises(ChatNotAllowed):
            allowlist.resolve(-1009999999999)

    def test_rejection_message_lists_allowed_aliases(self, tmp_path):
        path = write_config(tmp_path, [{"alias": "team", "id": -1001234567890, "title": "Team"}])
        allowlist = load_allowlist(path)
        with pytest.raises(ChatNotAllowed) as excinfo:
            allowlist.resolve(-1009999999999)
        assert "team" in str(excinfo.value)

    def test_rejects_unknown_alias(self, tmp_path):
        path = write_config(tmp_path, [{"alias": "team", "id": -1001234567890, "title": "Team"}])
        allowlist = load_allowlist(path)
        with pytest.raises(ChatNotAllowed):
            allowlist.resolve("family-chat")

    def test_empty_allowlist_denies_everything(self, tmp_path):
        path = write_config(tmp_path, [])
        allowlist = load_allowlist(path)
        with pytest.raises(ChatNotAllowed):
            allowlist.resolve(-1001234567890)

    def test_entries_expose_alias_id_and_title(self, tmp_path):
        path = write_config(tmp_path, [{"alias": "team", "id": -1001234567890, "title": "Team"}])
        allowlist = load_allowlist(path)
        assert [(e.alias, e.id, e.title) for e in allowlist.entries] == [("team", -1001234567890, "Team")]

    def test_config_with_duplicate_alias_is_rejected(self, tmp_path):
        path = write_config(
            tmp_path,
            [
                {"alias": "team", "id": -1001234567890, "title": "Team"},
                {"alias": "team", "id": -1000000000001, "title": "Another"},
            ],
        )
        with pytest.raises(ValueError):
            load_allowlist(path)

    def test_config_with_duplicate_id_is_rejected(self, tmp_path):
        path = write_config(
            tmp_path,
            [
                {"alias": "team", "id": -1001234567890, "title": "Team"},
                {"alias": "team-again", "id": -1001234567890, "title": "Team"},
            ],
        )
        with pytest.raises(ValueError):
            load_allowlist(path)

    def test_missing_config_file_is_a_clear_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_allowlist(tmp_path / "no-such-file.json")

    def test_entry_without_id_is_rejected(self, tmp_path):
        path = write_config(tmp_path, [{"alias": "team", "title": "Team"}])
        with pytest.raises(ValueError):
            load_allowlist(path)

    def test_id_as_string_in_config_is_accepted(self, tmp_path):
        path = write_config(tmp_path, [{"alias": "team", "id": "-1001234567890", "title": "Team"}])
        allowlist = load_allowlist(path)
        assert allowlist.resolve("team") == -1001234567890


class TestDefaultAllowlistPath:
    def test_environment_variable_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TG_ALLOWED_CHATS_FILE", str(tmp_path / "custom.json"))
        assert default_allowlist_path() == tmp_path / "custom.json"

    def test_falls_back_to_config_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TG_ALLOWED_CHATS_FILE", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert default_allowlist_path() == tmp_path / "telegram-mcp" / "allowed_chats.json"

    def test_never_defaults_next_to_the_code(self, tmp_path, monkeypatch):
        """Installed from a git URL, the package directory is a cache — configs must not live there."""
        monkeypatch.delenv("TG_ALLOWED_CHATS_FILE", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert default_allowlist_path() == tmp_path / ".config" / "telegram-mcp" / "allowed_chats.json"


class TestSanitizeText:
    def test_keeps_normal_text_intact(self):
        assert sanitize_text("Deploy failed, check the logs") == "Deploy failed, check the logs"

    def test_keeps_non_ascii_text_intact(self):
        text = "Déploiement échoué · 部署失败 · деплой упал"
        assert sanitize_text(text) == text

    def test_strips_zero_width_characters(self):
        assert sanitize_text("he\u200bllo") == "hello"

    def test_strips_bidi_overrides(self):
        assert sanitize_text("he\u202ello") == "hello"

    def test_strips_control_characters_but_keeps_newlines(self):
        assert sanitize_text("line\x00one\nline two") == "lineone\nline two"

    def test_truncates_long_text_with_marker(self):
        result = sanitize_text("a" * 100, limit=20)
        assert len(result) <= 40
        assert result.startswith("a" * 20)
        assert "…" in result

    def test_handles_none(self):
        assert sanitize_text(None) == ""


class TestDefaultMediaDir:
    def test_environment_variable_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TG_MEDIA_DIR", str(tmp_path / "elsewhere"))
        assert default_media_dir() == tmp_path / "elsewhere"

    def test_expands_a_tilde_in_the_environment_variable(self, monkeypatch):
        monkeypatch.setenv("TG_MEDIA_DIR", "~/tg-media")
        assert default_media_dir() == Path.home() / "tg-media"

    def test_honours_xdg_cache_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TG_MEDIA_DIR", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert default_media_dir() == tmp_path / "telegram-mcp" / "media"

    def test_falls_back_to_the_home_cache(self, monkeypatch):
        monkeypatch.delenv("TG_MEDIA_DIR", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert default_media_dir() == Path.home() / ".cache" / "telegram-mcp" / "media"


class TestMediaTooLarge:
    def test_is_not_an_os_error(self):
        # An OSError refusal is mistaken for a broken pipe by the stdio
        # transport and the client gets no answer at all — the same reason
        # ChatNotAllowed derives from Exception.
        assert not issubclass(MediaTooLarge, OSError)


MAIN_CHATS = [{"alias": "team", "id": -1001111111111, "title": "Team"}]


def write_named(tmp_path, name, chats):
    path = tmp_path / name
    path.write_text(json.dumps({"chats": chats}, ensure_ascii=False), encoding="utf-8")
    return path


class TestDefaultLocalAllowlistPath:
    def test_environment_variable_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TG_ALLOWED_CHATS_LOCAL_FILE", str(tmp_path / "mine.json"))
        assert default_local_allowlist_path() == tmp_path / "mine.json"

    def test_sits_next_to_the_default_allowlist(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TG_ALLOWED_CHATS_LOCAL_FILE", raising=False)
        monkeypatch.delenv("TG_ALLOWED_CHATS_FILE", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert default_local_allowlist_path() == tmp_path / "telegram-mcp" / "allowed_chats.local.json"
        assert default_local_allowlist_path().parent == default_allowlist_path().parent

    def test_does_not_follow_the_main_allowlist_variable(self, tmp_path, monkeypatch):
        """The main list may live inside a plugin; the personal one stays in your config."""
        monkeypatch.delenv("TG_ALLOWED_CHATS_LOCAL_FILE", raising=False)
        monkeypatch.setenv("TG_ALLOWED_CHATS_FILE", str(tmp_path / "plugin" / "allowed_chats.json"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        expected = tmp_path / "config" / "telegram-mcp" / "allowed_chats.local.json"
        assert default_local_allowlist_path() == expected


class TestLoadAllowlists:
    def test_without_a_local_file_the_main_list_is_used_as_is(self, tmp_path):
        main = write_named(tmp_path, "allowed_chats.json", MAIN_CHATS)
        allowlist, skipped = load_allowlists(main, tmp_path / "allowed_chats.local.json")
        assert [entry.alias for entry in allowlist.entries] == ["team"]
        assert skipped == []

    def test_no_local_path_means_the_main_list_only(self, tmp_path):
        main = write_named(tmp_path, "allowed_chats.json", MAIN_CHATS)
        allowlist, skipped = load_allowlists(main)
        assert [entry.alias for entry in allowlist.entries] == ["team"]
        assert skipped == []

    def test_local_file_adds_chats(self, tmp_path):
        main = write_named(tmp_path, "allowed_chats.json", MAIN_CHATS)
        local = write_named(
            tmp_path, "allowed_chats.local.json", [{"alias": "side", "id": -1003333333333, "title": "Side"}]
        )
        allowlist, skipped = load_allowlists(main, local)
        assert allowlist.resolve("team") == -1001111111111
        assert allowlist.resolve("side") == -1003333333333
        assert skipped == []

    def test_chats_in_neither_file_are_still_denied(self, tmp_path):
        main = write_named(tmp_path, "allowed_chats.json", MAIN_CHATS)
        local = write_named(
            tmp_path, "allowed_chats.local.json", [{"alias": "side", "id": -1003333333333, "title": "Side"}]
        )
        allowlist, _ = load_allowlists(main, local)
        with pytest.raises(ChatNotAllowed):
            allowlist.resolve(-1009999999999)

    def test_main_entry_wins_on_the_same_id(self, tmp_path):
        main = write_named(tmp_path, "allowed_chats.json", MAIN_CHATS)
        local = write_named(
            tmp_path, "allowed_chats.local.json", [{"alias": "mine", "id": -1001111111111, "title": "Mine"}]
        )
        allowlist, skipped = load_allowlists(main, local)
        assert [entry.alias for entry in allowlist.entries] == ["team"]
        with pytest.raises(ChatNotAllowed):
            allowlist.resolve("mine")
        assert len(skipped) == 1 and "-1001111111111" in skipped[0]

    def test_main_entry_wins_on_the_same_alias_in_any_case(self, tmp_path):
        main = write_named(tmp_path, "allowed_chats.json", MAIN_CHATS)
        local = write_named(
            tmp_path, "allowed_chats.local.json", [{"alias": "TEAM", "id": -1004444444444, "title": "Other"}]
        )
        allowlist, skipped = load_allowlists(main, local)
        assert allowlist.resolve("team") == -1001111111111
        assert not allowlist.contains(-1004444444444)
        assert len(skipped) == 1 and "'TEAM'" in skipped[0]

    def test_main_entry_keeps_its_title(self, tmp_path):
        main = write_named(tmp_path, "allowed_chats.json", MAIN_CHATS)
        local = write_named(
            tmp_path, "allowed_chats.local.json", [{"alias": "side", "id": -1003333333333, "title": "Team"}]
        )
        allowlist, skipped = load_allowlists(main, local)
        assert allowlist.resolve("Team") == -1001111111111
        assert allowlist.resolve("side") == -1003333333333
        assert skipped == []

    def test_duplicates_inside_the_local_file_are_a_startup_error(self, tmp_path):
        main = write_named(tmp_path, "allowed_chats.json", MAIN_CHATS)
        local = write_named(
            tmp_path,
            "allowed_chats.local.json",
            [
                {"alias": "side", "id": -1003333333333},
                {"alias": "side", "id": -1004444444444},
            ],
        )
        with pytest.raises(ValueError):
            load_allowlists(main, local)

    def test_local_path_pointing_at_the_main_file_is_read_once(self, tmp_path):
        main = write_named(tmp_path, "allowed_chats.json", MAIN_CHATS)
        allowlist, skipped = load_allowlists(main, main)
        assert [entry.alias for entry in allowlist.entries] == ["team"]
        assert skipped == []

    def test_missing_main_file_is_still_an_error(self, tmp_path):
        local = write_named(
            tmp_path, "allowed_chats.local.json", [{"alias": "side", "id": -1003333333333, "title": "Side"}]
        )
        with pytest.raises(FileNotFoundError):
            load_allowlists(tmp_path / "no-such-file.json", local)
