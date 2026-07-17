// SPDX-License-Identifier: MIT
#include "acutest.h"
#include "decoder.h"
#include <stdbool.h>
#include <stdint.h>

// An empty window is the end of the input: it carries no set bits, so every membership test fails at it of its own
// accord, and no test site in the parser needs an end-of-input case of its own.
static void test_end_of_input(void) {
    ys_char character = ys_next_char((const uint8_t *)"", 0);
    TEST_CHECK(character == YS_LIT_KEY_EOF);
    TEST_CHECK(YS_LEN(character) == 0);
    TEST_CHECK((character & YS_SET_BIT_NS_CHAR) == 0);
    TEST_CHECK((character & YS_SET_BIT_C_PRINTABLE) == 0);
}

// An ASCII character is one table lookup: the character the grammar names, the sets it is in, and a length of one.
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
    TEST_CHECK((space & YS_SET_BIT_NS_CHAR) == 0); // ns-char is what is printable but not white space

    ys_char seven = ys_next_char((const uint8_t *)"7", 1);
    TEST_CHECK(seven == YS_KEY_DIGIT); // the grammar names '0' but no other digit
    TEST_CHECK((seven & YS_SET_BIT_NS_DEC_DIGIT) != 0);
    TEST_CHECK((seven & YS_SET_BIT_NS_HEX_DIGIT) != 0);

    ys_char nul = ys_next_char((const uint8_t *)"\0", 1);
    TEST_CHECK(nul == YS_KEY_CONTROL);
    TEST_CHECK((nul & YS_SET_BIT_C_PRINTABLE) == 0); // a control character is in no set at all
    TEST_CHECK(YS_LEN(nul) == 1);                    // but its length still tells it from the end of the input
}

// The two characters the grammar names above ASCII.
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

// Ordinary content above ASCII: one key whatever its length, since the grammar cannot tell such characters apart.
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

// The C1 controls and the two noncharacters: JSON-compatible, but not printable, and so not content either.
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

// Malformed UTF-8, after Markus Kuhn's decoder stress test. Each is rejected, and each consumes exactly one byte, so
// that a caller can step over it and carry on reporting rather than stopping at the first bad byte.
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
        TEST_MSG("%s: ys_utf8_length called it well-formed", cases[index].name);
    }
}

// `ys_utf8_length` answers for the bytes the wire format is handed, which nothing has classified. It agrees with
// `ys_next_char` on every ill-formed shape above; here it is on the well-formed ones, and on the empty window that
// only it can be asked about — `ys_next_char` reads the end of the input as a character, where a length of no bytes
// is simply no sequence.
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

// Every codepoint UTF-8 can encode reports the length it was encoded in, and every surrogate is rejected. The encoder
// here is written independently of the decoder, which is what makes this a check rather than a tautology.
//
// The first codepoint to disagree is remembered and reported after the sweep rather than at once, so that the sweep has
// no branch that only a failure would take — a branch no passing run could ever cover.
static void test_every_codepoint(void) {
    uint32_t first_wrong = 0; // no codepoint below U+0080 is swept, so zero can mean "none"
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

// Scanning a run — what the parser does for every (***) and (+++) over a character set.
static void test_scan_set(void) {
    ys_run spaces = ys_scan_set((const uint8_t *)"    x", 5, YS_SET_ID_S_WHITE);
    TEST_CHECK(spaces.bytes == 4 && spaces.characters == 4);

    const uint8_t *digits = (const uint8_t *)"1234abc";
    ys_run run = ys_scan_set(digits, 7, YS_SET_ID_NS_DEC_DIGIT);
    TEST_CHECK(run.bytes == 4 && run.characters == 4);

    // A run of no length, when the very first character is not in the set.
    run = ys_scan_set(digits, 7, YS_SET_ID_S_WHITE);
    TEST_CHECK(run.bytes == 0 && run.characters == 0);

    // A run stops at the end of the window, never past it.
    run = ys_scan_set(digits, 2, YS_SET_ID_NS_DEC_DIGIT);
    TEST_CHECK(run.bytes == 2 && run.characters == 2);
    run = ys_scan_set(digits, 0, YS_SET_ID_NS_DEC_DIGIT);
    TEST_CHECK(run.bytes == 0 && run.characters == 0);

    // A run carries on through non-ASCII content, where the bytes outnumber the characters: "ab" and U+4E00, which is
    // three bytes, stopping at the space.
    run = ys_scan_set((const uint8_t *)"ab\xE4\xB8\x80 z", 7, YS_SET_ID_NS_CHAR);
    TEST_CHECK(run.bytes == 5 && run.characters == 3);

    // Bytes that are not UTF-8 are in no set, so they end a run rather than being consumed by it.
    run = ys_scan_set((const uint8_t *)"ab\xFF", 3, YS_SET_ID_NS_CHAR);
    TEST_CHECK(run.bytes == 2 && run.characters == 2);
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
    {"scan_set", test_scan_set},
    {NULL, NULL},
};
