# SPDX-License-Identifier: MIT
"""
Check that no background agent asked for a tool the hook withheld.

`.claude/hooks/agent-tools.sh` decides an agent's tool call. The hook allows the tools an agent's work needs. It refuses
a call outside that list. A refusal raises no prompt. The user reads nothing at the time.

An agent handed a refusal may answer anyway. Such an answer leaves the refusal out. An agent that runs out of output
dies without answering. The refusal goes unread either way. So the hook appends a line to `.git/agent-tool-misses`, and
this reads that file.

A line here says the hook withheld something an agent wanted. Either the agent wandered, or the hook's list is missing a
tool the work needs. Read the line, decide which, and empty the file.

**Usage:** `python3 generator/check_agent_tools.py`.
"""

import os

import gate

_MISSES = os.path.join(gate.TREE, ".git", "agent-tool-misses")  # the file the hook appends a refusal to.

# The file `prose_answer` appends a fragment an agent found no fix for to.
_UNFIXED = os.path.join(gate.TREE, ".git", "agent-unfixed")


def _lines_of(path: str) -> list[str]:
    """The lines of `path`. A path no file is at gives nothing."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [line for line in handle.read().split("\n") if line.strip()]


def _check() -> None:
    """Report the tools the hook withheld from an agent, and the fragments an agent found no fix for."""
    faults = []
    for line in _lines_of(_MISSES):
        agent, tool, path = (line.split("\t") + ["", ""])[:3]
        where = f" on `{path}`" if path else ""
        faults.append(f"agent `{agent}` asked for `{tool}`{where}, and `agent-tools.sh` withheld it")
    for line in _lines_of(_UNFIXED):
        agent, key, why = (line.split("\t") + ["", ""])[:3]
        faults.append(f"agent `{agent}` found no fix for `{key}`: {why}")
    gate.report(
        faults,
        f"answer(s) an agent owes. Name a withheld tool in `agent-tools.sh` where the work needs it. Say a fragment "
        f"again by hand where an agent found no fix. Then empty `{_MISSES}` and `{_UNFIXED}`",
        "agent answers: an agent asked for nothing the hook withheld, and left no fragment unfixed",
    )


def main() -> None:
    _check()


if __name__ == "__main__":
    main()
