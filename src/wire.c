// SPDX-License-Identifier: MIT
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
// escaped text. It is what a token stream is compared against the reference parser in, and what lets one be piped
// between tools.

// The character each code is written as. The three failures share '!': a consumer of the wire has no choice to make
// between them, since either unparsed tokens follow or the stream ends, and the message says which it was.
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
    [YS_CODE_ERROR_FORMAT] = '!',
    [YS_CODE_ERROR_MEMORY] = '!',
    [YS_CODE_ERROR_READER] = '!',
    [YS_CODE_UNPARSED] = '-',
    [YS_CODE_DETECTED] = '$',
};

char ys_code_char(ys_code code) {
    if ((size_t)code >= sizeof(YS_WIRE) / sizeof(YS_WIRE[0])) {
        return '?'; // UNTESTED
    }
    return YS_WIRE[code];
}

bool ys_code_of_char(char character, ys_code *code) {
    // '!' stands for all three failures; the format error is the one it reads back as.
    if (character == '!') {
        *code = YS_CODE_ERROR_FORMAT;
        return true;
    }
    for (size_t index = 0; index < sizeof(YS_WIRE) / sizeof(YS_WIRE[0]); index++) {
        if (YS_WIRE[index] == character && YS_WIRE[index] != '\0') {
            *code = (ys_code)index;
            return true;
        }
    }
    return false;
}

// --- Writing a token. ---

