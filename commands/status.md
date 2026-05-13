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
python3 - <<'PY'
import json, socket, os
sock_path = "/tmp/claude-buddy-bridge.sock"
if not os.path.exists(sock_path):
    print("  bridge socket not found")
else:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(sock_path)
        s.sendall((json.dumps({"kind": "ble_status"}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk: break
            buf += chunk
        resp = json.loads(buf.decode()) if buf else {}
        ble = resp.get("ble_connected", None)
        pending = resp.get("pending", 0)
        if ble is None:
            print("  bridge not responding")
        elif ble:
            print(f"  BLE: connected ({pending} pending approval)")
        else:
            print("  BLE: not connected")
        s.close()
    except Exception as e:
        print(f"  bridge query failed: {e}")
PY
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
