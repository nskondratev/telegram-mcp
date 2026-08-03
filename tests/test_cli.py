"""Tests for argument handling in the CLI: no network, no account."""
import asyncio
import json
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


class TestStderrProgress:
    """The download progress reporter must be throttled, not one line per chunk."""

    def test_throttles_to_one_line_per_whole_percent(self, capsys):
        report = cli._stderr_progress("test")
        total = 1000
        for current in range(0, total + 1):  # a chunk callback every single byte
            report(current, total)
        err = capsys.readouterr().err
        # 101 possible values (0..100 inclusive), not 1001 lines.
        assert err.count("%") == 101
        assert "100%" in err

    def test_repeated_calls_at_the_same_percent_print_nothing(self, capsys):
        report = cli._stderr_progress("test")
        report(0, 1000)
        capsys.readouterr()
        report(1, 1000)  # still 0%
        assert capsys.readouterr().err == ""

    def test_the_final_call_always_prints(self, capsys):
        report = cli._stderr_progress("test")
        report(1000, 1000)
        assert "100%" in capsys.readouterr().err


class TestCmdDownloadReportsTheAllowlist:
    """A security-critical, heuristically-chosen file should not be silent."""

    def _run(self, tmp_path, monkeypatch, as_json):
        allowlist_path = tmp_path / "allowed_chats.json"
        allowlist_path.write_text(json.dumps({"chats": [{"id": -1001111111111, "alias": "team"}]}))

        class FakeConnection:
            async def disconnect(self):
                pass

        async def fake_connected(_session):
            return FakeConnection()

        async def fake_download(*_args, **_kwargs):
            return {"path": "/out/team-4242.mp4", "source": "network", "origin": None}

        monkeypatch.setattr(cli, "_connected", fake_connected)
        monkeypatch.setattr(cli, "session_string", lambda: "dummy-session")
        monkeypatch.setattr(cli.media, "download_message_media", fake_download)

        asyncio.run(cli.cmd_download("team", 4242, ".", None, as_json, allowlist_path))
        return allowlist_path

    def test_json_payload_includes_the_resolved_allowlist_path(self, tmp_path, monkeypatch, capsys):
        allowlist_path = self._run(tmp_path, monkeypatch, as_json=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["allowlist_path"] == str(allowlist_path)

    def test_human_readable_output_mentions_the_allowlist_path(self, tmp_path, monkeypatch, capsys):
        allowlist_path = self._run(tmp_path, monkeypatch, as_json=False)
        assert f"allowlist: {allowlist_path}" in capsys.readouterr().out
