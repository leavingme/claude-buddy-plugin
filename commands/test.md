---
description: Send a fake permission request to the xiaozhi hardware (to test BLE approval path without running a real tool). Uses PermissionRequest hook.
allowed-tools: Bash
argument-hint: "[tool-name] [hint-text]"
---

Inject a fake PermissionRequest into the bridge so the xiaozhi hardware shows an ATTENTION panel. Useful for verifying the BLE link without waiting for Claude to actually try a risky tool.

Default: tool=Bash, hint="/buddy:test manual check"

Run this script (synthesize a `permission_request` event and wait for the device's decision):

```bash
python3 - <<'PY'
import json, socket, sys, uuid
sock = "/tmp/claude-buddy-bridge.sock"
tool = "$1" if "$1" else "Bash"
hint = "$2" if "$2" else "/buddy:test manual check"
req = {
    "kind": "permission_request",
    "session_id": "cmd-test",
    "req_id": f"test-{uuid.uuid4().hex[:8]}",
    "tool_name": tool,
    "tool_input": {"command": hint} if tool == "Bash" else {"hint": hint},
    "cwd": "/tmp",
}
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(60)
    s.connect(sock)
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(4096)
        if not chunk: break
        buf += chunk
    resp = json.loads(buf.split(b"\n",1)[0].decode())
    print(f"→ decision from device: {resp.get('decision')}")
except FileNotFoundError:
    print("✗ bridge daemon not running (no socket). Open a new session or /reload-plugins.")
    sys.exit(1)
except Exception as e:
    print(f"✗ error: {e}")
    sys.exit(1)
PY
```

After running, report whether the xiaozhi showed the ATTENTION panel and what decision was returned (allow / deny / ask / timeout).
