#!/usr/bin/env bash
# `.claude/settings.json` runs this as a PreToolUse hook on Bash. This refuses a shell command that writes a repo file.
#
# Edits must go through the Edit tool. An edit made that way shows as a reviewable diff. The hook passes a read with
# grep, awk or sed. It passes a redirect to `/tmp`, to `/dev/null` or to the job scratch directory. The hook refuses a
# redirect onto a top-level file whose extension marks a repo file.
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
# end. The hook refuses `open(path, "w", encoding="utf-8")`.
if printf '%s' "$command" | grep -Eq '\.write_text\(|\.write_bytes\(|\.writelines\('; then
    deny "This writes a file from the shell. Use the Edit tool."
fi
if printf '%s' "$command" | grep -Eq "open\([^)]*['\"][rwaxbt+]*[wax+][rwaxbt+]*['\"]"; then
    deny "This opens a file for writing from the shell. Use the Edit tool."
fi
if printf '%s' "$command" | grep -Eq '>[[:space:]]*\$\(|\btee\b|\bdd[[:space:]]+[^|]*\bof=|\btruncate\b'; then
    deny "This writes a file from the shell. Use the Edit tool."
fi

# A copy or a move landing in a source directory, or on a top-level file whose extension marks a repo file.
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(cp|mv|install|rsync)[[:space:]][^|;]*[[:space:]]"?\.?/?(generator|src|tests|include|scripts|grammar|cmake|ports|third_party|\.claude|\.github)/'; then
    deny "This copies or moves a file into a source directory. Use the Edit tool."
fi
if printf '%s' "$command" | grep -Eq '(^|[^[:alnum:]_])(cp|mv|install)[[:space:]][^|;]*[[:space:]]"?\.?/?[A-Za-z][A-Za-z0-9_.-]*\.(md|py|c|h|yaml|json|txt|sh|js)("|[[:space:]]|$)'; then
    deny "This copies or moves a file onto a repo file. Use the Edit tool."
fi

# The other checks of this hook read the command text. A script file hides its writes from that text. This check asks
# `written_by.py` about the script the command names. That helper follows the calls of the script into the modules of
# the tree.
#
# `declared` names a script allowed to write. A script that must write wants an entry in `declared`. The refusal asks
# for a comment above that entry saying why. `written_by.py` may find a write in a script `declared` leaves out. This
# check then refuses the command. This check also refuses a script `written_by.py` cannot read.
declared() {
    case "$1" in
    # `generator/record_run.py` writes a review run's proposals into the pending file.
    *generator/record_run.py) return 0 ;;
    # `generator/write_cited_names.py` writes the generated list of names a citation may name. `make regen` runs it, and
    # `check_documents` holds the file to the live pipeline.
    *generator/write_cited_names.py) return 0 ;;
    # `updated` in `generator/update_ledger_and_queue.py` writes the ledger and the settling queues. `LEDGER` is no
    # source file. The queue that `prose_queue_rewritten` writes under `PROSE_DIR` is no source file either. The script
    # writes the claim store through `claims_purged`. `updated` drops a suggested proposal the checkers refuse.
    *generator/update_ledger_and_queue.py) return 0 ;;
    # `generator/converge_prose.py` writes the settling queues and the ledger through `generator/settling_state.py`, and
    # writes the prose a comparator accepted through `apply_prose`. The `Makefile` puts the queues under `.git`.
    *generator/converge_prose.py) return 0 ;;
    # `generator/review_input.py` writes the files the pre-commit review reads. A review input is no source file.
    *generator/review_input.py) return 0 ;;
    # `generator/check_hooks.py` writes a probe file under a temporary directory. A pair fires against that file, and
    # the run deletes the directory, as https://docs.python.org/3/library/tempfile.html#tempfile.TemporaryDirectory
    # says.
    *generator/check_hooks.py) return 0 ;;
    # `.claude/hooks/prose_answer.py` writes the fragments an agent got right and the journal of the calls it decides.
    # Both live under `.git`.
    *.claude/hooks/prose_answer.py) return 0 ;;
    # `generator/claim_state.py` writes the claim store. The store names the fragments a claim pass read. The store is
    # no source file.
    *generator/claim_state.py) return 0 ;;
    # `generator/converge_claims.py` writes the claim store through `claims_written`. It writes the prose a claims
    # reader fixed through `apply_prose`. The `Makefile` hands `generator/converge_claims.py` the directory
    # `.git/critic`, and the run's log sits there.
    *generator/converge_claims.py) return 0 ;;
    # `.claude/mcp/fetch_fragments.py` writes the keys a claim call fetched, to the file the driver names. That record
    # is no source file.
    *.claude/mcp/fetch_fragments.py) return 0 ;;
    # writes the generated decoder tables in `src/decoder_tables.h`. The grammar is the source. The `regen` target of
    # `Makefile` runs `generator/grammar2decoder.py`, and that script regenerates the tables from the grammar.
    *generator/grammar2decoder.py) return 0 ;;
    # `generator/write_agent_prompts.py` writes the conventions into the agent definitions that judge prose.
    # `.claude/conventions.md` is the source `generator/write_agent_prompts.py` reads, and a hand-copied duplicate would
    # drift from it.
    *generator/write_agent_prompts.py) return 0 ;;
    # The tool this case declares, `generator/regen_fixture.py`, writes a fixture's expected output. The user runs the
    # tool by hand to record that output.
    *generator/regen_fixture.py) return 0 ;;
    # `scripts/coverage_badge.py` writes the coverage badge. The `Makefile` writes that badge into a build directory.
    *scripts/coverage_badge.py) return 0 ;;
    # `scripts/wrap_long_comments.py` wraps the `#` comments of a Python file, a shell script or the `Makefile` at the
    # column limit. `make reformat` runs it through `make reformat-py`, `make reformat-sh` and `make reformat-make`.
    *scripts/wrap_long_comments.py) return 0 ;;
    *) return 1 ;;
    esac
}

