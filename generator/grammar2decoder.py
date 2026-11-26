# SPDX-License-Identifier: MIT
"""
Emit the decoder's tables from the grammar IR. This is the data half of the decoder.

RFC 3629 fixes the UTF-8 mechanics, and `decoder.c` holds them hand-written. https://www.rfc-editor.org/rfc/rfc3629 is
that RFC. The grammar derives the classification. `chars.Model` computes the keys, and this file emits them. `decoder.c`
picks among the non-ASCII keys by hand, and `check_groups` holds the grammar to that grouping.

The classification holds the key of an ASCII character and the key of a character the grammar names. The classification
also holds the bit of a character set the grammar tests, and the keys a non-ASCII character can take.

The key's layout belongs to `chars`. Code reading or building a key goes through the macros this file emits.

Run `make regen` or `python3 generator/grammar2decoder.py`. The script writes `src/decoder_tables.h`.
"""

import io
import os
import unicodedata
from collections.abc import Mapping, Sequence

import annotated2ir
import chars
import gate
import ir

_ASCII_LIMIT = 0x80  # the first codepoint past the ASCII table. The probing starts there.
_DELETE = 0x7F  # DEL. The key table names it.
_NEL = 0x85  # NEXT LINE. `_CONTROL_NAMES` names it.
_NONCHARACTERS = (
    0xFFFE,
    0xFFFF,
)  # The noncharacters `check_groups` probes and `_non_ascii_group` keys as `YS_KEY_NOT_PRINTABLE`.
_SURROGATES = (
    0xD800,
    0xDFFF,
)  # The first and the last surrogate. `check_groups` skips a surrogate among its probes.
_C1_LIMIT = 0xA0  # the C1 controls run from the end of ASCII up to here.
_HEX_LETTERS = "ABCDEFabcdef"  # the letters `ns-hex-digit` takes, in upper case and in lower case.

# Unicode gives the control characters an alias rather than a name.
# https://www.unicode.org/Public/UCD/latest/ucd/NameAliases.txt lists those aliases.
_CONTROL_NAMES = {
    0x09: "CHARACTER TABULATION",
    0x0A: "LINE FEED",
    0x0D: "CARRIAGE RETURN",
    _NEL: "NEXT LINE",
}


def _ascii_group(codepoint: int) -> str:
    """
    The name of the key an unnamed ASCII character takes.

    A key names a group of characters the grammar cannot tell apart. `check_groups` proves that a key groups such
    characters. A grammar change cannot quietly redefine a name.
    """
    if codepoint < 0x20:
        return "YS_KEY_CONTROL"
    if codepoint == _DELETE:
        return "YS_KEY_DELETE"
    if chr(codepoint).isdigit():
        return "YS_KEY_DIGIT"
    if chr(codepoint) in _HEX_LETTERS:
        return "YS_KEY_HEX_LETTER"
    if chr(codepoint).isalpha():
        return "YS_KEY_LETTER"
    return "YS_KEY_OTHER"


def _non_ascii_group(codepoint: int) -> str:
    """
    The name of the key an unnamed non-ASCII character takes, with the length bits left out. `decoder.c` ORs the length
    bits in.

    The unnamed C1 controls and the noncharacters share a key. Both fall inside `nb-json` and outside `c-printable`.
    """
    if codepoint < _C1_LIMIT or codepoint in _NONCHARACTERS:
        return "YS_KEY_NOT_PRINTABLE"
    return "YS_KEY_CONTENT"


def check_groups(model: chars.Model, grammar: Mapping[str, ir.Prod]) -> dict[str, int]:
    """
    The key of a character the grammar does not name. The group name keys the answer.

    A group is a set of characters the grammar cannot tell apart. `decoder.c` classifies non-ASCII characters by UTF-8
    byte pattern, and the grammar reaches none of that. `decoder.c` stays correct while the grammar keeps grouping
    characters exactly this way. A change to the grouping would leave `decoder.c` misclassifying characters in silence.
    So this check fails generation instead. Checking a character per segment is exhaustive. A key cannot vary within a
    segment.
    """
    keys: dict[str, int] = {}
    for codepoint in range(_ASCII_LIMIT):
        if codepoint not in model.literal_ids:
            _record(keys, _ascii_group(codepoint), model.key(codepoint, 1), codepoint)
    probes = [point for point in chars.representatives(grammar) if point >= _ASCII_LIMIT]
    probes += list(range(_ASCII_LIMIT, _C1_LIMIT)) + list(_NONCHARACTERS)
    for codepoint in probes:
        is_surrogate = _SURROGATES[0] <= codepoint <= _SURROGATES[1]
        if codepoint not in model.literal_ids and not is_surrogate:
            _record(keys, _non_ascii_group(codepoint), model.key(codepoint, 0), codepoint)
    return keys


