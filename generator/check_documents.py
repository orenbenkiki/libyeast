# SPDX-License-Identifier: MIT
"""
Check that the documents and the prose beside the code describe the tree.

`a-count-is-answered-for`. A document states no number the tree decides. `_NOT_A_NUMBER_OF_THE_TREE` says which numbers
may appear. The rule covers `DESIGN.md`, `PLAN.md` and `CHANGELOG.md`, and it covers the prose beside the code too. The
gate prints the live figures on a run. `prose_rules` says what replaces a number.

`a-design-citation-keeps-its-altitude`. A private name cited in `DESIGN.md` repeats the docstring beside that name.
`private_citation_errors` decides which citations are private.

`text-says-what-is`. `DESIGN.md` says what is. `CHANGELOG.md` says what a change did. Neither writes in the tense
`PLAN.md` owns. This module holds `DESIGN.md` and `CHANGELOG.md` to that rule. A comment beside code may use the tense
of `PLAN.md`. A comment names a parse position in that tense.

`every-cited-name-exists` is the half of `every-claim-is-checked-before-it-is-written` a gate can decide. A backticked
name names something in the tree. This module asks that of `DESIGN.md`, `PLAN.md` and `CHANGELOG.md`, and of the prose
beside the code. `CHANGELOG.md` may also cite a name that `_NAMES_THE_TREE_NO_LONGER_HOLDS` declares gone. An entry
naming what a change took away is the entry doing its job.

`a-declared-exception-carries-its-reason`. This module holds `_NAMES_THE_TREE_NO_LONGER_HOLDS`, `_NAMES_STILL_OWED` and
`_NAMES_A_FILE_MAY_CITE_UNANSWERED` in both directions. A declaration the tree has outgrown fails as loudly as a missing
one.

A single checker reads a document and a comment alike. `_joined` flattens either to the line that holds the count.
`_AS_DIGITS` writes a word numeral as digits.

This module builds the stages of the pipeline the documents describe, and measures the counts off those stages. The
measure reads the grammar and runs the steps. The measure needs no interpreter and no corpus.
"""

import ast
import collections
import functools
import io
import json
import os
import pathlib
import re
import subprocess
import tokenize

from collections.abc import Collection, Container, Mapping, Sequence

import annotated2ir
import check_messages
import gate
import ir
import ir2spec
import normalize
import spaces
import spec_tests
import wire

