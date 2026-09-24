// SPDX-License-Identifier: MIT
// A decode reads the input as characters the grammar names. `decoder.h` declares the types a decode answers with. It
// holds the ASCII path inline off the generated tables. `decoder.c` implements the calls `decoder.h` declares.

#ifndef YEAST_DECODER_H
#define YEAST_DECODER_H

#include "decoder_tables.h"
#include <stddef.h>
#include <stdint.h>

// A character, as the grammar classifies it. The type packs the grammar's answers about a character into a word.
//
//     the low bits  the id the grammar gives the character. A character the grammar leaves unnamed gets `0`. A pair
//                   of further ids mark the end of the input and a byte that is not UTF-8. The id tells apart named
//                   characters that belong to the same sets, as 'a' and 'e' do.
//     the middle    a bit per character set the grammar tests. A bit says whether the character sits in that set. The
//                   generator evaluated the unions and the subtractions while writing the tables.
//     the high bits the count of input bytes the character consumed. That count runs from `0` to `4`.
//
// The keys, the set bits and the macros reaching the length live in `decoder_tables.h`. The grammar generates that
// file. That file says at its head which bits are which. The macros and that heading come off the same constants. The
// grammar starts testing another set. That moves the bit boundary and the heading together. A bit position is knowledge
// this file keeps to itself.
//
// The parser assembles no Unicode codepoint. The parser compares no numeric value of a character. A token is a span
// of input bytes. So the decoder validates and classifies rather than decoding.
//
// Testing a character therefore takes a pair of shapes, and both go against a constant.
//
//     character == `YS_LIT_KEY_HYPHEN_MINUS`   // is it a character the grammar names?
//     character & `YS_SET_BIT_NS_CHAR`         // is it in a character set the grammar tests?
//
// The first is a single comparison. A named character's key holds still. The sets and the length in that key hold
// still too. The second is a single AND.
//
// The sentinels have no set bits. A membership test fails at the end of the input and at a byte that is not UTF-8.
// A test site has no reason to ask.
typedef uint32_t ys_char;

// The distance a consume reached. The parser advances the byte offset by the bytes, and the character offset by the
// characters. The column follows the character offset.
//
// The counts differ. The character sets below admit non-ASCII characters. Comment text and the lines of a literal or
// folded scalar take nb-char. Directive names and parameters take ns-char, and anchor names take ns-anchor-char. The
// text of a document that failed to parse takes nb-unparsed.
typedef struct ys_consumed {
    size_t bytes; // the bytes the consume covered.
    // the characters those bytes encoded. That count falls below the byte count wherever a character runs past ASCII.
    size_t characters;
} ys_consumed;

// A consume split. The split marks where the characters the consume gave back begin. `span` names the part a trimmed
// consume keeps. `trim` names the part that consume gave back, and it may be empty.
//
// A plain scalar keeps `span` and hands `trim` to the caller as its own `s-white*`. `trim` holds the spaces that follow
// the scalar. The consume reads the input once.
typedef struct ys_trim {
    ys_consumed span; // the trimmed consume. It reaches through the last character kept.
    // the characters the consume gave back after that.
    ys_consumed trim;
} ys_trim;

// Classify the character at the head of the window. That window begins with a byte of 0x80 or above.
ys_char ys_next_char_slow(const uint8_t *bytes, size_t size);

// The head of the window may start a well-formed UTF-8 sequence. This call answers how many bytes that sequence takes.
// The answer is `0` where no such sequence starts there. That covers a byte that begins nothing, and a sequence the
// window is too short for. An overlong encoding comes under it as well. A surrogate does too. So does anything past
// U+10FFFF.
//
// `ys_next_char` settles the length as it classifies a character. This call offers the length and classifies nothing.
// The wire format is the caller that wants the length. The wire format writes text somebody handed it, and that text
// may not be UTF-8 at all. Another caller reaches these bytes through `ys_next_char`, and that call has already
// decided.
size_t ys_utf8_length(const uint8_t *bytes, size_t size);

// Advance while the character is in `set`. Stop at the first character outside `set`, or at the end of the window.
//
// A consume does not cross a line. The parser's line number holds, and its column simply advances by the characters
// counted. That holds while no character set the grammar consumes admits a line break. `generator/check_decoder.py`
// fails the build the day that stops being true.
ys_consumed ys_consume_set(const uint8_t *bytes, size_t size, ys_set_id set);

// Advance while the character is in `full`. The split falls where the trailing `trim` characters begin. A single pass
// yields both parts. `.span` reaches through the last character outside `trim`. The consume keeps that span. `.trim`
// holds the given-back characters after that span.
//
// Over `trim` characters and no others, the consume leaves `.span` empty and hands the characters back. The grammar
// does not ask for a trimmed consume over such input.
//
// A trimmed consume compiles to the line of a plain scalar or the line of a quoted scalar. The consume keeps the inner
// spaces and gives the trailing spaces back.
ys_trim ys_consume_trim_sets(const uint8_t *bytes, size_t size, ys_set_id full, ys_set_id trim);

// The next character at the head of a window of `size` readable bytes, or `YS_LIT_KEY_EOF` when the window is empty.
//
// The compiler predicts the `size == 0` branch not-taken. The `size == 0` test holds true once per parse. At the true
// end of the input the processor mispredicts once. It then speculatively loads the byte past the buffer and indexes
// `YS_ASCII` with that byte.
//
// The speculative load is a Spectre-v1 shape at the fixed address `bytes + size`. An attacker controls no offset into
// it, and the load reaches no arbitrary read.
//
// Hardening it would cost a speculation barrier per character.
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
