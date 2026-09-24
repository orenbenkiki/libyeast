// SPDX-License-Identifier: MIT
// The decoder's tests, over what a character classifies as and what a consume takes. These reach the private header
// that the library's public tests cannot.

#include "acutest.h"
#include "decoder.h"
#include <stdbool.h>
#include <stdint.h>

// An empty window is the end of the input. The key has no set bits, and a membership test fails there.
// A test site in the parser needs no end-of-input case of its own.
static void test_end_of_input(void) {
    ys_char character = ys_next_char((const uint8_t *)"", 0);
    TEST_CHECK(character == YS_LIT_KEY_EOF);
    TEST_CHECK(YS_LEN(character) == 0);
    TEST_CHECK((character & YS_SET_BIT_NS_CHAR) == 0);
    TEST_CHECK((character & YS_SET_BIT_C_PRINTABLE) == 0);
}

// An ASCII character is a single table lookup. The character the grammar names, the sets it is in, and a length of `1`.
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
    TEST_CHECK((space & YS_SET_BIT_NS_CHAR) == 0); // ns-char holds the printable characters bar white space

    ys_char seven = ys_next_char((const uint8_t *)"7", 1);
    TEST_CHECK(seven == YS_KEY_DIGIT); // the grammar names '0' but no other digit
    TEST_CHECK((seven & YS_SET_BIT_NS_DEC_DIGIT) != 0);
    TEST_CHECK((seven & YS_SET_BIT_NS_HEX_DIGIT) != 0);

    ys_char nul = ys_next_char((const uint8_t *)"\0", 1);
    TEST_CHECK(nul == YS_KEY_CONTROL);
    TEST_CHECK((nul & YS_SET_BIT_C_PRINTABLE) == 0); // a control character is in no set at all
    TEST_CHECK(YS_LEN(nul) == 1);                    // but its length still tells it from the end of the input
}

// The pair of characters the grammar names above ASCII.
static void test_named_non_ascii(void) {
    ys_char next_line = ys_next_char((const uint8_t *)"\xC2\x85", 2);
    TEST_CHECK(next_line == YS_LIT_KEY_NEXT_LINE);
    TEST_CHECK(YS_LEN(next_line) == 2);
    TEST_CHECK((next_line & YS_SET_BIT_C_PRINTABLE) != 0);

    ys_char byte_order_mark = ys_next_char((const uint8_t *)"\xEF\xBB\xBF", 3);
    TEST_CHECK(byte_order_mark == YS_LIT_KEY_ZERO_WIDTH_NO_BREAK_SPACE);
    TEST_CHECK(YS_LEN(byte_order_mark) == 3);
    TEST_CHECK((byte_order_mark & YS_SET_BIT_NB_CHAR) == 0); // nb-char withholds the byte-order mark
}

// Ordinary content above ASCII. A single key covers such a character at any length. The grammar cannot tell such
// characters apart.
static void test_content_non_ascii(void) {
    ys_char latin = ys_next_char((const uint8_t *)"\xC3\xA9", 2);         // U+00E9
    ys_char cjk = ys_next_char((const uint8_t *)"\xE4\xB8\x80", 3);       // U+4E00
    ys_char emoji = ys_next_char((const uint8_t *)"\xF0\x9F\x98\x80", 4); // U+1F600
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

// A C1 control is JSON-compatible. A C1 control is neither printable nor content. This test checks a pair of
// noncharacters as well.
static void test_not_printable_non_ascii(void) {
    ys_char control = ys_next_char((const uint8_t *)"\xC2\x80", 2);          // U+0080
    ys_char noncharacter = ys_next_char((const uint8_t *)"\xEF\xBF\xBE", 3); // U+FFFE

    TEST_CHECK(control == (YS_KEY_NOT_PRINTABLE | YS_LENGTH_BITS(2)));
    TEST_CHECK(noncharacter == (YS_KEY_NOT_PRINTABLE | YS_LENGTH_BITS(3)));
    TEST_CHECK((control & YS_SET_BIT_NB_JSON) != 0);
    TEST_CHECK((control & YS_SET_BIT_C_PRINTABLE) == 0);
    TEST_CHECK((noncharacter & YS_SET_BIT_NB_JSON) != 0);
    TEST_CHECK((noncharacter & YS_SET_BIT_C_PRINTABLE) == 0);
}

// Malformed UTF-8. The cases come from Markus Kuhn's decoder stress test. The decoder rejects a case, and consumes a
// single byte. A caller can then step over it and continue reporting rather than stopping at the first bad byte.
static void test_malformed_utf8(void) {
    static const struct {
        const char *name;
        const char *bytes;
        size_t size;
    } cases[] = {
        {"a lone continuation byte", "\x80", 1},
        {"a lone continuation byte, high", "\xBF", 1},
        {"an overlong two-byte solidus", "\xC0\xAF", 2},
        {"an overlong two-byte nul", "\xC1\x80", 2},
        {"an overlong three-byte sequence", "\xE0\x80\xAF", 3},
        {"an overlong four-byte sequence", "\xF0\x80\x80\xAF", 4},
        {"the surrogate U+D800", "\xED\xA0\x80", 3},
        {"the surrogate U+DFFF", "\xED\xBF\xBF", 3},
        {"a codepoint beyond U+10FFFF", "\xF4\x90\x80\x80", 4},
        {"the lead byte 0xF5", "\xF5\x80\x80\x80", 4},
        {"the lead byte 0xFE", "\xFE", 1},
        {"the lead byte 0xFF", "\xFF", 1},
        {"a truncated two-byte sequence", "\xC3", 1},
        {"a truncated three-byte sequence", "\xE4\xB8", 2},
        {"a truncated four-byte sequence", "\xF0\x9F\x98", 3},
        {"a bad first continuation byte", "\xE4\x20\x80", 3},
        {"a bad second continuation byte", "\xE4\xB8\x20", 3},
        {"a bad third continuation byte", "\xF0\x9F\x98\x20", 4},
    };

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        ys_char character = ys_next_char((const uint8_t *)cases[index].bytes, cases[index].size);
        TEST_CHECK(character == YS_LIT_KEY_INVALID);
        TEST_MSG("%s: key 0x%08X", cases[index].name, (unsigned)character);
        TEST_CHECK(YS_LEN(character) == 1);
        TEST_CHECK((character & YS_SET_BIT_C_PRINTABLE) == 0);

        // The same question, asked of the one caller that has bytes nothing has classified yet.
        TEST_CHECK(ys_utf8_length((const uint8_t *)cases[index].bytes, cases[index].size) == 0);
        TEST_MSG("%s: `ys_utf8_length` called it well-formed", cases[index].name);
    }
}

