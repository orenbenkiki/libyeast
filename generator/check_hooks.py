# SPDX-License-Identifier: MIT
"""
Check that a registered hook still answers.

A hook reads an edit and answers where the edit breaks a rule. A hook that has stopped answering falls silent. Silence
is also what a hook says about a clean edit. The pair of cases reads alike from outside. `gate` lists the hook files and
`check_failures` reads their text, and neither runs a hook. `altitude` ran against a checker that blanked the code spans
it looked for. It said nothing for that whole stretch.

So this runs a hook, against an edit the hook must answer and an edit the hook must pass. `tests/hooks.json` holds the
pairs. The roster comes from `.claude/settings.json`. A hook registered there with no pair fails as loudly as a pair
naming no hook.

A hook on an edit gets an entry per kind in `_KINDS`. An entry holds a pair. A `skips` entry holds a reason instead. A
hook that reads a comment and skips a string literal passes the comment's pair, and it reads as complete. This refuses
that silence.

`.claude/settings.json` registers the command, and this runs the command as written. That command names the `PYTHONPATH`
a hook needs along with the hook itself.

`a-write-time-hook-reads-the-edit-in-place` is held here. A pair with a `file` writes that text under a scratch
directory inside the tree, and its payload is an Edit into the file. A hook decides about a path the tree holds, and a
directory outside it would make the hook silent. The `old_string` is a slice of a comment with no marker. A hook that
reads only the slice finds no prose there and passes, and this reports that hook as silent.

Usage: `python3 generator/check_hooks.py`.
"""

import importlib
import json
import os
import subprocess
import sys
import tempfile

from collections.abc import Iterable
from typing import Any, Mapping

import gate

_SETTINGS = os.path.join(gate.TREE, ".claude", "settings.json")  # the file that registers a hook.
_TRIED = os.path.join(gate.TREE, "tests", "hooks.json")  # the pair a registered hook fires against.

# The events whose hooks get a tool call to decide.
_READS_A_TOOL_CALL = frozenset({"PreToolUse", "PostToolUse"})

# The tools that hand a hook a file to read. A hook on one of these gets an entry per kind of prose below.
_WRITES_A_FILE = ("Edit", "Write")

# The kinds of prose an edit may hold. A pair names the kind it puts the fault in.
_KINDS = ("a comment", "a docstring", "a markdown line", "a string literal")


def _pairs() -> dict[str, list[dict[str, Any]]]:
    """
    `{the hook's filename: the pairs it fires against}`, with the paths rooted in the tree.

    `prose_rewrite` reads a comment and a string literal, and a pair apiece holds it to both. An arm no pair fires
    against goes untried.
    """
    with open(_TRIED, encoding="utf-8") as handle:
        said = handle.read()
    held = json.loads(said.replace("TREE/", gate.TREE.rstrip("/") + "/"))
    return {name: pairs for name, pairs in held.items() if name != "_"}


def _landed(pair: Mapping[str, Any], scratch: str) -> Mapping[str, Any]:
    """
    `pair` with the file it names written into `scratch`. An edit then has somewhere to land.

    A hook reads the file its payload names and asks what the edit would leave. At a path holding nothing, a hook sees
    an edit that cannot apply and passes whatever came in. The pair says what the path holds.
    """
    text = pair.get("file")
    if not isinstance(text, str):
        return pair
    payload: dict[str, Any] = json.loads(json.dumps(pair))
    for what in ("answers", "passes"):
        held = payload[what]["tool_input"]
        path = os.path.join(scratch, os.path.basename(held["file_path"]))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        held["file_path"] = path
    return payload


def _registered() -> dict[str, tuple[str, str]]:
    """
    `{the hook's filename: (the command that runs it, the tool it matches)}`, over the hooks that read a tool call.

    `UserPromptSubmit` is left out. A hook on it puts context into the turn and decides nothing about an edit.
    """
    with open(_SETTINGS, encoding="utf-8") as handle:
        held = json.load(handle)
    named = {}
    for event, entries in held.get("hooks", {}).items():
        if event not in _READS_A_TOOL_CALL:
            continue
        for entry in entries:
            for one in entry.get("hooks", []):
                said = one.get("command", "")
                if said:
                    named[os.path.basename(said.rstrip('"'))] = (said, entry.get("matcher", ""))
    return named


