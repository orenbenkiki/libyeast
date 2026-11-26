# SPDX-License-Identifier: MIT
"""
Check that nothing in `generator/` or `scripts/` is unreachable.

This gate calls a module, a definition or a kind of `ir` dead. A dead module has no runner and no importer that runs. A
dead top-level function, class or constant is one no root reaches. A dead kind of `ir` has no construction that anything
reaches.

Naming a kind costs little. Building a kind is the commitment. A kind nobody builds is as dead as a helper nobody calls.
Both read like working code to the next reader.

The module walk starts from the modules the `Makefile` runs and the modules `_RUN_FROM_ELSEWHERE` declares. The walk
follows imports from there.

The definition walk starts from the code at import in the modules `gate.modules` returns. A module the `Makefile` runs
adds `main` as a root. So does a module `_RUN_FROM_ELSEWHERE` declares. A hook or an MCP server adds the names it reads
out of a module. The gate reports a module nothing reaches as a dead module. The gate reads the definitions of a dead
module too.

Both walks follow a reach rather than a mention. A walk that counted a mention would let a definition keep itself alive.
A pair of functions calling one another would then stay live. A cluster a removed step left behind would stay live too.

A declaration in `_KEPT_THOUGH_DEAD` or `_RUN_FROM_ELSEWHERE` keeps a name anyway. The declaration states its reason
under the rule `a-declared-exception-carries-its-reason` of `.claude/conventions.md`. This gate holds a declaration as
tightly as the rule it excuses. A declared name that comes back to life comes back as a fault.

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
    "_lower_continuations_into_conflicts": "the step is out of `STEPS`, the transformation kept for the lowering of "
    "continuations",
    "AutoDetectIndentValue": "`annotated2ir.SPECIALS` builds this while a reader takes in the vendored grammar. "
    "libyeast's own grammar writes no `<auto-detect-indent>`. `interpreter` refuses such a value deliberately.",
    "InvalidSet": "`annotated2ir.SPECIALS` builds this from `<invalid>`. the grammar writes `<invalid>`, and no step "
    "mints one.",
    "PushRecoveryAction": "the IR names this pair. the interpreter matches it. an unwind then reads where to stop and "
    "where to continue from the same place. `normalize` decides where an action leaves a parse, and that decision "
    "covers it. no step writes the pair.",
    "PopRecoveryAction": "the other half of that pair. the same reason keeps it unwritten.",
    "ConsumeTrimmedSpanAction": "the shape a `TrimStarTree` becomes. no step builds one. "
    "the interpreter matches it already. `normalize` decides what an action leaves, and that decision matches it too. "
    "the pass has something to land on.",
    "OpenProvisionalAction": "the provisional vocabulary of a speculation. `interpreter`, `normalize` and "
    "`check_grammar_coverage` match it. no step builds one.",
    "MarkProvisionalAction": "the same vocabulary. this names the side of a run a retype or an injection changes.",
    "RetypeProvisionalAction": "the same vocabulary. this gives a held token the code the resolution decides on.",
    "InjectBeforeAction": "the same vocabulary. this puts a decided marker in front of a held run.",
    "CommitProvisionalAction": "the same vocabulary. this closes a run once the resolution decides its meaning.",
}


# `_RUNS_A_MODULE` matches a `Makefile` recipe that runs a module in `generator` or `scripts` with `python3`. The
# `Makefile` holds the recipes `make` runs. This check reads those recipes rather than keeping a list beside them.
_RUNS_A_MODULE = re.compile(r"python3 (?:generator|scripts)/(\w+)\.py")

# The modules that run from outside `make`, with the caller that reaches them. A name here the `Makefile` also runs is a
# declaration that has gone stale, and the check says so.
_RUN_FROM_ELSEWHERE = {
    "regen_fixture": "a developer runs it by hand to record a fixture's expected output",
    "review_input": "the pre-commit review workflow runs it to prepare a change for review",
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
    return {name: imported_modules & set(found) for name, imported_modules in found.items()}


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
        f"the module {module} has no runner and no importer that runs. no declaration says why the tree keeps it."
        for module in sorted(set(imports) - reached)
    ]


def _names_in(node: ast.AST) -> set[str]:
    """
    The names and attribute names `ast.walk` finds under `node`, read or assigned. Such a name may sit in a call, in a
    dispatch table's value, or in a module's attribute.
    """
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
    reached, waiting = set(), [root for root in roots if root in held]
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

    A mention inside a definition that nothing reaches does not make another definition reached. A module's top-level
    statement reaches a name that module does not define. A top-level call statement reaches the names it calls. A hook
    or an MCP server reaches a name it reads off a module. The walk starts from `main` once something runs a module.
    `defined` keys a definition by its bare name. So the walk also reaches the `main` of a module nothing runs. From
    there the walk follows what a reached definition reads, such as a call or a dispatch table's value.

    A kind counts as built where a reached definition or a module's top-level statement *calls* it. A kind named in a
    family or a table is a kind somebody covers, and that differs from a kind somebody makes.

    This also reports a `_KEPT_THOUGH_DEAD` declaration that has stopped being true. Such a declaration names what the
    tree defines in no file. A stale declaration may instead name something the walk reaches. A declared kind goes stale
    only where a call builds it as well. Such a declaration excuses nothing.
    """
    defined: dict[str, str] = {}
    reads: dict[str, set[str]] = {}
    builds: dict[str, set[str]] = {}
    roots: set[str] = set()
    built: set[str] = set()
    runs = _run_by_make()
    # The walk parses the modules `gate.modules()` names.
    for path in gate.modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            # `gate.defined_names` gives the names a top-level definition or assignment binds. A binding's reads include
            # its annotation. The walk reaches a type alias that annotation names.
            for name in gate.defined_names(node):
                held: list[ast.AST | None]
                if gate.is_a_definition(node):
                    held = [node]
                elif isinstance(node, ast.AnnAssign):
                    held = [node.value, node.annotation]
                elif isinstance(node, ast.Assign):
                    held = [node.value]
                else:
                    raise AssertionError(f"{path.name}:{node.lineno}: a binding of a statement no reading covers")
                read_from: list[ast.AST] = [held_node for held_node in held if held_node is not None]
                defined.setdefault(name, f"{path.name}:{node.lineno}")
                for one in read_from:
                    reads.setdefault(name, set()).update(_names_in(one) - {name})
                    builds.setdefault(name, set()).update(_called_in(one))
                reads.setdefault(name, set())
                builds.setdefault(name, set())
                top.add(name)
        # A module's reads outside its definitions are roots, save a name that module defines. `main` is a root once
        # something runs a module. A bare call statement makes the names it calls roots.
        for node in tree.body:
            if gate.is_a_definition(node):
                continue
            roots |= _names_in(node) - top
            built |= _called_in(node)
            if isinstance(node, ast.Expr):
                roots |= _called_in(node)
        if path.stem in runs or path.stem in _RUN_FROM_ELSEWHERE:
            roots.add("main")
    # A name a hook or an MCP server reads off a module is a root.
    named = {module_path.stem for module_path in gate.modules()}
    for path in gate.hook_modules() + gate.mcp_modules():
        for read in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(read, ast.Attribute) and isinstance(read.value, ast.Name) and read.value.id in named:
                roots.add(read.attr)
    kinds = {kind.__name__ for kind in ir.KINDS}
    reached = _reached_from(roots, reads, defined)
    # A definition's calls build a kind only once the walk reaches that definition.
    for name in reached:
        built |= builds.get(name, set())
    dead = {defined_name for defined_name in defined if not defined_name.startswith("__")} - reached
    dead |= kinds - built
    # A helper reached from a `_KEPT_THOUGH_DEAD` name goes unreported. The declaration names the root of that cluster.
    dead -= _reached_from(_KEPT_THOUGH_DEAD, reads, defined) - set(_KEPT_THOUGH_DEAD)
    faults = [
        f"{defined.get(dead_name, 'ir')} defines `{dead_name}`. nothing reaches that name, and no declaration says why "
        f"the tree keeps it."
        for dead_name in sorted(dead - set(_KEPT_THOUGH_DEAD))
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
