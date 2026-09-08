# SPDX-License-Identifier: MIT
"""
Check that libyeast's grammar is still the official grammar.

Erases libyeast's token annotations from `grammar/yeast-spec-1.2.yaml`. Writes a reference to an indicator production
back as the character it names. Leaves out the rules libyeast adds of its own. Compares what remains against the
vendored `yaml-spec-1.2.yaml`, a production at a time.

A production that differs is either a mistake or a departure we chose. A departure we chose goes into DEVIATIONS with
its reason, and this fails otherwise. An addition therefore cannot quietly become a change.

`a-declared-exception-carries-its-reason`. This holds DEVIATIONS in both directions. This reports a production that has
stopped differing. A stale declaration would otherwise hide the next drift in that production.
"""

import os
from collections.abc import Iterator

import annotated2ir
import gate
import ir
import ir2spec
import validate_grammar

_VENDORED = os.path.join(gate.TREE, "third_party", "yaml-grammar", "yaml-spec-1.2.yaml")  # the official grammar.

# The places libyeast departs from the official grammar, and why. A deviation is a decision rather than an escape. A
# departure that correcting the grammar would fix belongs elsewhere. The cases libyeast completes share a reason. The
# official grammar leaves a context out and libyeast writes the decline. Shared rather than repeated. The cases read as
# the same departure made again.
_TOTAL_CASE = (
    "the official grammar's case names values of its parameter and stays silent about any other value. the official "
    "spec reads that silence as declining those values. libyeast names the values and writes the decline as <fail>. a "
    "walk over the case then reaches a branch saying what it does. the walk reads no absence as a refusal. the "
    "language is the same, and libyeast writes down the implicit part."
)

# The productions libyeast writes differently, mapped to what they depart over. A production this table does not name
# has to match the official grammar.
_DEVIATIONS = {
    "s-line-prefix": _TOTAL_CASE,
    "nb-double-text": _TOTAL_CASE,
    "nb-single-text": _TOTAL_CASE,
    "ns-plain-safe": _TOTAL_CASE,
    "ns-plain": _TOTAL_CASE,
    "c-indentation-indicator": (
        'the official grammar sets m to the string "auto-detect" and then computes n + m. that adds an integer to a '
        "string. nothing in the grammar redeems the sum. libyeast sets m to <auto-detect-indent>. the official "
        "grammar already uses that marker for the same thing in l+block-sequence and l+block-mapping. m is then an "
        "indentation and stays one."
    ),
    "s-l+block-indented": (
        "the official grammar reads m here and sets it in no rule. the official notes concede an assumption, that m "
        "is a state variable set somewhere else. libyeast reads no m. libyeast takes the spaces that follow as the "
        "indent token and reads <column>. the compact collection takes its indentation from where the indentation "
        "left the parse, rather than from a number worked out beside the rule."
    ),
    "l-chomped-empty": (
        "passes i to the two rules below. those rules need to know whether a rule established an indentation. the "
        "parse may reach them with no indentation. the trailing lines would then have established it."
    ),
    "l-strip-empty": (
        "the official grammar bounds a trailing line by n. a rule may still owe the indentation, where an indicator "
        "named none and no content line said it. there is then no n to bound the trailing lines by, and those lines "
        "would have said it. libyeast takes a line whole. the widest of them is the floor. the trailing comment must "
        "be shallower than that floor. the official meaning holds wherever a rule gives an n."
    ),
    "l-keep-empty": ("l-strip-empty's twin. this rule keeps the lines it takes, and those lines are the content."),
    "c-l+literal": (
        "the block scalar takes i, and its header sets i. the header gives i where the indicator named the "
        "indentation. the header detects i where the indicator named none, and the first content line then says it. "
        "the official grammar has no such thing. a special rule answers its m by looking ahead for that line, before "
        "the scalar has consumed anything."
    ),
    "c-l+folded": ("the literal scalar's twin: the folded scalar takes the same i, set by the same header"),
    "l-folded-content": (
        "the l-literal-content deviation's twin. this rule takes i too. either way of establishing the first content "
        "line's indentation has to reach that line."
    ),
    "l+block-sequence": (
        "the official grammar re-measures on a turn against an m fixed outside the repetition. a special rule looks "
        "ahead for the first content line to fix that m. libyeast takes the run of spaces on that line as the indent "
        "token, at the position the parse has already reached. libyeast then enters l-block-seq-entries at <column>. "
        "that is the same n+m. the first entry establishes that n+m, rather than a lookahead before the entries. a "
        "guard on the "
        "column keeps m > 0."
    ),
    "l+block-mapping": (
        "the sequence's twin. the run of spaces on the first entry is the indent token. <column> is the n+m the "
        "entries measure against, and l-block-map-entries runs them at that column."
    ),
    "b-break": (
        "the official grammar's break is a carriage return with a line feed, a carriage return, or a line feed. the "
        "first pair begin alike. a parse at a carriage return tells the pair apart by trying the longer way and "
        "handing back what fails. a rule that consumes a break inherits that. libyeast says the same matches as a "
        "line feed, or a carriage return with a line feed behind it where the input holds that pair. no input tells "
        "that apart from the official meaning, and no parse has to back out of it."
    ),
    "c-b-block-header": (
        "the official grammar's header is two orderings. those are indent-then-chomp and chomp-then-indent, and they "
        "begin alike. the official grammar tells them apart by backtracking, and calls no header malformed. libyeast "
        "guards the first ordering with a lookahead. a parse then reaches the second without backtracking. a junk "
        "header commits to a BLOCK_HEADER error rather than failing to match in silence."
    ),
    "l-literal-content": (
        "the official grammar's first content chunk is l-nb-literal-text. its l-empty* consumes the leading empty "
        "lines in silence. libyeast routes that chunk through l-nb-literal-first. that rule consumes the empties and "
        "holds them to section 8.1.1.1 of the spec's prose. it raises BLOCK_SCALAR_UNDER_INDENT where the first "
        "content line is shallower than the widest leading empty line. the official BNF does not state that rule."
    ),
    "l-nb-diff-lines": (
        "the folded twin of the l-literal-content deviation. its first same-lines chunk is l-nb-same-first. that "
        "rule applies the section 8.1.1.1 leading-empty floor before the folded content, as l-nb-literal-first does "
        "before the literal content."
    ),
    "c-l-block-map-implicit-value": (
        "the official grammar's `:` value indicator has no guard. it leans on greedy ns-plain. ns-plain swallows the "
        "mid-scalar `:` of a plain `a:b`. a parse therefore reaches the value `:` with a space or a break after it. "
        "a backtracking parser can shorten the key and hand that `:` back. libyeast guards the `:` with "
        "<not_followed_by_an_ns-char>. the official grammar already puts that guard on the `-` of "
        "c-l-block-seq-entry. the guard prunes that path and changes no token stream."
    ),
    "c-l-block-map-explicit-key": (
        "the folded twin for `?`. the official grammar makes `?foo` a plain scalar through ns-plain-first, and does "
        "not reach the explicit-key `?` for it. libyeast guards that `?` with the same <not_followed_by_an_ns-char>. "
        "a backtracking parser then does not reach it for a plain scalar beginning with `?`."
    ),
    "c-ns-esc-char": (
        "a `\\` at the end of a line is an escaped break. the official grammar reaches that line continuation "
        "through s-double-escaped rather than through this rule. this rule's escape alternation holds no break, and "
        "fails to match one. libyeast's INVALID_ESCAPE would fire on that failure. libyeast guards the escape with "
        "<not_followed_by_a_break>. the guard hands the `\\<break>` back to s-double-escaped and changes no token "
        "stream."
    ),
    "ns-anchor-name": (
        "`:` is an ns-anchor-char rather than a c-flow-indicator. a name takes it greedily, and `*a:` is the alias "
        "`a:`. the official grammar's ns-anchor-char+ is possessive under the reference's PEG. that PEG does not "
        "re-enter the name to hand a trailing `:` back. a backtracking parser can, and shortens the name to `a` so "
        "the `:` opens a mapping. libyeast guards the name with <not_followed_by_an_ns-anchor-char>. the name then "
        "commits to its greedy match, and the token stream stays the same."
    ),
}