# The quantities `DESIGN.md` may state, by the words that name them. `DESIGN.md` states a count of the tree. It
# describes what is, and there a magnitude is part of the argument.
#
# `PLAN.md` states none. It says what the tree owes rather than how much of that remains. `CHANGELOG.md` states none
# either. Nobody can check a count in it once the tree has moved.
#
# A count reads `<number> <name>` and no more. Such a count reads as prose and greps as data. That shape lets a gate
# find a count that a reviewer would otherwise hunt for. A number that nobody can find is a number nobody re-derives. A
# quantity worth stating in this document is worth naming here first.
#
# This list does not cover what else the document may count. An invariant has its own name, and the count goes in front
# of that name. `_measured_for_the_report` reads such a count off the pipeline rather than off any list. The list below
# names the quantities no invariant names.
#
# The words come first. The counting below follows them. A quantity's name and its value are separate questions.
# `review_input` asks the name without the value and prepares a review from it. A caller reaching the name through the
# value would build the whole pipeline to learn a list.
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
    `{the words a quantity is named by: what it actually is}`. The gate prints this mapping, and no document writes it.

    A reader runs the gate to get such a figure. This checks nothing against a document. A document states no number for
    it to check.
    """
    invariants = normalize.invariants_by_name()
    # What `verify-spec` compares against the official grammar, read off `ir2spec`'s own sets. The indicator productions
    # stay in, `ir2spec.official` keeping them and rewriting the references.
    base, written = stages[0][1], annotated2ir.written()
    aside = ir2spec.OWN | ir2spec.MARKER_ONLY
    official = [name for name in base if name not in aside]
    # How the guard answers decompose. The bookkeeping group and the compared group do not multiply out, and a group is
    # counted on either side of a line start.
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
    # An invariant is named by its own name, with the count written in front of it as a count here is. A document and
    # the pipeline then go through the same checker, and no second checker can drift from it.
    counts.update({name: len(held.test(final)) for name, held in invariants.items()})
    return counts


# The numbers a text in this project may write. A count of the tree is not among them. The list holds the marker of an
# ordered list, a spec version, and a milestone. It holds an item of the order of work, a section of this document, and
# an effort estimate. It holds a bound the grammar states, a test-suite case, and a value the code compares against.
#
# The list names what may appear. The list does not name what may not appear. A count somebody writes then fails here. A
# wording nobody thought of does not excuse that count. A number in this list depends on nothing in the tree. The gate
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
    # A width or a codepoint. The pattern also takes a literal, and a version of something outside this tree.
    # `zero-width` names a guard that reads without moving.
    r"|(?:zero|0)-width|[0-9]+-bit|[0-9]+[ -]?(?:bits?|bytes?|MB|KB)|\b1024\b|U\+[0-9A-F]{4}|0x[0-9A-Fa-f]+|RFC [0-9]+"
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

    This project wraps a document at the column limit, and a phrase can straddle a wrap. `the fourteen | generator
    gates` is a single count and reads as a pair of lines. A match over a single line misses such a count. `_joined`
    joins the lines and keeps the offsets that say which line a match came from.

    `_joined` strips code spans a line at a time. Stripping them over the whole text would swallow the newline inside a
    span that opens on a line and closes on another. The lines after such a span would get the wrong number.

    `_joined` drops a table row, an ordered list's marker and a document's opening YAML block. `collect_fragments` reads
    that block as data.

    A numeral written in digits stays. `_joined` rewrites a word numeral as its own digits. `_joined` takes the offsets
    after that rewriting rather than before, and they point into the text the patterns read.
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


# The pattern matches a document narrating its own history rather than saying what the tree holds. `It used to be X`
# describes a tree nobody has. A reader holds the present tree and cannot check a past one. A history sentence turns
# false for a reader who lacks that history.
#
# A changelog entry already records a change and needs no history word. `one cap bounds all three` says as much as `one
# cap now bounds them`.
#
# `no longer than` and `any more than` are comparisons, and the pattern passes both.
_NARRATES_ITS_OWN_HISTORY = re.compile(
    r"\bnow\b|\bno longer\b(?! than)|\bused to\b|\bpreviously\b|\bany ?more\b(?! than)", re.IGNORECASE
)


# A check looks for the backticked names of `DESIGN.md` and `PLAN.md` in the places below. A name in backticks is a
# citation. A citation nothing answers describes a thing the tree lacks.
#
# The check skips `CHANGELOG.md`. `CHANGELOG.md` names things a change removed. `OpenMatch`, `ColumnLtGuard` and
# `YS_CODE_UNPARSED` are such names.
#
# The rosters come from `gate`. `an-enumeration-of-the-tree-lives-in-gate` refuses a second glob here.

# The suffixes that name a category. A check reads these suffixes to resolve the shorthand the code and the documents
# share. Both write `PushCode` for `PushCodeAction` and `Look` for `LookGuard`. A citation is no worse for using the
# short form the source uses.
_A_CATEGORY = re.compile(r"(Action|Guard|Tree|Wrapper|Value|Set|State|Part|Call|Prod)$")

# Names that name something outside this tree. They are the ABI libyeast is a drop-in for, and a build shape another
# language writes. They are also the debug view vendored beside the reference parser, and the tools the review workflow
# gives a reader. Last, they are the external programs the vet targets run.
_NAMES_FROM_ELSEWHERE = frozenset(
    {"cdylib", "load_all", "next_token", "yaml2html", "Grep", "Glob", "clang-tidy", "clang-format"}
)

# The hyphenated names `PLAN.md` gives to work still owed. The tree holds none of them. The check beside this list
# refuses a sentence saying such a thing already runs. A name earns its place here where the plan describes the work as
# unwritten. A name comes off the day the pipeline builds the thing, and the pipeline then defines the name.
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
    The names the tree holds though `_NAMES_STILL_OWED` declares them unbuilt.

    This is the other direction of the rule `_NAMES_THE_TREE_NO_LONGER_HOLDS` states. A name here excuses a citation of
    something that does not exist. The day the pipeline builds the name, that excuse hides the citation from the check.
    The name reads as owed for as long as nobody notices. The list's comment says a name `comes off the day it is
    built`. This check says the day has come.
    """
    return [
        f"`NAMES_STILL_OWED` declares `{name}`, and the tree holds it"
        for name in sorted(_NAMES_STILL_OWED)
        if name in named or name.replace("-", "_") in known
    ]


# The names of the tracked files. A question of the tree asks for these, and this module reads them once.
_FILE_NAMES: set[str] = set()


