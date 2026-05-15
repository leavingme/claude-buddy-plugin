# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Claude Code plugin that bridges permission requests and session state to "Buddy" hardware (xiaozhi ESP32 or compatible devices) via BLE. It lets you approve/deny Claude Code tool operations using physical hardware buttons.

## Architecture

```
Claude Code hooks → Unix socket → Bridge daemon (buddy_bridge.py) → BLE (Nordic UART Service) → Buddy device
```

**Key components:**
- `scripts/buddy_bridge.py` — Long-running asyncio daemon. Scans/connects to BLE device, manages session state, pushes snapshots to device at 1Hz, receives permission decisions back.
- `hooks/hook_permission_request.py` — **Blocking** hook for PermissionRequest events. Sends request to daemon via socket, waits up to 540s for device decision, returns `allow/deny/ask` to Claude Code.
- `hooks/hook_event.py` — **Non-blocking** hook for PostToolUse, Stop, UserPromptSubmit, Notification, SessionStart events. Fires and forgets to daemon.
- `hooks/spawn_daemon.py` — Ensures daemon is running. Called on SessionStart (startup/resume/clear matcher) to lazy-spawn the bridge.
- `hooks/hook_sessionend.py` — Notifies the daemon about SessionEnd and removes the legacy session marker if present.
- `scripts/status.py`, `scripts/screenshot.py`, `scripts/test.py` — CLI clients that query the daemon via Unix socket.

**BLE protocol:** Nordic UART Service (NUS) — service `6e400001-...`, RX char `6e400002-...` (host→device), TX char `6e400003-...` (device→host).

**Daemon lifecycle:** `spawn_daemon.py` finds the real Claude Code PID by walking the PPID chain and registers it with `buddy_bridge.py`. The daemon owns the tracked PID set, mirrors it to `/tmp/claude-buddy-pids/<pid>` for diagnostics, and exits after all registered PIDs are gone for `--exit-on-idle` seconds (default 5).

## Daemon Exit Mechanism

**Goal:** Keep one bridge daemon shared across Claude Code windows, but disconnect BLE after the last tracked Claude Code process exits.

**Current implementation (2026-05-15):**

1. `spawn_daemon.py` is invoked on SessionStart and UserPromptSubmit.
2. The hook process has a short-lived intermediate parent, so `os.getppid()` is not reliable.
3. `find_claude_pid_via_ppid_chain()` walks the PPID chain and sends the real `claude` PID to the daemon as `register_pid`, or passes it as `--initial-claude-pid` when starting a new daemon.
4. `buddy_bridge.py` runs `process_sentinel()`, removes dead PIDs from its internal set and diagnostic mirror, and exits when no registered Claude Code PID remains alive for the configured grace period.
5. On daemon shutdown, the BLE client disconnects so the Buddy device leaves the Claude panel.

**Observed hook process tree:**
```
claude
└── short-lived hook runner
    └── spawn_daemon.py
```

**Key files:**
- `hooks/spawn_daemon.py` — finds and registers the owning Claude Code PID, starts the daemon if needed
- `scripts/buddy_bridge.py` — daemon, BLE bridge, and PID sentinel
- `docs/daemon-lifecycle.md` — detailed lifecycle analysis document

**Per-project disable:** `~/.claude-buddy/disabled/<project_name>` marker file causes `hook_permission_request` to fall back to `ask` immediately, bypassing Buddy.

## Running

**No build step.** Uses PEP 723 inline script metadata — `uv run <script>` auto-installs `bleak>=0.21` into an isolated venv.

```bash
# Run the daemon directly
uv run --quiet scripts/buddy_bridge.py --device-prefix 'Claude-' --owner leavingme -v

# Test the permission path
python3 scripts/test.py Bash "/buddy:test"

# Check BLE/status
python3 scripts/status.py

# Screenshot from device
python3 scripts/screenshot.py -o /tmp/buddy_screenshot.jpg
```

## Logs

- Bridge daemon: `/tmp/claude-buddy-bridge.log`
- Spawn diagnostics: `/tmp/claude-buddy-spawn.log`

## Plugin Commands

- `/claude-buddy:on` — Re-enable Buddy for current project
- `/claude-buddy:off` — Disable Buddy for current project (uses default Claude approval)
- `/claude-buddy:status` — Show daemon + BLE + project toggle status
- `/claude-buddy:restart` — Kill daemon (auto-respawned on next session)
- `/claude-buddy:screenshot` — Capture device screen to `/tmp/buddy_screenshot.jpg`
- `/claude-buddy:test` — Inject fake permission request to test BLE path
