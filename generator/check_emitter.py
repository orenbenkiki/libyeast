# SPDX-License-Identifier: MIT
"""
Check that the interpreter's emitter can be undone.

The backtracking rests on a promise `Emitter` makes in its own docstring. A checkpoint captures the state whole. An
alternative that fails can be undone to the point before it. An `(any)`, a `(---)` and a repetition take that on trust.
This gate is the first check to hold the emitter to that promise.

The fixtures cannot check it. A leak shows where a rule sets a parameter in a branch of an alternation and reads it in a
later branch. The grammar's sites of that shape are `c-chomping-indicator` twice and `c-indentation-indicator`. A site
sets the same parameter in its branches, and the branch that matches overwrites whatever leaked. That is why an aliased
`env` reproduced the fixtures byte for byte while quietly breaking the promise.

So this checks the promise rather than an incident. The promise breaks in a pair of ways. A field that no checkpoint
captures, and a field a checkpoint hands out rather than copies.

The first is why this file names the field list. A field added to `Emitter` and forgotten is a field nothing rewinds. It
fails this gate instead of passing quietly.

This checks the provisional run too. An `Emitter` holds that run, and the fixtures do not reach it. A retype rewrites
the held tokens on the side of the mark its region names, and rewrites by the kind of a token. An injection puts a
marker where the injection says, ahead of the run or between its sides.
"""

import gate
import interpreter
import wire

# The fields an `Emitter` holds, and whether a checkpoint must restore them. A pair of them are not the parse's state.
# The run reads the input and writes it at no point. The run writes the record of the productions it reached, and reads
# that record back at no point.
#
# A rewind leaves that record untouched. A production a fixture reached stays reached whatever the parse did after.
#
# Naming them is the point. A new field lands in a list here, and the gate says so rather than assuming.
_RESTORED = (  # in alphabetical order.
    "ceiling",
    "ceiling_message",
    "code",
    "consumed_length",
    "did_fill_span",
    "env",
    "forbidden",
    "is_sol",
    "mark",
    "open_token",
    "pending",
    "position",
    "probing",
    "provisional",
    "provisional_mark",
    "shadow",
    "stack",
    "tokens",
    "trail",
)
_READ_ONLY = (  # in alphabetical order.
    "byte_at",
    "chars",
    "checking",
    "coverage",
    "globals",
    "holds_indent",
    "passing_arguments",
    "raw",
)
# Its own pushes and pops balance this. A checkpoint does not. The production stack the depth guard traces is the live
# chain of entered productions, pushed on entry and popped on exit even as an exception unwinds. A rewind happens inside
# a production, with its entry still on the stack. The rewind must leave the stack untouched rather than cut it back.
# The return points of those same productions go on and come back with them, a return point per production.
_TRANSIENT = ("entered", "failing", "returns", "unwinding")


# The input `_dirty` needs to read. A caller handing it anything shorter than this runs off the end.
_ENOUGH_TO_DIRTY = b"abcd"


def _dirty(emitter: interpreter.Emitter) -> None:
    """Mutate the restorable fields of `emitter`. Matching a character under an annotation would do the same."""
    emitter.code = "text"
    emitter.try_consume()
    # What a consume leaves behind for a later question, which no method here writes. A field the dirtying leaves alone
    # is one the rewind check cannot see.
    emitter.consumed_length = 3
    emitter.did_fill_span = True
    emitter.env["n"] = 99
    emitter.shadow["m"] = (99,)
    emitter.stack += (("code", "text", frozenset()),)
    emitter.marker("begin-scalar")
    emitter.forbidden += (None,)
    emitter.ceiling = 5
    emitter.ceiling_message = "IMPLICIT_KEY_TOO_LONG"
    emitter.probing += 1
    emitter.open_provisional()
    emitter.try_consume()
    emitter.mark_provisional()
    emitter.try_consume()
    emitter.retype_provisional("meta", None, "before_mark")
    emitter.inject_before(("end-scalar",), "mark")
    emitter.commit_provisional()
    # Left open at the end, the pair above having put these back where it found them. A field the dirtying returns to
    # its initial value is one the rewind below cannot tell a restore from.
    emitter.open_provisional()
    emitter.mark_provisional()
    emitter.try_consume()


