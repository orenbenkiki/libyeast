#!/usr/bin/env -S -u BASH_ENV bash
# The command `.claude/settings.json` registers for a hook. The arguments name the hook this runs.
#
# A hook that dies turns the tool call back. https://code.claude.com/docs/en/hooks says how the tool reads the status
# and the output of a hook. Without this script, a dead hook writes nothing and leaves with a status the tool ignores.
#
# A hook that succeeds decides the call. The tool reads what that hook wrote. Silence is how a hook passes.
#
# A hook that fails gets the status the tool reads as a refusal, as https://code.claude.com/docs/en/hooks says. This
# script writes an error message. https://code.claude.com/docs/en/hooks says the tool hands that message to the model,
# and the model then answers again. A failure before this script runs the hook takes that status too.
#
# A non-interactive bash sources the startup file `BASH_ENV` names, as
# https://www.gnu.org/software/bash/manual/html_node/Bash-Startup-Files.html says. A startup file may reorder where a
# command gets looked up. The first line therefore starts bash with `BASH_ENV` unset.
set -euo pipefail

if [ "$#" -eq 0 ]; then
    echo "usage: run.sh <command> [argument ...]" >&2
    exit 2
fi

payload=$(mktemp) || exit 2
said=$(mktemp) || exit 2
aside=$(mktemp) || exit 2
trap 'rm -f "$payload" "$said" "$aside"' EXIT

cat >"$payload" || exit 2

# The hook's status goes into a variable rather than ending this script. The branch below reads it.
status=0
env "$@" <"$payload" >"$said" 2>"$aside" || status=$?

if [ "$status" -ne 0 ]; then
    printf 'the hook %s could not check this call. it left with status %s.\n\n%s\n' "$*" "$status" "$(cat "$aside")" >&2
    exit 2
fi

cat "$said"
cat "$aside" >&2
exit 0
