# SPDX-License-Identifier: MIT
"""
Check that the documents and the prose beside the code describe the tree.

`a-count-is-answered-for`. A document states no number the tree decides. `_NOT_A_NUMBER_OF_THE_TREE` says which numbers
may appear. The rule covers `DESIGN.md`, `PLAN.md` and `CHANGELOG.md`, and it covers the prose beside the code too. The
gate prints the live figures on a run. `prose_rules` says what replaces a number.

`text-says-what-is`. `DESIGN.md` says what is. `CHANGELOG.md` says what a change did. Neither writes in the tense
`PLAN.md` owns. This asks that of those documents. Beside code the same words describe where the parse is, and that is
what the comment is for.

`every-cited-name-exists` is the half of `every-claim-is-checked-before-it-is-written` a gate can decide. A backticked
name names something in the tree. This asks that of those documents and of the prose beside the code. `CHANGELOG.md`
answers for it less what `_NAMES_THE_TREE_NO_LONGER_HOLDS` declares gone. An entry naming what a change took away is the
entry doing its job.

`a-declared-exception-carries-its-reason`. This holds `_NAMES_THE_TREE_NO_LONGER_HOLDS`, `_NAMES_STILL_OWED` and
`_NAMES_A_FILE_MAY_CITE_UNANSWERED` in both directions. A declaration the tree has outgrown fails as loudly as a missing
one.

A document and a comment are one question here rather than two. The checker is one. `_joined` flattens either to the
line that holds the count. `_AS_DIGITS` writes a word numeral as its own. Splitting that checker apart is how the digits
and the words came to disagree by half.

This measures the counts off the pipeline the documents describe, and builds the stages to do it. That wants no
interpreter and no corpus, only the grammar read and the steps run.
"""

import ast
import collections
import io
import os
import pathlib
import re
import subprocess
import tokenize

from collections.abc import Container, Mapping, Sequence

import annotated2ir
import gate
import ir
import ir2spec
import normalize
import spaces
import spec_tests
import wire

# The quantities `DESIGN.md` is allowed to state, by the words that name them. It is the document that states a count of
# the tree. It describes what is, and there a magnitude is part of the argument. The space of quantities comes out
# small, and that is what makes reading it affordable.
#
# `PLAN.md` states none. It says what the tree owes rather than how much of that remains. `CHANGELOG.md` states none
# either. Nobody can check a count in it once the tree has moved.
#
# A count reads `<number> <name>`, and the convention stops there. It reads as prose and greps as data. That is what
# lets this be a gate rather than a reviewer's errand. A number that nobody can find is a number nobody re-derives. A
# stale number found by hand was of exactly that kind. A quantity worth stating in this document is worth naming here
# first.
#
# This list does not cover what else the document may count. An invariant has its own name, and the count goes in front
# of that name. `_measured_for_the_report` reads that off the pipeline rather than off any list. The list below names
# the quantities no invariant names.
#
# The words come first. The counting below follows them. A quantity's name and its value are separate questions.
# `review_input` asks the name without the value and prepares a review from it. Reaching the name through the value
# would build the whole pipeline to learn a list.
_NAMES_A_DOCUMENT_MAY_STATE = (
    "standings",
    "parameters of the grammar",
    "bookkeeping axes",
    "comparison axes",
    "assignments to the bookkeeping axes",
    "bookkeeping assignments off a line start",
    "bookkeeping assignments at a line start",
    "comparison assignments off a line start",
    "comparison assignments at a line start",
    "steps of the pipeline",
    "productions of libyeast's grammar",
    "productions of the official grammar",
    "indicator productions",
    "rules libyeast adds of its own",
    "official productions with the yeast token codes",
    "productions the pipeline hands on",
    "conformance fixtures",
)


# The grammar source writes these where a production says what its characters take.
_TOKEN_OPERATORS = ("(token)", "(emit)")


# A text as its lines, beside the numbers those lines take.
_Lines = list[tuple[int, str]]


def _does_hold_a_token(written: object) -> bool:
    """Whether a production, as the grammar file writes it, names a code for any character."""
    if isinstance(written, dict):
        return any(key in _TOKEN_OPERATORS for key in written) or any(map(_does_hold_a_token, written.values()))
    return isinstance(written, list) and any(map(_does_hold_a_token, written))


def _measured_for_the_report(
    stages: Sequence[tuple[str, dict[str, ir.Prod]]], final: dict[str, ir.Prod]
) -> dict[str, int]:
    """
    `{the words a quantity is named by: what it actually is}`, printed by the gate and written into no document.

    Read here to put a figure a command away. This checks nothing against a document. A document states no number for it
    to check.
    """
    invariants = normalize.invariants_by_name()
    # What `verify-spec` compares against the official grammar, read off `ir2spec`'s own sets. The indicator productions
    # stay in, `ir2spec.official` keeping them and rewriting the references.
    base, written = stages[0][1], annotated2ir.written()
    aside = ir2spec.OWN | ir2spec.MARKER_ONLY
    official = [name for name in base if name not in aside]
    # How the guard answers decompose. The two groups do not multiply out, and a group is counted on either side of a
    # line start.
    bookkeeping, compared = collections.defaultdict(set), collections.defaultdict(set)
    for one in spaces.ALL_GUARD_ANSWERS:
        bookkeeping[one.is_at_line_start].add(tuple(getattr(one, axis) for axis in spaces.BOOKKEEPING_AXES))
        compared[one.is_at_line_start].add(tuple(getattr(one, axis) for axis in spaces.COMPARISON_AXES))
    counts = {
        "standings": len(spaces.ALL_GUARD_ANSWERS),
        "parameters of the grammar": len(annotated2ir.PARAMS),
        "bookkeeping axes": len(spaces.BOOKKEEPING_AXES),
        "comparison axes": len(spaces.COMPARISON_AXES),
        "assignments to the bookkeeping axes": len(bookkeeping[False] | bookkeeping[True]),
        "bookkeeping assignments off a line start": len(bookkeeping[False]),
        "bookkeeping assignments at a line start": len(bookkeeping[True]),
        "comparison assignments off a line start": len(compared[False]),
        "comparison assignments at a line start": len(compared[True]),
        "steps of the pipeline": len(normalize.STEPS),
        "productions of libyeast's grammar": len(base),
        "productions of the official grammar": len(official),
        "indicator productions": len(ir2spec.INDICATORS),
        "rules libyeast adds of its own": len(ir2spec.OWN),
        "official productions with the yeast token codes": sum(
            1 for name in official if _does_hold_a_token(written[name])
        ),
        "productions the pipeline hands on": len(final),
        "conformance fixtures": len(spec_tests.load()),
    }
    if set(counts) != set(_NAMES_A_DOCUMENT_MAY_STATE):
        raise ValueError("the quantities counted here and the words naming them above have come apart")
    # An invariant is named by its own name, with the count written in front of it as a count here is. The checker that
    # answers a document is then the checker the pipeline is held to, not a second checker that could drift from it.
    counts.update({name: len(held.test(final)) for name, held in invariants.items()})
    return counts


