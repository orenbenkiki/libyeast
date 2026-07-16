# SPDX-License-Identifier: MIT
"""Check that the grammar's error cuts and the message table agree.

Every `(cut): CODE` in `yeast-spec-1.2.yaml` must name a code defined in `grammar/messages.yaml`, and every message must
be named by a cut. This keeps the cut sites and their messages the single source they share, so a renamed code or an
orphaned message fails the build. The character-level errors are grammar-independent and live elsewhere; they are not
in this table.
"""

import os

import annotated2ir
import gate
import ir
import yaml

MESSAGES = os.path.join(os.path.dirname(annotated2ir.DEFAULT_GRAMMAR), "messages.yaml")


def cut_codes(grammar):
    """The set of message codes named by a `(cut)` anywhere in `grammar`."""
    codes = set()
    for production in grammar.values():
        stack = [production.body]
        while stack:
            node = stack.pop()
            if isinstance(node, ir.Cut):
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
    cuts = cut_codes(annotated2ir.load())

    errors = [f"(cut): {code} names no message in messages.yaml" for code in sorted(cuts - set(messages))]
    errors += [f"message {code} is defined but no cut uses it" for code in sorted(set(messages) - cuts)]

    gate.report(errors, "message/cut disagreement(s)", f"messages: {len(messages)} defined, all named by a cut")


if __name__ == "__main__":
    main()
