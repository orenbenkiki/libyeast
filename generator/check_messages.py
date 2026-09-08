# SPDX-License-Identifier: MIT
"""
Check that the grammar's error sites and the message table agree.

A `(cut)`, `(error)`, `(max)` or `(commit)` in `yeast-spec-1.2.yaml` that names a code names a code defined in
`grammar/messages.yaml`. An action names a message in that table. The error sites and the message table stay a shared
source. A renamed code or an orphaned message fails the build. The character-level errors do not depend on the grammar
and live elsewhere. This table does not hold them.
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
    codes = set(
        chars.gathered(
            grammar, (ir.CutAction, ir.ErrorAction, ir.MaxWrapper, ir.CommitWrapper), lambda node: node.message
        )
    )
    return codes - {None}  # the vendored grammar's bare `(max)` names no message


def main() -> None:
    with open(MESSAGES, encoding="utf-8") as handle:
        messages = yaml.safe_load(handle)
    named = _named_codes(annotated2ir.load())

    errors = [
        f"the grammar names {code} and messages.yaml holds no message for it" for code in sorted(named - set(messages))
    ]
    errors += [
        f"messages.yaml defines {code} and nothing in the grammar names it" for code in sorted(set(messages) - named)
    ]

    gate.report(
        errors, "message/grammar disagreement(s)", f"messages: {len(messages)} defined, and the grammar names them"
    )


if __name__ == "__main__":
    main()
