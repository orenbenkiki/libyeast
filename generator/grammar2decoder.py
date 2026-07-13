# SPDX-License-Identifier: MIT
"""Emit the decoder's tables from the grammar IR — the data half of the decoder.

The UTF-8 mechanics are fixed by RFC 3629 and live hand-written in `decoder.c`; only the classification is grammar-
derived, and all of it is here: the key of every ASCII character, the key of each character the grammar names, the bit
of each character set the grammar tests, and the few keys a non-ASCII character can take.

The key's layout is this file's to know. Anything reading or building a key does so through the macros emitted here.

Usage: `python3 generator/grammar2decoder.py > src/decoder_tables.h`
"""

import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chars  # noqa: E402
import ir  # noqa: E402
import annotated2ir  # noqa: E402

ASCII_LIMIT = 0x80
DELETE = 0x7F
NEL = 0x85
BOM = 0xFEFF
NONCHARACTERS = (0xFFFE, 0xFFFF)
SURROGATES = (0xD800, 0xDFFF)
C1_LIMIT = 0xA0  # the C1 controls run from the end of ASCII up to here
HEX_LETTERS = "ABCDEFabcdef"

# Unicode gives the control characters no name, only an alias. These four are the ones the grammar names.
CONTROL_NAMES = {
    0x09: "CHARACTER TABULATION",
    0x0A: "LINE FEED",
    0x0D: "CARRIAGE RETURN",
    NEL: "NEXT LINE",
}

# The keys a character that the grammar does not name can take. Each is a group of characters the grammar cannot tell
# apart; `check_groups` proves the grouping is exactly this, so a grammar change cannot quietly redefine a name.
ASCII_GROUPS = ("YS_KEY_CONTROL", "YS_KEY_DELETE", "YS_KEY_DIGIT", "YS_KEY_HEX_LETTER", "YS_KEY_LETTER", "YS_KEY_OTHER")

# The keys a valid non-ASCII character that the grammar does not name can take, the length bits excluded — a character
# of either kind may be two, three or four bytes long, so `decoder.c` ORs the length in. The C1 controls and the
# noncharacters share a key: to the grammar, both are merely JSON-compatible and not printable.
NON_ASCII_GROUPS = ("YS_KEY_NOT_PRINTABLE", "YS_KEY_CONTENT")


def ascii_group(codepoint):
    """The name of the key an unnamed ASCII character takes."""
    if codepoint < 0x20:
        return "YS_KEY_CONTROL"
    if codepoint == DELETE:
        return "YS_KEY_DELETE"
    if chr(codepoint).isdigit():
        return "YS_KEY_DIGIT"
    if chr(codepoint) in HEX_LETTERS:
        return "YS_KEY_HEX_LETTER"
    if chr(codepoint).isalpha():
        return "YS_KEY_LETTER"
    return "YS_KEY_OTHER"


def non_ascii_group(codepoint):
    """The name of the key an unnamed non-ASCII character takes."""
    if codepoint < C1_LIMIT or codepoint in NONCHARACTERS:
        return "YS_KEY_NOT_PRINTABLE"
    return "YS_KEY_CONTENT"


def check_groups(model, grammar):
    """The key of every character the grammar does not name, by group name.

    A group is a set of characters the grammar cannot tell apart, and `decoder.c` classifies non-ASCII characters by
    UTF-8 byte pattern, knowing nothing of the grammar. Both stay correct only while the grammar keeps grouping
    characters exactly this way. Were that ever to change, the ladder would misclassify in silence — so this fails
    generation instead. Checking one character per segment is exhaustive: a key cannot vary within a segment.
    """
    keys = {}
    for codepoint in range(ASCII_LIMIT):
        if codepoint not in model.literal_ids:
            record(keys, ascii_group(codepoint), model.key(codepoint, 1), codepoint)
    probes = [point for point in chars.representatives(grammar) if point >= ASCII_LIMIT]
    probes += list(range(ASCII_LIMIT, C1_LIMIT)) + list(NONCHARACTERS)
    for codepoint in probes:
        is_surrogate = SURROGATES[0] <= codepoint <= SURROGATES[1]
        if codepoint not in model.literal_ids and not is_surrogate:
            record(keys, non_ascii_group(codepoint), model.key(codepoint, 0), codepoint)
    return keys