# The numbers a text in this project may write. A count of the tree is not among them. The list holds the marker of an
# ordered list, a spec version, and a milestone. It holds an item of the order of work, a section of this document, and
# an effort estimate. It holds a bound the grammar states, a test-suite case, and a value the code compares against.
#
# The list names what may appear. The list does not name what may not appear. A count somebody writes then fails here. A
# wording nobody thought of does not excuse that count. A number in this list depends on nothing in the tree. This
# refuses a number that does depend on the tree, in a document and in a comment alike. A measurement copied into prose
# is a copy nobody re-measures.
#
# The documents and the prose beside the code share this list. A list per document drifted apart. A changelog allowed a
# bound that a design refused, and neither could state a reason.
_NOT_A_NUMBER_OF_THE_TREE = re.compile(
    # The specification, the vendored grammar, and what either of them numbers.
    r"YAML 1\.[0-9]|libyaml/1\.[0-9]|spec-1\.[0-9]|\b1\.[0-9] quirks|section [0-9.]+|\brules? [0-9]+(?:/[0-9]+)?"
    # The structure of the plan. It numbers items rather than counting anything.
    r"|[Mm]ilestones? [0-9]+(?: and [0-9]+)?|\bitems? [0-9]+|[Pp]hases? [0-9]+|~[0-9]+-[0-9]+ (?:mo|wks?)"
    # A position or a bound the format itself fixes. This project sets the column limit, and the limit moves with
    # nothing.
    r"|\(max\)[: ]+[0-9]+|\bcolumn [0-9]+|[0-9]+ columns?|\bindentation of [0-9]+|[0-9]+ or more|[0-9]+ or -[0-9]"
    r"|UTF-[0-9]+"
    # A width or a codepoint. Also a literal, and a version of something outside this tree. `zero-width` names a guard
    # that reads without moving.
    r"|(?:zero|0)-width|[0-9]+-bit|[0-9]+ ?(?:bits|bytes|MB|KB)|\b1024\b|U\+[0-9A-F]{4}|0x[0-9A-Fa-f]+|RFC [0-9]+"
    r"|Python [0-9]+|[0-9]+ hexadecimal digits|\w+\([0-9]\)|\\0"
    # The numbers `decoder_tables.h` writes beside a key. A quoted literal is a character, a bracketed number is the
    # official grammar's production number, and a bit range is the decoder ABI's key layout.
    r"|'[0-9]'|\[[0-9]{3}\]|\bbits [0-9]+\.\.[0-9]+"
    # A measurement of time. That is a fact about a machine rather than about the grammar.
    r"|[0-9.]+ ?(?:us|ms|s)\b|[0-9]+ (?:seconds?|minutes?|million)|slower than [0-9]+"
    # The shape of an example the prose reasons over. The example fixes the shape, and the shape moves with nothing.
    r"|[0-9]+ short lines|[0-9]+ tokens|[0-9]+-space"
    # An identifier, a link, and a comparison written as code.
    r"|[A-Z0-9]{4}/[0-9]{2}|https?://\S+|\b0\.x\b|[=<>+-]=?\s*[0-9]+"
    # The pronouns `one` and `two` count nothing. A pattern here takes the word and the digit alike. `_joined` writes a
    # numeral word as its digit before this runs.
    r"|\b(?:one|1) (?:at a time|by (?:one|1)|another|apiece)\b|\b(?:one|1|two|2) of\b"
    r"|\b(?:is|are|was|were|be|been|as|the|this|that|any|no|another|its|their)\s+(?:one|1|two|2)\b"
    r"|\b(?:one|1|two|2)\b(?=\s*[.,;:)])",
    re.IGNORECASE,
)


# A line whose numbers are no count of anything. That is a table. Its leading column is a milestone. This reads a
# heading like any other line. A count stated in a heading counts the tree as much as a count stated in a sentence.
_A_TABLE_ROW = re.compile(r"^\s*\|")

# The fence around the YAML a document may open with. An agent definition names itself and its tools there. Those fields
# are data.
_A_FRONT_MATTER = re.compile(r"^---\s*$")

# The digits a numeral word comes to. `_joined` writes them over the word in place. The offsets hold, and a single
# pattern reads both forms.
#
# `zero`, `one` and `two` are here. A determiner reads as a numeral, and a determiner goes stale the way a count does.
# `_NOT_A_NUMBER_OF_THE_TREE` lists what may appear and says why.
_AS_DIGITS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16",
    "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
}  # fmt: skip
# The word numerals in either case.
_A_WORD_NUMERAL = re.compile(r"\b(?:" + "|".join(_AS_DIGITS) + r")\b", re.IGNORECASE)


def _joined(text: str) -> tuple[str, list[tuple[int, int]]]:
    """
    `(the document's prose as one line, [(where a source line starts in it, its number)])`.

    The prose comes back as a single line. This project wraps these documents at the column limit, and a phrase is as
    likely to straddle a wrap as not. `the fourteen | generator gates` is a single count and reads as a pair of lines.
    Matching a line at a time misses those. So the lines are joined. This keeps the offsets to say which line a match
    came from. A fault naming the wrong line is a fault somebody has to go looking for.

    Code spans go a line at a time on the way in. Stripping them over the whole text would swallow the newline inside a
    span that opens on a line and closes on another. The lines after such a span would get the wrong number.

    A table row contributes nothing. Its numbers count nothing at all. An ordered list's marker goes the same way. A
    document opening on YAML goes the same way, and `collect_fragments` reads that block as data. This answers whatever
    is line-shaped. A checker downstream cannot see a line.

    A numeral written in digits stays. This rewrites a word numeral as its own digits. A count is a count however the
    text writes it. Writing them alike is what lets a single rule read both. A count written out was wrong by half for
    as long as it was read by something other than what read the digits. This takes the offsets after that rewriting
    rather than before, and they answer for the text the patterns read.
    """
    pieces: list[str] = []
    starts: list[tuple[int, int]] = []
    at = 0
    is_front = text.startswith("---")
    for number, line in enumerate(text.splitlines(), start=1):
        if is_front:
            is_front = number == 1 or not _A_FRONT_MATTER.match(line)
            starts.append((at, number))
            pieces.append("")
            at += 1
            continue
        # The list marker goes before the emphasis does, and not after. A count opening a line in bold reads as
        # `**516**.` and becomes `516.` the moment the stars are gone. That is a marker to anything looking for one.
        said = "" if _A_TABLE_ROW.match(line) else re.sub(r"^\s*[0-9]+\.\s", " ", line)
        said = re.sub(r"`[^`]*`|\*", "", said).strip()
        said = _A_WORD_NUMERAL.sub(lambda found: _AS_DIGITS[found.group(0).lower()], said)
        starts.append((at, number))
        pieces.append(said)
        at += len(said) + 1
    return " ".join(pieces), starts


def _line_of(starts: Sequence[tuple[int, int]], offset: int) -> int:
    """The source line a match at `offset` in the joined prose came from."""
    found = starts[0][1]
    for at, number in starts:
        if at > offset:
            break
        found = number
    return found