def _file_names() -> set[str]:
    """
    The files the tree holds, named with the extension and without. A citation may write a file name either way.

    This walks the files `git` tracks and no more. A walk of the directory would count the objects under `.git` and the
    build output as files, and a citation of such a name would pass.
    """
    if not _FILE_NAMES:
        listed = subprocess.run(["git", "-C", gate.TREE, "ls-files"], capture_output=True, text=True, check=False)
        if listed.returncode or not listed.stdout.strip():
            raise RuntimeError("the tree holds what `git` tracks, and this is no git working tree")
        for name in listed.stdout.splitlines():
            path = pathlib.PurePosixPath(name)
            _FILE_NAMES.update((path.name, path.stem))
    return _FILE_NAMES


@functools.cache
def _named_in_the_tree() -> frozenset[str]:
    """
    The names the tree writes, with the category suffixes stripped. The shorthand a source writes then resolves too.
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
    return frozenset(known)


def named_by_the_pipeline(stages: Sequence[tuple[str, dict[str, ir.Prod]]]) -> set[str]:
    """
    The hyphenated names a text may cite. A phase, a step and an invariant are such names. A production of any stage is
    another. The values a parameter takes and the codes of the wire go in as well.

    The grammar writes a parameter's value hyphenated, and the prose cites it that way. `annotated2ir` names the context
    `block-in`.

    A text may cite a production that a step mints. `monomorphize` mints `b-l-folded_c_flow-in`, and `PLAN.md` reasons
    over that production.
    """
    named = {step.name for step in normalize.STEPS}
    named |= set(normalize.PHASE_NAMES)
    named |= {held.name for step in normalize.STEPS for held in step.invariants}
    named |= {held.name for held in normalize.OWED}
    named |= {name for _label, grammar in stages for name in grammar}
    return named | set(wire.CODE_CHAR) | set(annotated2ir.CONTEXTS) | _named_by_the_process()


# The file holding the names `named_by_the_pipeline` answers with. `write_cited_names` writes it, and `make regen` runs
# that script.
CITED_NAMES = os.path.join("generator", "cited_names.json")


@functools.cache
def _cited_names() -> frozenset[str]:
    """
    The names the pipeline holds.

    A write-time hook reads the set from a file rather than running the pipeline per edit. `_stale_cited_names_errors`
    holds that file to the pipeline.
    """
    with open(os.path.join(gate.TREE, CITED_NAMES), encoding="utf-8") as handle:
        return frozenset(json.load(handle))


def _stale_cited_names_errors(stages: Sequence[tuple[str, dict[str, ir.Prod]]]) -> list[str]:
    """The ways `CITED_NAMES` disagrees with the pipeline. A disagreement means `make regen` has not run."""
    live = named_by_the_pipeline(stages)
    held = _cited_names()
    return [
        f"`{CITED_NAMES}` {said} `{name}`. run `make regen`."
        for said, gone in (("leaves out", live - held), ("holds a name the pipeline dropped,", held - live))
        for name in sorted(gone)
    ]


def _named_by_the_process() -> set[str]:
    """
    The hyphenated names the process uses. Such a name is a rule of `.claude/conventions.md`, a hook that enforces a
    rule, or a workflow.

    The code that enforces a rule cites the rule by name. `check_conventions` names the rules it decides. A hook names
    the rule it enforces. A workflow takes the name its own `meta` declares. Such a name is no step and no production.
    It names something a reader can open.
    """
    held = set()
    for path in gate.hooks():
        held.add(path.stem)
    for path in gate.workflows():
        held |= set(re.findall(r"name:\s*'([a-z][a-z0-9-]*)'", path.read_text(encoding="utf-8")))
    held |= set(gate.conventions())
    return held


# A hyphenated name, in the shape a citation takes. A segment past the first holds a letter. That letter tells a name
# from the arithmetic a comment writes the same way. `n-1` is a column and not a count to justify.
#
# The first segment may be a single letter, as it is in a production of the grammar. `c-printable` and `s-white` begin
# with a letter. So do `l-yaml-stream` and `b-break`. A rule wanting a longer first segment would be blind to the
# productions the documents cite.
#
# A segment may hold an underscore. That is how a monomorphized copy writes the arguments moved into its name, as in
# `b-l-folded_c_flow-in`. The backticked-identifier rule does not match such a name either.
_A_HYPHENATED_NAME = re.compile(r"`([a-z][a-z0-9_]*(?:-[a-z0-9_+]*[a-z+][a-z0-9_+]*)+)`")

_A_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # An identifier, in the shape the tree writes.

# A name the `Makefile` declares. That is a variable or a target at a line's start.
_A_MAKE_DECLARATION = re.compile(r"^([A-Za-z0-9_./%-]+)\s*(?::(?!=)|[:?+]?=)", re.MULTILINE)

# A name as a text cites it. The name is in backticks, and has a dot where it names a member of something. The dot tells
# a citation from an identifier.
_A_CITED_NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")


def private_citation_errors(where: str, lines: _Lines) -> list[str]:
    """
    The places a text cites a name its module keeps to itself.

    The function is public. The `altitude` hook calls the function on a sentence as the writer writes that sentence.

    The function looks for a leading underscore on the name or on a part of a dotted name. The source writes that
    underscore to say a reader outside the module has no business with the name.
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

    A name for work still owed is fine. A sentence claiming the thing already runs is a fault. Whether the tree holds
    the name tells the pair apart.

    A name whose underscore form the tree writes counts too. The same transformation is a step called
    `lower-continuations-into-conflicts` and a function called `_lower_continuations_into_conflicts`. Prose citing
    either is citing the thing that exists.

    A `verify-`, `vet-` or `gh-` name is no citation of this kind, and this passes it. Such a name is a `Makefile`
    target, and the `Makefile` defines it. `_readme_gate_errors` holds `README.md`'s roster to the gates a target runs.
    """
    faults = []
    for number, line in lines:
        for cited in _A_HYPHENATED_NAME.findall(line):
            if cited in named or cited in _NAMES_STILL_OWED or cited.startswith(("verify-", "vet-", "gh-")):
                continue
            if _does_answer(known, cited.replace("-", "_")) or cited in gone:
                continue
            # A name from outside names nothing here, under any check that reaches it. `_A_NAME` splits a hyphenated
            # name. The tree cannot be said to write `clang-tidy` however often a recipe runs it.
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

    `is_a_bare_word_a_citation` is false for the prose beside the code and true for a document. A false answer keeps a
    citation whose shape belongs to the tree. That is a name with an underscore, or a dotted path.

    Code prose backticks a shell command, a value written as a string, and a placeholder in a worked example. Those name
    nothing, and they read as a bare word.

    A rename leaves a name with an underscore behind. A docstring may still name the private name a function held before
    a rename. This function reports that docstring.
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
# took away is the point of an entry. A citation naming nothing is correct in `CHANGELOG.md`. The same citation in
# `DESIGN.md` or `PLAN.md` is a fault.
#
# The list is for an entry of another kind. Such an entry describes the mechanism as it is and names that mechanism
# wrongly. Such an entry reads as history, and nobody checks it. Somebody wrote `Step`'s fields here under a name the
# type did not have. The invariant report gave a name that named nothing. The claims the pipeline makes sat under
# invariant names that named nothing. Such names looked exactly like the legitimate citations around them.
#
# So this list declares the names an entry writes. The check refuses a name the list leaves out. The tree may hold a
# name of this list again. The check reports such a name as a stale declaration, as it reports the other exemptions of
# the tree.
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
    whether the tree writes it anywhere. `_named_in_the_tree` counts a name written in a string or a comment. That is
    right for a citation, where the tree writes the name. It is wrong here. `_NAMES_THE_TREE_NO_LONGER_HOLDS` is itself
    a list of names written as strings. The permissive checker answers that the tree still holds a name that list
    declares gone.

    This reads the C too, and reads it differently. The C gives the identifiers its code writes, with the comments and
    the strings taken out. A C name has no top level to walk the way a module's does. The question is whether the tree
    still holds the name at all. The code writing it answers that. The C may go on defining a symbol the list declares
    gone. Without the C, no check reports that declaration stale. The same name in `DESIGN.md` resolves against `src/`
    perfectly well.
    """
    return set(_file_names()) | _named_by_the_c() | gate.names_defined_in_modules()


