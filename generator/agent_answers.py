# SPDX-License-Identifier: MIT
"""
The asks the prose agents read, and the shapes they answer in.

A critic reads the prose of a batch of fragments. A record also holds the code under a note, and the record tells the
critic to write no part of that code. `tiled_faults` and `claim_changes` read the answer of a claims reader.
"""

import difflib
import os
import re

from typing import Any

import check_conventions
import gate

# The most rules `rules_by_use` names.
_RULES_SHOWN = 8

# A critic cites a rule this many times before `rules_by_use` lists its share turned down.
_FEWEST_CITED = 100

# The proposals a reader raises from a batch. A reader holds a batch and no view of the tree. The reader cannot sweep a
# pattern and cannot see how far a rule would reach. `.claude/agents/prose-critic.md` gives the reader no tool that
# reads the tree. A cap makes the reader pick rather than list. The critic's schema states the cap, and the condense
# schema states none.
MOST_PROPOSALS = 1

# The heading `rules_by_use` writes above its list.
_BROKEN_RULES = (
    "## How your changes have fared\n\n"
    "A settling round cited these rules. The comparator read the rewrite against the prose it replaces, and the "
    "counts say how that went. A rule turned down often is a rule this project's writers reach for and misapply."
)

# `converge_prose` puts this above the prose of a critic's batch. `.claude/agents/prose-critic.md` says how a critic
# answers.
ASK = (
    "Judge the writing of this prose. Do not judge whether it is true.\n\n"
    "Your definition holds the conventions you judge against. It holds the proposals the author has turned down. "
    "Your batch comes below. Answer with a verdict per key of that batch. A key of another batch belongs to another "
    "reader. An answer leaving a key out comes back to you.\n\n"
    "A rewrite is a list of changes. A change cites by name the rule of `.claude/conventions.md` the old text "
    "breaks. Drop a change you cannot cite a rule for."
)

# The description the verdicts of an answer take.
_VERDICTS_SAY = "a verdict per key of the batch. a key outside that batch belongs to another reader"

# The verdict a reader gives a fragment of its batch.
_A_VERDICT: dict[str, Any] = {
    "type": "object",
    "required": ["verdict"],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["passed", "rewritten", "out_of_budget"],
            "description": "your finding for this fragment. `passed` says the prose is sound. `rewritten` says you "
            "rewrote areas of the prose, and `changes` then holds them. `out_of_budget` says you ran out of room "
            "to read it",
        },
        "changes": {
            "type": "array",
            "description": "a change per area of the text you rewrote. cite a rule for an area or make no change "
            "there",
            "items": {
                "type": "object",
                "required": ["old", "new", "rule"],
                "properties": {
                    "old": {
                        "type": "string",
                        "description": "the text you replace, copied out of the fragment character for character",
                    },
                    "new": {"type": "string", "description": "the text that replaces `old`"},
                    "rule": {
                        "type": "string",
                        "description": "the name of the bullet of `.claude/conventions.md` the old text breaks",
                    },
                },
            },
        },
    },
}

# The rest of the shape a reader answers in. `EXAMINED` puts the verdicts of a batch beside it.
_PROPOSED: dict[str, Any] = {
    "properties": {
        "proposals": {
            "type": "array",
            "maxItems": MOST_PROPOSALS,
            "description": "a tightening of a write-time checker that would have caught a fault you rewrote. a batch "
            "raises the proposal you believe in hardest and leaves a weaker proposal unsaid",
            "items": {
                "type": "object",
                "required": ["rule", "why", "seen", "before", "after"],
                "properties": {
                    "rule": {
                        "type": "string",
                        "description": "the checker change, stated the way `.claude/conventions.md` states a rule. a "
                        "hyphenated name, then what the checker refuses. the word rules read this wording, and they "
                        "refuse a universal and a count.",
                    },
                    "why": {
                        "type": "string",
                        "description": "the gain for a reader, and what goes wrong without the change",
                    },
                    "seen": {
                        "type": "string",
                        "description": "the key of the fragment you rewrote, and no sentence beside it. `before` "
                        "quotes the prose, and a quote outside backticks breaks the shape rules where it lands",
                    },
                    "before": {
                        "type": "string",
                        "description": "a short quote of prose the proposed checker refuses",
                    },
                    "after": {
                        "type": "string",
                        "description": "the same prose rewritten into the form the proposed checker passes",
                    },
                },
            },
        },
    },
}


