// SPDX-License-Identifier: MIT
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

// The yeast wire format: a token is two lines, the first its position and the second its code character followed by its
// escaped text. It lets a token stream be piped between tools, stored, or compared against another parser's, byte for
// byte.

// The character each code is written as. A malformed document is '!', the one error a wire carries; a host failure is
// no token, so no wire character stands for one.
//
// Every one of them is printable, which is what lets '\0' mean "the wire spells nothing for this" without the two ever
// being confused: a line is NUL-terminated, so a code written as one would read back as an empty line rather than as
// that code. `check_wire.py` holds the table to it, so the answer cannot quietly become a character somebody uses.
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
        return '\0'; // UNTESTED — every code the enum names has a character, so only an out-of-range code reaches this
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
    errno = EINVAL; // the character stands for no code
    return YS_FAILED_ACTION;
}

// --- Writing a token. ---

// Whether a whole buffer reached the writer. A short write is a failure: a half-written token is not a token.
bool ys_put(ys_bytes_writer *writer, const char *bytes, size_t size) {
    while (size > 0) {
        ptrdiff_t written = writer->write(writer->context, bytes, size);
        if (written <= 0) {
            return false; // UNTESTED
        }
        bytes += (size_t)written;
        size -= (size_t)written;
    }
    return true;
}

// The codepoint the UTF-8 sequence at `bytes` encodes, and how many bytes it took — or a length of 0 where the text is
// not UTF-8 there. Writing is the one place libyeast needs a codepoint at all: the wire escapes by codepoint, not by
// byte, so it is also the one place that must know the bytes it was handed encode one.
static unsigned long ys_codepoint(const unsigned char *bytes, size_t size, size_t *length) {
    static const unsigned char YS_LEAD_MASK[5] = {0, 0x7Fu, 0x1Fu, 0x0Fu, 0x07u}; // the lead byte's payload bits
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
    // A code the wire spells nothing for is a bad argument, not a token to write: there is no character to say it with.
    // Every code the enum names has one, so this answers only an out-of-range code — which a test cannot hand over
    // without undefined behavior, and so which nothing here covers.
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
    if (!ys_put(writer, buffer, (size_t)written)) {
        return YS_FAILED_STREAM; // UNTESTED
    }

    // The text is escaped by codepoint: printable ASCII other than a backslash stands for itself, and everything else
    // becomes \xXX, \uXXXX or \UXXXXXXXX with lower-case hex, a fixed convention, so token streams compare byte for
    // byte. An error's text is its message, which is not in the input and so spans none of it:
    // its length is its own, and the marks would say zero.
    const unsigned char *text = (const unsigned char *)token.text;
    size_t size = token.text == NULL            ? 0
                  : token.code == YS_CODE_ERROR ? strlen(token.text)
                                                : token.end.byte_offset - token.start.byte_offset;
    // An escape means a codepoint under every code but one, and a byte under YS_CODE_UNPARSED_INVALID, so each holds
    // the other's text to being what it is not. Writing `\x80` for a raw 0x80 under a code that means codepoints says
    // U+0080, and a reader hands back two bytes that were never given — so the text of every other code must encode
    // characters throughout. A run of bytes that encode none is what YS_CODE_UNPARSED_INVALID exists to carry, so its
    // text must encode none of them, or the code is a lie about what the escapes in it mean.
    const bool wants_characters = token.code != YS_CODE_UNPARSED_INVALID;
    for (size_t index = 0; index < size;) {
        size_t length = 1;
        unsigned long codepoint = ys_codepoint(text + index, size - index, &length);
        if ((length == 0) == wants_characters) {
            errno = EINVAL;
            return YS_FAILED_ACTION;
        }
        if (length == 0) {
            // A byte that begins no character: write the byte itself, which is what an escape says under this code.
            int escaped = snprintf(buffer, sizeof(buffer), "\\x%02x", text[index]);
            if (escaped < 0 || !ys_put(writer, buffer, (size_t)escaped)) {
                return YS_FAILED_STREAM; // UNTESTED
            }
            index += 1;
            continue;
        }
        index += length;
        if (codepoint >= ' ' && codepoint <= '~' && codepoint != '\\') {
            char character = (char)codepoint;
            if (!ys_put(writer, &character, 1)) {
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
            if (escaped < 0 || !ys_put(writer, buffer, (size_t)escaped)) {
                return YS_FAILED_STREAM; // UNTESTED
            }
        }
    }
    return ys_put(writer, "\n", 1) ? YS_OK : YS_FAILED_STREAM;
}

// --- Reading a token back. ---

// The wire arm accumulates whole lines and unescapes a token's text into storage of its own — which is why replaying a
// wire is state and parsing YAML into the same tokens is not more of it. Both buffers grow through its ys_memory, so
// ys_options::max_bytes bounds them just as it bounds the parser's. The struct is `wire.h`'s.

void ys_wire_init(ys_wire *wire, ys_memory memory) {
    // The rest is the zeroed state ys_memory_new left; the caller sets ys_wire::source's reader.
    wire->memory = memory;
}

// The next line of the wire, without its newline, or NULL at the end of the stream — or when the source could not be
// read, which sets ys_wire::fault. The line stays valid until the next call, and is NUL-terminated: the
// newline is overwritten with one, and a last line without a newline gets one written past its end, which is the byte
// the source keeps spare. Callers scan it with the string functions, and those read until a NUL, not until a length.
// The next line of the wire, or NULL at its end or on a fault. The search resumes where the last one gave up rather
// than starting over: a line arriving in pieces is filled for once per piece, and rescanning the pieces already looked
// at would cost a long line its length squared — which a wire read from a pipe, the thing the format is for, is made
// of.
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
        reader->scanned = reader->source.size; // every byte here has been looked at, and none of them is the break
        if (reader->source.is_at_end) {
            if (reader->consumed == reader->source.size) {
                return NULL;
            }
            char *line = bytes + reader->consumed;
            *size = reader->source.size - reader->consumed;
            bytes[reader->source.size] = '\0'; // the byte the source keeps spare
            reader->consumed = reader->source.size;
            reader->scanned = reader->consumed;
            reader->wire_line += 1;
            return line;
        }

        // No whole line to hand back: drop the lines already handed back, and read more. Compacting slides everything
        // left by what was dropped, so how far the search has looked slides with it.
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

// The value of `count` hexadecimal digits, if they are all hexadecimal digits. It is unsigned because eight of them
// reach 0xFFFFFFFF, which overflows a signed long wherever a long is 32 bits — which is where MSVC is.
static bool ys_hex(const char *digits, size_t count, unsigned long *value) {
    *value = 0;
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
            return false;
        }
        *value = *value * 16uL + place;
    }
    return true;
}

