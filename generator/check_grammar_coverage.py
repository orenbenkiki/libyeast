# SPDX-License-Identifier: MIT
"""Check that the conformance fixtures exercise every production of the grammar, both ways.

Coverage is dynamic, not by name: a production counts when running a fixture actually reaches it — so a production with
no fixture of its own (a `seq-spaces`, an `in-flow`) is covered by the fixtures that reach it, and one that nothing
reaches is a gap the suite must fill. What the gate proves is coverage by the suite the interpreter reproduces, since a
fixture it crashes on leaves its productions unexercised and is reported as the gap it is.

Reaching a production is half of exercising it. A rule is a decision, and a fixture that only ever watches it say yes
leaves the other answer untested — so each must also be seen to **reject** an input, by failing to match or by a `(cut)`
inside it raising. The exception is a rule that *cannot* say no: `l-yaml-stream` matches at every position because every
part of it is optional, and asking for a fixture where it fails would be asking for the impossible. Those are computed,
not listed — `is_total` proves it from the body — and a rule that can never say no is worth knowing about anyway, that
being exactly what let `l-yaml-stream` swallow a whole input before `l-yeast-stream` was written to say so.

A `(cut)` is a decision too, and the same argument applies to it: one that never fires is a commit point nothing has
shown is reachable, and a message nothing has shown is right. That is checked against the fixtures' own expected output
rather than by watching the interpreter, which is stricter — it proves the error survived to be handed back, where a cut
raising inside a lookahead would prove only that it can raise.

`exercised` takes the grammar as an argument, the way the interpreter does: this gate runs on the base grammar now, and
re-runs on each structurally-transformed grammar later — every transformation reshapes the productions, and the fixtures
must still exercise all of them.
"""

import os

import annotated2ir
import check_messages
import gate
import interpreter
import ir
import spec_tests
import wire
import yaml

# The parameters every rule is reached under every value of, and how many values that is. The resume policy is the only
# one: the caller chooses it once and it is threaded down unchanged, where a context is chosen by the rule that descends
# into one and can leave a rule out of reach of a value entirely.
AMBIENT = {"r": len(annotated2ir.RESUMES)}

# The nodes that match wherever they are asked to, and the ones that may always refuse. A `(cut)` counts as matching: it
# never returns "no", it raises instead, which is a different thing that `rejected` accounts for separately. Both lists
# in alphabetical order.
ALWAYS = (ir.Cut, ir.Emit, ir.Empty, ir.Error, ir.ExcludeAt, ir.Flip, ir.Increase, ir.SetVar)
NEVER_SURE = (
    ir.Char,
    ir.Diff,
    ir.EndOfStream,
    ir.Invalid,
    ir.Le,
    ir.Look,
    ir.LookBehind,
    ir.Lt,
    ir.NegLook,
    ir.Range,
    ir.StartOfLine,
)


def is_total(node, grammar, seen=frozenset()):
    """Whether `node` matches at every position and for every parameter value, provably — or not at all.

    Proving it takes only the shape: a repetition or an optional can take nothing, a sequence is total when every item
    is, an alternation when any item is, a `(case)` when every branch it has is total. Anything that reads the input,
    and every zero-width guard, may say no. Recursion assumes total and lets the rest of the body decide, so a rule is
    total only when some path through it does not depend on the recursion.

    A `(case)` on `c` or `t` is read the way `check_markers` reads one: a rule reached only in some contexts lists only
    those, so a branch that is not there is a path that cannot be taken rather than one that says no. Counting a missing
    branch as a refusal would ask for a fixture running `nb-single-text` at `block-in`, which nothing reaches it with.

    `r` is not like them and `AMBIENT` says so. A context is chosen by the rule that descends into one, so a rule can be
    out of reach of a value; the resume policy is chosen once by the caller and threaded to everything unchanged, so
    every rule is reached under every value of it. A `(case)` on `r` with a value missing is therefore a path that is
    taken and says no — which is exactly how `l-recover-entry` declines to answer for a policy that recovers elsewhere.
    """
    if isinstance(node, ALWAYS):
        return True
    if isinstance(node, NEVER_SURE):
        return False
    if isinstance(node, (ir.Star, ir.Opt)):
        return True
    if isinstance(node, ir.Max):
        # A wrapping `(max)` says no where its production does; the vendored grammar's bare `(max)` is a length note.
        return is_total(node.item, grammar, seen) if node.item is not None else False
    if isinstance(node, (ir.Plus, ir.Token, ir.Wrap, ir.Bound, ir.Rep)):
        return is_total(node.item, grammar, seen)
    if isinstance(node, ir.Bind):
        return is_total(node.cond, grammar, seen)
    if isinstance(node, ir.Recover):
        # A recovery answers a cut and nothing else, so what says no here is the item saying it.
        return is_total(node.item, grammar, seen)
    if isinstance(node, ir.Seq):
        return all(is_total(item, grammar, seen) for item in node.items)
    if isinstance(node, ir.Alt):
        return any(is_total(item, grammar, seen) for item in node.items)
    if isinstance(node, ir.Case):
        if len(node.branches) < AMBIENT.get(node.var, 0):
            return False  # a value of an ambient parameter with no branch is a path that is taken, and says no
        return all(is_total(branch.item, grammar, seen) for branch in node.branches)
    if isinstance(node, ir.Ref):
        if node.name in seen:
            return True
        return is_total(grammar[node.name].body, grammar, seen | {node.name})
    raise TypeError(f"cannot decide whether {type(node).__name__} is total")


