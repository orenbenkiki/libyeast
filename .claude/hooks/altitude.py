#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook refuses a private name cited in `DESIGN.md` as the writer writes it.
`a-design-citation-keeps-its-altitude` is the rule.

`DESIGN.md` is context, perspective and architecture. A private name belongs to the module that holds it. The docstring
beside that name explains it. A passage citing a private name repeats that docstring.

`check_documents.private_citation_errors` decides which citations are private. This hook imports that checker rather
than copying it. The checker reads the lines the writer wrote. A citation appears in backticks. A checker that blanks a
code span finds nothing there.

`check_documents` refuses the same citation when the gate runs. This hook refuses the citation earlier and keeps it out
of the file.



This hook imports the checker with no guard. A checker that cannot check must fail loudly. A silent checker and a clean
edit read the same.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import os

import check_documents


def refusal_for(prose: str, path: str) -> str | None:
    """
    The refusal the altitude rule gives for `prose` at `path`, or None where the citations pass.

    A caller with prose and no file asks here. `prose_answer` gives the critic's rewrite this same refusal.
    """
    if os.path.basename(path) != "DESIGN.md":
        return None
    found = check_documents.private_citation_errors(path, list(enumerate(prose.split("\n"), start=1)))
    if not found:
        return None
    cited = "; ".join(sorted({one.split("cites ")[1].split(",")[0] for one in found}))
    return (
        f"This edit cites a private name in {path}: {cited}.\n\n"
        "DESIGN.md gives context, perspective and architecture. It repeats no code comment. A private "
        "name belongs to its module, and the docstring beside it explains that name.\n\n"
        "So the sentence is about the wrong altitude, and shortening it does not fix that. TAKE THE "
        "PASSAGE OUT. Where the architecture needs the point, say it in the words the architecture uses "
        "and name the piece rather than the function.\n\n"
        "Rule: a-design-citation-keeps-its-altitude."
    )