def _state(emitter: interpreter.Emitter) -> dict[str, object]:
    """
    The state a checkpoint is supposed to restore, keyed by the field it comes from. A value here compares by equality.

    Keyed rather than listed. `RESTORED` and this cannot then drift apart. A field named there and not read here would
    be a field the rewind below silently does not look at. That is the silence this gate exists to refuse. It shows up a
    level up.
    """
    return {
        "position": emitter.position,
        "mark": emitter.mark,
        "tokens": list(emitter.tokens),
        "open_token": emitter.open_token,
        "provisional": emitter.provisional,
        "provisional_mark": emitter.provisional_mark,
        "trail": list(emitter.trail),
        "code": emitter.code,
        "env": dict(emitter.env),
        "shadow": dict(emitter.shadow),
        "stack": emitter.stack,
        "is_sol": emitter.is_sol,
        "forbidden": emitter.forbidden,
        "pending": emitter.pending,
        "ceiling": emitter.ceiling,
        "ceiling_message": emitter.ceiling_message,
        "probing": emitter.probing,
        "did_fill_span": emitter.did_fill_span,
        "consumed_length": emitter.consumed_length,
    }


def _fields_are_accounted(errors: list[str]) -> None:
    """A field of an `Emitter` is either restored by a checkpoint or declared read-only."""
    held = set(vars(interpreter.Emitter(b"x")))
    for name in sorted(held - set(_RESTORED) - set(_READ_ONLY) - set(_TRANSIENT)):
        errors.append(f"Emitter.{name}: nothing says whether a checkpoint restores it")
    for name in sorted((set(_RESTORED) | set(_READ_ONLY) | set(_TRANSIENT)) - held):
        errors.append(f"Emitter.{name}: named here but no such field")
    # And what the rewind check reads is exactly what `RESTORED` names, or a field is declared restored and never looked
    # at.
    read = set(_state(interpreter.Emitter(b"x")))
    for name in sorted(set(_RESTORED) - read):
        errors.append(f"RESTORED names Emitter.{name}, and the rewind check leaves that field unread")
    for name in sorted(read - set(_RESTORED)):
        errors.append(f"Emitter.{name}: the rewind check reads that field and RESTORED does not name it")


def _rewind_restores(errors: list[str]) -> None:
    """
    A checkpoint taken, the state dirtied, then the checkpoint rewound to. The state comes back as the checkpoint took
    it.
    """
    emitter = interpreter.Emitter(_ENOUGH_TO_DIRTY)
    before = _state(emitter)
    checkpoint = emitter.checkpoint()
    _dirty(emitter)
    emitter.rewind(checkpoint)
    if _state(emitter) != before:
        errors.append("rewind: a checkpoint does not restore the state the parse took it from")


def _rewind_is_repeatable(errors: list[str]) -> None:
    """
    A single checkpoint rewound to twice restores the same state twice.

    This is what an alternation does, with a checkpoint rewound to once per branch. A checkpoint that hands out its own
    mutable state rather than a copy lets a branch reach into what the next rewinds to.
    """
    emitter = interpreter.Emitter(_ENOUGH_TO_DIRTY)
    before = _state(emitter)
    checkpoint = emitter.checkpoint()
    for attempt in ("first", "second"):
        _dirty(emitter)
        emitter.rewind(checkpoint)
        if _state(emitter) != before:
            errors.append(
                f"rewind: the {attempt} rewind to a checkpoint does not restore the state the parse took it from"
            )
            return


def _held(emitter: interpreter.Emitter) -> list[tuple[str, str]]:
    """The held provisional run's tokens, as `(code, text)` pairs. The cut of the open token comes first."""
    emitter.cut()
    return [(token.code, token.text) for token in emitter.tokens[emitter.provisional :]]


