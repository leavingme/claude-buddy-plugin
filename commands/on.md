---
description: Enable Buddy hardware approval for current project
allowed-tools: Bash
---

Re-enable Buddy hardware approval for the current project. Permission requests will be routed through the Buddy device for approval.

```bash
PROJECT_NAME=$(basename "$(pwd)")
MARKER="$HOME/.claude-buddy/disabled/$PROJECT_NAME"
if [ -f "$MARKER" ]; then
    rm "$MARKER"
    echo "Buddy enabled for project: $PROJECT_NAME"
else
    echo "Buddy was already enabled for project: $PROJECT_NAME"
fi
```

After running, confirm to the user whether Buddy was actually re-enabled or was already enabled.
