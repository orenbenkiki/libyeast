#!/bin/sh
# Verify the given tools are available. Each argument is either a command name (checked on PATH) or `python3:MODULE`
# (checked by importing MODULE). Prints one line per entry and exits non-zero (with a count) if any are missing.
# Usage: check-deps.sh <tool-or-path-or-python3:module>...
set -u

missing=0
for tool in "$@"; do
    case "$tool" in
    python3:*)
        if python3 -c "import ${tool#python3:}" >/dev/null 2>&1; then
            present=yes
        else
            present=no
        fi
        ;;
    *)
        if command -v "$tool" >/dev/null 2>&1; then
            present=yes
        else
            present=no
        fi
        ;;
    esac
    if [ "$present" = yes ]; then
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