# Inline code hides its writes the same way. `written_by.py` reads the code of a `-c` and of a heredoc off the command.
if printf '%s' "$command" | grep -Eq 'python3?[[:space:]]+-(c[[:space:]]|[[:space:]])'; then
    status=0
    found=$(printf '%s' "$command" | python3 "$(dirname "${BASH_SOURCE[0]}")/written_by.py" --command 2>&1) || status=$?
    if [ "$status" -eq 0 ]; then
        deny "This hands Python code to the shell, and that code writes a file. The write: $found. Use the Edit tool."
    elif [ "$status" -ne 1 ]; then
        deny "The inline code of this command reads as no Python. The reading said: $found"
    fi
fi

for script in $(printf '%s' "$command" | grep -Eo 'python3?[[:space:]]+[^[:space:]"]+\.py' | sed -E 's/^python3?[[:space:]]+//' || true); do
    [ -f "$script" ] || continue
    if declared "$script"; then
        continue
    fi
    status=0
    found=$(python3 "$(dirname "${BASH_SOURCE[0]}")/written_by.py" "$script" 2>&1) || status=$?
    if [ "$status" -eq 0 ]; then
        deny "\`$script\` writes a file. The declaration list leaves the script out. The write: $found. Use the Edit tool. The user reads a change as a diff. A tool that must write gets a case in \`declared\` in this hook. A comment above that case says why."
    elif [ "$status" -ne 1 ]; then
        deny "\`written_by.py\` could not read \`$script\`: $found"
    fi
done

# A redirect into a source directory, or into a top-level repo file.
if printf '%s' "$command" | grep -Eq '>>?[[:space:]]*"?\.?/?(generator|src|tests|include|scripts|grammar|cmake|ports|third_party)/'; then
    deny "This redirects into a source directory. Use the Edit tool."
fi
if printf '%s' "$command" | grep -Eq '>>?[[:space:]]*"?\.?/?[A-Za-z][A-Za-z0-9_.-]*\.(md|py|c|h|yaml|json|txt)([[:space:]]|$)'; then
    deny "This redirects into a repo file. Use the Edit tool."
fi

exit 0