def _record(keys: dict[str, int], group: str, key: int, codepoint: int) -> None:
    """Note that `codepoint` takes `key` as a member of `group`. A group that disagrees with itself fails here."""
    if keys.setdefault(group, key) != key:
        raise ValueError(
            f"U+{codepoint:04X} has key {key:#010x} and {group} holds {keys[group]:#010x}. the grammar groups "
            f"characters in a way the decoder does not assume."
        )


def _literal_name(codepoint: int) -> str:
    """
    The C identifier for a character the grammar names. A control character takes its alias from `_CONTROL_NAMES`. Any
    other character takes its Unicode name.
    """
    name = _CONTROL_NAMES.get(codepoint) or unicodedata.name(chr(codepoint))
    return name.replace(" ", "_").replace("-", "_")


def _set_name(name: str) -> str:
    """The suffix of a character set's C identifiers. `ns-plain-safe-in` becomes `NS_PLAIN_SAFE_IN`."""
    return name.upper().replace("-", "_")


def _set_cite(name: str, grammar: Mapping[str, ir.Prod]) -> str:
    """The comment naming the production a character set comes from."""
    owner = name if name in grammar else name.rsplit("-inline-", 1)[0]
    return f"[{grammar[owner].number:03d}] {owner}"


def _form(codepoint: int) -> str:
    """
    _form writes a character for a comment. _form quotes printable ASCII other than a space, a single quote and a
    backslash. _form writes other characters in Unicode notation.
    """
    if 0x21 <= codepoint <= 0x7E and codepoint not in (ord("'"), ord("\\")):
        return f"'{chr(codepoint)}'"
    return f"U+{codepoint:04X}"


def _defined(grammar: Mapping[str, ir.Prod], body: ir.Node) -> int | None:
    """
    The character that a production defines outright, or None. The body takes that codepoint. The form the body uses
    changes nothing.

    Read off what the body consumes rather than off its shape. The tables then hold a character written a new way rather
    than silently missing from the tables. `chars.denote` answers what a node takes, and a production defines a
    character exactly where that answer is a set of a single codepoint.
    """
    return chars.single_codepoint(chars.denote(grammar, body))


def _sites(grammar: Mapping[str, ir.Prod]) -> dict[int, tuple[list[str], list[str]]]:
    """
    The places the grammar names a character. A codepoint keys a pair of lists of production citations. The first list
    holds the plainest production that defines the character. The second holds the productions that use it.

    A production defines a character where its body takes that character and takes nothing besides. A pair of
    productions may do so, and the plainest of them names the character. `chars.naming_productions` answers that for a
    lone character exactly as it does for a wider set.

    The characters that no production names appear inline. A citation of such a character names the productions it
    appears in.
    """
    cited = {
        production_name: f"[{named_production.number:03d}] {production_name}"
        for production_name, named_production in grammar.items()
    }
    naming = chars.naming_productions(grammar)
    definer: dict[int, list[str]] = {}
    for denotation, name in naming.items():
        codepoint = chars.single_codepoint(denotation)
        if codepoint is not None:
            rivals = definer.setdefault(codepoint, [])
            rivals.append(name)
    found: dict[int, tuple[list[str], list[str]]] = {
        defined_codepoint: ([cited[chars.simplest_name(grammar, names)]], [])
        for defined_codepoint, names in definer.items()
    }

    for name, production in grammar.items():
        if _defined(grammar, production.body) is not None:
            # A production that defines a character adds nothing to the second list. The first list names the plainest
            # such production.
            continue
        pending, seen = [production.body], set[int]()
        while pending:
            node = pending.pop()
            nested = list(chars.children(node))
            # The second list counts a leaf node that denotes a lone codepoint. A node with children counts nothing. The
            # loop searches the children of that node instead.
            written = None if nested else chars.single_codepoint(chars.denote(grammar, node))
            if written is not None and written not in seen:
                seen.add(written)
                found.setdefault(written, ([], []))[1].append(cited[name])
            pending.extend(nested)
    return found