# The answer shape a batch takes. The shape holds still between calls. The fixed prefix in front of a batch then stays
# in the cache, as https://platform.claude.com/docs/en/build-with-claude/prompt-caching says. `_VERDICTS_SAY` tells a
# reader to give a verdict per key of its batch.
EXAMINED: dict[str, Any] = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "object",
            "description": _VERDICTS_SAY,
            "additionalProperties": _A_VERDICT,
        },
        **_PROPOSED["properties"],
    },
}


# The sides of a pair a comparator may pick. `converge_prose` reads a verdict against these.
SIDES = ("A", "B")

# The verdict a comparator gives where neither side reads better. `converge_prose` rejects the change then.
A_TIE = "equivalent"

# The shape a comparator answers in. A verdict names the pair it judged, and the batch fixes no id in this shape.
JUDGED: dict[str, Any] = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "description": "a verdict per pair in the batch, in the order the pairs arrive",
            "items": {
                "type": "object",
                "required": ["pair", "verdict"],
                "properties": {
                    "pair": {"type": "string", "description": "the pair's id, written as the batch writes it"},
                    "verdict": {
                        "type": "string",
                        "enum": [*SIDES, A_TIE],
                        "description": "the version that reads better, or `equivalent` where neither does",
                    },
                },
            },
        },
    },
}

_SOUND = "sound"  # The verdict a run takes where the code bears the run out.

# The verdict a run takes where the reader rewrote the run. `converge_claims` writes such a run into the tree.
_CHANGED = "changed"

# The verdict a run takes where the fetches left it open. `converge_claims` files the fragment of such a run in the
# unsettled map. `claim_state._NOTE` says the author edits a fragment there.
UNVERIFIABLE = "unverifiable"

# The verdict a run takes where its claim rests on a source outside the tree. The run links that source. Outside a file
# of `_CONTEXT_DOCUMENTS`, the run cites the code that relies on the claim. `converge_claims` settles such a run as it
# settles a sound one.
_EXTERNAL = "external"

# The verdicts a run may take, in the order a refusal names them.
_VERDICTS = (_SOUND, _CHANGED, UNVERIFIABLE, _EXTERNAL)

# A link to a source outside the tree. An `_EXTERNAL` run holds such a link in its text.
_A_LINK = re.compile(r"https?://\S+")

# The rule a change from a claims reader cites. A claims reader judges whether the code bears the prose out. The bullet
# `every-claim-is-checked-before-it-is-written` of `.claude/conventions.md` states that rule.
_A_CLAIM_RULE = "every-claim-is-checked-before-it-is-written"

# A citation of the code a reader read. A citation names a fragment and a run of lines in the code of that fragment.
# `collect_fragments.said_by` writes the numbers the reader counts by, and the count starts at the fragment's own first
# line of code.
_A_CITATION: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "required": ["name", "from", "to"],
        "properties": {
            "name": {
                "type": "string",
                "description": "the fragment holding the code, written the way a fetch writes a name",
            },
            "from": {"type": "integer", "description": "the first line of the code, as the fragment numbers its code"},
            "to": {"type": "integer", "description": "the last line of the code, as the fragment numbers its code"},
        },
    },
}

