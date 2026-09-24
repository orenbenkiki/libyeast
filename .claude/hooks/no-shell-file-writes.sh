#!/usr/bin/env bash
# PreToolUse on Bash. This refuses a shell command that writes a repo file.
#
# Edits must go through the Edit tool. An edit made that way shows as a reviewable diff. The hook passes a read with
# grep, awk or sed. It passes a redirect to `/tmp`, to `/dev/null` or to the job scratch directory. It also passes a
# path named *junk*.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/refusal.sh"

command=$(jq -r '.tool_input.command // ""')

# The hook refuses an in-place rewriter at any path.
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(sed|perl|ruby)[[:space:]]+-[[:alnum:]]*i'; then
    deny "an in-place rewriter changes a file behind the user. Use the Edit tool - the user reviews changes as diffs."
fi
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(patch|git[[:space:]]+apply)([[:space:]]|$)'; then
    deny "This applies a patch to the tree. Use the Edit tool."
fi

# The Python forms of a write. This looks for the mode of an `open` anywhere among the arguments, rather than at the
# end.
# `open(path, "w", encoding="utf-8")` went through a test that wanted the mode last, and wrote a source file.
if printf '%s' "$command" | grep -Eq '\.write_text\(|\.write_bytes\(|\.writelines\('; then
    deny "This writes a file from the shell. Use the Edit tool."
fi
if printf '%s' "$command" | grep -Eq "open\([^)]*['\"][rwaxbt+]*[wax+][rwaxbt+]*['\"]"; then
    deny "This opens a file for writing from the shell. Use the Edit tool."
fi
if printf '%s' "$command" | grep -Eq '>[[:space:]]*\$\(|\btee\b|\bdd[[:space:]]+[^|]*\bof=|\btruncate\b'; then
    deny "This writes a file from the shell. Use the Edit tool."
fi

# A copy or a move landing in a source directory, or on a top-level document.
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(cp|mv|install|rsync)[[:space:]][^|;]*[[:space:]]"?\.?/?(generator|src|tests|include|scripts|grammar|cmake|ports|third_party|\.claude|\.github)/'; then
    deny "This copies or moves a file into a source directory. Use the Edit tool."
fi
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(cp|mv|install)[[:space:]][^|;]*[[:space:]]"?\.?/?[A-Za-z][A-Za-z0-9_.-]*\.(md|py|c|h|yaml|json|txt|sh|js)("|[[:space:]]|$)'; then
    deny "This copies or moves a file onto a repo file. Use the Edit tool."
fi

# The other checks of this hook read the command text. A script file hides its writes from that text. This check reads the script the command names. The check looks there for the same write forms.
#
# `declared` names a script allowed to write. A comment above an entry of `declared` says why that script may write. This check refuses a script `declared`
# leaves out.
declared() {
    case "$1" in
    # writes a fragment's new prose back through the lines its site names. The user approved this tool by name.
    *generator/apply_prose.py) return 0 ;;
    # writes the batch files and the workflow script the linguistic pass reads. Neither is a source file.
    *generator/batch_pending_fragments.py) return 0 ;;
    # writes a run's proposals into the pending file and moves the keys the readers passed. The user approved this tool
    # by name.
    *generator/record_run.py) return 0 ;;
    # writes the generated list of names a citation may name. `make regen` runs it, and `check_documents` holds the
    # file to the live pipeline.
    *generator/write_cited_names.py) return 0 ;;
    # writes the ledger and the settling queues. Neither is a source file.
    *generator/update_ledger_and_queue.py) return 0 ;;
    # writes the settling queues and the ledger, and writes the prose a comparator accepted through `apply_prose`. The
    # queues sit under `.git`.
    *generator/converge_prose.py) return 0 ;;
    # writes the files the pre-commit review reads. A review input is no source file.
    *generator/review_input.py) return 0 ;;
    # writes a probe file under a temporary directory. A pair fires against that file, and the run deletes the directory.
    *generator/check_hooks.py) return 0 ;;
    # writes what an agent got right, and the fragments an agent found no fix for. Both live under `.git`.
    *.claude/hooks/prose_answer.py) return 0 ;;
    # writes the generated decoder tables. The grammar is the source, and `make` regenerates the tables from it.
    *generator/grammar2decoder.py) return 0 ;;
    # writes the conventions into the agent definitions that judge prose. `.claude/conventions.md` is the source, and a
    # hand-copied duplicate would drift from it. The user approved this tool by name.
    *generator/write_agent_prompts.py) return 0 ;;
    # A tool this case declares writes a fixture's expected output. The user runs the tool by hand to record that output.
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
        deny "\`$script\` opens a file for writing, and the declaration list leaves it out. Use the Edit tool. The user reads a change as a diff. A tool that must write gets a case in \`declared\` in this hook. A comment above that case says why."
    fi
done

# A redirect into a source directory, or into a top-level document.
if printf '%s' "$command" | grep -Eq '>>?[[:space:]]*"?\.?/?(generator|src|tests|include|scripts|grammar|cmake|ports|third_party)/'; then
    deny "This redirects into a source directory. Use the Edit tool."
fi
if printf '%s' "$command" | grep -Eq '>>?[[:space:]]*"?\.?/?[A-Za-z][A-Za-z0-9_.-]*\.(md|py|c|h|yaml|json|txt)([[:space:]]|$)'; then
    deny "This redirects into a repo file. Use the Edit tool."
fi

exit 0
