# SPDX-License-Identifier: MIT
"""
Check that nothing in `generator/` or `scripts/` is unreachable.

This gate calls a module, a definition or a kind of `ir` dead. A dead module has no runner and no importer. A dead
top-level function, class or constant has no reference. A dead kind of `ir` has no construction.

Naming a kind costs little. Building a kind is the commitment. A kind nobody builds is as dead as a helper nobody calls.
Both read like working code to the next reader.

The module walk starts from the modules the `Makefile` runs and the modules `_RUN_FROM_ELSEWHERE` declares. The walk
follows imports from there.

In a module the module walk reaches, the definition walk starts from the code at import and from `main`. The gate
reports a module nothing reaches as a dead module. The gate reads no definition of a dead module.

Both walks follow a reach rather than a mention. A walk that counted a mention would let a definition keep itself alive.
A pair of functions calling one another would then stay live. A cluster a removed step left behind would stay live too.

A declaration in `_KEPT_THOUGH_DEAD` or `_RUN_FROM_ELSEWHERE` keeps a name anyway. The declaration states its reason
under the rule `a-declared-exception-carries-its-reason`. This gate holds a declaration as tightly as the rule it
excuses. A declared name that comes back to life comes back as a fault.

A `_KEPT_THOUGH_DEAD` name also keeps the helpers a kept transformation calls. A helper under a kept transformation
takes no reason of its own.
"""

import ast
import pathlib
import re
from collections.abc import Collection, Iterable, Mapping

import gate
import ir

# The names that are dead and kept anyway, and what keeps them. A declaration comes off the day it stops being true.
# This check reports a declaration that has outlived its reason.
_KEPT_THOUGH_DEAD = {
    "_lower_continuations_into_conflicts": "the step is out of `STEPS`, the transformation kept for the item in "
    "`PLAN.md` that wants it",
    "AutoDetectIndentValue": "`annotated2ir.SPECIALS` builds this while a reader takes in the vendored grammar. "
    "libyeast's own grammar writes no `<auto-detect-indent>`. `interpreter` refuses such a value deliberately.",
    "InvalidSet": "`annotated2ir.SPECIALS` builds this from `<invalid>`. the grammar writes `<invalid>`, and no step "
    "mints one.",
    "PushRecoveryAction": "the IR names this pair. the interpreter matches it. an unwind then reads where to stop and "
    "where to continue from the same place. `normalize` decides where an action leaves a parse, and that decision "
    "covers it. no step writes the pair.",
    "PopRecoveryAction": "the other half of that pair. the same reason keeps it unwritten.",
    "ConsumeTrimmedSpanAction": "the shape a `TrimStarTree` becomes. the trim-reuse pass `PLAN.md` owes builds one. "
    "the interpreter matches it already. `normalize` decides what an action leaves, and that decision matches it too. "
    "the pass has something to land on.",
    "OpenProvisionalAction": "the provisional vocabulary of a speculation. `interpreter`, `normalize` and "
    "`check_grammar_coverage` match it. the speculations `PLAN.md` owes build one.",
    "MarkProvisionalAction": "the same vocabulary. this names the side of a run a retype or an injection changes.",
    "RetypeProvisionalAction": "the same vocabulary. this gives a held token the code the resolution decides on.",
    "InjectBeforeAction": "the same vocabulary. this puts a decided marker in front of a held run.",
    "CommitProvisionalAction": "the same vocabulary. this closes a run once the resolution decides its meaning.",
}


# `_RUNS_A_MODULE` matches a `Makefile` recipe that runs a module. The `Makefile` holds the recipes `make` runs. This
# check reads those recipes rather than keeping a list beside them.
#
# A gate added to the `Makefile` becomes reachable the day somebody adds it, and a gate taken out is dead the same day.
# A list kept by hand catches the first of those and misses the second.
_RUNS_A_MODULE = re.compile(r"python3 (?:generator|scripts)/(\w+)\.py")