def _unasked_faults(named: Iterable[str]) -> list[str]:
    """
    The hooks on an edit that `checkers` neither calls nor declares.

    `checkers.refusals_for` is what `prose_answer` asks of an agent's answer. A hook missing from there reads an edit a
    writer makes. It then says nothing about the same prose from an agent.
    """
    checkers = importlib.import_module("checkers")
    faults = []
    for name in sorted(named):
        if name in checkers.CALLED or name in checkers.NO_CALLABLE:
            continue
        faults.append(
            f"`checkers` says nothing of `{name}`. Name it in `CALLED`. A hook it cannot call goes in `NO_CALLABLE` "
            f"with a reason."
        )
    return faults


def _kind_faults(name: str, pairs: list[dict[str, Any]]) -> list[str]:
    """
    The kinds of prose `name` says nothing about.

    `_KINDS` lists the kinds an edit may hold. A hook that reads a comment and skips a string literal passes the
    comment's pair. That hook then reads as complete. So a kind gets an entry. An entry holds a pair or a reason.
    """
    probed = {one["probes"]: one for one in pairs}
    faults = []
    for kind in _KINDS:
        one = probed.get(kind)
        if one is None:
            faults.append(f"`{name}` reads an edit and `tests/hooks.json` says nothing of {kind}. Add a pair for it.")
        elif "skips" in one and not str(one["skips"]).strip():
            faults.append(f"`{name}` skips {kind} and writes no reason")
    return faults


def _answered(command: str, payload: Mapping[str, Any]) -> str:
    """The answer a hook gives about `payload`, as the tool would receive it. An empty string is the hook passing."""
    run = subprocess.run(
        command.replace("$CLAUDE_PROJECT_DIR", gate.TREE),
        shell=True,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=gate.TREE,
        env={**os.environ, "CLAUDE_PROJECT_DIR": gate.TREE},
        check=False,
    )
    if run.returncode not in (0, 2):
        raise RuntimeError(f"{command}: exited {run.returncode}\n{run.stderr}")
    return (run.stdout + run.stderr).strip()


def _silent_hook_faults() -> list[str]:
    """The hooks that answer an edit a pair says to pass, or pass an edit a pair says to answer."""
    registered, tried = _registered(), _pairs()
    on_an_edit = [name for name, (_, matcher) in registered.items() if any(t in matcher for t in _WRITES_A_FILE)]
    sys.path.insert(0, os.path.join(gate.TREE, ".claude", "hooks"))
    faults = _unasked_faults(on_an_edit)
    with tempfile.TemporaryDirectory(dir=gate.TREE) as scratch:
        for name in sorted(set(registered) | set(tried)):
            if name not in registered:
                faults.append(
                    f"`tests/hooks.json` holds `{name}` to a pair, and `.claude/settings.json` names no such hook"
                )
                continue
            if name not in tried:
                faults.append(
                    f"`.claude/settings.json` registers `{name}` and `tests/hooks.json` holds it to nothing. A silent "
                    f"hook reads as clean."
                )
                continue
            command, matcher = registered[name]
            if any(tool in matcher for tool in _WRITES_A_FILE):
                faults += _kind_faults(name, tried[name])
            for one in tried[name]:
                probes = one["probes"]
                if "skips" in one:
                    continue
                pair = _landed(one, scratch)
                if not _answered(command, pair["answers"]):
                    faults.append(
                        f"`{name}` says nothing about the edit it must answer for {probes}. that hook is silent "
                        f"whatever it reads there."
                    )
                if _answered(command, pair["passes"]):
                    faults.append(
                        f"`{name}` answers the edit it must pass for {probes}. that hook says the same of whatever it "
                        f"reads there."
                    )
    return faults


def _check() -> None:
    """Report the registered hooks that have stopped answering the edit their pair calls for."""
    gate.report(
        _silent_hook_faults(),
        "hook(s) that fail to tell a broken edit from a clean one",
        "hooks: a registered hook refuses the faulty edit and passes the clean one",
    )


def main() -> None:
    _check()


if __name__ == "__main__":
    main()
