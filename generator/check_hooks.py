# SPDX-License-Identifier: MIT
"""
Check that a registered hook still answers.

A hook reads an edit and answers where the edit breaks a rule. A hook that has stopped answering falls silent. Silence
is also what a hook says about a clean edit. The pair of cases reads alike from outside. `gate` lists the hook files and
`check_failures` reads their text, and neither runs a hook.

So this runs a hook, against an edit the hook must answer and an edit the hook must pass. `tests/hooks.json` holds the
pairs. The roster comes from `.claude/settings.json`. A hook registered there with no pair fails as loudly as a pair
naming no hook.

A hook on an edit gets an entry per kind in `_KINDS`. An entry holds a pair. A `skips` entry holds a reason instead. A
hook that reads a comment and skips a string literal passes the comment's pair, and the hook reads as complete. This
refuses that silence.

`.claude/settings.json` registers the command, and this runs that command with `$CLAUDE_PROJECT_DIR` replaced by the
root of the tree. A checker that `checkers.CALLED` names runs under the command registered for `checkers.THE_HOOK`.

This module holds `a-prose-checker-reads-flattened-prose` of `.claude/conventions.md`. `_flattening_faults` re-wraps the
prose of the tree and asks whether `collect_fragments.flattened` gives the same text back. `_raw_prose_faults` reads the
calls of `checkers` and asks whether a prose checker takes a flattened text.

This module holds `a-hook-answers-through-the-runner` of `.claude/conventions.md`. A registered command opens with
`run.sh`. A hook without that runner writes nothing when it dies. Such a hook leaves with a status Claude Code ignores.
Claude Code then reads such a hook as a hook that passed. https://code.claude.com/docs/en/hooks says which statuses
Claude Code reads.

This module holds `a-write-time-hook-reads-the-edit-in-place` of `.claude/conventions.md`. This module writes a pair's
`file` text into a scratch file inside the tree. The hook then gets an Edit into that scratch file. A hook decides about
a path the tree holds, and `.claude/hooks/prose_gate.py` passes a path outside it in silence. The `old_string` of a pair
in `tests/hooks.json` may be a slice of a comment with no marker. A hook that reads only the slice finds no prose there
and passes, and this reports that hook as silent.

Usage: `python3 generator/check_hooks.py`.
"""

import ast
import concurrent.futures
import importlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

from collections.abc import Container, Iterable
from typing import Any, Mapping

import collect_fragments
import gate
import prose_rules

_SETTINGS = os.path.join(gate.TREE, ".claude", "settings.json")  # the file that registers a hook.

# The command a registered hook goes through. `.claude/hooks/run.sh` turns a dead hook into a refusal.
_RUNNER = ".claude/hooks/run.sh"
_TRIED = os.path.join(gate.TREE, "tests", "hooks.json")  # the file holding the pairs a registered hook fires against.

# The events whose hooks get a tool call to decide, as https://code.claude.com/docs/en/hooks says.
_READS_A_TOOL_CALL = frozenset({"PreToolUse", "PostToolUse"})

# The tools that write a file. Such a tool hands a hook an edit to read, as https://code.claude.com/docs/en/hooks says.
# A hook on one of these needs a pair in `tests/hooks.json` per kind in `_KINDS`.
_WRITES_A_FILE = ("Edit", "Write")

# The name a probe directory opens on. The hooks under test read a path of this tree. The probe therefore sits here
# rather than under the system temporary directory. `FIND_PRUNE` in the `Makefile` keeps a directory of this name out of
# the rosters the formatters and the linters read.
_SCRATCH = "junk-hooks-"

# The kinds of prose an edit may hold. A pair names the kind it puts the fault in.
_KINDS = ("a comment", "a comment in a function body", "a docstring", "a markdown line", "a string literal")

# A checker of `checkers.READS_THE_FILE` holds a pair, and `probes` names that pair. The file of such a pair in
# `tests/hooks.json` holds the fault before the edit. The `answers` edit changes text away from that fault. The hook
# must pass the `passes` edit.
_A_HELD_FAULT = "a fault the file held"


