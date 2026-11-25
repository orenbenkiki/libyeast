# SPDX-License-Identifier: MIT
"""
Check that the interpreter's emitter can be undone.

The backtracking rests on a promise `Emitter` makes in its own docstring. A checkpoint captures the state whole. The
interpreter can rewind a failed alternative to the checkpoint before it. The IR holds an `(any)` as an `AltTree`. An
`(any)`, a `(---)` and a repetition trust that rewind. `interpreter._try_alternation`, `interpreter._try_difference` and
`interpreter._try_repeat` go back to a checkpoint.

So this checks the promise rather than an incident. The promise breaks in a pair of ways. A checkpoint may miss a field.
A checkpoint may also hand a field out rather than copy it.

This file sorts the fields of `Emitter` into `_RESTORED`, `_READ_ONLY` and `_TRANSIENT`. This gate fails on a field of
`Emitter` that none of these lists names.

This checks the provisional run too. An `Emitter` keeps where that run begins in `provisional`.
`check_dead_code._KEPT_THOUGH_DEAD` keeps the provisional action kinds alive, and no step builds them. A retype rewrites
held tokens by kind. A retype's region names the side of the mark that the retype rewrites, or `all` for the whole run.
An injection puts a marker where the injection says, ahead of the run or between its sides.
"""

import gate
import interpreter
import wire

# The fields of an `Emitter` that a checkpoint must restore. The input and the record of the productions the run reached
# are not among them. The run reads the input and writes it at no point. The run writes the record of the productions it
# reached, and reads that record back at no point.
#
# A rewind leaves that record untouched. A production a fixture reached stays reached under any later parse. The
# interpreter only adds names to `Coverage.reached` and `Coverage.rejected`. `interpreter._evaluated_call`,
# `interpreter._try_call` and `interpreter._try_way` make those adds.
_RESTORED = (  # The tuple names the fields in alphabetical order.
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
# `interpreter._try_call` pushes and pops `entered` and `returns` in balance. `failing` and `unwinding` say where a
# failure is going. A checkpoint of an `Emitter` captures neither field. The depth guard traces the production stack.
# That stack holds the live chain of entered productions. A production goes on at entry and comes off at exit, even as
# an exception unwinds. A rewind happens inside a production, and that production's entry stays on the stack. A rewind
# of an `Emitter` leaves `entered` and `returns` untouched rather than cut them back. A production holds a single return
# point. That return point goes on and comes off with the production's entry.
_TRANSIENT = ("entered", "failing", "returns", "unwinding")


# The input `_dirty` needs to read. A caller handing `_dirty` a shorter input has `try_consume` run off the end.
_ENOUGH_TO_DIRTY = b"abcd"


def _dirty(emitter: interpreter.Emitter) -> None:
    """
    Mutate the restorable fields of `emitter`. The first pair of lines match a character under an annotation, the way
    `interpreter._try_token` sets the code and `Emitter.try_consume` takes the character.
    """
    emitter.code = "text"
    emitter.try_consume()
    # Set the restorable fields away from their initial values.
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
    # The provisional pair stays open at the end. A field back at its initial value would hide a missed restore.
    emitter.open_provisional()
    emitter.mark_provisional()
    emitter.try_consume()


def _state(emitter: interpreter.Emitter) -> dict[str, object]:
    """
    The state a checkpoint is supposed to restore, keyed by the field it comes from. A value here compares by equality.

    The rewind below compares the fields this function reads. A gate a level up refuses a field that `RESTORED` names
    and this function does not read.
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
    """A field of an `Emitter` is restored by a checkpoint, declared read-only, or declared transient."""
    held = set(vars(interpreter.Emitter(b"x")))
    for name in sorted(held - set(_RESTORED) - set(_READ_ONLY) - set(_TRANSIENT)):
        errors.append(f"`Emitter.{name}`: nothing says whether a checkpoint restores it")
    for name in sorted((set(_RESTORED) | set(_READ_ONLY) | set(_TRANSIENT)) - held):
        errors.append(f"`Emitter.{name}`: named here but no such field")
    # And the rewind check reads exactly the fields `RESTORED` names.
    read = set(_state(interpreter.Emitter(b"x")))
    for name in sorted(set(_RESTORED) - read):
        errors.append(f"`RESTORED` names `Emitter.{name}`, and the rewind check leaves that field unread")
    for name in sorted(read - set(_RESTORED)):
        errors.append(f"`Emitter.{name}`: the rewind check reads that field and `RESTORED` does not name it")


def _rewind_restores(errors: list[str]) -> None:
    """
    Take a checkpoint, dirty the state, and rewind to the checkpoint. The state comes back as the checkpoint took it.
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

    An alternation rewinds to its checkpoint after a branch fails, and the next branch starts from there. A rewind
    handing out the checkpoint's own mutable state rather than a copy would let a branch reach into the state the next
    branch rewinds to. `Emitter._restore_scopes` hands out a copy of `env` and of `shadow`.
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
    """A retype over the whole run rewrites a break by `breaks` and anything else by `rest`."""
    emitter = interpreter.Emitter(b"\n ")
    emitter.open_provisional()
    emitter.code = "break"
    emitter.try_consume()
    emitter.cut()  # The cut closes the break token, as a `(token)` boundary would.
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
    An injection at the mark sits between the sides of the run. It comes behind the pre-mark tokens and ahead of the
    post-mark tokens.
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
