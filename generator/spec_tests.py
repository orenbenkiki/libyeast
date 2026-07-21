# SPDX-License-Identifier: MIT
"""
libyeast's own conformance fixtures.

`tests/spec/` holds libyeast's differential oracle: for each `<production>[.n=N][.c=C][.t=T][.r=R].<case>` an `.input`
YAML fragment and an `.output` YEAST token stream the production must emit for it. The filename says which production to
run and with which parameters — this module decodes that convention, mirroring the reference's own test runner, and
pairs each input with its expected output.

The suite was built once from the vendored reference parser's fixtures and is now libyeast's to add to and correct;
`is_runnable` and `bad_value` are what the fixture gate checks each fixture against the grammar with.
"""

import os
import re
from dataclasses import dataclass

import annotated2ir
import ir

_TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(_TREE, "tests", "spec")

# The parameter values the grammar understands, from the grammar's own reader rather than a second list of them here.
# `n` is an indentation, any integer (-1 is the auto-detect base), so it has none. A fixture whose value falls outside
# these is malformed, not merely foreign.
CONTEXTS, CHOMPINGS, RESUMES = annotated2ir.CONTEXTS, annotated2ir.CHOMPINGS, annotated2ir.RESUMES

# `r` is the resume policy, and the only parameter a caller chooses rather than the grammar threads: a fixture that does
# not name it runs under the default a zeroed `ys_options` already selects, `YS_RESUME_NONE`. Every other parameter must
# be named, having no default to fall back on.
DEFAULTS = {"r": "n"}

# A production name is the leading run of a filename, up to its first `.`; a parameter is a `.<name>=<value>` segment.
_PARAMETER = re.compile(r"\.([nctr])=([^.]+)")


@dataclass(frozen=True)
class Fixture:
    """One reference test: the production to run, its parameters, and where the input and expected output live."""

    production: str
    parameters: dict  # {"n": "2", "c": "flow-in", ...}, values verbatim from the filename
    case: str  # the arbitrary testcase name, e.g. "a" or "empty.invalid"
    is_invalid: bool  # whether the name claims the production does not cleanly match the whole input
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
    """
    Decode a fixture filename into its production, parameters, case, and whether it is an invalid-input test.

    `filename` is a bare `.input`/`.output` name; its extension is ignored.
    """
    stem = filename.rsplit(".", 1)[0] if filename.endswith((".input", ".output")) else filename
    production = stem.split(".", 1)[0]
    rest = stem[len(production) :]
    parameters = {name: value for name, value in _PARAMETER.findall(rest)}
    case = _PARAMETER.sub("", rest).strip(".")
    return production, parameters, case, "invalid" in case.split(".")


def is_runnable(fixture, grammar):
    """
    Return None if `grammar` can run `fixture`, else a one-line reason it cannot.

    Runnable means the grammar has the production and declares every parameter the filename supplies, and the filename
    supplies every parameter the grammar declares but for the ones `DEFAULTS` answers for. This is the structural test
    the interpreter driver filters on; that the supplied values are ones the grammar understands is a separate data
    check the reference-test gate makes.
    """
    name, runtime = ir.entry(grammar, fixture.production, fixture.parameters)
    production = grammar.get(name)
    if production is None:
        return "not a production of the official grammar"
    given = set(runtime)  # the finite parameters a monomorphized copy fixes are in its name, not its arguments
    wanted = set(production.params)
    if given - wanted:
        listed = ", ".join(sorted(given - wanted))
        declared = ", ".join(production.params) or "none"
        return f"parameters {{{listed}}} are not the grammar's {{{declared}}}"
    if wanted - given - set(DEFAULTS):
        listed = ", ".join(sorted(wanted - given - set(DEFAULTS)))
        return f"parameters {{{listed}}} are the grammar's and the filename does not give them"
    return None


def arguments(fixture, grammar):
    """The parameters to run `fixture` with: those its filename names, and `DEFAULTS` for those it leaves out."""
    resolved, _runtime = ir.entry(grammar, fixture.production, fixture.parameters)
    declared = grammar[resolved].params
    defaulted = {name: value for name, value in DEFAULTS.items() if name in declared}
    return {**defaulted, **fixture.parameters}


def bad_value(fixture):
    """
    Return a one-line reason a parameter value is malformed, or None if every value is well-formed.

    Independent of the grammar: `n` is an integer (the root's is -1, the auto-detect base), `c` a context, `t` a
    chomping mode, `r` a resume policy, whatever production carries them.
    """
    for name, value in fixture.parameters.items():
        if name == "n" and not re.fullmatch(r"-?[0-9]+", value):
            return f"n={value!r} is not an integer"
        if name == "c" and value not in CONTEXTS:
            return f"c={value!r} is not a context"
        if name == "t" and value not in CHOMPINGS:
            return f"t={value!r} is not a chomping mode"
        if name == "r" and value not in RESUMES:
            return f"r={value!r} is not a resume policy"
    return None


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