def _pairs() -> dict[str, list[dict[str, Any]]]:
    """
    `{the name a pair is keyed by: the pairs it fires against}`. This function writes the root of the tree in place of
    `TREE/` in the file. A path in a pair is then absolute.
    """
    with open(_TRIED, encoding="utf-8") as handle:
        said = handle.read()
    held = json.loads(said.replace("TREE/", gate.TREE.rstrip("/") + "/"))
    return {name: pairs for name, pairs in held.items() if name != "_"}


def _landed(pair: Mapping[str, Any], scratch: str) -> Mapping[str, Any]:
    """
    Write the text under `file` into `scratch` for the `answers` side and the `passes` side. Answer a copy of the pair
    that names those files. An edit then has somewhere to land.

    A hook reads the file its payload names and asks what the edit would leave. An empty path lacks the `old_string` of
    an Edit. A hook passes such an Edit unread. `collect_fragments.written` answers `None` for such an edit, and
    `prose_gate` then passes it. The pair says what the path holds.

    The directory the pair names comes along. A hook that decides by directory reads `generator/probe.py` as a file of
    the generator. `kind_dispatches.dispatches_of` decides by directory.

    A side of the pair may hold `predicted`. That is the text `prose_gate` would have recorded for the call.
    `edit_landed.predicted_at` names the file that takes it.
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
        predicted = payload[what].pop("predicted", None)
        if isinstance(predicted, str):
            recorded = importlib.import_module("edit_landed").predicted_at(str(payload[what]["tool_use_id"]))
            os.makedirs(os.path.dirname(recorded), exist_ok=True)
            with open(recorded, "w", encoding="utf-8") as handle:
                handle.write(predicted)
    return payload


def _registered(events: Container[str] = _READS_A_TOOL_CALL) -> dict[str, tuple[str, str]]:
    """
    `{the hook's filename: (the command that runs it, the tool it matches)}`, over the hooks registered on `events`.

    The default `events` leaves out `UserPromptSubmit`. A hook on it puts context into the turn and decides nothing
    about an edit, as https://code.claude.com/docs/en/hooks says.
    """
    with open(_SETTINGS, encoding="utf-8") as handle:
        held = json.load(handle)
    named = {}
    for event, entries in held.get("hooks", {}).items():
        if event not in events:
            continue
        for entry in entries:
            for one in entry.get("hooks", []):
                said = one.get("command", "")
                if said:
                    named[os.path.basename(said.rstrip('"'))] = (said, entry.get("matcher", ""))
    return named


# The columns a reflow re-wraps prose to.
_COLUMNS = (gate.WIDEST, 400)

# The classes of checker that read prose. Such a checker reads a text that `collect_fragments.flattened` flattened.
_READS_PROSE = ("_PROSE", "_DOCUMENT")

# The module whose calls `_raw_prose_faults` reads.
_THE_CHECKERS = os.path.join(gate.TREE, ".claude", "hooks", "checkers.py")


def _masked(line: str) -> tuple[str, dict[str, str]]:
    """
    Hide the matches of `prose_rules.A_CODE_SPAN` in `line` behind numbered markers. Answer the masked line and a map
    from marker to span. A wrap then keeps a span whole.
    """
    spans: dict[str, str] = {}

    def hidden(found: re.Match[str]) -> str:
        key = f"\x00{len(spans)}\x00"
        spans[key] = found.group(0)
        return key

    return prose_rules.A_CODE_SPAN.sub(hidden, line), spans


def _rewrapped(text: str, columns: int) -> str:
    """
    `text` re-wrapped to `columns`. A table row keeps a line of its own. A fenced block keeps the layout the writer gave
    it.

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
        indent = line[: len(line) - len(line.lstrip())]
        under = indent + "  " if collect_fragments.does_open_a_block(line) else indent
        for said in collect_fragments.filled(masked, indent, under, columns) or [masked]:
            for key, span in spans.items():
                said = said.replace(key, span)
            held.append(said)
    return "\n".join(held)


def _unflattened_faults() -> list[str]:
    """
    Report a fault for a fragment whose prose differs from the text `collect_fragments.flattened` gives. A site's prose
    takes the same test. A fault also names a fragment whose prose digest differs from `prose_digest` over its sites.
    """
    faults = []
    for fragment in collect_fragments.fragments():
        said = [site.prose for site in fragment.sites]
        if any(one != collect_fragments.flattened(one) for one in [fragment.prose.content, *said]):
            faults.append(f"`{fragment.key}` holds prose that `flattened` changes")
        if fragment.prose.sha != collect_fragments.prose_digest(said):
            faults.append(f"`{fragment.key}` holds a prose digest that `prose_digest` over its sites does not give")
    return faults


