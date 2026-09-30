"""Tests for argument handling in the CLI: no network, no account."""
import asyncio
import json
from pathlib import Path

import pytest

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


class TestResolveLocalAllowlistPath:
    def test_falls_back_to_the_claude_config(self, monkeypatch):
        monkeypatch.setattr(cli, "read_env", lambda name: "~/knowledge/allowed_chats.local.json")
        assert cli.resolve_local_allowlist_path() == Path.home() / "knowledge" / "allowed_chats.local.json"

    def test_falls_back_to_the_default_location(self, monkeypatch):
        monkeypatch.setattr(cli, "read_env", lambda name: None)
        assert cli.resolve_local_allowlist_path() == cli.default_local_allowlist_path()

    def test_reads_its_own_variable(self, monkeypatch):
        asked = []
        monkeypatch.setattr(cli, "read_env", lambda name: asked.append(name))
        cli.resolve_local_allowlist_path()
        assert asked == ["TG_ALLOWED_CHATS_LOCAL_FILE"]


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

        async def fake_download(*_args, **kwargs):
            # The CLI must keep downloading with no cap: max_size is the model's
            # safety valve, not something the human at the terminal should hit.
            assert kwargs.get("max_size") is None
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


class TestPersonalAdditionsInCommands:
    """The CLI sees the same merged list as the server."""

    def _files(self, tmp_path):
        main = tmp_path / "allowed_chats.json"
        main.write_text(json.dumps({"chats": [{"id": -1001111111111, "alias": "team"}]}))
        local = tmp_path / "allowed_chats.local.json"
        local.write_text(
            json.dumps(
                {
                    "chats": [
                        {"id": -1003333333333, "alias": "side"},
                        {"id": -1001111111111, "alias": "mine"},
                    ]
                }
            )
        )
        return main, local

    def _fake_telegram(self, monkeypatch):
        class FakeConnection:
            async def disconnect(self):
                pass

        async def fake_connected(_session):
            return FakeConnection()

        monkeypatch.setattr(cli, "_connected", fake_connected)
        monkeypatch.setattr(cli, "session_string", lambda: "dummy-session")

    def test_download_accepts_a_chat_from_the_personal_additions(self, tmp_path, monkeypatch):
        main, local = self._files(tmp_path)
        self._fake_telegram(monkeypatch)
        seen = {}

        async def fake_download(_client, allowlist, chat_ref, *_args, **_kwargs):
            seen["id"] = allowlist.resolve(chat_ref)
            return {"path": "/out/side-7.jpg", "source": "network", "origin": None}

        monkeypatch.setattr(cli.media, "download_message_media", fake_download)
        asyncio.run(cli.cmd_download("side", 7, ".", None, True, main, local))
        assert seen["id"] == -1003333333333

    def test_check_reports_additions_and_skipped_entries(self, tmp_path, monkeypatch, capsys):
        main, local = self._files(tmp_path)
        self._fake_telegram(monkeypatch)

        async def fake_chat_info(_client, allowlist, chat):
            return {"title": allowlist.entry(chat).alias}

        async def fake_messages(_client, allowlist, chat, limit=1):
            allowlist.entry(chat)  # a chat outside the list raises, as the real handler does
            return {"messages": []}

        monkeypatch.setattr(cli.handlers, "get_chat_info", fake_chat_info)
        monkeypatch.setattr(cli.handlers, "get_messages", fake_messages)
        asyncio.run(cli.cmd_check(main, local))
        out = capsys.readouterr().out
        assert f"Personal additions: {local} — chats: 1" in out
        assert "SKIP id -1001111111111" in out
        assert "OK   side" in out
        assert "a chat outside the allowlist was rejected" in out


# Claude Code's own files as a source of credentials

