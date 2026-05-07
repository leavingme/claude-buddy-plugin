---
description: Restart the xiaozhi buddy bridge daemon.
allowed-tools: Bash
---

Restart the bridge daemon by killing any running instance. The plugin's monitor will auto-respawn it on the next session, but for this session you may need to `/reload-plugins` after killing.

Run:

```bash
pgrep -f "buddy_bridge.py" | xargs -r kill -TERM 2>/dev/null
sleep 1
if pgrep -f "buddy_bridge.py" > /dev/null; then
  pgrep -f "buddy_bridge.py" | xargs -r kill -KILL
fi
rm -f /tmp/claude-buddy-bridge.sock
echo "daemon killed; run /reload-plugins to respawn, or wait for next session"
```

After running, tell the user to run `/reload-plugins` (if in current session) to respawn the daemon via the plugin monitor.
