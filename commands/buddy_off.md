---
description: Disable Buddy hardware approval for the current project. When disabled, permission requests fall back to Claude's default approval dialog instead of going through the Buddy device.
allowed-tools: Bash
---

Disable Buddy approval for the current project. Claude will use its default permission dialog instead of routing requests through the Buddy hardware.

This only affects the current project (identified by the current working directory). Other projects remain unaffected.

```bash
PROJECT_NAME=$(basename "$(pwd)")
MARKER_DIR="$HOME/.claude-buddy/disabled"
mkdir -p "$MARKER_DIR"
touch "$MARKER_DIR/$PROJECT_NAME"
echo "Buddy disabled for project: $PROJECT_NAME"
```

After running, confirm to the user that Buddy has been disabled for this project.
