# SPDX-License-Identifier: MIT
"""
Static validation of the grammar IR.

Loads libyeast's grammar (via `annotated2ir`) and checks that:
  * every referenced production exists,
  * every reference passes as many arguments as the target declares,
  * every production is referenced, so nothing is dead — except the `UNREFERENCED` ones, each of which must exist, and
  * every character the parser consumes lies within a token annotation.
Reports every problem found and exits non-zero if there are any.
"""

import chars
import ir
import annotated2ir
import gate


def walk(node):
    """Yield `node` and every IR node nested within it."""
    yield node
    for child in chars.children(node):
        yield from walk(child)


# The productions nothing references: the root the parser runs, and c-reserved — the reserved characters (§5.4), which
# the grammar defines but never uses. The indicator characters (§5.3) each have a production too, and those *are*
# referenced: libyeast reaches an indicator through the production that names it, so a token annotation has somewhere to
# attach.
UNREFERENCED = frozenset({"l-yeast-stream", "c-reserved"})


def consumed(node, is_annotated, references):
    """
    Yield, for each character `node` consumes, whether a token annotation covers it; collect the references reached.

    A lookahead consumes nothing and emits nothing, so what is inside one is neither counted nor followed. A `(---)`
    matches one character, so it counts as one; the characters it subtracts are operands, not matches.

    A kind named nowhere raises rather than being walked into for its children: a new way of taking a character would
    otherwise yield nothing of its own, and every character it takes would pass this check without an annotation.
    """
    if isinstance(node, ir.Token):
        yield from consumed(node.item, True, references)
    elif isinstance(node, (*ir.ZERO_WIDTH, ir.LiteralPeek)):
        return  # reads the input and gives it back; what is inside is a question, not a match
    elif isinstance(node, ir.CONSUMING):
        yield is_annotated
    elif isinstance(node, ir.Ref):
        references.append((node.name, is_annotated))
    elif isinstance(node, ir.KINDS):
        for child in chars.children(node):
            yield from consumed(child, is_annotated, references)
    else:
        raise TypeError(f"cannot tell what {type(node).__name__} consumes")


def check_annotated(grammar):
    """
    Every character the parser consumes must lie within a token annotation.

    A character consumed outside one is given the code `unparsed` — what the parser says about input it could not parse
    — and would reach the caller as a token saying so. On the success path that is always an annotation someone forgot,
    and nothing else would catch it until the token stream was compared against the reference.

    A production is reached with an annotation around it, or without one, or both; whichever it is propagates from the
    root through every reference, until it settles.
    """
    reached = {name: set() for name in grammar}
    reached[ir.ROOT].add(False)
    is_settled = False
    while not is_settled:
        is_settled = True
        for name, production in grammar.items():
            for is_annotated in list(reached[name]):
                references = []
                list(consumed(production.body, is_annotated, references))
                for target, is_covered in references:
                    if target in reached and is_covered not in reached[target]:
                        reached[target].add(is_covered)
                        is_settled = False

    errors = []
    for name, production in sorted(grammar.items()):
        for is_annotated in reached[name]:
            if not all(consumed(production.body, is_annotated, [])):
                errors.append(f"{name}: consumes a character outside any token annotation, which would emit `unparsed`")
                break
    return errors


def check_matches(grammar):
    """
    Every `(match)` reads the open run, so what it returns is the text since the last token cut. That is the rule's own
    match only where the rule matched inside one `(token)` and nothing before it in that token was consumed by another:
    a `(match)` must stand in a `(token)` whose item it is the whole of, past what that item itself matched.
    """
    errors = []
    for name, production in sorted(grammar.items()):
        holders = [node for node in walk(production.body) if isinstance(node, ir.Token)]
        for token in holders:
            inner = [node for node in walk(token.item) if isinstance(node, ir.Token)]
            if inner and any(isinstance(node, ir.Match) for node in walk(token.item)):
                errors.append(f"{name}: a `(match)` reads a run a nested `(token)` has cut")
        covered = {id(node) for token in holders for node in walk(token.item)}
        for node in walk(production.body):
            if isinstance(node, ir.Match) and id(node) not in covered:
                errors.append(f"{name}: a `(match)` stands outside any `(token)`, so no run is its own")
    return errors


def check_renaming(grammar):
    """
    Every node's `renamed` rewrites exactly the names its `references` reports.

    The two are written side by side on each class, because a class's fields mean different things — a call is not a
    continuation, a protected match is not the handler that answers for it — and neither can be derived from the other.
    What keeps them in step is this: rename every name in the grammar to itself with a mark, and what comes back must
    report the marked names and nothing else. A class that lists a field to reachability and forgets it in the rewrite
    fails here, where the sweep would otherwise leave a call pointing at a production that has been merged away.
    """
    errors = []
    for name, production in grammar.items():
        wanted = [f"{held}!" for held in production.references()]
        got = production.renamed({held: f"{held}!" for held in production.references()}).references()
        if got != wanted:
            errors.append(f"{name}: renaming reports {got}, where its references are {wanted}")
    return errors


def validate(grammar):
    """Return a list of human-readable validation errors (empty if the grammar is clean)."""
    errors = check_annotated(grammar) + check_matches(grammar) + check_renaming(grammar)
    referenced = set()
    for name, prod in grammar.items():
        for ref in (n for n in walk(prod.body) if isinstance(n, ir.Ref)):
            referenced.add(ref.name)
            if ref.name not in grammar:
                errors.append(f"{name}: reference to undefined production {ref.name!r}")
            elif len(ref.args) != len(grammar[ref.name].params):
                expected = len(grammar[ref.name].params)
                errors.append(f"{name}: {ref.name!r} called with {len(ref.args)} argument(s), expects {expected}")
    for name in sorted(set(grammar) - referenced - UNREFERENCED):
        errors.append(f"{name}: production is defined but never referenced")
    for name in sorted(UNREFERENCED - set(grammar)):
        errors.append(f"{name}: listed as never referenced but no such production")
    return errors


def main():
    grammar = annotated2ir.load()
    gate.report(validate(grammar), "grammar validation error(s)", f"grammar validation OK: {len(grammar)} productions")


if __name__ == "__main__":
    main()