def record(keys, group, key, codepoint):
    """Note that `codepoint` takes `key` as a member of `group`, failing if the group is not of one mind."""
    if keys.setdefault(group, key) != key:
        raise ValueError(
            f"U+{codepoint:04X} has key {key:#010x}, but {group} is {keys[group]:#010x}: the grammar no longer groups "
            f"characters the way the decoder assumes"
        )


def literal_name(codepoint):
    """The C identifier for a character the grammar names, from its Unicode name."""
    name = CONTROL_NAMES.get(codepoint) or unicodedata.name(chr(codepoint))
    return name.replace(" ", "_").replace("-", "_")


def set_name(name):
    """The C identifier for a character set: `ns-plain-safe-in` becomes `NS_PLAIN_SAFE_IN`."""
    return name.upper().replace("-", "_")


def set_cite(name, grammar):
    """The comment naming the production a character set comes from."""
    owner = name if name in grammar else name.rsplit("-inline-", 1)[0]
    return f"[{grammar[owner].number:03d}] {owner}"


def spelling(codepoint):
    """How to show a character in a comment: itself where that is legible, else its Unicode notation."""
    if 0x21 <= codepoint <= 0x7E and codepoint not in (ord("'"), ord("\\")):
        return f"'{chr(codepoint)}'"
    return f"U+{codepoint:04X}"


def defined(body):
    """The character a production defines outright, or None — looking through any token annotation that wraps it."""
    while isinstance(body, (ir.Token, ir.Wrap)):
        body = body.item
    return body.cp if isinstance(body, ir.Char) else None


def sites(grammar):
    """For each character the grammar names, where it is named: `{codepoint: ([defining], [using])}` production texts.

    A production defines a character when its whole body is that character, however the production annotates it; the
    characters no production defines are written inline, inside `ns-uri-char` and its like, so those are cited by where
    they appear instead.
    """
    found = {}
    for name, production in grammar.items():
        cited = f"[{production.number:03d}] {name}"
        codepoint = defined(production.body)
        if codepoint is not None:
            found.setdefault(codepoint, ([], []))[0].append(cited)
            continue
        pending, seen = [production.body], set()
        while pending:
            node = pending.pop()
            if isinstance(node, ir.Char) and node.cp not in seen:
                seen.add(node.cp)
                found.setdefault(node.cp, ([], []))[1].append(cited)
            pending.extend(chars.children(node))
    return found


def cite(codepoint, where):
    """The comment naming the productions a character comes from: those defining it, or those merely using it."""
    defining, using = where.get(codepoint, ([], []))
    return ", ".join(sorted(defining or using))


def defines(out, rows):
    """Write `#define` lines, their values and their comments each aligned into a column."""
    name_width = max(len(name) for name, _value, _comment in rows)
    value_width = max(len(value) for _name, value, _comment in rows)
    for name, value, comment in rows:
        line = f"#define {name:<{name_width}} {value:<{value_width}}"
        out.write(f"{line} // {comment}\n" if comment else f"{line.rstrip()}\n")


def entries(out, rows):
    """Write the entries of an initializer, their comments aligned into a column."""
    width = max(len(value) for value, _comment in rows)
    for value, comment in rows:
        out.write(f"    {value + ',':<{width + 1}} // {comment}\n")