# A document narrating its own history rather than saying what the tree holds. `It used to be X` is a claim about a tree
# nobody has. The reader has the tree in front of them and no way to check the other. The sentence ages into a lie the
# day somebody reads it without that history.
#
# A changelog entry is already the record that something changed. It does not need the word either. `one cap bounds all
# three` says what the change did as well as `one cap now bounds them` does.
#
# `no longer than` and `any more than` are comparisons and not this. This lets both through.
_NARRATES_ITS_OWN_HISTORY = re.compile(
    r"\bnow\b|\bno longer\b(?! than)|\bused to\b|\bpreviously\b|\bany ?more\b(?! than)", re.IGNORECASE
)


# The places a check looks for a document's backticked names. A name in backticks is a citation. A citation that nothing
# answers is a document describing something that does not exist. That is how `extend-returns`, `factor-prefixes` and
# the functions an argument rested on stayed written long after the code stopped holding them.
#
# Asked of `DESIGN.md` and `PLAN.md`. `CHANGELOG.md` cites what a change took away, such as `OpenMatch`, `ColumnLtGuard`
# and `YS_CODE_UNPARSED`. Naming the thing removed is what the entry is for. A name absent from the tree is right there
# rather than wrong.
#
# The rosters come from `gate`. `gate` holds them. A second glob here is what `an-enumeration-of-the-tree-lives-in-gate`
# refuses.

# The suffixes that name a category. The shorthand the code and the documents share then resolves. Both write `PushCode`
# for `PushCodeAction` and `Look` for `LookGuard`. A citation is no worse for using the short form the source uses.
_A_CATEGORY = re.compile(r"(Action|Guard|Tree|Wrapper|Value|Set|State|Part|Call|Prod)$")

# Names that name something outside this tree. This tree holds none of them. They are the ABI libyeast is a drop-in for,
# and a build shape another language writes. They are also the debug view vendored beside the reference parser, and the
# tools the review workflow gives a reader. Last, they are the external programs the vet targets run.
_NAMES_FROM_ELSEWHERE = frozenset(
    {"cdylib", "load_all", "next_token", "yaml2html", "Grep", "Glob", "clang-tidy", "clang-format"}
)

# The hyphenated names `PLAN.md` gives to work still owed. The tree holds none of them yet. Naming such a thing is how a
# plan says what it intends to build. The fault the check beside this catches is a sentence saying such a thing already
# runs. A name earns its place here where the plan describes the work as unwritten. A name comes off the day the
# pipeline builds the thing, and the pipeline then answers for the name.
_NAMES_STILL_OWED = frozenset(
    {
        "distribute-empties",
        "every-called-alternative-is-unconditional",
        "every-choice-is-deterministic",
        "explicit-empties",
        "lift-empties",
        "no-conditional-production-matches-empty",
    }
)


def _stale_owed_errors(named: Container[str], known: Container[str]) -> list[str]:
    """
    The names `_NAMES_STILL_OWED` declares as unbuilt that the tree already holds.

    This is the other direction of the rule `_NAMES_THE_TREE_NO_LONGER_HOLDS` states. A name here excuses a citation of
    something that does not exist. The day the pipeline grows it, that excuse hides the citation from the check. The
    name reads as owed for as long as nobody notices. The list's comment says a name `comes off the day it is built`.
    This is what says the day has come.
    """
    return [
        f"`NAMES_STILL_OWED` declares `{name}`, and the tree holds it"
        for name in sorted(_NAMES_STILL_OWED)
        if name in named or name.replace("-", "_") in known
    ]


# The names of the tracked files. A question of the tree asks for these, and the read happens once.
_FILE_NAMES: set[str] = set()


def _file_names() -> set[str]:
    """
    The files the tree holds, named with the extension and without. A citation may write a file name either way.

    The tree this walks is what `git` tracks and no more. A walk of the directory answers with `.git`'s contents too. A
    citation then resolves against a git object's name. The hex names of those objects count as files the project holds,
    and a citation of any of them passes. Build output would answer the same way and go with the build.
    """
    if not _FILE_NAMES:
        listed = subprocess.run(["git", "-C", gate.TREE, "ls-files"], capture_output=True, text=True, check=False)
        if listed.returncode or not listed.stdout.strip():
            raise RuntimeError("the tree holds what `git` tracks, and this is no git working tree")
        for name in listed.stdout.splitlines():
            path = pathlib.PurePosixPath(name)
            _FILE_NAMES.update((path.name, path.stem))
    return _FILE_NAMES


def _named_in_the_tree() -> set[str]:
    """
    The names the tree writes, with the category suffixes stripped so the shorthand a source writes resolves too.
    """
    known = set(_file_names())
    reached = (
        gate.modules()
        + gate.hook_modules()
        + gate.c_sources()
        + gate.test_sources()
        + gate.grammar_files()
        + gate.build_files()
    )
    for path in reached:
        found = _A_NAME.findall(path.read_text(encoding="utf-8", errors="ignore"))
        known |= set(found) | {_A_CATEGORY.sub("", one) for one in found}
    return known


def _named_by_the_pipeline(stages: Sequence[tuple[str, dict[str, ir.Prod]]]) -> set[str]:
    """
    The hyphenated names a text may cite. Those are a step, an invariant, and a production of any stage. They are also a
    value a parameter takes, and a code of the wire.

    A parameter's values are among them. The grammar writes a value hyphenated, and the prose cites it that way.
    `block-in` is what a context *is*. `annotated2ir` names that context. A string is no less real than a rule.

    A stage counts as much as the base does. A text may cite a production that a step mints. The argument for a step is
    usually about the shapes the stages before it made. `b-l-folded_c_flow-in` is `monomorphize`'s, and is what
    `PLAN.md` reasons over.
    """
    named = {step.name for step in normalize.STEPS}
    named |= {held.name for step in normalize.STEPS for held in step.invariants}
    named |= {held.name for held in normalize.OWED}
    named |= {name for _label, grammar in stages for name in grammar}
    return named | set(wire.CODE_CHAR) | set(annotated2ir.CONTEXTS) | _named_by_the_process()


def _named_by_the_process() -> set[str]:
    """
    The hyphenated names the process uses. Such a name is a rule of `.claude/conventions.md`, a hook that enforces a
    rule, or a workflow.

    A rule is cited by name where it is enforced. `check_conventions` names the rules it decides. A hook names the rule
    it enforces. A workflow takes the name its own `meta` declares. Such a name is no step and no production. It names
    something a reader can open.
    """
    held = set()
    for path in gate.hooks():
        held.add(path.stem)
    for path in gate.workflows():
        held |= set(re.findall(r"name:\s*'([a-z][a-z0-9-]*)'", path.read_text(encoding="utf-8")))
    rules = pathlib.Path(gate.TREE, ".claude", "conventions.md")
    if rules.exists():
        held |= set(re.findall(r"^- \*\*([a-z][a-z0-9-]*)\*\*", rules.read_text(encoding="utf-8"), re.MULTILINE))
    return held


