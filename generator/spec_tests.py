# SPDX-License-Identifier: MIT
"""
libyeast's own conformance fixtures.

`tests/spec/` holds libyeast's differential oracle. A `<production>[.n=N][.p=N][.c=C][.t=T][.r=R][.i=I].<case>` has an
`.input` YAML fragment and an `.output` YEAST token stream the production must emit for it.

The filename says which production to run and with which parameters. This module decodes that convention. It mirrors the
test runner the reference ships. The module pairs an input with its expected output.

The suite came once from the vendored reference parser's fixtures, and libyeast adds to that suite and corrects it from
there. `runnable_fault` and `bad_value` are what the fixture gate checks a fixture against the grammar with.
"""

import os
import re
from dataclasses import dataclass

import annotated2ir
import gate
import ir

TESTS_DIR = os.path.join(gate.TREE, "tests", "spec")  # the directory the conformance fixtures live in.

# The parameter values the grammar understands, from the reader the grammar itself uses rather than a second list of
# them here. `n` is an indentation, any integer (`-1` is the auto-detect base). It has none. A fixture whose value falls
# outside these comes out malformed rather than merely foreign.
CONTEXTS, CHOMPINGS, RESUMES = annotated2ir.CONTEXTS, annotated2ir.CHOMPINGS, annotated2ir.RESUMES
_INDENT_MODES = annotated2ir.INDENT_MODES  # the values `i` may take, read from the same source.

# `r` is the resume policy. A caller chooses that policy, and the grammar does not thread it. A fixture that does not
# name it runs under the default a zeroed `ys_options` selects. That default is `YS_RESUME_NONE`. A parameter besides
# `r` has to have a name. Such a parameter has no default to fall back on.
_DEFAULTS = {"r": "n"}

# The parameters that a production detects rather than takes. The auto-detected indent `m` and the block scalar's floor
# `f` come from the production that measures them. A caller passes neither, and a fixture names neither. A fixture
# entering a production that declares such a parameter enters with the parameter unset. That is what a fresh parse gives
# it.
_DETECTED = ("m", "f")

# The parameters a run enters under, rather than a production declaring them. Past `read-indents` the stack holds the
# indentation, and a push lands where the indentation changes. A fixture naming `n` seeds that stack, and no production
# takes `n` as an argument. A fixture names `n` either way, whichever place holds it.
#
# `p` is how much of the input the parse crossed to reach the rule. Those characters go before the run begins, and the
# token they built drops. The line, the column and the input behind the parse follow from those characters rather than
# from what the name asserts. `p` goes undeclared by any production of any stage. The productions declare `n` as the
# author writes the grammar, and stop once `read-indents` has taken it off.
_ENTERED = ("n", "p")

# A production name is the leading run of a filename. That run ends at the first `.`. A parameter is a `.<name>=<value>`
# segment.
_PARAMETER = re.compile(r"\.([nctrip])=([^.]+)")


@dataclass(frozen=True)
class Fixture:
    """A reference test. The production to run, its parameters, and where the input and expected output live."""

    production: str
    parameters: dict[str, str]  # `{"n": "2", "c": "flow-in", ...}`, values verbatim from the filename.
    case: str  # the arbitrary testcase name, such as "a" or "empty.invalid".
    is_invalid: bool  # whether the name claims the production does not cleanly match the whole input.
    input_path: str
    output_path: str

    @property
    def input(self) -> bytes:
        """The YAML fragment fed to the production, as the exact bytes the test is about."""
        with open(self.input_path, "rb") as handle:
            return handle.read()

    @property
    def expected(self) -> str:
        """The YEAST token stream the production must emit for this fixture. The wire form holds it."""
        with open(self.output_path, encoding="utf-8") as handle:
            return handle.read()


def _parse_name(filename: str) -> tuple[str, dict[str, str], str, bool]:
    """
    Decode a fixture filename into the production, the parameters and the case. Also whether it is an invalid-input
    test.

    `filename` is a bare `.input`/`.output` name. The extension is ignored.
    """
    stem = filename.rsplit(".", 1)[0] if filename.endswith((".input", ".output")) else filename
    production = stem.split(".", 1)[0]
    rest = stem[len(production) :]
    parameters = dict(_PARAMETER.findall(rest))
    case = _PARAMETER.sub("", rest).strip(".")
    return production, parameters, case, "invalid" in case.split(".")


def runnable_fault(fixture: Fixture, grammar: dict[str, ir.Prod]) -> str | None:
    """
    Return None if `grammar` can run `fixture`, else a single-line reason it cannot.

    Runnable means a pair of things. The grammar has the production and declares the parameters the filename supplies,
    bar the `ENTERED` parameters a run enters under. The filename supplies the parameters the grammar declares. That
    holds bar the parameters `DEFAULTS` answers for, and bar the `DETECTED` parameters a production binds for itself.

    This is the structural test the interpreter driver filters on. Whether the grammar understands a supplied value is a
    separate data check the reference-test gate makes.
    """
    name, runtime = ir.entry(grammar, fixture.production, fixture.parameters)
    production = grammar.get(name)
    if production is None:
        return "not a production of the grammar"
    given = set(runtime)  # the finite parameters a monomorphized copy fixes are in its name, not its arguments
    wanted = set(production.params)
    if given - wanted - set(_ENTERED):
        listed = ", ".join(sorted(given - wanted - set(_ENTERED)))
        declared = ", ".join(production.params) or "none"
        return f"parameters {{{listed}}} are not the grammar's {{{declared}}}"
    if wanted - given - set(_DEFAULTS) - set(_DETECTED):
        listed = ", ".join(sorted(wanted - given - set(_DEFAULTS) - set(_DETECTED)))
        return f"the grammar declares parameters {{{listed}}} and the filename does not give them"
    return None


def arguments(fixture: Fixture, grammar: dict[str, ir.Prod]) -> dict[str, str]:
    """
    The parameters to run `fixture` with. The filename names some, and `DEFAULTS` answers for a parameter it omits.
    """
    resolved, _runtime = ir.entry(grammar, fixture.production, fixture.parameters)
    declared = grammar[resolved].params
    defaulted = {name: value for name, value in _DEFAULTS.items() if name in declared}
    return {**defaulted, **fixture.parameters}


def bad_value(fixture: Fixture) -> str | None:
    """
    Return a single-line reason a parameter value comes out malformed, or None where the values are well-formed.

    Independent of the grammar. `n` is an integer. The root takes the auto-detect base `-1`. `p` is a count of
    characters and so is not negative. `c` is a context and `t` a chomping mode. `r` is a resume policy and `i` an
    indentation mode. Whatever production takes them.
    """
    for name, value in fixture.parameters.items():
        if name == "n" and not re.fullmatch(r"-?[0-9]+", value):
            return f"n={value!r} is not an integer"
        if name == "p" and not re.fullmatch(r"[0-9]+", value):
            return f"p={value!r} is not a count of characters"
        if name == "c" and value not in CONTEXTS:
            return f"c={value!r} is not a context"
        if name == "t" and value not in CHOMPINGS:
            return f"t={value!r} is not a chomping mode"
        if name == "r" and value not in RESUMES:
            return f"r={value!r} is not a resume policy"
        if name == "i" and value not in _INDENT_MODES:
            return f"i={value!r} is not an indentation mode"
    return None


def load(tests_dir: str = TESTS_DIR) -> list[Fixture]:
    """Load the fixtures in `tests_dir`. A fixture is an `.input` file with its `.output` sibling."""
    fixtures = []
    for path in gate.named_in(tests_dir, ".input"):
        entry = path.name
        production, parameters, case, is_invalid = _parse_name(entry)
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