// Whether a whole buffer reached the writer. A short write is a failure: a half-written token is not a token.
static bool ys_put(ys_writer *writer, const char *bytes, size_t size) {
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

// The codepoint the UTF-8 sequence at `bytes` encodes, and how many bytes it took. Writing is the one place libyeast
// needs a codepoint at all: the wire escapes by codepoint, not by byte. An ill-formed byte is passed through as itself,
// so that a token holding one still writes something a reader can read back.
static unsigned long ys_codepoint(const unsigned char *bytes, size_t size, size_t *length) {
    unsigned long codepoint = bytes[0];
    size_t wanted = 1;
    if (bytes[0] >= 0xF0u) {
        codepoint = bytes[0] & 0x07u;
        wanted = 4;
    } else if (bytes[0] >= 0xE0u) {
        codepoint = bytes[0] & 0x0Fu;
        wanted = 3;
    } else if (bytes[0] >= 0xC0u) {
        codepoint = bytes[0] & 0x1Fu;
        wanted = 2;
    }
    if (wanted > size) {
        *length = 1;     // UNTESTED
        return bytes[0]; // UNTESTED
    }
    for (size_t index = 1; index < wanted; index++) {
        codepoint = (codepoint << 6) | (bytes[index] & 0x3Fu);
    }
    *length = wanted;
    return codepoint;
}

bool ys_write_token(ys_writer *writer, ys_token token) {
    char buffer[128];
    int written = snprintf(buffer, sizeof(buffer), "# B: %zu, C: %zu, L: %zu, c: %zu\n%c", token.start.byte_offset,
                           token.start.char_offset, token.start.line, token.start.column, ys_code_char(token.code));
    if (written < 0 || (size_t)written >= sizeof(buffer)) {
        return false; // UNTESTED
    }
    if (!ys_put(writer, buffer, (size_t)written)) {
        return false; // UNTESTED
    }

    // The text is escaped by codepoint: printable ASCII other than a backslash stands for itself, and everything else
    // becomes \xXX, \uXXXX or \UXXXXXXXX. An error's text is its message, which is not in the input and so spans none
    // of it: its length is its own, and the marks would say zero.
    const unsigned char *text = (const unsigned char *)token.text;
    size_t size = token.text == NULL             ? 0
                  : ys_is_error_code(token.code) ? strlen(token.text)
                                                 : token.end.byte_offset - token.start.byte_offset;
    for (size_t index = 0; index < size;) {
        size_t length = 1;
        unsigned long codepoint = ys_codepoint(text + index, size - index, &length);
        index += length;
        if (codepoint >= ' ' && codepoint <= '~' && codepoint != '\\') {
            char character = (char)codepoint;
            if (!ys_put(writer, &character, 1)) {
                return false; // UNTESTED
            }
        } else {
            int escaped;
            if (codepoint <= 0xFFuL) {
                escaped = snprintf(buffer, sizeof(buffer), "\\x%02lX", codepoint);
            } else if (codepoint <= 0xFFFFuL) {
                escaped = snprintf(buffer, sizeof(buffer), "\\u%04lX", codepoint);
            } else {
                escaped = snprintf(buffer, sizeof(buffer), "\\U%08lX", codepoint);
            }
            if (escaped < 0 || !ys_put(writer, buffer, (size_t)escaped)) {
                return false; // UNTESTED
            }
        }
    }
    return ys_put(writer, "\n", 1);
}

// --- Reading a token back. ---

// The reader accumulates whole lines, and unescapes a token's text into storage of its own — which is why reading is an
// object and writing is a function. Both buffers grow through its ys_memory, so ys_options::max_bytes bounds them just
// as it bounds the parser's.
struct ys_token_reader {
    ys_memory memory;
    ys_source source;     // the wire's bytes, and the buffer they land in
    size_t consumed;      // how many of them the lines handed back have taken
    size_t wire_line;     // how many lines of the wire have been handed back; the current one is one past it
    char *text;           // the text of the token last read, unescaped
    size_t text_size;     // how many bytes of it there are
    size_t text_capacity; // how many the text buffer holds
    ys_message_id fault;  // the resource failure ys_next_line hit, when has_fault
    bool has_fault;       // ys_next_line could not read more: out of memory, or the reader failed
    bool is_over;         // the wire is spent — it ended, or its fault has been handed back — and yields no more
};

ys_token_reader *ys_new_token_reader(ys_reader reader, const ys_options *options) {
    // The reader is handed over whether or not the token reader can be built, so an owned one is closed here rather
    // than leaked. errno is set after the close and preserved across it, so the close cannot overwrite the reason.
    if (reader.read == NULL) {
        if (reader.close != NULL) {
            reader.close(reader.context);
        }
        errno = EINVAL; // a reader with nothing to read from
        return NULL;
    }
    ys_memory memory;
    ys_token_reader *token_reader = ys_memory_new(&memory, options, sizeof(ys_token_reader)); // sets errno on failure
    if (token_reader == NULL) {
        if (reader.close != NULL) {
            int saved_errno = errno; // the close must not overwrite the reason ys_memory_new gave
            reader.close(reader.context);
            errno = saved_errno;
        }
        return NULL;
    }
    token_reader->memory = memory;
    token_reader->source.reader = reader;
    return token_reader;
}

void ys_free_token_reader(ys_token_reader *reader) {
    if (reader != NULL) {
        if (reader->source.reader.close != NULL) {
            reader->source.reader.close(reader->source.reader.context);
        }
        ys_allocator allocator = reader->memory.allocator;
        ys_source_free(&reader->source, &allocator);
        ys_deallocate(&allocator, reader->text);
        ys_deallocate(&allocator, reader);
    }
}

// The next line of the wire, without its newline, or NULL at the end of the stream — or when the source could not be
// read, which sets ys_token_reader::has_fault. The line stays valid until the next call, and is NUL-terminated: the
// newline is overwritten with one, and a last line without a newline gets one written past its end, which is the byte
// the source keeps spare. Callers scan it with the string functions, and those read until a NUL, not until a length.
static char *ys_next_line(ys_token_reader *reader, size_t *size) {
    for (;;) {
        char *bytes = (char *)reader->source.bytes;
        for (size_t index = reader->consumed; index < reader->source.size; index++) {
            if (bytes[index] == '\n') {
                char *line = bytes + reader->consumed;
                *size = index - reader->consumed;
                bytes[index] = '\0';
                reader->consumed = index + 1;
                reader->wire_line += 1;
                return line;
            }
        }
        if (reader->source.is_at_end) {
            if (reader->consumed == reader->source.size) {
                return NULL;
            }
            char *line = bytes + reader->consumed;
            *size = reader->source.size - reader->consumed;
            bytes[reader->source.size] = '\0'; // the byte the source keeps spare
            reader->consumed = reader->source.size;
            reader->wire_line += 1;
            return line;
        }

        // No whole line to hand back: drop the lines already handed back, and read more.
        ys_fill filled = ys_source_fill(&reader->source, &reader->memory, reader->consumed, 1);
        reader->consumed = 0;
        if (filled == YS_FILL_OUT_OF_MEMORY) {
            reader->fault = YS_MESSAGE_OUT_OF_MEMORY;
            reader->has_fault = true;
            return NULL;
        }
        if (filled == YS_FILL_READER_FAILED) {
            reader->fault = YS_MESSAGE_READER_FAILED;
            reader->has_fault = true;
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
static bool ys_append(ys_token_reader *reader, unsigned long codepoint) {
    char *grown = ys_memory_grow(&reader->memory, reader->text, &reader->text_capacity, reader->text_size + 4,
                                 YS_MEMORY_ITEMS, sizeof(char));
    if (grown == NULL) {
        return false; // ys_unescape names this out of memory, and where in the text it happened
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
static bool ys_unescape(ys_token_reader *reader, const char *escaped, size_t size, ys_mark *end, size_t *fault_at,
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
            *fault_at = index;
            *why = YS_MESSAGE_OUT_OF_MEMORY;
            return false;
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
    char *after = NULL;
    errno = 0;
    unsigned long parsed = strtoul(digits, &after, 10);
    if (after == digits || errno != 0) {
        return false;
    }
    *at = after;
    *value = (size_t)parsed;
    return true;
}

// The wire cannot be read: the token handed back is YS_CODE_WIRE_ERROR — a code no parse emits and no writer produces,
// so it is never mistaken for the error tokens a wire legitimately carries — and it ends the stream. Its marks locate
// the fault in the wire, `line` (1-based) and `column` (0-based), the byte and codepoint offsets left 0 since the wire
// is the thing at fault and not something parsed from it. Its text is what was wrong.
static bool ys_wire_fault(ys_token_reader *reader, ys_token *token, size_t line, size_t column, ys_message_id id) {
    reader->is_over = true;
    token->code = YS_CODE_WIRE_ERROR;
    token->start = (ys_mark){0, 0, line, column};
    token->end = token->start;
    token->text = ys_message(id);
    return true;
}

// The resource fault ys_next_line hit while trying to read the line one past the last one handed back.
static bool ys_reading_fault(ys_token_reader *reader, ys_token *token) {
    return ys_wire_fault(reader, token, reader->wire_line + 1, 0, reader->fault);
}

bool ys_read_token(ys_token_reader *reader, ys_token *token) {
    if (reader->is_over) {
        return false;
    }

    size_t size = 0;
    char *line = ys_next_line(reader, &size);
    if (line == NULL) {
        if (reader->has_fault) {
            return ys_reading_fault(reader, token);
        }
        reader->is_over = true; // the wire simply ended, which is the one thing a false return means
        return false;
    }

    // The first line holds the four marks; the second the code character and the escaped text.
    ys_mark start = {0, 0, 0, 0};
    const char *at = line;
    if (!ys_scan(&at, "# B: ", &start.byte_offset) || !ys_scan(&at, ", C: ", &start.char_offset) ||
        !ys_scan(&at, ", L: ", &start.line) || !ys_scan(&at, ", c: ", &start.column)) {
        return ys_wire_fault(reader, token, reader->wire_line, (size_t)(at - line), YS_MESSAGE_WIRE_BAD_POSITION);
    }

    line = ys_next_line(reader, &size);
    if (line == NULL) {
        return reader->has_fault ? ys_reading_fault(reader, token)
                                 : ys_wire_fault(reader, token, reader->wire_line, 0, YS_MESSAGE_WIRE_TRUNCATED);
    }
    if (size < 1) {
        return ys_wire_fault(reader, token, reader->wire_line, 0, YS_MESSAGE_WIRE_TRUNCATED);
    }
    if (!ys_code_of_char(line[0], &token->code)) {
        return ys_wire_fault(reader, token, reader->wire_line, 0, YS_MESSAGE_WIRE_BAD_CODE);
    }

    ys_mark end = start;
    size_t fault_at = 0;
    ys_message_id why = YS_MESSAGE_WIRE_BAD_ESCAPE;
    if (!ys_unescape(reader, line + 1, size - 1, &end, &fault_at, &why)) {
        // The code character is column 0 of this line, so the text — where the fault is — begins at column 1.
        return ys_wire_fault(reader, token, reader->wire_line, 1 + fault_at, why);
    }

    // Leave the text NUL-terminated. A leaf token's text is handed out as a span, but an error's is handed out as a
    // string — ys_write_token() takes its length with strlen — so the terminator must be there, and the buffer must
    // exist even when the message is empty. The terminator is past text_size and not counted in it.
    char *terminated = ys_memory_grow(&reader->memory, reader->text, &reader->text_capacity, reader->text_size + 1,
                                      YS_MEMORY_ITEMS, sizeof(char));
    if (terminated == NULL) {
        return ys_wire_fault(reader, token, reader->wire_line, 0, YS_MESSAGE_OUT_OF_MEMORY);
    }
    reader->text = terminated;
    reader->text[reader->text_size] = '\0';

    // An error's text is its message, which spans none of the input: it ends where it began, however long the message
    // is, and is never NULL even when empty. Everything else spans exactly the text it carries, and a marker carries
    // none.
    token->start = start;
    token->end = ys_is_error_code(token->code) ? start : end;
    token->text = ys_is_error_code(token->code) ? reader->text : reader->text_size == 0 ? NULL : reader->text;
    return true;
}
