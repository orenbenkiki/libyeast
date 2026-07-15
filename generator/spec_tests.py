# SPDX-License-Identifier: MIT
"""libyeast's own conformance fixtures, and the reference fixtures they were migrated from.

`tests/spec/` holds libyeast's differential oracle: for each `<production>[.n=N][.c=C][.t=T].<case>` an `.input` YAML
fragment and an `.output` YEAST token stream the production must emit for it. The filename says which production to run
and with which parameters — this module decodes that convention, mirroring the reference's own test runner, and pairs
each input with its expected output.

The fixtures are migrated once from the vendored reference parser's `tests/` (`SOURCE_DIR`), by `migrate_tests.py`; the
harness reads only `tests/spec/`, so the suite is libyeast's to add to and correct without being tied to the reference's
outputs. The migration needs to know which reference fixtures align with libyeast's grammar (`is_runnable`) and which
productions libyeast flattens to a bare character class and so emits as plain unparsed (`annotation_free`).
"""

import os
import re
from dataclasses import dataclass

import ir

_TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(_TREE, "tests", "spec")
SOURCE_DIR = os.path.join(_TREE, "third_party", "yamlreference", "tests")

# The token-annotation nodes: a production that reaches none of them emits every character it consumes as unparsed.
_ANNOTATIONS = (ir.Token, ir.Wrap, ir.Emit)

# The parameter values the grammar understands: the six contexts of `c` and the three chomping modes of `t`. `n` is an
# indentation, any integer (-1 is the auto-detect base). A fixture whose value falls outside these is malformed, not
# merely foreign.
CONTEXTS = ("block-in", "block-out", "block-key", "flow-in", "flow-out", "flow-key")
CHOMPINGS = ("strip", "clip", "keep")

# A production name is the leading run of a filename, up to its first `.`; a parameter is a `.<name>=<value>` segment.
_PARAMETER = re.compile(r"\.([nct])=([^.]+)")


@dataclass(frozen=True)
class Fixture:
    """One reference test: the production to run, its parameters, and where the input and expected output live."""

    production: str
    parameters: dict  # {"n": "2", "c": "flow-in", ...}, values verbatim from the filename
    case: str  # the arbitrary testcase name, e.g. "a" or "empty.invalid"
    is_invalid: bool  # whether the expected output is a parse error rather than a clean token stream
    input_path: str
    output_path: str

    @property
    def input(self):
        """The YAML fragment fed to the production, as the exact bytes the test is about."""
        with open(self.input_path, "rb") as handle:
            return handle.read()

    @property
    def expected(self):
        """The YEAST token stream the reference parser emits for this fixture, in wire form."""
        with open(self.output_path, "r") as handle:
            return handle.read()


def parse_name(filename):
    """Decode a fixture filename into its production, parameters, case, and whether it is an invalid-input test.

    `filename` is a bare `.input`/`.output` name; its extension is ignored.
    """
    stem = filename.rsplit(".", 1)[0] if filename.endswith((".input", ".output")) else filename
    production = stem.split(".", 1)[0]
    rest = stem[len(production) :]
    parameters = {name: value for name, value in _PARAMETER.findall(rest)}
    case = _PARAMETER.sub("", rest).strip(".")
    return production, parameters, case, "invalid" in case.split(".")


def is_runnable(fixture, grammar):
    """Return None if `grammar` can run `fixture`, else a one-line reason it cannot.

    Runnable means the grammar has the production and declares exactly the parameters the filename supplies — no more,
    no fewer. This is the structural test the interpreter driver filters on; that the supplied values are ones the
    grammar understands is a separate data check the reference-test gate makes.
    """
    production = grammar.get(fixture.production)
    if production is None:
        return "not a production of the official grammar"
    if set(fixture.parameters) != set(production.params):
        wanted = ", ".join(production.params) or "none"
        given = ", ".join(fixture.parameters) or "none"
        return f"parameters {{{given}}} do not match the grammar's {{{wanted}}}"
    return None


def bad_value(fixture):
    """Return a one-line reason a parameter value is malformed, or None if every value is well-formed.

    Independent of the grammar: `n` is an integer (the root's is -1, the auto-detect base), `c` a context, `t` a
    chomping mode, whatever production carries them.
    """
    for name, value in fixture.parameters.items():
        if name == "n" and not re.fullmatch(r"-?[0-9]+", value):
            return f"n={value!r} is not an integer"
        if name == "c" and value not in CONTEXTS:
            return f"c={value!r} is not a context"
        if name == "t" and value not in CHOMPINGS:
            return f"t={value!r} is not a chomping mode"
    return None


def annotation_free(production, grammar):
    """Whether running `production` emits every character as unparsed — its reachable body annotates no token.

    These are the productions libyeast flattens to a bare character class because it uses them only inside a `Diff`,
    where the character set matters and the tokens never fire. Run alone they diverge from the reference, which keeps
    the tokens; the migration rewrites their expected output to the plain unparsed libyeast emits.
    """
    seen = set()
    frontier = [production]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        node = grammar.get(name)
        if node is None:
            continue
        stack = [node.body]
        while stack:
            current = stack.pop()
            if isinstance(current, _ANNOTATIONS):
                return False
            if isinstance(current, ir.Ref):
                frontier.append(current.name)
                continue
            for field in getattr(current, "__dataclass_fields__", ()):
                value = getattr(current, field)
                for child in value if isinstance(value, tuple) else (value,):
                    if hasattr(child, "__dataclass_fields__"):
                        stack.append(child)
    return True


def load(tests_dir=TESTS_DIR):
    """Load every fixture in `tests_dir`, one per `.input` file, paired with its `.output` sibling."""
    fixtures = []
    for entry in sorted(os.listdir(tests_dir)):
        if not entry.endswith(".input"):
            continue
        production, parameters, case, is_invalid = parse_name(entry)
        input_path = os.path.join(tests_dir, entry)
        fixtures.append(
            Fixture(
                production=production,
                parameters=parameters,
                case=case,
                is_invalid=is_invalid,
                input_path=input_path,
                output_path=input_path[: -len(".input")] + ".output",
            )
        )
    return fixtures