def _flattening_faults() -> list[str]:
    """
    Report a fault for a fragment whose flattened prose moves once `_rewrapped` wraps it to a width in `_COLUMNS`. The
    fault names the fragment's site.

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
    The faults naming the registered hooks whose command has a first word that lacks `.claude/hooks/run.sh`.

    A hook that dies writes nothing and leaves with a status the tool ignores. That reads as a hook that passed a clean
    call. https://code.claude.com/docs/en/hooks says which statuses the tool ignores. A command registered around
    `run.sh` breaks that silence. `run.sh` turns the death into a refusal.
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
    The faults naming a hook that runs on an edit and that `checkers` does not call. `checkers.THE_HOOK` draws no fault.

    `prose_answer` checks the rewrites of an agent with `apply_prose.draft_refusals`. That call reaches
    `checkers.refusals_for_edit` through `text_refusals`. `.claude/settings.json` registers such a hook on an edit. The
    hook reads an edit a writer makes and says nothing about the same prose from an agent.
    """
    checkers = importlib.import_module("checkers")
    faults = []
    for name in sorted(named):
        if name in checkers.CALLED or name == checkers.THE_HOOK:
            continue
        faults.append(f"`checkers` says nothing of `{name}`. Make it a checker of `_CHECKERS`.")
    return faults


