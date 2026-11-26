# SPDX-License-Identifier: MIT
"""
Check that libyeast's grammar documents the tokens its rules emit.

The grammar libyeast derives from is a document. The YAML specification at https://yaml.org/spec/1.2.2/ holds it. In
`grammar/yeast-spec-1.2.yaml`, a rule is preceded by its spec BNF, and a rule that needs explaining gets a section of
prose. libyeast's additions deserve no less.

A rule whose own body emits a token is preceded by an `Emits:` line. The line lists the codes of that body without
repeats, in the order of first emission. A `RefCall` holds only a name and args. The check counts no code from a called
rule. The codes come from the grammar itself. A note that is wrong fails the build exactly as a note that is missing
does.
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

    A kind this question does not name makes `Question` raise rather than walking into its children.
    """
    return list(dict.fromkeys(_EMITTED(node)))


def _emitted_around_it(node: ir.Wrapper) -> list[str]:
    """
    A `(wrap)`'s codes. The opening marker comes first. The codes of the item the wrap holds come next. The closing
    marker comes last.
    """
    return [node.begin, *_emitted(node.item), node.end]


def _emitted_by_what_it_holds(node: ir.Node) -> list[str]:
    """A node holding parts emits the codes of its parts, in the order the parts run."""
    return [code for child in chars.children(node) for code in _emitted(child)]


# The kinds an annotated grammar uses. `main` asks this question of the grammar `annotated2ir.load` returns.
# `annotated2ir.load` translates that grammar and does not lower it.
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
    # The canonical forms. An annotated grammar holds none of them. `annotated2ir.translate` builds none of them.
    untested=(
        ir.CharSet,
        ir.ConsumeCharAction,
        ir.ConsumeLimitedSpanAction,
        ir.ConsumeSpanAction,
        ir.ConsumeTrimmedSpanAction,
    ),
)


def _documented(text: str) -> dict[str, list[str]]:
    """
    Map a rule to the codes its comment block says the rule emits, as `{name: [code, ...]}`. A rule whose block holds no
    `Emits:` line gets no entry.
    """
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

    emitting = sum(1 for named_production in grammar.values() if _emitted(named_production.body))
    gate.report(
        errors,
        "undocumented or misdocumented rule(s)",
        f"grammar documented: {emitting} rules emit tokens, and the documentation says so",
    )


if __name__ == "__main__":
    main()
