#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Claude Code PermissionRequest hook - 阻塞等 Buddy 硬件审批。

stdin: hook 事件 JSON (Claude Code 注入)
stdout: {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"|"deny"}}}

流程：
1) 读 stdin hook payload（PermissionRequest 只在需要弹审批框时触发）
2) 通过 Unix socket 发 permission_request 给 bridge daemon
3) 阻塞等 daemon 回决策（bridge 侧判断 BLE 未连则立即返回 ask，已连则等 Buddy 回执，最多 540s）
4) 若 daemon 连不上 / 超时，返回 behavior: "ask"（走 Claude Code 默认审批流程）
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path

SOCK_PATH = os.environ.get(
    "CLAUDE_BUDDY_SOCK", "/tmp/claude-buddy-bridge.sock"
)
# Claude Code hook 默认 600s，留 60s 余量
TIMEOUT_SEC = 540
# per-project Buddy 禁用标记目录
BUDDY_DISABLED_DIR = Path.home() / ".claude-buddy" / "disabled"


def _emit(behavior: str, reason: str = "") -> None:
    """输出 PermissionRequest 格式的决策。

    behavior: "allow" | "deny" | "ask"
    - allow: 直接放行，不弹审批框
    - deny: 拒绝操作
    - ask: 走 Claude Code 默认审批流程
    - notify_system: 是否注入 systemMessage（BLE 断开等需要告知用户的情况）
    """
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": behavior,
            }
        }
    }
    if reason:
        out["hookSpecificOutput"]["decision"]["reason"] = reason
    # 降级为 ask 时注入 systemMessage，让用户在对话里看到原因
    if behavior == "ask" and reason:
        out["systemMessage"] = f"[Buddy] {reason}，已切换为默认审批"
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

    # per-project 禁用检查：cwd 对应的项目是否被禁用 Buddy
    if cwd:
        project_name = Path(cwd).name
        if (BUDDY_DISABLED_DIR / project_name).exists():
            _emit("ask", f"Buddy disabled for project '{project_name}'")
            return 0

    req_id = f"{sid[:6]}-{uuid.uuid4().hex[:8]}"

    payload = {
        "kind": "permission_request",
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
        _emit("ask", f"buddy bridge unavailable: {e}")
        return 0

    try:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        # 等 daemon 回决策
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
        reason = resp.get("reason", "")
        # 只接受 allow/deny，ask 及以上默认值都降级为 ask
        if decision not in ("allow", "deny"):
            decision = "ask"
        if reason == "ble_not_connected":
            _emit(decision, "BLE 未连接")
        else:
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
