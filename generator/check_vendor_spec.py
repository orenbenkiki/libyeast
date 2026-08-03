# SPDX-License-Identifier: MIT
"""
Check that libyeast's grammar is still the official grammar.

Erases libyeast's token annotations and indicator productions from `grammar/yeast-spec-1.2.yaml`, and compares what
remains, production by production, against the vendored `yaml-spec-1.2.yaml`. A production that differs is either a
mistake or a departure we chose; a chosen one must be declared in DEVIATIONS, with its reason, or this fails. What
libyeast adds therefore cannot quietly become what libyeast changes.
"""

import ir2spec
import os

import annotated2ir
import gate
import ir
import validate_grammar

VENDORED = os.path.join(annotated2ir.TREE, "third_party", "yaml-grammar", "yaml-spec-1.2.yaml")

# Where libyeast departs from the official grammar, and why. A deviation is a decision, not an escape: nothing belongs
# here that could be fixed by correcting the grammar instead.
DEVIATIONS = {
    "c-indentation-indicator": (
        'the official grammar sets m to the string "auto-detect", and then computes n + m — an integer plus a string, '
        "which nothing in it ever redeems. libyeast sets m to <auto-detect-indent>, the marker the official grammar "
        "already uses for the same thing in l+block-sequence and l+block-mapping, so that m is always an indentation"
    ),
    "s-l+block-indented": (
        "the official grammar reads m here and sets it nowhere — its own notes concede that it 'assumes that m is "
        "stored as a state/stack variable and has been set somewhere else'. libyeast has no m to read: it takes the "
        "spaces that follow as the indent token and reads <column>, so what the compact collection is indented by is "
        "where its own indentation left the parse rather than a number worked out beside it"
    ),
    "l+block-sequence": (
        "the official grammar's every turn re-measures against an m fixed outside the repetition, by a special rule "
        "that looks ahead for the first content line. libyeast takes that line's own run of spaces as the indent token "
        "where the parse already stands, and enters l-block-seq-entries at <column> — the same n+m, established by the "
        "first entry rather than looked for before it, with m > 0 kept as a guard on the column"
    ),
    "l+block-mapping": (
        "the sequence's twin: the first entry's own run of spaces is the indent token and <column> is the n+m the "
        "entries are measured against, l-block-map-entries running them at it"
    ),
    "c-b-block-header": (
        "the official grammar's header is two orderings, indent-then-chomp or chomp-then-indent, that begin the same "
        "way, so it can only tell them apart by backtracking and can never call a header malformed. libyeast guards "
        "the first ordering with a lookahead, so the second is reached without backtracking and a junk header commits "
        "to a BLOCK_HEADER error instead of silently failing to match"
    ),
    "l-literal-content": (
        "the official grammar's first content chunk is l-nb-literal-text, whose l-empty* silently consumes the leading "
        "empty lines; libyeast routes it through l-nb-literal-first, which consumes those empties itself and holds "
        "them to the spec's prose §8.1.1.1 — an error, BLOCK_SCALAR_UNDER_INDENT, when the first content line is less "
        "indented than the widest leading empty line — a rule the official BNF does not carry"
    ),
    "l-nb-diff-lines": (
        "the folded twin of the l-literal-content deviation: its first same-lines chunk is l-nb-same-first, which "
        "applies the same §8.1.1.1 leading-empty floor before the folded content that l-nb-literal-first does before "
        "the literal content"
    ),
    "c-l-block-map-implicit-value": (
        "the official grammar's ':' value indicator carries no guard: it leans on greedy ns-plain, which swallows the "
        "mid-scalar ':' of a plain a:b, so the value ':' is only ever reached with a space or break after it. A "
        "backtracking parser can shorten the key and hand that ':' back, so libyeast guards it with "
        "<not_followed_by_an_ns-char> — the guard the official grammar already puts on the '-' of "
        "c-l-block-seq-entry — which prunes that path and changes no token stream"
    ),
    "c-l-block-map-explicit-key": (
        "the folded twin for '?': the official grammar makes '?foo' a plain scalar through ns-plain-first and never "
        "reaches the explicit-key '?' for it; libyeast guards that '?' with the same <not_followed_by_an_ns-char> so a "
        "backtracking parser does not reach it for a plain scalar that begins with '?'"
    ),
    "c-ns-esc-char": (
        "a '\\' at the end of a line is an escaped break — a line continuation the official grammar reaches through "
        "s-double-escaped, not this rule, whose escape alternation carries no break and so simply fails to match it. "
        "libyeast's INVALID_ESCAPE would fire on that failure, so it guards the escape with <not_followed_by_a_break>, "
        "which hands the '\\<break>' back to s-double-escaped and changes no token stream"
    ),
    "ns-anchor-name": (
        "':' is an ns-anchor-char — it is not a c-flow-indicator — so a name greedily takes it: '*a:' is the "
        "alias 'a:'. The official grammar's ns-anchor-char+ is possessive under the reference's PEG, which never "
        "re-enters it to hand a trailing ':' back; a backtracking parser can, shortening the name to 'a' so the "
        "':' opens a mapping. libyeast guards the name with <not_followed_by_an_ns-anchor-char> so it commits to "
        "its greedy match, and changes no token stream"
    ),
}


