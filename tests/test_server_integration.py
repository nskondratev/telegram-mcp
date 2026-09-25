"""End-to-end test over stdio: the allowlist fires before we connect anywhere.

The server runs as a real process without Telegram credentials. If a request for
a foreign chat answered "no environment variables", we would already have gone to
Telegram before checking the allowlist; we expect exactly the opposite.
"""
import json
import os
import subprocess
import sys

FORBIDDEN_CHAT = "-1001234567890"
ALLOWED_CHAT_ID = -1005555555555


def rpc(method, params, allowlist_path, timeout=180, local_path=None):
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
    ]
    # Credentials are stripped on purpose: the server must refuse a forbidden chat
    # without them, and must ask for them as soon as a chat is allowed.
    env = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")}
    env["TG_ALLOWED_CHATS_FILE"] = str(allowlist_path)
    # Never pick up the personal additions of whoever runs the tests.
    env["TG_ALLOWED_CHATS_LOCAL_FILE"] = str(local_path or allowlist_path.parent / "absent.local.json")
    proc = subprocess.Popen(
        [sys.executable, "-m", "telegram_mcp", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        for request in requests:
            proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        # stdin stays open: on EOF the server exits before draining the queue.
        for line in proc.stdout:
            if not line.strip():
                continue
            message = json.loads(line)
            if message.get("id") == 2:
                return message
        raise AssertionError(f"no answer to tools/call; stderr={proc.stderr.read()}")
    finally:
        proc.kill()
        proc.wait(timeout=timeout)


def call_tool(name, arguments, allowlist_path, timeout=180, local_path=None):
    return rpc("tools/call", {"name": name, "arguments": arguments}, allowlist_path, timeout, local_path)


def write_allowlist(tmp_path):
    path = tmp_path / "allowed_chats.json"
    path.write_text(
        json.dumps({"chats": [{"alias": "work", "id": ALLOWED_CHAT_ID, "title": "Work"}]}),
        encoding="utf-8",
    )
    return path


def test_forbidden_chat_is_rejected_before_connecting_to_telegram(tmp_path):
    response = call_tool("get_messages", {"chat": FORBIDDEN_CHAT, "limit": 5}, write_allowlist(tmp_path))
    text = json.dumps(response, ensure_ascii=False)
    assert "allowlist" in text, text
    assert "TELEGRAM_API_ID" not in text, "went to Telegram first and checked the allowlist after"


def test_allowed_chat_reaches_telegram_layer(tmp_path):
    """An allowed chat goes further — and stops at the missing credentials."""
    response = call_tool("get_messages", {"chat": "work", "limit": 5}, write_allowlist(tmp_path))
    assert "TELEGRAM_API_ID" in json.dumps(response, ensure_ascii=False)


def test_media_tool_is_registered_and_read_only(tmp_path):
    response = rpc("tools/list", {}, write_allowlist(tmp_path))
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    assert set(tools) == {
        "list_chats",
        "get_chat_info",
        "get_messages",
        "get_message_context",
        "search_messages",
        "get_message_media",
    }
    assert tools["get_message_media"]["annotations"]["readOnlyHint"] is True


def test_media_tool_rejects_a_forbidden_chat_before_connecting(tmp_path):
    response = call_tool(
        "get_message_media",
        {"chat": FORBIDDEN_CHAT, "message_id": 1},
        write_allowlist(tmp_path),
    )
    text = json.dumps(response, ensure_ascii=False)
    assert "allowlist" in text, text
    assert "TELEGRAM_API_ID" not in text, "went to Telegram first and checked the allowlist after"


def write_local_allowlist(tmp_path):
    path = tmp_path / "allowed_chats.local.json"
    path.write_text(
        json.dumps({"chats": [{"alias": "side", "id": -1006666666666, "title": "Side"}]}),
        encoding="utf-8",
    )
    return path


def test_chat_from_the_personal_additions_reaches_telegram_layer(tmp_path):
    response = call_tool(
        "get_messages",
        {"chat": "side", "limit": 5},
        write_allowlist(tmp_path),
        local_path=write_local_allowlist(tmp_path),
    )
    assert "TELEGRAM_API_ID" in json.dumps(response, ensure_ascii=False)


def test_personal_additions_do_not_open_other_chats(tmp_path):
    response = call_tool(
        "get_messages",
        {"chat": FORBIDDEN_CHAT, "limit": 5},
        write_allowlist(tmp_path),
        local_path=write_local_allowlist(tmp_path),
    )
    text = json.dumps(response, ensure_ascii=False)
    assert "allowlist" in text, text
    assert "TELEGRAM_API_ID" not in text, "went to Telegram first and checked the allowlist after"
