// SPDX-License-Identifier: MIT
// The decoder's tests cover what a character classifies as and what a consume takes.

#include "acutest.h"
#include "decoder.h"
#include <ctype.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// An empty window is the end of the input. The key has no set bits, and a membership test fails there.
static void test_end_of_input(void) {
    ys_char character = ys_next_char((const uint8_t *)"", 0);
    TEST_CHECK(character == YS_LIT_KEY_EOF);
    TEST_CHECK(YS_LEN(character) == 0);
    TEST_CHECK((character & YS_SET_BIT_NS_CHAR) == 0);
    TEST_CHECK((character & YS_SET_BIT_C_PRINTABLE) == 0);
}

// The decoder reads an ASCII character with a single table lookup. The lookup yields a key, the sets of the character,
// and a length of `1`. The digit `7` yields `YS_KEY_DIGIT`.
static void test_ascii_character(void) {
    ys_char dash = ys_next_char((const uint8_t *)"-x", 2);
    TEST_CHECK(dash == YS_LIT_KEY_HYPHEN_MINUS);
    TEST_CHECK(YS_LEN(dash) == 1);
    TEST_CHECK((dash & YS_SET_BIT_C_INDICATOR) != 0);
    TEST_CHECK((dash & YS_SET_BIT_NS_CHAR) != 0);
    TEST_CHECK((dash & YS_SET_BIT_C_FLOW_INDICATOR) == 0);

    ys_char space = ys_next_char((const uint8_t *)" ", 1);
    TEST_CHECK(space == YS_LIT_KEY_SPACE);
    TEST_CHECK((space & YS_SET_BIT_S_WHITE) != 0);
    TEST_CHECK((space & YS_SET_BIT_NS_CHAR) == 0); // `ns-char` takes `s-white` out of `nb-char`. `nb-char` takes line
                                                   // breaks and the byte order mark out of the printable characters.

    ys_char seven = ys_next_char((const uint8_t *)"7", 1);
    TEST_CHECK(seven == YS_KEY_DIGIT); // The grammar names `0` as a literal. `7` takes the shared digit key.
    TEST_CHECK((seven & YS_SET_BIT_NS_DEC_DIGIT) != 0);
    TEST_CHECK((seven & YS_SET_BIT_NS_HEX_DIGIT) != 0);

    ys_char nul = ys_next_char((const uint8_t *)"\0", 1);
    TEST_CHECK(nul == YS_KEY_CONTROL);
    TEST_CHECK((nul & YS_SET_BIT_C_PRINTABLE) ==
               0); // In `src/decoder_tables.h`, a control character the grammar does not name sits in `nb-unparsed`.
    TEST_CHECK(YS_LEN(nul) == 1); // A length of `1` sets the NUL apart from the end of the input.
}

// Above ASCII, `src/decoder_tables.h` names the next-line character and the byte-order mark.
static void test_named_non_ascii(void) {
    ys_char next_line = ys_next_char((const uint8_t *)"\xC2\x85", 2);
    TEST_CHECK(next_line == YS_LIT_KEY_NEXT_LINE);
    TEST_CHECK(YS_LEN(next_line) == 2);
    TEST_CHECK((next_line & YS_SET_BIT_C_PRINTABLE) != 0);

    ys_char byte_order_mark = ys_next_char((const uint8_t *)"\xEF\xBB\xBF", 3);
    TEST_CHECK(byte_order_mark == YS_LIT_KEY_ZERO_WIDTH_NO_BREAK_SPACE);
    TEST_CHECK(YS_LEN(byte_order_mark) == 3);
    TEST_CHECK((byte_order_mark & YS_SET_BIT_NB_CHAR) == 0); // The byte-order mark lies outside nb-char.
}

// The test feeds ordinary content above ASCII. A single key covers such a character at any length. The grammar cannot
// tell such characters apart, and `check_groups` holds it to that.
static void test_content_non_ascii(void) {
    ys_char latin = ys_next_char((const uint8_t *)"\xC3\xA9", 2);
    ys_char cjk = ys_next_char((const uint8_t *)"\xE4\xB8\x80", 3);
    ys_char emoji = ys_next_char((const uint8_t *)"\xF0\x9F\x98\x80", 4);
    ys_char characters[3] = {latin, cjk, emoji};

    TEST_CHECK(YS_LEN(latin) == 2);
    TEST_CHECK(YS_LEN(cjk) == 3);
    TEST_CHECK(YS_LEN(emoji) == 4);
    for (size_t index = 0; index < 3; index++) {
        TEST_CHECK(characters[index] == (YS_KEY_CONTENT | YS_LENGTH_BITS(YS_LEN(characters[index]))));
        TEST_CHECK((characters[index] & YS_SET_BIT_C_PRINTABLE) != 0);
        TEST_CHECK((characters[index] & YS_SET_BIT_NS_CHAR) != 0);
    }
}

