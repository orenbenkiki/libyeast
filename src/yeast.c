// SPDX-License-Identifier: MIT
#include "alloc.h"
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <io.h>
#define YS_OS_READ _read
#define YS_OS_WRITE _write
#define YS_OS_CLOSE _close
#else
#include <unistd.h>
#define YS_OS_READ read
#define YS_OS_WRITE write
#define YS_OS_CLOSE close
#endif

// Version components injected by the build system (CMake) from the one source of truth: project(yeast VERSION ...). The
// 0.0.0 fallback lets out-of-build tooling parse this file; a binary actually built at 0.0.0 refuses to load (below).
#ifndef YS_VERSION_MAJOR
#define YS_VERSION_MAJOR 0
#endif
#ifndef YS_VERSION_MINOR
#define YS_VERSION_MINOR 0
#endif
#ifndef YS_VERSION_PATCH
#define YS_VERSION_PATCH 0
#endif

// Compose "MAJOR.MINOR.PATCH" from the components at preprocess time.
#define YS_STRINGIFY_(x) #x
#define YS_STRINGIFY(x) YS_STRINGIFY_(x)
#define YS_VERSION_STRING                                                                                              \
    YS_STRINGIFY(YS_VERSION_MAJOR) "." YS_STRINGIFY(YS_VERSION_MINOR) "." YS_STRINGIFY(YS_VERSION_PATCH)

#if defined(__GNUC__) || defined(__clang__)
// Refuse to load a library built without a real version (all components zero).
__attribute__((constructor)) static void ys_assert_version(void) {
    if (YS_VERSION_MAJOR == 0 && YS_VERSION_MINOR == 0 && YS_VERSION_PATCH == 0) {
        (void)fputs("libyeast: built without a version number; refusing to load\n", stderr); // UNTESTED
        abort();                                                                             // UNTESTED
    }
}
#endif

const char *ys_version(void) {
    return YS_VERSION_STRING;
}

int ys_major(void) {
    return YS_VERSION_MAJOR;
}

int ys_minor(void) {
    return YS_VERSION_MINOR;
}

int ys_patch(void) {
    return YS_VERSION_PATCH;
}

// --- Counting allocator: a malloc/free wrapper that counts live allocations, for leak checking. ---

struct ys_counting_allocator {
    size_t live_buffers;
};

static void *ys_counting_allocate(void *context, size_t size) {
    void *pointer = malloc(size);
    if (pointer != NULL) {
        ((ys_counting_allocator *)context)->live_buffers++;
    }
    return pointer;
}

static void ys_counting_deallocate(void *context, void *pointer) {
    if (pointer != NULL) {
        ((ys_counting_allocator *)context)->live_buffers--;
    }
    free(pointer);
}

static void *ys_counting_reallocate(void *context, void *pointer, size_t size) {
    // Handle the edge cases explicitly instead of leaving realloc's implementation-defined size==0 behavior to skew
    // the count: size 0 frees, a NULL pointer allocates, and a genuine resize keeps the count (realloc frees the old
    // block and returns the new one itself).
    if (size == 0) {
        ys_counting_deallocate(context, pointer);
        return NULL;
    } else if (pointer == NULL) {
        return ys_counting_allocate(context, size);
    } else {
        return realloc(pointer, size);
    }
}

ys_counting_allocator *ys_new_counting_allocator(void) {
    ys_counting_allocator *counter = malloc(sizeof(*counter));
    if (counter != NULL) {
        counter->live_buffers = 0;
    }
    return counter;
}

ys_allocator ys_counting_allocator_functions(ys_counting_allocator *counter) {
    ys_allocator allocator;
    allocator.allocate = ys_counting_allocate;
    allocator.reallocate = ys_counting_reallocate;
    allocator.deallocate = ys_counting_deallocate;
    allocator.context = counter;
    return allocator;
}

size_t ys_counting_allocator_live_buffers(const ys_counting_allocator *counter) {
    return counter->live_buffers;
}

void ys_free_counting_allocator(ys_counting_allocator *counter) {
    free(counter);
}

// --- Reader adapters. The fd/FILE* is stashed in the reader context; ownership picks whether close is wired up. ---

static ptrdiff_t ys_fd_read(void *context, char *buffer, size_t size) {
    // read()/_read() report their result in a signed type (ssize_t / int), so a single call can transfer at most
    // INT_MAX bytes — far more than any real read. Cap the request there so neither the count nor the return overflows.
    unsigned int capped = size > (unsigned int)INT_MAX ? (unsigned int)INT_MAX : (unsigned int)size;
    return (ptrdiff_t)YS_OS_READ((int)(intptr_t)context, buffer, capped);
}

static void ys_fd_close(void *context) {
    YS_OS_CLOSE((int)(intptr_t)context);
}

ys_reader ys_fd_reader(int fd, ys_ownership ownership) {
    ys_reader reader;
    reader.read = ys_fd_read;
    reader.close = ownership == YS_OWN ? ys_fd_close : NULL;
    reader.context = (void *)(intptr_t)fd;
    return reader;
}

