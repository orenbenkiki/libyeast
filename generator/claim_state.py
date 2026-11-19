# SPDX-License-Identifier: MIT
"""
The store holding what a claim pass verified. The store also names the fragments the pass must read once more.

A claim pass reads a fragment's prose against its code. The pass records the fragment and the digests it held. The pass
keeps the keys of the fetched fragments that a run of the answer cites. The pass keeps the questions it asked, cited or
not. `.claude/prose-dependencies.json` holds that record. `.gitignore` holds no pattern matching the file. A clone then
verifies what moved rather than the whole tree.

A fragment falls stale where its own prose or code moved. A fragment falls stale where the code of a fragment it read
moved. A fragment falls stale where a fragment it read left the tree. A stale fragment goes back to the pass.

**Usage:** `python3 generator/claim_state.py`. The run names the fragments the pass owes a reading. The run also names
the fragments the store holds unsettled. The run fails where it names any fragment.
"""

import dataclasses
import hashlib
import json
import os

from collections.abc import Iterable, Mapping
from typing import Any

import collect_fragments
import gate
import settling_state

# The store.
_STORE = os.path.join(gate.TREE, ".claude", "prose-dependencies.json")

# The note at the head of the store. A reader of the file reads this first.
_NOTE = (
    "A claim pass reads a fragment's prose against its code and records the reading here. `own` holds the digests the "
    "pass read, as the prose digest and the code digest with a colon between them. `read` names the fragments the "
    "pass fetched, and holds the code digest they had then. An entry of `unsettled` names a fragment the pass gave up "
    "on. The entry says why, and it holds the claims left open. The author edits such a fragment, and the pass then "
    "reads it again. `CLAIM_AGAIN` names a fragment the pass reads again without an edit. "
    "A verified entry with `is_attested` holds a claim a person vouched for."
)

# The key of the store's section holding what a claim pass settled.
_VERIFIED = "verified"

# The key of the store's section holding what a claim pass gave up on.
_UNSETTLED = "unsettled"


@dataclasses.dataclass
class Verified:
    """
    A claim pass settled a single fragment, and this holds the settlement.

    `own` holds the prose digest and the code digest the pass read. A colon joins the pair. `rounds` counts the calls
    the fragment has taken. `read` maps the key of a fragment the pass fetched to the code digest that fragment held at
    the fetch. A key naming a question maps to the digest of that question's answer.

    `is_attested` says a person vouched for a claim no code settles. Such an entry holds no reads. An edit to the
    fragment moves `own`, and the fragment goes back to the pass.
    """

    own: str
    rounds: int
    read: dict[str, str]
    is_attested: bool = False

    def __post_init__(self) -> None:
        """Refuse an attested settlement that holds reads."""
        if self.is_attested and self.read:
            raise ValueError(f"an attested settlement holds reads of {', '.join(sorted(self.read))}")


def digests_of(one: collect_fragments.Fragment) -> str:
    """
    The digests of a fragment, as the field `own` of `Verified` holds them. The prose digest comes first and the code
    digest follows.
    """
    return f"{one.prose.sha}:{collect_fragments.code_digest(one)}"


def read_claims() -> tuple[dict[str, Verified], dict[str, Any]]:
    """
    The settlements the store holds, and the claims the store calls unsettled.

    A tree with no store answers empty. A store missing a section raises. Reading such a store as an empty store would
    throw the record away and verify the tree again. A store whose `unsettled` section is no map raises too.
    """
    if not os.path.exists(_STORE):
        return {}, {}
    with open(_STORE, encoding="utf-8") as handle:
        held = json.load(handle)
    for section in (_VERIFIED, _UNSETTLED):
        if section not in held:
            raise ValueError(f"{_STORE} holds no `{section}` section")
    if not isinstance(held[_UNSETTLED], dict):
        raise ValueError(f"{_STORE} writes `{_UNSETTLED}` as a list. the section maps a key to the claims it left open")
    settled = {key: Verified(**one) for key, one in held[_VERIFIED].items()}
    return settled, dict(held[_UNSETTLED])