// A C1 control is JSON-compatible. A C1 control is not printable. This test checks a noncharacter as well.
static void test_not_printable_non_ascii(void) {
    ys_char control = ys_next_char((const uint8_t *)"\xC2\x80", 2);
    ys_char noncharacter = ys_next_char((const uint8_t *)"\xEF\xBF\xBE", 3);

    TEST_CHECK(control == (YS_KEY_NOT_PRINTABLE | YS_LENGTH_BITS(2)));
    TEST_CHECK(noncharacter == (YS_KEY_NOT_PRINTABLE | YS_LENGTH_BITS(3)));
    TEST_CHECK((control & YS_SET_BIT_NB_JSON) != 0);
    TEST_CHECK((control & YS_SET_BIT_C_PRINTABLE) == 0);
    TEST_CHECK((noncharacter & YS_SET_BIT_NB_JSON) != 0);
    TEST_CHECK((noncharacter & YS_SET_BIT_C_PRINTABLE) == 0);
}

// The decoder rejects a truncated four-byte sequence and a bad second or third continuation byte, and
// consumes a single byte.
static void test_malformed_utf8(void) {
    static const struct {
        const char *name;
        const char *bytes;
        size_t size;
    } cases[] = {
        {"a truncated four-byte sequence", "\xF0\x9F\x98", 3},
        {"a bad second continuation byte", "\xE4\xB8\x20", 3},
        {"a bad third continuation byte", "\xF0\x9F\x98\x20", 4},
    };

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        ys_char character = ys_next_char((const uint8_t *)cases[index].bytes, cases[index].size);
        TEST_CHECK(character == YS_LIT_KEY_INVALID);
        TEST_MSG("%s: key 0x%08X", cases[index].name, (unsigned)character);
        TEST_CHECK(YS_LEN(character) == 1);
        TEST_CHECK((character & YS_SET_BIT_C_PRINTABLE) == 0);

        // `ys_utf8_length` answers `0` for the truncated sequence and for the bad second and third continuation bytes.
        TEST_CHECK(ys_utf8_length((const uint8_t *)cases[index].bytes, cases[index].size) == 0);
        TEST_MSG("%s: `ys_utf8_length` called it well-formed", cases[index].name);
    }
}

// Whether the decoder refuses the case `name` of `third_party/utf8-stress-test/UTF-8-test.txt`. The file titles its
// sections. The decoder refuses the malformed sequences and the
// overlong sequences. It refuses the UTF-16 surrogates, lone and paired. Among the boundary cases, the decoder refuses
// a form longer than UTF-8 allows. It refuses a code point past U+10FFFF.
static bool is_refused(const char *name) {
    static const char *const refused_in_section_2[] = {"2.1.5", "2.1.6", "2.2.4", "2.2.5", "2.2.6", "2.3.5"};
    if (name[0] == '3' || name[0] == '4' || strncmp(name, "5.1.", 4) == 0 || strncmp(name, "5.2.", 4) == 0) {
        return true;
    }
    for (size_t index = 0; index < sizeof(refused_in_section_2) / sizeof(refused_in_section_2[0]); index++) {
        if (strcmp(name, refused_in_section_2[index]) == 0) {
            return true;
        }
    }
    return false;
}

