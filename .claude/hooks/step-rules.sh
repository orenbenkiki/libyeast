#!/usr/bin/env bash
# PreToolUse on Edit and Write. A new pipeline step states the argument that answers the rules written above `_Step`.
#
# Reads only the text the edit introduces. The steps already in the tree do not fire on an edit elsewhere.
#
# A script cannot decide whether a step is right. Whether the step does a single thing, and whether the
# correctness argument is local. Whether a value the step produces is a global or a stack entry. A hook can refuse the
# step until the author writes the argument down beside the step. The written argument is then what the review reads.
#
# `_Step` itself is gated already. `invariant_faults` holds its declarations to what the counts say happened, and
# `untested_steps` counts a step promising something nothing checks. This covers what those cannot see.
set -euo pipefail

payload=$(cat)
path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')
case "$path" in
*generator/normalize.py) ;;
*) exit 0 ;;
esac

# The text this edit adds. An Edit's replacement, or a Write's whole content.
added=$(printf '%s' "$payload" | jq -r '.tool_input.new_string // .tool_input.content // ""')
# An edit rewriting the prose around a step hands the steps back with it. Those are no new step. The count has to grow.
replaced=$(printf '%s' "$payload" | jq -r '.tool_input.old_string // ""')
was=$(printf '%s' "$replaced" | grep -c '_Step(' || true)
now=$(printf '%s' "$added" | grep -c '_Step(' || true)
if [ "$now" -le "$was" ]; then
    exit 0
fi
# The step's prose declares the argument, in the words the block below asks for.
if printf '%s' "$added" | grep -q 'step rules answered'; then
    exit 0
fi

jq -nc '{
    decision: "block",
    reason: "This edit adds a pipeline step. Read the rules written above `_Step` in `generator/normalize.py`, then write the step`s argument beside it and open that argument with `step rules answered`.\n\nThe argument says three things. Which invariant the step establishes, settles or reduces, or why it names none. That the transform reads its own production`s nodes plus the named grammar-wide tables, and leans on no global property of the parse. That a value it produces is a global singleton or an entry in the unified stack, there being no third place.\n\nA step doing two things is split before it is written. A step that would name the productions it applies to is the wrong step, and the question is which universal rule is missing.\n\nRule: a-step-carries-its-argument."
}'