def _retype_selects_by_region(errors: list[str]) -> None:
    """
    A retype rewrites the held tokens on the side of the mark its `region` names. A pair of breaks sit with a mark
    between them. `before_mark` reaches the first and `after_mark` the second. `all` reaches both. The region is the
    whole discriminator. The tokens are the same kind.
    """
    line_feed = wire.CODE_CHAR["line-feed"]
    for region, wanted in (
        ("before_mark", [line_feed, "b"]),
        ("after_mark", ["b", line_feed]),
        ("all", [line_feed, line_feed]),
    ):
        emitter = interpreter.Emitter(b"\n\n")
        emitter.open_provisional()
        emitter.code = "break"
        emitter.try_consume()
        emitter.mark_provisional()
        emitter.try_consume()
        emitter.retype_provisional(None, "line-feed", region)
        got = [code for code, _text in _held(emitter)]
        if got != wanted:
            errors.append(f"retype region={region}: rewrote {got}, wanted {wanted}")


def _retype_selects_by_kind(errors: list[str]) -> None:
    """A retype over the whole run rewrites a break by `breaks` and anything else by `rest`, as a pair of classes."""
    emitter = interpreter.Emitter(b"\n ")
    emitter.open_provisional()
    emitter.code = "break"
    emitter.try_consume()
    emitter.cut()  # close the break token, as a `(token)` boundary would. The space is then its own
    emitter.code = "white"
    emitter.try_consume()
    emitter.retype_provisional("indent", "line-feed", "all")
    got = [code for code, _text in _held(emitter)]
    wanted = [wire.CODE_CHAR["line-feed"], wire.CODE_CHAR["indent"]]
    if got != wanted:
        errors.append(f"retype by kind: rewrote {got}, wanted {wanted}")


def _inject_inserts_in_order(errors: list[str]) -> None:
    """An injection at the run's start puts its markers ahead of the held run, and keeps the order given."""
    emitter = interpreter.Emitter(b"a")
    emitter.open_provisional()
    emitter.code = "text"
    emitter.try_consume()
    emitter.inject_before(("begin-document", "begin-node"), "start")
    emitter.cut()
    got = [token.code for token in emitter.tokens]
    wanted = [wire.CODE_CHAR["begin-document"], wire.CODE_CHAR["begin-node"], wire.CODE_CHAR["text"]]
    if got != wanted:
        errors.append(f"inject at start: emitted {got}, wanted {wanted}")
    if [code for code, _text in _held(emitter)] != [wire.CODE_CHAR["text"]]:
        errors.append("inject at start: the injected markers stayed in the held run")


def _inject_at_mark(errors: list[str]) -> None:
    """
    An injection at the mark sits between the sides of the run, behind the pre-mark tokens and ahead of what follows.
    """
    emitter = interpreter.Emitter(b"\na")
    emitter.open_provisional()
    emitter.code = "break"
    emitter.try_consume()
    emitter.mark_provisional()
    emitter.code = "text"
    emitter.try_consume()
    emitter.inject_before(("begin-pair",), "mark")
    emitter.cut()
    got = [token.code for token in emitter.tokens]
    wanted = ["b", wire.CODE_CHAR["begin-pair"], wire.CODE_CHAR["text"]]
    if got != wanted:
        errors.append(f"inject at mark: emitted {got}, wanted {wanted}")


def main() -> None:
    errors: list[str] = []
    _fields_are_accounted(errors)
    _rewind_restores(errors)
    _rewind_is_repeatable(errors)
    _retype_selects_by_region(errors)
    _retype_selects_by_kind(errors)
    _inject_inserts_in_order(errors)
    _inject_at_mark(errors)
    gate.report(errors, "broken promise(s) of the emitter", f"emitter: {len(_RESTORED)} fields checkpointed and undone")


if __name__ == "__main__":
    main()
