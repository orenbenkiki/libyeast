#!/bin/sh
# Verify the given tools are available on PATH. Prints one line per tool and exits non-zero (with a count) if any are
# missing. Usage: check-deps.sh <tool-or-path>...
set -u

missing=0
for tool in "$@"; do
    if command -v "$tool" >/dev/null 2>&1; then
        printf '  ok       %s\n' "$tool"
    else
        printf '  MISSING  %s\n' "$tool"
        missing=$((missing + 1))
    fi
done

if [ "$missing" -gt 0 ]; then
    printf '%s: %d dependency(ies) missing\n' "$0" "$missing" >&2
    exit 1
fi
