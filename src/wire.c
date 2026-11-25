// SPDX-License-Identifier: MIT
// The yeast wire format. A token is a pair of lines. The first line is the position. The second is the code character
// followed by the escaped text. The wire lets a tool pipe a token stream onward or store it. `check_interpreter`
// serializes the interpreter's tokens with `wire.serialize` and compares the result against the `.output` file that
// `spec_tests.load` pairs with a `Fixture`. The comparison goes byte by byte.

#include "wire.h"
#include "decoder.h"
#include "memory.h"
#include "messages.h"
#include "source.h"

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <yeast.h>

// The character that writes a code. A wire reports a malformed document as '!'. A host failure is
// no token, and no wire character writes a host failure.
//
// A character in the table is printable. '\0' means "the wire writes nothing for this", and no printable code takes
// that value. `ys_code_of_char` refuses `'\0'`. A reader handed a code written as `\0` reports a malformed wire.
// `check_wire.py` holds the table to that rule.
static const char YS_WIRE[] = {
    [YS_CODE_BOM] = 'U',
    [YS_CODE_TEXT] = 'T',
    [YS_CODE_META] = 't',
    [YS_CODE_BREAK] = 'b',
    [YS_CODE_LINE_FEED] = 'L',
    [YS_CODE_LINE_FOLD] = 'l',
    [YS_CODE_INDICATOR] = 'I',
    [YS_CODE_WHITE] = 'w',
    [YS_CODE_INDENT] = 'i',
    [YS_CODE_DIRECTIVES_END] = 'K',
    [YS_CODE_DOCUMENT_END] = 'k',
    [YS_CODE_BEGIN_ESCAPE] = 'E',
    [YS_CODE_END_ESCAPE] = 'e',
    [YS_CODE_BEGIN_COMMENT] = 'C',
    [YS_CODE_END_COMMENT] = 'c',
    [YS_CODE_BEGIN_DIRECTIVE] = 'D',
    [YS_CODE_END_DIRECTIVE] = 'd',
    [YS_CODE_BEGIN_TAG] = 'G',
    [YS_CODE_END_TAG] = 'g',
    [YS_CODE_BEGIN_HANDLE] = 'H',
    [YS_CODE_END_HANDLE] = 'h',
    [YS_CODE_BEGIN_ANCHOR] = 'A',
    [YS_CODE_END_ANCHOR] = 'a',
    [YS_CODE_BEGIN_PROPERTIES] = 'P',
    [YS_CODE_END_PROPERTIES] = 'p',
    [YS_CODE_BEGIN_ALIAS] = 'R',
    [YS_CODE_END_ALIAS] = 'r',
    [YS_CODE_BEGIN_SCALAR] = 'S',
    [YS_CODE_END_SCALAR] = 's',
    [YS_CODE_BEGIN_SEQUENCE] = 'Q',
    [YS_CODE_END_SEQUENCE] = 'q',
    [YS_CODE_BEGIN_MAPPING] = 'M',
    [YS_CODE_END_MAPPING] = 'm',
    [YS_CODE_BEGIN_PAIR] = 'X',
    [YS_CODE_END_PAIR] = 'x',
    [YS_CODE_BEGIN_NODE] = 'N',
    [YS_CODE_END_NODE] = 'n',
    [YS_CODE_BEGIN_DOCUMENT] = 'O',
    [YS_CODE_END_DOCUMENT] = 'o',
    [YS_CODE_BEGIN_STREAM] = 'Y',
    [YS_CODE_END_STREAM] = 'y',
    [YS_CODE_ERROR] = '!',
    [YS_CODE_UNPARSED_TEXT] = '-',
    [YS_CODE_UNPARSED_BREAK] = '.',
    [YS_CODE_UNPARSED_INVALID] = '~',
    [YS_CODE_DETECTED] = '$',
};

char ys_code_char(ys_code code) {
    if ((size_t)code >= sizeof(YS_WIRE) / sizeof(YS_WIRE[0])) {
        return '\0'; // UNTESTED
    }
    return YS_WIRE[code];
}

int ys_code_of_char(char character, ys_code *code) {
    for (size_t index = 0; index < sizeof(YS_WIRE) / sizeof(YS_WIRE[0]); index++) {
        if (YS_WIRE[index] == character && YS_WIRE[index] != '\0') {
            *code = (ys_code)index;
            return YS_OK;
        }
    }
    errno = EINVAL; // The character names no code.
    return YS_FAILED_ACTION;
}

