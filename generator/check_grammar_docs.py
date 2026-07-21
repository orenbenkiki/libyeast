# SPDX-License-Identifier: MIT
"""
Check that libyeast's grammar documents the tokens it emits.

The grammar it derives from is a document: every rule is preceded by its spec BNF, and a rule that needs explaining gets
a section of prose. libyeast's additions deserve no less — a reader should not have to work out for themselves why
`c-quoted-quote` marks the second quote `meta` while the first is an `indicator`.

So every rule that carries a token action must be preceded by an `Emits:` line naming the codes it emits, in the order
it emits them. That the codes are checked against the grammar itself, rather than merely required to be present, is what
keeps the comment from drifting: a note that is wrong fails the build exactly as a note that is missing does.
"""

import re

import annotated2ir
import gate
import chars
import ir

EMITS = re.compile(r"^#\s*Emits:\s*(.*)$", re.M)


def emitted(node):
    """The codes a node emits, in the order it emits them, without repeats."""
    codes = []
    if isinstance(node, ir.Token):
        codes.append(node.code)
        codes.extend(emitted(node.item))
    elif isinstance(node, ir.Wrap):
        codes.append(node.begin)
        codes.extend(emitted(node.item))
        codes.append(node.end)
    elif isinstance(node, ir.Emit):
        codes.append(node.code)
    else:
        for child in chars.children(node):
            codes.extend(emitted(child))
    return list(dict.fromkeys(codes))


def documented(text):
    """The codes each rule's comment block says it emits: `{name: [code, ...]}`, for the rules that say so."""
    said = {}
    for match in re.finditer(r"^:\d+: ([\w+.-]+)\n((?:#.*\n)*)", text, re.M):
        emits = EMITS.search(match.group(2))
        if emits:
            said[match.group(1)] = [code.strip() for code in emits.group(1).split(",") if code.strip()]
    return said


def main():
    grammar = annotated2ir.load()
    with open(annotated2ir.DEFAULT_GRAMMAR) as handle:
        said = documented(handle.read())

    errors = []
    for name, production in grammar.items():
        codes = emitted(production.body)
        if not codes:
            if name in said:
                errors.append(f"{name}: says it emits {said[name]}, but it emits nothing")
            continue
        if name not in said:
            errors.append(f"{name}: emits {', '.join(codes)}, and says nothing about it")
        elif said[name] != codes:
            errors.append(f"{name}: says it emits {', '.join(said[name])}, but it emits {', '.join(codes)}")

    emitting = sum(1 for name in grammar if emitted(grammar[name].body))
    gate.report(
        errors,
        "undocumented or misdocumented rule(s)",
        f"grammar documented: {emitting} rules emit tokens, all said",
    )


if __name__ == "__main__":
    main()
