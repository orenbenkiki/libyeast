# SPDX-License-Identifier: MIT
"""
Check that a registered hook still answers.

A hook reads an edit and answers where the edit breaks a rule. A hook that has stopped answering falls silent. Silence
is also what a hook says about a clean edit. The pair of cases reads alike from outside. `gate` lists the hook files and
`check_failures` reads their text, and neither runs a hook. A checker once blanked the code spans `altitude` looks for.
`altitude` said nothing for as long as the blanking lasted.

So this runs a hook, against an edit the hook must answer and an edit the hook must pass. `tests/hooks.json` holds the
pairs. The roster comes from `.claude/settings.json`. A hook registered there with no pair fails as loudly as a pair
naming no hook.

A hook on an edit gets an entry per kind in `_KINDS`. An entry holds a pair. A `skips` entry holds a reason instead. A
hook that reads a comment and skips a string literal passes the comment's pair, and it reads as complete. This refuses
that silence.

`.claude/settings.json` registers the command, and this runs the command as written. That command names the hook and the
`PYTHONPATH` the hook needs.

This module holds `a-prose-checker-reads-flattened-prose`. `_flattening_faults` re-wraps the prose of the tree and asks
whether `collect_fragments.flattened` gives the same text back. `_raw_prose_faults` reads the calls of `checkers` and
asks whether a prose checker takes a flattened text.

This module holds `a-hook-answers-through-the-runner`. A registered command opens with `run.sh`. A hook that dies
otherwise writes nothing and leaves with a status the tool ignores, and that reads as a hook that passed.

This module holds `a-write-time-hook-reads-the-edit-in-place`. This module writes a pair's `file` text into a scratch
file inside the tree. The hook then gets an Edit into that scratch file. A hook decides about a path the tree holds, and
a directory outside it would make the hook silent. The `old_string` is a slice of a comment with no marker. A hook that
reads only the slice finds no prose there and passes, and this reports that hook as silent.

Usage: `python3 generator/check_hooks.py`.
"""

import ast
import importlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import textwrap

from collections.abc import Iterable
from typing import Any, Mapping

import collect_fragments
import gate

_SETTINGS = os.path.join(gate.TREE, ".claude", "settings.json")  # the file that registers a hook.

# The command a registered hook goes through. `.claude/hooks/run.sh` turns a dead hook into a refusal.
_RUNNER = ".claude/hooks/run.sh"
_TRIED = os.path.join(gate.TREE, "tests", "hooks.json")  # the pair a registered hook fires against.

# The events whose hooks get a tool call to decide.
_READS_A_TOOL_CALL = frozenset({"PreToolUse", "PostToolUse"})

# The tools that hand a hook a file to read. A hook on one of these gets an entry per kind of prose below.
_WRITES_A_FILE = ("Edit", "Write")

# The kinds of prose an edit may hold. A pair names the kind it puts the fault in.
_KINDS = ("a comment", "a docstring", "a markdown line", "a string literal")


def _pairs() -> dict[str, list[dict[str, Any]]]:
    """
    `{the hook's filename: the pairs it fires against}`. The pairs name their paths from the root of the tree.

    A pair tries `prose_rewrite` on a comment, and another pair tries it on a string literal. An arm no pair fires
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
    an edit that cannot apply and passes the payload it came in with. The pair says what the path holds.

    The directory the pair names comes along. A hook that decides by directory reads `generator/probe.py` as a file of
    the generator. Dropping that directory made such a hook silent.
    """
    text = pair.get("file")
    if not isinstance(text, str):
        return pair
    payload: dict[str, Any] = json.loads(json.dumps(pair))
    for what in ("answers", "passes"):
        held = payload[what]["tool_input"]
        path = os.path.join(scratch, os.path.relpath(os.path.abspath(held["file_path"]), gate.TREE))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        held["file_path"] = path
    return payload


