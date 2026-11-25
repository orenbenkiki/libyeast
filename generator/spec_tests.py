# SPDX-License-Identifier: MIT
"""
libyeast's own conformance fixtures.

`tests/spec/` holds libyeast's differential oracle. A `<production>[.n=N][.p=N][.c=C][.t=T][.r=R][.i=I].<case>` has an
`.input` YAML fragment and an `.output` YEAST token stream the production must emit for it.

The filename says which production to run and with which parameters. This module decodes that convention. The module
pairs an input with its expected output.

The fixture gate `check_spec_tests` checks a fixture against the grammar with `runnable_fault`. It checks the form of
the fixture's parameter values with `bad_value`.
"""

import os
import re
from dataclasses import dataclass

import annotated2ir
import gate
import ir

TESTS_DIR = os.path.join(gate.TREE, "tests", "spec")  # the directory the conformance fixtures live in.

# `annotated2ir` lists the parameter values the grammar understands. This module keeps no second list. `n` is an
# indentation. `bad_value` accepts any integer for `n`. `l-bare-document` enters at an `n` of `-1`. `p` counts
# characters. `bad_value` accepts only a non-negative integer for `p`. `check_spec_tests` reports a fixture with a bad
# value as malformed.
CONTEXTS, CHOMPINGS, RESUMES = annotated2ir.CONTEXTS, annotated2ir.CHOMPINGS, annotated2ir.RESUMES
_INDENT_MODES = (
    annotated2ir.INDENT_MODES
)  # Indent modes come from `annotated2ir.INDENT_MODES`. A mode says how a block scalar gets `i`.

# `r` is the resume policy. A fixture that does not name it runs under `n`. The filename names a declared parameter
# other than `r`, `m` or `f`. Such a parameter has no default to fall back on.
_DEFAULTS = {"r": "n"}

# The parameters that a production detects rather than takes. `runnable_fault` lets a fixture leave either out.
_DETECTED = ("m", "f")

# The parameters a run enters under, rather than parameters a production declares. Past `read-indents`, a run's stack
# holds the indentation. A push lands where the indentation changes. `run` seeds the stack from the `n` a fixture names.
# A fixture names `n` for the stack and for the parameter alike.
#
# `p` counts the characters a parse crossed to reach a rule. `run` starts past those characters and drops their token.
# `run` derives the line, the column and the input behind a parse from the characters `try_consume` consumes. `run` pops
# `p` before it enters a production. A production ahead of `read-indents` declares `n`. A production past `read-indents`
# declares no `n`.
_ENTERED = ("n", "p")

# A production name is the leading run of a filename. That run ends at the first `.`. A parameter is a
# `.<letter>=<value>` segment. The letter comes from `nctrip`. The value holds no `.`.
_PARAMETER = re.compile(r"\.([nctrip])=([^.]+)")


@dataclass(frozen=True)
class Fixture:
    """
    A fixture is a reference test. A fixture names the production to run and its parameters. A fixture also names the
    files holding the input and the expected output.

    `check_spec_tests` refuses a fixture under `is_invalid` whose expected output matches the whole input cleanly.
    """

    production: str
    parameters: dict[str, str]  # `{"n": "2", "c": "flow-in", ...}`, values verbatim from the filename.
    case: str  # the arbitrary testcase name, such as `a` or `empty.invalid`.
    is_invalid: bool  # whether the name says the production fails to match the whole input cleanly.
    input_path: str
    output_path: str

    @property
    def input(self) -> bytes:
        """The YAML fragment fed to the production, as the exact bytes the test is about."""
        with open(self.input_path, "rb") as handle:
            return handle.read()

    @property
    def expected(self) -> str:
        """
        The YEAST token stream the production must emit for this fixture. `check_interpreter` compares it with the text
        `wire.serialize` writes.
        """
        with open(self.output_path, encoding="utf-8") as handle:
            return handle.read()


def _parse_name(filename: str) -> tuple[str, dict[str, str], str, bool]:
    """
    Decode a fixture filename into the production, the parameters and the case. Also decode whether the name claims an
    invalid-input test.

    `filename` is a bare `.input`/`.output` name. This ignores the extension.
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

    Runnable means the conditions below hold. The grammar has the production `ir.entry` resolves the fixture to. That
    production declares the parameters the filename supplies, bar the `_ENTERED` parameters a run enters under and bar
    the finite parameters its name fixes. The filename supplies the parameters the grammar declares. That holds bar the
    parameters `_DEFAULTS` supplies, and bar the `_DETECTED` parameters a production binds for itself.

    This is the structural test `check_normalize` filters fixtures on before the interpreter runs them. A separate
    function, `bad_value`, tests the form of a supplied value. `check_spec_tests` calls both.
    """
    name, runtime = ir.entry(grammar, fixture.production, fixture.parameters)
    production = grammar.get(name)
    if production is None:
        return "not a production of the grammar"
    given = set(
        runtime
    )  # The name of a monomorphic copy fixes its finite parameters. `ir.entry` drops them from the arguments.
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
    The parameters to run `fixture` with. The filename names some, and `_DEFAULTS` supplies a parameter the production
    declares and the filename omits.
    """
    resolved, _runtime = ir.entry(grammar, fixture.production, fixture.parameters)
    declared = grammar[resolved].params
    defaulted = {name: value for name, value in _DEFAULTS.items() if name in declared}
    return {**defaulted, **fixture.parameters}


def bad_value(fixture: Fixture) -> str | None:
    """
    Return a single-line reason a parameter value comes out malformed, or None where the values are well-formed.

    `n` is an integer. `l-bare-document` enters at an `n` of `-1`. `p` is a count of characters and is therefore not
    negative. `c` is a context, and `t` is a chomping mode. `r` is a resume policy, and `i` is an indentation mode. The
    check of a value does not depend on the production that takes the value.
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
