# SPDX-License-Identifier: MIT
"""
Check that the grammar's error sites and the message table agree.

`grammar/messages.yaml` holds the message table. A `(cut)` in `yeast-spec-1.2.yaml` names a code from that table. So
does an `(error)`. So do a `(max)` and a `(commit)`. An action names a message in the table. A renamed code or an
orphaned message fails the build. Character-level errors do not depend on the grammar. The table holds no
character-level error.
"""

import os

import annotated2ir
import chars
import gate
import ir
import yaml

MESSAGES = os.path.join(os.path.dirname(annotated2ir.DEFAULT_GRAMMAR), "messages.yaml")  # the grammar's own messages.


def _named_codes(grammar: dict[str, ir.Prod]) -> set[str]:
    """
    The message codes that the actions of `grammar` name. A `(cut)` names a code. So does an `(error)`. So does a
    wrapping `(max)` or a `(commit)`.
    """
    named = set(
        chars.gathered(
            grammar, (ir.CutAction, ir.ErrorAction, ir.MaxWrapper, ir.CommitWrapper), lambda node: node.message
        )
    )
    return named - {None}  # the vendored grammar's bare `(max)` names no message


def codes() -> dict[str, str]:
    """
    `{a message code: its text}`, as `grammar/messages.yaml` holds them.

    Public. `check_documents` takes a code for a name of the tree.
    """
    with open(MESSAGES, encoding="utf-8") as handle:
        held: dict[str, str] = yaml.safe_load(handle)
    return held


def main() -> None:
    messages = codes()
    named = _named_codes(annotated2ir.load())

    errors = [
        f"the grammar names {code} and `grammar/messages.yaml` holds no message for it"
        for code in sorted(named - set(messages))
    ]
    errors += [
        f"`grammar/messages.yaml` defines {code} and nothing in the grammar names it"
        for code in sorted(set(messages) - named)
    ]

    gate.report(
        errors,
        "disagreement(s) between the messages and the grammar",
        f"messages: {len(messages)} defined, and the grammar names them",
    )


if __name__ == "__main__":
    main()
