#!/usr/bin/env bash
# PreToolUse on Bash. This refuses a shell command that writes a repo file.
#
# Edits must go through the Edit tool so they show as a reviewable diff. Reading with grep/awk/sed is fine. Writing is
# not. This passes a redirect to /tmp, to /dev/null or to the job scratch directory. This passes anything named *junk*
# too.
set -euo pipefail

command=$(jq -r '.tool_input.command // ""')

deny() {
    jq -nc --arg why "$1" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $why
        }
    }'
    exit 0
}

# In-place rewriters, wherever they point.
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(sed|perl|ruby)[[:space:]]+-[[:alnum:]]*i'; then
    deny "sed/perl -i rewrites a file in place. Use the Edit tool - the user reviews changes as diffs (memory \`edit-files-with-edit-tool-never-scripts\`)."
fi
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(patch|git[[:space:]]+apply)([[:space:]]|$)'; then
    deny "This applies a patch to the tree. Use the Edit tool (memory \`edit-files-with-edit-tool-never-scripts\`)."
fi

# The Python forms of a write. This looks for the mode of an `open` anywhere among the arguments, rather than at the
# end.
# `open(path, "w", encoding="utf-8")` went through a test that wanted the mode last, and wrote a source file.
if printf '%s' "$command" | grep -Eq '\.write_text\(|\.write_bytes\(|\.writelines\('; then
    deny "This writes a file from the shell. Use the Edit tool (memory \`edit-files-with-edit-tool-never-scripts\`)."
fi
if printf '%s' "$command" | grep -Eq "open\([^)]*['\"][rwaxbt+]*[wax+][rwaxbt+]*['\"]"; then
    deny "This opens a file for writing from the shell. Use the Edit tool (memory \`edit-files-with-edit-tool-never-scripts\`)."
fi
if printf '%s' "$command" | grep -Eq '>[[:space:]]*\$\(|\btee\b|\bdd[[:space:]]+[^|]*\bof=|\btruncate\b'; then
    deny "This writes a file from the shell. Use the Edit tool (memory \`edit-files-with-edit-tool-never-scripts\`)."
fi

# A copy or a move landing in a source directory, or on a top-level document.
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(cp|mv|install|rsync)[[:space:]][^|;]*[[:space:]]"?\.?/?(generator|src|tests|include|scripts|grammar|cmake|ports|third_party|\.claude|\.github)/'; then
    deny "This copies or moves a file into a source directory. Use the Edit tool (memory \`edit-files-with-edit-tool-never-scripts\`)."
fi
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(cp|mv|install)[[:space:]][^|;]*[[:space:]]"?\.?/?[A-Za-z][A-Za-z0-9_.-]*\.(md|py|c|h|yaml|json|txt|sh|js)("|[[:space:]]|$)'; then
    deny "This copies or moves a file onto a repo file. Use the Edit tool (memory \`edit-files-with-edit-tool-never-scripts\`)."
fi

# A script that writes. The checks above read the command text. A command naming a script file says nothing about what
# the script does. `python3 tool.py` rewrote the prose of the tree, and the checks above passed it. So this reads the
# script named on the command line. It looks for the same write forms in that script.
#
# `declared` names a script allowed to write. A comment above the case says why. This refuses a script the case list
# leaves out.
declared() {
    case "$1" in
    # writes a fragment's new prose back through the lines its site names. The user approved this tool by name.
    *generator/apply_prose.py) return 0 ;;
    # writes the batch files and the workflow script the linguistic pass reads. Neither is a source file.
    *generator/batch_pending_fragments.py) return 0 ;;
    *generator/converge_fragments.py) return 0 ;;
    # writes the ledger and the queue the linguistic pass reads. Neither is a source file.
    *generator/update_ledger_and_queue.py) return 0 ;;
    *generator/pass_examined_fragments.py) return 0 ;;
    # writes the files the pre-commit review reads. A review input is no source file.
    *generator/review_input.py) return 0 ;;
    # writes the probe file a pair fires against, under a temporary directory the run deletes.
    *generator/check_hooks.py) return 0 ;;
    # writes what an agent got right, and the fragments an agent found no fix for. Both live under `.git`.
    *.claude/hooks/prose_answer.py) return 0 ;;
    # writes the generated decoder tables. The grammar is the source, and `make` regenerates the tables from it.
    *generator/grammar2decoder.py) return 0 ;;
    # writes a fixture's expected output. The user runs this by hand to record it.
    *generator/regen_fixture.py) return 0 ;;
    # writes the coverage badge. That badge is a build artifact.
    *scripts/coverage_badge.py) return 0 ;;
    # wraps the comments of a source file at the column limit. `make reformat` runs it.
    *scripts/wrap_long_comments.py) return 0 ;;
    *) return 1 ;;
    esac
}

for script in $(printf '%s' "$command" | grep -Eo 'python3?[[:space:]]+[^[:space:]"]+\.py' | sed -E 's/^python3?[[:space:]]+//' || true); do
    [ -f "$script" ] || continue
    if declared "$script"; then
        continue
    fi
    if grep -Eq "open\([^)]*['\"][rwaxbt+]*[wax+][rwaxbt+]*['\"]|\.write_text\(|\.write_bytes\(|\.writelines\(" "$script"; then
        deny "\`$script\` opens a file for writing, and the declaration list leaves it out. Use the Edit tool. The user reads a change as a diff. A tool that must write gets a case in \`declared\` in this hook. A comment above that case says why. Memory \`edit-files-with-edit-tool-never-scripts\`."
    fi
done

# A redirect into a source directory, or into a top-level document.
if printf '%s' "$command" | grep -Eq '>>?[[:space:]]*"?\.?/?(generator|src|tests|include|scripts|grammar|cmake|ports|third_party)/'; then
    deny "This redirects into a source directory. Use the Edit tool (memory \`edit-files-with-edit-tool-never-scripts\`)."
fi
if printf '%s' "$command" | grep -Eq '>>?[[:space:]]*"?\.?/?[A-Za-z][A-Za-z0-9_.-]*\.(md|py|c|h|yaml|json|txt)([[:space:]]|$)'; then
    deny "This redirects into a repo file. Use the Edit tool (memory \`edit-files-with-edit-tool-never-scripts\`)."
fi

exit 0