def _named_by_the_c() -> set[str]:
    """
    The identifiers the C writes, with the comments and the string literals taken out.

    The comments go first. A quotation mark inside a comment would otherwise open a literal. Blanking the literals would
    then take out the code up to the next quotation mark.
    """
    found: set[str] = set()
    for path in gate.c_sources():
        code = _strings_blanked(_A_C_COMMENT.sub(" ", path.read_text(encoding="utf-8")))
        found |= set(_A_NAME.findall(code))
    return found


@functools.cache
def _named_by_the_code() -> frozenset[str]:
    """
    The names the tree's code writes, with the prose beside that code left out. Those are the Python's `NAME` tokens,
    the C's identifiers, and the files the tree holds.

    These answer a citation written beside the code. `_named_in_the_tree` cannot answer it. That checker counts a name
    written in a comment. A docstring citing a name is then its own witness that the name exists. A rename leaving a
    name behind reads exactly like a rename that left none.

    `_defined_in_the_tree` is too narrow the other way. Prose beside the code cites a parameter, a method and an
    attribute as readily as a top-level definition. A module binds no such name. This function admits such a name and
    still refuses a name that appears only in the prose.
    """
    # A name a declaration says names nothing. Each is written as a string in the declaration itself. The harvest below
    # would take that string as the answer, and this check would go blind to the name everywhere.
    declared = set(_NAMES_THE_TREE_NO_LONGER_HOLDS) | set(_NAMES_STILL_OWED)
    declared |= {one for names in _NAMES_A_FILE_MAY_CITE_UNANSWERED.values() for one in names}
    found = set(_file_names()) | _named_by_the_c() | set(check_messages.codes())
    # The names the `Makefile` declares. A recipe's prose cites a variable there the way it cites a function.
    found |= set(_A_MAKE_DECLARATION.findall(pathlib.Path(gate.TREE, "Makefile").read_text(encoding="utf-8")))
    for path in gate.modules() + gate.hook_modules():
        found |= names_written_by(path.read_text(encoding="utf-8"))
    return frozenset(found)


