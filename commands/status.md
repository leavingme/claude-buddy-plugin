---
description: Show buddy bridge status (daemon + BLE connection + project toggle).
allowed-tools: Bash
argument-hint: ""
---

Check whether the claude-buddy bridge daemon is running, whether it's connected to the buddy hardware over BLE, and whether Buddy is enabled for the current project.

```bash
PROJECT_NAME=$(basename "$(pwd)")
MARKER_DIR="$HOME/.claude-buddy/disabled"
MARKER="$MARKER_DIR/$PROJECT_NAME"

echo "=== daemon socket ==="
ls -la /tmp/claude-buddy-bridge.sock 2>&1 | head -1
echo ""
echo "=== daemon process ==="
pgrep -af "buddy_bridge.py" || echo "  (no daemon running)"
echo ""
echo "=== BLE status from bridge ==="
python3 scripts/status.py
echo ""
echo "=== Buddy enabled for this project? ==="
if [ -f "$MARKER" ]; then
    echo "  DISABLED (using Claude default approval)"
else
    echo "  ENABLED (routing through Buddy hardware)"
fi
```

After running, tell the user:
- Whether the daemon is up (socket exists + process running)
- Whether BLE is connected
- Whether Buddy is enabled or disabled for the current project
- If Buddy is disabled, mention they can run `/claude-buddy:on` to re-enable
- If something looks wrong, suggest `/claude-buddy:restart`
