#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
`prose_gate` runs this check ahead of the checkers. The check refuses an edit that leaves the hooks unable to run.
`an-edit-leaves-the-hooks-running` is the rule.

A hook imports modules of `generator/` and of `.claude/hooks/`. An edit to such a module may leave a name undefined at
import. It may leave a checker that raises. A later edit then crashes in `prose_gate`, and the edit that fixes the fault
crashes there too.

The check writes the edited file into a scratch directory. That directory sits ahead of the tree on the import path. A
child process imports `checkers`, and an import of the edited module finds the scratch copy. The child runs
`checkers.refusals_for_edit` over the edited file. A child that fails gives the refusal the tail of its standard error.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import os
import subprocess
import sys
import tempfile

import gate

# The directories of `gate.modules` and `gate.hook_modules`.
_IMPORTED_FROM = tuple(
    sorted({os.path.relpath(one.parent, gate.TREE) for one in (*gate.modules(), *gate.hook_modules())})
)

# The program the child runs. The command line names the scratch directory, the edited file and its path in the tree.
_PROGRAM = (
    "import sys\n"
    "scratch, edited, path = sys.argv[1:4]\n"
    "sys.path[:0] = [scratch]\n"
    "import checkers, collect_fragments\n"
    "checkers.refusals_for_edit(collect_fragments.whole(edited), path)\n"
)

# The tail of the child's error output the refusal quotes. A Python traceback ends on the exception, below the frame
# that raised it. https://docs.python.org/3/library/traceback.html shows that layout.
_MOST_QUOTED = 3000


def refusal_for(text: str, path: str) -> str | None:
    """
    The refusal for an edit that leaves `text` at `path` and stops the hooks, or None where the hooks still run.

    `path` names the file relative to the tree. A path outside `_IMPORTED_FROM` takes no reading.

    Public. `prose_gate` asks this ahead of the checkers.
    """
    if not path.endswith(".py") or os.path.dirname(path) not in _IMPORTED_FROM:
        return None
    hooks = os.path.join(gate.TREE, ".claude", "hooks")
    with tempfile.TemporaryDirectory() as scratch:
        edited = os.path.join(scratch, os.path.basename(path))
        with open(edited, "w", encoding="utf-8") as handle:
            handle.write(text)
        searched = os.pathsep.join([scratch, os.path.join(gate.TREE, "generator"), hooks])
        ran = subprocess.run(
            [sys.executable, "-c", _PROGRAM, scratch, edited, path],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": searched, "YEAST_TREE": gate.TREE},
            cwd=gate.TREE,
            check=False,
        )
    if ran.returncode == 0:
        return None
    return (
        f"This edit leaves the hooks unable to run. The hooks import {path}, and the edited file fails there:\n\n"
        f"{ran.stderr[-_MOST_QUOTED:]}\n\n"
        "Define a name before the edit that uses it. A new parameter takes a default before a caller passes it.\n\n"
        "Rule: an-edit-leaves-the-hooks-running."
    )