# A run of a fragment's prose, and what a claims reader found in it. The runs of an answer tile the prose. A run begins
# where the run before it ended. A reader that skipped a run of the prose therefore leaves a gap, and `tiled_faults`
# reports that gap. A reader chooses where a run ends. `.claude/agents/prose-claims.md` tells the reader that a run
# holds as much text as one claim needs.
_A_CHUNK: dict[str, Any] = {
    "type": "object",
    "required": ["text", "verdict"],
    "properties": {
        "text": {
            "type": "string",
            "description": "this run of the prose, copied out of the fragment character for character. the runs of "
            "your answer join back into the whole prose",
        },
        "verdict": {
            "type": "string",
            "enum": list(_VERDICTS),
            "description": "your finding for this run. `sound` says the code bears it out, and `shown_by` cites that "
            "code. `changed` says the code contradicts it. such a run says the rewrite under `new`. `unverifiable` "
            "says your fetches left it open, and `looked_for` then says what you fetched. `external` says the claim "
            "rests on a source outside the tree that the text links, and `shown_by` cites the code relying on it. in "
            "`DESIGN.md` such a run may cite no code",
        },
        "new": {"type": "string", "description": "the text that replaces this run. a `changed` run holds it"},
        "wrong": {
            "type": "string",
            "description": "the thing the code does that this run denies. a `changed` run holds it",
        },
        "shown_by": {
            **_A_CITATION,
            "description": "the code that bears this run out. a `sound` run cites the code behind its text, and a "
            "`changed` run cites the code behind `new`",
        },
        "denied_by": {
            **_A_CITATION,
            "description": "the code that makes the text of this run false. a `changed` run holds it",
        },
        "looked_for": {
            "type": "string",
            "description": "the names you fetched for this run, and what those answers left open. an `unverifiable` "
            "run holds it",
        },
    },
}

# The findings a claims reader gives its fragment. The runs tile the prose, and a run states its own verdict.
# `converge_claims` reads the runs and decides what the fragment takes next.
_A_CLAIM: dict[str, Any] = {
    "type": "object",
    "required": ["chunks"],
    "properties": {
        "chunks": {
            "type": "array",
            "description": "the runs of this fragment's prose, in the order the fragment writes them",
            "items": _A_CHUNK,
            "minItems": 1,
        },
    },
}


def _unbacked_faults(at: int, run: dict[str, Any], verdict: str) -> list[str]:
    """
    The faults of a run under `_SOUND` or `_CHANGED`, and of an `external` run outside a file of `_CONTEXT_DOCUMENTS`. A
    run under `UNVERIFIABLE` goes by `looked_for` instead. `prose_answer._unread_names` checks the fragments that
    `looked_for` names.

    A decided run cites the code it rests on under `shown_by`. A `_CHANGED` run also holds the text that replaces the
    run, and it holds under `wrong` the thing the code does. Such a run cites under `denied_by` the code that makes the
    old text false. A reader deciding a run off its own taste has none of this to give.
    """
    faults = []
    if not list(run.get("shown_by") or []):
        faults.append(f"run {at} says `{verdict}` and cites no `shown_by`. cite the code that bears the text out.")
    if verdict != _CHANGED:
        return faults
    if not str(run.get("new") or ""):
        faults.append(f"run {at} says `{_CHANGED}` and holds no `new`. put the text that replaces the run there.")
    if not str(run.get("wrong") or ""):
        faults.append(
            f"run {at} says `{_CHANGED}` and holds no `wrong`. say the thing the code does that the run denies."
        )
    if not list(run.get("denied_by") or []):
        faults.append(f"run {at} says `{_CHANGED}` and cites no `denied_by`. cite the code that makes the text false.")
    return faults


# The documents that give context. An `external` run there links its source, and no code has to rely on that source.
_CONTEXT_DOCUMENTS = ("DESIGN.md",)