// --- Writing a token.

// `wire.h` declares this function.
int ys_put(ys_bytes_writer *writer, const char *bytes, size_t size) {
    while (size > 0) {
        ptrdiff_t written = writer->write(writer->context, bytes, size);
        if (written <= 0) {
            return YS_FAILED_STREAM; // UNTESTED
        }
        bytes += (size_t)written;
        size -= (size_t)written;
    }
    return YS_OK;
}

// `ys_codepoint` decodes a codepoint from the UTF-8 sequence at `bytes`. It also answers the length of that sequence in
// bytes. A length of `0` says the text at `bytes` is not UTF-8.
//
// `ys_wire_write` calls `ys_codepoint` to escape a token's text by codepoint rather than by byte.
static unsigned long ys_codepoint(const unsigned char *bytes, size_t size, size_t *length) {
    static const unsigned char YS_LEAD_MASK[5] = {0, 0x7Fu, 0x1Fu, 0x0Fu,
                                                  0x07u}; // The mask keeps a lead byte's payload bits.
    *length = ys_utf8_length(bytes, size);
    if (*length == 0) {
        return 0;
    }
    unsigned long codepoint = bytes[0] & YS_LEAD_MASK[*length];
    for (size_t index = 1; index < *length; index++) {
        codepoint = (codepoint << 6) | (bytes[index] & 0x3Fu);
    }
    return codepoint;
}

int ys_wire_write(ys_bytes_writer *writer, ys_token token) {
    // An out-of-range code has no wire character, and the call refuses it as a bad argument.
    const char code_character = ys_code_char(token.code);
    if (code_character == '\0') {
        errno = EINVAL;          // UNTESTED
        return YS_FAILED_ACTION; // UNTESTED
    }

    char buffer[128];
    int written = snprintf(buffer, sizeof(buffer), "# B: %zu, C: %zu, L: %zu, c: %zu\n%c", token.start.byte_offset,
                           token.start.char_offset, token.start.line, token.start.column, code_character);
    if (written < 0 || (size_t)written >= sizeof(buffer)) {
        return YS_FAILED_STREAM; // UNTESTED
    }
    if (ys_put(writer, buffer, (size_t)written) != YS_OK) {
        return YS_FAILED_STREAM; // UNTESTED
    }

    // Printable ASCII other than a backslash writes as itself. Another codepoint writes as a lower-case hex escape. The
    // text of an error is its message.
    const unsigned char *text = (const unsigned char *)token.text;
    size_t size = token.text == NULL            ? 0
                  : token.code == YS_CODE_ERROR ? strlen(token.text)
                                                : token.end.byte_offset - token.start.byte_offset;
    // An escape means a codepoint under a code other than `YS_CODE_UNPARSED_INVALID`. An escape means a byte under that
    // code. A text that breaks this rule for its code refuses the write.
    const bool wants_characters = token.code != YS_CODE_UNPARSED_INVALID;
    for (size_t index = 0; index < size;) {
        size_t length = 1;
        unsigned long codepoint = ys_codepoint(text + index, size - index, &length);
        if ((length == 0) == wants_characters) {
            errno = EINVAL;
            return YS_FAILED_ACTION;
        }
        if (length == 0) {
            // The byte begins no character. The wire writes the byte as a byte escape.
            int escaped = snprintf(buffer, sizeof(buffer), "\\x%02x", text[index]);
            if (escaped < 0 || ys_put(writer, buffer, (size_t)escaped) != YS_OK) {
                return YS_FAILED_STREAM; // UNTESTED
            }
            index += 1;
            continue;
        }
        index += length;
        if (codepoint >= ' ' && codepoint <= '~' && codepoint != '\\') {
            char character = (char)codepoint;
            if (ys_put(writer, &character, 1) != YS_OK) {
                return YS_FAILED_STREAM; // UNTESTED
            }
        } else {
            int escaped;
            if (codepoint <= 0xFFuL) {
                escaped = snprintf(buffer, sizeof(buffer), "\\x%02lx", codepoint);
            } else if (codepoint <= 0xFFFFuL) {
                escaped = snprintf(buffer, sizeof(buffer), "\\u%04lx", codepoint);
            } else {
                escaped = snprintf(buffer, sizeof(buffer), "\\U%08lx", codepoint);
            }
            if (escaped < 0 || ys_put(writer, buffer, (size_t)escaped) != YS_OK) {
                return YS_FAILED_STREAM; // UNTESTED
            }
        }
    }
    return ys_put(writer, "\n", 1);
}

