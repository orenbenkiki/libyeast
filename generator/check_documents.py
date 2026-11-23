# SPDX-License-Identifier: MIT
"""
The readings that tell whether the documents and the prose beside the code describe the tree.

The checkers of `checkers` ask through these readings. This gate holds the declarations those readings excuse.

`a-count-is-answered-for`. A document states no number the tree decides. `_NOT_A_NUMBER_OF_THE_TREE` says which numbers
may appear. The rule covers `DESIGN.md`, `PLAN.md` and `CHANGELOG.md`, and it covers the prose beside the code too. The
`no_tree_numbers` checker applies the rule. The gate prints the live figures on a run. `prose_rules` says what replaces
a number.

`a-design-citation-keeps-its-altitude` of `.claude/conventions.md`. `private_citation_errors` decides which citations
are private.

`a-design-paragraph-copies-no-cited-prose`. A paragraph of `DESIGN.md` shares no long run of words with the prose of a
fragment it cites. `copied_prose_errors` finds such a run.

`no-prose-points-at-the-plan`. A file that neither checks the plan nor states its rules names no `PLAN.md`.
`plan_pointer_errors` finds such a name.

`text-says-what-is`. `DESIGN.md` says what is. `CHANGELOG.md` says what a change did. Neither writes in the tense
`PLAN.md` owns. `domain_errors` holds `DESIGN.md` and `CHANGELOG.md` to that rule. `history_errors` refuses a word
narrating history in `DESIGN.md`, `PLAN.md` and `CHANGELOG.md`.

`every-cited-name-exists` is the half of `every-claim-is-checked-before-it-is-written` a gate can decide. A backticked
name names something in the tree. `citation_refusal` asks that of `DESIGN.md`, `PLAN.md` and `CHANGELOG.md`, and of the
prose beside the code. `CHANGELOG.md` may also cite a name that `_NAMES_THE_TREE_NO_LONGER_HOLDS` declares gone. An
entry naming what a change took away is the entry doing its job.

`an-owed-name-lives-while-the-plan-owes-it`. `_unplanned_owed_errors` refuses an owed name `PLAN.md` does not cite.
`_stale_owed_errors` refuses an owed name the tree holds.

`a-declared-exception-carries-its-reason`. This module holds `_NAMES_THE_TREE_NO_LONGER_HOLDS`, `_NAMES_STILL_OWED` and
`_ABSENT_NAMES_A_FILE_MAY_CITE` in both directions. A declaration the tree has outgrown fails as loudly as a missing
one. `_stale_elsewhere_errors` holds `_NAMES_FROM_ELSEWHERE` to the texts that cite its names.

A single checker reads a document and a comment alike. `_joined` flattens either into a single line, and `_line_of` maps
an offset in that line back to the line of the text. `_AS_DIGITS` writes a word numeral as digits.

This module builds the stages of the pipeline the documents describe, and measures the counts off those stages. The
measure reads the grammar and runs the steps. The measure needs no interpreter. It counts the conformance fixtures
`spec_tests.load` lists.
"""

import ast
import collections
import difflib
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
import prose_rules
import spaces
import spec_tests
import wire

# The quantities the gate prints, by the words that name them. `no_tree_numbers.refusal_for` refuses such a count in a
# document and in a comment alike.
#
# The gate prints a count as `<number> <name>`. A reader wanting a figure runs the gate.
#
# This list does not cover the invariants the gate also counts. An invariant has its own name, and the count goes in
# front of that name. `_measured_for_the_report` reads such a count off the pipeline rather than off any list. The list
# below names the quantities no invariant names.
#
# The words come first. The counting below follows them. `_measured_for_the_report` refuses a count whose name this list
# does not hold.
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

    A reader runs the gate to get such a figure. This checks nothing against a document. `no_tree_numbers.refusal_for`
    refuses a count in a document.
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


