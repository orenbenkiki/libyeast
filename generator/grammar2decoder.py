# SPDX-License-Identifier: MIT
"""
Emit the decoder's tables from the grammar IR. This is the data half of the decoder.

RFC 3629 fixes the UTF-8 mechanics, and `decoder.c` holds them hand-written. The grammar derives the classification, and
this file holds the whole classification.

The classification holds the key of an ASCII character and the key of a character the grammar names. The classification
also holds the bit of a character set the grammar tests, and the keys a non-ASCII character can take.

The key's layout belongs to this file. Code reading or building a key goes through the macros this file emits.

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

_ASCII_LIMIT = 0x80  # the codepoint the ASCII table ends at. The probing starts there.
_DELETE = 0x7F  # DEL. The key table names it.
_NEL = 0x85  # NEXT LINE. The key table names it.
_NONCHARACTERS = (0xFFFE, 0xFFFF)  # probed, and reached by no decode.
_SURROGATES = (0xD800, 0xDFFF)  # probed, and refused where a decode reaches a surrogate.
_C1_LIMIT = 0xA0  # the C1 controls run from the end of ASCII up to here.
_HEX_LETTERS = "ABCDEFabcdef"  # the letters a hex escape may use, in upper case and in lower case.

# Unicode gives the control characters an alias rather than a name. These are the aliases the grammar names.
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

    The C1 controls and the noncharacters share a key. To the grammar, both are merely JSON-compatible and not
    printable.
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
    """The C identifier for a character the grammar names. The Unicode name of the character gives it."""
    name = _CONTROL_NAMES.get(codepoint) or unicodedata.name(chr(codepoint))
    return name.replace(" ", "_").replace("-", "_")


def _set_name(name: str) -> str:
    """The C identifier for a character set. `ns-plain-safe-in` becomes `NS_PLAIN_SAFE_IN`."""
    return name.upper().replace("-", "_")


def _set_cite(name: str, grammar: Mapping[str, ir.Prod]) -> str:
    """The comment naming the production a character set comes from."""
    owner = name if name in grammar else name.rsplit("-inline-", 1)[0]
    return f"[{grammar[owner].number:03d}] {owner}"


def _form(codepoint: int) -> str:
    """
    The way a comment shows a character. The character itself where that is legible, and its Unicode notation otherwise.
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
    The places the grammar names a character. A codepoint keys a pair of lists of production texts. The first list holds
    the productions that define the character. The second holds the productions that use it.

    A production defines a character where its body takes that character and takes nothing besides. A pair of
    productions may do so, and the plainest of them names the character. `chars.naming_productions` answers that for a
    lone character exactly as it does for a wider set.

    The characters that no production names appear inline. The `ns-uri-char` production and its like hold such a
    character. A citation of such a character names the productions it appears in.
    """
    cited = {name: f"[{production.number:03d}] {name}" for name, production in grammar.items()}
    naming = chars.naming_productions(grammar)
    definer: dict[int, list[str]] = {}
    for denotation, name in naming.items():
        codepoint = chars.single_codepoint(denotation)
        if codepoint is not None:
            rivals = definer.setdefault(codepoint, [])
            rivals.append(name)
    found: dict[int, tuple[list[str], list[str]]] = {
        codepoint: ([cited[chars.simplest_name(grammar, names)]], []) for codepoint, names in definer.items()
    }

    for name, production in grammar.items():
        if _defined(grammar, production.body) is not None:
            continue  # its own character is named above, wherever the plainest form of it appears
        pending, seen = [production.body], set[int]()
        while pending:
            node = pending.pop()
            nested = list(chars.children(node))
            # A character is written *here* where the node holds nothing and takes a codepoint. That is a leaf form it,
            # rather than a shape built over one, which is cited at whatever it is built from.
            written = None if nested else chars.single_codepoint(chars.denote(grammar, node))
            if written is not None and written not in seen:
                seen.add(written)
                found.setdefault(written, ([], []))[1].append(cited[name])
            pending.extend(nested)
    return found


def _cite(codepoint: int, where: Mapping[int, tuple[list[str], list[str]]]) -> str:
    """
    The comment naming the productions a character comes from. The productions that define the character, or those that
    merely use it.
    """
    defining, using = where.get(codepoint, ([], []))
    return ", ".join(sorted(defining or using))


def _defines(out: io.StringIO, rows: Sequence[tuple[str, str, str]]) -> None:
    """Write `#define` lines. The names, the values and the comments line up into columns."""
    name_width = max(len(name) for name, _value, _comment in rows)
    value_width = max(len(value) for _name, value, _comment in rows)
    for name, value, comment in rows:
        line = f"#define {name:<{name_width}} {value:<{value_width}}"
        out.write(f"{line} // {comment}\n" if comment else f"{line.rstrip()}\n")


def _entries(out: io.StringIO, rows: Sequence[tuple[str, str]]) -> None:
    """Write the entries of an initializer, and align their comments into a column."""
    width = max(len(value) for value, _comment in rows)
    for value, comment in rows:
        out.write(f"    {value + ',':<{width + 1}} // {comment}\n")


# The whole header this generator owns. The header sits in the tree, and `make verify-decoder` fails where it differs
# from what the grammar says.
TABLES = os.path.join(gate.TREE, "src", "decoder_tables.h")


def emit(out: io.StringIO, model: chars.Model, grammar: Mapping[str, ir.Prod], keys: Mapping[str, int]) -> None:
    """
    Write the header.

    This file settles the layout rather than clang-format. The tables read as tables only when their columns line up,
    and the citations are too long to survive a reflow at the column limit. The header therefore holds a `clang-format
    off`, and this function emits the formatting clang-format would otherwise impose.
    """
    where = _sites(grammar)
    out.write("// SPDX-License-Identifier: MIT\n")
    out.write("// Generated by `generator/grammar2decoder.py` from `grammar/yeast-spec-1.2.yaml`. Do not edit.\n")
    out.write("// clang-format off\n")
    out.write("#ifndef YEAST_DECODER_TABLES_H\n")
    out.write("#define YEAST_DECODER_TABLES_H\n\n")
    out.write("#include <stdint.h>\n\n")

    # The layout is `chars`'. The sentence describing it is worked out from the same constants the macros below are.
    # Written out again here, it would say whatever it said when it was last read.
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
            (f"YS_SET_BIT_{_set_name(name)}", f"0x{model.set_mask(index):08X}u", _set_cite(name, grammar))
            for index, (name, _denotation) in enumerate(model.sets)
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
    _entries(out, [(f"YS_SET_BIT_{_set_name(name)}", _set_cite(name, grammar)) for name, _denotation in model.sets])
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
    # Build the whole header before writing any of it. A generator that fails partway then leaves the committed file as
    # it was, rather than truncated to whatever it had got to.
    source = io.StringIO()
    emit(source, model, grammar, check_groups(model, grammar))
    with open(TABLES, "w", encoding="utf-8") as handle:
        handle.write(source.getvalue())
    print(f"wrote {TABLES}: {len(model.literals)} literals, {len(model.sets)} sets")


if __name__ == "__main__":
    main()