def emit(out, model, grammar, keys):
    """Write the header.

    Its layout is this file's, not clang-format's: the tables read as tables only when their columns line up, and the
    citations are too long to survive a 120-column reflow. Hence the `clang-format off` — which obliges us to emit
    formatting the project would otherwise have imposed.
    """
    where = sites(grammar)
    out.write("// SPDX-License-Identifier: MIT\n")
    out.write("// Generated by generator/grammar2decoder.py from the vendored yaml-grammar. Do not edit.\n")
    out.write("// clang-format off\n")
    out.write("#ifndef YEAST_DECODER_TABLES_H\n")
    out.write("#define YEAST_DECODER_TABLES_H\n\n")
    out.write("#include <stdint.h>\n\n")

    out.write("// A character's key holds the id of the character the grammar names in bits 0..5, one bit per\n")
    out.write("// character set the grammar tests in bits 6..24, and the bytes the character consumed in bits\n")
    out.write("// 25..27. Only these two macros know that.\n")
    defines(
        out,
        [
            ("YS_LEN(key)", f"(((key) >> {chars.LEN_SHIFT}) & 0x7u)", "the bytes a character consumed"),
            ("YS_LENGTH_BITS(length)", f"((uint32_t)(length) << {chars.LEN_SHIFT})", "how the decoder writes them"),
        ],
    )

    out.write("\n// The key of each character the grammar names. A character's key is fixed — both its sets and its\n")
    out.write("// length are — so testing for one is a single comparison.\n")
    defines(
        out,
        [
            (
                f"YS_LIT_KEY_{literal_name(codepoint)}",
                f"0x{model.key(codepoint, utf8_length(codepoint)):08X}u",
                f"{spelling(codepoint) + ',':<7} {cite(codepoint, where)}",
            )
            for codepoint in model.literals
        ],
    )

    out.write("\n// The sentinels: an id and no set bits, so every membership test fails at them.\n")
    defines(
        out,
        [
            ("YS_LIT_KEY_EOF", f"0x{model.sentinel(model.lit_eof, 0):08X}u", "the window is empty"),
            ("YS_LIT_KEY_INVALID", f"0x{model.sentinel(model.lit_invalid, 1):08X}u", "the bytes are not UTF-8"),
        ],
    )

    out.write("\n// One bit per character set the grammar tests. The unions and subtractions are already evaluated.\n")
    defines(
        out,
        [
            (f"YS_SET_BIT_{set_name(name)}", f"0x{model.set_mask(index):08X}u", set_cite(name, grammar))
            for index, (name, _denotation) in enumerate(model.sets)
        ],
    )

    out.write("\n// The character sets by id, for ys_scan_set().\n")
    out.write("typedef enum ys_set_id {\n")
    for name, _denotation in model.sets:
        out.write(f"    YS_SET_ID_{set_name(name)},\n")
    out.write("    YS_SET_ID_COUNT\n")
    out.write("} ys_set_id;\n\n")

    out.write("// The bit of each character set, indexed by its id.\n")
    out.write("static const uint32_t YS_SET_BITS[YS_SET_ID_COUNT] = {\n")
    entries(out, [(f"YS_SET_BIT_{set_name(name)}", set_cite(name, grammar)) for name, _denotation in model.sets])
    out.write("};\n\n")

    out.write("// The key of an ASCII character the grammar does not name — one per group of characters that the\n")
    out.write("// grammar cannot tell apart.\n")
    defines(
        out,
        [
            ("YS_KEY_CONTROL", f"0x{keys['YS_KEY_CONTROL']:08X}u", "a C0 control, which belongs to no set at all"),
            ("YS_KEY_DELETE", f"0x{keys['YS_KEY_DELETE']:08X}u", "U+007F, JSON-compatible but not printable"),
            ("YS_KEY_DIGIT", f"0x{keys['YS_KEY_DIGIT']:08X}u", "'1'..'9' ('0' the grammar names)"),
            ("YS_KEY_HEX_LETTER", f"0x{keys['YS_KEY_HEX_LETTER']:08X}u", "a letter that is also a hexadecimal digit"),
            ("YS_KEY_LETTER", f"0x{keys['YS_KEY_LETTER']:08X}u", "any other letter"),
            ("YS_KEY_OTHER", f"0x{keys['YS_KEY_OTHER']:08X}u", "printable, and in none of the grammar's classes"),
        ],
    )

    out.write("\n// The key of a valid non-ASCII character the grammar does not name, its length bits excluded: such\n")
    out.write("// a character may be two, three or four bytes long, so decoder.c ORs its length in.\n")
    defines(
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

    out.write("\n// The key of every ASCII character. Index it with the byte.\n")
    out.write(f"static const uint32_t YS_ASCII[{ASCII_LIMIT}] = {{\n")
    entries(
        out,
        [
            (
                f"YS_LIT_KEY_{literal_name(codepoint)}" if codepoint in model.literal_ids else ascii_group(codepoint),
                spelling(codepoint),
            )
            for codepoint in range(ASCII_LIMIT)
        ],
    )
    out.write("};\n\n")
    out.write("#endif // YEAST_DECODER_TABLES_H\n")
    out.write("// clang-format on\n")


def utf8_length(codepoint):
    """The number of bytes UTF-8 uses to encode `codepoint`."""
    if codepoint < 0x80:
        return 1
    if codepoint < 0x800:
        return 2
    if codepoint < 0x10000:
        return 3
    return 4


def main():
    grammar = annotated2ir.load()
    model = chars.Model(grammar)
    emit(sys.stdout, model, grammar, check_groups(model, grammar))


if __name__ == "__main__":
    main()
