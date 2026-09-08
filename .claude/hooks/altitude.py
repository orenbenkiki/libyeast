#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
PreToolUse on Edit and Write. This hook refuses a private name cited in `DESIGN.md` as the writer writes it.

`DESIGN.md` is context, perspective and architecture. A private name belongs to the module that holds it. The docstring
beside that name explains it. A citation of a private name is the symptom of a passage saying what the source already
says. The detail then appears twice, and an edit reaches a single copy.

`check_documents.private_citation_errors` decides which citations are private. This hook imports that checker rather
than copying it. The checker runs over the lines as written. A citation appears in backticks. A checker that blanks a
code span finds nothing there.

The gate refuses the same citation minutes later. Refusing it here keeps the citation out of the file.

A session drove the sentence-shape meter to none across `DESIGN.md` while the altitude rule went unread, and this hook
came out of that. A meter with a single dimension is a meter a rewrite satisfies.

There is no import guard. A checker that cannot check must fail loudly. A silent checker and a clean edit read the same.

`.claude/settings.json` sets the `PYTHONPATH` that puts `generator` on the path.
"""

import json
import os
import sys

import check_documents
import collect_fragments
import refusal


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
        "Rule: each-document-keeps-to-its-domain."
    )


def main() -> None:
    payload = json.load(sys.stdin)
    edit = collect_fragments.edited(payload.get("tool_input", {}))
    if edit is None:
        return
    found = refusal_for(edit.now, edit.path)
    if found:
        refusal.refuse(found)


if __name__ == "__main__":
    main()