def _cite(codepoint: int, where: Mapping[int, tuple[list[str], list[str]]]) -> str:
    """
    _cite returns the sorted, comma-joined names of the productions that define a character, for a comment to cite. A
    character no production defines gets the productions that use it.
    """
    defining, using = where.get(codepoint, ([], []))
    return ", ".join(sorted(defining or using))


def _defines(out: io.StringIO, rows: Sequence[tuple[str, str, str]]) -> None:
    """Write `#define` lines. The names, the values and the comments line up into columns."""
    name_width = max(len(row_name) for row_name, _value, _comment in rows)
    value_width = max(len(row_value) for _name, row_value, _comment in rows)
    for name, value, comment in rows:
        line = f"#define {name:<{name_width}} {value:<{value_width}}"
        out.write(f"{line} // {comment}\n" if comment else f"{line.rstrip()}\n")


def _entries(out: io.StringIO, rows: Sequence[tuple[str, str]]) -> None:
    """Write the entries of an initializer, and align their comments into a column."""
    width = max(len(row_value) for row_value, _comment in rows)
    for value, comment in rows:
        out.write(f"    {value + ',':<{width + 1}} // {comment}\n")


# TABLES holds the path of the header this generator writes. The header sits in the tree. `make verify-decoder` runs
# `generator/check_decoder.py`, and that script fails where the header differs from what the grammar says.
TABLES = os.path.join(gate.TREE, "src", "decoder_tables.h")