// The test reads the cases of Markus Kuhn's UTF-8 decoder stress test from
// `third_party/utf8-stress-test/UTF-8-test.txt`. `CMakeLists.txt` names that file in `YS_UTF8_STRESS_TEST`. The file
// comes from https://www.cl.cam.ac.uk/~mgk25/ucs/examples/UTF-8-test.txt. The number of a case opens a
// line. The bytes of the case sit between the next pair of quotes. The test walks a case with `ys_utf8_length`. At a
// position `ys_utf8_length` refuses, `ys_next_char` gives a one-byte `YS_LIT_KEY_INVALID`. At any other position,
// `ys_next_char` gives a character of the length `ys_utf8_length` gives. A refused case holds a position both refuse,
// and `is_refused` names such a case. The count of cases guards the parse.
static void test_stress_test(void) {
    static uint8_t text[32768];
    FILE *file = fopen(YS_UTF8_STRESS_TEST, "rb");
    TEST_ASSERT_(file != NULL, "cannot open %s", YS_UTF8_STRESS_TEST);
    if (file == NULL) {
        return; // UNTESTED
    }
    size_t size = fread(text, 1, sizeof(text), file);
    TEST_CHECK(fclose(file) == 0);
    TEST_ASSERT(size > 0 && size < sizeof(text));

    const uint8_t *end = text + size;
    size_t cases = 0;
    for (const uint8_t *line = text; line < end;) {
        char name[16];
        size_t named = 0;
        int dots = 0;
        while (line + named < end && named + 1 < sizeof(name) && (isdigit(line[named]) || line[named] == '.')) {
            dots += line[named] == '.';
            name[named] = (char)line[named];
            named++;
        }
        name[named] = '\0';
        const uint8_t *next = memchr(line, '\n', (size_t)(end - line));
        if (dots != 2) {
            line = next != NULL ? next + 1 : end;
            continue;
        }
        const uint8_t *open = memchr(line, '"', (size_t)(end - line));
        TEST_ASSERT_(open != NULL, "case %s opens no quote", name);
        const uint8_t *close = memchr(open + 1, '"', (size_t)(end - open - 1));
        TEST_ASSERT_(close != NULL, "case %s closes no quote", name);

        bool is_refused_here = false;
        const uint8_t *bytes = open + 1;
        size_t length = (size_t)(close - bytes);
        for (size_t at = 0; at < length;) {
            size_t measured = ys_utf8_length(bytes + at, length - at);
            ys_char character = ys_next_char(bytes + at, length - at);
            if (measured == 0) {
                TEST_CHECK(character == YS_LIT_KEY_INVALID && YS_LEN(character) == 1);
                TEST_MSG("case %s at byte %zu: key 0x%08X", name, at, (unsigned)character);
                is_refused_here = true;
                at += 1;
            } else {
                TEST_CHECK(character != YS_LIT_KEY_INVALID && YS_LEN(character) == measured);
                TEST_MSG("case %s at byte %zu: key 0x%08X, length %zu", name, at, (unsigned)character, measured);
                at += measured;
            }
        }
        TEST_CHECK(is_refused_here == is_refused(name));
        TEST_MSG("case %s: the decoder %s it", name, is_refused_here ? "refused" : "accepted");
        cases++;

        const uint8_t *after = memchr(close, '\n', (size_t)(end - close));
        line = after != NULL ? after + 1 : end;
    }
    TEST_CHECK(cases == 78);
    TEST_MSG("the stress test held %zu case(s)", cases);
}

// `ys_codepoint` hands `ys_utf8_length` the bytes `ys_wire_write` takes in. Those bytes reach `ys_utf8_length`
// unclassified. `ys_utf8_length` agrees with `ys_next_char` on the ill-formed shapes above. This test measures
// well-formed sequences and an empty window. `ys_next_char` reads the end of the input as a character. A length of no
// bytes is no sequence.
static void test_utf8_length(void) {
    static const struct {
        const char *name;
        const char *bytes;
        size_t size;
        size_t length;
    } cases[] = {
        {"no bytes at all", "", 0, 0},
        {"ASCII", "a", 1, 1},
        {"the nul byte", "\0", 1, 1},
        {"two bytes", "\xC3\xA9", 2, 2},
        {"three bytes", "\xE4\xB8\x80", 3, 3},
        {"four bytes", "\xF0\x9F\x98\x80", 4, 4},
        {"a sequence and what follows", "\xC3\xA9zz", 4, 2}, // The length counts the first sequence.
    };

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        size_t length = ys_utf8_length((const uint8_t *)cases[index].bytes, cases[index].size);
        TEST_CHECK(length == cases[index].length);
        TEST_MSG("%s: got %zu, wanted %zu", cases[index].name, length, cases[index].length);
    }
}

// The decoder reports the length of the encoding for a codepoint UTF-8 can encode. It rejects a surrogate. The encoder
// here goes independently of the decoder. That independence makes the test a check rather than a tautology.
//
// The sweep holds the first codepoint to disagree and reports it at the end rather than at once. The sweep then has no
// failure-only branch.
static void test_every_codepoint(void) {
    uint32_t first_wrong = 0; // The sweep starts above ASCII, and this initial value means none.
    ys_char wrong_key = 0;

    for (uint32_t codepoint = 0x80u; codepoint <= 0x10FFFFu; codepoint++) {
        uint8_t bytes[4];
        size_t size;
        if (codepoint < 0x800u) {
            bytes[0] = (uint8_t)(0xC0u | (codepoint >> 6));
            bytes[1] = (uint8_t)(0x80u | (codepoint & 0x3Fu));
            size = 2;
        } else if (codepoint < 0x10000u) {
            bytes[0] = (uint8_t)(0xE0u | (codepoint >> 12));
            bytes[1] = (uint8_t)(0x80u | ((codepoint >> 6) & 0x3Fu));
            bytes[2] = (uint8_t)(0x80u | (codepoint & 0x3Fu));
            size = 3;
        } else {
            bytes[0] = (uint8_t)(0xF0u | (codepoint >> 18));
            bytes[1] = (uint8_t)(0x80u | ((codepoint >> 12) & 0x3Fu));
            bytes[2] = (uint8_t)(0x80u | ((codepoint >> 6) & 0x3Fu));
            bytes[3] = (uint8_t)(0x80u | (codepoint & 0x3Fu));
            size = 4;
        }

        ys_char character = ys_next_char(bytes, size);
        bool is_surrogate = codepoint >= 0xD800u && codepoint <= 0xDFFFu;
        bool is_right = is_surrogate ? character == YS_LIT_KEY_INVALID : (size_t)YS_LEN(character) == size;
        wrong_key = (first_wrong == 0 && !is_right) ? character : wrong_key;
        first_wrong = (first_wrong == 0 && !is_right) ? codepoint : first_wrong;
    }

    TEST_CHECK(first_wrong == 0);
    TEST_MSG("U+%04X: key 0x%08X, length %u", first_wrong, (unsigned)wrong_key, (unsigned)YS_LEN(wrong_key));
}

