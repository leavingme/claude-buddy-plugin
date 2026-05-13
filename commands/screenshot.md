---
description: Take a screenshot of the xiaozhi buddy device display and save as JPEG.
allowed-tools: Bash
---

Take a screenshot from the buddy device via the bridge daemon's unix socket.

Run:

```bash
python3 scripts/screenshot.py
```

Then show the screenshot to the user using the Read tool on `/tmp/buddy_screenshot.jpg`.
