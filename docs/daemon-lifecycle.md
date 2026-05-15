# Daemon 生命周期管理

## 需求

| 场景 | 预期行为 |
|------|----------|
| Terminal 关 | BLE 断开 |
| 最后一个 Claude Code 窗口关 | BLE 断开 |
| 多窗口：一个关 | daemon 继续服务其他窗口 |
| /clear | 复用已有 daemon，不产生新的 |

## 技术方案

### 现状问题

Terminal 关掉后 daemon 没死。原因分析：

1. `start_new_session=True` 让 daemon 创建独立 session（sid = daemon PID）
2. Terminal 关 → shell（session leader）收到 SIGHUP 退出
3. Daemon 在独立 session，不是 session leader，**不会收到 SIGHUP**
4. Daemon 变成孤儿进程，被 launchd 收养，继续运行
5. BLE 保持连接

### 方案：去掉 start_new_session=True

让 daemon 在原 session 中运行，terminal 关时 shell 会给 daemon 发 SIGHUP。

**注意**：daemon 不是 session leader，所以不会自动收到 SIGHUP。需要额外处理。

#### 方案 A：Daemon 处理 SIGHUP

Daemon 收到 SIGHUP → 优雅退出 → BLE 断开。

问题是：terminal 关时谁给 daemon 发 SIGHUP？
- Shell 是 session leader，收到 SIGHUP 退出
- Daemon 是 shell 的子进程（通过 spawn_daemon.py fork），在后台运行
- Shell 退出时不会给子进程发 SIGHUP（默认行为）

#### 方案 B：Daemon 在原 session，不在独立 session

当 terminal 关闭时，SIGHUP 发送给 session leader（shell）和 foreground process group。
Daemon 作为 background 进程，不会收到 SIGHUP。

#### 方案 C：Daemon 监控 Terminal 状态（推荐）

Daemon 定期检查 session 状态，如果发现 terminal 不存在就退出。

#### 方案 D：使用 pdeath signal

设置 daemon 的 parent death 行为，让 daemon 在 parent 退出时退出。

### 结论

需要进一步调研 Unix 进程机制，确定 terminal 关闭时如何让 daemon 也退出。

## 实际修复（2026-05-15）

### 问题根因

Claude Code 调用 hook 时，通过一个**短命的中间进程**执行，而不是直接 fork-exec。
`os.getppid()` 返回的是这个中间进程的 PID，执行完后立即退出。

进程关系：
```
claude (PID 69015)        ← 真正的 Claude Code 进程，有 TTY
└── 中间进程 (短命)        ← 执行 hook，getppid() 返回这个
    └── hook (spawn_daemon.py)
```

sentinel 检测到"所有 PID 都死了"后 5 秒退出，BLE 断开。

### 第一版修复方案

1. **`spawn_daemon.py`**：用 PPID 链向上查找真正的 claude 进程 PID
   - `find_claude_pid_via_ppid_chain()` 沿父进程链找到 comm 包含 "claude" 的进程
   - 不再依赖 `os.getppid()`（它返回中间进程）

2. **`buddy_bridge.py`**：
   - 读取 `/tmp/claude-buddy-pids/` 中由 hook 写入的 PID 文件
   - sentinel 逐个检测 PID，死的文件直接删

这个版本后来被下面的“PID 管理重构”替代：PID 文件目录不再作为生命周期状态源。

### 验证方法

1. 检查 `/tmp/claude-buddy-pids/` 的诊断镜像是否包含真正的 claude PID（如 69015）
2. 等待 10 秒以上，确认 claude 仍存活时 daemon 不会因为 "all PIDs dead" 退出
3. 关闭 terminal tab，确认 Claude PID 死亡后 daemon 在 grace 之后退出，BLE 断开

## 相关代码

- `hooks/spawn_daemon.py`: 启动 daemon，检查 daemon 存活
- `scripts/buddy_bridge.py`: daemon 主程序，处理 BLE 通信、sentinel 检测
- `hooks/hooks.json`: hook 配置，SessionStart 触发 spawn_daemon

## PID 管理重构（2026-05-15）

### 问题

早期实现由 `spawn_daemon.py` 直接 touch `/tmp/claude-buddy-pids/<pid>`，daemon 再扫描这个目录判断是否退出。
这会让目录同时承担“状态源”和“调试输出”两个职责，目录为空时无法区分：

1. daemon 刚启动，还没有任何 hook 注册 PID
2. 最后一个 Claude PID 已死亡，文件已被 daemon 清理

### 调整

1. `spawn_daemon.py` 只负责发现真实 Claude PID
2. daemon 已运行时，hook 通过 socket 发送 `{"kind":"register_pid","pid":...}`
3. daemon 未运行时，hook 启动 daemon 并传 `--initial-claude-pid`
4. `buddy_bridge.py` 内部维护 `tracked_pids`
5. `/tmp/claude-buddy-pids/` 只作为 daemon 写出的诊断镜像，不再作为生命周期状态源

### 退出判断

`process_sentinel()` 每秒检查 daemon 内部已注册的 PID：

- 有存活 PID：继续运行，并重置退出计时
- 曾经注册过 PID，但当前没有存活 PID：开始 grace 计时
- grace 超过 `--exit-on-idle`：daemon 退出，BLE 断开