def tiled_faults(prose: str, chunks: Any, path: str = "") -> list[str]:
    """
    The faults of a tiling of `prose`. An empty answer says the runs join back into the prose.

    A run holds its text. A run under `_CHANGED` holds its replacement. The runs join into `prose` and into nothing
    besides. `prose_answer` hands these faults back to the reader, and the reader answers again. `converge_claims` and
    `converge_prose` stop on these faults.

    `path` names the fragment's file. An `external` run in a file of `_CONTEXT_DOCUMENTS` may cite no `shown_by`.

    Public. `tiling` reads an answer this way. `prose_answer` reads an empty answer this way too.
    """
    if not isinstance(chunks, list) or not chunks:
        return ["your answer holds no run of the prose. answer with the runs, in the order the fragment writes them."]
    is_context = os.path.basename(path) in _CONTEXT_DOCUMENTS
    faults = []
    for at, one in enumerate(chunks):
        if not isinstance(one, dict):
            faults.append(f"run {at} is no object. a run holds `text` and `verdict`.")
            continue
        if not str(one.get("text") or ""):
            faults.append(f"run {at} holds no `text`. copy the run's text out of the fragment.")
        verdict = str(one.get("verdict") or "")
        if verdict not in _VERDICTS:
            named = ", ".join(f"`{one}`" for one in _VERDICTS)
            faults.append(f"run {at} takes the verdict {verdict!r}. give the run one of {named}.")
        if verdict in (_SOUND, _CHANGED) or (verdict == _EXTERNAL and not is_context):
            faults += _unbacked_faults(at, one, verdict)
        if verdict == _EXTERNAL and not _A_LINK.search(str(one.get("text") or "")):
            faults.append(
                f"run {at} says `{_EXTERNAL}` and its text links no source. a run resting on a source outside the tree "
                f"links that source. a run whose text links none is `{UNVERIFIABLE}`."
            )
    joined = "".join(str(one.get("text") or "") for one in chunks if isinstance(one, dict))
    if joined != prose:
        at = next((n for n, (a, b) in enumerate(zip(joined, prose)) if a != b), min(len(joined), len(prose)))
        faults.append(
            f"your runs join into text the fragment does not hold. the join and the prose agree up to character {at}. "
            f"the prose reads {prose[at:at + 80]!r} there, and your runs read {joined[at:at + 80]!r}. "
            "answer again with runs that join back into the prose, and copy the text of a run out of the fragment."
        )
    return faults


def claim_changes(chunks: list[dict[str, Any]]) -> list[dict[str, str]]:
    """
    The changes the runs under `_CHANGED` ask for. A change takes the text of its run as `old` and the replacement as
    `new`.

    `tiled_faults` holds the runs of a tiling to the whole prose. `collect_fragments._joined` puts a blank line between
    the prose of a pair of sites. `collect_fragments.flattened` keeps that blank line, and it falls into some run. That
    break belongs to the tiling rather than to the text a site holds. A change leaves the newlines at either end of its
    run out of `old` and out of `new`.

    Public. `prose_answer`, `converge_claims` and `converge_prose` read a claims answer this way.
    """
    return [
        {
            "old": str(one.get("text") or "").strip("\n"),
            "new": str(one.get("new") or "").strip("\n"),
            "rule": _A_CLAIM_RULE,
        }
        for one in chunks
        if one.get("verdict") == _CHANGED
    ]


def runs_of(chunks: Any) -> list[dict[str, Any]]:
    """
    The runs of `chunks` that are objects.

    Public. `tiling` reads the runs of a claims answer this way. `prose_answer` reads them again after `tiling`.
    """
    return [one for one in chunks or [] if isinstance(one, dict)]


def tiling(prose: str, chunks: Any, path: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """
    The runs of a claims answer over `prose`, and the faults of that tiling. `tiled_faults` gives the faults, and it
    reports a run that is no object. `runs_of` leaves such a run out of the runs. `path` names the fragment's file.

    Public. `prose_answer`, `converge_claims` and `converge_prose` read a claims answer this way.
    """
    return runs_of(chunks), tiled_faults(prose, chunks, path)


def batch_mismatch(spoken: set[str], batch: set[str]) -> tuple[list[str], list[str]]:
    """
    The sorted keys of `spoken` outside `batch`, and the sorted keys of `batch` that `spoken` lacks. `spoken` holds the
    keys a critic answer gives a verdict.

    Public. `prose_answer` refuses a critic answer this finds a key for. `converge_prose` stops a run on such an answer.
    """
    return sorted(spoken - batch), sorted(batch - spoken)


def rewritten_spans(was: str, now: str) -> list[tuple[int, int]]:
    """
    The spans of `now` that a rewrite of `was` changed, as `(start, end)` offsets. A deletion spans no character.

    Public. `converge_prose` and `converge_claims` pass the spans to `does_correct_a_rewrite`.
    """
    matcher = difflib.SequenceMatcher(None, was, now, autojunk=False)
    return [(start, end) for tag, _, _, start, end in matcher.get_opcodes() if tag != "equal"]


def does_correct_a_rewrite(runs: list[dict[str, Any]], spans: list[tuple[int, int]]) -> bool:
    """
    Whether a run under `_CHANGED` overlaps a span of `spans`. The runs tile the prose. A run then starts where the runs
    ahead of it end. A run touching an insertion at its edge overlaps that insertion.

    Public. `converge_prose` and `converge_claims` send a fragment to a person where this answers yes.
    """
    at = 0
    for run in runs:
        start, at = at, at + len(str(run.get("text") or ""))
        if claim_changes([run]) and any(
            first < at and start < last or first == last and start <= first <= at for first, last in spans
        ):
            return True
    return False


# The shape a claims reader answers in. This shape holds the findings. A claim pass raises no proposal.
CLAIMED: dict[str, Any] = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "object",
            "description": "the findings for the key your prompt holds. a key outside it belongs to another reader",
            "additionalProperties": _A_CLAIM,
            "minProperties": 1,
        },
    },
}