# The modules that run from outside `make`, with the caller that reaches them. A name here the `Makefile` also runs is a
# declaration that has gone stale, and the check says so.
_RUN_FROM_ELSEWHERE = {
    "regen_fixture": "a developer runs it by hand to record a fixture's expected output",
    "review_input": "the pre-commit review workflow runs it to prepare a change for review",
    "batch_pending_fragments": "the critic workflow runs it to write the unexamined fragments into batches",
    "apply_prose": "a developer runs it by hand to write a fragment's new prose into the tree",
}


def _run_by_make() -> set[str]:
    """The modules the `Makefile` runs."""
    makefile = pathlib.Path(gate.TREE, "Makefile").read_text(encoding="utf-8")
    return set(_RUNS_A_MODULE.findall(makefile))


def _imports() -> dict[str, set[str]]:
    """
    `{a module of generator/ or scripts/: the modules of those two it imports}`.

    The hooks are out of this. A hook is run by `.claude/settings.json` rather than by the `Makefile`. A walk looking
    for a module nothing runs and nothing imports would call a hook dead.
    """
    found: dict[str, set[str]] = {}
    for path in gate.modules():
        imported: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        found[path.stem] = imported
    return {name: imported & set(found) for name, imported in found.items()}


def _unrun_module_errors() -> list[str]:
    """
    A module of `generator/` or `scripts/` that nothing runs, directly or through an import.

    This function also returns a `_RUN_FROM_ELSEWHERE` declaration that has stopped being true. Such a declaration names
    something that is no module of those directories. A declaration also stops being true once the `Makefile` starts
    running the module it names.
    """
    imports = _imports()
    runs = _run_by_make()
    faults = []
    for name, why in sorted(_RUN_FROM_ELSEWHERE.items()):
        if name not in imports:
            faults.append(f"`RUN_FROM_ELSEWHERE` declares `{name}`, and no module of generator/ or scripts/ holds it")
        elif name in runs:
            faults.append(f"the Makefile runs `{name}`, and the stale reason for keeping it says {why}")
    reached = _reached_from(runs | set(_RUN_FROM_ELSEWHERE), imports, imports)
    return faults + [
        f"the module {name} has no runner and no importer that runs. no declaration says why the tree keeps it."
        for name in sorted(set(imports) - reached)
    ]


def _names_in(node: ast.AST) -> set[str]:
    """The names `node` reads. A call, a dispatch table's value, or a module's attribute."""
    found = set()
    for one in ast.walk(node):
        if isinstance(one, ast.Name):
            found.add(one.id)
        elif isinstance(one, ast.Attribute):
            found.add(one.attr)
    return found