# A hyphenated name, in the shape a citation takes. A segment past the first holds a letter. That is what tells a name
# from the arithmetic a comment writes the same way. `n-1` is a column and not a count to justify.
#
# The first segment may be a single letter, and the grammar's segment usually holds just that. `c-printable`, `s-white`,
# `l-yaml-stream` and `b-break` begin with a letter. A rule wanting a longer first segment would be blind to the
# productions the documents cite.
#
# A segment may hold an underscore. That is how a monomorphized copy writes the arguments moved into its name, as in
# `b-l-folded_c_flow-in`. Such a name matched neither this nor the backticked-identifier rule. It was the citation shape
# both checks were blind to.
_A_HYPHENATED_NAME = re.compile(r"`([a-z][a-z0-9_]*(?:-[a-z0-9_+]*[a-z+][a-z0-9_+]*)+)`")

_A_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # An identifier, in the shape the tree writes.

# A name the `Makefile` declares. That is a variable or a target at a line's start.
_A_MAKE_DECLARATION = re.compile(r"^([A-Za-z0-9_./%-]+)\s*(?::(?!=)|[:?+]?=)", re.MULTILINE)

# A name as a text cites it. The name is in backticks, and has a dot where it names a member of something. The dot is
# what tells a citation from an identifier. Code names a module and its member apart. So the patterns stay separate.
_A_CITED_NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")


def private_citation_errors(where: str, lines: _Lines) -> list[str]:
    """
    The places a text cites a name its module keeps to itself.

    Public. The `altitude` hook asks this of a sentence as the writer writes it, minutes before the gate would.

    `DESIGN.md` gives context, perspective and architecture. A private name belongs to its module. The prose beside that
    name explains it. A document citing such a name repeats what a docstring says. Somebody then edits the docstring and
    leaves the copy behind.

    A leading underscore is the shape, on the name or on any part of a dotted name. It is what the source writes to say
    a reader outside the module has no business here.
    """
    faults = []
    for number, line in lines:
        for cited in _A_CITED_NAME.findall(line):
            if any(part.startswith("_") for part in cited.split(".")):
                faults.append(f"{where}:{number} cites `{cited}`. that name belongs to its module.")
    return faults


def _does_answer(known: Container[str], cited: str) -> bool:
    """
    Whether `known` is the name `cited` writes. The private form counts as well.

    The tree writes `_lowered_once`, and prose cites `lowered_once` for the same thing. The underscore goes on the last
    part of a dotted name.
    """
    if cited in known:
        return True
    head, _, tail = cited.rpartition(".")
    return (f"{head}._{tail}" if head else f"_{tail}") in known


def _dangling_step_errors(
    where: str, lines: _Lines, named: Container[str], known: Container[str], gone: Container[str] = frozenset()
) -> list[str]:
    """
    The places a text cites a hyphenated name that is no step, no invariant and no production.

    A step named and not built is how a reader came to call `factor-prefixes` built. The prefix extraction the next work
    turns on read as half-done while nothing of it existed.

    A name for work still owed is fine. A sentence claiming the thing already runs is a fault. Whether the tree holds
    the name tells the pair apart.

    A name whose underscore form the tree writes counts too. The same transformation is a step called
    `lower-continuations-into-conflicts` and a function called `_lower_continuations_into_conflicts`. Prose citing
    either is citing the thing that exists.

    A `verify-`, `vet-` or `gh-` name is no citation of this kind, and this passes it. Such a name is a `Makefile`
    target, and the target itself answers for it. `_readme_gate_errors` holds `README.md`'s roster to what the targets
    run.
    """
    faults = []
    for number, line in lines:
        for cited in _A_HYPHENATED_NAME.findall(line):
            if cited in named or cited in _NAMES_STILL_OWED or cited.startswith(("verify-", "vet-", "gh-")):
                continue
            if _does_answer(known, cited.replace("-", "_")) or cited in gone:
                continue
            # A name from outside names nothing here whichever check reaches it. `_A_NAME` splits a hyphenated name. The
            # tree cannot be said to write `clang-tidy` however often a recipe runs it.
            if cited in _NAMES_FROM_ELSEWHERE:
                continue
            faults.append(f"{where}:{number} cites `{cited}`. that is no step, no invariant and no production.")
    return faults


def _dangling_name_errors(
    where: str,
    lines: _Lines,
    known: Container[str],
    gone: Container[str] = frozenset(),
    is_a_bare_word_a_citation: bool = True,
) -> list[str]:
    """
    The places a text cites a name in backticks that names nothing in the tree.

    `is_a_bare_word_a_citation` is false for the prose beside the code and true for a document. It leaves a citation
    whose shape belongs to the tree. That is a name with an underscore, or a dotted path.

    Code prose backticks a shell command, a value written as a string, and a placeholder in a worked example. Those name
    nothing, and they read as a bare word.

    A name with an underscore reads as a citation and no more. It is the shape a rename leaves behind. This catches a
    docstring naming the private form a rename took a function away from. The other checkers let such a docstring
    through.
    """
    faults = []
    for number, line in lines:
        for cited in _A_CITED_NAME.findall(line):
            if not is_a_bare_word_a_citation and "_" not in cited and "." not in cited:
                continue
            if cited in _NAMES_FROM_ELSEWHERE or _does_answer(known, cited) or cited in gone:
                continue
            # A dotted citation is answered by each part of it. Held to the first alone, a citation naming an absent
            # member resolves against the module it hangs off.
            if all(_does_answer(known, part) for part in cited.split(".")):
                continue
            faults.append(f"{where}:{number} cites `{cited}`. that name names nothing in the tree.")
    return faults