// Append a codepoint to the reader's text, as UTF-8.
static bool ys_append(ys_wire *reader, unsigned long codepoint) {
    char *grown = ys_memory_grow(&reader->memory, reader->text, &reader->text_capacity, reader->text_size + 4,
                                 YS_MEMORY_ITEMS, sizeof(char));
    if (grown == NULL) {
        reader->fault = YS_FAILED_MEMORY; // a resource fault, told from a malformed wire by ys_wire::fault being set
        return false;
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
    return true;
}

// Unescape a token's text into the reader's own storage, and count the codepoints and the breaks in it, so that the
// token's end can be worked out — the wire records only its start. On a fault it returns false, and reports where in
// `escaped` it was (`fault_at`) and what it was (`why`), so the caller can locate it in the wire.
static bool ys_unescape(ys_wire *reader, const char *escaped, size_t size, ys_mark *end, size_t *fault_at,
                        ys_message_id *why) {
    reader->text_size = 0;
    for (size_t index = 0; index < size;) {
        unsigned long codepoint;
        if (escaped[index] != '\\') {
            // The writer emits a byte raw only when it is printable ASCII and not a backslash; everything else it
            // escapes. So a raw byte outside that range is not the wire format — a high byte most of all, which taken
            // as itself would be a lone continuation or a truncated lead, and put bytes that are not UTF-8 into the
            // reader's own text. The wire is untrusted input, and this is where it is checked.
            unsigned char raw = (unsigned char)escaped[index];
            if (raw < 0x20u || raw > 0x7Eu) {
                *fault_at = index;
                *why = YS_MESSAGE_WIRE_STRAY_BYTE;
                return false;
            }
            codepoint = raw;
            index += 1;
        } else {
            size_t digits = index + 1 < size && escaped[index + 1] == 'x'   ? 2
                            : index + 1 < size && escaped[index + 1] == 'u' ? 4
                            : index + 1 < size && escaped[index + 1] == 'U' ? 8
                                                                            : 0;
            if (digits == 0 || index + 2 + digits > size || !ys_hex(escaped + index + 2, digits, &codepoint) ||
                // A codepoint the escape names but Unicode does not is as much a fault as a digit that is not one:
                // ys_append() would otherwise write bytes that are not UTF-8 into the reader's own text.
                codepoint > 0x10FFFFuL || (codepoint >= 0xD800uL && codepoint <= 0xDFFFuL)) {
                *fault_at = index;
                *why = YS_MESSAGE_WIRE_BAD_ESCAPE;
                return false;
            }
            index += 2 + digits;
        }
        if (!ys_append(reader, codepoint)) {
            return false; // ys_append set ys_wire::fault; the caller tells this resource fault from a malformed wire
        }
        end->char_offset += 1;
        if (codepoint == '\n') {
            end->line += 1;
            end->column = 0;
        } else {
            end->column += 1;
        }
    }
    end->byte_offset += reader->text_size;
    return true;
}

// A labelled number, as the wire writes it: the label, then the digits. Advances past both.
static bool ys_scan(const char **at, const char *label, size_t *value) {
    size_t size = strlen(label);
    if (strncmp(*at, label, size) != 0) {
        return false;
    }
    const char *digits = *at + size;
    if (digits[0] < '0' || digits[0] > '9') {
        return false; // strtoul takes a sign and skips white space; a position on the wire is neither
    }
    // `strtoul` reports a range error through `errno` and cannot be asked any other way, so it must be cleared first —
    // and put back after, because reading a token is a function that reports its failures through the token it returns
    // and leaves `errno` to whatever callback set one. Clobbering a caller's `errno` on the way to succeeding is the
    // one thing this must not do.
    char *after = NULL;
    const int saved = errno;
    errno = 0;
    unsigned long parsed = strtoul(digits, &after, 10);
    const bool ranged = errno != 0;
    errno = saved;
    if (after == digits || ranged) {
        return false;
    }
    *at = after;
    *value = (size_t)parsed;
    return true;
}

// The wire is malformed: the token handed back is a YS_CODE_ERROR, the same code a malformed document is, since a
// malformed wire is bad data like a bad document. The wire is spent after it — one bad token is not to be trusted for
// more. Its marks locate the fault in the wire, `line` (1-based) and `column` (0-based), the byte and codepoint offsets
// left 0 since the wire is the thing at fault and not something parsed from it. Its text is what was wrong.
static int ys_wire_error(ys_wire *reader, ys_token *token, size_t line, size_t column, ys_message_id id) {
    reader->is_done = true;
    token->code = YS_CODE_ERROR;
    token->start = (ys_mark){0, 0, line, column};
    token->end = token->start;
    token->text = ys_message(id);
    return 0;
}

// A resource failure ys_next_line or ys_append hit: not a token, but ys_read_token()'s return value. The allocator's
// failure is ENOMEM; the reader's is whatever it left, passed through.
static int ys_wire_resource(ys_wire *reader) {
    reader->is_done = true;
    if (reader->fault == YS_FAILED_MEMORY) {
        errno = ENOMEM;
    }
    return reader->fault;
}

int ys_wire_read(ys_wire *reader, ys_token *token) {
    if (reader->is_done) {
        errno = ENODATA; // the wire ended, or faulted, and is being read past
        return YS_FAILED_ACTION;
    }

    size_t size = 0;
    char *line = ys_next_line(reader, &size);
    if (line == NULL) {
        if (reader->fault != 0) {
            return ys_wire_resource(reader);
        }
        reader->is_done = true;
        return YS_FAILED_ACTION; // the wire simply ended
    }

    // The first line holds the four marks; the second the code character and the escaped text.
    ys_mark start = {0, 0, 0, 0};
    const char *at = line;
    if (!ys_scan(&at, "# B: ", &start.byte_offset) || !ys_scan(&at, ", C: ", &start.char_offset) ||
        !ys_scan(&at, ", L: ", &start.line) || !ys_scan(&at, ", c: ", &start.column)) {
        return ys_wire_error(reader, token, reader->wire_line, (size_t)(at - line), YS_MESSAGE_WIRE_BAD_POSITION);
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
    if (!ys_unescape(reader, line + 1, size - 1, &end, &fault_at, &why)) {
        if (reader->fault != 0) {
            return ys_wire_resource(reader); // out of memory unescaping, not a malformed wire
        }
        // The code character is column 0 of this line, so the text — where the fault is — begins at column 1.
        return ys_wire_error(reader, token, reader->wire_line, 1 + fault_at, why);
    }
    if (end.byte_offset < start.byte_offset || end.char_offset < start.char_offset || end.line < start.line) {
        // The position was a number `strtoul` could read but not one a token can start at: its own text carries the end
        // of it past where counting stops and back around. A caller told the two marks would hand out a span of nothing
        // or of everything, so this is the position line being wrong rather than the text — the same fault `ys_scan`
        // reports when the number will not fit at all, found one step later.
        return ys_wire_error(reader, token, reader->wire_line - 1, 0, YS_MESSAGE_WIRE_BAD_POSITION);
    }

    // Leave the text NUL-terminated. A leaf token's text is handed out as a span, but an error's is handed out as a
    // string — ys_write_token() takes its length with strlen — so the terminator must be there, and the buffer must
    // exist even when the message is empty. The terminator is past text_size and not counted in it.
    char *terminated = ys_memory_grow(&reader->memory, reader->text, &reader->text_capacity, reader->text_size + 1,
                                      YS_MEMORY_ITEMS, sizeof(char));
    if (terminated == NULL) {
        reader->fault = YS_FAILED_MEMORY;
        return ys_wire_resource(reader);
    }
    reader->text = terminated;
    reader->text[reader->text_size] = '\0';

    // An error's text is its message, which spans none of the input: it ends where it began, however long the message
    // is, and is never NULL even when empty. Everything else spans exactly the text it carries, and a marker carries
    // none.
    token->start = start;
    token->end = token->code == YS_CODE_ERROR ? start : end;
    token->text = token->code == YS_CODE_ERROR ? reader->text : reader->text_size == 0 ? NULL : reader->text;
    return 0;
}
