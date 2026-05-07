#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Claude Code PreToolUse hook - 阻塞等小智审批。

stdin: hook 事件 JSON (Claude Code 注入)
stdout: {"hookSpecificOutput": {"permissionDecision": "allow"|"deny"|"ask"}}

流程：
1) 读 stdin hook payload
2) 通过 Unix socket 发 preuse_start 给 bridge daemon
3) 阻塞等 daemon 回决策（bridge 侧等小智 BLE 回执，内部超时 540s）
4) 若 daemon 连不上 / 超时，返回 "ask"（走 Claude Code 默认权限流程）
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid

SOCK_PATH = os.environ.get(
    "CLAUDE_BUDDY_SOCK", "/tmp/claude-buddy-bridge.sock"
)
# Claude Code hook 默认 600s，留 60s 余量
TIMEOUT_SEC = 540


def _emit(decision: str, reason: str = "") -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    print(json.dumps(out), flush=True)


def main() -> int:
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
    except Exception:
        _emit("ask", "hook: bad stdin JSON")
        return 0

    sid = ev.get("session_id") or "default"
    tool = ev.get("tool_name", "?")
    tool_input = ev.get("tool_input") or {}
    cwd = ev.get("cwd", "")
    permission_mode = ev.get("permission_mode", "")

    # Auto-accept 模式下不走 BLE 审批，直接放行
    # acceptEdits / bypassPermissions / auto 都属于自动放行
    if permission_mode in ("acceptEdits", "bypassPermissions", "auto"):
        _emit("allow", f"auto-accept mode ({permission_mode})")
        return 0

    # 跳过低危工具（避免审批疲劳）
    skip = os.environ.get("CLAUDE_BUDDY_SKIP_TOOLS", "Read,Glob,Grep,TodoWrite,TaskList,TaskGet,TaskUpdate,TaskCreate,WebFetch,WebSearch,LSP").split(",")
    if tool.strip() in [s.strip() for s in skip if s.strip()]:
        _emit("allow", f"auto-allow low-risk tool {tool}")
        return 0

    req_id = f"{sid[:6]}-{uuid.uuid4().hex[:8]}"

    payload = {
        "kind": "preuse_start",
        "session_id": sid,
        "req_id": req_id,
        "tool_name": tool,
        "tool_input": tool_input,
        "cwd": cwd,
        "transcript_path": ev.get("transcript_path", ""),
    }

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(SOCK_PATH)
    except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
        # daemon 没跑：降级为 ask（让 Claude Code 弹默认审批框）
        _emit("ask", f"buddy bridge unavailable: {e}")
        return 0

    try:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        # 现在等 daemon 回决策
        sock.settimeout(TIMEOUT_SEC)
        buf = b""
        deadline = time.time() + TIMEOUT_SEC
        while time.time() < deadline:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        if b"\n" not in buf:
            _emit("ask", "buddy timeout")
            return 0
        line = buf.split(b"\n", 1)[0]
        resp = json.loads(line.decode("utf-8"))
        decision = resp.get("decision", "ask")
        if decision not in ("allow", "deny", "ask"):
            decision = "ask"
        _emit(decision, f"buddy: {decision} via BLE")
        return 0
    except socket.timeout:
        _emit("ask", "buddy socket timeout")
        return 0
    except Exception as e:
        _emit("ask", f"buddy error: {e}")
        return 0
    finally:
        try:
            sock.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