static ptrdiff_t ys_fp_read(void *context, char *buffer, size_t size) {
    FILE *file = context;
    size_t read_count = fread(buffer, 1, size, file);
    if (read_count == 0 && ferror(file)) {
        return -1; // UNTESTED
    } else {
        return (ptrdiff_t)read_count;
    }
}

static void ys_fp_close(void *context) {
    (void)fclose(context); // a read stream's close error is not actionable here
}

ys_reader ys_fp_reader(FILE *file, ys_ownership ownership) {
    ys_reader reader;
    reader.read = ys_fp_read;
    reader.close = ownership == YS_OWN ? ys_fp_close : NULL;
    reader.context = file;
    return reader;
}

// --- The yeast wire format. ---

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

// --- Writers. ---

static ptrdiff_t ys_fd_write(void *context, const char *buffer, size_t size) {
    unsigned int capped = size > (unsigned int)INT_MAX ? (unsigned int)INT_MAX : (unsigned int)size;
    return (ptrdiff_t)YS_OS_WRITE((int)(intptr_t)context, buffer, capped);
}

ys_writer ys_fd_writer(int fd, ys_ownership ownership) {
    ys_writer writer;
    writer.write = ys_fd_write;
    writer.close = ownership == YS_OWN ? ys_fd_close : NULL;
    writer.context = (void *)(intptr_t)fd;
    return writer;
}

static ptrdiff_t ys_fp_write(void *context, const char *buffer, size_t size) {
    size_t written = fwrite(buffer, 1, size, context);
    if (written < size) {
        return -1; // UNTESTED
    }
    return (ptrdiff_t)written;
}

static void ys_fp_write_close(void *context) {
    (void)fclose(context);
}

ys_writer ys_fp_writer(FILE *file, ys_ownership ownership) {
    ys_writer writer;
    writer.write = ys_fp_write;
    writer.close = ownership == YS_OWN ? ys_fp_write_close : NULL;
    writer.context = file;
    return writer;
}

void ys_close_writer(ys_writer *writer) {
    if (writer->close != NULL) {
        writer->close(writer->context);
        writer->close = NULL;
    }
}

// --- Writing a token in the yeast wire format. ---

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
    // becomes \xXX, \uXXXX or \UXXXXXXXX.
    const unsigned char *text = (const unsigned char *)token.text;
    size_t size = token.text == NULL ? 0 : token.end.byte_offset - token.start.byte_offset;
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

// --- Reading a token in the yeast wire format. ---

// The reader accumulates whole lines, and unescapes a token's text into storage of its own — which is why reading is an
// object and writing is a function.
struct ys_token_reader {
    ys_allocator allocator;
    size_t max_bytes;
    ys_reader reader;
    char *buffer;      // the bytes read but not yet consumed
    size_t readable;   // how many of them there are
    size_t offset;     // how many have been consumed
    size_t capacity;   // how many the buffer holds
    char *text;        // the text of the token last read, unescaped
    size_t text_size;  // how many bytes of it there are
    size_t text_space; // how many the text buffer holds
    bool is_at_end;    // the source is exhausted
};

// Whether `size` more bytes may be allocated, given what the reader already holds.
static bool ys_within(const ys_token_reader *reader, size_t wanted) {
    if (reader->max_bytes == 0) {
        return true;
    }
    return sizeof(*reader) + reader->capacity + reader->text_space + wanted <= reader->max_bytes;
}

// Grow a buffer to hold `wanted` bytes, carrying over the `used` that are in it. Returns NULL if the cap or the
// allocator refuses, leaving the old buffer alone.
static char *ys_grow(ys_token_reader *reader, char *buffer, size_t *space, size_t used, size_t wanted) {
    if (*space >= wanted) {
        return buffer;
    }
    size_t doubled = *space * 2 > wanted ? *space * 2 : wanted;
    if (!ys_within(reader, doubled - *space)) {
        return NULL;
    }
    char *grown = ys_allocate(&reader->allocator, doubled);
    if (grown == NULL) {
        return NULL; // UNTESTED
    }
    for (size_t index = 0; index < used; index++) {
        grown[index] = buffer[index];
    }
    ys_deallocate(&reader->allocator, buffer);
    *space = doubled;
    return grown;
}

ys_token_reader *ys_new_token_reader(ys_reader reader, const ys_options *options) {
    ys_options resolved = options != NULL ? *options : (ys_options){0};
    ys_token_reader *token_reader = ys_allocate(&resolved.allocator, sizeof(*token_reader));
    if (token_reader != NULL) {
        token_reader->allocator = resolved.allocator;
        token_reader->max_bytes = resolved.max_bytes;
        token_reader->reader = reader;
        token_reader->buffer = NULL;
        token_reader->readable = 0;
        token_reader->offset = 0;
        token_reader->capacity = 0;
        token_reader->text = NULL;
        token_reader->text_size = 0;
        token_reader->text_space = 0;
        token_reader->is_at_end = false;
    }
    return token_reader;
}

void ys_free_token_reader(ys_token_reader *reader) {
    if (reader != NULL) {
        if (reader->reader.close != NULL) {
            reader->reader.close(reader->reader.context);
        }
        ys_deallocate(&reader->allocator, reader->buffer);
        ys_deallocate(&reader->allocator, reader->text);
        ys_deallocate(&reader->allocator, reader);
    }
}