def _stored(one: Verified) -> dict[str, Any]:
    """`one` as the store writes it. An entry no person attested leaves `is_attested` out."""
    said = dataclasses.asdict(one)
    if not one.is_attested:
        del said["is_attested"]
    return said


def claims_written(held: dict[str, Verified], unsettled: Mapping[str, Any], keys: Iterable[str]) -> None:
    """
    Write `held` and `unsettled` to the store. The head of the file holds `_NOTE`, and an edit to the note in the file
    does not survive the write.

    `keys` names the fragments whose entries the store keeps. An entry under another key leaves the store.
    """
    live = set(keys)
    said = {
        "_": _NOTE,
        _VERIFIED: {key: _stored(one) for key, one in sorted(held.items()) if key in live},
        _UNSETTLED: {key: unsettled[key] for key in sorted(unsettled) if key in live},
    }
    settling_state.replaced(_STORE, json.dumps(said, indent=2) + "\n")


def settlement_written(key: str, settled: Verified) -> None:
    """
    Put `settled` into the store under `key`. An unsettled entry under `key` leaves the store. The other entries stay.

    Public. `converge_prose` settles a critic's draft this way.
    """
    held, unsettled = read_claims()
    held[key] = settled
    unsettled.pop(key, None)
    claims_written(held, unsettled, [*held, *unsettled])


def answer_sha(
    held: list[collect_fragments.Fragment], asked: str, by_name: Mapping[str, set[str]] | None = None
) -> str:
    """
    The digest of the answer `collect_fragments.answered_by` gives the question `asked`. `by_name` goes to
    `collect_fragments.answered_by`.

    Public. `converge_claims` writes this digest where a reader asked a question. The digest changes where a fragment
    arrives in the answer, leaves it or moves. The fragment that asked then falls stale.
    """
    by_key = {one.key: one for one in held}
    said = "\n".join(
        f"{key}:{collect_fragments.code_digest(by_key[key])}"
        for key in collect_fragments.answered_by(held, asked, by_name)
    )
    return hashlib.sha256(said.encode()).hexdigest()[:16]


# The files the claim pass reads no prose of. `CHANGELOG.md` says what a change did, and the tree has moved since.
# `CONTRIBUTING.md`, `README.md` and `SECURITY.md` hold rules for a contributor and statements about things outside the
# tree. `PLAN.md` holds the work the project owes. `.claude/agents/` holds prompts. `.claude/conventions.md` holds
# rules. `.claude/proposals-pending.md` and `.claude/rejected.md` hold proposals. `Skills.md` holds practices. A path
# ending in `/` names the files under it. `_is_unclaimed` reads such a path that way.
_UNCLAIMED = (
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "PLAN.md",
    "README.md",
    "SECURITY.md",
    "Skills.md",
    ".claude/agents/",
    ".claude/conventions.md",
    ".claude/proposals-pending.md",
    ".claude/rejected.md",
)


def _is_unclaimed(path: str) -> bool:
    """Whether `_UNCLAIMED` names `path`, or a directory above it."""
    return any(path == one or (one.endswith("/") and path.startswith(one)) for one in _UNCLAIMED)