# The shape a condense answers in. The critic states a proposal in that same item shape.
CONDENSED: dict[str, Any] = {
    "type": "object",
    "required": ["proposals"],
    "properties": {
        "proposals": {key: value for key, value in EXAMINED["properties"]["proposals"].items() if key != "maxItems"}
    },
}


def rules_by_use() -> str:
    """
    A line per rule, saying how often a critic cited that rule and how often the comparator turned the change down.

    Public. `converge_prose` puts this in front of a critic. `.claude/hooks/standing-rules.sh` puts it in front of the
    session at the keyboard. The pair reads this function and cannot drift apart.

    A rule cited often and turned down often is a rule a writer reaches for and misapplies. `.claude/rule-use.json`
    holds the count cited and the count accepted. The count turned down is the count cited less the count accepted. The
    list runs in the order of the share turned down, and stops at `_RULES_SHOWN` rules. A rule
    `check_conventions.mechanised_rules` names stays out. A rule cited fewer times than `_FEWEST_CITED` stays out as
    well.
    """
    decided = check_conventions.mechanised_rules()
    held = [
        (one.get("cited", 0) - one.get("accepted", 0), one.get("cited", 0), name)
        for name, one in gate.rule_use().items()
        if name not in decided and one.get("cited", 0) >= _FEWEST_CITED
    ]
    if not held:
        return ""
    ranked = sorted(held, key=lambda one: (-one[0] / one[1], -one[1]))[:_RULES_SHOWN]
    lines = "\n".join(
        f"- `{name}`: cited {cited} time(s), and the comparator turned down {rejected} of them "
        f"({rejected / cited:.0%})"
        for rejected, cited, name in ranked
    )
    return f"{_BROKEN_RULES}\n\n{lines}"


def sited(fragment: dict[str, Any]) -> str:
    """
    The prose a reader sees. A fragment gets a heading per site unless its only site is no note. A heading names the
    role of its site. `collect_fragments._Split.role` gives a site its role. A role is a `docstring`, a `comment` or a
    `note`. The code under a note follows that note.

    A struct's own comment and the comment on a field are sites apart. `collect_fragments._Split.sites` makes a field's
    comment a site of its own.

    Public. `converge_prose` lays out the prose of a critic's batch this way.
    """
    sites = fragment["sites"]
    if len(sites) < 2 and sites and sites[0].get("role") != "note":
        return f"{fragment['prose']['content']}\n"
    said = []
    for at, site in enumerate(sites, start=1):
        said.append(f"--- site {at}/{len(sites)} {site['path']} ({site.get('role', 'comment')})\n{site['prose']}")
        if site.get("code_below"):
            said.append(f"--- code under the note. Read it. Write no part of it.\n{site['code_below']}")
    return "\n".join(said) + "\n"


def record_of(fragment: dict[str, Any]) -> str:
    """
    Write a fragment as a record for a critic's prompt. The record opens with the key. A line then gives the path and
    the kind. The prose comes last.

    Public. `converge_prose` writes the seed of a critic this way.
    """
    return f"### {fragment['key']}\n" f"{fragment['path']} ({fragment['kind']})\n" f"{sited(fragment)}"
