// SPDX-License-Identifier: MIT
// Reading the input as characters the grammar names. The types a decode answers with, the ASCII path taken inline
// from the generated tables, and the declarations `decoder.c` implements.

#ifndef YEAST_DECODER_H
#define YEAST_DECODER_H

#include "decoder_tables.h"
#include <stddef.h>
#include <stdint.h>

// A character, as the grammar classifies it. This is the grammar's answer, in a word, to what it can ask about a
// character.
//
//     the low bits  the id the grammar gives the character. A character the grammar leaves unnamed gets `0`. A pair
//                   of further ids mark the end of the input and a byte that is not UTF-8. The id tells apart named
//                   characters that belong to the same sets, as 'a' and 'e' do.
//     the middle    a bit per character set the grammar tests. A bit says whether the character sits in that set. The
//                   generator evaluated the unions and the subtractions while writing the tables.
//     the high bits the count of input bytes the character consumed. That count runs from `0` to `4`.
//
// The keys, the set bits and the macros reaching the length live in decoder_tables.h. The grammar generates that file.
// That file says at its head which bits are which. The macros and that heading come off the same constants. A set the
// grammar starts testing moves the boundary and the sentence together. A bit position is knowledge this file keeps to
// itself.
//
// The parser assembles no Unicode codepoint. The parser compares no numeric value of a character. A token is a span
// of input bytes. So the decoder validates and classifies rather than decoding.
//
// Testing a character therefore takes a pair of shapes, and both go against a constant.
//
//     character == YS_LIT_KEY_HYPHEN_MINUS   // is it a character the grammar names?
//     character & YS_SET_BIT_NS_CHAR         // is it in a character set the grammar tests?
//
// The first is a single comparison. A named character's key holds still. The sets and the length in that key hold
// still too. The second is a single AND.
//
// The sentinels have no set bits. At the end of the input, and at a byte that is not UTF-8, a membership test fails
// of its own accord. A test site has no reason to ask.
typedef uint32_t ys_char;

// The distance a consume reached. The parser advances the byte offset by the bytes, and the character offset by the
// characters. The column follows the character offset.
//
// The counts differ. Some of the character sets the grammar consumes admit non-ASCII characters. Those are comment
// text and the lines of a literal or folded scalar (nb-char). Directive names and parameters (ns-char), and anchor
// names (ns-anchor-char). Also the text of a document that failed to parse (nb-unparsed).
typedef struct ys_consumed {
    size_t bytes; // the bytes the consume covered.
    // the characters those bytes encoded. That count falls below the byte count wherever a character runs past ASCII.
    size_t characters;
} ys_consumed;

// A consume split where its trailing given-back characters begin. `span` reaches through the last character kept,
// where a trimmed consume ends. `trim` holds what the consume gave back after that, and it may be empty.
//
// A plain scalar keeps `span` and hands `trim` to the caller as its own `s-white*`. Those are the spaces that follow,
// and the consume reads the input once.
typedef struct ys_trim {
    ys_consumed span; // the trimmed consume. It reaches through the last character kept.
    // the characters the consume gave back after that. The trailing characters of the trimmed kind, or an empty run.
    ys_consumed trim;
} ys_trim;

// Classify the character at the head of the window. That window begins with a byte of 0x80 or above.
ys_char ys_next_char_slow(const uint8_t *bytes, size_t size);

// The number of bytes the well-formed UTF-8 sequence at the head of the window takes, or `0` where no such sequence
// starts there. That covers a byte that begins nothing, and a sequence the window is too short for. An overlong
// encoding comes under it as well. A surrogate does too. So does anything past U+10FFFF.
//
// Classifying a character settles this on the way past. This call offers the answer on its own. The caller that wants
// it holds bytes whose well-formedness nothing has established yet. That caller is the wire format. The wire format
// writes text somebody handed it. Another caller reaches these bytes through `ys_next_char`, and that call has already
// decided.
size_t ys_utf8_length(const uint8_t *bytes, size_t size);

// Advance while the character is in `set`. Stop at the first character outside `set`, or at the end of the window.
//
// A consume does not cross a line. The parser's line number holds, and its column simply advances by the characters
// counted. That holds while no character set the grammar consumes admits a line break. A quiet reliance on a
// coincidence would be a fault. `generator/check_decoder.py` fails the build the day that stops being true.
ys_consumed ys_consume_set(const uint8_t *bytes, size_t size, ys_set_id set);

// Advance while the character is in `full`. The split falls where the trailing `trim` characters begin. A single pass
// yields both parts. `.span` reaches through the last character outside `trim`, and the consume keeps that. `.trim`
// holds the given-back characters after it.
//
// Over `trim` characters and no others, this leaves `.span` empty and hands the characters back. That is what the
// split says of that input. The grammar does not ask it there. A trimmed consume sits behind a gate that already
// answered for the character.
//
// A trimmed consume compiles to this. That is a plain or a quoted scalar's line, with the inner spaces kept and the
// trailing spaces given back.
ys_trim ys_consume_trim_sets(const uint8_t *bytes, size_t size, ys_set_id full, ys_set_id trim);

// The next character at the head of a window of `size` readable bytes, or YS_LIT_KEY_EOF when the window is empty.
//
// The compiler predicts the `size == 0` branch not-taken, and it holds true once per parse. At the true end of the
// input the processor mispredicts once and speculatively loads the byte past the buffer to index YS_ASCII.
//
// That is a Spectre-v1 shape, and it is not a threat. The address is exactly `bytes + size`. An attacker controls no
// offset into it. That load cannot reach an arbitrary read, and the bounds check itself is correct.
//
// Hardening it would cost a speculation barrier per character, or a padded-input contract that
// ys_new_yaml_memory_parser() cannot impose on a buffer that belongs to the caller.
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
