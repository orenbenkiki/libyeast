# SPDX-License-Identifier: MIT
"""Migrate the reference parser's fixtures into libyeast's own conformance suite.

Run once, and again whenever the rules below change, the way the grammar was migrated from the vendored spec: it reads
`third_party/yamlreference/tests/` and writes `tests/spec/`, which is then libyeast's to own. Only fixtures that align
with libyeast's grammar are taken (`spec_tests.is_runnable`); each is rewritten so its expected output is what libyeast
emits rather than what the reference does:

  * a production libyeast flattens to a bare character class emits plain unparsed, so its tokens are recoded to unparsed
    and merged into one run (`spec_tests.annotation_free`);
  * no token spans a line, so a run is cut into a token for each line's content and each break;
  * an error's message names libyeast's productions, not the reference's, so it is blanked — the failure's position is
    kept and checked, the wording is not.

A production libyeast annotates the same as the reference keeps its output verbatim; the interpreter is what proves that
claim, fixture by fixture.
"""

import os
import shutil

import annotated2ir
import spec_tests
import wire


def rewrite_bom(tokens, data):
    """Give each byte-order-mark token the character it matched, not the reference's detected encoding name.

    libyeast reads only UTF-8 and detects no encoding, so its `bom` token holds the mark itself. The consumed bytes are
    the token's span — up to the next token's start — re-escaped by codepoint.
    """
    rewritten = []
    for index, token in enumerate(tokens):
        if token.code == wire.BOM:
            end = tokens[index + 1].start.byte if index + 1 < len(tokens) else len(data)
            rewritten.append(wire.Token(wire.BOM, token.start, wire.escape(data[token.start.byte : end])))
        else:
            rewritten.append(token)
    return rewritten


def rewrite(fixture, annotation_free, data):
    """Rewrite `fixture`'s reference output into the token stream libyeast emits, as a list of tokens."""
    output = []
    run = []
    run_start = None

    def flush():
        nonlocal run, run_start
        if run:
            output.extend(wire.split_unparsed(run, run_start))
            run = []
            run_start = None

    for token in rewrite_bom(wire.parse(fixture.expected), data):
        if token.code == wire.ERROR:
            flush()
            output.append(wire.Token(wire.ERROR, token.start, ""))
        elif annotation_free:
            if run_start is None:
                run_start = token.start
            run.extend(wire.units(token.text))
        else:
            output.append(token)
    flush()
    return output


def main():
    grammar = annotated2ir.load()
    fixtures = spec_tests.load(spec_tests.SOURCE_DIR)

    if os.path.isdir(spec_tests.TESTS_DIR):
        shutil.rmtree(spec_tests.TESTS_DIR)
    os.makedirs(spec_tests.TESTS_DIR)

    migrated = 0
    foreign = 0
    non_utf8 = 0
    errors = []
    for fixture in fixtures:
        if spec_tests.is_runnable(fixture, grammar) is not None or spec_tests.bad_value(fixture) is not None:
            foreign += 1
            continue
        with open(fixture.input_path, "rb") as handle:
            data = handle.read()
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            non_utf8 += 1  # libyeast reads only UTF-8, so a non-UTF-8 input is one it never sees
            continue
        tokens = rewrite(fixture, spec_tests.annotation_free(fixture.production, grammar), data)
        reason = wire.chain_fault(tokens)
        if reason is not None:
            errors.append(f"{os.path.basename(fixture.input_path)}: {reason}")
            continue
        base = os.path.basename(fixture.input_path)[: -len(".input")]
        shutil.copyfile(fixture.input_path, os.path.join(spec_tests.TESTS_DIR, base + ".input"))
        with open(os.path.join(spec_tests.TESTS_DIR, base + ".output"), "w") as handle:
            handle.write(wire.serialize(tokens))
        migrated += 1

    for error in errors:
        print(error)
    print(f"migrated {migrated} fixtures into tests/spec; skipped {foreign} reference-internal, {non_utf8} non-UTF-8")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
