# SPDX-License-Identifier: MIT
"""
Check `failures-are-not-ignored`.

A failure ignored produces output that looks like success. Nobody goes back to check it.

A surface has its own shape of the fault. A shell script that does not stop on a failed command. A `Makefile` recipe
that swallows a failure. A workflow that reads an agent's result without asking whether the agent answered. A workflow
that asks that of an agent answering under a schema, and stops there. Python that suppresses an exception. Python that
reads a command's output without reading its status.

An agent that could not do the work reports the refusal and answers under its schema anyway. That answer is present and
empty. A guard asking merely whether an answer arrived passes it through, and what follows reads emptiness as work.

An `except` that puts the caught exception into what the run reports takes `failure-is-reported:` on its own line. That
marker says where the failure comes out. A gate collecting divergences over a corpus wants the whole set rather than the
first crash, and the crash is a divergence like any other.

An `except` whose exception is an ordinary answer takes `not-a-failure:` on its own line. That marker says what the
answer means. The decoder takes in a malformed UTF-8 sequence, and `(None, 1)` is what it answers about the byte.

A marker sits on the line it excuses. A store keyed by `file:line` moves under any edit above it, and a stale key reads
as a site that stopped ignoring anything.

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

# The expressions that ask whether a bound answer holds anything. A length read off the answer. Also the keys or the
# values of that answer.
_ASKS_IF_EMPTY = r"Object\.(?:keys|values)\(\s*{name}\b|\b{name}\b[\w.\[\]]*\.length"

# The keyword that a call says it will not raise on a failure with. `subprocess.run(check=False)` is where it is
# written.
_UNRAISED = "check"

# The markers on an `except` a reader has answered for. Both sit on the `except` line and state their reason.
# `failure-is-reported:` says where the failure comes out. `not-a-failure:` says what the exception answers instead.
_IS_ANSWERED_FOR = re.compile(r"#.*\b(?:failure-is-reported|not-a-failure):\s*(\S.*)$")


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
    """`(site, what is wrong)` per `Makefile` recipe that discards the status of what it runs."""
    held = []
    for at, line in enumerate(pathlib.Path(gate.TREE, "Makefile").read_text(encoding="utf-8").splitlines(), start=1):
        if _SWALLOWED_IN_MAKE.search(line):
            held.append((f"Makefile:{at}", f"Makefile:{at} discards the status of {line.strip()}"))
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

    This reports such a handler unless its `except` line has a marker. Reading the body to guess which absorptions are
    sound would put that judgement in this file. A guess that is a little wrong lets the next case through in silence.
    The marker puts the judgement beside the handler, where a reader arrives at it.
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
            if _IS_ANSWERED_FOR.search(lines[node.lineno - 1]):
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
        "failures: a handler reports its failure, bar one whose line answers for the handler",
    )


def main() -> None:
    _check()


if __name__ == "__main__":
    main()
