# SPDX-License-Identifier: MIT
"""
Check that libyeast's grammar documents the tokens it emits.

The grammar that it derives from is a document. A rule is preceded by its spec BNF, and a rule that needs explaining
gets a section of prose. libyeast's additions deserve no less. A reader should not have to work out for themselves why
`c-quoted-quote` marks the second quote `meta` while the first is an `indicator`.

So a rule with a token action is preceded by an `Emits:` line. The line names the codes, in the order the rule emits
them. The codes come from the grammar itself. A mere presence check would let the comment drift. A note that is wrong
fails the build exactly as a note that is missing does.
"""

import re

import annotated2ir
import chars
import gate
import ir

_EMITS = re.compile(r"^#\s*Emits:\s*(.*)$", re.M)  # the line a rule's comment lists its tokens on.


def _emitted(node: ir.Node) -> list[str]:
    """
    The codes that a node emits, in the order the node emits them. Repeats come out.

    A kind this question does not name raises rather than walking into its children. A new way of emitting would
    otherwise contribute no code of its own. A rule using that new way would read as documented while saying nothing
    about it.
    """
    return list(dict.fromkeys(_EMITTED(node)))


def _emitted_around_it(node: ir.Wrapper) -> list[str]:
    """
    A `(wrap)`'s codes. The opening marker comes first. Then come the codes of the item it holds. The closing marker
    comes last.
    """
    return [node.begin, *_emitted(node.item), node.end]


def _emitted_by_what_it_holds(node: ir.Node) -> list[str]:
    """Anything holding parts. The codes the parts emit, in the order the parts run."""
    return [code for child in chars.children(node) for code in _emitted(child)]


# The kinds an annotated grammar uses. This question reads libyeast's grammar before any lowering.
_EMITTED: ir.Question[list[str]] = ir.Question(
    "the list of token codes a node emits, in the order of emission and with repeats",
    {
        ir.TokenWrapper: lambda node: [node.code, *_emitted(node.item)],
        ir.Wrapper: _emitted_around_it,
        ir.EmitAction: lambda node: [node.code],
        (
            *ir.VALUE_KINDS,
            *ir.PARTS,
            *ir.CALLS,
            *ir.TREES,
            *(kind for kind in ir.WRAPPERS if kind not in (ir.TokenWrapper, ir.Wrapper)),
            *(kind for kind in ir.ACTIONS if kind is not ir.EmitAction),
            *ir.GUARDS,
            *(kind for kind in ir.CONSUMING if kind not in ir.ACTIONS),
        ): _emitted_by_what_it_holds,
    },
    # The canonical forms. An annotated grammar holds none of them.
    untested=(
        ir.CharSet,
        ir.ConsumeCharAction,
        ir.ConsumeLimitedSpanAction,
        ir.ConsumeSpanAction,
        ir.ConsumeTrimmedSpanAction,
    ),
)


def _documented(text: str) -> dict[str, list[str]]:
    """The codes a rule's comment block says it emits. `{name: [code, ...]}`, over the rules that say so."""
    said = {}
    for match in re.finditer(r"^:\d+: ([\w+.-]+)\n((?:#.*\n)*)", text, re.M):
        emits = _EMITS.search(match.group(2))
        if emits:
            said[match.group(1)] = [code.strip() for code in emits.group(1).split(",") if code.strip()]
    return said


def main() -> None:
    grammar = annotated2ir.load()
    with open(annotated2ir.DEFAULT_GRAMMAR, encoding="utf-8") as handle:
        said = _documented(handle.read())

    errors = []
    for name, production in grammar.items():
        codes = _emitted(production.body)
        if not codes:
            if name in said:
                errors.append(f"{name}: says it emits {said[name]} and emits nothing")
            continue
        if name not in said:
            errors.append(f"{name}: emits {', '.join(codes)} and says nothing about that code")
        elif said[name] != codes:
            errors.append(f"{name}: says it emits {', '.join(said[name])} and emits {', '.join(codes)} instead")

    emitting = sum(1 for production in grammar.values() if _emitted(production.body))
    gate.report(
        errors,
        "undocumented or misdocumented rule(s)",
        f"grammar documented: {emitting} rules emit tokens, and the documentation says so",
    )


if __name__ == "__main__":
    main()
