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


def call_tool(name, arguments, allowlist_path, timeout=180):
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
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    ]
    # Credentials are stripped on purpose: the server must refuse a forbidden chat
    # without them, and must ask for them as soon as a chat is allowed.
    env = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")}
    env["TG_ALLOWED_CHATS_FILE"] = str(allowlist_path)
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
