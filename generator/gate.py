# SPDX-License-Identifier: MIT
"""
The way a gate says what it found, where the repository is, and the room a check gets to walk a grammar.

A gate reports through `report` here. A failure then reads the same wherever it came from. A single reporting path
cannot be got subtly wrong here and right elsewhere.

A roster here runs in a settled order.
"""

import ast
import faulthandler
import multiprocessing
import os
import pathlib
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable, Sequence
from typing import TypeGuard, TypeVar, cast

import ir

# The repository's root, worked out from where this file is rather than from where a command ran. `gate` works it out
# here, and a module that needs the root reads this. A pair of forms of the same path is a pair of things to change the
# day the layout moves.
TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_in_the_tree(path: str) -> bool:
    """Whether `path` names a file inside this repository."""
    return os.path.commonpath([os.path.abspath(path), TREE]) == TREE


def modules() -> list[pathlib.Path]:
    """
    The modules of `generator/` and `scripts/`. More than a single gate asks for this list. The dead-code checker walks
    the modules. So does the prose checker, and so does the review that reads the names a change took away. A second
    glob would be a second list to keep in step. The day a third directory holds a module, the globs would disagree.
    """
    return sorted(pathlib.Path(TREE).glob("generator/*.py")) + sorted(pathlib.Path(TREE).glob("scripts/*.py"))


def hook_modules() -> list[pathlib.Path]:
    """
    The Python of `.claude/hooks`. A hook is no module of the project, and no gate reports over a hook. A hook does
    import the modules and read names out of them. A checker of who reads a name goes wrong without these.
    """
    return sorted(pathlib.Path(TREE).glob(".claude/hooks/*.py"))


def hooks() -> list[pathlib.Path]:
    """The files of `.claude/hooks`, in whatever language the file uses."""
    return sorted(held for held in pathlib.Path(TREE).glob(".claude/hooks/*") if held.is_file())


def shell_scripts() -> list[pathlib.Path]:
    """
    The shell the project writes. The hooks come first and then the setup scripts.
    """
    return sorted(pathlib.Path(TREE).glob(".claude/hooks/*.sh")) + sorted(pathlib.Path(TREE).glob("scripts/*.sh"))


def workflows() -> list[pathlib.Path]:
    """The review workflows."""
    return sorted(pathlib.Path(TREE).glob(".claude/workflows/*.js"))


def c_sources() -> list[pathlib.Path]:
    """
    The C the project writes. The generated table is among them. `make regen` re-derives what `src/decoder_tables.h`
    says. The file cannot go stale. A rule the table breaks is a rule `grammar2decoder` writes. Reading the table says
    so.
    """
    return sorted(
        held for pattern in ("src/*.c", "src/*.h", "include/*.h") for held in pathlib.Path(TREE).glob(pattern)
    )


def test_sources() -> list[pathlib.Path]:
    """The C of the tests."""
    return sorted(pathlib.Path(TREE).glob("tests/*.c"))


def grammar_files() -> list[pathlib.Path]:
    """The grammar the generator reads."""
    return sorted(pathlib.Path(TREE).glob("grammar/*.yaml"))


def build_files() -> list[pathlib.Path]:
    """The build files the project writes. The Conan recipe is among them."""
    return [pathlib.Path(TREE, name) for name in ("CMakeLists.txt", "Makefile", "conanfile.py")]


def documents() -> list[pathlib.Path]:
    """
    The markdown the project writes. The documents of `.claude/` state the process, and they are among these.

    Public. `check_documents` holds this roster to the number rule.
    """
    return sorted(pathlib.Path(TREE).glob("*.md")) + sorted(pathlib.Path(TREE).glob(".claude/**/*.md"))


# The base names `prose_files` leaves out. A file named here writes prose the checkers refuse. The project declines to
# say that prose again. `LICENSE` is the MIT text and belongs to whoever wrote it. A file beside it is configuration for
# a tool outside the tree. `collect_fragments` holds the tree to this list.
UNREAD = ("LICENSE", ".gitattributes", ".pylintrc", "Doxyfile", "DoxygenLayout.xml", "mypy.ini", "portfile.cmake")

# The paths this project writes no prose in. A fixture is data. `third_party/` holds somebody else's code.
# `grammar/yeast-spec-1.2.yaml` is the specification's BNF with a tweak or two. `grammar2decoder` writes
# `src/decoder_tables.h`, and the prose about that table lives in the generator's own source.
NOT_OURS = ("third_party/", "tests/spec/", "grammar/yeast-spec-1.2.yaml", "src/decoder_tables.h")


