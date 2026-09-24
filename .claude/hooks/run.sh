#!/bin/sh
# The command `.claude/settings.json` registers for a hook. The arguments name the hook this runs.
#
# A hook that dies turns the tool call back. A dead hook otherwise writes nothing and leaves with a status the tool
# ignores. That reads exactly like a hook that passed a clean call.
#
# A hook that succeeds decides the call. The tool reads what that hook wrote. Silence is how a hook passes.
#
# A hook that fails gets the status the tool reads as a refusal. The tool hands the text below to the model. The model
# then says the answer again. A failure inside this script takes that status too.
#
# This runs under sh. A non-interactive bash sources a startup file here, and that file reorders where a command gets
# looked up. A Python hook under bash then gets an interpreter that lacks the packages the hook imports.
set -eu

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