def stale_claims(fragments: Iterable[collect_fragments.Fragment]) -> dict[str, str]:
    """
    `{a fragment key: why the claim pass must read it again}`, over `fragments`.

    Public. `converge_claims` replays an answer of the journal only for a key this names.

    A fragment the store lacks reads again. `claims_purged` drops the entry of a fragment this reports, and a fragment
    no pass has read has none. A fragment whose own digests moved reads again. A fragment that read another fragment
    reads again where that other fragment moved or left the tree.

    A fragment the store calls unsettled stays out while its own digests hold. A person owes such a fragment a reading.
    `_NOTE` tells that person what to do. An edit to such a fragment moves those digests, and the fragment goes back to
    the pass. The claims the store holds for that fragment name the text of the reading before that edit.

    A read edge reaches a fragment of `collect_fragments.code_only` as well. Such a fragment holds no prose, and the
    claim pass checks no claim of that fragment. The code digest of that fragment still restales a fragment that read
    it.

    A fragment of a file `_is_unclaimed` names stays out.
    """
    held = list(fragments)
    settled, unsettled = read_claims()
    by_key = {one.key: one for one in held + collect_fragments.code_only()}
    around = list(by_key.values())
    by_name = collect_fragments.keys_by_name(around)
    answers: dict[str, str] = {}
    aside = set(unsettled)
    found = {}
    for one in held:
        if _is_unclaimed(one.path):
            continue
        if one.key in aside:
            if unsettled[one.key].get("own") == digests_of(one):
                continue
            found[one.key] = "its own prose or code moved past an unsettled entry"
            continue
        was = settled.get(one.key)
        if was is None:
            found[one.key] = "the store holds no reading of it"
            continue
        if was.own != digests_of(one):
            found[one.key] = "its own prose or code moved"
            continue
        for key, sha in sorted(was.read.items()):
            if collect_fragments.is_a_question(key):
                if key not in answers:
                    answers[key] = answer_sha(around, key, by_name)
                if answers[key] != sha:
                    found[one.key] = f"it asked for {collect_fragments.asked_in(key)}, and that answer moved"
                    break
                continue
            read = by_key.get(key)
            if read is None:
                found[one.key] = f"it read `{key}`, and the tree dropped that fragment"
                break
            if collect_fragments.code_digest(read) != sha:
                found[one.key] = f"it read `{key}`, and the code of that fragment moved"
                break
    return found


def unpointed_reads(
    one: collect_fragments.Fragment,
    read: Iterable[str],
    held: list[collect_fragments.Fragment],
    by_name: Mapping[str, set[str]],
) -> list[str]:
    """
    The sorted keys among `read` that `one` does not point at. `held` holds the fragments of the tree. `by_name` comes
    from `collect_fragments.keys_by_name` over them.

    Public. `converge_claims` sends a fragment to the unsettled map where this answers a key. The prose then rests on
    code it does not cite.

    A fragment points at its own key and at the keys of the prose citations. A fragment points at a name the code
    writes. A fragment points at the file fragments of its own file and of the files holding the sites. A fragment
    points at the fragments answering a question `read` holds. A fragment points at the callers of the fragment and at
    the fragments mentioning the fragment. A walk of the tree finds the callers and the mentions. That walk runs only
    where `one` is no file fragment and a key stays unpointed after the earlier tests.

    A fragment points at the code a pointed C or Python fragment names through fragments that are not public. A public
    fragment reached past such a name wants a citation. `collect_fragments.is_public` decides public.

    A fragment pointing at a markdown document points at its paragraphs.
    """
    reads = set(read)
    pointed = {one.key, *one.references}
    pointed |= {
        key for name in set(collect_fragments.AN_IDENTIFIER.findall(one.code.content)) for key in by_name.get(name, ())
    }
    paths = {one.path} | {site.path for site in one.sites}
    pointed |= {other.key for other in held if other.path in paths and collect_fragments.is_a_file(other.key)}
    for asked in sorted(reads):
        if collect_fragments.is_a_question(asked):
            pointed.add(asked)
            pointed |= set(collect_fragments.answered_by(held, asked, by_name))
    left = reads - pointed
    if left and not collect_fragments.is_a_file(one.key):
        around = set(collect_fragments.callers_of(held, one.key, by_name))
        around |= set(collect_fragments.mentions_of(held, one.key, by_name))
        pointed |= around
        left -= around
    if left:
        left -= _reached_privately(pointed, held, by_name)
    if left:
        documents = {
            other.path for other in held if other.key in pointed and collect_fragments.is_a_document(other.key)
        }
        left = {key for key in left if not (collect_fragments.is_markdown(key) and _path_of(held, key) in documents)}
    return sorted(left)