def tracked_files() -> list[str]:
    """
    The files `git` tracks, named relative to the root.

    A walk of the directory answers with the build output and with `.git` itself. `git` answers with the files the
    project holds.
    """
    listed = subprocess.run(["git", "-C", TREE, "ls-files"], capture_output=True, text=True, check=False)
    if listed.returncode or not listed.stdout.strip():
        raise RuntimeError("the tree holds what `git` tracks, and this is no git working tree")
    return listed.stdout.split()


def prose_files() -> list[pathlib.Path]:
    """
    The files the project writes prose in, sorted and named once apiece.

    A tracked file is one of these. `NOT_OURS` names the data, and `UNREAD` names the file whose prose the checkers do
    not read. A file added to the tree therefore lands in this roster, and the checkers read it without anybody widening
    a list.

    `check_documents.is_prose_of_the_project` decides the same question for a path an edit names.
    """
    held = [path for path in tracked_files() if not any(part in path for part in NOT_OURS)]
    return sorted(pathlib.Path(TREE, path) for path in held if os.path.basename(path) not in UNREAD)


def named_in(directory: str, suffix: str) -> list[pathlib.Path]:
    """The entries of `directory` whose name ends in `suffix`."""
    return sorted(path for path in pathlib.Path(directory).glob(f"*{suffix}"))


def cases_under(root: str, marked_by: str) -> list[str]:
    """The directories under `root` holding a file called `marked_by`, named relative to `root` and sorted."""
    return sorted(str(held.parent.relative_to(root)) for held in pathlib.Path(root).rglob(marked_by))


def is_a_definition(statement: ast.AST) -> TypeGuard[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]:
    """Whether `statement` defines a function or a class rather than binding a value."""
    return isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))


def defined_names(statement: ast.stmt) -> tuple[str, ...]:
    """
    The names a module's top-level `statement` defines. A definition defines the name it declares. An assignment defines
    its plain targets.

    An `import` defines nothing. The name an import binds stays out. A checker of the names a file merely writes has to
    read the tokens instead.
    """
    if is_a_definition(statement):
        return (statement.name,)
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        return tuple(target.id for target in targets if isinstance(target, ast.Name))
    return ()


def names_defined_in_modules() -> set[str]:
    """
    The names the modules of `generator/` and `scripts/` define at their top level.

    A caller wanting a defined name rather than a written name asks this. `check_documents` answers a stale gone-name
    declaration with it. `review_input` drops a name it finds here from what a change took away. That name moved rather
    than went.
    """
    found = set()
    for path in modules():
        for statement in ast.parse(path.read_text(encoding="utf-8")).body:
            found |= set(defined_names(statement))
    return found


# The time an item may go without finishing before the watchdog takes it for stuck. An item here is a pure question
# about something already built, such as an invariant over a grammar or a fixture through an interpreter. Such an item
# answers in well under a second. A walk that reaches this prints where the threads are and stops. A silent spin does
# not.
_PATIENCE = 10


def _impatient(what: str, seconds: int = _PATIENCE) -> None:
    """
    Arm the watchdog over `what`. Once `seconds` have gone by, say what it was on. Print where the threads are, and take
    the whole run down.

    Naming the work is the point. A stack says which walk is spinning, and this says which question it was answering. A
    walk runs instantly over a grammar and sticks over another. The question tells the runs apart.
    """
    _patient()

    def fire() -> None:
        print(f"stuck on {what} for {seconds}s. nothing here takes that long.", file=sys.stderr, flush=True)
        faulthandler.dump_traceback()
        os._exit(3)  # noqa: SLF001  the run is wedged, and a raise here reaches no thread that would act on it

    # A single lock per process, the way the work it watches is one at a time.
    global _WATCHDOG  # noqa: PLW0603  # pylint: disable=global-statement
    _WATCHDOG = threading.Timer(seconds, fire)
    _WATCHDOG.daemon = True
    _WATCHDOG.start()


def _patient() -> None:
    """Take the watchdog off. Whatever it was watching has finished."""
    # A single lock per process, the way the work it watches is one at a time.
    global _WATCHDOG  # noqa: PLW0603  # pylint: disable=global-statement
    if _WATCHDOG is not None:
        _WATCHDOG.cancel()
        _WATCHDOG = None


# The watchdog over the work in hand. `None` while no work is under a watchdog.
_WATCHDOG: threading.Timer | None = None