def _match_drift(vendored, ours):
    """
    The productions whose `(match)` the two grammars disagree about.

    A `(match)` is read here as the open run — the token the rule is building — where the official grammar means the
    text the rule matched. Those are the same wherever both read one in the same production, which is what this holds: a
    `(match)` the official grammar reads and libyeast does not is a reading nothing here justifies.
    """
    holders = {
        label: {name for name, production in grammar.items() if any(isinstance(n, ir.Match) for n in walk(production))}
        for label, grammar in (("official", vendored), ("libyeast", ours))
    }
    return [
        f"{name}: the official grammar reads a `(match)` here and libyeast does not, so the two need not agree"
        for name in sorted(holders["official"] - holders["libyeast"])
    ]


def walk(production):
    """Every IR node in `production`'s body."""
    yield from validate_grammar.walk(production.body)


def main():
    vendored_ir, ours_ir = annotated2ir.load(VENDORED), annotated2ir.load()
    vendored = ir2spec.normalized(vendored_ir)
    recovered = ir2spec.official(ours_ir)

    errors = _match_drift(vendored_ir, ours_ir)
    for name in (key for key in vendored if not key.startswith(":")):
        if recovered.get(name) != vendored[name] and name not in DEVIATIONS:
            errors.append(f"{name}: differs from the official grammar, and is not a declared deviation")
            errors.append(f"    official: {vendored[name]!r}")
            errors.append(f"    libyeast: {recovered.get(name)!r}")
    for name in sorted(set(recovered) - set(vendored)):
        if not name.startswith(":"):
            errors.append(f"{name}: libyeast has a production the official grammar does not")
    for name in sorted(DEVIATIONS):
        if name not in vendored:
            errors.append(f"{name}: declared as a deviation, but the official grammar has no such production")
        elif recovered.get(name) == vendored[name]:
            # A deviation that no longer deviates is a stale declaration, and a dangerous one: it exempts the production
            # from the comparison, so any later drift in it goes unseen. If it matches now, it must not be declared.
            errors.append(f"{name}: declared as a deviation, but no longer differs from the official grammar")

    productions = sum(1 for key in vendored if not key.startswith(":"))
    gate.report(
        errors,
        "production(s) that are not the official grammar's",
        f"official grammar recovered: {productions} productions, {len(DEVIATIONS)} declared deviation(s)",
    )
    for name in sorted(DEVIATIONS):
        print(f"    {name}: {DEVIATIONS[name]}")


if __name__ == "__main__":
    main()
