// SPDX-License-Identifier: MIT
#ifndef YEAST_DECODER_H
#define YEAST_DECODER_H

#include "decoder_tables.h"
#include <stddef.h>
#include <stdint.h>

// A character, classified against the grammar — the grammar's answer to every question it can ask about one, in a word:
//
//     bits  0..5   the id of the character, if the grammar names it, and 0 if it does not. The grammar names 57
//                  characters; two further ids mark the end of the input and a byte that is not UTF-8. The id is what
//                  tells two named characters apart when they belong to exactly the same sets, as 'a' and 'e' do.
//     bits  6..24  one bit per character set the grammar tests, of which there are 19, saying whether the character is
//                  in that set. The unions and the subtractions were evaluated when the tables were generated.
//     bits 25..27  how many bytes of input the character consumed, from 0 to 4.
//
// The keys, the set bits and the macros reaching the length are all in decoder_tables.h, which the grammar generates —
// so this is where the layout is described, and that is where it is decided. No other code knows a bit position.
//
// No Unicode codepoint is ever assembled. Nothing in the parser compares a character's numeric value — tokens are spans
// of input bytes — so the decoder validates and classifies rather than decoding. Testing a character is therefore one
// of exactly two shapes, each against a constant:
//
//     character == YS_LIT_KEY_HYPHEN_MINUS   // is it a character the grammar names?
//     character & YS_SET_BIT_NS_CHAR         // is it in a character set the grammar tests?
//
// The first is a single comparison, because a named character's key is fixed: its sets are, and so is its length. The
// second is a single AND. The sentinels carry no set bits, so at the end of the input, and at a byte that is not UTF-8,
// every membership test fails of its own accord — no test site anywhere needs to ask.
typedef uint32_t ys_char;

// How far a run of characters reached: the parser advances its byte offset by the one and its character offset — and so
// its column — by the other. The two differ, because three of the seven character sets the grammar scans admit
// non-ASCII characters: comment text and the lines of a literal or folded scalar (nb-char), directive names and
// parameters (ns-char), and anchor names (ns-anchor-char).
typedef struct ys_run {
    size_t bytes;      // the bytes the run covered
    size_t characters; // the characters they encoded, which is fewer whenever any of them is not ASCII
} ys_run;

// The characters of the set `set`.
static inline uint32_t ys_set_bits(ys_set_id set) {
    return YS_SET_BITS[set];
}

// Classify the character at the head of the window, which begins with a byte of 0x80 or above.
ys_char ys_next_char_slow(const uint8_t *bytes, size_t size);

// Advance while the character is in `set`, stopping at the first character that is not — or at the end of the window.
//
// A run never crosses a line, so the parser's line number is unchanged by one and its column simply advances by the
// characters counted. That holds because no character set the grammar scans admits a line break, which is not a
// coincidence to be relied upon quietly: `generator/check_decoder.py` fails the build if it ever stops being true.
ys_run ys_scan_set(const uint8_t *bytes, size_t size, ys_set_id set);

// The next character at the head of a window of `size` readable bytes, or YS_LIT_KEY_EOF when the window is empty.
//
// The `size == 0` branch is predicted not-taken almost always — it is true once per parse — so at the true end of the
// input the processor mispredicts once and speculatively loads the byte past the buffer to index YS_ASCII. That is a
// Spectre-v1 shape, and it is not a threat: the address is always exactly `bytes + size`, with no attacker-controlled
// offset, so it cannot be steered into an arbitrary read, and the bounds check itself is correct. Hardening it would
// cost a speculation barrier per character, or a padded-input contract that ys_new_string_parser() cannot impose on a
// buffer that belongs to the caller.
static inline ys_char ys_next_char(const uint8_t *bytes, size_t size) {
    if (size == 0) {
        return YS_LIT_KEY_EOF;
    }
    if (bytes[0] < 0x80u) {
        return YS_ASCII[bytes[0]];
    }
    return ys_next_char_slow(bytes, size);
}

#endif // YEAST_DECODER_H
