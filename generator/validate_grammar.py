# SPDX-License-Identifier: MIT
"""
Static validation of the grammar IR.

Loads libyeast's grammar (via `annotated2ir`) and checks the following.

  * A referenced production exists.
  * A reference passes as many arguments as the target declares.
  * A production has a reference unless `_UNREFERENCED` names it. A production in `_UNREFERENCED` must exist.
  * A character the parser consumes lies within a token annotation.
  * A `(match)` sits inside a `(token)`. That `(token)` holds no inner `(token)`.
  * A production's `renamed` rewrites exactly the names its `references` reports.
  * A case without a default is on a parameter in `_FINITE_VALUES`. The case names a branch per value.

Reports the problems found and exits with a failing status where the grammar breaks any of these.
"""

from collections.abc import Iterable, Iterator, Mapping

import annotated2ir
import chars
import gate
import ir


def walk(node: ir.Node) -> Iterator[ir.Node]:
    """Yield `node` and the IR nodes nested within it."""
    yield node
    for child in chars.children(node):
        yield from walk(child)


# The productions that nothing references. `ROOT` names the root the parser runs. `c-reserved` matches the reserved
# characters of section 5.3. The grammar defines `c-reserved` and leaves it unused.
#
# `c-sequence-entry` is another indicator production of section 5.3, and the grammar references it. libyeast reaches an
# indicator through the production naming it. That production holds the token annotation.
_UNREFERENCED = frozenset({"l-yeast-stream", "c-reserved"})


def _consumed(node: ir.Node, is_annotated: bool, references: list[tuple[str, bool]]) -> Iterator[bool]:
    """
    For a character `node` consumes, yield whether a token annotation covers it. Collect the references reached.

    `ASKED_NOT_TAKEN_NODES` holds the lookarounds and the exclusion. Such a node consumes nothing. `_consumed` neither
    counts nor follows a node nested under such a node. A `(---)` is a `DiffSet`, and `CONSUMING` holds that kind. So a
    `(---)` matches a character and counts as such. The characters it subtracts are operands rather than matches.

    A kind this question does not name makes `Question` raise rather than walk into its children. A new way of taking a
    character would otherwise yield nothing of its own. The characters that way takes would then pass this check without
    an annotation.
    """
    yield from _CONSUMED(node, is_annotated, references)


def _taken_under_an_annotation(
    node: ir.TokenWrapper, _is_annotated: bool, references: list[tuple[str, bool]]
) -> Iterable[bool]:
    """
    An annotation's characters. The annotation holds an item, and the take runs under the annotation. The characters
    inside are therefore under cover.
    """
    return _consumed(node.item, True, references)


def _taken_by_a_call(node: ir.RefCall, is_annotated: bool, references: list[tuple[str, bool]]) -> Iterable[bool]:
    """
    A call's characters. A call takes none of its own. The name goes on the record with whether an annotation covers it.
    """
    references.append((node.name, is_annotated))
    return ()


def _taken_by_what_it_holds(node: ir.Node, is_annotated: bool, references: list[tuple[str, bool]]) -> Iterable[bool]:
    """Anything holding parts. The characters the parts take, under the annotation that covers the whole."""
    return (held for child in chars.children(node) for held in _consumed(child, is_annotated, references))


