// SPDX-License-Identifier: MIT
// Decode a UTF-8 character of 0x80 or above. Consume a run of characters the grammar names. `ys_next_char` in the
// header decodes an ASCII byte from a table. It hands a byte of 0x80 or above to this file.

#include "decoder.h"
#include <stdbool.h>

// A lead byte says what shape the sequence it begins takes. That shape is a byte count and a range of bytes that may
// immediately follow. That range rejects an overlong encoding (0xE0 admits just 0xA0..0xBF), a surrogate (0xED admits
// just 0x80..0x9F) and a codepoint beyond U+10FFFF (0xF4 admits just 0x80..0x8F). Such a rejection needs no working
// out of the codepoint. A length of `0` marks a byte that cannot begin a sequence at all.
typedef struct ys_lead {
    uint8_t length;
    uint8_t first_min;
    uint8_t first_max;
} ys_lead;

// The shape of the sequence `byte` begins. https://www.rfc-editor.org/rfc/rfc3629 fixes that shape, and the grammar has
// no say in it.
static ys_lead lead_of(uint8_t byte) {
    if (byte >= 0xC2u && byte <= 0xDFu) {
        return (ys_lead){2, 0x80u, 0xBFu};
    }
    if (byte == 0xE0u) {
        return (ys_lead){3, 0xA0u, 0xBFu}; // below 0xA0 the sequence would be overlong
    }
    if (byte == 0xEDu) {
        return (ys_lead){3, 0x80u, 0x9Fu}; // The surrogates lie above 0x9F, and UTF-8 encodes no surrogate.
    }
    if (byte >= 0xE1u && byte <= 0xEFu) {
        return (ys_lead){3, 0x80u, 0xBFu};
    }
    if (byte == 0xF0u) {
        return (ys_lead){4, 0x90u, 0xBFu}; // below 0x90 the sequence would be overlong
    }
    if (byte == 0xF4u) {
        return (ys_lead){4, 0x80u, 0x8Fu}; // Above 0x8F lie the codepoints past U+10FFFF.
    }
    if (byte >= 0xF1u && byte <= 0xF3u) {
        return (ys_lead){4, 0x80u, 0xBFu};
    }
    // A byte from 0x80 to 0xBF continues a sequence. 0xC0 and 0xC1 lead an overlong sequence. UTF-8 assigns 0xF5 to
    // 0xFF no meaning.
    return (ys_lead){0, 0, 0};
}

// Whether `byte` continues a UTF-8 sequence.
static bool is_continuation(uint8_t byte) {
    return (byte & 0xC0u) == 0x80u;
}

size_t ys_utf8_length(const uint8_t *bytes, size_t size) {
    if (size == 0) {
        return 0;
    }
    if (bytes[0] < 0x80u) {
        return 1;
    }
    const ys_lead lead = lead_of(bytes[0]);
    if (lead.length == 0 || size < (size_t)lead.length) {
        return 0;
    }
    if (bytes[1] < lead.first_min || bytes[1] > lead.first_max) {
        return 0;
    }
    for (size_t index = 2; index < (size_t)lead.length; index++) {
        if (!is_continuation(bytes[index])) {
            return 0;
        }
    }
    return lead.length;
}

// A sequence running past the end of the window is invalid. That is the right answer at the true end of the input.
// The decoder answers "a
// character" or "not UTF-8", and it holds no further answer.
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

    // Above ASCII the grammar names NEL and U+FEFF. The grammar withholds c-printable from the C1 controls and from
    // U+FFFE and U+FFFF. A valid sequence outside those groups is ordinary content. `check_groups` in the generator
    // holds the grammar to exactly this grouping.
    if (lead.length == 2 && bytes[0] == 0xC2u) {
        if (bytes[1] == 0x85u) {
            return YS_LIT_KEY_NEXT_LINE;
        }
        if (bytes[1] < 0xA0u) {
            return YS_KEY_NOT_PRINTABLE | YS_LENGTH_BITS(2); // This byte pair encodes a C1 control other than NEL.
        }
    } else if (lead.length == 3 && bytes[0] == 0xEFu) {
        if (bytes[1] == 0xBBu && bytes[2] == 0xBFu) {
            return YS_LIT_KEY_ZERO_WIDTH_NO_BREAK_SPACE;
        }
        if (bytes[1] == 0xBFu && bytes[2] >= 0xBEu) {
            return YS_KEY_NOT_PRINTABLE | YS_LENGTH_BITS(3); // These bytes encode the noncharacter U+FFFE or U+FFFF.
        }
    }
    return YS_KEY_CONTENT | YS_LENGTH_BITS(lead.length);
}

// Advance while the character is in `set`. The ASCII branch classifies a byte at a time.
ys_consumed ys_consume_set(const uint8_t *bytes, size_t size, ys_set_id set) {
    const uint32_t wanted = YS_SET_BITS[set];
    ys_consumed taken = {0, 0};
    while (taken.bytes < size) {
        if (bytes[taken.bytes] < 0x80u) {
            if ((YS_ASCII[bytes[taken.bytes]] & wanted) == 0) {
                break;
            }
            taken.bytes += 1;
        } else {
            const ys_char character = ys_next_char_slow(bytes + taken.bytes, size - taken.bytes);
            if ((character & wanted) == 0) {
                break;
            }
            taken.bytes += YS_LEN(character);
        }
        taken.characters += 1;
    }
    return taken;
}

// `ys_consume_trim_sets` is the consume behind a trimmed span. The function takes the `full` set as `ys_consume_set`
// does. The function also remembers the split. `decoder.h` says which bytes the span keeps and which go back.
ys_trim ys_consume_trim_sets(const uint8_t *bytes, size_t size, ys_set_id full, ys_set_id trim) {
    const uint32_t in_full = YS_SET_BITS[full];
    const uint32_t in_trim = YS_SET_BITS[trim];
    ys_consumed taken = {0, 0};
    ys_consumed span = {0, 0};
    while (taken.bytes < size) {
        uint32_t key;
        if (bytes[taken.bytes] < 0x80u) {
            key = YS_ASCII[bytes[taken.bytes]];
            if ((key & in_full) == 0) {
                break;
            }
            taken.bytes += 1;
        } else {
            const ys_char character = ys_next_char_slow(bytes + taken.bytes, size - taken.bytes);
            key = character;
            if ((key & in_full) == 0) {
                break;
            }
            taken.bytes += YS_LEN(character);
        }
        taken.characters += 1;
        if ((key & in_trim) == 0) {
            span = taken;
        }
    }
    ys_trim result = {span, {taken.bytes - span.bytes, taken.characters - span.characters}};
    return result;
}
