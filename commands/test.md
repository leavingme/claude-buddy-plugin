---
description: Send a fake permission request to the xiaozhi hardware (to test BLE approval path without running a real tool). Uses PermissionRequest hook.
allowed-tools: Bash
argument-hint: "[tool-name] [hint-text]"
---

Inject a fake PermissionRequest into the bridge so the xiaozhi hardware shows an ATTENTION panel. Useful for verifying the BLE link without waiting for Claude to actually try a risky tool.

Default: tool=Bash, hint="/buddy:test manual check"

Run this script (synthesize a `permission_request` event and wait for the device's decision):

```bash
python3 scripts/test.py "$1" "$2"
```

After running, report whether the xiaozhi showed the ATTENTION panel and what decision was returned (allow / deny / ask / timeout).