# The kinds an annotated grammar uses. A canonical form arriving means a caller handed this gate the wrong grammar. It
# is not a shape to walk into.
_CONSUMED: ir.Question[Iterable[bool]] = ir.Question(
    "a boolean per character a node takes. a boolean says whether a token annotation covers that character.",
    {
        ir.TokenWrapper: _taken_under_an_annotation,
        # The item inside gets a question and no take. A character of it counts for nothing here.
        ir.ASKED_NOT_TAKEN_NODES: lambda node, is_annotated, references: (),
        ir.CONSUMING: lambda node, is_annotated, references: (is_annotated,),
        ir.RefCall: _taken_by_a_call,
        (
            *ir.VALUE_KINDS,
            *ir.PARTS,
            *(kind for kind in ir.TREES if kind is not ir.EmptyTree),
            *(kind for kind in ir.WRAPPERS if kind is not ir.TokenWrapper),
            *(kind for kind in ir.ACTIONS if kind not in ir.ASKED_NOT_TAKEN_NODES and kind not in ir.CONSUMING),
            *(kind for kind in ir.GUARDS if kind not in ir.ASKED_NOT_TAKEN_NODES),
            ir.EmptyTree,
        ): _taken_by_what_it_holds,
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


def _check_annotated(grammar: Mapping[str, ir.Prod]) -> list[str]:
    """
    A character the parser consumes must lie within a token annotation.

    The check reaches a production with an annotation around it, without one, or both ways. The check follows the
    references from the root and runs until the set of reached productions settles.
    """
    reached: dict[str, set[bool]] = {name: set() for name in grammar}
    reached[ir.ROOT].add(False)
    is_settled = False
    while not is_settled:
        is_settled = True
        for name, production in grammar.items():
            for is_annotated in list(reached[name]):
                references: list[tuple[str, bool]] = []
                list(_consumed(production.body, is_annotated, references))
                for target, is_covered in references:
                    if target in reached and is_covered not in reached[target]:
                        reached[target].add(is_covered)
                        is_settled = False

    errors = []
    for name, production in sorted(grammar.items()):
        for is_annotated in reached[name]:
            if not all(_consumed(production.body, is_annotated, [])):
                errors.append(
                    f"{name}: consumes a character outside any token annotation. such a character would emit "
                    f"`unparsed`."
                )
                break
    return errors


# The match a node cuts into a token, and `None` for a node that cuts none.
_CUT_INTO_A_TOKEN: ir.Question[ir.Node | None] = ir.Question(
    "the match a node cuts into a token",
    {ir.TokenWrapper: lambda node: node.item, ir.kinds_other_than(ir.TokenWrapper): None},
)


def _check_matches(grammar: Mapping[str, ir.Prod]) -> list[str]:
    """
    `interpreter._evaluated_match` reads a `(match)` off the token under construction. It gives back the characters
    since the last token cut. That is the match of the rule itself where the rule matched inside a single `(token)`.
    Such a `(match)` must sit inside a `(token)`. The item of a `(token)` holding a `(match)` must hold no nested
    `(token)`.

    The check also walks into `(len)`, as `children` yields its argument. A `(match)` under `(len)` falls under the same
    rules. `interpreter._evaluated_length` reads the length of the last consume off a slot of its own.
    """
    errors = []
    for name, production in sorted(grammar.items()):
        cuts = [item for node in walk(production.body) if (item := _CUT_INTO_A_TOKEN(node)) is not None]
        for item in cuts:
            is_cut_again = any(_CUT_INTO_A_TOKEN(node) is not None for node in walk(item))
            if is_cut_again and any(ir.DOES_READ_THE_MATCH(node) for node in walk(item)):
                errors.append(f"{name}: a `(match)` reads a run a nested `(token)` has cut")
        covered = {id(node) for item in cuts for node in walk(item)}
        for node in walk(production.body):
            if ir.DOES_READ_THE_MATCH(node) and id(node) not in covered:
                errors.append(f"{name}: a `(match)` sits outside any `(token)`, and no run is its own")
    return errors


def _check_renaming(grammar: Mapping[str, ir.Prod]) -> list[str]:
    """
    A node's `renamed` rewrites exactly the names its `references` reports.

    A rename keeps `renamed` and `references` in step. The check maps the names a production references to themselves
    with a mark. The renamed production must report the marked names in order. A class that adds a field to reachability
    and leaves that field out of the rename fails here.
    """
    errors = []
    for name, production in grammar.items():
        wanted = [f"{held}!" for held in production.references()]
        got = production.renamed({held: f"{held}!" for held in production.references()}).references()
        if got != wanted:
            errors.append(f"{name}: renaming reports {got} where the references read {wanted}")
    return errors


# The values a finite parameter takes. A case on such a parameter has to name those values in full or have a default.
_FINITE_VALUES = {
    "c": annotated2ir.CONTEXTS,
    "i": annotated2ir.INDENT_MODES,
    "r": annotated2ir.RESUMES,
    "t": annotated2ir.CHOMPINGS,
}


def _check_total_cases(grammar: Mapping[str, ir.Prod]) -> list[str]:
    """
    Check that a case with no default is on a finite parameter and names a branch per value that parameter takes.

    A `CaseTree` silent about a value declines it. A reader then has to infer the production's behaviour from that
    absence. An absence can mean "it matches nothing" or "nobody asks". Those meanings differ, and the absence cannot
    say which holds. A `<fail>` branch names the decline in full. That node is an `ir.FailTree`. `interpreter._MATCHED`
    answers false for a `FailTree`. A `<fail>` branch says the case matches nothing.
    """
    return [
        fault
        for name, production in grammar.items()
        for node in walk(production.body)
        for fault in _TOTALITY_FAULTS(node, name)
    ]


def _case_faults(node: ir.CaseTree, name: str) -> tuple[str, ...]:
    """The faults `_check_total_cases` finds in `node`, a case of the production `name`."""
    if node.default is not None:
        return ()
    if node.var not in _FINITE_VALUES:
        return (f"{name}: the case is on {node.var}. nothing enumerates the values of that parameter.",)
    named = {branch.value for branch in node.branches}
    missing = [value for value in _FINITE_VALUES[node.var] if value not in named]
    return (f"{name}: the case on {node.var} names no branch for {', '.join(missing)}",) if missing else ()


# The faults a node gives `_check_total_cases`. A node other than a case gives no fault. An `ir.FlipValue` gives none
# either.
_TOTALITY_FAULTS: ir.Question[tuple[str, ...]] = ir.Question(
    "the faults a node gives the check of total cases",
    {ir.CaseTree: _case_faults, ir.kinds_other_than(ir.CaseTree): ()},
)


def _validate(grammar: Mapping[str, ir.Prod]) -> list[str]:
    """Return a list of human-readable validation errors (empty if the grammar is clean)."""
    errors = (
        _check_annotated(grammar) + _check_matches(grammar) + _check_renaming(grammar) + _check_total_cases(grammar)
    )
    referenced: set[str] = set()
    for name, production in grammar.items():
        for reference in (call for node in walk(production.body) if (call := ir.CALLED(node)) is not None):
            referenced.add(reference.name)
            if reference.name not in grammar:
                errors.append(f"{name}: reference to undefined production {reference.name!r}")
            elif len(reference.args) != len(grammar[reference.name].params):
                expected = len(grammar[reference.name].params)
                given = len(reference.args)
                errors.append(f"{name}: {reference.name!r} called with {given} argument(s) where it expects {expected}")
    for name in sorted(set(grammar) - referenced - _UNREFERENCED):
        errors.append(f"{name}: the grammar defines the production and nothing references it")
    for name in sorted(_UNREFERENCED - set(grammar)):
        errors.append(f"{name}: listed as unreferenced but no such production")
    return errors


def main() -> None:
    grammar = annotated2ir.load()
    gate.report(_validate(grammar), "grammar validation error(s)", f"grammar validation OK: {len(grammar)} productions")


if __name__ == "__main__":
    main()
