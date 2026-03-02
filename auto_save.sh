#!/bin/zsh
# Auto-save git backup script
# Usage: ./auto_save.sh "optional commit message"

cd /Users/FIRMAS/.openclaw

# Get commit message or use default
if [ -n "$1" ]; then
    msg="$1"
else
    msg="Auto-save: $(date '+%Y-%m-%d %H:%M:%S')"
fi

# Add all changes
git add -A

# Check if there are changes
if git diff --cached --quiet; then
    echo "No changes to commit"
    exit 0
fi

# Commit with timestamp
git commit -m "$msg"

echo "Committed: $msg"

# Optional: Push to remote (uncomment if you have a remote)
# git push origin main