def _one(item: object) -> tuple[object, ir.Reached]:
    """
    An item's answer in a worker, and the kinds that answering the item reached. The parent folds that into its own.
    """
    assert _SPREAD is not None, "a worker running outside a `spread` call"
    work, held, named = _SPREAD
    what = named(item) if named else str(item)
    _impatient(what)  # a fork keeps no threads. A worker arms its own, over each item it takes
    ir.working(what)
    try:
        return work(held, item), ir.what_was_reached()
    finally:
        _patient()
        ir.working("")


# The value the workers of `spread` read. `spread` sets the value before forking the workers, and whatever a worker
# answers about costs nothing to hand over. That is a grammar, or a corpus. A forked child copies this process, and the
# copy stays put while the workers run.
_SPREAD: tuple[Callable[..., object], object, Callable[..., str] | None] | None = None

# The types a call of `spread` is over. The thing a worker reads, the items shared out, and an answer.
_Held = TypeVar("_Held")
_Item = TypeVar("_Item")  # an item shared out.
_Answer = TypeVar("_Answer")  # a worker's answer for an item.


def spread(
    work: Callable[[_Held, _Item], _Answer],
    held: _Held,
    items: Sequence[_Item],
    named: Callable[[_Item], str] | None = None,
) -> list[_Answer]:
    """
    `work(held, item)` per item, shared out over the cores. The answers come back in the order of the items.

    A gate here asks the same shape of question. The question is a pure function of something already built and of an
    item of a list, and it runs over the whole list.

    `spread` forks the workers rather than starting them afresh, and keeps `held` in this process. The workers hand back
    their answers. The fork happens on whichever thread calls this, and that thread's stack goes with the workers. A
    check that recurses deeply then keeps its stack in a worker.

    The kinds a worker reached come back with its answer. The process running a question marks the kinds that question
    answered for. A kind a single worker reached would otherwise read as a kind nobody reached. Folding the kinds in
    here makes a reached kind reached whoever did it.

    `named` says what an item is, for the lines a worker prints while it works on that item. Without a name a line says
    the time and no more, and a dozen workers writing to a single stream give a dozen unattributable lines.
    """
    # What the forked workers read. There is one per process.
    global _SPREAD  # noqa: PLW0603  # pylint: disable=global-statement
    _SPREAD = (work, held, named)
    # The pool hands back what a worker answered, and the type went with the fork. This signature is what says what it
    # is.
    answers: list[_Answer] = []
    try:
        with multiprocessing.get_context("fork").Pool() as pool:
            # A worker that stops answering leaves the parent waiting on a result that does not come. It is stuck, or
            # gone where its own watchdog took it. So the parent watches too, and an answer that arrives puts it back.
            _impatient(f"an answer from one of the workers over {len(items)} item(s)")
            for answer, reached in pool.imap(_one, items, chunksize=8):
                _impatient(f"an answer from one of the workers over {len(items)} item(s)")
                ir.also_reached(reached)
                answers.append(cast(_Answer, answer))
    finally:
        _patient()
        _SPREAD = None
    return answers


# The stack a check walking a grammar gets. `gate` raises it past what `interpreter` sets at import. A walk over a
# transformed grammar goes past that.
_STACK_BYTES = 256 * 1024 * 1024
_RECURSION_LIMIT = 200000  # the depth Python lets that walk reach. `gate` raises it the same way.


def run_deep(work: Callable[[], None]) -> None:
    """
    Run `work` on a thread with a stack for a grammar's recursion, and end the process with what it came to.

    Whatever the work raises decides the exit code. A thread otherwise takes that away. An exception raised in a worker
    thread goes to that thread's excepthook while the main thread exits with a success. A gate then reads green over a
    check that did not finish.
    """
    sys.setrecursionlimit(_RECURSION_LIMIT)
    threading.stack_size(_STACK_BYTES)
    status = {}

    def worker() -> None:
        try:
            work()
        except SystemExit as leaving:  # failure-is-reported: as `status["code"]`, which the main thread exits with
            status["code"] = leaving.code
        except BaseException:  # noqa: BLE001  failure-is-reported: traceback and failing exit  # pylint: disable=W0718
            traceback.print_exc()
            status["code"] = 1

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    sys.exit(status.get("code", 0))


def report(errors: list[str], noun: str, summary: str) -> None:
    """
    Print `errors` to standard error and exit with a failure, or print `summary` and return.

    `noun` names what an error is, for the count that follows the list of them.
    """
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"{len(errors)} {noun}", file=sys.stderr)
        sys.exit(1)
    print(summary)