def _called_in(node: ast.AST) -> set[str]:
    """
    The names `node` calls.

    A call makes a kind live, and a mention leaves it dead. A kind listed in a family or given a row in a table counts
    as a mention.
    """
    found = set()
    for one in ast.walk(node):
        if isinstance(one, ast.Call):
            called = one.func
            found.add(called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", ""))
    return found


def _reached_from(roots: Iterable[str], reaches: Mapping[str, Collection[str]], held: Collection[str]) -> set[str]:
    """
    The names a worklist reaches from `roots` through `reaches`, out of the names `held` knows about.

    The definition walk and the module walk both run through this function. The definition walk follows the names a
    definition reads, over the definitions the tree holds. The module walk follows the modules a module imports, over
    the modules the tree holds.

    `held` says which names exist and `reaches` says what a name reaches. The module walk passes a single dictionary for
    both. A key is a module, and its value holds the modules that module imports.
    """
    reached, waiting = set(), [name for name in roots if name in held]
    while waiting:
        name = waiting.pop()
        if name not in reached:
            reached.add(name)
            waiting += sorted(set(reaches.get(name, ())) & set(held))
    return reached


def _dead_code_errors() -> list[str]:
    """
    Report the parts of `generator/` and `scripts/` that nothing reaches. Such a part is a name that nothing reaches, or
    a kind that nothing builds.

    A mention does not make a definition reached. A definition is reachable from what a module does at import. The walk
    starts from `main` too. That start applies in a module something runs. From there the walk follows what a reached
    definition reads, such as a call or a dispatch table's value.

    Counting mentions instead makes a definition its own witness. A pair that call back and forth would then be live off
    a mention apiece. A whole cluster left behind by the step that used it reads as working code.

    A kind counts as built where something *calls* it. A kind named in a family or a table is a kind somebody covers,
    and that differs from a kind somebody makes. That is how `ConsumeTrimmedSpanAction` reads as live to the checks
    around it while nothing has ever produced such a node.

    This also reports a `_KEPT_THOUGH_DEAD` declaration that has stopped being true. Such a declaration names what the
    tree defines in no file. Or the declaration names what something reaches, and then it excuses nothing.
    """
    defined: dict[str, str] = {}
    reads: dict[str, set[str]] = {}
    builds: dict[str, set[str]] = {}
    roots: set[str] = set()
    built: set[str] = set()
    runs = _run_by_make()
    # The hooks define nothing this reports on. `_imports` says why. What they read is gathered below.
    for path in gate.modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            # The bindings a module makes, whatever they are called. A binding with a type is read through its
            # annotation too, a type alias being reached from there and from nowhere else.
            for name in gate.defined_names(node):
                held = (
                    [node]
                    if gate.is_a_definition(node)
                    else [getattr(node, "value", None), getattr(node, "annotation", None)]
                )
                read_from: list[ast.AST] = [one for one in held if one is not None]
                defined.setdefault(name, f"{path.name}:{node.lineno}")
                for one in read_from:
                    reads.setdefault(name, set()).update(_names_in(one) - {name})
                    builds.setdefault(name, set()).update(_called_in(one))
                reads.setdefault(name, set())
                builds.setdefault(name, set())
                top.add(name)
        # What the module reads outside any definition of its own is where the walk starts, and `main` besides where
        # something runs the module. A top-level statement that is only a call names a root, whatever defines it.
        for node in tree.body:
            if gate.is_a_definition(node):
                continue
            roots |= _names_in(node) - top
            built |= _called_in(node)
            if isinstance(node, ast.Expr):
                roots |= _called_in(node)
        if path.stem in runs or path.stem in _RUN_FROM_ELSEWHERE:
            roots.add("main")
    # A hook is not a module of the project and nothing here reports over one. A hook does read names out of the
    # modules, and a name it reads is reached. Leaving the hooks out called live code dead.
    named = {path.stem for path in gate.modules()}
    for path in gate.hook_modules():
        for read in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(read, ast.Attribute) and isinstance(read.value, ast.Name) and read.value.id in named:
                roots.add(read.attr)
    kinds = {kind.__name__ for kind in ir.KINDS}
    reached = _reached_from(roots, reads, defined)
    # What a definition constructs counts only where something reaches that definition. Counting the site rather than
    # the reach would let a dead body keep a dead kind alive.
    for name in reached:
        built |= builds.get(name, set())
    dead = {name for name in defined if not name.startswith("__")} - reached
    dead |= kinds - built
    # A helper only what is kept though dead reaches is kept with it. What is declared is the one covering the cluster
    # under it. Naming them one by one would be the same words written over, and stale in as many places.
    dead -= _reached_from(_KEPT_THOUGH_DEAD, reads, defined) - set(_KEPT_THOUGH_DEAD)
    faults = [
        f"{defined.get(name, 'ir')} defines `{name}`. nothing reaches that name, and no declaration says why the "
        f"tree keeps it."
        for name in sorted(dead - set(_KEPT_THOUGH_DEAD))
    ]
    for name, why in sorted(_KEPT_THOUGH_DEAD.items()):
        if name not in defined and name not in kinds:
            faults.append(f"`KEPT_THOUGH_DEAD` declares `{name}` and nothing defines it")
        elif name not in dead:
            faults.append(f"something reaches `{name}`, and the stale reason for keeping it says {why}")
    return faults


def main() -> None:
    gate.report(
        [f"[dead] {fault}" for fault in _unrun_module_errors() + _dead_code_errors()],
        "dead code fault(s) - a module, a definition or a kind nothing reaches with no reason given, or a reason that "
        "has gone stale",
        "dead code: something runs or imports a module of generator/ and scripts/. something reaches a definition or "
        "a kind in them. `KEPT_THOUGH_DEAD` and `RUN_FROM_ELSEWHERE` declare an exception with a reason.",
    )


if __name__ == "__main__":
    main()
