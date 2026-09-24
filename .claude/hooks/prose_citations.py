#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook refuses prose citing a name the tree does not hold. `every-cited-name-exists` is
the rule.

The gate refuses the same citation minutes later. Refusing it here keeps the dangling name out of the file.

`check_documents.citation_refusal` decides the fault. This hook imports that checker rather than copying it. The names
the pipeline mints come off a generated file. This hook therefore runs no pipeline.

There is no import guard. A checker that cannot check must fail loudly. A silent checker and a clean edit read the same.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import tokenize

import check_documents


def minted_by(source: str) -> frozenset[str]:
    """
    The names `source` writes. A docstring of the same edit may cite one of them.

    `checkers` reads this off the file the edit would leave. A source the parser turns down mints nothing.
    """
    try:
        return frozenset(check_documents.names_written_by(source))
    except (SyntaxError, tokenize.TokenError, IndentationError, ValueError):  # not-a-failure: a half-written source
        return frozenset()


def refusal_for(prose: str, path: str, minted: frozenset[str] | None = None) -> str | None:
    """
    The refusal the citation rule gives for `prose` at `path`, or None where the citations pass.

    A caller with prose and no file asks here. `prose_answer` gives the critic's rewrite this same refusal.

    `minted` comes from `minted_by`. A caller that gives none has `minted_by` read `prose` where `path` names a Python
    file.
    """
    named = minted if minted is not None else (minted_by(prose) if path.endswith(".py") else frozenset())
    return check_documents.citation_refusal(path, list(enumerate(prose.split("\n"), start=1)), set(named))
