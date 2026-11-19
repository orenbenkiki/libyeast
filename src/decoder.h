// SPDX-License-Identifier: MIT
// A decode reads the input as characters the grammar classifies. `decoder.h` declares the types a decode answers with.
// It holds the ASCII path inline off the generated tables in `decoder_tables.h`.

#ifndef YEAST_DECODER_H
#define YEAST_DECODER_H

#include "decoder_tables.h"
#include <stddef.h>
#include <stdint.h>

// A character, as the grammar classifies it. The type packs the grammar's answers about a character into a word.
//
//     the low bits  the id the grammar gives the character. An unnamed character takes the id `0`.
//                   `chars._LIT_NONE` holds that id. A pair
//                   of further ids mark the end of the input and a byte that is not UTF-8. The id tells apart named
//                   characters that belong to the same sets, as 'a' and 'e' do.
//     the middle    a bit per character set the grammar tests. A bit says whether the character sits in that set. The
//                   generator evaluated the unions and the subtractions in `does_contain` while writing the tables.
//     the high bits the count of input bytes the character consumed. That count runs from `0` to `4`.
//
// The keys, the set bits and the macros reaching the length live in `decoder_tables.h`. `generator/grammar2decoder.py`
// generates that file from the grammar. That file says at its head which bits are which. The macros and that heading
// come off the same constants. A grammar testing another set widens the set range the heading states. `chars.LEN_SHIFT`
// fixes where the length starts. `chars.Model` refuses a grammar whose sets would reach it.
//
// `ys_next_char` validates and classifies a character rather than decoding it. The `text` of a leaf `ys_token` points
// at the matched input bytes.
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
// A test site need not check for a sentinel first.
typedef uint32_t ys_char;

// The distance a consume reached.
//
// The counts differ wherever a character runs past ASCII.
typedef struct ys_consumed {
    size_t bytes; // the bytes the consume covered.
    // the characters those bytes encoded. That count falls below the byte count wherever a character runs past ASCII.
    size_t characters;
} ys_consumed;

// A consume split. The split marks where the characters the consume gave back begin. `span` names the part a trimmed
// consume keeps. `trim` names the part that consume gave back, and it may be empty.
//
// `ys_consume_trim_sets` reads the input once.
typedef struct ys_trim {
    ys_consumed span; // the trimmed consume. It reaches through the last character kept.
    // the characters the consume gave back after that.
    ys_consumed trim;
} ys_trim;

// Classify the character at the head of the window. That window begins with a byte of 0x80 or above.
ys_char ys_next_char_slow(const uint8_t *bytes, size_t size);

// The head of the window may start a well-formed UTF-8 sequence. This call answers how many bytes that sequence takes.
// The answer is `0` where no such sequence starts there. That covers a byte that begins nothing, and a sequence the
// window is too short for. The answer is `0` for an overlong encoding, for a surrogate, and for a code point past
// U+10FFFF.
//
// `ys_next_char` settles the length as it classifies a character. This call offers the length and classifies nothing.
// `ys_codepoint` calls this function for the length. `ys_unescape` calls it to check that no byte of an
// unparsed-invalid token begins a character. `ys_wire_write` hands `ys_codepoint` the text a caller gives the sink.
// Such text may hold bytes that are not UTF-8.
size_t ys_utf8_length(const uint8_t *bytes, size_t size);

// Advance while the character is in `set`. Stop at the first character outside `set`, or at the end of the window.
//
// `generator/check_decoder.py` fails the build where a character set the grammar consumes admits a line break.
ys_consumed ys_consume_set(const uint8_t *bytes, size_t size, ys_set_id set);

// Advance while the character is in `full`. The split falls where the trailing `trim` characters begin. A single pass
// yields both parts. `.span` reaches through the last character outside `trim`. The consume keeps that span. `.trim`
// holds the given-back characters after that span.
//
// Over `trim` characters and no others, the consume leaves `.span` empty and hands the characters back.
//
// A `trim` character inside the span stays in it. The trailing `trim` characters go back.
ys_trim ys_consume_trim_sets(const uint8_t *bytes, size_t size, ys_set_id full, ys_set_id trim);

// The next character at the head of a window of `size` readable bytes, or `YS_LIT_KEY_EOF` when the window is empty.
//
// A processor may mispredict the `size == 0` branch at the end of the input. The processor then speculatively loads the
// byte past the buffer and indexes `YS_ASCII` with that byte. https://spectreattack.com/spectre.pdf describes that
// speculation.
//
// The speculative load is a Spectre-v1 shape at the fixed address `bytes + size`. An attacker controls no offset into
// it, and the load reaches no arbitrary read.
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