// --- Reading a token back.

// The wire arm accumulates whole lines and unescapes a token's text into storage of its own. The wire arm holds its
// replay state in `ys_wire`. The parser arm `ys_parser` holds no text buffer. Its `ys_window` holds a `ys_source`, the
// same kind of byte buffer the wire reads lines into. The union in `ys_token_source` holds both arms. Both buffers grow
// through the arm's `ys_memory` handle. `ys_new_source` fills a `ys_memory` from the options, and
// `ys_new_yeast_stream_reader` hands it to the arm. `ys_options::max_bytes` bounds both buffers the way it bounds the
// parser. `wire.h` declares the struct.

void ys_wire_init(ys_wire *wire, ys_memory memory) {
    // Point the wire at `memory`.
    wire->memory = memory;
}

// The next line of the wire. The newline drops off. NULL at the end of the stream. NULL too where the reader or the
// allocator fails, and that sets `ys_wire::fault`.
//
// The line stays valid until the next call. A NUL takes the newline's place. A last line without a newline gets a NUL
// past its end.
//
// The search for the newline resumes where the last search gave up rather than starting over. A line arriving in pieces
// gets a fill per piece. Rescanning those pieces would cost a long line its length squared.
static char *ys_next_line(ys_wire *reader, size_t *size) {
    for (;;) {
        char *bytes = (char *)reader->source.bytes;
        char *found = memchr(bytes + reader->scanned, '\n', reader->source.size - reader->scanned);
        if (found != NULL) {
            size_t index = (size_t)(found - bytes);
            char *line = bytes + reader->consumed;
            *size = index - reader->consumed;
            bytes[index] = '\0';
            reader->consumed = index + 1;
            reader->scanned = reader->consumed;
            reader->wire_line += 1;
            return line;
        }
        reader->scanned = reader->source.size; // The search found no break in these bytes.
        if (reader->source.is_at_end) {
            if (reader->consumed == reader->source.size) {
                return NULL;
            }
            char *line = bytes + reader->consumed;
            *size = reader->source.size - reader->consumed;
            bytes[reader->source.size] = '\0'; // A NUL fills the byte the source keeps spare.
            reader->consumed = reader->source.size;
            reader->scanned = reader->consumed;
            reader->wire_line += 1;
            return line;
        }

        // The buffer holds no whole line. The fill drops the lines already handed back and reads more. `scanned` moves
        // left with the bytes.
        ys_fill filled = ys_source_fill(&reader->source, &reader->memory, reader->consumed, 1);
        reader->scanned -= reader->consumed;
        reader->consumed = 0;
        if (filled == YS_FILL_OUT_OF_MEMORY) {
            reader->fault = YS_FAILED_MEMORY;
            return NULL;
        }
        if (filled == YS_FILL_READER_FAILED) {
            reader->fault = YS_FAILED_STREAM;
            return NULL;
        }
    }
}

// Read `count` hexadecimal digits into `value`, and hand back the byte past the last of them. NULL where a character
// there falls outside the hexadecimal digits, and `value` stays unchanged. A parse of the wire says where it
// reached.
//
// The value is unsigned. `8` digits reach 0xFFFFFFFF. That overflows a signed long wherever a long is 32 bits. MSVC's
// `long` is 32 bits. [Data Type Ranges](https://learn.microsoft.com/en-us/cpp/cpp/data-type-ranges?view=msvc-170)
// lists the sizes MSVC uses.
static const char *ys_hex(const char *digits, size_t count, unsigned long *value) {
    unsigned long read = 0;
    for (size_t index = 0; index < count; index++) {
        char digit = digits[index];
        unsigned long place;
        if (digit >= '0' && digit <= '9') {
            place = (unsigned long)(digit - '0');
        } else if (digit >= 'A' && digit <= 'F') {
            place = (unsigned long)(digit - 'A') + 10uL;
        } else if (digit >= 'a' && digit <= 'f') {
            place = (unsigned long)(digit - 'a') + 10uL;
        } else {
            return NULL;
        }
        read = read * 16uL + place;
    }
    *value = read;
    return digits + count;
}

