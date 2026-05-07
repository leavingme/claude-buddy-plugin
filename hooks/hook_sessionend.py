#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""SessionEnd hook: 取消该 session 的引用计数。

删 /tmp/claude-buddy-sessions/<sid>。daemon 见到目录空 > grace 秒就自己退。
"""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

SESSIONS_DIR = Path("/tmp/claude-buddy-sessions")
SOCK = "/tmp/claude-buddy-bridge.sock"
SPAWN_LOG = Path("/tmp/claude-buddy-spawn.log")


def _log(msg: str) -> None:
    try:
        with SPAWN_LOG.open("a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} [end] {msg}\n")
    except Exception:
        pass


def unmark_session(session_id: str) -> None:
    try:
        (SESSIONS_DIR / session_id).unlink(missing_ok=True)
    except Exception as e:
        _log(f"unmark({session_id}) failed: {e}")


def notify_daemon(session_id: str) -> None:
    """顺手给 daemon 发个 SessionEnd 事件（清理 pending/state）。"""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(SOCK)
        msg = {
            "kind": "event",
            "session_id": session_id,
            "hook_event_name": "SessionEnd",
        }
        s.sendall((json.dumps(msg) + "\n").encode())
        try:
            s.settimeout(0.5)
            s.recv(64)
        except Exception:
            pass
        s.close()
    except Exception:
        pass


def main() -> int:
    try:
        raw = sys.stdin.read()
        ev = json.loads(raw) if raw else {}
        sid = (ev.get("session_id") or "").strip()
    except Exception:
        sid = ""
    _log(f"invoked session_id={sid!r}")
    if sid:
        unmark_session(sid)
        notify_daemon(sid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