# The numbers a text in this project may write. A count of the tree is not among them. The list holds a spec version and
# a milestone. It holds an item of the order of work, a section of this document, and an effort estimate. It holds a
# bound the grammar states, a test-suite case, and a value the code compares against.
#
# The list names what may appear. The list does not name what may not appear. A count somebody writes then fails here. A
# wording nobody thought of does not excuse that count. A number in this list depends on nothing in the tree. The gate
# refuses a number that does depend on the tree, in a document and in a comment alike. A measurement copied into prose
# is a copy nobody re-measures.
#
# The documents and the prose beside the code share this list.
_NOT_A_NUMBER_OF_THE_TREE = re.compile(
    # The specification, the vendored grammar, and what either of them numbers.
    r"YAML 1\.[0-9]|libyaml/1\.[0-9]|spec-1\.[0-9]|\b1\.[0-9] quirks|section [0-9.]+|\brules? [0-9]+(?:/[0-9]+)?"
    # The structure of the plan. It numbers items rather than counting anything.
    r"|[Mm]ilestones? [0-9]+(?: and [0-9]+)?|\bitems? [0-9]+|[Pp]hases? [0-9]+|~[0-9]+-[0-9]+ (?:mo|wks?)"
    # A position or a bound the format itself fixes. This project sets the column limit, and the limit moves with
    # nothing.
    r"|\(max\)[: ]+[0-9]+|\bcolumn [0-9]+|[0-9]+ columns?|\bindentation of [0-9]+|[0-9]+ or more|[0-9]+ or -[0-9]"
    r"|UTF-[0-9]+"
    # A width or a codepoint. The pattern also takes a literal, and a version of something outside this tree. zero-width
    # names a guard that reads without moving. `ir.LookGuard` is such a guard.
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


# A row of a markdown table. `_joined` drops such a row before the count patterns read it. `_joined` keeps a heading,
# and the count patterns read it like any other line. A count stated in a heading counts the tree as much as a count
# stated in a sentence.
_A_TABLE_ROW = re.compile(r"^\s*\|")

# The fence around the YAML a document may open with. An agent definition names itself and its tools there, as
# https://code.claude.com/docs/en/sub-agents says. Those fields are data.
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
# citation. A citation of a name the tree lacks describes a thing that is not there.
#
# The check reads `CHANGELOG.md` too. `CHANGELOG.md` may also cite a name a change removed.
# `_NAMES_THE_TREE_NO_LONGER_HOLDS` declares such names, `CloseMatch` and `ColumnLeGuard` among them.
#
# The rosters come from `gate`. `an-enumeration-of-the-tree-lives-in-gate` of `.claude/conventions.md` refuses a second
# glob here.

# The suffixes that name a category. A check reads these suffixes to resolve the shorthand the code and the documents
# share. `lower-tokens` writes `PushCodeAction` in full. `normalize._split_consumes_into_gates` writes `Look` for
# `LookGuard`. A citation is no worse for using the short form the source uses.
_A_CATEGORY = re.compile(r"(Action|Guard|Tree|Wrapper|Value|Set|State|Part|Call|Prod)$")

# Names that name something outside this tree. `load_all` names the ABI that the milestone `Milestone 05 - ABI layer` of
# `PLAN.md` plans. `next_token` and `cdylib` are names the milestone `Milestone 04 - C codegen` of `PLAN.md` uses. The
# milestone `Milestone 04 - C codegen` of `PLAN.md` plans to migrate `yaml2html` into the package. That milestone
# describes `yaml2html` as a C program that renders the yeast stream as colorized HTML. `Grep` and `Glob` are among the
# tools the review workflow gives a reader in `.claude/agents/reader.md`. `clang-tidy` and `clang-format` are the
# external programs the vet targets of `Makefile` run. `LANG` names the locale variable Python decodes a file by, as
# https://docs.python.org/3/library/functions.html#open says. `BASH_ENV` names the startup file a non-interactive bash
# sources, as https://www.gnu.org/software/bash/manual/html_node/Bash-Startup-Files.html says.
_NAMES_FROM_ELSEWHERE = frozenset(
    {"cdylib", "load_all", "next_token", "yaml2html", "Grep", "Glob", "clang-tidy", "clang-format", "LANG", "BASH_ENV"}
)

# The hyphenated names of work `PLAN.md` still owes. The tree holds none of them. `_stale_owed_errors` refuses a name
# here that the tree holds. `_unplanned_owed_errors` refuses a name here that `PLAN.md` does not cite.
# `_dangling_step_errors` passes a citation of a name here in `PLAN.md` and refuses it in any other text.
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
    The faults naming the names the tree holds though `_NAMES_STILL_OWED` declares them unbuilt.

    `_stale_gone_errors` makes the same check of `_NAMES_THE_TREE_NO_LONGER_HOLDS`. The rule
    `an-owed-name-lives-while-the-plan-owes-it` says a name `comes off the day the pipeline builds the thing`.
    """
    return [
        f"`NAMES_STILL_OWED` declares `{name}`, and the tree holds it"
        for name in sorted(_NAMES_STILL_OWED)
        if name in named or name.replace("-", "_") in known
    ]


def _unplanned_owed_errors(plan: _Lines) -> list[str]:
    """
    The errors for the names `_NAMES_STILL_OWED` declares that `plan` does not cite. `plan` holds the lines of
    `PLAN.md`.

    A name stays owed while the plan holds its work. A plan that drops the work leaves the name excusing a citation of
    something nobody will build.
    """
    said = "\n".join(line for _number, line in plan)
    return [
        f"`NAMES_STILL_OWED` declares `{name}`, and `PLAN.md` does not cite it"
        for name in sorted(_NAMES_STILL_OWED)
        if f"`{name}`" not in said
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
    The names the tree writes, both as written and with the category suffixes stripped. The shorthand a source writes
    then resolves too.
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
    another. The values of the context parameter `c` and the codes of the wire go in as well. So do the names the
    process uses.

    The grammar writes a parameter's value hyphenated, and the prose cites it that way. `annotated2ir` names the context
    `block-in`.

    A text may cite a production that a step mints. `monomorphize` mints `b-l-folded_c_flow-in`.
    """
    named = {step.name for step in normalize.STEPS}
    named |= set(normalize.PHASE_NAMES)
    named |= {held.name for step in normalize.STEPS for held in step.invariants}
    named |= {held.name for held in normalize.OWED}
    named |= {name for _label, grammar in stages for name in grammar}
    return named | set(wire.CODE_CHAR) | set(annotated2ir.CONTEXTS) | _named_by_the_process()


# The file holding the names `named_by_the_pipeline` answers with. `write_cited_names` writes it, and the `regen` target
# of `Makefile` runs that script.
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
    """
    The ways `CITED_NAMES` disagrees with the pipeline. A disagreement means the `regen` target of `Makefile` has not
    run.
    """
    live = named_by_the_pipeline(stages)
    held = _cited_names()
    return [
        f"`{CITED_NAMES}` {said} `{name}`. run `make regen`."
        for said, gone in (("leaves out", live - held), ("holds a name the pipeline dropped,", held - live))
        for name in sorted(gone)
    ]


def _named_by_the_process() -> set[str]:
    """
    The names the process uses. Such a name is a rule of `.claude/conventions.md`, the stem of a file under
    `.claude/hooks`, or a workflow.

    The code that enforces a rule cites the rule by name. `check_conventions` names the rules it decides. A hook names
    the rule it enforces. A workflow file contributes the values of its single-quoted `name:` fields.
    `.claude/workflows/pre-commit-review.js` declares `pre-commit-review` in its `meta`.
    """
    held = set()
    for path in gate.hooks():
        held.add(path.stem)
    for path in gate.workflows():
        held |= set(re.findall(r"name:\s*'([a-z][a-z0-9-]*)'", path.read_text(encoding="utf-8")))
    held |= set(gate.conventions())
    return held


# A hyphenated name, in the shape a citation takes. A segment past the first holds a letter or a `+`. That character
# tells a name from the arithmetic a comment writes the same way. `n-1` is a column and not a count to justify.
#
# The first segment may be a single letter, as it is in a production of the grammar. `c-printable` and `s-white` begin
# with a letter. So do `l-yaml-stream` and `b-break`. A rule wanting a longer first segment would be blind to the
# productions the documents cite.
#
# A segment may hold an underscore. That is how a monomorphized copy writes the arguments moved into its name, as in
# `b-l-folded_c_flow-in`. `ir.specialized` writes such a name. The backticked-identifier rule does not match such a name
# either.
_A_HYPHENATED_NAME = re.compile(r"`([a-z][a-z0-9_]*(?:-[a-z0-9_+]*[a-z+][a-z0-9_+]*)+)`")

_A_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # An identifier, in the shape the tree writes.

# A name the `Makefile` declares. That is a variable or a target at a line's start.
_A_MAKE_DECLARATION = re.compile(r"^([A-Za-z0-9_./%-]+)\s*(?::(?!=)|[:?+]?=)", re.MULTILINE)

# A name as a text cites it. The name is in backticks, and has a dot where it names a member of something. The pattern
# matches a backticked name with or without a dot.
_A_CITED_NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")


def private_citation_errors(where: str, lines: _Lines) -> list[str]:
    """
    The places a text cites a name its module keeps to itself.

    The function is public. The `altitude` hook calls the function on the prose of an edit to `DESIGN.md`.

    The function looks for a leading underscore on the name or on a part of a dotted name.
    """
    faults = []
    for number, line in lines:
        for cited in _A_CITED_NAME.findall(line):
            if any(part.startswith("_") for part in cited.split(".")):
                faults.append(f"{where}:{number} cites `{cited}`. that name belongs to its module.")
    return faults


# The longest run of words `DESIGN.md` may share with the prose of a name it cites. A longer run copies that prose.
_MOST_SHARED_WORDS = 7

# A word, as the copy check compares it.
_A_WORD = re.compile(r"[a-z0-9_']+")


def _word_runs(prose: str) -> list[list[str]]:
    """The runs of lowercased words in `prose`. A code span ends a run. A web address ends a run too."""
    return [_A_WORD.findall(run.lower()) for span in _A_CODE_SPAN.split(prose) for run in prose_rules.A_URL.split(span)]


def _longest_shared(first: list[list[str]], second: list[list[str]]) -> list[str]:
    """The longest run of words that a run of `first` and a run of `second` both hold."""
    best: list[str] = []
    for one in first:
        for other in second:
            found = difflib.SequenceMatcher(None, one, other, autojunk=False).find_longest_match()
            if found.size > len(best):
                best = one[found.a : found.a + found.size]
    return best


def copied_prose_errors(text: str, cited_prose: Mapping[str, Sequence[tuple[str, str]]]) -> list[str]:
    """
    The faults of the fragments reached through the names `text` cites. Such a fragment faults where its prose shares a
    run of more than `_MOST_SHARED_WORDS` words with `text`. A fault holds the fragment's key and the longest shared
    run. `cited_prose` maps a name to the fragments the name reaches, as a key and a prose apiece.

    Public. The `design_copies` checker reads a `DESIGN.md` text through here.
    """
    runs = _word_runs(text)
    faults = []
    for cited in sorted(set(_A_CITED_NAME.findall(text))):
        for key, prose in cited_prose.get(cited, ()):
            shared = _longest_shared(runs, _word_runs(prose))
            if len(shared) > _MOST_SHARED_WORDS:
                faults.append(f"`{key}`: {' '.join(shared)!r}")
    return faults


# A name of the plan document.
_A_PLAN_NAME = re.compile(r"\bPLAN(?:\.md)?\b")

# The paths that may name the plan document. `README.md` links the plan in its opening note. `CHANGELOG.md` is history.
# `PLAN.md` is the plan. `converge_claims._given_up` quotes claims into the claim store
# `.claude/prose-dependencies.json`. `tests/hooks.json` holds the edits a probe of the plan checker writes.
# `generator/claim_state.py` keeps the plan out of the claim pass. `generator/review_input.py` hands a reviewer the diff
# of the plan. `.claude/conventions.md` and `.claude/workflows/pre-commit-review.js` state the rules the plan keeps to.
# So do the agent definitions, where `write_agent_prompts` copies the conventions. `.claude/conventions.md` states
# `no-prose-points-at-the-plan`. `check_conventions.mechanised_rules` names that rule, and `write_agent_prompts` leaves
# it out of the agent definitions. `document_rules`, `history_errors` in this module and `prose_rules` check the plan.
# The `Makefile` names the plan among its `DOCUMENTS`. A path ending in `/` names the files under it.
_MAY_NAME_THE_PLAN = (
    "README.md",
    "CHANGELOG.md",
    "PLAN.md",
    "Makefile",
    ".claude/prose-dependencies.json",
    ".claude/agents/",
    ".claude/conventions.md",
    ".claude/hooks/document_rules.py",
    ".claude/workflows/pre-commit-review.js",
    "generator/check_documents.py",
    "generator/claim_state.py",
    "generator/prose_rules.py",
    "generator/review_input.py",
    "tests/hooks.json",
)


def _may_name_the_plan(path: str) -> bool:
    """Whether `_MAY_NAME_THE_PLAN` names `path`, or a directory above it."""
    return any(path == one or (one.endswith("/") and path.startswith(one)) for one in _MAY_NAME_THE_PLAN)


def plan_pointer_errors(where: str, lines: _Lines) -> list[str]:
    """
    The places a text names the plan document outside the paths `_may_name_the_plan` allows.

    `no-prose-points-at-the-plan` of `.claude/conventions.md` is the rule.

    Public. The `plan_pointers` checker reads a text through here.
    """
    if _may_name_the_plan(where):
        return []
    return [f"{where}:{number} names the plan" for number, line in lines if _A_PLAN_NAME.search(line)]


def _is_known(known: Container[str], cited: str) -> bool:
    """
    Whether `known` holds the name `cited` writes. The private form counts as well.

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

    `_NAMES_STILL_OWED` lists the names of work still owed. `PLAN.md` may cite such a name. Any other text says what the
    tree holds, and this refuses such a name there.

    A name whose underscore form the tree writes counts too. The same transformation is a step called
    `lower-continuations-into-conflicts` and a function called `_lower_continuations_into_conflicts`. Prose citing
    either is citing the thing that exists.

    A `verify-`, `vet-` or `gh-` name is no citation of this kind, and this passes it. The `Makefile` names some of its
    targets with those prefixes. This checks no such name against the `Makefile`. `_readme_gate_errors` holds
    `README.md`'s roster to the gates a target runs.
    """
    faults = []
    for number, line in lines:
        for cited in _A_HYPHENATED_NAME.findall(line):
            is_owed_in_the_plan = cited in _NAMES_STILL_OWED and where == "PLAN.md"
            if cited in named or is_owed_in_the_plan or cited.startswith(("verify-", "vet-", "gh-")):
                continue
            if _is_known(known, cited.replace("-", "_")) or cited in gone:
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
            if cited in _NAMES_FROM_ELSEWHERE or _is_known(known, cited) or cited in gone:
                continue
            # A dotted citation holds where the tree knows each part of it. A check of the first part alone would pass a
            # citation of an absent member of a module the tree has.
            if all(_is_known(known, part) for part in cited.split(".")):
                continue
            faults.append(f"{where}:{number} cites `{cited}`. that name names nothing in the tree.")
    return faults


# The names `CHANGELOG.md` uses that the tree has dropped. A changelog says what a change did. Naming the thing a change
# took away is the point of an entry. A citation naming nothing is correct in `CHANGELOG.md`. The same citation in
# `DESIGN.md` or `PLAN.md` is a fault.
#
# This list declares the names an entry writes. The check refuses a name the list leaves out. The tree may hold a name
# of this list again. The check reports such a name as a stale declaration. `_stale_owed_errors` does the same for
# `_NAMES_STILL_OWED`. `_stale_excuse_errors` does it for `_ABSENT_NAMES_A_FILE_MAY_CITE`. `_stale_elsewhere_errors`
# reports a name of `_NAMES_FROM_ELSEWHERE` that no text cites.
_NAMES_THE_TREE_NO_LONGER_HOLDS = frozenset({
    "ALWAYS_CONSUMES", "CRLF", "CloseMatch", "ColumnLeGuard", "ColumnLtGuard", "ConsumeCountedSpan",
    "ConsumeLiteralAction", "ConsumePeekedAction", "OpenMatch", "YS_CODE_UNPARSED",
    "EndMustConsumeGuard", "Invariant.__call__", "LiteralPeekGuard", "NEVER", "PEEK_OUTPUT", "Region",
    "TAKES_CHARACTERS", "Verdict", "barrier", "critic.js", "licensed",
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
    The names `generator/` and `scripts/` define at their top level, the identifiers the C writes, and the files the
    tree holds.

    A caller asks this instead of `_named_in_the_tree` where the question is whether something *exists* rather than
    whether the tree writes it anywhere. `_named_in_the_tree` counts a name written in a string or a comment. That is
    right for a citation, where the tree writes the name. It is wrong here. `_NAMES_THE_TREE_NO_LONGER_HOLDS` is itself
    a list of names written as strings. `_named_in_the_tree` would answer that the tree still holds a name that list
    declares gone.

    This reads the C too, and reads it differently. The C gives the identifiers its code writes, with the comments and
    the strings taken out. A C name has no top level to walk the way a module's does. The question is whether the tree
    still holds the name at all. The C code writing the name answers that question. The C may go on defining a symbol
    the list declares gone. Without the C, no check reports that declaration stale. The same name in `DESIGN.md`
    resolves against `src/` perfectly well.
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
    The names the tree's code writes, with the prose beside that code left out. Those are the Python's `NAME` tokens and
    its strings shaped like a name, the C's identifiers, and the files the tree holds. The message codes count too. So
    do the variables and the targets the `Makefile` declares. So do the names in the code of `CMakeLists.txt`.

    These are the names a citation beside the code may name. `_named_in_the_tree` is the wrong list for such a citation.
    That checker counts a name written in a comment. A docstring citing a name is then its own witness that the name
    exists. A rename leaving a name behind reads exactly like a rename that left none.

    `_defined_in_the_tree` is too narrow the other way. Prose beside the code cites a parameter, a method and an
    attribute as readily as a top-level definition. A module binds no such name. This function admits such a name and
    still refuses a name that appears only in the prose.
    """
    # A name a declaration says names nothing. Each is written as a string in the declaration itself. The harvest below
    # would take that string as the answer, and this check would go blind to the name everywhere.
    declared = set(_NAMES_THE_TREE_NO_LONGER_HOLDS) | set(_NAMES_STILL_OWED)
    declared |= {one for names in _ABSENT_NAMES_A_FILE_MAY_CITE.values() for one in names}
    found = set(_file_names()) | _named_by_the_c() | set(check_messages.codes())
    # The names the `Makefile` declares. A recipe's prose cites a variable there the way it cites a function.
    found |= set(_A_MAKE_DECLARATION.findall(pathlib.Path(gate.TREE, "Makefile").read_text(encoding="utf-8")))
    # The names `CMakeLists.txt` writes outside its comments. A comment cites a CMake property the way it cites a
    # function.
    cmake = pathlib.Path(gate.TREE, "CMakeLists.txt").read_text(encoding="utf-8").splitlines()
    found |= {name for line in cmake if not _A_HASH_COMMENT.match(line) for name in _A_NAME.findall(line)}
    for path in gate.modules() + gate.hook_modules():
        found |= names_written_by(path.read_text(encoding="utf-8"))
    return frozenset(found)


def names_written_by(source: str) -> set[str]:
    """
    The names the code of `source` writes.

    A declaration writes a gone name as a string. The harvest drops that string. The check then still sees the name as
    gone.

    The harvest keeps a string shaped like a name. `before_mark` is a region `interpreter.Emitter._region` names. `text`
    is a code `wire.CODE_CHAR` names.
    """
    declared = set(_NAMES_THE_TREE_NO_LONGER_HOLDS) | set(_NAMES_STILL_OWED)
    declared |= {one for names in _ABSENT_NAMES_A_FILE_MAY_CITE.values() for one in names}
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
    # A dotted name is checked once more for each part of it. The first part alone says the module is there and nothing
    # about the member. A member the tree lost read as held.
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
    The prose beside the code, keyed by the file that holds it. That is the comments and docstrings of `generator/`,
    `scripts/` and `.claude/hooks/`. `_c_prose` adds the comments of the C, and `_marked_prose` then adds those of the
    shell scripts and the workflows.

    A comment and a docstring are one thing here.

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


# A comment line opened by `#`, with the text after the marker captured. `_marked_prose` reads the comments of the shell
# scripts by it. `_named_by_the_code` skips the comments of `CMakeLists.txt` by it.
_A_HASH_COMMENT = re.compile(r"^\s*#\s?(.*)$")
_A_SLASH_COMMENT = re.compile(r"^\s*//\s?(.*)$")  # The marker the workflows use.


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
    `_strings_blanked` replaces double-quoted spans in `source` by spaces of equal length. Offsets stay put. A newline
    inside a match becomes a space.

    A literal holds data rather than code or prose. A `//` in a literal is no comment. An identifier in a literal is a
    name the C mentions rather than writes. Blanking costs less than a C lexer and is enough here. A blanked literal
    holds no comment and no name.
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
# The scope is the file rather than the name. In any other file the check hunts exactly that name. Exempting
# `speculate-folds` outright would blind the check to a docstring citing it as a stage of the pipeline. This check
# reports such a docstring as a fault. A document may cite none of these.
_ABSENT_NAMES_A_FILE_MAY_CITE = {
    # Names this file cites as gone. The comment above this list cites `speculate-folds` as its example.
    "check_documents.py": frozenset({
        "speculate-folds",
    }),  # fmt: skip
    # Names a worked example invents for the shape it shows. `_Namer` shows what minting a helper for `foo` comes to,
    # and `_set_name` shows what that transformation makes of a set's name.
    "normalize.py": frozenset({"foo_1", "foo_2", "foo_3", "foo_4", "foo_3_1"}),
    "grammar2decoder.py": frozenset({"NS_PLAIN_SAFE_IN"}),
    # A name a worked example invents for the suffixes a monomorphic copy and a minted helper take.
    "check_grammar_coverage.py": frozenset({"foo_c_flow-in_1"}),
}


# A numeral, written in digits or in words. `_AS_DIGITS` gives the words.
#
# `_NOT_A_NUMBER_OF_THE_TREE` lists what may pass. `_numbers_of_the_tree` blanks what that list matches, and this
# pattern then finds the numerals left. A determiner reads as a numeral here. `one of` and `the one` go in the list with
# their reason.
_A_NUMBER = re.compile(r"\b(?:[0-9]+|" + "|".join(_AS_DIGITS) + r")\b", re.IGNORECASE)


def is_prose_of_the_project(path: str) -> bool:
    """
    Whether the rules on prose hold for `path`.

    An absolute path outside the tree holds prose of somebody else. `gate.NOT_OURS` names the paths in this tree holding
    no prose of the project, and `gate.UNREAD` names the base names of the files the checkers leave alone. Any other
    path is prose of the project.

    The code opens no file. A path nobody has added yet gets the answer an added path would get.
    """
    if os.path.isabs(path) and os.path.commonpath([os.path.abspath(path), gate.TREE]) != gate.TREE:
        return False
    if any(part in path for part in gate.NOT_OURS):
        return False
    return os.path.basename(path) not in gate.UNREAD


# A code span. The contents are code, and the words inside belong to no sentence.
_A_CODE_SPAN = re.compile(r"`[^`]*`")

# A Doxygen code block holds a program. A number a program writes counts nothing in the tree.
_A_CODE_BLOCK = re.compile(r"@code\b.*?@endcode\b", re.DOTALL)


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

    Public. The `no_tree_numbers` checker asks this of a text.
    """
    return sorted({found.group(0).strip() for found in _numbers_of_the_tree(_joined(prose)[0])})


# The form the checkers above give a text's citation.
_A_FAULTED_CITATION = re.compile(r"cites `([^`]+)`")


def _cited_by(fault: str) -> str:
    """The name a dangling-citation fault names as the citation."""
    found = _A_FAULTED_CITATION.search(fault)
    if found is None:
        raise ValueError(f"a dangling-citation fault naming nothing cited: {fault!r}")
    return found.group(1)


# The documents the citation rules read. A file outside this tuple and outside `_A_SOURCE_SITS_UNDER` gets no citation
# rule.
_DOCUMENTS_CITED = ("DESIGN.md", "PLAN.md", "CHANGELOG.md")


# The directories holding the code whose prose the citation rule reads. `_code_prose` hands over the comments and the
# docstrings of a file under one of these. `_named_by_the_code` gathers names from these directories.
# `_named_by_the_code` reads the `Makefile` and `CMakeLists.txt` as well. `_citations` reads a file outside these
# directories only where `_DOCUMENTS_CITED` names that file.
_A_SOURCE_SITS_UNDER = ("generator/", "scripts/", ".claude/hooks/", "src/", "include/")


def _citations(path: str, lines: _Lines, minted: Collection[str] = frozenset()) -> list[str]:
    """
    A fault per name that `lines` cites where the name names nothing, with the declared excuses left in.

    A document and the prose beside the code take different name sets. `DESIGN.md` cites a production, and a bare word
    there is a citation. A docstring cites what the code writes, and a bare word there is prose. A file outside both
    lists gets no citation rule.

    `minted` holds the names a text writes, and the tree may lack some of them. A write-time caller fills it through
    `minted_by`, from the source the edit leaves. A gate over the tree leaves `minted` empty. The tree on disk already
    holds those names.
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
    A fault per name that `lines` cites where the name points at nothing and lacks a declared excuse.

    `_ABSENT_NAMES_A_FILE_MAY_CITE` excuses a name for a file, and this reads that list.
    """
    excused = _ABSENT_NAMES_A_FILE_MAY_CITE.get(os.path.basename(path), frozenset())
    return [fault for fault in _citations(path, lines, minted) if _cited_by(fault) not in excused]


def citation_refusal(path: str, lines: _Lines, minted: Collection[str] = frozenset()) -> str | None:
    """
    The refusal the citation rule gives the prose beside the code at `path`, or None where the citations pass.

    Public. The `prose_citations` checker asks here.

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


def _stale_excuse_errors(prose: Mapping[str, _Lines]) -> list[str]:
    """
    Find the names `_ABSENT_NAMES_A_FILE_MAY_CITE` excuses where no citation of `prose` needs the excuse. `_check` hands
    over that prose from `_code_prose`.

    `_citations` reads the prose with nothing excused. A declaration takes a fault out, and a declaration that takes
    none out reads as stale.
    """
    by_file: dict[str, list[str]] = {}
    for name, lines in prose.items():
        by_file.setdefault(os.path.basename(name), []).extend(_citations(name, lines))
    faults = []
    for name, gone in sorted(_ABSENT_NAMES_A_FILE_MAY_CITE.items()):
        cited = {_cited_by(fault) for fault in by_file.get(name, [])}
        faults += [
            f"`ABSENT_NAMES_A_FILE_MAY_CITE` declares `{one}` for {name}. nothing there wants the excuse."
            for one in sorted(gone - cited)
        ]
    return faults


def _stale_elsewhere_errors(texts: Mapping[str, _Lines]) -> list[str]:
    """
    The errors naming the names `_NAMES_FROM_ELSEWHERE` declares that no line of `texts` cites. A key of `texts` is a
    path. Its value is the lines of the documents or of the prose beside the code. This skips the comment directly above
    that list.
    """
    own = os.path.relpath(__file__, gate.TREE)
    source = pathlib.Path(__file__).read_text(encoding="utf-8").split("\n")
    ends = next(at for at, line in enumerate(source, start=1) if line.startswith("_NAMES_FROM_ELSEWHERE = "))
    opens = ends
    while opens > 1 and source[opens - 2].startswith("#"):
        opens -= 1
    cited: set[str] = set()
    for where, lines in texts.items():
        for number, line in lines:
            if where == own and opens <= number < ends:
                continue
            cited |= set(_A_CITED_NAME.findall(line)) | set(_A_HYPHENATED_NAME.findall(line))
    return [
        f"`NAMES_FROM_ELSEWHERE` declares `{name}`, and no text cites it"
        for name in sorted(_NAMES_FROM_ELSEWHERE - cited)
    ]


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


# The documents that keep a tense. `_OUT_OF_ITS_DOMAIN_IN` holds the words `DESIGN.md` and `CHANGELOG.md` may not say.
# It holds no entry for `PLAN.md`.
_TENSED = ("DESIGN.md", "PLAN.md", "CHANGELOG.md")


def domain_errors(document: str, text: str) -> list[str]:
    """
    The places `text` writes in a tense another document owns. `text` is the text of `document`.

    Public. The `document_rules` checker reads a text through here.
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

    Public. The `document_rules` checker reads a text through here.
    """
    if document not in _TENSED:
        return []
    said, starts = _joined(text)
    return [
        f"{document}:{_line_of(starts, found.start())} says {found.group(0)!r}, and a document says what is rather "
        f"than what changed"
        for found in _NARRATES_ITS_OWN_HISTORY.finditer(said)
    ]


# The targets whose rosters `README.md` gives. The `Makefile` decides what a target runs, and `README.md` follows that
# decision rather than the other way about.
_ROSTERS = ("verify", "vet", "vet-format")

# The gates `README.md` lists under a roster whose target does not name them. A gate the target names runs the gates
# this list declares. `verify` names `verify-grammar`, and `verify-grammar` runs the pair of gates `README.md` lists
# under `verify`. `vet` names `vet-format`, and `vet-format` runs the formatters. This list is a declaration, like the
# other exemptions here. README may grow a list that nothing runs. Without the declaration, that list would read exactly
# like this one.
_GATES_UNDER_ANOTHER = {
    "verify": frozenset({"verify-grammar-base", "verify-grammar-base-coverage"}),
    "vet": frozenset({
        "vet-format-c", "vet-format-md", "vet-format-py", "vet-format-cmake", "vet-format-sh", "vet-format-js",
        "vet-format-make",
    }),  # fmt: skip
    "vet-format": frozenset(),
}


def readme_roster_errors(document: str, text: str) -> list[str]:
    """
    The disagreements between the rosters `README.md` lists and the `Makefile`, where `document` is either file and
    holds `text`.

    Public. The `document_rules` checker reads an edit through here.
    """
    if document not in ("README.md", "Makefile"):
        return []
    held = {name: pathlib.Path(gate.TREE, name).read_text(encoding="utf-8") for name in ("README.md", "Makefile")}
    held[document] = text
    return _readme_gate_errors(held["Makefile"], held["README.md"])


def _readme_gate_errors(makefile: str, readme: str) -> list[str]:
    """
    The disagreements between `README.md`'s list of the gates a roster target runs and the `Makefile`'s target.

    A list of the gates is a roster.

    Both sides write a roster out in full, a name at a time. A checker cannot answer a line of suffixes such as
    `vet-format-c` then `-md` then `-py`.
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
    """Report the faults this module found. The report tags a fault `[cites]`."""
    stages = normalize.stages(annotated2ir.load())
    final = stages[-1][1]
    # The declarations this module holds. `checkers` asks the rules of a text, and this gate asks whether a declaration
    # still has a text that needs it.
    named_anywhere = _named_in_the_tree()
    named_by_a_stage = _cited_names()
    errors = [f"[cites] {fault}" for fault in _stale_cited_names_errors(stages)]
    errors += [f"[cites] {fault}" for fault in _stale_gone_errors(_document_lines("CHANGELOG.md"), named_by_a_stage)]
    errors += [f"[cites] {fault}" for fault in _stale_owed_errors(named_by_a_stage, named_anywhere)]
    errors += [f"[cites] {fault}" for fault in _unplanned_owed_errors(_document_lines("PLAN.md"))]
    errors += [f"[cites] {fault}" for fault in _stale_excuse_errors(_code_prose())]
    texts = {**_code_prose(), **{document: _document_lines(document) for document in _DOCUMENTS_CITED}}
    errors += [f"[cites] {fault}" for fault in _stale_elsewhere_errors(texts)]
    # The live figures, printed and written down nowhere. A reader wanting a figure runs the gate.
    for name, count in sorted(_measured_for_the_report(stages, final).items()):
        print(f"  {count} {name}")
    gate.report(
        errors,
        "document fault(s). a declaration excuses nothing",
        "documents: a declared excuse covers a citation the tree writes",
    )


def main() -> None:
    gate.run_deep(_check)


if __name__ == "__main__":
    main()