// Append a codepoint to the reader's text, as UTF-8.
static int ys_append(ys_wire *reader, unsigned long codepoint) {
    char *grown = ys_memory_grow(&reader->memory, reader->text, &reader->text_capacity, reader->text_size + 4,
                                 YS_MEMORY_ITEMS, sizeof(char));
    if (grown == NULL) {
        reader->fault = YS_FAILED_MEMORY; // `ys_wire::fault` tells a resource fault from a malformed wire.
        return YS_FAILED_MEMORY;
    }
    reader->text = grown;
    char *at = reader->text + reader->text_size;
    if (codepoint < 0x80uL) {
        at[0] = (char)codepoint;
        reader->text_size += 1;
    } else if (codepoint < 0x800uL) {
        at[0] = (char)(0xC0uL | (codepoint >> 6));
        at[1] = (char)(0x80uL | (codepoint & 0x3FuL));
        reader->text_size += 2;
    } else if (codepoint < 0x10000uL) {
        at[0] = (char)(0xE0uL | (codepoint >> 12));
        at[1] = (char)(0x80uL | ((codepoint >> 6) & 0x3FuL));
        at[2] = (char)(0x80uL | (codepoint & 0x3FuL));
        reader->text_size += 3;
    } else {
        at[0] = (char)(0xF0uL | (codepoint >> 18));
        at[1] = (char)(0x80uL | ((codepoint >> 12) & 0x3FuL));
        at[2] = (char)(0x80uL | ((codepoint >> 6) & 0x3FuL));
        at[3] = (char)(0x80uL | (codepoint & 0x3FuL));
        reader->text_size += 4;
    }
    return YS_OK;
}

// Append a raw byte, for an unparsed-invalid token whose text is bytes and not codepoints.
static int ys_append_byte(ys_wire *reader, unsigned char byte) {
    char *grown = ys_memory_grow(&reader->memory, reader->text, &reader->text_capacity, reader->text_size + 1,
                                 YS_MEMORY_ITEMS, sizeof(char));
    if (grown == NULL) {
        reader->fault = YS_FAILED_MEMORY; // `ys_wire::fault` tells a resource fault from a malformed wire.
        return YS_FAILED_MEMORY;
    }
    reader->text = grown;
    reader->text[reader->text_size] = (char)byte;
    reader->text_size += 1;
    return YS_OK;
}