def names_written_by(source: str) -> set[str]:
    """
    The names the code of `source` writes.

    A declaration writes a gone name as a string. The harvest drops that string. The check then still sees the name as
    gone.

    The harvest keeps a string shaped like a name. `before_mark` is a region a retype names. `text` is a code a token
    holds.
    """
    declared = set(_NAMES_THE_TREE_NO_LONGER_HOLDS) | set(_NAMES_STILL_OWED)
    declared |= {one for names in _NAMES_A_FILE_MAY_CITE_UNANSWERED.values() for one in names}
    found = {
        held.string for held in tokenize.generate_tokens(io.StringIO(source).readline) if held.type == tokenize.NAME
    }
    for held in ast.walk(ast.parse(source)):
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
        f"`NAMES_THE_TREE_NO_LONGER_HOLDS` declares `{name}`, and `CHANGELOG.md` does not cite it"
        for name in sorted(_NAMES_THE_TREE_NO_LONGER_HOLDS - cited)
    ]


def _document_lines(document: str) -> _Lines:
    """`[(the line's number, what it says)]` over the lines of `document`."""
    with open(os.path.join(gate.TREE, document), encoding="utf-8") as handle:
        return list(enumerate(handle.read().splitlines(), start=1))


def _code_prose() -> dict[str, _Lines]:
    """
    The prose beside the code, keyed by the file that holds it. That is the comments and docstrings of `generator/` and
    `scripts/`. `_c_prose` adds the comments of the C at the end.

    A comment and a docstring are one thing here. Anything outside them is code, where a number is a literal and a
    hyphenated string reads as grammar text rather than as a citation.

    This takes the comments from `tokenize` rather than from the lines that start with a `#`. A comment sharing its line
    with code then reads like any other. A trailing comment contributes its own text and not the code in front.
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
        found[str(path.relative_to(gate.TREE))] = sorted(said.items())
    found.update(_c_prose())
    found.update(_marked_prose())
    return found


# The files the rest of the project's prose lives in, by the marker its comments use. `is_prose_of_the_project` names
# the suffixes of these files, and the write-time hooks read those files.
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
            prose[str(path.relative_to(gate.TREE))] = sorted(said.items())
    return prose


# A `//` comment, and a `/* */` comment. Either holds the text after the marker. Doxygen's `///` and `///<` are the same
# comment with a marker of their own, and `lstrip` takes the whole marker.
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



    This takes a comment's text and not the line it shares, as in the Python. The code beside it stays code.
    `_strings_blanked` runs first, and a `//` inside a string then reads as no comment.
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
        prose[str(path.relative_to(gate.TREE))] = sorted(said.items())
    return prose


# The names a file's prose may cite that name nothing in the tree, keyed by the file writing them. An entry says why
# below.
#
# The scope is the file rather than the name. Elsewhere the name is exactly what this hunts. Exempting `speculate-folds`
# outright would blind the check to a docstring citing it as a stage of the pipeline. This check reports such a
# docstring as a fault. A document may cite none of these. `DESIGN` and `PLAN` describe the tree the project holds.
_NAMES_A_FILE_MAY_CITE_UNANSWERED = {
    # Names this file cites as gone. Such a name outlived the code holding it. Naming such a name is how the prose
    # beside a check says what the check is about. `zero-width` names a guard rather than a step.
    "check_documents.py": frozenset({
        "speculate-folds", "YS_CODE_UNPARSED", "zero-width",
    }),  # fmt: skip
    # Names a worked example invents for the shape it shows. `Namer` shows what minting a helper for `foo` comes to, and
    # `_set_name` shows what that transformation makes of a set's name.
    "normalize.py": frozenset({"foo_1", "foo_2", "foo_3", "foo_4", "foo_3_1"}),
    "grammar2decoder.py": frozenset({"NS_PLAIN_SAFE_IN"}),
    # The header the state dispatcher goes into. `PLAN.md` owes that header, and the generator writes it the day the
    # dispatcher lands.
    "parser.h": frozenset({"parser_tables.h"}),
    # A name a worked example invents for the suffixes a monomorphic copy and a minted helper take.
    "check_grammar_coverage.py": frozenset({"foo_c_flow-in_1"}),
}


# A numeral, written in digits or in words. `_AS_DIGITS` gives the words.
#
# `_NOT_A_NUMBER_OF_THE_TREE` lists what may pass, and this refuses a numeral outside that list. A determiner reads as a
# numeral here. `one way of a choice` and `both halves` go in the list with their reason.
_A_NUMBER = re.compile(r"\b(?:[0-9]+|" + "|".join(_AS_DIGITS) + r")\b", re.IGNORECASE)


def is_prose_of_the_project(path: str) -> bool:
    """
    Whether the rules on prose hold for `path`.

    A path outside the tree holds prose of somebody else. `gate.NOT_OURS` names the data of this tree, and `gate.UNREAD`
    names the file the checkers leave alone. Any other path is prose of the project.

    A path nobody has added yet answers yes.
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
# The checker drops the token. It reads the prose sharing the line, and checks a claim in that prose like any other.
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
    limit, and a number may straddle a wrap.
    """
    faults = []
    for name, lines in prose.items():
        held = dict(lines)
        said, starts = _joined("\n".join(held.get(at, "") for at in range(1, max(held, default=0) + 1)))
        for found in _numbers_of_the_tree(said):
            at = _line_of(starts, found.start())
            faults.append(f"{name}:{at} states {found.group(0).strip()!r}. that number depends on the tree.")
    return faults


# The form the checkers above give a text's citation.
_A_FAULTED_CITATION = re.compile(r"cites `([^`]+)`")


def _cited_by(fault: str) -> str:
    """The name a dangling-citation fault names as the citation."""
    found = _A_FAULTED_CITATION.search(fault)
    if found is None:
        raise ValueError(f"a dangling-citation fault naming nothing cited: {fault!r}")
    return found.group(1)


# The documents the citation rules read. A file outside this tuple and outside `_code_prose` gets no citation rule.
_DOCUMENTS_CITED = ("DESIGN.md", "PLAN.md", "CHANGELOG.md")


# The directories holding the code whose prose the citation rule reads. `_code_prose` hands over the comments and the
# docstrings of a file under one of these. `_named_by_the_code` gathers names from the same directories. A file outside
# them cites a name no harvest holds.
_A_SOURCE_SITS_UNDER = ("generator/", "scripts/", ".claude/hooks/", "src/", "include/")


def _citations(path: str, lines: _Lines, minted: Collection[str] = frozenset()) -> list[str]:
    """
    The names `lines` cite that name nothing, with the declared excuses left in.

    A document and the prose beside the code take different name sets. `DESIGN.md` cites a production, and a bare word
    there is a citation. A docstring cites what the code writes, and a bare word there is prose. A file outside both
    lists gets no citation rule.

    `minted` holds the names a text writes that the tree lacks. A write-time caller fills it from the source the edit
    leaves. A gate over the tree leaves it empty, the tree on disk already holding those names.
    """
    name = os.path.basename(path)
    known = frozenset(_named_in_the_tree()) | frozenset(minted)
    if name in _DOCUMENTS_CITED:
        gone = _NAMES_THE_TREE_NO_LONGER_HOLDS if name == "CHANGELOG.md" else frozenset()
        return _dangling_name_errors(name, lines, known, gone) + _dangling_step_errors(
            name, lines, _cited_names(), known, gone
        )
    if not any(one in path for one in _A_SOURCE_SITS_UNDER):
        return []
    return _dangling_step_errors(name, lines, _cited_names(), known) + _dangling_name_errors(
        name, lines, frozenset(_named_by_the_code()) | frozenset(minted), is_a_bare_word_a_citation=False
    )


def _citation_errors(path: str, lines: _Lines, minted: Collection[str] = frozenset()) -> list[str]:
    """
    The names `lines` cite that name nothing and that no declared excuse covers.

    `_NAMES_A_FILE_MAY_CITE_UNANSWERED` excuses a name for a file, and this reads that list.
    """
    excused = _NAMES_A_FILE_MAY_CITE_UNANSWERED.get(os.path.basename(path), frozenset())
    return [fault for fault in _citations(path, lines, minted) if _cited_by(fault) not in excused]


def citation_refusal(path: str, lines: _Lines, minted: Collection[str] = frozenset()) -> str | None:
    """
    The refusal the citation rule gives the prose beside the code at `path`, or None where the citations pass.

    A write-time hook asks here, and `_cited_in_code_errors` asks here for the gate. Both readers therefore refuse the
    same citation.

    `minted` holds the names the edit itself writes. A hook fills it. A docstring may then cite a name the same edit
    adds.
    """
    found = _citation_errors(path, lines, minted)
    if not found:
        return None
    return (
        f"{path} cites a name the tree does not hold:\n\n"
        + "\n".join(f"  {fault}" for fault in found)
        + "\n\nA name inside backticks names something in the tree. A name that outlived its definition reads as "
        "a thing that exists. Take the citation out, or cite the name the tree holds.\n\n"
        "A file under `.git` is no name of the tree. Say what the file holds instead of naming it.\n\n"
        "Rule: every-cited-name-exists."
    )


def _cited_in_code_errors(prose: Mapping[str, _Lines]) -> list[str]:
    """
    The places the prose beside the code cites a name that names nothing. `_code_prose` hands over that prose from the
    generator's comments and docstrings and from the C's comments.

    This asks of the prose beside the code what the other checks ask of the documents. A name outliving its definition
    reads as a thing that exists.

    `_NAMES_A_FILE_MAY_CITE_UNANSWERED` declares the names this checker excuses. `_citations` reads the prose with
    nothing excused. A declaration takes a fault out, and a declaration that takes none out reads as stale.
    """
    by_file: dict[str, list[str]] = {}
    for name, lines in prose.items():
        by_file.setdefault(os.path.basename(name), []).extend(_citations(name, lines))
    faults = [fault for name, lines in prose.items() for fault in _citation_errors(name, lines)]
    for name, gone in sorted(_NAMES_A_FILE_MAY_CITE_UNANSWERED.items()):
        cited = {_cited_by(fault) for fault in by_file.get(name, [])}
        faults += [
            f"`NAMES_A_FILE_MAY_CITE_UNANSWERED` declares `{one}` for {name}. nothing there wants the excuse."
            for one in sorted(gone - cited)
        ]
    return faults


# The words a document may not say, by the tense they keep. `PLAN.md` states the work owed. `DESIGN.md` describes the
# mechanism. `CHANGELOG.md` records a change. A tense is a word, and this matches on the word.
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


# The documents that keep a tense. `_OUT_OF_ITS_DOMAIN_IN` says which tense a document keeps.
_TENSED = ("DESIGN.md", "PLAN.md", "CHANGELOG.md")


def domain_errors(document: str, text: str) -> list[str]:
    """
    The places `text` writes in a tense another document owns. `text` is the text of `document`.

    Public. The `document_rules` checker reads an edit through here, and this gate reads a document through here.
    """
    held = _OUT_OF_ITS_DOMAIN_IN.get(document)
    if held is None:
        return []
    pattern, why = held
    said, starts = _joined(text)
    return [
        f"{document}:{_line_of(starts, found.start())} says {found.group(0)!r}, and {why}"
        for found in pattern.finditer(said)
    ]


def history_errors(document: str, text: str) -> list[str]:
    """
    The places `text` narrates its own history rather than describing the tree. `text` is the text of `document`. A
    document outside `_TENSED` takes no such reading.

    Public. The `document_rules` checker reads an edit through here, and this gate reads a document through here.
    """
    if document not in _TENSED:
        return []
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
    entire, and the ticks go with it. A code span holds a literal the grammar or the C writes.
    """
    with open(os.path.join(gate.TREE, document), encoding="utf-8") as handle:
        said, starts = _joined(handle.read())
    return [
        f"{document}:{_line_of(starts, found.start())} states {found.group(0)!r}. that number depends on the tree."
        for found in _numbers_of_the_tree(said)
    ]