#: What the lookup starts from. A developer running the tests inside Claude Code has the
#: very credentials in the environment that these tests are about, so they are cleared.
LOOKUP_VARS = (
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_SESSION_STRING",
    "TG_ALLOWED_CHATS_FILE",
    "TG_ALLOWED_CHATS_LOCAL_FILE",
    "CLAUDE_CONFIG_DIR",
    "XDG_CONFIG_HOME",
)


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    """A fake, empty home directory: nothing of whoever runs the tests can get in.

    HOME points into tmp_path, so ``~/.claude/settings.json`` and ``~/.claude.json`` are
    looked up there and never in the real ones.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in LOOKUP_VARS:
        monkeypatch.delenv(name, raising=False)
    return home


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_settings(home, env):
    """``~/.claude/settings.json`` with an env block."""
    return write_json(home / ".claude" / "settings.json", {"env": env})


def write_claude_json(home, env, server="telegram"):
    """``~/.claude.json`` with one MCP server that carries the given env."""
    return write_json(home / ".claude.json", {"mcpServers": {server: {"command": "uvx", "env": env}}})


class TestReadEnv:
    NAME = "TELEGRAM_API_HASH"

    def test_environment_wins_over_both_files(self, claude_home, monkeypatch):
        write_settings(claude_home, {self.NAME: "from-settings"})
        write_claude_json(claude_home, {self.NAME: "from-claude-json"})
        monkeypatch.setenv(self.NAME, "from-environment")
        assert cli.read_env(self.NAME) == "from-environment"

    def test_settings_json_wins_over_claude_json(self, claude_home):
        write_settings(claude_home, {self.NAME: "from-settings"})
        write_claude_json(claude_home, {self.NAME: "from-claude-json"})
        assert cli.read_env(self.NAME) == "from-settings"

    def test_settings_json_alone_is_enough(self, claude_home):
        write_settings(claude_home, {self.NAME: "from-settings"})
        assert cli.read_env(self.NAME) == "from-settings"

    def test_claude_json_is_used_when_settings_json_has_no_such_key(self, claude_home):
        write_settings(claude_home, {"SOMETHING_ELSE": "x"})
        write_claude_json(claude_home, {self.NAME: "from-claude-json"})
        assert cli.read_env(self.NAME) == "from-claude-json"

    def test_claude_json_is_used_without_a_settings_file(self, claude_home):
        write_claude_json(claude_home, {self.NAME: "from-claude-json"})
        assert cli.read_env(self.NAME) == "from-claude-json"

    def test_nothing_anywhere_is_none(self, claude_home):
        assert cli.read_env(self.NAME) is None

    def test_an_empty_environment_variable_does_not_count(self, claude_home, monkeypatch):
        monkeypatch.setenv(self.NAME, "")
        write_settings(claude_home, {self.NAME: "from-settings"})
        assert cli.read_env(self.NAME) == "from-settings"

    @pytest.mark.parametrize(
        "content",
        [
            b"{not json",
            b"",
            b"\xff\xfe\x00\x01",
            b"[]",
            b"{}",
            b'{"env": null}',
            b'{"env": ["TELEGRAM_API_HASH"]}',
            b'{"env": {"TELEGRAM_API_HASH": ""}}',
            b'{"env": {"TELEGRAM_API_HASH": 12345}}',
        ],
        ids=[
            "malformed-json",
            "empty-file",
            "not-utf8",
            "not-an-object",
            "no-env",
            "env-is-null",
            "env-is-a-list",
            "empty-value",
            "value-is-not-a-string",
        ],
    )
    def test_an_unusable_settings_json_is_skipped(self, claude_home, content):
        settings = claude_home / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_bytes(content)
        write_claude_json(claude_home, {self.NAME: "from-claude-json"})
        assert cli.read_env(self.NAME) == "from-claude-json"

    def test_an_unreadable_settings_json_is_skipped(self, claude_home):
        (claude_home / ".claude" / "settings.json").mkdir(parents=True)  # reading a directory fails
        write_claude_json(claude_home, {self.NAME: "from-claude-json"})
        assert cli.read_env(self.NAME) == "from-claude-json"

    def test_a_broken_settings_json_alone_yields_nothing(self, claude_home):
        settings = claude_home / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text("{not json", encoding="utf-8")
        assert cli.read_env(self.NAME) is None

    def test_a_broken_claude_json_is_skipped_too(self, claude_home):
        (claude_home / ".claude.json").write_text("{not json", encoding="utf-8")
        assert cli.read_env(self.NAME) is None


class TestClaudeConfigDir:
    """CLAUDE_CONFIG_DIR moves Claude Code's whole config directory, settings.json with it."""

    NAME = "TELEGRAM_API_HASH"

    def test_settings_are_read_from_the_moved_directory(self, claude_home, tmp_path, monkeypatch):
        moved = tmp_path / "other-account"
        write_json(moved / "settings.json", {"env": {self.NAME: "from-moved-settings"}})
        write_settings(claude_home, {self.NAME: "from-default-settings"})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(moved))
        assert cli.read_env(self.NAME) == "from-moved-settings"

    def test_the_default_location_is_not_consulted_once_moved(self, claude_home, tmp_path, monkeypatch):
        write_settings(claude_home, {self.NAME: "from-default-settings"})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty-dir"))
        assert cli.read_env(self.NAME) is None

    def test_a_tilde_is_expanded(self, claude_home, monkeypatch):
        moved = claude_home / ".claude-work"
        write_json(moved / "settings.json", {"env": {self.NAME: "from-moved-settings"}})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/.claude-work")
        assert cli.read_env(self.NAME) == "from-moved-settings"

    def test_environment_still_wins(self, claude_home, tmp_path, monkeypatch):
        moved = tmp_path / "other-account"
        write_json(moved / "settings.json", {"env": {self.NAME: "from-moved-settings"}})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(moved))
        monkeypatch.setenv(self.NAME, "from-environment")
        assert cli.read_env(self.NAME) == "from-environment"