def exercised(grammar):
    """The productions the reproducible fixtures reach, and the ones they see reject an input.

    Returns `(reached, rejected)`. A production is reached when its body offers a solution or a value expression
    evaluates it; it is rejected when it fails to match, or when a `(cut)` inside it raises — a rule holding a cut never
    returns "no", so watching only the return value would call it untested for ever.
    """
    reached, rejected = set(), set()
    base_match, base_evaluate = interpreter.match, interpreter.evaluate

    def match(node, emitter, grammar_arg, continuation):
        if isinstance(node, ir.Ref) and node.name in grammar_arg:
            name = node.name

            def record():
                reached.add(name)  # the production's body offered a solution — it matched
                return continuation()

            try:
                matched = base_match(node, emitter, grammar_arg, record)
            except interpreter.CommitFailure:
                rejected.add(name)
                raise
            if not matched:
                rejected.add(name)
            return matched
        return base_match(node, emitter, grammar_arg, continuation)

    def evaluate(expression, emitter, grammar_arg):
        if isinstance(expression, ir.Ref) and expression.name in grammar_arg:
            reached.add(expression.name)  # a value production evaluated
        return base_evaluate(expression, emitter, grammar_arg)

    interpreter.match, interpreter.evaluate = match, evaluate
    try:
        for fixture in spec_tests.load():
            # a crashing fixture simply leaves its productions unexercised, for the gate to report
            try:
                interpreter.run(grammar, fixture.production, fixture.input, spec_tests.arguments(fixture, grammar))
            except Exception:  # noqa: BLE001
                pass
    finally:
        interpreter.match, interpreter.evaluate = base_match, base_evaluate
    return reached, rejected


def fired():
    """The error texts the fixtures' own expected output carries — the `(cut)`s and `(error)`s shown to reach it.

    These are the texts as the wire holds them, escaped, which is what a message must be escaped to be compared against:
    a message naming a backslash is `\\x5c` here and a backslash in `messages.yaml`.
    """
    texts = set()
    for fixture in spec_tests.load():
        if not os.path.exists(fixture.output_path):
            continue  # unpaired, which the fixture gate reports
        for token in wire.parse(fixture.expected):
            if token.code == wire.ERROR:
                texts.add(token.text)
    return texts


def main():
    grammar = annotated2ir.load()
    reached, rejected = exercised(grammar)
    with open(check_messages.MESSAGES) as handle:
        messages = yaml.safe_load(handle)
    texts = fired()

    errors = [f"{name}: no reproducible fixture exercises it" for name in grammar if name not in reached]
    errors += [
        f"{name}: no fixture makes it reject an input, and it is not total"
        for name in grammar
        if name not in rejected and not is_total(grammar[name].body, grammar, frozenset({name}))
    ]
    errors += [
        f"{code}: no fixture's output carries its message, so nothing shows the cut fires"
        for code, text in sorted(messages.items())
        if wire.escape(text.encode("utf-8")) not in texts
    ]

    gate.report(
        errors,
        "gap(s) in what the fixtures exercise",
        f"grammar coverage: {len(grammar)} productions matched and rejected, {len(messages)} messages fired",
    )


if __name__ == "__main__":
    main()