def _citations(runs: list[dict[str, Any]], held: list[collect_fragments.Fragment]) -> list[tuple[str, dict[str, Any]]]:
    """
    The citations of `runs` under `shown_by` or `denied_by`, as `(the key, the citation)`. A citation writes a key, or a
    name that reaches a single fragment of `held`. Any other citation stays out.
    """
    reach = collect_fragments.reach_by_name(held)
    found = []
    for run in runs:
        for side in ("shown_by", "denied_by"):
            for citation in run.get(side) or []:
                name = str(citation.get("name") or "").strip() if isinstance(citation, dict) else ""
                reached = reach.get(name, set())
                if len(reached) == 1:
                    found.append((next(iter(reached)), citation))
    return found


def refined(read: Iterable[str], runs: list[dict[str, Any]], held: list[collect_fragments.Fragment]) -> set[str]:
    """
    The reads of `read`, with a read of a file narrowed to the declarations the citations of `runs` reach.
    `collect_fragments.owners_of` names the declarations behind the lines of a citation. A read of a file stays whole
    where a citation of it names no lines. The read also stays whole where a cited line that is not blank has no owner.

    Public. `converge_claims.settlement_of` stores these reads after `unpointed_reads` passes the reads that `relied_on`
    keeps.
    """
    by_key = {one.key: one for one in held}
    ranges: dict[str, list[tuple[int, int]]] = {}
    whole = set()
    for key, citation in _citations(runs, held):
        if not collect_fragments.is_a_file(key):
            continue
        try:
            ranges.setdefault(key, []).append((int(citation["from"]), int(citation["to"])))
        except (KeyError, TypeError, ValueError):  # not-a-failure: a citation naming no lines keeps the whole file
            whole.add(key)
    narrowed = set()
    for key in read:
        owners: set[str] = set()
        for first, last in ranges.get(key, []) if key in by_key and key not in whole else []:
            got = collect_fragments.owners_of(held, by_key[key], first, last)
            if not got:
                owners = set()
                break
            owners |= got
        narrowed |= owners or {key}
    return narrowed


def relied_on(read: Iterable[str], runs: list[dict[str, Any]], held: list[collect_fragments.Fragment]) -> set[str]:
    """
    The reads among `read` an answer relies on. `runs` holds the runs of that answer, and `held` holds the fragments of
    the tree.

    Public. `prose_answer` asks `unpointed_reads` about these reads. `refined` narrows a read of a file to the
    declarations the runs cite. `converge_claims` then stores these reads and drops any other read.

    The answer relies on a read a run cites under `shown_by` or `denied_by`. A citation writes a key, or a name that
    reaches a single fragment through `collect_fragments.reach_by_name`. A question stays a read. The digest
    `answer_sha` writes for a question changes when a fragment that question gave back moves.
    """
    cited = {key for key, _citation in _citations(runs, held)}
    return {one for one in read if collect_fragments.is_a_question(one) or one in cited}


def _path_of(held: list[collect_fragments.Fragment], key: str) -> str:
    """The file of the fragment `key` names. The lookup raises where `held` lacks `key`."""
    for other in held:
        if other.key == key:
            return other.path
    raise KeyError(f"the tree holds no fragment under {key}")


# The languages whose names `_reached_privately` follows.
_NAMING = ("C", "Python")


