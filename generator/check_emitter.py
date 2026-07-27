# SPDX-License-Identifier: MIT
"""
Check that the interpreter's emitter can be undone.

The whole of the backtracking rests on one promise, which `Emitter` makes in its own docstring: a checkpoint captures
the whole of the state, so an alternative that fails can be undone to the point before it. Every `(any)`, every `(---)`
and every repetition takes it on trust. Nothing checked it.

The fixtures cannot: a leak only shows where a rule sets a parameter in one branch of an alternation and reads it in a
later one, and the grammar's only three such sites — `c-chomping-indicator` twice and `c-indentation-indicator` — set
the same parameter in every branch, so whatever leaked is overwritten by the branch that matches. That is why an aliased
`env` reproduced all 654 fixtures byte for byte while quietly breaking the promise.

So this checks the promise rather than an incident, in the two ways it can be broken: a field that no checkpoint
captures, and a field a checkpoint hands out rather than copies. The first is why the field list is named here — a field
added to `Emitter` and forgotten is a field nothing rewinds, and it fails this instead of passing everything.
"""

import gate
import interpreter
import wire

# What an `Emitter` holds, and whether a checkpoint must restore it. The input is the only thing that is not state: it
# is read and never written. Naming them is the point — a new field must be sorted into one list or the other, and the
# gate says so rather than assuming.
RESTORED = (  # in alphabetical order
    "ceiling",
    "ceiling_message",
    "code",
    "env",
    "forbidden",
    "is_sol",
    "mark",
    "pending",
    "position",
    "probing",
    "provisional",
    "provisional_mark",
    "run",
    "stack",
    "tokens",
    "trail",
    "window_depth",
)
READ_ONLY = ("byte_at", "chars", "deterministic", "holds_indent", "passing_arguments", "raw")  # in alphabetical order
# Balanced by its own pushes and pops rather than by a checkpoint: the production stack the depth guard traces is the
# live chain of entered productions, pushed on entry and popped on exit even as an exception unwinds, so a rewind —
# which happens inside a production, its entry still standing — must leave it alone, not truncate it. The committed
# regions likewise: push and pop restore their records on their own failure paths, a region once reached stays reached
# whatever backtracking does after, and recovery truncates what an abandoned parse left open.
TRANSIENT = ("commitments", "entered")


def _dirty(emitter):
    """Mutate every restorable field of `emitter`, the way matching a character under an annotation would."""
    emitter.code = "text"
    emitter.consume()
    emitter.env["n"] = 99
    emitter.stack += ("text",)
    emitter.marker("begin-scalar")
    emitter.forbidden += (None,)
    emitter.ceiling = 5
    emitter.ceiling_message = "IMPLICIT_KEY_TOO_LONG"
    emitter.window_depth += 1
    emitter.probing += 1
    emitter.open_provisional()
    emitter.consume()
    emitter.mark_provisional()
    emitter.consume()
    emitter.retype_provisional("meta", None, "before_mark")
    emitter.inject_before(("end-scalar",), "mark")
    emitter.commit_provisional()


def _state(emitter):
    """Everything a checkpoint is supposed to restore, as values that compare by equality."""
    return (
        emitter.position,
        emitter.mark,
        list(emitter.tokens),
        emitter.run,
        emitter.provisional,
        emitter.provisional_mark,
        list(emitter.trail),
        emitter.code,
        dict(emitter.env),
        emitter.stack,
        emitter.is_sol,
        emitter.forbidden,
        emitter.pending,
        emitter.ceiling,
        emitter.ceiling_message,
        emitter.window_depth,
        emitter.probing,
    )


def _fields_are_accounted(errors):
    """Every field of an `Emitter` is either restored by a checkpoint or declared read-only."""
    held = set(vars(interpreter.Emitter(b"x")))
    for name in sorted(held - set(RESTORED) - set(READ_ONLY) - set(TRANSIENT)):
        errors.append(f"Emitter.{name}: nothing says whether a checkpoint restores it")
    for name in sorted((set(RESTORED) | set(READ_ONLY) | set(TRANSIENT)) - held):
        errors.append(f"Emitter.{name}: named here but no such field")


def _rewind_restores(errors):
    """A checkpoint taken, the state dirtied, and the checkpoint rewound to, leaves the state as it was."""
    emitter = interpreter.Emitter(b"abc")
    before = _state(emitter)
    checkpoint = emitter.checkpoint()
    _dirty(emitter)
    emitter.rewind(checkpoint)
    if _state(emitter) != before:
        errors.append("rewind: a checkpoint does not restore the state it was taken from")


