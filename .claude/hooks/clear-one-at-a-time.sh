#!/usr/bin/env bash
# UserPromptSubmit. This deletes the markers `one-at-a-time.sh` writes.
#
# A prompt from the user clears a marker. A turn may then put something in front of the user again, and it may run a
# tool again after a refusal.
set -euo pipefail

root=${CLAUDE_PROJECT_DIR:-.}
rm -f "$root/.git/one-at-a-time" "$root/.git/floor-is-yours"
exit 0