def _match_drift(vendored: dict[str, ir.Prod], ours: dict[str, ir.Prod]) -> list[str]:
    """
    The productions whose `(match)` the grammars disagree about.

    This reads a `(match)` as the open run, the token the rule is building. The official grammar means the text the rule
    matched. Both meanings coincide wherever a production has a `(match)` in both grammars, and that is what this holds.
    A `(match)` the official grammar reads and libyeast does not is a meaning nothing here justifies.
    """
    holders = {
        label: {
            name for name, production in grammar.items() if any(isinstance(n, ir.MatchValue) for n in _walk(production))
        }
        for label, grammar in (("official", vendored), ("libyeast", ours))
    }
    return [
        f"{name}: the official grammar reads a `(match)` here and libyeast reads none. the two need not agree."
        for name in sorted(holders["official"] - holders["libyeast"])
    ]


def _walk(production: ir.Prod) -> Iterator[ir.Node]:
    """The IR nodes in `production`'s body."""
    yield from validate_grammar.walk(production.body)


def main() -> None:
    vendored_ir, ours_ir = annotated2ir.load(_VENDORED), annotated2ir.load()
    vendored = ir2spec.normalized(vendored_ir)
    recovered = ir2spec.official(ours_ir)

    errors = _match_drift(vendored_ir, ours_ir)
    for name in (key for key in vendored if not key.startswith(":")):
        if recovered.get(name) != vendored[name] and name not in _DEVIATIONS:
            errors.append(f"{name}: differs from the official grammar, and is not a declared deviation")
            errors.append(f"    official: {vendored[name]!r}")
            errors.append(f"    libyeast: {recovered.get(name)!r}")
    for name in sorted(set(recovered) - set(vendored)):
        if not name.startswith(":"):
            errors.append(f"{name}: libyeast has a production the official grammar does not")
    for name in sorted(_DEVIATIONS):
        if name not in vendored:
            errors.append(f"{name}: declared as a deviation, but the official grammar has no such production")
        elif recovered.get(name) == vendored[name]:
            # A deviation that no longer deviates is a stale declaration, and a dangerous one. It exempts the production
            # from the comparison. Any later drift in it goes unseen. If it matches now, it must not be declared.
            errors.append(f"{name}: declared as a deviation, and it matches the official grammar")

    productions = sum(1 for key in vendored if not key.startswith(":"))
    gate.report(
        errors,
        "production(s) that are not the official grammar's",
        f"official grammar recovered: {productions} productions, {len(_DEVIATIONS)} declared deviation(s)",
    )
    for name in sorted(_DEVIATIONS):
        print(f"    {name}: {_DEVIATIONS[name]}")


if __name__ == "__main__":
    main()
