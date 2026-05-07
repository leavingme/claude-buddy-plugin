---
description: Take a screenshot of the xiaozhi buddy device display and save as JPEG.
allowed-tools: Bash
---

Take a screenshot from the buddy device via the bridge daemon's unix socket.

Run:

```bash
python3 -c "
import socket, json, base64, time, sys

SOCK_PATH = '/tmp/claude-buddy-bridge.sock'

try:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(15)
    sock.connect(SOCK_PATH)
    sock.sendall((json.dumps({'kind': 'screenshot'}) + '\n').encode())
    
    # Read response
    buf = b''
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
        if b'\n' in buf:
            break
    sock.close()
    
    line = buf.split(b'\n')[0]
    resp = json.loads(line)
    if resp.get('ok'):
        jpg = base64.b64decode(resp['data'])
        path = '/tmp/buddy_screenshot.jpg'
        with open(path, 'wb') as f:
            f.write(jpg)
        print(f'Screenshot saved: {path} ({len(jpg)} bytes)')
    else:
        print(f'Error: {resp.get(\"error\", \"unknown\")}')
except Exception as e:
    print(f'Failed: {e}')
"
```

Then show the screenshot to the user using the Read tool on `/tmp/buddy_screenshot.jpg`.