// The next line of the source, without its newline, or NULL at the end of the stream. The line stays valid until the
// next call, and is NUL-terminated: the newline is overwritten with one, and a last line without a newline gets one
// written past its end — which is why the buffer always keeps a byte in hand. Callers scan it with the string
// functions, and those read until a NUL, not until a length.
static char *ys_next_line(ys_token_reader *reader, size_t *size) {
    for (;;) {
        for (size_t index = reader->offset; index < reader->readable; index++) {
            if (reader->buffer[index] == '\n') {
                char *line = reader->buffer + reader->offset;
                *size = index - reader->offset;
                reader->buffer[index] = '\0';
                reader->offset = index + 1;
                return line;
            }
        }
        if (reader->is_at_end) {
            if (reader->offset == reader->readable) {
                return NULL;
            }
            char *line = reader->buffer + reader->offset;
            *size = reader->readable - reader->offset;
            reader->buffer[reader->readable] = '\0'; // the byte the buffer keeps in hand
            reader->offset = reader->readable;
            return line;
        }

        // Nothing to hand back yet: drop what has been consumed, make room, and read more.
        for (size_t index = reader->offset; index < reader->readable; index++) {
            reader->buffer[index - reader->offset] = reader->buffer[index];
        }
        reader->readable -= reader->offset;
        reader->offset = 0;

        size_t wanted = reader->capacity == 0 ? 4096 : reader->capacity * 2;
        if (wanted <= reader->readable) {
            wanted = reader->readable + 2; // UNTESTED
        } // UNTESTED
        char *grown = ys_grow(reader, reader->buffer, &reader->capacity, reader->readable, wanted);
        if (grown == NULL) {
            return NULL;
        }
        reader->buffer = grown;

        // One byte of the buffer is never read into: it is where a last line without a newline gets its terminator.
        ptrdiff_t read_count = reader->reader.read(reader->reader.context, reader->buffer + reader->readable,
                                                   reader->capacity - reader->readable - 1);
        if (read_count <= 0) {
            reader->is_at_end = true;
        } else {
            reader->readable += (size_t)read_count;
        }
    }
}

// The value of `count` hexadecimal digits, or -1 if they are not all hexadecimal digits.
static long ys_hex(const char *digits, size_t count) {
    long value = 0;
    for (size_t index = 0; index < count; index++) {
        char digit = digits[index];
        long place;
        if (digit >= '0' && digit <= '9') {
            place = digit - '0';
        } else if (digit >= 'A' && digit <= 'F') {
            place = digit - 'A' + 10;
        } else if (digit >= 'a' && digit <= 'f') {
            place = digit - 'a' + 10;
        } else {
            return -1;
        }
        value = value * 16 + place;
    }
    return value;
}

// Append a codepoint to the reader's text, as UTF-8.
static bool ys_append(ys_token_reader *reader, unsigned long codepoint) {
    char *grown = ys_grow(reader, reader->text, &reader->text_space, reader->text_size, reader->text_size + 4);
    if (grown == NULL) {
        return false; // UNTESTED
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
// token's end can be worked out — the wire records only its start.
static bool ys_unescape(ys_token_reader *reader, const char *escaped, size_t size, ys_mark *end) {
    reader->text_size = 0;
    for (size_t index = 0; index < size;) {
        unsigned long codepoint;
        if (escaped[index] != '\\') {
            codepoint = (unsigned char)escaped[index];
            index += 1;
        } else {
            size_t digits = index + 1 < size && escaped[index + 1] == 'x'   ? 2
                            : index + 1 < size && escaped[index + 1] == 'u' ? 4
                            : index + 1 < size && escaped[index + 1] == 'U' ? 8
                                                                            : 0;
            if (digits == 0 || index + 2 + digits > size) {
                return false;
            }
            long value = ys_hex(escaped + index + 2, digits);
            if (value < 0) {
                return false;
            }
            codepoint = (unsigned long)value;
            index += 2 + digits;
        }
        if (!ys_append(reader, codepoint)) {
            return false; // UNTESTED
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

bool ys_read_token(ys_token_reader *reader, ys_token *token) {
    size_t size = 0;
    char *line = ys_next_line(reader, &size);
    if (line == NULL) {
        return false;
    }

    // The first line holds the four marks; the second the code character and the escaped text.
    ys_mark start = {0, 0, 0, 0};
    const char *at = line;
    if (!ys_scan(&at, "# B: ", &start.byte_offset) || !ys_scan(&at, ", C: ", &start.char_offset) ||
        !ys_scan(&at, ", L: ", &start.line) || !ys_scan(&at, ", c: ", &start.column)) {
        return false;
    }

    line = ys_next_line(reader, &size);
    if (line == NULL || size < 1 || !ys_code_of_char(line[0], &token->code)) {
        return false;
    }

    ys_mark end = start;
    if (!ys_unescape(reader, line + 1, size - 1, &end)) {
        return false;
    }
    token->start = start;
    token->end = end;
    token->text = reader->text_size == 0 ? NULL : reader->text;
    return true;
}
