#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Claude Code 通用单向 hook - 推事件给 bridge daemon，不阻塞。

用于：Stop / SubagentStop / Notification / SessionStart / SessionEnd /
      UserPromptSubmit / PostToolUse / PostToolUseFailure

设计：
- 非阻塞：socket 发完就退，不等回执
- 失败静默：daemon 没跑不影响 Claude Code 正常运行
- hook 总是退出 0，不产生 stdout（不影响 Claude）
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time

SOCK_PATH = os.environ.get(
    "CLAUDE_BUDDY_SOCK", "/tmp/claude-buddy-bridge.sock"
)


def main() -> int:
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
    except Exception:
        return 0

    # Slim down tool_input to avoid huge payloads (Edit can have large strings)
    raw_input = ev.get("tool_input") or {}
    slim_input = {}
    if isinstance(raw_input, dict):
        for k in ("command", "file_path", "url", "query"):
            if k in raw_input:
                v = str(raw_input[k])
                slim_input[k] = v[:300] if len(v) > 300 else v

    payload = {
        "kind": "event",
        "session_id": ev.get("session_id") or "default",
        "hook_event_name": ev.get("hook_event_name", ""),
        "tool_name": ev.get("tool_name", ""),
        "tool_input": slim_input,
        "notification_type": ev.get("notification_type", ""),
        "prompt": ev.get("prompt", ""),
        "transcript_path": ev.get("transcript_path", ""),
        "cwd": ev.get("cwd", ""),
        "ts": time.time(),
    }

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(SOCK_PATH)
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        # 不读回复（bridge 会 ack 但我们不关心）
        try:
            sock.settimeout(1)
            sock.recv(64)
        except Exception:
            pass
        sock.close()
    except Exception:
        # 静默失败
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