class TestCredentialsFromSettings:
    """What the terminal commands need from the lookup: no `export` for plugin users."""

    def test_api_credentials_need_no_export(self, claude_home):
        write_settings(claude_home, {"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "abcdef0123456789"})
        assert cli.credentials() == (12345, "abcdef0123456789")

    def test_session_string_needs_no_export(self, claude_home):
        write_settings(claude_home, {"TELEGRAM_SESSION_STRING": "fake-session"})
        assert cli.session_string() == "fake-session"

    def test_reading_them_prints_nothing(self, claude_home, capsys):
        write_settings(
            claude_home,
            {
                "TELEGRAM_API_ID": "12345",
                "TELEGRAM_API_HASH": "abcdef0123456789",
                "TELEGRAM_SESSION_STRING": "fake-session",
            },
        )
        cli.credentials()
        cli.session_string()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_a_misplaced_api_hash_is_not_echoed(self, claude_home):
        """The api_hash pasted into the api_id slot must not come back in the error."""
        write_settings(claude_home, {"TELEGRAM_API_ID": "abcdef0123456789", "TELEGRAM_API_HASH": "12345"})
        with pytest.raises(SystemExit) as exc:
            cli.credentials()
        assert "TELEGRAM_API_ID must be a number" in str(exc.value)
        assert "abcdef0123456789" not in str(exc.value)


class TestAllowlistPathsFromSettings:
    """The same lookup finds where the allowlist files are."""

    def test_main_allowlist(self, claude_home):
        write_settings(claude_home, {"TG_ALLOWED_CHATS_FILE": "~/lists/team.json"})
        assert cli.resolve_allowlist_path(None) == claude_home / "lists" / "team.json"

    def test_personal_additions(self, claude_home):
        write_settings(claude_home, {"TG_ALLOWED_CHATS_LOCAL_FILE": "~/lists/mine.json"})
        assert cli.resolve_local_allowlist_path() == claude_home / "lists" / "mine.json"

    def test_the_flag_still_wins(self, claude_home, tmp_path):
        write_settings(claude_home, {"TG_ALLOWED_CHATS_FILE": "~/lists/team.json"})
        assert cli.resolve_allowlist_path(str(tmp_path / "flag.json")) == tmp_path / "flag.json"


class TestMissingCredentialMessages:
    def test_missing_api_credentials_point_at_settings_json(self, claude_home):
        with pytest.raises(SystemExit) as exc:
            cli.credentials()
        message = str(exc.value)
        assert "TELEGRAM_API_ID" in message
        assert "TELEGRAM_API_HASH" in message
        assert "~/.claude/settings.json" in message

    def test_missing_session_string_points_at_settings_json(self, claude_home):
        with pytest.raises(SystemExit) as exc:
            cli.session_string()
        message = str(exc.value)
        assert "telegram-mcp login" in message
        assert "~/.claude/settings.json" in message

    @pytest.mark.parametrize("missing", [cli.credentials, cli.session_string])
    def test_the_messages_name_the_file_when_the_directory_is_moved(
        self, claude_home, tmp_path, monkeypatch, missing
    ):
        moved = tmp_path / "other-account"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(moved))
        with pytest.raises(SystemExit) as exc:
            missing()
        assert str(moved / "settings.json") in str(exc.value)


class FakeDialog:
    """What Telethon's iter_dialogs yields. ``entity`` is the peer id itself, see fake_telethon."""

    def __init__(self, peer_id, name, is_channel=False, is_group=False):
        self.entity = peer_id
        self.name = name
        self.is_channel = is_channel
        self.is_group = is_group


DIALOGS = [
    FakeDialog(-1001111111111, "Team chat", is_channel=True),
    FakeDialog(-1002222222222, "Alerts", is_group=True),
    FakeDialog(42, "Alice"),
]


def fake_telethon(monkeypatch):
    """Put a fake in place of Telethon's client and return the list of clients built from it.

    The real ``_connected`` and ``build_client`` still run, credentials lookup included:
    what the fake remembers is exactly what Telethon would have been given.
    """
    built = []

    class FakeTelegramClient:
        def __init__(self, session, api_id, api_hash, **_kwargs):
            self.session = session
            self.api_id = api_id
            self.api_hash = api_hash
            built.append(self)

        async def connect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def iter_dialogs(self, limit=None):
            for dialog in DIALOGS[:limit]:
                yield dialog

        async def disconnect(self):
            pass

    monkeypatch.setattr("telethon.TelegramClient", FakeTelegramClient)
    monkeypatch.setattr("telethon.sessions.StringSession", lambda session: session)
    monkeypatch.setattr("telethon.utils.get_peer_id", lambda entity: entity)
    return built


def what_telethon_was_given(built):
    return [(client.session, client.api_id, client.api_hash) for client in built]


class TestAllowlistIsRequiredOnlyWhereItGuards:
    """`dialogs` is how an allowlist gets built, so it has to work before there is one.

    `check` and `download` are the other way round: they need the list, and fail without
    it before anything reaches Telegram. That is the security boundary, and it stays put.
    """

    @pytest.fixture(autouse=True)
    def credentials_in_the_environment(self, claude_home, monkeypatch):
        monkeypatch.setenv("TELEGRAM_API_ID", "12345")
        monkeypatch.setenv("TELEGRAM_API_HASH", "abcdef0123456789")
        monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")

    def test_dialogs_lists_chats_without_an_allowlist_file(self, monkeypatch, capsys):
        fake_telethon(monkeypatch)
        assert not cli.default_allowlist_path().exists()  # the premise: there is no allowlist file

        cli.main(["dialogs"])

        out = capsys.readouterr().out
        rows = [line.split(None, 2) for line in out.splitlines() if line.strip()][:3]
        assert rows == [
            ["-1001111111111", "channel", "Team chat"],
            ["-1002222222222", "group", "Alerts"],
            ["42", "direct", "Alice"],
        ]
        assert "Total: 3" in out

    def test_dialogs_json_skeleton_works_without_an_allowlist_file(self, monkeypatch, capsys):
        fake_telethon(monkeypatch)
        assert not cli.default_allowlist_path().exists()

        cli.main(["dialogs", "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert [chat["id"] for chat in payload["chats"]] == [-1001111111111, -1002222222222, 42]

    def test_check_still_fails_without_an_allowlist(self, claude_home, monkeypatch):
        built = fake_telethon(monkeypatch)
        missing = claude_home / "no-such-allowlist.json"
        with pytest.raises(FileNotFoundError):
            cli.main(["--allowlist", str(missing), "check"])
        assert built == []  # no client was even created

    def test_download_still_fails_without_an_allowlist(self, claude_home, monkeypatch):
        built = fake_telethon(monkeypatch)
        missing = claude_home / "no-such-allowlist.json"
        with pytest.raises(FileNotFoundError):
            cli.main(["--allowlist", str(missing), "download", "team", "--message", "4242"])
        assert built == []


class TestTerminalWithOnlyClaudeSettings:
    """A plugin user keeps the credentials in settings.json and exports nothing."""

    def test_dialogs_needs_no_export_and_no_allowlist(self, claude_home, monkeypatch, capsys):
        write_settings(
            claude_home,
            {
                "TELEGRAM_API_ID": "12345",
                "TELEGRAM_API_HASH": "abcdef0123456789",
                "TELEGRAM_SESSION_STRING": "fake-session",
            },
        )
        built = fake_telethon(monkeypatch)
        assert not cli.default_allowlist_path().exists()

        cli.main(["dialogs"])

        assert what_telethon_was_given(built) == [("fake-session", 12345, "abcdef0123456789")]
        assert "Team chat" in capsys.readouterr().out

    def test_login_builds_its_client_from_settings_too(self, claude_home, monkeypatch):
        write_settings(claude_home, {"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "abcdef0123456789"})
        built = fake_telethon(monkeypatch)

        cli.build_client(None)  # what `login` starts from: there is no session string yet

        assert what_telethon_was_given(built) == [("", 12345, "abcdef0123456789")]