def _registered() -> dict[str, tuple[str, str]]:
    """
    `{the hook's filename: (the command that runs it, the tool it matches)}`, over the hooks that read a tool call.

    The map leaves out `UserPromptSubmit`. A hook on it puts context into the turn and decides nothing about an edit.
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


# The columns a reflow re-wraps prose to. A wider column leaves fewer breaks.
_COLUMNS = (120, 400)

# A code span. A reflow keeps a span whole, and a re-wrap here keeps it whole too.
_A_SPAN = re.compile(r"`[^`]*`")

# The classes of checker that read prose. Such a checker reads the line breaks of a text nothing flattened.
_READS_PROSE = ("_PROSE", "_DOCUMENT")

# The module whose calls `_raw_prose_faults` reads.
_THE_CHECKERS = os.path.join(gate.TREE, ".claude", "hooks", "checkers.py")


def _masked(line: str) -> tuple[str, dict[str, str]]:
    """`line` with a code span put behind a marker, beside the spans by marker. A wrap then keeps a span whole."""
    spans: dict[str, str] = {}

    def hidden(found: re.Match[str]) -> str:
        key = f"\x00{len(spans)}\x00"
        spans[key] = found.group(0)
        return key

    return _A_SPAN.sub(hidden, line), spans


def _rewrapped(text: str, columns: int) -> str:
    """
    `text` re-wrapped to `columns`. A table row and a fenced block keep the layout the writer gave them.

    A code span stays whole.
    """
    held: list[str] = []
    is_fenced = False
    for line in collect_fragments.flattened(text).split("\n"):
        if line.lstrip().startswith("```"):
            is_fenced = not is_fenced
            held.append(line)
            continue
        if is_fenced or not line.strip() or line.lstrip().startswith("|"):
            held.append(line)
            continue
        masked, spans = _masked(line.strip())
        for said in textwrap.wrap(masked, columns, break_on_hyphens=False, break_long_words=False) or [masked]:
            for key, span in spans.items():
                said = said.replace(key, span)
            held.append(said)
    return "\n".join(held)


def _flattening_faults() -> list[str]:
    """
    The fragments whose flattened prose moves when a reflow re-wraps the file.

    `collect_fragments.flattened` takes the wrapping out. A checker reads the text it gives, and `prose_digest` takes
    the digest of that text.
    """
    faults = []
    for fragment in collect_fragments.fragments():
        for site in fragment.sites:
            if not site.prose.strip():
                continue
            said = collect_fragments.flattened(site.prose)
            for columns in _COLUMNS:
                if collect_fragments.flattened(_rewrapped(site.prose, columns)) != said:
                    faults.append(
                        f"`{fragment.key}` in {site.path} flattens differently once a reflow wraps it to {columns} "
                        f"columns. a refusal and a digest move with the wrapping."
                    )
    return faults


def _raw_prose_faults() -> list[str]:
    """The faults `_calls_reading_raw` finds in the source of `checkers`."""
    return _calls_reading_raw(pathlib.Path(_THE_CHECKERS).read_text(encoding="utf-8"))


def _calls_reading_raw(source: str) -> list[str]:
    """
    The calls in `source` handing a prose checker a text that `collect_fragments.flattened` did not flatten.

    `checkers._refusals` takes the class of checker to run. A call naming a class of `_READS_PROSE` builds its `_Prose`
    around a `flattened` call. A call built around anything else hands a checker the wrapping of the file.
    """
    faults = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or getattr(node.func, "id", "") != "_refusals":
            continue
        if len(node.args) < 2 or getattr(node.args[1], "id", "") not in _READS_PROSE:
            continue
        built = node.args[0]
        if not isinstance(built, ast.Call) or getattr(built.func, "id", "") != "_Prose" or not built.args:
            faults.append(
                f"`checkers.py`:{node.lineno} runs a prose checker. `_Prose` did not build the value it reads."
            )
            continue
        said = built.args[0]
        if not isinstance(said, ast.Call) or getattr(said.func, "attr", "") != "flattened":
            faults.append(
                f"`checkers.py`:{node.lineno} runs a prose checker. `collect_fragments.flattened` did not flatten the "
                f"text it reads."
            )
    return faults


def _runner_faults() -> list[str]:
    """
    The registered hooks whose command opens with something other than `run.sh`.

    A hook that dies writes nothing and leaves with a status the tool ignores. That reads as a hook that passed a clean
    call. A command registered around `run.sh` keeps that silence.
    """
    with open(_SETTINGS, encoding="utf-8") as handle:
        held = json.load(handle)
    faults = []
    for event, entries in held.get("hooks", {}).items():
        for entry in entries:
            for one in entry.get("hooks", []):
                said = one.get("command", "")
                if said and _RUNNER not in said.split()[0]:
                    faults.append(
                        f"`.claude/settings.json` registers `{said}` on {event}, and that command opens with "
                        f"something other than `{_RUNNER}`. a death in that hook reads as a pass."
                    )
    return faults


def _unasked_faults(named: Iterable[str]) -> list[str]:
    """
    The hooks on an edit that `checkers` neither calls nor declares.

    `prose_answer` puts an agent's answer through `checkers.refusals_for_edit`. A hook missing from there reads an edit
    a writer makes. It then says nothing about the same prose from an agent.
    """
    checkers = importlib.import_module("checkers")
    faults = []
    for name in sorted(named):
        if name in checkers.CALLED or name in checkers.NO_CALLABLE or name == checkers.THE_HOOK:
            continue
        faults.append(
            f"`checkers` says nothing of `{name}`. Name it in `CALLED`. A hook it cannot call goes in `NO_CALLABLE` "
            f"with a reason."
        )
    return faults


def _reached(path: Any) -> tuple[set[str], set[tuple[str, str]]]:
    """The modules `path` imports, and the `(module, name)` pairs its code reads."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {str(node.module) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    read = {
        (node.value.id, node.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    return imported, read


def _bypass_faults() -> list[str]:
    """
    The modules that run a checker without `checkers`.

    `checkers.CALLED` names the checker modules. This reports a module that imports a checker module.

    A checker calls a function of a helper module to decide. `prose_rules.word_refusal` is such a function. The name of
    such a function holds `refus` or `fault`. This reports a module that calls such a function.

    `checkers` and the checker modules are exempt from both reports.
    """
    checkers = importlib.import_module("checkers")
    called = {name.removesuffix(".py") for name in checkers.CALLED}
    by_module = {path.stem: _reached(path) for path in gate.modules() + gate.hook_modules()}
    deciding = {
        (module, name)
        for checker in called
        for module, name in by_module.get(checker, (set(), set()))[1]
        if module not in called and ("refus" in name or "fault" in name)
    }
    faults = []
    for module, (imported, read) in sorted(by_module.items()):
        if module == "checkers" or module in called:
            continue
        faults += [
            f"{module} imports the checker {name}. use `checkers.refusals_for_edit`" for name in imported & called
        ]
        faults += [
            f"{module} decides with {owner}.{name}. use `checkers.refusals_for_edit`"
            for owner, name in sorted(read & deciding)
            if owner != module
        ]
    return faults


def _unchecked_agent_faults() -> list[str]:
    """
    The agent definitions whose answer `prose_answer` cannot read.

    An agent answers through the StructuredOutput tool, and `prose_answer` reads that call. A definition whose tools
    line leaves the tool out answers in text, and the checkers read nothing of that answer.
    """
    faults = []
    for path in gate.agents():
        head = path.read_text(encoding="utf-8").split("\n---\n", 1)[0]
        tools = next((line for line in head.splitlines() if line.startswith("tools:")), "")
        if "StructuredOutput" not in re.split(r"[\s,:]+", tools):
            faults.append(f"`.claude/agents/{path.name}` names no StructuredOutput tool. the checkers miss its answer")
    return faults


# The calls that put a text through the checkers. A writer of prose reaches `checkers.refusals_for_edit` through one of
# these.
_CHECKS_ITS_PROSE = ("refusals_for_edit", "text_refusals", "draft_refusals", "proposal_refusals")

# The scripts that write a file and put no new prose in it. The value says what the script writes. A declared writer
# outside this list calls the checkers.
_WRITES_NO_NEW_PROSE = {
    "batch_pending_fragments": "The batches of a settling run sit under the git directory.",
    "check_hooks": "A probe file sits under a temporary directory.",
    "coverage_badge": "The coverage badge is a build artifact.",
    "grammar2decoder": "The comments of the decoder tables come from the literals of that module.",
    "regen_fixture": "A fixture's expected output is data.",
    "review_input": "The files a review reads sit under the git directory.",
    "wrap_long_comments": "The comments of a source file come back wrapped at the column limit.",
    "write_agent_prompts": "An agent definition copies `.claude/conventions.md` and `.claude/rejected.md`.",
    "write_cited_names": "The generated list holds the names a citation may name.",
}


def _declared_writers() -> list[str]:
    """The scripts `no-shell-file-writes.sh` declares as writers of a file."""
    said = pathlib.Path(gate.TREE, ".claude", "hooks", "no-shell-file-writes.sh").read_text(encoding="utf-8")
    return [pathlib.Path(one).stem for one in re.findall(r"^\s*\*(\S+\.py)\)\s*return 0", said, re.MULTILINE)]


def _unchecked_writer_faults() -> list[str]:
    """
    The scripts that write a file, put prose in it, and ask no checker.

    `_WRITES_NO_NEW_PROSE` declares a writer whose file holds no new prose. A writer outside that list calls one of
    `_CHECKS_ITS_PROSE`.
    """
    by_module = {path.stem: path for path in gate.modules() + gate.hook_modules()}
    declared = _declared_writers()
    faults = []
    for name in declared:
        if name in _WRITES_NO_NEW_PROSE or name not in by_module:
            continue
        said = by_module[name].read_text(encoding="utf-8")
        if not any(call in said for call in _CHECKS_ITS_PROSE):
            faults.append(
                f"{name} writes a file and asks no checker. Call `checkers.refusals_for_edit`, or declare the writer "
                f"in `_WRITES_NO_NEW_PROSE` with what it writes."
            )
    faults += [
        f"`_WRITES_NO_NEW_PROSE` declares {name}, and `no-shell-file-writes.sh` declares no such writer"
        for name in sorted(set(_WRITES_NO_NEW_PROSE) - set(declared))
    ]
    return faults


def _runners() -> dict[str, tuple[str, str]]:
    """
    `{the name a pair is keyed by: (the command that runs it, the tool it matches)}`.

    A checker of `checkers.CHECKERS` gets a pair of its own. The hook registered on an edit runs that pair. A checker
    registers as no hook of its own. A shell hook runs as itself.
    """
    checkers = importlib.import_module("checkers")
    registered = _registered()
    held = {name: one for name, one in registered.items() if name != checkers.THE_HOOK}
    if checkers.THE_HOOK in registered:
        held.update({name: registered[checkers.THE_HOOK] for name in checkers.CALLED})
    return held


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
    sys.path.insert(0, os.path.join(gate.TREE, ".claude", "hooks"))
    tried = _pairs()
    on_an_edit = [name for name, (_, matcher) in _registered().items() if any(t in matcher for t in _WRITES_A_FILE)]
    faults = (
        _runner_faults()
        + _flattening_faults()
        + _raw_prose_faults()
        + _unasked_faults(on_an_edit)
        + _bypass_faults()
        + _unchecked_agent_faults()
        + _unchecked_writer_faults()
    )
    registered = _runners()
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
                        f"`{name}` says nothing about the edit it must refuse at {probes}. that hook is silent "
                        f"under any text it reads there."
                    )
                if _answered(command, pair["passes"]):
                    faults.append(
                        f"`{name}` answers the edit it must pass for {probes}. that hook says the same of the text it "
                        f"reads there."
                    )
    return faults


def _check() -> None:
    """Report the registered hooks that have stopped answering the edit their pair calls for."""
    gate.report(
        _silent_hook_faults(),
        "hook(s) that fail to tell a broken edit from a clean edit",
        "hooks: a registered hook refuses the faulty edit and passes the clean edit",
    )


def main() -> None:
    _check()


if __name__ == "__main__":
    main()
