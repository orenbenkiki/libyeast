#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
`prose_gate` runs this module on Edit and Write through `checkers._CHECKERS`. This hook refuses a private name cited in
`DESIGN.md` as the writer writes it. `a-design-citation-keeps-its-altitude` is the rule.

`DESIGN.md` is context, perspective and architecture. A private name belongs to the module that holds it. The docstring
beside that name explains it. A passage citing a private name repeats that docstring.

`check_documents.private_citation_errors` decides which citations are private. This hook imports that checker rather
than copying it. `checkers.refusals_for_edit` hands the checker the flattened edit. A citation appears in backticks. A
checker that blanks a code span finds nothing there.

This hook imports the checker with no guard. A checker that cannot check must fail loudly. A silent checker and a clean
edit read the same.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import os

import check_documents


def refusal_for(prose: str, path: str) -> str | None:
    """
    The refusal the altitude rule gives for `prose` at `path`. The answer is None where `path` names no `DESIGN.md`, and
    None where the citations pass.

    The `altitude.py` row of `checkers._CHECKERS` asks here with the text and the path of an edit. `prose_answer` gives
    the critic's rewrite this same refusal through `draft_refusals`, `text_refusals` and `refusals_for_edit`.
    """
    if os.path.basename(path) != "DESIGN.md":
        return None
    found = check_documents.private_citation_errors(path, list(enumerate(prose.split("\n"), start=1)))
    if not found:
        return None
    cited = "; ".join(sorted({one.split("cites ")[1].split(",")[0] for one in found}))
    return (
        f"This edit leaves a citation of a private name in {path}: {cited}.\n\n"
        "DESIGN.md gives context, perspective and architecture. It repeats no code comment. A private "
        "name belongs to its module, and the docstring beside it explains that name.\n\n"
        "So the sentence is about the wrong altitude, and shortening it does not fix that. TAKE THE "
        "PASSAGE OUT. Where the architecture needs the point, say it in the words the architecture uses "
        "and name the piece rather than the function.\n\n"
        "Rule: a-design-citation-keeps-its-altitude."
    )
