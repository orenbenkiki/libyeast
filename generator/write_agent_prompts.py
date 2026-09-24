# SPDX-License-Identifier: MIT
"""
Write the conventions into the agent definitions that judge prose.

An agent definition file becomes the system prompt of its agent. A request caches the system prompt ahead of the
message. A sibling agent then reads the cached prompt. The same text in a message caches for nobody. So the text a
reader judges against belongs here.

`.claude/conventions.md` is the source of the rules. `.claude/rejected.md` is the source of the proposals the author
turned down. The critic and the condense hold that file too.

`batch_pending_fragments` is the source of the answer shape. `converge_prose` sends that same schema with the call.

A definition keeps its hand-written body. This writes the tools line of that body. This writes below
`collect_fragments.COPIED_BELOW` too. The text above that line stays as the author wrote it. `collect_fragments` stops
at the same line. The copy makes no fragment, and `a-piece-of-prose-is-written-once` holds.

**Usage:** `python3 generator/write_agent_prompts.py [--check]`. The check reports a definition the sources have moved
past. The check writes no file.
"""

import json
import os
import re
import sys

import batch_pending_fragments
import check_conventions
import collect_fragments
import gate

_USAGE = "usage: write_agent_prompts.py [--check]"  # the message a bad call gets.

# The definitions this writes, and the sources a definition holds. A comparator judges a change against a rule. A
# comparator raises no proposal. So no prompt hands a comparator the turned-down list.
_AGENTS = (
    ("prose-critic.md", ("answer", "conventions", "rejected")),
    ("prose-compare.md", ("answer", "conventions")),
    ("prose-condense.md", ("answer", "conventions", "rejected")),
)

# The file a source name points at.
_SOURCES = {"conventions": ".claude/conventions.md", "rejected": ".claude/rejected.md"}

# The heading over a copied source.
_HEADINGS = {
    "conventions": "The conventions",
    "rejected": "The proposals the author turned down",
    "answer": "The answer",
}

# The schema a definition states, by the definition's file name.
_SCHEMAS = {
    "prose-critic.md": batch_pending_fragments.EXAMINED,
    "prose-compare.md": batch_pending_fragments.JUDGED,
    "prose-condense.md": batch_pending_fragments.CONDENSED,
}

# The sentence over the schema. A reader answers in the shape the schema decides.
_ANSWERS = (
    "Answer by calling the StructuredOutput tool. The schema below decides the shape of the input to that call. Write "
    "no sentence beside the call. A hook may turn the call back with a refusal. Say the refused prose again, and call "
    "the tool again."
)

# The tools a definition names. The command withholds a tool the list leaves out. The answer arrives as a
# StructuredOutput call, and `prose_answer` reads that call. The inert tool keeps the agent from the default tools.
_TOOLS = ("mcp__inert__nothing", "StructuredOutput")

# The line in the head of a definition that names its tools.
_A_TOOLS_LINE = re.compile(r"^tools:.*$", re.MULTILINE)

# An item of the conventions file. The rule's name leads the item in bold.
_AN_ITEM = re.compile(r"^- \*\*([a-z0-9-]+)\*\*")

# `_SAID` holds the sentence that marks the line below which nobody edits the file.
_SAID = (
    "The text below comes from the files this heading names. `generator/write_agent_prompts.py` writes it. "
    "`make verify-agent-prompts` refuses a copy the sources have moved past. Edit the source rather than this file."
)


def _read(path: str) -> str:
    """The text `path` holds."""
    with open(os.path.join(gate.TREE, path), encoding="utf-8") as handle:
        return handle.read().strip("\n")


def _judged_rules(text: str) -> str:
    """
    `text`, the conventions file, less the items of the rules a checker decides in full.

    A checker decides such a rule before a reader sees the prose. `check_conventions.mechanised_rules` names those
    rules.
    """
    decided = check_conventions.mechanised_rules()
    held, taken = [], True
    for line in text.split("\n"):
        found = _AN_ITEM.match(line)
        if found:
            taken = found.group(1) not in decided
        elif line and not line.startswith(" "):
            taken = True
        if taken:
            held.append(line)
    return "\n".join(held)


def _said_by(name: str, source: str) -> str:
    """The text the source `source` puts under its heading in the definition `name`."""
    if source == "answer":
        return f"{_ANSWERS}\n\n```json\n{json.dumps(_SCHEMAS[name], indent=2)}\n```"
    said = _read(_SOURCES[source])
    return _judged_rules(said) if source == "conventions" else said


def _rendered(name: str, sources: tuple[str, ...]) -> str:
    """
    The whole text the definition `name` holds. The hand-written body above the marker keeps its text bar the tools.
    """
    path = os.path.join(gate.TREE, ".claude", "agents", name)
    with open(path, encoding="utf-8") as handle:
        was = handle.read()
    head = was.split(collect_fragments.COPIED_BELOW)[0].rstrip("\n")
    body = _A_TOOLS_LINE.sub(f"tools: {', '.join(_TOOLS)}", head, count=1)
    parts = [f"## {_HEADINGS[one]}\n\n{_said_by(name, one)}" for one in sources]
    return f"{body}\n\n{collect_fragments.COPIED_BELOW}\n\n{_SAID}\n\n" + "\n\n".join(parts) + "\n"


def main() -> None:
    """Write the definitions, or report a definition a check finds stale."""
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "--check"):
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    is_checking = len(sys.argv) == 2
    stale = []
    for name, sources in _AGENTS:
        path = os.path.join(gate.TREE, ".claude", "agents", name)
        said = _rendered(name, sources)
        with open(path, encoding="utf-8") as handle:
            if handle.read() == said:
                continue
        if is_checking:
            stale.append(f"`.claude/agents/{name}`: the sources have moved past this copy. write it again")
            continue
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(said)
        print(f"  `.claude/agents/{name}`: {len(said)} chars")
    if is_checking:
        said = "agent prompts: a definition that judges prose holds the conventions the tree holds"
        gate.report(stale, "stale agent definition(s)", said)


if __name__ == "__main__":
    main()
