---
description: Show xiaozhi buddy bridge status (daemon + BLE connection).
allowed-tools: Bash
---

Check whether the claude-buddy bridge daemon is running and whether it's connected to the xiaozhi hardware over BLE.

Run this shell command and report the results to the user in a concise table:

```bash
echo "=== daemon socket ==="
ls -la /tmp/claude-buddy-bridge.sock 2>&1 | head -1
echo ""
echo "=== daemon process ==="
pgrep -af "buddy_bridge.py" || echo "  (no daemon running)"
echo ""
echo "=== last 20 lines of daemon log (if running via monitor) ==="
# Monitor stdout goes to Claude as notifications; no persistent log file.
# Use /buddy:logs if you need recent output.
```

After running, tell the user:
- Whether the daemon is up (socket exists + process running)
- Whether a device appears paired (look for "BLE connected" in output)
- If something looks wrong, suggest `/claude-buddy:restart`
