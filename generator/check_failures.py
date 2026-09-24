# SPDX-License-Identifier: MIT
"""
Check `failures-are-not-ignored`.

A failure ignored produces output that looks like success.

The rule covers the code of the tree, and the shape a failure takes is no part of it. This gate reads the shapes below.
A shape this gate leaves alone breaks the rule the same way.

A surface has its own shape of the fault. A faulty shell script goes on past a failed command. A faulty `Makefile`
recipe swallows a failure. A faulty workflow reads an agent's result without asking whether the agent answered. Another
faulty workflow asks that only of an agent answering under a schema. Faulty Python suppresses an exception, or reads a
command's output without reading its status.

An agent may fail at a task. Such an agent reports a failure. That agent still answers under a schema. Such an answer is
present and empty. A guard asking only whether an answer arrived passes an empty answer through. Code after that guard
reads emptiness as work.

An `except` may put the caught exception into the run's report. Such an `except` takes `failure-is-reported:` on a line
of its own. That marker says where the failure comes out. A gate collecting divergences over a corpus wants the whole
set rather than the first crash. A crash is a divergence like any other.

An `except` whose exception is an ordinary answer takes `not-a-failure:` on a line of its own. That marker says what the
answer means. The decoder answers `(None, 1)` about a byte of a malformed UTF-8 sequence.

A marker sits on the line it excuses. A store keyed by `file:line` goes stale under an edit above the line. A stale key
reads as a site that stopped ignoring anything.

Usage: `python3 generator/check_failures.py`.
"""

import ast
import pathlib
import re

import gate

# The line a shell script starts with. `-e` stops on a failed command. `-u` refuses an unset name. `pipefail` gives a
# pipeline the status of the command that failed rather than the trailing status.
#
# `pipefail` is bash. POSIX `sh` has no such option and dash refuses it. A `#!/bin/sh` script uses `set -eu`.
_WANTED_SET = "set -euo pipefail"
_WANTED_SET_IN_SH = "set -eu"  # the rule a `#!/bin/sh` script follows instead.

# A recipe line `make` runs without stopping on its status, and the pair of forms that discard a status.
_SWALLOWED_IN_MAKE = re.compile(r"^\t-|\|\|\s*true\b|\|\|\s*:")