def emit(out: io.StringIO, model: chars.Model, grammar: Mapping[str, ir.Prod], keys: Mapping[str, int]) -> None:
    """
    Write the header.

    The tables read as tables only when their columns line up. `src/decoder_tables.h` holds citation lines past the
    column limit `.clang-format` sets. clang-format would break such a line, as
    https://clang.llvm.org/docs/ClangFormatStyleOptions.html#reflowcomments says. The header therefore holds a
    `clang-format off`, and this function aligns the columns itself in place of clang-format.
    """
    where = _sites(grammar)
    out.write("// SPDX-License-Identifier: MIT\n")
    out.write("// Generated by `generator/grammar2decoder.py` from `grammar/yeast-spec-1.2.yaml`. Do not edit.\n")
    out.write("// clang-format off\n")
    out.write("#ifndef YEAST_DECODER_TABLES_H\n")
    out.write("#define YEAST_DECODER_TABLES_H\n\n")
    out.write("#include <stdint.h>\n\n")

    # Write a comment on the key's layout from the `chars` constants the macros use.
    ids = f"0..{chars.SET_SHIFT - 1}"
    bits = f"{chars.SET_SHIFT}..{chars.SET_SHIFT + len(model.sets) - 1}"
    length = f"{chars.LEN_SHIFT}..{chars.LEN_SHIFT + 2}"
    out.write(f"// A character's key holds the id of the character the grammar names in bits {ids},\n")
    out.write(f"// a bit per character set the grammar tests in bits {bits}, and the bytes the\n")
    out.write(f"// character consumed in bits {length}. Use the macros below.\n")
    _defines(
        out,
        [
            ("YS_LEN(key)", f"(((key) >> {chars.LEN_SHIFT}) & 0x7u)", "the bytes a character consumed"),
            ("YS_LENGTH_BITS(length)", f"((uint32_t)(length) << {chars.LEN_SHIFT})", "the way the decoder writes them"),
        ],
    )

    out.write("\n// The key of a character the grammar names. A key stays put. The sets and the length stay put\n")
    out.write("// as well. Testing for a character is one comparison.\n")
    _defines(
        out,
        [
            (
                f"YS_LIT_KEY_{_literal_name(codepoint)}",
                f"0x{model.key(codepoint, _utf8_length(codepoint)):08X}u",
                f"{_form(codepoint) + ',':<7} {_cite(codepoint, where)}",
            )
            for codepoint in model.literals
        ],
    )

    out.write("\n// The sentinels: an id and no set bits. A membership test fails at them.\n")
    _defines(
        out,
        [
            ("YS_LIT_KEY_EOF", f"0x{model.sentinel(model.lit_eof, 0):08X}u", "the window is empty"),
            ("YS_LIT_KEY_INVALID", f"0x{model.sentinel(model.lit_invalid, 1):08X}u", "the bytes are not UTF-8"),
        ],
    )

    out.write(
        "\n// One bit per character set the grammar tests. The generator evaluated the unions and subtractions "
        "already.\n"
    )
    _defines(
        out,
        [
            (f"YS_SET_BIT_{_set_name(set_name)}", f"0x{model.set_mask(index):08X}u", _set_cite(set_name, grammar))
            for index, (set_name, _set_denotation) in enumerate(model.sets)
        ],
    )

    out.write("\n// The character sets by id. `ys_consume_set` reads them.\n")
    out.write("typedef enum ys_set_id {\n")
    for name, _denotation in model.sets:
        out.write(f"    YS_SET_ID_{_set_name(name)},\n")
    out.write("    YS_SET_ID_COUNT\n")
    out.write("} ys_set_id;\n\n")

    out.write("// The bit of a character set. Its id finds the bit.\n")
    out.write("static const uint32_t YS_SET_BITS[YS_SET_ID_COUNT] = {\n")
    _entries(
        out,
        [
            (f"YS_SET_BIT_{_set_name(set_name)}", _set_cite(set_name, grammar))
            for set_name, _set_denotation in model.sets
        ],
    )
    out.write("};\n\n")

    out.write("// The key of an ASCII character the grammar does not name - one per group of characters that the\n")
    out.write("// grammar cannot tell apart.\n")
    _defines(
        out,
        [
            ("YS_KEY_CONTROL", f"0x{keys['YS_KEY_CONTROL']:08X}u", "a C0 control. that character belongs to no set."),
            ("YS_KEY_DELETE", f"0x{keys['YS_KEY_DELETE']:08X}u", "U+007F, JSON-compatible but not printable"),
            ("YS_KEY_DIGIT", f"0x{keys['YS_KEY_DIGIT']:08X}u", "'1'..'9' ('0' the grammar names)"),
            ("YS_KEY_HEX_LETTER", f"0x{keys['YS_KEY_HEX_LETTER']:08X}u", "a letter that is also a hexadecimal digit"),
            ("YS_KEY_LETTER", f"0x{keys['YS_KEY_LETTER']:08X}u", "any other letter"),
            ("YS_KEY_OTHER", f"0x{keys['YS_KEY_OTHER']:08X}u", "printable, and in none of the grammar's classes"),
        ],
    )

    out.write("\n// The key of a valid non-ASCII character the grammar does not name, with its length bits left out.\n")
    out.write("// Such a character is a 2-byte, 3-byte or 4-byte sequence. `decoder.c` ORs its length in.\n")
    _defines(
        out,
        [
            (
                "YS_KEY_NOT_PRINTABLE",
                f"0x{keys['YS_KEY_NOT_PRINTABLE']:08X}u",
                "a C1 control or a noncharacter: JSON-compatible, not printable",
            ),
            ("YS_KEY_CONTENT", f"0x{keys['YS_KEY_CONTENT']:08X}u", "an ordinary content character"),
        ],
    )

    out.write("\n// The key of an ASCII character. Index it with the byte.\n")
    out.write(f"static const uint32_t YS_ASCII[{_ASCII_LIMIT}] = {{\n")
    _entries(
        out,
        [
            (
                f"YS_LIT_KEY_{_literal_name(codepoint)}" if codepoint in model.literal_ids else _ascii_group(codepoint),
                _form(codepoint),
            )
            for codepoint in range(_ASCII_LIMIT)
        ],
    )
    out.write("};\n\n")
    out.write("#endif // YEAST_DECODER_TABLES_H\n")
    out.write("// clang-format on\n")


def _utf8_length(codepoint: int) -> int:
    """The number of bytes UTF-8 uses to encode `codepoint`."""
    if codepoint < 0x80:
        return 1
    if codepoint < 0x800:
        return 2
    if codepoint < 0x10000:
        return 3
    return 4


def main() -> None:
    grammar = annotated2ir.load()
    model = chars.Model(grammar)
    # The function builds the whole header before writing it. A failure then leaves the committed file untouched.
    source = io.StringIO()
    emit(source, model, grammar, check_groups(model, grammar))
    with open(TABLES, "w", encoding="utf-8") as handle:
        handle.write(source.getvalue())
    print(f"wrote {TABLES}: {len(model.literals)} literals, {len(model.sets)} sets")


if __name__ == "__main__":
    main()
