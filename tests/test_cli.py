"""Tests for argument handling in the CLI: no network, no account."""
from pathlib import Path

from telegram_mcp import cli


class TestResolveAllowlistPath:
    def test_explicit_argument_wins(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "read_env", lambda name: "/from/config.json")
        assert cli.resolve_allowlist_path(str(tmp_path / "a.json")) == tmp_path / "a.json"

    def test_falls_back_to_the_claude_config(self, monkeypatch):
        monkeypatch.setattr(cli, "read_env", lambda name: "~/knowledge/allowed_chats.json")
        assert cli.resolve_allowlist_path(None) == Path.home() / "knowledge" / "allowed_chats.json"

    def test_falls_back_to_the_default_location(self, monkeypatch):
        monkeypatch.setattr(cli, "read_env", lambda name: None)
        assert cli.resolve_allowlist_path(None) == cli.default_allowlist_path()


class TestDownloadArguments:
    def test_link_form(self):
        args = cli.build_parser().parse_args(["download", "https://t.me/c/1234567890/8/4242"])
        assert args.command == "download"
        assert args.target == "https://t.me/c/1234567890/8/4242"
        assert args.message is None
        assert args.out == "."

    def test_alias_form_with_repeatable_lookup_dirs(self):
        args = cli.build_parser().parse_args(
            [
                "download",
                "team",
                "--message",
                "4242",
                "--out",
                "/tmp/cache",
                "--lookup-dir",
                "~/Downloads/Telegram Lite",
                "--lookup-dir",
                "~/Downloads/Telegram Desktop",
                "--json",
            ]
        )
        assert args.message == 4242
        assert args.out == "/tmp/cache"
        assert args.lookup_dirs == ["~/Downloads/Telegram Lite", "~/Downloads/Telegram Desktop"]
        assert args.json is True


class TestResolveTarget:
    def test_link_is_parsed(self):
        assert cli.resolve_target("https://t.me/c/1234567890/8/4242", None) == (-1001234567890, 4242)

    def test_alias_with_message_id(self):
        assert cli.resolve_target("team", 4242) == ("team", 4242)

    def test_alias_without_message_id_is_an_error(self):
        try:
            cli.resolve_target("team", None)
        except SystemExit as exc:
            assert "--message" in str(exc)
        else:
            raise AssertionError("expected SystemExit")
