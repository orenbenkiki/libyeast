# SPDX-License-Identifier: MIT
"""Check that the conformance fixtures exercise every production of the grammar.

Coverage is dynamic, not by name: a production counts as exercised when running a fixture actually matches its body or
evaluates it as a value — so a production with no fixture of its own (a `seq-spaces`, an `in-flow`) is covered by the
fixtures that reach it, and one that nothing reaches is a gap the suite must fill. Only the fixtures the interpreter can
reproduce are run, so what the gate proves is coverage by the validated suite.

`exercised` takes the grammar as an argument, the way the interpreter does: this gate runs on the base grammar now, and
re-runs on each structurally-transformed grammar later — every transformation reshapes the productions, and the fixtures
must still exercise all of them.
"""

import annotated2ir
import gate
import interpreter
import ir
import spec_tests


def exercised(grammar):
    """The set of `grammar`'s production names that running the reproducible fixtures matches or evaluates."""
    reached = set()
    base_match, base_evaluate = interpreter.match, interpreter.evaluate

    def match(node, emitter, grammar_arg, continuation):
        if isinstance(node, ir.Ref) and node.name in grammar_arg:
            name = node.name

            def record():
                reached.add(name)  # the production's body offered a solution — it matched
                return continuation()

            return base_match(node, emitter, grammar_arg, record)
        return base_match(node, emitter, grammar_arg, continuation)

    def evaluate(expression, emitter, grammar_arg):
        if isinstance(expression, ir.Ref) and expression.name in grammar_arg:
            reached.add(expression.name)  # a value production evaluated
        return base_evaluate(expression, emitter, grammar_arg)

    interpreter.match, interpreter.evaluate = match, evaluate
    try:
        for fixture in spec_tests.load():
            if not interpreter.coverable(fixture.production, grammar):
                continue
            # a crashing fixture simply leaves its productions unexercised, for the gate to report
            try:
                arguments = spec_tests.arguments(fixture, grammar)
                if interpreter.run(grammar, fixture.production, fixture.input, arguments) is not None:
                    reached.add(fixture.production)  # matched as the top production
            except Exception:  # noqa: BLE001
                pass
    finally:
        interpreter.match, interpreter.evaluate = base_match, base_evaluate
    return reached


def main():
    grammar = annotated2ir.load()
    reached = exercised(grammar)
    errors = [f"{name}: no reproducible fixture exercises it" for name in grammar if name not in reached]
    gate.report(errors, "unexercised production(s)", f"grammar coverage: {len(grammar)} productions, all exercised")


if __name__ == "__main__":
    main()