# The names `CHANGELOG.md` uses that the tree has dropped. A changelog says what a change did. Naming the thing a change
# took away is what an entry is for. A citation naming nothing is right there, where the same citation in `DESIGN.md` or
# `PLAN.md` is a fault.
#
# The list is for the other half. That half is an entry describing the mechanism as it is and naming that mechanism
# wrongly. Such an entry reads as history, and nobody checks it. Somebody wrote `Step`'s fields here under a name the
# type did not have. The invariant report gave a name that named nothing. The claims the pipeline makes sat under
# invariant names that named nothing. Those looked exactly like the legitimate citations around them.
#
# So this declares what an entry writes, and a name absent from here fails. A name here the tree holds again is a stale
# declaration, and this reports it as the other exemptions in this tree report theirs.
_NAMES_THE_TREE_NO_LONGER_HOLDS = frozenset({
    "ALWAYS_CONSUMES", "CRLF", "CloseMatch", "ColumnLeGuard", "ConsumeCountedSpan", "ConsumeLiteralAction",
    "ConsumePeekedAction",
    "EndMustConsumeGuard", "Invariant.__call__", "LiteralPeekGuard", "NEVER", "PEEK_OUTPUT", "Region",
    "TAKES_CHARACTERS", "Verdict", "licensed",
    "WRITES_NOTHING", "ZERO_WIDTH", "_EVERY_CHARACTER", "_ahead_of", "_ahead_of_any", "_ahead_of_gate",
    "_caller_continuation", "_does_scan_read", "_entering_guards", "_narrowed_ahead", "_spans_meeting",
    "bound-exclusions", "check_determinize", "check_determinize.py", "check_provisional", "check_provisional.py",
    "_standings_now", "spaces._standing_at",
    "determinize.py", "determinize.verdict", "normalize._denoted_spans",
    "normalize.provisional_faults",
    "did_move", "dissolve-residues", "distribute-residues", "does_refuse_softly",
    "every-body-is-a-choice-a-run-or-a-set", "every-choice-is-a-body", "every-conflict-can-be-asked",
    "every-decision-goes-on-a-character", "every-empty-match-is-a-way", "every-end-of-stream-gates-a-leaf-way",
    "every-run-is-a-body", "every-scope-closes-on-its-own-way", "every-scope-closes-on-the-path-that-opens-it",
    "every-way-carries-a-test", "every-way-gated", "every-way-has-actions-or-a-call", "expand-called-ways",
    "fold-literals-into-gates", "gate-hoist", "gate-hoist-call", "gate_hoist_call", "header-eof",
    "hoist-askable-guards", "hoist-guards", "hoist-past-actions", "inline-shared-heads", "keep-empties", "keep-none",
    "lift-chomping", "lift-gates-to-callers", "lift-runs", "lower-bounds", "match_start",
    "mint-consuming-and-residue", "no-call-enters-both-ways", "no-conflict-shares-a-called-head",
    "no-gate-decides-nothing", "no-guard-left-among-the-actions", "no-item-holds-a-match",
    "no-lookahead-left-to-factor", "no-partial-overlap", "no-unreachable-option", "only-root-empties",
    "refuses_softly", "speculate-folds",
    "splice-conflicts", "split-counted-spans-on-the-count", "split-gates", "ys_close_writer", "ys_discard_reader",
})  # fmt: skip


def _defined_in_the_tree() -> set[str]:
    """
    The names `generator/` and `scripts/` define at their top level, and the files the tree holds.

    A caller asks this instead of `_named_in_the_tree` where the question is whether something *exists* rather than
    whether the tree writes it anywhere. The permissive checker counts a name written in a string or a comment. That is
    right for a citation, where the tree writes the name. It is wrong here. `_NAMES_THE_TREE_NO_LONGER_HOLDS` is itself
    a list of names written as strings. The permissive checker answers that the tree still holds a name that list
    declares gone.

    This reads the C too, and reads it differently. The C gives the identifiers its code writes, with the comments and
    the strings taken out. A C name has no top level to walk the way a module's does. The question is whether the tree
    still holds the name at all. The code writing it answers that. Left out, a C symbol declared gone that the C goes on
    defining is a declaration nothing can report stale. The same name in `DESIGN.md` resolves against `src/` perfectly
    well.
    """
    return set(_file_names()) | _named_by_the_c() | gate.names_defined_in_modules()


def _named_by_the_c() -> set[str]:
    """The identifiers the C writes, with the comments and the string literals taken out."""
    found: set[str] = set()
    for path in gate.c_sources():
        code = _A_C_COMMENT.sub(" ", _strings_blanked(path.read_text(encoding="utf-8")))
        found |= set(_A_NAME.findall(code))
    return found


def _named_by_the_code() -> set[str]:
    """
    The names the tree's code writes, with the prose beside that code left out. Those are the Python's `NAME` tokens,
    the C's identifiers, and the files the tree holds.

    These answer a citation written beside the code. `_named_in_the_tree` cannot answer it. That checker counts a name
    written in a comment. A docstring citing a name is then its own witness that the name exists. A rename leaving a
    name behind reads exactly like a rename that left none.

    `_defined_in_the_tree` is too narrow the other way. Prose beside the code cites a parameter, a method and an
    attribute as readily as a top-level definition. A module binds no such name. The names the code writes is the
    checker that admits them and still refuses a name that appears only in the prose.
    """
    # A name a declaration says names nothing. Each is written as a string in the declaration itself. The harvest below
    # would take that string as the answer, and this check would go blind to the name everywhere.
    declared = set(_NAMES_THE_TREE_NO_LONGER_HOLDS) | set(_NAMES_STILL_OWED)
    declared |= {one for names in _NAMES_A_FILE_MAY_CITE_UNANSWERED.values() for one in names}
    found = set(_file_names()) | _named_by_the_c()
    # The names the `Makefile` declares. A recipe's prose cites a variable there the way it cites a function.
    found |= set(_A_MAKE_DECLARATION.findall(pathlib.Path(gate.TREE, "Makefile").read_text(encoding="utf-8")))
    for path in gate.modules() + gate.hook_modules():
        with open(path, "rb") as handle:
            found |= {held.string for held in tokenize.tokenize(handle.readline) if held.type == tokenize.NAME}
        # And the values it writes as strings, where a value is shaped like a name. `before_mark` is a region a retype
        # names, and `text` is a code a token holds.
        for held in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(held, ast.Constant) and isinstance(held.value, str) and _A_NAME.fullmatch(held.value):
                if held.value not in declared:
                    found.add(held.value)
    return found


def _stale_gone_errors(lines: _Lines, named: Container[str]) -> list[str]:
    """
    The names `_NAMES_THE_TREE_NO_LONGER_HOLDS` declares needlessly. This reads either direction.

    A name the tree holds again is a declaration that outlived what it excused. A name `CHANGELOG.md` has stopped citing
    wants no excuse at all. Leaving that name there says an entry names something the entry does not name.
    """
    defined = _defined_in_the_tree()
    cited: set[str] = set()
    for _number, line in lines:
        cited |= set(_A_CITED_NAME.findall(line))
        cited |= set(_A_HYPHENATED_NAME.findall(line))
    # A dotted name is held again where each part of it is, which is the checker its citation is answered by. The first
    # part alone says the module is there and nothing about the member. A member the tree lost read as held.
    faults = [
        f"`NAMES_THE_TREE_NO_LONGER_HOLDS` declares `{name}`, and the tree still holds it"
        for name in sorted(_NAMES_THE_TREE_NO_LONGER_HOLDS)
        if name in defined or name in named or all(part in defined for part in name.split("."))
    ]
    return faults + [
        f"`NAMES_THE_TREE_NO_LONGER_HOLDS` declares `{name}`, and CHANGELOG.md does not cite it"
        for name in sorted(_NAMES_THE_TREE_NO_LONGER_HOLDS - cited)
    ]


def _document_lines(document: str) -> _Lines:
    """`[(the line's number, what it says)]` over the lines of `document`."""
    with open(os.path.join(gate.TREE, document), encoding="utf-8") as handle:
        return list(enumerate(handle.read().splitlines(), start=1))