// Unescape a token's text into storage the reader owns, and count the codepoints and the breaks in it. The wire
// records the start of a token. Those counts give the end.
//
// A false `wants_characters` marks an unparsed-invalid token. The text of such a token is raw bytes. Such a byte comes
// from its
// \xXX escape. That byte must begin no character, and the writer follows the same rule.
//
// Hands back the byte past the text it read, or NULL on a fault. The `fault_at` parameter gives the
// fault's offset in the `escaped` text. The `why` parameter names the fault. A host failure sets the `ys_wire::fault`
// field instead. The caller tells the faults apart by `ys_wire::fault`.
static const char *ys_unescape(ys_wire *reader, const char *escaped, size_t size, bool wants_characters, ys_mark *end,
                               size_t *fault_at, ys_message_id *why) {
    reader->text_size = 0;
    for (size_t index = 0; index < size;) {
        if (!wants_characters) {
            // The writer writes a byte of an unparsed-invalid token as \xXX. The reader turns that escape back into the
            // same byte.
            if (escaped[index] != '\\' || index + 4 > size || escaped[index + 1] != 'x') {
                *fault_at = index;
                *why = YS_MESSAGE_WIRE_BAD_ESCAPE;
                return NULL;
            }
            unsigned long byte;
            const char *past = ys_hex(escaped + index + 2, 2, &byte);
            if (past == NULL) {
                *fault_at = index;
                *why = YS_MESSAGE_WIRE_BAD_ESCAPE;
                return NULL;
            }
            if (ys_append_byte(reader, (unsigned char)byte) != YS_OK) {
                return NULL; // `ys_append_byte` set `ys_wire::fault`.
            }
            end->char_offset += 1;
            end->column += 1;
            index = (size_t)(past - escaped);
            continue;
        }
        unsigned long codepoint;
        if (escaped[index] != '\\') {
            unsigned char raw = (unsigned char)escaped[index];
            if (raw < 0x20u || raw > 0x7Eu) {
                *fault_at = index;
                *why = YS_MESSAGE_WIRE_STRAY_BYTE;
                return NULL;
            }
            codepoint = raw;
            index += 1;
        } else {
            size_t digits = index + 1 < size && escaped[index + 1] == 'x'   ? 2
                            : index + 1 < size && escaped[index + 1] == 'u' ? 4
                            : index + 1 < size && escaped[index + 1] == 'U' ? 8
                                                                            : 0;
            const char *past =
                digits == 0 || index + 2 + digits > size ? NULL : ys_hex(escaped + index + 2, digits, &codepoint);
            if (past == NULL || codepoint > 0x10FFFFuL || (codepoint >= 0xD800uL && codepoint <= 0xDFFFuL)) {
                *fault_at = index;
                *why = YS_MESSAGE_WIRE_BAD_ESCAPE;
                return NULL;
            }
            index = (size_t)(past - escaped);
        }
        if (ys_append(reader, codepoint) != YS_OK) {
            return NULL; // `ys_append` set `ys_wire::fault`. `ys_wire_read` reads that field to tell a resource fault
                         // from a malformed wire.
        }
        end->char_offset += 1;
        if (codepoint == '\n') {
            end->line += 1;
            end->column = 0;
        } else {
            end->column += 1;
        }
    }
    // A byte that begins a character faults with `YS_MESSAGE_WIRE_CHAR_IN_INVALID`. The offset of a fault at byte `at`
    // is `at` times the width of a `\xXX` escape.
    if (!wants_characters) {
        for (size_t at = 0; at < reader->text_size; at += 1) {
            if (ys_utf8_length((const unsigned char *)reader->text + at, reader->text_size - at) != 0) {
                *fault_at = at * 4;
                *why = YS_MESSAGE_WIRE_CHAR_IN_INVALID;
                return NULL;
            }
        }
    }
    end->byte_offset += reader->text_size;
    return escaped + size;
}

// The wire writes a labelled number as a label followed by decimal digits. `ys_scan` returns the byte past the digits.
// `ys_scan` returns NULL where the label is missing, where no digit follows, or where the number overflows.
// `ys_next_line` also signals failure with NULL.
static const char *ys_scan(const char *at, const char *label, size_t *value) {
    size_t size = strlen(label);
    if (strncmp(at, label, size) != 0) {
        return NULL;
    }
    const char *digits = at + size;
    // `strtoul` skips white space and accepts a sign, as https://en.cppreference.com/w/c/string/byte/strtoul says. A
    // position on the wire holds neither.
    if (digits[0] < '0' || digits[0] > '9') {
        return NULL;
    }
    // `strtoul` reports a range error through `errno`, as https://en.cppreference.com/w/c/string/byte/strtoul says.
    // `ys_scan` restores the caller's `errno` after the call.
    char *after = NULL;
    const int saved = errno;
    errno = 0;
    unsigned long parsed = strtoul(digits, &after, 10);
    const bool ranged = errno != 0;
    errno = saved;
    if (after == digits || ranged) {
        return NULL;
    }
    *value = (size_t)parsed;
    return after;
}

// The wire is malformed. The call hands back a `YS_CODE_ERROR` token. That token spends the wire.
//
// The token's marks locate the fault in the wire. `line` counts from `1` and `column` counts from `0`. The byte and
// codepoint offsets stay `0`. The fault sits in the wire rather than in something parsed from the wire. The token's
// text describes the fault.
static int ys_wire_error(ys_wire *reader, ys_token *token, size_t line, size_t column, ys_message_id id) {
    reader->is_done = true;
    token->code = YS_CODE_ERROR;
    token->start = (ys_mark){0, 0, line, column};
    token->end = token->start;
    token->text = ys_message(id);
    return YS_OK; // A malformed wire is a token like any other. The read succeeded.
}

// `ys_wire_read` calls this on a resource failure. The value is a `ys_read_token` return value rather than a
// token. The allocator's failure sets `errno` to ENOMEM. The reader's failure is the value the reader left, and that
// value passes through.
static int ys_wire_resource(ys_wire *reader) {
    reader->is_done = true;
    if (reader->fault == YS_FAILED_MEMORY) {
        errno = ENOMEM;
    }
    return reader->fault;
}