// `ys_utf8_length` measures the bytes the wire format takes in. Those bytes reach it unclassified. `ys_utf8_length`
// agrees with `ys_next_char` on the ill-formed shapes above. This test runs the call on a well-formed shape, and on the
// empty window that `ys_next_char` rejects. `ys_next_char` reads the end of the input as a character. A length of no
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
        {"two bytes", "\xC3\xA9", 2, 2},                     // U+00E9
        {"three bytes", "\xE4\xB8\x80", 3, 3},               // U+4E00
        {"four bytes", "\xF0\x9F\x98\x80", 4, 4},            // U+1F600
        {"a sequence and what follows", "\xC3\xA9zz", 4, 2}, // it measures the first, not the window
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
// The sweep holds the first codepoint to disagree and reports it at the end rather than at once. The sweep then
// has no failure-only branch. A passing run covers no such branch.
static void test_every_codepoint(void) {
    uint32_t first_wrong = 0; // no codepoint below U+0080 is swept. Zero can mean "none"
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

// Consume a set for a (***) or a (+++) over a character set.
static void test_consume_set(void) {
    ys_consumed spaces = ys_consume_set((const uint8_t *)"    x", 5, YS_SET_ID_S_WHITE);
    TEST_CHECK(spaces.bytes == 4 && spaces.characters == 4);

    const uint8_t *digits = (const uint8_t *)"1234abc";
    ys_consumed taken = ys_consume_set(digits, 7, YS_SET_ID_NS_DEC_DIGIT);
    TEST_CHECK(taken.bytes == 4 && taken.characters == 4);

    // A consume of no length, when the very first character is not in the set.
    taken = ys_consume_set(digits, 7, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.bytes == 0 && taken.characters == 0);

    // A consume stops at the end of the window, never past it.
    taken = ys_consume_set(digits, 2, YS_SET_ID_NS_DEC_DIGIT);
    TEST_CHECK(taken.bytes == 2 && taken.characters == 2);
    taken = ys_consume_set(digits, 0, YS_SET_ID_NS_DEC_DIGIT);
    TEST_CHECK(taken.bytes == 0 && taken.characters == 0);

    // A consume continues through non-ASCII content, where the bytes outnumber the characters. Here "ab" and U+4E00,
    // which is three bytes, stopping at the space.
    taken = ys_consume_set((const uint8_t *)"ab\xE4\xB8\x80 z", 7, YS_SET_ID_NS_CHAR);
    TEST_CHECK(taken.bytes == 5 && taken.characters == 3);

    // Bytes that are not UTF-8 are in no set. They end a consume rather than being consumed by it.
    taken = ys_consume_set((const uint8_t *)"ab\xFF", 3, YS_SET_ID_NS_CHAR);
    TEST_CHECK(taken.bytes == 2 && taken.characters == 2);
}

// A trimmed consume splits the take in half. The span it keeps comes first. The trailing run given back comes after.
static void test_consume_trim_sets(void) {
    // A consume keeps its inner spaces and gives back the trailing ones. The span is "a b", the trim the two spaces
    // after.
    ys_trim taken = ys_consume_trim_sets((const uint8_t *)"a b  ", 5, YS_SET_ID_NB_CHAR, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.span.bytes == 3 && taken.trim.bytes == 2);

    // Nothing but the given-back kind. The kept span is empty and the whole take is trim. A line of only spaces comes
    // to that.
    taken = ys_consume_trim_sets((const uint8_t *)"    ", 4, YS_SET_ID_NB_CHAR, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.span.bytes == 0 && taken.trim.bytes == 4);

    // No trailing given-back characters. The span is the whole of what was taken, the trim empty. nb-char stops at
    // the break.
    taken = ys_consume_trim_sets((const uint8_t *)"abc\n", 4, YS_SET_ID_NB_CHAR, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.span.bytes == 3 && taken.trim.bytes == 0);

    // The span is counted in characters too, stopping after a non-ASCII kept character. Here "a" then U+4E00 (three
    // bytes), then two given-back spaces.
    taken = ys_consume_trim_sets((const uint8_t *)"a\xE4\xB8\x80  ", 6, YS_SET_ID_NB_CHAR, YS_SET_ID_S_WHITE);
    TEST_CHECK(taken.span.bytes == 4 && taken.span.characters == 2 && taken.trim.bytes == 2 &&
               taken.trim.characters == 2);

    // A non-ASCII byte in no set ends the consume as an ASCII one out of set does. Here "ab" then a byte that is not
    // UTF-8.
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
    {"utf8_length", test_utf8_length},
    {"every_codepoint", test_every_codepoint},
    {"consume_set", test_consume_set},
    {"consume_trim_sets", test_consume_trim_sets},
    {NULL, NULL},
};
