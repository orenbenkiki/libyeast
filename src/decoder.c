// SPDX-License-Identifier: MIT
#include "decoder.h"
#include <stdbool.h>

// What a lead byte says about the sequence it begins: how many bytes it takes, and which bytes may follow it
// immediately. That second range is what rejects an overlong encoding (0xE0 admits only 0xA0..0xBF), a surrogate (0xED
// admits only 0x80..0x9F) and a codepoint beyond U+10FFFF (0xF4 admits only 0x80..0x8F) — none of which needs the
// codepoint to be worked out. A length of zero marks a byte that cannot begin a sequence at all.
typedef struct ys_lead {
    uint8_t length;
    uint8_t first_min;
    uint8_t first_max;
} ys_lead;

// The shape of the sequence `byte` begins. Fixed by RFC 3629; the grammar has no say in it.
static ys_lead lead_of(uint8_t byte) {
    if (byte >= 0xC2u && byte <= 0xDFu) {
        return (ys_lead){2, 0x80u, 0xBFu};
    }
    if (byte == 0xE0u) {
        return (ys_lead){3, 0xA0u, 0xBFu}; // below 0xA0 the sequence would be overlong
    }
    if (byte == 0xEDu) {
        return (ys_lead){3, 0x80u, 0x9Fu}; // above 0x9F lie the surrogates, which UTF-8 cannot encode
    }
    if (byte >= 0xE1u && byte <= 0xEFu) {
        return (ys_lead){3, 0x80u, 0xBFu};
    }
    if (byte == 0xF0u) {
        return (ys_lead){4, 0x90u, 0xBFu}; // below 0x90 the sequence would be overlong
    }
    if (byte == 0xF4u) {
        return (ys_lead){4, 0x80u, 0x8Fu}; // above 0x8F lies everything past U+10FFFF
    }
    if (byte >= 0xF1u && byte <= 0xF3u) {
        return (ys_lead){4, 0x80u, 0xBFu};
    }
    // 0x80..0xBF continue a sequence rather than beginning one, 0xC0 and 0xC1 could only ever be overlong, and
    // 0xF5..0xFF encode nothing at all.
    return (ys_lead){0, 0, 0};
}

// Whether `byte` continues a UTF-8 sequence.
static bool is_continuation(uint8_t byte) {
    return (byte & 0xC0u) == 0x80u;
}

// Classify the character at the head of the window, which begins with a byte of 0x80 or above.
//
// A sequence running past the end of the window is invalid, which is the right answer at the true end of the input. A
// buffer that hands the decoder a partial sequence anywhere else has a fault of its own, so there is no third answer
// between "a character" and "not UTF-8".
ys_char ys_next_char_slow(const uint8_t *bytes, size_t size) {
    const ys_lead lead = lead_of(bytes[0]);
    if (lead.length == 0 || size < (size_t)lead.length) {
        return YS_LIT_KEY_INVALID;
    }
    if (bytes[1] < lead.first_min || bytes[1] > lead.first_max) {
        return YS_LIT_KEY_INVALID;
    }
    for (size_t index = 2; index < (size_t)lead.length; index++) {
        if (!is_continuation(bytes[index])) {
            return YS_LIT_KEY_INVALID;
        }
    }

    // Above ASCII the grammar names two characters, and withholds c-printable from two kinds. Everything else valid is
    // ordinary content, whatever its length. The generator holds the grammar to exactly this grouping, so the ladder
    // cannot quietly fall out of step with it.
    if (lead.length == 2 && bytes[0] == 0xC2u) {
        if (bytes[1] == 0x85u) {
            return YS_LIT_KEY_NEXT_LINE;
        }
        if (bytes[1] < 0xA0u) {
            return YS_KEY_NOT_PRINTABLE | YS_LENGTH_BITS(2); // a C1 control
        }
    } else if (lead.length == 3 && bytes[0] == 0xEFu) {
        if (bytes[1] == 0xBBu && bytes[2] == 0xBFu) {
            return YS_LIT_KEY_ZERO_WIDTH_NO_BREAK_SPACE;
        }
        if (bytes[1] == 0xBFu && bytes[2] >= 0xBEu) {
            return YS_KEY_NOT_PRINTABLE | YS_LENGTH_BITS(3); // U+FFFE or U+FFFF, the noncharacters
        }
    }
    return YS_KEY_CONTENT | YS_LENGTH_BITS(lead.length);
}

// Advance while the character is in `set`, stopping at the first character that is not — or at the end of the window.
//
// This is what a (***) or a (+++) over a character set compiles to. The bytes of a YAML document go into runs — plain
// scalars, comment text, indentation, quoted content — and a run must not cost a classification per byte forever. The
// ASCII loop is where the time goes, and where a vector kernel will go: a nibble-table lookup classifies sixteen bytes
// at a time under SSSE3 or NEON, behind this same signature, without the generated parser changing a line.
ys_run ys_scan_set(const uint8_t *bytes, size_t size, ys_set_id set) {
    const uint32_t wanted = ys_set_bits(set);
    ys_run run = {0, 0};
    while (run.bytes < size) {
        if (bytes[run.bytes] < 0x80u) {
            if ((YS_ASCII[bytes[run.bytes]] & wanted) == 0) {
                break;
            }
            run.bytes += 1;
        } else {
            const ys_char character = ys_next_char_slow(bytes + run.bytes, size - run.bytes);
            if ((character & wanted) == 0) {
                break;
            }
            run.bytes += YS_LEN(character);
        }
        run.characters += 1;
    }
    return run;
}