def _code_prose() -> dict[str, _Lines]:
    """
    The prose beside the code, keyed by the file that holds it. That is the comments and docstrings of `generator/` and
    `scripts/`. From `_c_prose` at the end, it is the comments of the C.

    A comment and a docstring are one thing here. Both are prose beside code, read by whoever arrives next and corrected
    by nobody. Both go stale the way a document does. Anything outside them is code, where a number is a literal and a
    hyphenated string is what the grammar writes rather than a citation.

    This takes the comments from `tokenize` rather than from the lines that start with a `#`. A comment sharing its line
    with code then reads like any other. A fixpoint's depth sat written down in such a comment. Another named a step
    that nothing built. A trailing comment contributes its own text and not the code in front. That code is code
    wherever it appears.
    """
    found = {}
    for path in gate.modules() + gate.hook_modules():
        source = path.read_text(encoding="utf-8")
        lines = source.split("\n")  # the newline the tokens are numbered by. `splitlines` splits on more than that
        said = {}
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                at, column = token.start
                # A comment on a line of its own is part of a wrapped block, and its marker goes so the block joins into
                # a line. A comment sharing its line with code keeps its marker.
                said[at] = token.string.lstrip("#") if not lines[at - 1][:column].strip() else token.string
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                if ast.get_docstring(node):
                    docstring = node.body[0]
                    for at in range(docstring.lineno, (docstring.end_lineno or docstring.lineno) + 1):
                        said[at] = lines[at - 1]
        found[path.name] = sorted(said.items())
    found.update(_c_prose())
    found.update(_marked_prose())
    return found


# The files the rest of the project's prose lives in, by the marker its comments use. `is_prose_of_the_project` names
# these suffixes and the write-time hooks hold them. This checker skipped them. A hook's own comment lives in such a
# file.
_A_HASH_COMMENT = re.compile(r"^\s*#\s?(.*)$")
_A_SLASH_COMMENT = re.compile(r"^\s*//\s?(.*)$")  # The marker C and the workflows use.


def _marked_prose() -> dict[str, _Lines]:
    """The comments the shell scripts and the workflows hold, keyed by the file that holds them."""
    prose: dict[str, _Lines] = {}
    for paths, marker in ((gate.shell_scripts(), _A_HASH_COMMENT), (gate.workflows(), _A_SLASH_COMMENT)):
        for path in paths:
            said: dict[int, str] = {}
            for at, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                held = marker.match(line)
                if held and not line.startswith("#!"):
                    said[at] = held.group(1)
            prose[path.name] = sorted(said.items())
    return prose


# A `//` comment, and a `/* */` comment. Either holds whatever follows the marker. Doxygen's `///` and `///<` are the
# same comment with a marker of their own, and `lstrip` takes the whole marker.
_A_C_COMMENT = re.compile(r"//(?P<line>[^\n]*)|/\*(?P<block>.*?)\*/", re.DOTALL)


def _strings_blanked(source: str) -> str:
    """
    `source` with the string literals blanked to spaces of their own length. Offsets and lines are unmoved.

    A literal's contents are data rather than code or prose. A `//` in a literal is no comment. An identifier in a
    literal is a name the C mentions rather than a name it writes. Blanking is enough here and cheaper than a C lexer. A
    blanked literal leaves neither behind.
    """
    return re.sub(r'"(?:\\.|[^"\\])*"', lambda found: " " * len(found.group(0)), source)


def _c_prose() -> dict[str, _Lines]:
    """
    The comments the C holds, keyed by the file that holds them.

    The C goes stale the way the generator does. This checker is the first to cover it. A comment in `decoder.h` named
    `ys_new_string_parser` long after the constructor took another name. A count of the character sets the grammar
    consumes sat beside that comment. Nobody could reproduce the checker that answered it.

    This takes a comment's text and not the line it shares, as in the Python. The code beside it stays code. This does
    not walk into a string, where a `//` would read as a comment. So the quotes go blank first. That is enough here and
    cheaper than a C lexer. A blanked string leaves nothing prose-shaped behind, and prose is what this looks for.
    """
    prose = {}
    for path in gate.c_sources():
        blanked = _strings_blanked(path.read_text(encoding="utf-8"))
        said = {}
        for found in _A_C_COMMENT.finditer(blanked):
            text = found.group("line")
            if text is None:
                text = found.group("block")
            at = blanked.count("\n", 0, found.start()) + 1
            for offset, one in enumerate(text.splitlines() or [""]):
                said[at + offset] = one.lstrip("/*< ")
        prose[path.name] = sorted(said.items())
    return prose


# The names a file's prose may cite that name nothing in the tree, keyed by the file writing them. An entry says why
# below.
#
# The scope is the file rather than the name. Elsewhere the name is exactly what this hunts. Exempting `speculate-folds`
# outright would blind the check to a docstring citing it as a stage of the pipeline. Such a docstring is the fault this
# check exists for. A document may cite none of these. `DESIGN` and `PLAN` describe the tree the project holds.
_NAMES_A_FILE_MAY_CITE_UNANSWERED = {
    # Names this file cites as gone. Such a name outlived the code holding it. Naming such a name is how the prose
    # beside a check says what the check is about. `zero-width` names a guard rather than a step.
    "check_documents.py": frozenset({
        "extend-returns", "factor-prefixes", "speculate-folds", "YS_CODE_UNPARSED", "ys_new_string_parser",
        "zero-width",
    }),  # fmt: skip
    # The hook `prose_rewrite` replaced. Naming that hook is how the prose says what changed.
    "check_prose.py": frozenset({"prose_shape"}),
    "prose_rewrite.py": frozenset({"prose_shape"}),
    # Names a worked example invents for the shape it shows. `Namer` shows what minting a helper for `foo` comes to, and
    # `_set_name` shows what that transformation makes of a set's name.
    "normalize.py": frozenset({"foo_1", "foo_2", "foo_3", "foo_4", "foo_3_1"}),
    "grammar2decoder.py": frozenset({"NS_PLAIN_SAFE_IN"}),
    # A name a worked example invents for the suffixes a monomorphic copy and a minted helper take.
    "check_grammar_coverage.py": frozenset({"foo_c_flow-in_1"}),
}


# A numeral, written in digits or in words. `_AS_DIGITS` gives the words.
#
# `_NOT_A_NUMBER_OF_THE_TREE` lists what may pass, and this refuses a numeral outside that list. A determiner reads as a
# numeral here. `one way of a choice` and `the two halves` go in the list with their reason.
_A_NUMBER = re.compile(r"\b(?:[0-9]+|" + "|".join(_AS_DIGITS) + r")\b", re.IGNORECASE)


def is_prose_of_the_project(path: str) -> bool:
    """
    Whether the rules on prose hold for `path`.

    A path outside the tree holds prose of somebody else. `gate.NOT_OURS` names the data of this tree, and `gate.UNREAD`
    names the file the checkers leave alone. Any other path is prose of the project.

    A path nobody has added yet answers yes. A new file is prose until somebody rules otherwise.
    """
    if os.path.isabs(path) and os.path.commonpath([os.path.abspath(path), gate.TREE]) != gate.TREE:
        return False
    if any(part in path for part in gate.NOT_OURS):
        return False
    return os.path.basename(path) not in gate.UNREAD


