#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak>=0.21"]
# ///
"""Claude Code → 小智 Buddy Bridge (常驻 daemon)

左侧：Unix socket server，接 Claude Code hooks 发来的事件
右侧：BLE central (bleak)，连小智 NUS，按 REFERENCE.md 协议推送 / 接收

一个进程做三件事：
1) 聚合多 Claude Code session 的运行态（running / waiting / transcript tail / tokens）
2) 1Hz 把聚合快照通过 BLE 发给小智（或变更时立即推）
3) 收小智回来的 permission 决策，匹配到具体 session 的阻塞 hook 返回

依赖由 PEP 723 内联元数据声明：`uv run` 自动在缓存的 venv 里装 bleak，
不污染用户环境。无 uv 时降级为 `python3`，要求用户自行 `pip install bleak`。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

# ---------------------------------------------------------------------------
# 协议常量（和 Desktop Buddy 完全一致）

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # host → device
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # device → host

SOCK_PATH_DEFAULT = "/tmp/claude-buddy-bridge.sock"

# ---------------------------------------------------------------------------


@dataclass
class Session:
    """一个 Claude Code 会话的实时状态。"""

    session_id: str
    cwd: str = ""
    running: bool = False  # 工具正在执行
    waiting: bool = False  # 阻塞在权限决策
    current_tool: str = ""  # 正在跑或正在等审批的工具名
    current_input: dict = field(default_factory=dict)
    last_prompt: str = ""  # 最近的 user prompt (截断)
    last_assistant: str = ""  # 最近的 assistant 消息
    transcript_path: str = ""
    tokens_total: int = 0
    # 待决权限：id -> asyncio.Future（小智回执时 set_result）
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    # 最近活动时间（idle 判定）
    last_activity: float = field(default_factory=time.time)


class Bridge:
    def __init__(self, device_prefix: str, sock_path: str, owner: str):
        self.device_prefix = device_prefix
        self.sock_path = sock_path
        self.owner = owner
        self.sessions: dict[str, Session] = {}
        self.entries: deque[str] = deque(maxlen=8)  # 全局 transcript tail
        self.tokens_today: int = 0
        self.tokens_total: int = 0
        # 当前正在审批的 prompt（Desktop 协议里 prompt 对象只允许一个）
        self.current_prompt_id: Optional[str] = None  # 等于 "<session_id>:<req_seq>"
        self.current_prompt_session: Optional[str] = None
        self.current_prompt_tool: str = ""
        self.current_prompt_hint: str = ""
        # 排队未推送的 prompt（FIFO），id -> (session_id, tool, hint)
        self.prompt_queue: deque[tuple[str, str, str, str]] = deque()
        # BLE
        self.ble_client: Optional[BleakClient] = None
        self.ble_rx_buf: bytearray = bytearray()
        self.ble_write_lock = asyncio.Lock()
        self.dirty = asyncio.Event()
        self.stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Session reference counting
    #   /tmp/claude-buddy-sessions/<sid> 文件的存在 = 活跃 session。
    #   Claude Code SessionStart hook touch 它；SessionEnd hook 删它。
    #   daemon 每秒扫，空目录超过 EXIT_GRACE_SEC 就自己优雅退出。

    async def session_sentinel(self, dir_path: Path, grace_sec: int) -> None:
        """若 session 引用计数长时间为零就退出。"""
        empty_since: float | None = None
        while not self.stop_event.is_set():
            try:
                live = [p for p in dir_path.iterdir() if p.is_file()]
            except FileNotFoundError:
                live = []
            if not live:
                if empty_since is None:
                    empty_since = time.time()
                elif time.time() - empty_since >= grace_sec:
                    logging.info(
                        "no active Claude Code sessions for %ds, exiting", grace_sec
                    )
                    self.stop_event.set()
                    break
            else:
                empty_since = None
            await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # 快照构造（对齐 REFERENCE.md heartbeat snapshot）

    def _snapshot(self) -> dict:
        running = sum(1 for s in self.sessions.values() if s.running)
        waiting = sum(1 for s in self.sessions.values() if s.waiting)
        total = len(self.sessions)

        # msg：一行摘要
        if self.current_prompt_id is not None:
            msg = f"approve: {self.current_prompt_tool}"
        elif running > 0:
            # 取一个 running session 的工具名
            tool = next(
                (s.current_tool for s in self.sessions.values() if s.running and s.current_tool),
                "working",
            )
            msg = f"{tool} running"
        elif total > 0:
            msg = f"{total} session{'s' if total > 1 else ''} idle"
        else:
            msg = "idle"

        snap: dict[str, Any] = {
            "total": total,
            "running": running,
            "waiting": waiting,
            "msg": msg,
            "entries": [e.replace("#", "＃") for e in self.entries],
            "tokens": self.tokens_total,
            "tokens_today": self.tokens_today,
        }
        if self.current_prompt_id is not None:
            snap["prompt"] = {
                "id": self.current_prompt_id,
                "tool": self.current_prompt_tool,
                "hint": self.current_prompt_hint,
            }
        return snap

    # ------------------------------------------------------------------
    # Hook 事件分发

    async def handle_hook_event(
        self, payload: dict, writer: asyncio.StreamWriter
    ) -> None:
        """处理来自 hook 脚本的 JSON 消息。

        payload 由 hook 脚本构造，形如：
          {"kind":"preuse_start", "session_id": "...", "req_id":"...",
           "tool_name":"Bash", "tool_input":{...}, "cwd":"..."}
          {"kind":"event", "session_id":"...", "hook_event_name":"Stop", ...}
        """
        kind = payload.get("kind")
        sid = payload.get("session_id") or "default"

        if kind == "preuse_start":
            await self._on_preuse(payload, writer)
            return  # writer 已被 _on_preuse 接管
        elif kind == "event":
            self._on_generic_event(payload)
        elif kind == "ping":
            pass
        elif kind == "fake_ble":
            # 调试通道：直接分发一条模拟的 BLE 消息到 _handle_ble_msg
            self._handle_ble_msg(payload.get("payload") or {})
        elif kind == "list_pending":
            pending = []
            for sess in self.sessions.values():
                for req_id in sess.pending.keys():
                    pending.append({"session_id": sess.session_id, "req_id": req_id})
            try:
                writer.write(
                    (json.dumps({"ack": True, "pending": pending}) + "\n").encode()
                )
                await writer.drain()
            except Exception:
                pass
            writer.close()
            return
        elif kind == "dump_snapshot":
            try:
                writer.write(
                    (json.dumps({"ack": True, "snapshot": self._snapshot()}) + "\n").encode()
                )
                await writer.drain()
            except Exception:
                pass
            writer.close()
            return
        elif kind == "screenshot":
            jpg_b64 = await self._take_screenshot()
            try:
                if jpg_b64:
                    writer.write(
                        (json.dumps({"ok": True, "data": jpg_b64}) + "\n").encode()
                    )
                else:
                    writer.write(
                        (json.dumps({"ok": False, "error": "screenshot failed"}) + "\n").encode()
                    )
                await writer.drain()
            except Exception:
                pass
            writer.close()
            return
        else:
            logging.warning("unknown hook kind: %r", kind)

        # 默认 ack（除 preuse 外）
        try:
            writer.write(b'{"ack":true}\n')
            await writer.drain()
        except Exception:
            pass
        writer.close()
        self.dirty.set()

    async def _on_preuse(
        self, payload: dict, writer: asyncio.StreamWriter
    ) -> None:
        sid = payload["session_id"]
        req_id = payload.get("req_id") or f"{sid[:6]}-{int(time.time()*1000)}"
        tool_name = payload.get("tool_name", "?")
        tool_input = payload.get("tool_input", {}) or {}
        cwd = payload.get("cwd", "")

        sess = self.sessions.setdefault(sid, Session(session_id=sid))
        sess.cwd = cwd
        sess.waiting = True
        sess.current_tool = tool_name
        sess.current_input = tool_input
        sess.last_activity = time.time()

        # 构造 hint（短摘要，适合小屏显示）
        hint = _summarize_tool_input(tool_name, tool_input)
        self._queue_prompt(sid, req_id, tool_name, hint)

        # 建 Future，等小智或超时
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        sess.pending[req_id] = fut

        self.dirty.set()

        try:
            # 超时略小于 hook 端 timeout，给 stdout 返回留余量
            decision = await asyncio.wait_for(fut, timeout=540)
        except asyncio.TimeoutError:
            decision = "ask"
            logging.info("session=%s req=%s timeout, fallback=ask", sid, req_id)

        # 清理状态
        sess.pending.pop(req_id, None)
        sess.waiting = False
        if self.current_prompt_id == req_id:
            self._consume_current_prompt()
        else:
            # 从队列里剔除
            self.prompt_queue = deque(
                (r, s, t, h) for (r, s, t, h) in self.prompt_queue if r != req_id
            )

        self.dirty.set()

        # 把决策写给 hook 脚本
        try:
            writer.write((json.dumps({"decision": decision}) + "\n").encode())
            await writer.drain()
        except Exception as e:
            logging.warning("writeback to hook failed: %s", e)
        writer.close()

    def _queue_prompt(self, sid: str, req_id: str, tool: str, hint: str) -> None:
        sess = self.sessions.setdefault(sid, Session(session_id=sid))
        # 占用文本摘要
        self.entries.appendleft(
            f"{time.strftime('%H:%M')} {tool}: {hint[:40]}"
        )
        if self.current_prompt_id is None:
            self.current_prompt_id = req_id
            self.current_prompt_session = sid
            self.current_prompt_tool = tool
            self.current_prompt_hint = hint
        else:
            self.prompt_queue.append((req_id, sid, tool, hint))

    def _consume_current_prompt(self) -> None:
        if self.prompt_queue:
            req_id, sid, tool, hint = self.prompt_queue.popleft()
            self.current_prompt_id = req_id
            self.current_prompt_session = sid
            self.current_prompt_tool = tool
            self.current_prompt_hint = hint
        else:
            self.current_prompt_id = None
            self.current_prompt_session = None
            self.current_prompt_tool = ""
            self.current_prompt_hint = ""

    def _on_generic_event(self, payload: dict) -> None:
        sid = payload["session_id"]
        ev = payload.get("hook_event_name", "")
        sess = self.sessions.setdefault(sid, Session(session_id=sid))
        sess.last_activity = time.time()

        if ev == "UserPromptSubmit":
            prompt = (payload.get("prompt") or "").strip().replace("\n", " ")
            sess.last_prompt = prompt[:120]
            sess.running = True
            self.entries.appendleft(f"{time.strftime('%H:%M')} {prompt[:240]}")
        elif ev == "PreToolUse":
            sess.running = True
            tool = payload.get("tool_name", "")
            sess.current_tool = tool
        elif ev in ("PostToolUse", "PostToolUseFailure"):
            # 工具结束 → 清除该 session 的 waiting 状态和对应的 prompt
            # (如果 Claude Code 端直接放行了，设备端 prompt 要清掉)
            sess.waiting = False
            if self.current_prompt_session == sid:
                self.current_prompt_id = None
                self.current_prompt_session = None
                self.current_prompt_tool = None
                self.current_prompt_hint = None
                self._consume_current_prompt()
            tool = payload.get("tool_name", "")
            tool_input = payload.get("tool_input", {})
            summary = _summarize_tool_input(tool, tool_input) if tool_input else tool
            if ev == "PostToolUse":
                self.entries.appendleft(
                    f"{time.strftime('%H:%M')} {summary[:240]}"
                )
            else:
                self.entries.appendleft(
                    f"{time.strftime('%H:%M')} {tool} failed"
                )
        elif ev == "Stop":
            sess.running = False
            # 尝试从 transcript 拉 token 计数
            tpath = payload.get("transcript_path", "")
            if tpath:
                sess.transcript_path = tpath
                t = _tally_tokens(tpath)
                if t is not None:
                    delta = max(0, t - sess.tokens_total)
                    sess.tokens_total = t
                    self.tokens_total += delta
                    self.tokens_today += delta
                # 拉最新 assistant 回复加入 entries
                reply = _last_assistant_text(tpath, max_chars=240)
                if reply:
                    self.entries.appendleft(
                        f"{time.strftime('%H:%M')} {reply}"
                    )
        elif ev == "SessionStart":
            sess.running = False
        elif ev == "SessionEnd":
            # 清掉
            self.sessions.pop(sid, None)
        elif ev == "Notification":
            nt = payload.get("notification_type", "")
            if nt == "idle_prompt":
                sess.running = False
            elif nt == "permission_prompt":
                sess.waiting = True

    # ------------------------------------------------------------------
    # Unix socket server

    async def start_socket_server(self) -> asyncio.base_events.Server:
        # 清掉旧 socket
        try:
            os.unlink(self.sock_path)
        except FileNotFoundError:
            pass
        server = await asyncio.start_unix_server(
            self._on_socket_conn, path=self.sock_path
        )
        os.chmod(self.sock_path, 0o600)
        logging.info("socket listening: %s", self.sock_path)
        return server

    async def _on_socket_conn(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            if not line:
                writer.close()
                return
            payload = json.loads(line.decode().strip())
        except Exception as e:
            logging.warning("bad hook payload: %s", e)
            writer.close()
            return
        await self.handle_hook_event(payload, writer)

    # ------------------------------------------------------------------
    # BLE client

    async def ble_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                dev = await self._scan_device()
                if dev is None:
                    await asyncio.sleep(3)
                    continue
                logging.info("connecting %s (%s)", dev.name, dev.address)
                async with BleakClient(dev, timeout=20) as client:
                    self.ble_client = client
                    await client.start_notify(NUS_TX, self._ble_notify_cb)
                    # 连上先推 owner + time（对齐 Desktop 行为）
                    await self._ble_send({"cmd": "owner", "name": self.owner})
                    now = int(time.time())
                    tz = -time.timezone if time.daylight == 0 else -time.altzone
                    await self._ble_send({"time": [now, tz]})
                    logging.info("BLE connected, owner=%s sent", self.owner)
                    await self._ble_serve(client)
            except Exception as e:
                logging.warning("BLE loop error: %s", e)
                self.ble_client = None
                await asyncio.sleep(2)

    async def _scan_device(self) -> Optional[BLEDevice]:
        logging.info("scanning for %s*…", self.device_prefix)
        devices = await BleakScanner.discover(timeout=6)
        for d in devices:
            if d.name and d.name.startswith(self.device_prefix):
                return d
        return None

    async def _ble_serve(self, client: BleakClient) -> None:
        """连接建立后：按变更/心跳推送快照，直到掉线或停止。"""
        last_push = 0.0
        while client.is_connected and not self.stop_event.is_set():
            # 有变更立即推，或 10s 心跳兜底
            try:
                await asyncio.wait_for(self.dirty.wait(), timeout=10)
                self.dirty.clear()
            except asyncio.TimeoutError:
                pass
            now = time.time()
            if now - last_push >= 0.2:  # 最快 5Hz 防刷屏
                snap = self._snapshot()
                await self._ble_send(snap)
                last_push = now
        logging.info("BLE disconnected")

    async def _take_screenshot(self) -> Optional[str]:
        """Send screenshot command to device, collect JPEG chunks, return base64."""
        if self.ble_client is None or not self.ble_client.is_connected:
            return None

        chunks = bytearray()
        done = asyncio.Event()
        accepting = [False]

        # Temporarily intercept BLE notifications for screenshot data
        original_buf = bytearray(self.ble_rx_buf)
        self.ble_rx_buf.clear()

        try:
            # Send screenshot command
            cmd = json.dumps({"cmd": "screenshot"}) + "\n"
            await self.ble_client.write_gatt_char(
                NUS_RX, cmd.encode(), response=False
            )

            # Wait for ss_begin → ss_chunk* → ss_end
            deadline = time.time() + 15
            while time.time() < deadline and not done.is_set():
                await asyncio.sleep(0.05)
                # Process any data in rx_buf
                while b"\n" in self.ble_rx_buf:
                    line_bytes, _, rest = self.ble_rx_buf.partition(b"\n")
                    self.ble_rx_buf = bytearray(rest)
                    line = line_bytes.decode("utf-8", errors="replace")
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    evt = obj.get("evt", "")
                    if evt == "ss_begin":
                        accepting[0] = True
                        chunks.clear()
                    elif evt == "ss_chunk" and accepting[0]:
                        chunks.extend(base64.b64decode(obj.get("d", "")))
                    elif evt == "ss_end":
                        accepting[0] = False
                        done.set()

            if done.is_set() and chunks:
                return base64.b64encode(bytes(chunks)).decode("ascii")
            return None
        except Exception as e:
            logging.warning("screenshot error: %s", e)
            return None

    async def _ble_send(self, obj: dict) -> None:
        if self.ble_client is None or not self.ble_client.is_connected:
            return
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        async with self.ble_write_lock:
            try:
                # 小智固件默认 MTU 小，分片写
                mtu = 180
                for i in range(0, len(data), mtu):
                    await self.ble_client.write_gatt_char(
                        NUS_RX, data[i : i + mtu], response=False
                    )
            except Exception as e:
                logging.warning("BLE write failed: %s", e)

    def _ble_notify_cb(self, _sender, data: bytearray) -> None:
        self.ble_rx_buf.extend(data)
        while b"\n" in self.ble_rx_buf:
            line, _, rest = self.ble_rx_buf.partition(b"\n")
            self.ble_rx_buf = bytearray(rest)
            try:
                msg = json.loads(line.decode("utf-8").strip())
            except Exception:
                continue
            self._handle_ble_msg(msg)

    def _handle_ble_msg(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        if cmd == "permission":
            req_id = msg.get("id", "")
            decision = msg.get("decision", "")  # "once" | "deny"
            # 找到对应 session 的 Future
            for sess in self.sessions.values():
                fut = sess.pending.get(req_id)
                if fut is not None and not fut.done():
                    # 映射到 Claude Code hookSpecificOutput.permissionDecision
                    # once → "allow" ; deny → "deny"
                    out = "allow" if decision == "once" else "deny"
                    fut.set_result(out)
                    logging.info(
                        "permission %s for session=%s req=%s",
                        out,
                        sess.session_id,
                        req_id,
                    )
                    return
            logging.warning("permission for unknown req_id=%s", req_id)
        elif cmd == "status":
            # 小智问状态，简单回一个
            asyncio.create_task(
                self._ble_send(
                    {
                        "ack": "status",
                        "ok": True,
                        "data": {
                            "name": f"bridge@{os.uname().nodename}",
                            "stats": {
                                "appr": sum(
                                    1 for _ in self.sessions.values()
                                ),
                            },
                        },
                    }
                )
            )
        else:
            logging.debug("ble rx: %r", msg)


# ---------------------------------------------------------------------------
# 工具

def _summarize_tool_input(tool: str, tool_input: dict) -> str:
    """生成摘要，适合小屏显示。"""
    if tool == "Bash":
        cmd = (tool_input.get("command") or "").replace("\n", " ")
        return f"Bash({cmd[:230]})"
    if tool in ("Edit", "Write"):
        fp = tool_input.get("file_path") or ""
        # 只取文件名，不显示全路径
        name = fp.rsplit("/", 1)[-1] if "/" in fp else fp
        return f"{tool}({name})"
    if tool == "Read":
        fp = tool_input.get("file_path") or ""
        name = fp.rsplit("/", 1)[-1] if "/" in fp else fp
        return f"Read({name})"
    if tool == "WebFetch":
        return (tool_input.get("url") or "")[:120]
    # 兜底：挑前两个字段
    parts = []
    for k, v in list(tool_input.items())[:2]:
        s = str(v).replace("\n", " ")[:30]
        parts.append(f"{k}={s}")
    return ",".join(parts)[:60]


def _tally_tokens(transcript_path: str) -> Optional[int]:
    """从 Claude Code transcript JSONL 里拉 output token 累计。"""
    p = Path(transcript_path)
    if not p.exists():
        return None
    total = 0
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                # Claude Code transcript 格式里 usage 在 message 对象下
                usage = None
                if isinstance(obj, dict):
                    msg = obj.get("message")
                    if isinstance(msg, dict):
                        usage = msg.get("usage")
                    if not usage:
                        usage = obj.get("usage")
                if isinstance(usage, dict):
                    total += int(usage.get("output_tokens") or 0)
    except Exception:
        return None
    return total


def _last_assistant_text(transcript_path: str, max_chars: int = 240) -> Optional[str]:
    """从 Claude Code transcript JSONL 提取最新 assistant 文本回复（截取前 max_chars 字符）。"""
    p = Path(transcript_path)
    if not p.exists():
        return None
    last_text = None
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                # 拼接所有 text block
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                if texts:
                    last_text = " ".join(texts)
    except Exception:
        return None
    if not last_text:
        return None
    # 截取，去换行
    s = last_text.replace("\n", " ").strip()
    return s[:max_chars] if len(s) > max_chars else s


# ---------------------------------------------------------------------------
# 入口

async def amain(args) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    bridge = Bridge(
        device_prefix=args.device_prefix,
        sock_path=args.sock,
        owner=args.owner,
    )

    # 信号
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, bridge.stop_event.set)

    server = await bridge.start_socket_server()
    ble_task = asyncio.create_task(bridge.ble_loop())

    # Session 引用计数：空 > grace 秒就自己退出
    sessions_dir = Path(args.sessions_dir)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sentinel_task: asyncio.Task | None = None
    if args.exit_on_idle > 0:
        sentinel_task = asyncio.create_task(
            bridge.session_sentinel(sessions_dir, args.exit_on_idle)
        )

    try:
        await bridge.stop_event.wait()
    finally:
        logging.info("shutting down…")
        ble_task.cancel()
        if sentinel_task:
            sentinel_task.cancel()
        # 断 BLE：触发 buddy 面板退出 → 小智回原界面
        if bridge.ble_client and bridge.ble_client.is_connected:
            try:
                await bridge.ble_client.disconnect()
            except Exception:
                pass
        server.close()
        await server.wait_closed()
        try:
            os.unlink(bridge.sock_path)
        except FileNotFoundError:
            pass
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--device-prefix",
        default="Claude-",
        help="BLE 设备名前缀（默认 'Claude-'，匹配小智 Claude-XXXX）",
    )
    p.add_argument("--sock", default=SOCK_PATH_DEFAULT)
    p.add_argument(
        "--owner",
        default="",
        help="传给小智显示的 owner name (空则用 $USER)",
    )
    p.add_argument(
        "--sessions-dir",
        default="/tmp/claude-buddy-sessions",
        help="活跃 Claude Code session 标记目录；每文件=一个活 session",
    )
    p.add_argument(
        "--exit-on-idle",
        type=int,
        default=5,
        help="session 目录空此秒数后自动退出；0=永不（纯 daemon 模式）",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    if not args.owner:
        args.owner = os.environ.get("USER", "Felix")
    if not args.device_prefix:
        args.device_prefix = "Claude-"
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