// Consume a run of characters in a character set.
static void test_consume_set(void) {
    ys_consumed spaces = ys_consume_set((const uint8_t *)"    x", 5, YS_SET_ID_S_WHITE);
    TEST_CHECK(spaces.bytes == 4 && spaces.characters == 4);

    const uint8_t *digits = (const uint8_t *)"1234abc";
    ys_consumed taken = ys_consume_set(digits, 7, YS_SET_ID_NS_DEC_DIGIT);
    TEST_CHECK(taken.bytes == 4 && taken.characters == 4);

    // A consume takes nothing where the first character is not in the set.
    taken = ys_consume_set(digits, 7, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.bytes == 0 && taken.characters == 0);

    // A consume stops at the end of the window.
    taken = ys_consume_set(digits, 2, YS_SET_ID_NS_DEC_DIGIT);
    TEST_CHECK(taken.bytes == 2 && taken.characters == 2);
    taken = ys_consume_set(digits, 0, YS_SET_ID_NS_DEC_DIGIT);
    TEST_CHECK(taken.bytes == 0 && taken.characters == 0);

    // A consume runs through non-ASCII content, where the bytes outnumber the characters. The space stops it.
    taken = ys_consume_set((const uint8_t *)"ab\xE4\xB8\x80 z", 7, YS_SET_ID_NS_CHAR);
    TEST_CHECK(taken.bytes == 5 && taken.characters == 3);

    // Bytes that are not UTF-8 belong to no set. Such bytes end a consume.
    taken = ys_consume_set((const uint8_t *)"ab\xFF", 3, YS_SET_ID_NS_CHAR);
    TEST_CHECK(taken.bytes == 2 && taken.characters == 2);
}

// A trimmed consume splits the take in two. The span it keeps comes first. The trailing run given back comes after.
static void test_consume_trim_sets(void) {
    // A consume keeps its inner spaces and gives back the trailing ones.
    ys_trim taken = ys_consume_trim_sets((const uint8_t *)"a b  ", 5, YS_SET_ID_NB_CHAR, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.span.bytes == 3 && taken.trim.bytes == 2);

    // A line of spaces keeps an empty span. The whole take is trim.
    taken = ys_consume_trim_sets((const uint8_t *)"    ", 4, YS_SET_ID_NB_CHAR, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.span.bytes == 0 && taken.trim.bytes == 4);

    // The trim stays empty where the take ends on no white space. `nb-char` stops at the break.
    taken = ys_consume_trim_sets((const uint8_t *)"abc\n", 4, YS_SET_ID_NB_CHAR, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.span.bytes == 3 && taken.trim.bytes == 0);

    // The span counts characters as well as bytes. The kept span ends after a non-ASCII character.
    taken = ys_consume_trim_sets((const uint8_t *)"a\xE4\xB8\x80  ", 6, YS_SET_ID_NB_CHAR, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.span.bytes == 4 && taken.span.characters == 2 && taken.trim.bytes == 2 &&
               taken.trim.characters == 2);

    // A byte that is not UTF-8 ends the consume.
    taken = ys_consume_trim_sets((const uint8_t *)"ab\xFF", 3, YS_SET_ID_NB_CHAR, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.span.bytes == 2 && taken.trim.bytes == 0);
}

TEST_LIST = {
    {"end_of_input", test_end_of_input},
    {"ascii_character", test_ascii_character},
    {"named_non_ascii", test_named_non_ascii},
    {"content_non_ascii", test_content_non_ascii},
    {"not_printable_non_ascii", test_not_printable_non_ascii},
    {"malformed_utf8", test_malformed_utf8},
    {"stress_test", test_stress_test},
    {"utf8_length", test_utf8_length},
    {"every_codepoint", test_every_codepoint},
    {"consume_set", test_consume_set},
    {"consume_trim_sets", test_consume_trim_sets},
    {NULL, NULL},
};