_A_FENCE = re.compile(r"^\s*```")  # The marker a document opens and closes a code block with.

# A code span. The contents are code, and the words inside belong to no sentence.
_A_CODE_SPAN = re.compile(r"`[^`]*`")

# A Doxygen code block holds a program. A number a program writes counts nothing in the tree.
_A_CODE_BLOCK = re.compile(r"@code\b.*?@endcode\b", re.DOTALL)

# A member named the way Doxygen references one. The pair of colons belongs to the name rather than to the sentence.
# `@ref` takes the name bare. A comment cannot write this one inside a code span.
_A_MEMBER_NAME = re.compile(r"\b\w+::\w+")

# The tokens a comment writes as syntax rather than as a sentence. A tool's pragma names the rule it turns off. The
# marker `failure-is-reported:` opens a declared exemption. `check_failures` reads that exemption off the source itself.
# The token goes and the sentence beside it stays. This reads prose sharing the line, and checks a claim in it like any
# other.
_A_DIRECTIVE = re.compile(r"\b(?:noqa|pylint|type|fmt|isort|mypy|yapf|nopep8)\s*:\s*\S*|\bfailure-is-reported:")

# A web address. The scheme ends in a colon, and the path holds full stops. A checker that takes an address for prose
# finds an appositive where a link is, and cuts a sentence at the dots.
_A_URL = re.compile(r"https?://\S+")


def _numbers_of_the_tree(said: str) -> list[re.Match[str]]:
    """
    The numbers in `said` that depend on the tree. `said` is prose joined into a single line.

    `_NOT_A_NUMBER_OF_THE_TREE` excuses a numeral. Any other numeral is a fault. This covers a document and a comment
    under the same checker. A numeral a comment needs, such as the value of the constant beside it, goes into that list
    with a reason.

    A code span holds code rather than a sentence. Its numerals go blank before the patterns read the text. A Doxygen
    code block goes the same way.

    `numbers_in_prose` wraps this for the `no_tree_numbers` hook.
    """
    said = _A_CODE_BLOCK.sub(lambda found: " " * len(found.group(0)), said)
    said = _A_CODE_SPAN.sub(lambda found: " " * len(found.group(0)), said)
    left = _NOT_A_NUMBER_OF_THE_TREE.sub(lambda found: " " * len(found.group(0)), said)
    return list(_A_NUMBER.finditer(left))


def numbers_in_prose(prose: str) -> list[str]:
    """
    The numerals of the tree `prose` holds. `_joined` blanks a table row. A step number written in a table reaches
    nothing here.

    Public. The `no_tree_numbers` hook asks this of a fragment, and the gate asks the same of the tree.
    """
    return sorted({found.group(0).strip() for found in _numbers_of_the_tree(_joined(prose)[0])})


def _numbers_in_code_errors(prose: Mapping[str, _Lines]) -> list[str]:
    """
    The places the prose beside the code states a number that depends on the tree.

    `_code_prose` hands over the generator's comments and docstrings together with the C's comments. This joins that
    prose into a single line before reading it, the way `_joined` treats a document. A comment block wraps at the column
    limit, and a number is as likely to straddle a wrap as not.
    """
    faults = []
    for name, lines in prose.items():
        held = dict(lines)
        said, starts = _joined("\n".join(held.get(at, "") for at in range(1, max(held, default=0) + 1)))
        for found in _numbers_of_the_tree(said):
            at = _line_of(starts, found.start())
            faults.append(f"{name}:{at} states {found.group(0).strip()!r}. that number depends on the tree.")
    return faults


# The form the two checkers above give a text's citation. Both write the fault the same way.
_A_FAULTED_CITATION = re.compile(r"cites `([^`]+)`")


def _cited_by(fault: str) -> str:
    """The name a dangling-citation fault names as the citation."""
    found = _A_FAULTED_CITATION.search(fault)
    if found is None:
        raise ValueError(f"a dangling-citation fault naming nothing cited: {fault!r}")
    return found.group(1)


def _cited_in_code_errors(
    prose: Mapping[str, _Lines], named: Container[str], known: Container[str], written: Container[str]
) -> list[str]:
    """
    The places the prose beside the code cites a name that names nothing. That is the generator's comments and
    docstrings and the C's comments alike, as `_code_prose` hands them over.

    This asks of the prose beside the code what the other checks ask of the documents. A name outliving what answered to
    it reads as a thing that exists. `check_normalize` described a stage at `speculate-folds` while no such step
    existed. The citation checks read the documents, and the prose beside the code went unread.

    This asks both forms a citation comes in. A hyphenated name is a step, an invariant or a production. The documents
    and the code both go through `_dangling_step_errors`. `written` holds the names the code writes, with the prose left
    out. `known` counts a name written in a comment too.

    `_NAMES_A_FILE_MAY_CITE_UNANSWERED` answers in both directions off this single checker. This reads the prose with
    nothing excused. A declaration takes a fault out, and a declaration that takes none out reads as stale.
    """
    by_file = {
        name: _dangling_step_errors(name, lines, named, known)
        + _dangling_name_errors(name, lines, written, is_a_bare_word_a_citation=False)
        for name, lines in prose.items()
    }
    faults = [
        fault
        for name, found in by_file.items()
        for fault in found
        if _cited_by(fault) not in _NAMES_A_FILE_MAY_CITE_UNANSWERED.get(name, frozenset())
    ]
    for name, gone in sorted(_NAMES_A_FILE_MAY_CITE_UNANSWERED.items()):
        cited = {_cited_by(fault) for fault in by_file.get(name, [])}
        faults += [
            f"`NAMES_A_FILE_MAY_CITE_UNANSWERED` declares `{one}` for {name}. nothing there wants the excuse."
            for one in sorted(gone - cited)
        ]
    return faults


# The words a document may not say, by the tense they keep. `PLAN.md` is what is owed. `DESIGN.md` is what is true.
# `CHANGELOG.md` is what a change did. A tense is a word, and this matches on the word.
_OUT_OF_ITS_DOMAIN_IN = {
    "DESIGN.md": (
        re.compile(r"\b(?:will be|will have|is going to|to be (?:built|written|added|done)|is owed|are owed|remains "
                   r"to|still to|not yet|planned|intends? to|intended to)\b", re.IGNORECASE),
        "DESIGN says what is",
    ),
    "CHANGELOG.md": (
        re.compile(r"\b(?:is owed|are owed|still owed|remains to|still to|to be (?:built|written|added|done)|"
                   r"will be|is going to|planned|next step)\b", re.IGNORECASE),
        "CHANGELOG says what a change did",
    ),
}  # fmt: skip


def _out_of_domain_errors(document: str) -> list[str]:
    """The places `document` writes in a tense another document owns."""
    held = _OUT_OF_ITS_DOMAIN_IN.get(document)
    if held is None:
        return []
    pattern, why = held
    with open(os.path.join(gate.TREE, document), encoding="utf-8") as handle:
        said, starts = _joined(handle.read())
    return [
        f"{document}:{_line_of(starts, found.start())} says {found.group(0)!r}, and {why}"
        for found in pattern.finditer(said)
    ]