# The targets whose rosters `README.md` gives. The `Makefile` decides what a target runs, and `README.md` follows that
# decision rather than the other way about.
_ROSTERS = ("verify", "vet", "vet-format")

# The gates `README.md` lists under a roster whose target does not name them. A gate the target names runs the gates
# this list declares. `verify-grammar` runs the pair of gates `README.md` lists before it. `vet` names `vet-format`, and
# `vet-format` runs the formatters. This list is a declaration, like the other exemptions here. README may grow a list
# that nothing runs. Without the declaration, that list would read exactly like this one.
_GATES_UNDER_ANOTHER = {
    "verify": frozenset({"verify-grammar-base", "verify-grammar-base-coverage"}),
    "vet": frozenset({
        "vet-format-c", "vet-format-md", "vet-format-py", "vet-format-cmake", "vet-format-sh", "vet-format-make",
    }),  # fmt: skip
    "vet-format": frozenset(),
}


def readme_roster_errors(document: str, text: str) -> list[str]:
    """
    The disagreements between the rosters `README.md` lists and the `Makefile`, where `document` is either file and
    holds `text`. Any other document takes no such reading.

    Public. The `document_rules` checker reads an edit through here, and this gate reads the tree through here.
    """
    if document not in ("README.md", "Makefile"):
        return []
    held = {name: pathlib.Path(gate.TREE, name).read_text(encoding="utf-8") for name in ("README.md", "Makefile")}
    held[document] = text
    return _readme_gate_errors(held["Makefile"], held["README.md"])