# An agent call whose result a name binds, and a call whose result goes unbound.
_BOUND_AGENT = re.compile(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*await\s+agent\s*\(")
_BARE_AGENT = re.compile(r"^\s*await\s+agent\s*\(", re.MULTILINE)  # the unbound call. Its answer goes unread.

# An expression in this set asks whether a bound answer holds anything. It reads the length, the keys or the values of
# the answer.
_ASKS_IF_EMPTY = r"Object\.(?:keys|values)\(\s*{name}\b|\b{name}\b[\w.\[\]]*\.length"

# A call writes this keyword to raise no error on a failure. `subprocess.run(check=False)` writes it.
_UNRAISED = "check"

# The markers on an `except` a reader has already ruled on. Both sit on the `except` line and state their reason.
# `failure-is-reported:` says where the failure comes out. `not-a-failure:` says what the exception answers instead.
_IS_EXCUSED = re.compile(r"#.*\b(?:failure-is-reported|not-a-failure):\s*(\S.*)$")


def _shell_faults() -> list[tuple[str, str]]:
    """`(site, what is wrong)` per shell script that does not stop on a failed command."""
    held = []
    for path in gate.shell_scripts():
        name = str(path.relative_to(gate.TREE))
        lines = path.read_text(encoding="utf-8").splitlines()
        wanted = _WANTED_SET if lines and lines[0].endswith("bash") else _WANTED_SET_IN_SH
        said = [line.strip() for line in lines if line.startswith("set ")]
        if wanted not in said:
            opened = said[0] if said else "(no set line)"
            held.append((name, f"{name} starts {opened!r}, and wants {wanted!r}"))
    return held


def _make_faults() -> list[tuple[str, str]]:
    """`(site, what is wrong)` per `Makefile` recipe that discards the status of its own command."""
    held = []
    for at, line in enumerate(pathlib.Path(gate.TREE, "Makefile").read_text(encoding="utf-8").splitlines(), start=1):
        if _SWALLOWED_IN_MAKE.search(line):
            held.append((f"Makefile:{at}", f"the Makefile line {at} discards the status of {line.strip()}"))
    return held


def _call_from(said: str, opened_at: int) -> str:
    """The text of the call whose `(` is at `opened_at`, up to the `)` that closes it."""
    depth = 0
    for at in range(opened_at, len(said)):
        if said[at] == "(":
            depth += 1
        elif said[at] == ")":
            depth -= 1
            if depth == 0:
                return said[opened_at : at + 1]
    return said[opened_at:]


def _workflow_faults() -> list[tuple[str, str]]:
    """`(site, what is wrong)` per workflow that reads an agent's result without asking whether the agent answered."""
    held = []
    for path in gate.workflows():
        name = str(path.relative_to(gate.TREE))
        said = path.read_text(encoding="utf-8")
        for bound in _BOUND_AGENT.finditer(said):
            bound_to = bound.group(1)
            at = said[: bound.start()].count("\n") + 1
            if not re.search(rf"if\s*\(\s*!\s*{re.escape(bound_to)}\b", said):
                held.append((f"{name}:{at}", f"{name}:{at} binds `{bound_to}` and nothing asks whether it answered"))
            asked_under_a_schema = "schema" in _call_from(said, bound.end() - 1)
            if asked_under_a_schema and not re.search(_ASKS_IF_EMPTY.format(name=re.escape(bound_to)), said):
                held.append(
                    (
                        f"{name}:{at}",
                        f"{name}:{at} binds `{bound_to}` under a schema and nothing asks whether it holds anything",
                    )
                )
        for bare in _BARE_AGENT.finditer(said):
            at = said[: bare.start()].count("\n") + 1
            held.append((f"{name}:{at}", f"{name}:{at} runs an agent and reads no result"))
    return held


def _does_swallow(handler: ast.ExceptHandler) -> bool:
    """
    Whether an `except` handler answers with nothing rather than raising.

    This reports such a handler unless its `except` line has a marker.
    """
    return not any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def _python_faults() -> list[tuple[str, str]]:
    """`(site, what is wrong)` per site where Python suppresses an exception or reads output without a status."""
    held = []
    for path in gate.modules() + gate.hook_modules():
        name = str(path.relative_to(gate.TREE))
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.split("\n")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not _does_swallow(node):
                continue
            if _IS_EXCUSED.search(lines[node.lineno - 1]):
                continue
            caught = ast.unparse(node.type) if node.type else "everything"
            at = f"{name}:{node.lineno}"
            held.append(
                (
                    at,
                    f"{at} catches {caught}, does not raise, and has no `failure-is-reported:` or `not-a-failure:`",
                )
            )
        for holder in ast.walk(tree):
            if not isinstance(holder, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            reads_status = "returncode" in ast.dump(holder)
            for call in ast.walk(holder):
                if not isinstance(call, ast.Call) or reads_status:
                    continue
                if any(
                    word.arg == _UNRAISED and isinstance(word.value, ast.Constant) and word.value.value is False
                    for word in call.keywords
                ):
                    at = f"{name}:{call.lineno}"
                    held.append((at, f"{at} runs a command that will not raise. nothing reads its status."))
    return held


def _check() -> None:
    """Report the ignored failures, over the languages a recipe or a hook can swallow a failure in."""
    found = _shell_faults() + _make_faults() + _workflow_faults() + _python_faults()
    gate.report(
        [said for _site, said in found],
        "ignored failure(s). Read the status, or raise, or ask whether the answer holds anything. Or mark the handler "
        "`failure-is-reported:` with where the failure comes out. Or mark the handler `not-a-failure:` with what it "
        "answers instead.",
        "failures: a handler reports its failure, bar a handler a marker excuses",
    )


def main() -> None:
    _check()


if __name__ == "__main__":
    main()