def _document_history_errors(document: str) -> list[str]:
    """The places `document` narrates its own history rather than describing the tree."""
    with open(os.path.join(gate.TREE, document), encoding="utf-8") as handle:
        text = handle.read()
    said, starts = _joined(text)
    return [
        f"{document}:{_line_of(starts, found.start())} says {found.group(0)!r}, and a document says what is rather "
        f"than what changed"
        for found in _NARRATES_ITS_OWN_HISTORY.finditer(said)
    ]


def _number_errors(document: str) -> list[str]:
    """
    The places `document` states a number that depends on the tree.

    A single rule covers the markdown `gate.documents` names and the prose beside the code. This cuts a code span
    entire, and the ticks go with it. Its contents are a literal the grammar or the C writes.
    """
    with open(os.path.join(gate.TREE, document), encoding="utf-8") as handle:
        said, starts = _joined(handle.read())
    return [
        f"{document}:{_line_of(starts, found.start())} states {found.group(0)!r}. that number depends on the tree."
        for found in _numbers_of_the_tree(said)
    ]


# The targets whose rosters `README.md` gives, held to what the `Makefile` says they run. The target decides. The README
# is therefore held to it rather than the other way about.
_ROSTERS = ("verify", "vet", "vet-format")

# The gates `README.md` lists under a roster whose target does not name them. Something the target does name runs them.
# `verify-grammar` gathers the pair before it. README gives that pair so a reader can run either. The formatters run
# under `vet-format`. `vet` names that. This list is a declaration, like the other exemptions here. A list README grew
# and nothing runs would otherwise read exactly like this list.
_GATES_UNDER_ANOTHER = {
    "verify": frozenset({"verify-grammar-base", "verify-grammar-base-coverage"}),
    "vet": frozenset({
        "vet-format-c", "vet-format-md", "vet-format-py", "vet-format-cmake", "vet-format-sh", "vet-format-make",
    }),  # fmt: skip
    "vet-format": frozenset(),
}


def _readme_gate_errors() -> list[str]:
    """
    The disagreements between `README.md`'s list of what a roster target runs and the `Makefile`'s target.

    A list of the gates is a roster. A roster kept by hand goes stale the first time somebody adds a gate. This roster
    went stale, and `vet-format` went stale after it. Kept mechanically, a roster is a list a reader can trust. Kept by
    hand, a roster says the target runs less than the target does.

    Both sides write a roster out in full, a name at a time. README wrote the formatters as a line of suffixes,
    `vet-format-c` then `-md` then `-py`. That reads perfectly well. A checker cannot answer such a line.
    """
    makefile = pathlib.Path(gate.TREE, "Makefile").read_text(encoding="utf-8")
    readme = pathlib.Path(gate.TREE, "README.md").read_text(encoding="utf-8")
    faults = []
    for target in _ROSTERS:
        said = re.search(rf"^{re.escape(target)}:((?:[^\n]*\\\n)*[^\n]*)", makefile, re.M)
        if said is None:
            faults.append(f"Makefile: no `{target}:` target is here, and a reader cannot tell what README should list")
            continue
        gate_name = re.compile(rf"\b{re.escape(target)}-[a-z-]+")
        runs, listed = set(gate_name.findall(said.group(1))), set(gate_name.findall(readme))
        faults += [f"README.md does not list `make {name}`. `make {target}` runs it." for name in sorted(runs - listed)]
        faults += [
            f"README.md lists `make {name}`. `make {target}` does not run it."
            for name in sorted(listed - runs - _GATES_UNDER_ANOTHER[target])
        ]
    return faults


def _check() -> None:
    """Report the faults this module found, tagged by the rule they break."""
    stages = normalize.stages(annotated2ir.load())
    final = stages[-1][1]
    # No document states a number the tree decides. The gate measures what it needs and prints it, and a copy written
    # into prose is a copy nobody re-measures.
    counts = [fault for path in gate.documents() for fault in _number_errors(str(path.relative_to(gate.TREE)))]
    errors = [f"[count] {fault}" for fault in counts]
    for document in ("DESIGN.md", "PLAN.md", "CHANGELOG.md"):
        for fault in _document_history_errors(document):
            errors.append(f"[says-what-is] {fault}")
        for fault in _out_of_domain_errors(document):
            errors.append(f"[domain] {fault}")
    # Asked of the two documents that describe what is. Each checker is named by which it is, and which a question is
    # asked with is the whole of what it means.
    named_anywhere = _named_in_the_tree()
    named_by_the_pipeline = _named_by_the_pipeline(stages)
    named_by_the_code = _named_by_the_code()
    for document in ("DESIGN.md", "PLAN.md"):
        lines = _document_lines(document)
        for fault in _dangling_name_errors(document, lines, named_anywhere) + _dangling_step_errors(
            document, lines, named_by_the_pipeline, named_anywhere
        ):
            errors.append(f"[cites] {fault}")
    # Altitude. `DESIGN.md` describes the architecture, and a private name belongs to its module.
    for fault in private_citation_errors("DESIGN.md", _document_lines("DESIGN.md")):
        errors.append(f"[altitude] {fault}")
    # `CHANGELOG.md` is held to the same names, less what it declares gone. An entry naming what a change took away is
    # the entry doing its job.
    lines = _document_lines("CHANGELOG.md")
    for fault in _dangling_name_errors(
        "CHANGELOG.md", lines, named_anywhere, _NAMES_THE_TREE_NO_LONGER_HOLDS
    ) + _dangling_step_errors(
        "CHANGELOG.md", lines, named_by_the_pipeline, named_anywhere, _NAMES_THE_TREE_NO_LONGER_HOLDS
    ):
        errors.append(f"[cites] {fault}")
    for fault in _stale_gone_errors(lines, named_by_the_pipeline):
        errors.append(f"[cites] {fault}")
    for fault in _stale_owed_errors(named_by_the_pipeline, named_anywhere):
        errors.append(f"[cites] {fault}")
    # The prose beside the code, read once and asked both questions. It goes stale the way a document does, and is
    # corrected by even fewer people.
    prose = _code_prose()
    for fault in _numbers_in_code_errors(prose):
        errors.append(f"[count] {fault}")
    for fault in _cited_in_code_errors(prose, named_by_the_pipeline, named_anywhere, named_by_the_code):
        errors.append(f"[cites] {fault}")
    for fault in _readme_gate_errors():
        errors.append(f"[lists] {fault}")
    # The live figures, printed and written down nowhere. A reader wanting a figure runs the gate.
    for name, count in sorted(_measured_for_the_report(stages, final).items()):
        print(f"  {count} {name}")
    gate.report(
        errors,
        "document fault(s). a number depends on the tree, a document narrates its own history, or a cited name names "
        "nothing",
        "documents: a document and a comment state no number the tree decides and narrate no history of their own. "
        "The names they cite are names the tree holds, bar what the declared exemptions allow",
    )


def main() -> None:
    gate.run_deep(_check)


if __name__ == "__main__":
    main()