int ys_wire_read(ys_wire *reader, ys_token *token) {
    if (reader->is_done) {
        errno = ENODATA; // A caller reads past the end of the wire or past a fault.
        return YS_FAILED_ACTION;
    }

    size_t size = 0;
    char *line = ys_next_line(reader, &size);
    if (line == NULL) {
        if (reader->fault != 0) {
            return ys_wire_resource(reader);
        }
        reader->is_done = true;
        return YS_FAILED_ACTION; // The wire ended without a fault.
    }

    // A token takes a pair of wire lines. The first line holds the marks. The second line holds the code character and
    // the escaped text. `at` points at the label that `ys_scan` rejected. `ys_wire_error` reports that column.
    ys_mark start = {0, 0, 0, 0};
    const char *labels[] = {"# B: ", ", C: ", ", L: ", ", c: "};
    size_t *marks[] = {&start.byte_offset, &start.char_offset, &start.line, &start.column};
    const char *at = line;
    for (size_t index = 0; index < sizeof(labels) / sizeof(*labels); index++) {
        const char *past = ys_scan(at, labels[index], marks[index]);
        if (past == NULL) {
            return ys_wire_error(reader, token, reader->wire_line, (size_t)(at - line), YS_MESSAGE_WIRE_BAD_POSITION);
        }
        at = past;
    }

    line = ys_next_line(reader, &size);
    if (line == NULL) {
        return reader->fault != 0 ? ys_wire_resource(reader)
                                  : ys_wire_error(reader, token, reader->wire_line, 0, YS_MESSAGE_WIRE_TRUNCATED);
    }
    if (size < 1) {
        return ys_wire_error(reader, token, reader->wire_line, 0, YS_MESSAGE_WIRE_TRUNCATED);
    }
    if (ys_code_of_char(line[0], &token->code) != YS_OK) {
        return ys_wire_error(reader, token, reader->wire_line, 0, YS_MESSAGE_WIRE_BAD_CODE);
    }

    ys_mark end = start;
    size_t fault_at = 0;
    ys_message_id why = YS_MESSAGE_WIRE_BAD_ESCAPE;
    bool wants_characters = token->code != YS_CODE_UNPARSED_INVALID;
    if (ys_unescape(reader, line + 1, size - 1, wants_characters, &end, &fault_at, &why) == NULL) {
        if (reader->fault != 0) {
            return ys_wire_resource(reader); // The reader ran out of memory unescaping the text.
        }
        // The code character is column 0 of this line. The text begins at column 1. `fault_at` counts from the start of
        // the text.
        return ys_wire_error(reader, token, reader->wire_line, 1 + fault_at, why);
    }
    if (end.byte_offset < start.byte_offset || end.char_offset < start.char_offset || end.line < start.line) {
        // The position was a number `strtoul` could read but not a number a token can start at. `ys_unescape` adds the
        // counts of the text to the start, and that sum overflowed `size_t`. The end mark therefore lies before the
        // start mark.
        //
        // This check reports `YS_MESSAGE_WIRE_BAD_POSITION` against the position line.
        return ys_wire_error(reader, token, reader->wire_line - 1, 0, YS_MESSAGE_WIRE_BAD_POSITION);
    }

    // Leave the text NUL-terminated. `ys_wire_read` writes the terminator at index `text_size`. `text_size` does not
    // count the terminator.
    char *terminated = ys_memory_grow(&reader->memory, reader->text, &reader->text_capacity, reader->text_size + 1,
                                      YS_MEMORY_ITEMS, sizeof(char));
    if (terminated == NULL) {
        reader->fault = YS_FAILED_MEMORY;
        return ys_wire_resource(reader);
    }
    reader->text = terminated;
    reader->text[reader->text_size] = '\0';

    // An error's text is its message. The message spans no input. An error's end mark equals its start mark. An error's
    // text is not NULL even when empty. A token of any other code spans the bytes of its text. A marker has no text,
    // and its text is NULL.
    token->start = start;
    token->end = token->code == YS_CODE_ERROR ? start : end;
    token->text = token->code == YS_CODE_ERROR ? reader->text : reader->text_size == 0 ? NULL : reader->text;
    return YS_OK;
}