def _readme_gate_errors(makefile: str, readme: str) -> list[str]:
    """
    The disagreements between `README.md`'s list of the gates a roster target runs and the `Makefile`'s target.

    A list of the gates is a roster. A roster kept by hand goes stale the first time somebody adds a gate. This roster
    went stale, and `vet-format` went stale after it. Kept mechanically, a roster is a list a reader can trust. Kept by
    hand, a roster says the target runs less than the target does.

    Both sides write a roster out in full, a name at a time. README wrote the formatters as a line of suffixes,
    `vet-format-c` then `-md` then `-py`. That reads perfectly well. A checker cannot answer such a line.
    """
    faults = []
    for target in _ROSTERS:
        said = re.search(rf"^{re.escape(target)}:((?:[^\n]*\\\n)*[^\n]*)", makefile, re.M)
        if said is None:
            faults.append(f"Makefile: no `{target}:` target is here, and a reader cannot tell what README should list")
            continue
        gate_name = re.compile(rf"\b{re.escape(target)}-[a-z-]+")
        runs, listed = set(gate_name.findall(said.group(1))), set(gate_name.findall(readme))
        faults += [
            f"`README.md` does not list `make {name}`. `make {target}` runs it." for name in sorted(runs - listed)
        ]
        faults += [
            f"`README.md` lists `make {name}`. `make {target}` does not run it."
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
    for document in _TENSED:
        text = pathlib.Path(gate.TREE, document).read_text(encoding="utf-8")
        errors += [f"[says-what-is] {fault}" for fault in history_errors(document, text)]
        errors += [f"[domain] {fault}" for fault in domain_errors(document, text)]
    # Asked of `DESIGN.md` and `CHANGELOG.md`. A checker's name says which question it asks, and that question is the
    # whole meaning of the checker.
    named_anywhere = _named_in_the_tree()
    named_by_a_stage = _cited_names()
    for fault in _stale_cited_names_errors(stages):
        errors.append(f"[cites] {fault}")
    for document in ("DESIGN.md", "PLAN.md"):
        lines = _document_lines(document)
        for fault in _dangling_name_errors(document, lines, named_anywhere) + _dangling_step_errors(
            document, lines, named_by_a_stage, named_anywhere
        ):
            errors.append(f"[cites] {fault}")
    # Altitude. `DESIGN.md` describes the architecture, and a private name belongs to its module.
    for fault in private_citation_errors("DESIGN.md", _document_lines("DESIGN.md")):
        errors.append(f"[altitude] {fault}")
    # The same names cover `CHANGELOG.md`, less what that file declares gone. An entry naming what a change took away is
    # the entry doing its job.
    lines = _document_lines("CHANGELOG.md")
    for fault in _dangling_name_errors(
        "CHANGELOG.md", lines, named_anywhere, _NAMES_THE_TREE_NO_LONGER_HOLDS
    ) + _dangling_step_errors("CHANGELOG.md", lines, named_by_a_stage, named_anywhere, _NAMES_THE_TREE_NO_LONGER_HOLDS):
        errors.append(f"[cites] {fault}")
    for fault in _stale_gone_errors(lines, named_by_a_stage):
        errors.append(f"[cites] {fault}")
    for fault in _stale_owed_errors(named_by_a_stage, named_anywhere):
        errors.append(f"[cites] {fault}")
    # The prose beside the code, read once and asked both questions. It goes stale the way a document does, and is
    # corrected by even fewer people.
    prose = _code_prose()
    for fault in _numbers_in_code_errors(prose):
        errors.append(f"[count] {fault}")
    for fault in _cited_in_code_errors(prose):
        errors.append(f"[cites] {fault}")
    readme = pathlib.Path(gate.TREE, "README.md").read_text(encoding="utf-8")
    errors += [f"[lists] {fault}" for fault in readme_roster_errors("README.md", readme)]
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
