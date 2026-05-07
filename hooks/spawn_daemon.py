#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""SessionStart hook: 如果 bridge daemon 没跑，就后台拉起来。

监听 Claude Code SessionStart 事件，幂等保证 daemon 常驻：
- 检查 /tmp/claude-buddy-bridge.sock 存在且进程活着 → 什么都不做
- 否则后台 detach 起 daemon（nohup setsid，断了 SIGHUP 也不死）

日志：/tmp/claude-buddy-bridge.log（无条件）
      /tmp/claude-buddy-spawn.log（spawner 自己的诊断日志）

这是替代 plugin monitors 的方案。一旦 Claude Code 支持 manifest `monitors`
字段，改回 monitors 即可。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

SOCK = os.environ.get("CLAUDE_BUDDY_SOCK", "/tmp/claude-buddy-bridge.sock")
SPAWN_LOG = Path("/tmp/claude-buddy-spawn.log")
DAEMON_LOG = Path("/tmp/claude-buddy-bridge.log")
SESSIONS_DIR = Path("/tmp/claude-buddy-sessions")


def _log(msg: str) -> None:
    try:
        with SPAWN_LOG.open("a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} [spawn] {msg}\n")
    except Exception:
        pass


def mark_session_alive(session_id: str) -> None:
    """创建 /tmp/claude-buddy-sessions/<sid> 文件，daemon 据此判断活跃性。"""
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        (SESSIONS_DIR / session_id).touch()
    except Exception as e:
        _log(f"mark_session_alive({session_id}) failed: {e}")


def daemon_alive() -> bool:
    if not os.path.exists(SOCK):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(SOCK)
        s.sendall(b'{"kind":"ping"}\n')
        s.recv(64)
        s.close()
        return True
    except Exception:
        try:
            os.unlink(SOCK)
        except Exception:
            pass
        return False


def spawn_daemon() -> None:
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    _log(f"CLAUDE_PLUGIN_ROOT={plugin_root!r}")
    if not plugin_root:
        _log("ERROR: CLAUDE_PLUGIN_ROOT not set; giving up")
        return
    script = Path(plugin_root) / "scripts" / "buddy_bridge.py"
    _log(f"script path: {script} exists={script.exists()}")
    if not script.exists():
        return

    uv_path = shutil.which("uv")
    _log(f"which uv -> {uv_path!r}")
    if not uv_path:
        # 尝试用户常见位置
        for guess in ("/Users/" + os.environ.get("USER", "") + "/.local/bin/uv",
                      "/opt/homebrew/bin/uv",
                      "/usr/local/bin/uv"):
            if os.path.exists(guess):
                uv_path = guess
                _log(f"found uv at fallback: {guess}")
                break
    if not uv_path:
        _log("ERROR: uv not found in PATH nor fallbacks. PATH=" + os.environ.get("PATH", ""))
        return

    device_prefix = os.environ.get("CLAUDE_BUDDY_DEVICE_PREFIX") or "Claude-"
    owner = os.environ.get("CLAUDE_BUDDY_OWNER") or os.environ.get("USER", "Felix")

    cmd = [uv_path, "run", "--quiet", str(script),
           "--device-prefix", device_prefix,
           "--owner", owner, "-v"]
    _log(f"spawning: {' '.join(cmd)}")

    try:
        with DAEMON_LOG.open("ab", buffering=0) as lf:
            lf.write(f"\n=== spawn at {time.strftime('%F %T')} ===\n".encode())
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=lf,
                stderr=lf,
                start_new_session=True,
                close_fds=True,
            )
        _log(f"spawned pid={p.pid}")
    except Exception as e:
        _log(f"ERROR spawn failed: {e}")


def main() -> int:
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        pass

    # 从 stdin JSON 提 session_id 做引用计数
    session_id = ""
    try:
        ev = json.loads(raw) if raw else {}
        session_id = (ev.get("session_id") or "").strip()
    except Exception:
        session_id = ""

    _log(f"invoked session_id={session_id!r}")
    if session_id:
        mark_session_alive(session_id)

    if daemon_alive():
        _log("daemon already alive, skipping")
        return 0
    try:
        spawn_daemon()
    except Exception as e:
        _log(f"top-level exception: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