def _reached_privately(
    starts: set[str], held: list[collect_fragments.Fragment], by_name: Mapping[str, set[str]]
) -> set[str]:
    """
    The keys the names of the `_NAMING` fragments among `starts` reach without entering a public fragment. The walk
    stops at a public fragment and leaves that fragment out. `collect_fragments.named_by` gives the names.
    """
    by_key = {other.key: other for other in held}
    reached: set[str] = set()
    waiting = [
        key
        for start in sorted(starts)
        if start in by_key and start.partition(":")[0] in _NAMING
        for key in collect_fragments.named_by(by_key[start], by_name)
        if key in by_key
    ]
    while waiting:
        key = waiting.pop()
        if key in reached or collect_fragments.is_public(by_key[key]):
            continue
        reached.add(key)
        waiting += [named for named in collect_fragments.named_by(by_key[key], by_name) if named in by_key]
    return reached


def unpointed_settlements(fragments: Iterable[collect_fragments.Fragment]) -> dict[str, list[str]]:
    """
    `{a fragment key: the reads it does not point at}`, over the settlements the store holds whose `own` matches the
    digests of a fragment the tree holds. `unpointed_reads` decides a read. `Verified` refuses an attested settlement
    holding reads.

    Public. `converge_claims` moves such a settlement to the unsettled map as a run starts.

    A read of a declaration counts as pointed where a read of its file would. `refined` narrows a read of a file to such
    reads, and `converge_claims` checked the read of the file before the narrowing.
    """
    held = list(fragments)
    every = held + collect_fragments.code_only()
    by_name = collect_fragments.keys_by_name(every)
    by_key = {one.key: one for one in held}
    every_key = {one.key: one for one in every}
    file_of = {one.path: one.key for one in every if collect_fragments.is_a_file(one.key)}
    settled, _unsettled = read_claims()
    found = {}
    for key, was in sorted(settled.items()):
        one = by_key.get(key)
        if one is None or was.own != digests_of(one):
            continue
        left = unpointed_reads(one, was.read, every, by_name)
        files = {
            read: file_of[every_key[read].path]
            for read in left
            if read in every_key and every_key[read].path in file_of and not collect_fragments.is_a_file(read)
        }
        if files:
            unpointed_files = set(unpointed_reads(one, set(files.values()), every, by_name))
            left = [read for read in left if read not in files or files[read] in unpointed_files]
        if left:
            found[key] = left
    return found


def claims_purged(fragments: Iterable[collect_fragments.Fragment]) -> dict[str, str]:
    """
    Drop from the store the entries `stale_claims` reports, and answer with them.

    Public. `update_ledger_and_queue.updated` calls this on the fragments `collect_fragments` collected.
    `converge_claims._re_read` calls this. `converge_claims._converge` calls `_re_read` as the run starts, after an
    answer that wrote, and as the run ends. The store then holds the entries the tree bears out.
    """
    held = list(fragments)
    found = stale_claims(held)
    settled, unsettled = read_claims()
    kept = {key: one for key, one in settled.items() if key not in found}
    open_claims = {key: one for key, one in unsettled.items() if key not in found}
    claims_written(kept, open_claims, [one.key for one in held if not _is_unclaimed(one.path)])
    return found


def main() -> None:
    """
    Report the fragments a claim pass owes a reading, the fragments that sit unsettled, and the fragments settled on a
    read they do not point at. The `Makefile` runs this.
    """
    held = collect_fragments.fragments()
    _settled, unsettled = read_claims()
    faults = [f"{key} wants a reading: {why}" for key, why in sorted(stale_claims(held).items())]
    faults += [f"{key} sits unsettled: {entry['why']}" for key, entry in sorted(unsettled.items())]
    faults += [
        f"{key} settled on a read it does not point at: {', '.join(left)}"
        for key, left in sorted(unpointed_settlements(held).items())
    ]
    gate.report(
        faults,
        "claim fault(s). `make examine-claims` reads a fragment that wants a reading. A fragment that sits unsettled "
        "wants an edit, or a name in the file `CLAIM_AGAIN` gives",
        "claims: a claim pass has read the prose of the tree against the code",
    )


if __name__ == "__main__":
    main()