def _reached(path: Any) -> tuple[set[str], set[tuple[str, str]]]:
    """The modules `path` imports, and the `(name, attribute)` pairs its code reads off a bare name."""
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

    A checker hands its decision to a function of a helper module. `prose_words.refusal_for` names
    `prose_rules.word_refusal` that way. The name of such a function holds `refus` or `fault`. This reports a module
    that names such a function of another module.

    `checkers` and the checker modules are exempt from both reports.
    """
    checkers = importlib.import_module("checkers")
    called = {name.removesuffix(".py") for name in checkers.CALLED}
    by_module = {path.stem: _reached(path) for path in gate.modules() + gate.hook_modules()}
    deciding = {
        (owner, name)
        for checker in called
        for owner, name in by_module.get(checker, (set(), set()))[1]
        if owner not in called and ("refus" in name or "fault" in name)
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
    line leaves the tool out gives the checkers nothing to read.
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
    "check_hooks": "A probe file sits under a temporary directory.",
    "claim_state": "The claim store holds keys, digests, and a note this module states.",
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
    The faults of the writers `no-shell-file-writes.sh` declares. A writer outside `_WRITES_NO_NEW_PROSE` that asks no
    checker is a fault where the gate's modules or hook modules hold it. A name `_WRITES_NO_NEW_PROSE` declares and
    `no-shell-file-writes.sh` does not is a fault too.

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
        f"`_WRITES_NO_NEW_PROSE` declares {writer}, and `no-shell-file-writes.sh` declares no such writer"
        for writer in sorted(set(_WRITES_NO_NEW_PROSE) - set(declared))
    ]
    return faults


def _runners() -> dict[str, tuple[str, str]]:
    """
    `{the name a pair is keyed by: (the command that runs it, the tool it matches)}`.

    A registered hook other than `checkers.THE_HOOK` keeps its own pair. A checker of `checkers.CALLED` takes the pair
    of `checkers.THE_HOOK` where the settings register that hook.
    """
    checkers = importlib.import_module("checkers")
    registered = _registered()
    held = {name: one for name, one in registered.items() if name != checkers.THE_HOOK}
    if checkers.THE_HOOK in registered:
        held.update({name: registered[checkers.THE_HOOK] for name in checkers.CALLED})
    return held


def _kind_faults(name: str, pairs: list[dict[str, Any]]) -> list[str]:
    """
    The faults in the pairs `name` holds. A kind can have no pair. A pair can skip its kind and write no reason. A hook
    in `checkers.READS_THE_FILE` can hold no pair for `_A_HELD_FAULT`.

    `_KINDS` lists the kinds an edit may hold. A hook that reads a comment and skips a string literal passes the
    comment's pair. That hook then reads as complete. So a kind in `_KINDS` needs a pair. A pair that skips its kind
    writes a reason.
    """
    probed = {pair["probes"]: pair for pair in pairs}
    faults = []
    for kind in _KINDS:
        one = probed.get(kind)
        if one is None:
            faults.append(f"`{name}` reads an edit and `tests/hooks.json` says nothing of {kind}. Add a pair for it.")
        elif "skips" in one and not str(one["skips"]).strip():
            faults.append(f"`{name}` skips {kind} and writes no reason")
    checkers = importlib.import_module("checkers")
    if name in checkers.READS_THE_FILE and _A_HELD_FAULT not in probed:
        faults.append(f"`{name}` reads the whole file and `tests/hooks.json` holds no pair for {_A_HELD_FAULT}.")
    return faults


def _answered(command: str, payload: Mapping[str, Any]) -> str:
    """
    The answer a hook gives about `payload`, as the tool would receive it. https://code.claude.com/docs/en/hooks says
    what the tool receives, and that a hook printing nothing passes. An empty string is the hook passing.
    """
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


def _own_agent(pair: Mapping[str, Any], index: int) -> dict[str, Any]:
    """
    A copy of `pair` whose sides name an agent of their own. `index` tells the pairs apart.
    """
    held: dict[str, Any] = json.loads(json.dumps(pair))
    for side in ("answers", "passes"):
        for field in ("agent_id", "session_id"):
            if field in held[side]:
                held[side][field] = f"{held[side][field]}-{index}"
    return held


def _pair_faults(name: str, command: str, index: int, pair: Mapping[str, Any]) -> list[str]:
    """
    The faults `pair` finds in the hook `name`. `command` runs the hook. `_landed` writes the file of `pair` into a
    fresh scratch directory. `_own_agent` renames the agent of both sides. The edit the hook must refuse goes first.
    """
    probes = pair["probes"]
    faults = []
    with tempfile.TemporaryDirectory(dir=gate.TREE, prefix=_SCRATCH) as scratch:
        held = _own_agent(_landed(pair, scratch), index)
        if not _answered(command, held["answers"]):
            faults.append(
                f"`{name}` says nothing about the edit it must refuse at {probes}. that hook is silent under any text "
                f"it reads there."
            )
        if _answered(command, held["passes"]):
            faults.append(
                f"`{name}` answers the edit it must pass for {probes}. that hook says the same of the text it reads "
                f"there."
            )
    return faults


def _silent_hook_faults() -> list[str]:
    """
    List the faults of the hooks. A fault names a hook that passes an edit its pair says to answer. A fault names a hook
    that answers an edit its pair says to pass. A fault names a hook that only one of `tests/hooks.json` and
    `.claude/settings.json` holds. The helper checks add their own faults.
    """
    sys.path.insert(0, os.path.join(gate.TREE, ".claude", "hooks"))
    tried = _pairs()
    # A hook after the edit reads no prose an agent could hand over. `edit_landed` compares a landed file instead.
    on_an_edit = [
        hook
        for hook, (_, hook_matcher) in _registered(("PreToolUse",)).items()
        if any(tool_name in hook_matcher for tool_name in _WRITES_A_FILE)
    ]
    faults = (
        _runner_faults()
        + _flattening_faults()
        + _unflattened_faults()
        + _raw_prose_faults()
        + _unasked_faults(on_an_edit)
        + _bypass_faults()
        + _unchecked_agent_faults()
        + _unchecked_writer_faults()
    )
    registered = _runners()
    run: list[tuple[str, str, int, Mapping[str, Any]]] = []
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
        run += [(name, command, len(run), one) for one in tried[name] if "skips" not in one]
    with concurrent.futures.ThreadPoolExecutor() as pool:
        for found in pool.map(lambda job: _pair_faults(*job), run):
            faults += found
    return faults


def _check() -> None:
    """Report the hooks that fail to tell a broken edit from a clean edit."""
    gate.report(
        _silent_hook_faults(),
        "hook(s) that fail to tell a broken edit from a clean edit",
        "hooks: a registered hook refuses the faulty edit and passes the clean edit",
    )


def main() -> None:
    _check()


if __name__ == "__main__":
    main()
