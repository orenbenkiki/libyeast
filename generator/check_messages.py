# SPDX-License-Identifier: MIT
"""Check that the grammar's error sites and the message table agree.

Every `(cut): CODE` and `(error): CODE` in `yeast-spec-1.2.yaml` must name a code defined in `grammar/messages.yaml`,
and every message must be named by one of them. This keeps the error sites and their messages the single source they
share, so a renamed code or an orphaned message fails the build. The character-level errors are grammar-independent and
live elsewhere; they are not in this table.
"""

import os

import annotated2ir
import gate
import ir
import yaml

MESSAGES = os.path.join(os.path.dirname(annotated2ir.DEFAULT_GRAMMAR), "messages.yaml")


def named_codes(grammar):
    """The set of message codes named by a `(cut)` or an `(error)` anywhere in `grammar`."""
    codes = set()
    for production in grammar.values():
        stack = [production.body]
        while stack:
            node = stack.pop()
            if isinstance(node, (ir.Cut, ir.Error)):
                codes.add(node.message)
                continue
            for field in getattr(node, "__dataclass_fields__", ()):
                value = getattr(node, field)
                for child in value if isinstance(value, tuple) else (value,):
                    if hasattr(child, "__dataclass_fields__"):
                        stack.append(child)
    return codes


def main():
    with open(MESSAGES) as handle:
        messages = yaml.safe_load(handle)
    named = named_codes(annotated2ir.load())

    errors = [
        f"{code} is named in the grammar but has no message in messages.yaml" for code in sorted(named - set(messages))
    ]
    errors += [
        f"message {code} is defined but nothing in the grammar names it" for code in sorted(set(messages) - named)
    ]

    gate.report(
        errors, "message/grammar disagreement(s)", f"messages: {len(messages)} defined, all named by the grammar"
    )


if __name__ == "__main__":
    main()
