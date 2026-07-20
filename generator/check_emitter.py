# SPDX-License-Identifier: MIT
"""Check that the interpreter's emitter can be undone.

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
    "match_start",
    "pending",
    "position",
    "probing",
    "run",
    "tokens",
)
READ_ONLY = ("byte_at", "chars", "raw")  # in alphabetical order
# Balanced by its own pushes and pops rather than by a checkpoint: the production stack the depth guard traces is the
# live chain of entered productions, pushed on entry and popped on exit even as an exception unwinds, so a rewind —
# which happens inside a production, its entry still standing — must leave it alone, not truncate it.
TRANSIENT = ("stack",)


def _dirty(emitter):
    """Mutate every restorable field of `emitter`, the way matching a character under an annotation would."""
    emitter.code = "text"
    emitter.consume()
    emitter.env["n"] = 99
    emitter.marker("begin-scalar")
    emitter.forbidden += (None,)
    emitter.match_start = 1
    emitter.ceiling = 5
    emitter.ceiling_message = "IMPLICIT_KEY_TOO_LONG"
    emitter.probing += 1


def _state(emitter):
    """Everything a checkpoint is supposed to restore, as values that compare by equality."""
    return (
        emitter.position,
        emitter.mark,
        list(emitter.tokens),
        emitter.run,
        emitter.code,
        dict(emitter.env),
        emitter.match_start,
        emitter.is_sol,
        emitter.forbidden,
        emitter.pending,
        emitter.ceiling,
        emitter.ceiling_message,
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
    emitter = interpreter.Emitter(b"ab")
    before = _state(emitter)
    checkpoint = emitter.checkpoint()
    _dirty(emitter)
    emitter.rewind(checkpoint)
    if _state(emitter) != before:
        errors.append("rewind: a checkpoint does not restore the state it was taken from")


def _rewind_is_repeatable(errors):
    """The same checkpoint rewound to twice restores the same state twice.

    This is what an alternation does — one checkpoint, rewound to once per branch — so a checkpoint that hands out its
    own mutable state rather than a copy of it lets one branch reach into what the next rewinds to.
    """
    emitter = interpreter.Emitter(b"ab")
    before = _state(emitter)
    checkpoint = emitter.checkpoint()
    for attempt in ("first", "second"):
        _dirty(emitter)
        emitter.rewind(checkpoint)
        if _state(emitter) != before:
            errors.append(f"rewind: the {attempt} rewind to a checkpoint does not restore the state it was taken from")
            return


def main():
    errors = []
    _fields_are_accounted(errors)
    _rewind_restores(errors)
    _rewind_is_repeatable(errors)
    gate.report(errors, "broken promise(s) of the emitter", f"emitter: {len(RESTORED)} fields checkpointed and undone")


if __name__ == "__main__":
    main()