def _rewind_is_repeatable(errors):
    """
    The same checkpoint rewound to twice restores the same state twice.

    This is what an alternation does — one checkpoint, rewound to once per branch — so a checkpoint that hands out its
    own mutable state rather than a copy of it lets one branch reach into what the next rewinds to.
    """
    emitter = interpreter.Emitter(b"abc")
    before = _state(emitter)
    checkpoint = emitter.checkpoint()
    for attempt in ("first", "second"):
        _dirty(emitter)
        emitter.rewind(checkpoint)
        if _state(emitter) != before:
            errors.append(f"rewind: the {attempt} rewind to a checkpoint does not restore the state it was taken from")
            return


def _held(emitter):
    """The held run's tokens, as `(code, text)` pairs, after cutting the open character run."""
    emitter.cut()
    return [(token.code, token.text) for token in emitter.tokens[emitter.provisional :]]


def _retype_selects_by_region(errors):
    """
    A retype rewrites the held tokens on the side of the mark its `region` names. Two breaks are held with a mark
    between them, so `before_mark` reaches the first and `after_mark` the second, `all` both — the region the whole
    discriminator, the two tokens being the same kind.
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
        emitter.consume()
        emitter.mark_provisional()
        emitter.consume()
        emitter.retype_provisional(None, "line-feed", region)
        got = [code for code, _text in _held(emitter)]
        if got != wanted:
            errors.append(f"retype region={region}: rewrote {got}, wanted {wanted}")


def _retype_selects_by_kind(errors):
    """A retype over the whole run rewrites a break by `breaks` and anything else by `rest`, each its own class."""
    emitter = interpreter.Emitter(b"\n ")
    emitter.open_provisional()
    emitter.code = "break"
    emitter.consume()
    emitter.cut()  # close the break token, as a `(token)` boundary would, so the space is its own
    emitter.code = "white"
    emitter.consume()
    emitter.retype_provisional("indent", "line-feed", "all")
    got = [code for code, _text in _held(emitter)]
    wanted = [wire.CODE_CHAR["line-feed"], wire.CODE_CHAR["indent"]]
    if got != wanted:
        errors.append(f"retype by kind: rewrote {got}, wanted {wanted}")


def _inject_inserts_in_order(errors):
    """An injection at the run's start puts its markers ahead of the held run, in the order given."""
    emitter = interpreter.Emitter(b"a")
    emitter.open_provisional()
    emitter.code = "text"
    emitter.consume()
    emitter.inject_before(("begin-document", "begin-node"), "start")
    emitter.cut()
    got = [token.code for token in emitter.tokens]
    wanted = [wire.CODE_CHAR["begin-document"], wire.CODE_CHAR["begin-node"], wire.CODE_CHAR["text"]]
    if got != wanted:
        errors.append(f"inject at start: emitted {got}, wanted {wanted}")
    if [code for code, _text in _held(emitter)] != [wire.CODE_CHAR["text"]]:
        errors.append("inject at start: the injected markers stayed in the held run")


def _inject_at_mark(errors):
    """
    An injection at the mark stands between the two sides of the run, behind the pre-mark tokens and ahead of the rest.
    """
    emitter = interpreter.Emitter(b"\na")
    emitter.open_provisional()
    emitter.code = "break"
    emitter.consume()
    emitter.mark_provisional()
    emitter.code = "text"
    emitter.consume()
    emitter.inject_before(("begin-pair",), "mark")
    emitter.cut()
    got = [token.code for token in emitter.tokens]
    wanted = ["b", wire.CODE_CHAR["begin-pair"], wire.CODE_CHAR["text"]]
    if got != wanted:
        errors.append(f"inject at mark: emitted {got}, wanted {wanted}")


def main():
    errors = []
    _fields_are_accounted(errors)
    _rewind_restores(errors)
    _rewind_is_repeatable(errors)
    _retype_selects_by_region(errors)
    _retype_selects_by_kind(errors)
    _inject_inserts_in_order(errors)
    _inject_at_mark(errors)
    gate.report(errors, "broken promise(s) of the emitter", f"emitter: {len(RESTORED)} fields checkpointed and undone")


if __name__ == "__main__":
    main()
