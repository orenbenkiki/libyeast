// SPDX-License-Identifier: MIT
#include "acutest.h"
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <yeast.h>
#ifndef _WIN32
#include <unistd.h>
#endif

static void test_ys_version_matches_components(void) {
    char expected[32];
    int written = snprintf(expected, sizeof(expected), "%d.%d.%d", ys_major(), ys_minor(), ys_patch());
    TEST_CHECK(written > 0 && (size_t)written < sizeof(expected));

    const char *version = ys_version();
    TEST_CHECK(version != NULL);
    if (version != NULL) {
        TEST_CHECK(strcmp(version, expected) == 0);
        TEST_MSG("ys_version()=\"%s\" components=\"%s\"", version, expected);
    }
}

// The parser is not implemented yet: any pull yields a "not implemented" error at the first character. It is not a
// failure the parse halts on — a malformed document never is — so every pull yields it again.
static void test_string_parser(void) {
    const char *input = "hello: world\n";
    ys_parser *parser = ys_new_string_parser(input, strlen(input), NULL);
    TEST_ASSERT(parser != NULL);
    TEST_CHECK(ys_are_tokens_stable(parser)); // string input: text points into the caller's buffer

    ys_token token = ys_next_token(parser);
    TEST_CHECK(token.code == YS_CODE_ERROR_FORMAT);
    TEST_CHECK(token.text != NULL && strcmp(token.text, "not implemented") == 0);
    TEST_CHECK(token.start.byte_offset == 0 && token.start.char_offset == 0);
    TEST_CHECK(token.start.line == 0 && token.start.column == 0);
    TEST_CHECK(token.end.byte_offset == token.start.byte_offset);

    ys_token again = ys_next_token(parser);
    TEST_CHECK(again.code == YS_CODE_ERROR_FORMAT); // and again, there being nothing else it can do yet

    ys_free_parser(parser);
}

// A stream parser routed through the counting allocator, which lets the test assert nothing leaked.
static void test_stream_parser(void) {
    ys_counting_allocator *counter = ys_new_counting_allocator();
    TEST_ASSERT(counter != NULL);
    ys_options options = {ys_counting_allocator_functions(counter), YS_RESUME_NONE, 0};

    FILE *file = tmpfile();
    TEST_ASSERT(file != NULL);
    ys_parser *parser = ys_new_stream_parser(ys_fp_reader(file, YS_OWN), &options);
    TEST_ASSERT(parser != NULL);
    TEST_CHECK(!ys_are_tokens_stable(parser)); // stream input: text is valid only until the next call
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);

    TEST_CHECK(ys_next_token(parser).code == YS_CODE_ERROR_FORMAT);

    ys_free_parser(parser);                                       // YS_OWN closes the file, then the parser is freed
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 0); // no leak
    ys_free_counting_allocator(counter);
}

static bool is_closed;

static void note_close(void *context) {
    (void)context;
    is_closed = true;
    errno = EIO; // a close can fail, and its errno must not overwrite the reason construction failed
}

static void *failing_allocate(void *context, size_t size) {
    (void)context;
    (void)size;
    errno = ENOMEM; // an allocator that returns NULL must set errno; libyeast passes it through
    return NULL;
}

// An allocator that refuses reports ENOMEM, and the constructor passes it through rather than inventing its own.
static void test_alloc_failure(void) {
    ys_allocator allocator = {failing_allocate, NULL, NULL, NULL};
    ys_options options = {allocator, YS_RESUME_NONE, 0};

    errno = 0;
    TEST_CHECK(ys_new_string_parser("x", 1, &options) == NULL);
    TEST_CHECK(errno == ENOMEM);

    errno = 0;
    TEST_CHECK(ys_new_stream_parser(ys_fp_reader(stdin, YS_BORROW), &options) == NULL);
    TEST_CHECK(errno == ENOMEM);
}

// A bad argument is EINVAL, told apart from a memory failure: a string parser given a NULL buffer with a length, and a
// stream parser or token reader given a reader with nothing to read from.
static void test_bad_arguments(void) {
    errno = 0;
    TEST_CHECK(ys_new_string_parser(NULL, 5, NULL) == NULL);
    TEST_CHECK(errno == EINVAL);

    ys_reader empty = {NULL, NULL, NULL}; // no read callback
    errno = 0;
    TEST_CHECK(ys_new_stream_parser(empty, NULL) == NULL);
    TEST_CHECK(errno == EINVAL);
    errno = 0;
    TEST_CHECK(ys_new_token_reader(empty, NULL) == NULL);
    TEST_CHECK(errno == EINVAL);

    // A bad reader is handed over too, so an owned one is closed even when it is the reason for the failure — and the
    // EINVAL survives the close, which sets its own errno.
    ys_reader owned = {NULL, note_close, NULL}; // no read callback, but an owned resource to close
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_stream_parser(owned, NULL) == NULL);
    TEST_CHECK(is_closed && errno == EINVAL);
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_token_reader(owned, NULL) == NULL);
    TEST_CHECK(is_closed && errno == EINVAL);

    // A NULL buffer with no length is an empty input, not a mistake.
    ys_parser *parser = ys_new_string_parser(NULL, 0, NULL);
    TEST_CHECK(parser != NULL);
    ys_free_parser(parser);
}

static void test_free_null(void) {
    ys_free_parser(NULL);             // no-op
    ys_free_counting_allocator(NULL); // no-op
    TEST_CHECK(true);
}

static void test_fp_reader(void) {
    FILE *file = tmpfile();
    TEST_ASSERT(file != NULL);
    if (file != NULL) {
        TEST_ASSERT(fwrite("hi", 1, 2, file) == 2);
        TEST_ASSERT(fseek(file, 0, SEEK_SET) == 0);

        ys_reader reader = ys_fp_reader(file, YS_OWN);
        char buffer[8];
        TEST_CHECK(reader.read(reader.context, buffer, sizeof(buffer)) == 2);
        TEST_CHECK(memcmp(buffer, "hi", 2) == 0);
        TEST_CHECK(reader.read(reader.context, buffer, sizeof(buffer)) == 0); // end of input
        reader.close(reader.context);
    }

    ys_reader borrowed = ys_fp_reader(stdin, YS_BORROW);
    TEST_CHECK(borrowed.close == NULL); // YS_BORROW leaves the stream alone
}

#ifndef _WIN32
static void test_fd_reader(void) {
    int fds[2];
    TEST_ASSERT(pipe(fds) == 0);
    TEST_ASSERT(write(fds[1], "hi", 2) == 2);
    TEST_ASSERT(close(fds[1]) == 0); // closing the write end makes the read end report end of input

    ys_reader reader = ys_fd_reader(fds[0], YS_OWN);
    char buffer[8];
    TEST_CHECK(reader.read(reader.context, buffer, sizeof(buffer)) == 2);
    TEST_CHECK(memcmp(buffer, "hi", 2) == 0);
    TEST_CHECK(reader.read(reader.context, buffer, sizeof(buffer)) == 0); // end of input
    reader.close(reader.context);

    ys_reader borrowed = ys_fd_reader(-1, YS_BORROW);
    TEST_CHECK(borrowed.close == NULL); // YS_BORROW leaves the descriptor alone
}
#endif

static void test_counting_allocator(void) {
    ys_counting_allocator *counter = ys_new_counting_allocator();
    TEST_ASSERT(counter != NULL);
    ys_allocator allocator = ys_counting_allocator_functions(counter);

    void *first = allocator.allocate(allocator.context, 16);
    TEST_CHECK(first != NULL);
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);

    first = allocator.reallocate(allocator.context, first, 32); // resize: same buffer, count unchanged
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);

    void *second = allocator.reallocate(allocator.context, NULL, 8); // NULL pointer: a fresh allocation
    TEST_CHECK(second != NULL);
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 2);

    TEST_CHECK(allocator.reallocate(allocator.context, second, 0) == NULL); // size 0: frees, returns NULL
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);

    allocator.deallocate(allocator.context, first);
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 0);

    ys_free_counting_allocator(counter);
}

// Every code has a wire character, and reading it back gives the code again — except that the three failures share
// '!', so all three read back as the format error, which is what the wire means by it.
static void test_wire_codes(void) {
    for (int code = YS_CODE_BOM; code <= YS_CODE_DETECTED; code++) {
        char character = ys_code_char((ys_code)code);
        TEST_CHECK(character != '?');
        TEST_MSG("code %d has no wire character", code);

        ys_code read_back;
        TEST_CHECK(ys_code_of_char(character, &read_back));
        if (code == YS_CODE_ERROR_MEMORY || code == YS_CODE_ERROR_READER) {
            TEST_CHECK(character == '!');
            TEST_CHECK(read_back == YS_CODE_ERROR_FORMAT); // the wire tells the three failures apart by their message
        } else {
            TEST_CHECK(read_back == (ys_code)code);
            TEST_MSG("code %d wrote '%c', which reads back as %d", code, character, read_back);
        }
    }
    ys_code code;
    TEST_CHECK(!ys_code_of_char('~', &code)); // not a wire character
}

// A sink and a source over one buffer, so that the wire tests need no file — and so that they exercise the ys_writer
// and ys_reader abstractions rather than the FILE* adapters, which have tests of their own.
typedef struct wire_buffer {
    char bytes[4096];
    size_t size;
    size_t offset;
} wire_buffer;

static ptrdiff_t wire_write(void *context, const char *bytes, size_t size) {
    wire_buffer *wire = context;
    if (wire->size + size > sizeof(wire->bytes)) {
        return -1; // UNTESTED
    }
    memcpy(wire->bytes + wire->size, bytes, size);
    wire->size += size;
    return (ptrdiff_t)size;
}

static ptrdiff_t wire_read(void *context, char *bytes, size_t size) {
    wire_buffer *wire = context;
    size_t left = wire->size - wire->offset;
    size_t taken = size < left ? size : left;
    memcpy(bytes, wire->bytes + wire->offset, taken);
    wire->offset += taken;
    return (ptrdiff_t)taken;
}

// A token written to the wire and read back is the same token — its code, its marks, and its text.
static void test_wire_round_trip(void) {
    // The wire records only a token's start, so its end is worked out from that start and its text — every component of
    // it, not only the byte offset. `characters` and `breaks` are what the text holds, and `column` is where it leaves
    // the token when it holds a break, the count having started over.
    static const struct {
        const char *name;
        ys_code code;
        const char *text;
        size_t size;
        size_t characters;
        size_t breaks;
        size_t column;
    } cases[] = {
        {"an indicator", YS_CODE_INDICATOR, "-", 1, 1, 0, 0},
        {"a zero-width marker", YS_CODE_BEGIN_SCALAR, NULL, 0, 0, 0, 0},
        {"content", YS_CODE_TEXT, "hello", 5, 5, 0, 0},
        {"a backslash, which must be escaped", YS_CODE_TEXT, "a\\b", 3, 3, 0, 0},
        {"a line break, which must be escaped", YS_CODE_LINE_FEED, "\n", 1, 1, 1, 0},
        {"two lines and what follows them", YS_CODE_TEXT, "a\nb\ncd", 6, 6, 2, 2},
        {"two-byte non-ASCII", YS_CODE_TEXT, "\xC3\xA9", 2, 1, 0, 0},             // U+00E9
        {"three-byte non-ASCII", YS_CODE_TEXT, "\xE4\xB8\x80", 3, 1, 0, 0},       // U+4E00
        {"beyond the basic plane", YS_CODE_TEXT, "\xF0\x9F\x98\x80", 4, 1, 0, 0}, // U+1F600
    };
    const size_t count = sizeof(cases) / sizeof(cases[0]);

    wire_buffer wire = {{0}, 0, 0};
    ys_writer writer = {wire_write, NULL, &wire};
    for (size_t index = 0; index < count; index++) {
        ys_token token;
        token.code = cases[index].code;
        token.start = (ys_mark){10 + index, 20 + index, 30 + index, 40 + index};
        token.end = token.start;
        token.end.byte_offset += cases[index].size;
        token.text = cases[index].text;
        TEST_CHECK(ys_write_token(&writer, token));
        TEST_MSG("%s: could not be written", cases[index].name);
    }
    ys_close_writer(&writer);

    ys_reader reader = {wire_read, NULL, &wire};
    ys_token_reader *tokens = ys_new_token_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);
    for (size_t index = 0; index < count; index++) {
        ys_token token;
        TEST_ASSERT(ys_read_token(tokens, &token));
        TEST_CHECK(token.code == cases[index].code);
        TEST_MSG("%s: read back as code %d", cases[index].name, token.code);
        TEST_CHECK(token.start.byte_offset == 10 + index);
        TEST_CHECK(token.start.char_offset == 20 + index);
        TEST_CHECK(token.start.line == 30 + index);
        TEST_CHECK(token.start.column == 40 + index);
        TEST_CHECK(token.end.byte_offset == token.start.byte_offset + cases[index].size);
        TEST_CHECK(token.end.char_offset == token.start.char_offset + cases[index].characters);
        TEST_CHECK(token.end.line == token.start.line + cases[index].breaks);
        size_t column = cases[index].breaks > 0 ? cases[index].column : token.start.column + cases[index].characters;
        TEST_CHECK(token.end.column == column);
        TEST_MSG("%s: it ends at line %zu column %zu", cases[index].name, token.end.line, token.end.column);
        TEST_CHECK((token.text == NULL) == (cases[index].text == NULL));
        if (token.text != NULL && cases[index].text != NULL) {
            TEST_CHECK(memcmp(token.text, cases[index].text, cases[index].size) == 0);
            TEST_MSG("%s: the text came back different", cases[index].name);
        }
    }

    ys_token token;
    TEST_CHECK(!ys_read_token(tokens, &token)); // the stream is exhausted
    ys_free_token_reader(tokens);
}

// A source that hands out so many bytes and then reports a failure, rather than an end.
typedef struct drip_source {
    const char *bytes;
    size_t good; // how many it hands out before it fails
    size_t offset;
} drip_source;

static ptrdiff_t drip_source_read(void *context, char *bytes, size_t size) {
    drip_source *source = context;
    if (source->offset == source->good) {
        return -1;
    }
    size_t left = source->good - source->offset;
    size_t take = size < left ? size : left;
    memcpy(bytes, source->bytes + source->offset, take);
    source->offset += take;
    return (ptrdiff_t)take;
}

// A reader is handed over to the constructor, so an owned one is the caller's no longer. If the parser cannot be built,
// the caller is left with nothing to free it with — so the constructor closes it itself. And the memory failure's
// ENOMEM survives the close, which set its own errno.
static void test_owned_reader_is_closed_when_construction_fails(void) {
    ys_options tiny = {{NULL, NULL, NULL, NULL}, YS_RESUME_NONE, 8}; // smaller than either object
    wire_buffer wire = {{0}, 0, 0};
    ys_reader reader = {wire_read, note_close, &wire}; // it is never read from: there is nothing to read it into

    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_stream_parser(reader, &tiny) == NULL);
    TEST_CHECK(is_closed);       // the parser could not be built, and closed what it had been given
    TEST_CHECK(errno == ENOMEM); // the cap was too small, and the close's EIO did not overwrite that

    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_token_reader(reader, &tiny) == NULL);
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == ENOMEM);
}

// An error's text is its message, which is not in the input and spans none of it. So the wire cannot take its length
// from the marks, which say zero — and reading it back must not take the marks from its length.
static void test_wire_round_trips_an_error(void) {
    const char *message = "inside production 'ns-plain', expected ':' or a line break";

    wire_buffer wire = {{0}, 0, 0};
    ys_writer writer = {wire_write, NULL, &wire};
    ys_token written;
    written.code = YS_CODE_ERROR_FORMAT;
    written.start = (ys_mark){7, 7, 1, 3};
    written.end = written.start; // an error consumes nothing
    written.text = message;
    TEST_CHECK(ys_write_token(&writer, written));
    ys_close_writer(&writer);

    TEST_CHECK(strstr(wire.bytes, message) != NULL); // the message reached the wire, and is not merely a bare '!'

    ys_reader reader = {wire_read, NULL, &wire};
    ys_token_reader *tokens = ys_new_token_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token read;
    TEST_ASSERT(ys_read_token(tokens, &read));
    TEST_CHECK(read.code == YS_CODE_ERROR_FORMAT);
    TEST_CHECK(read.text != NULL && strncmp(read.text, message, strlen(message)) == 0);
    TEST_CHECK(read.start.byte_offset == 7 && read.start.line == 1 && read.start.column == 3);
    TEST_CHECK(read.end.byte_offset == read.start.byte_offset); // and it still consumes nothing
    TEST_CHECK(read.end.line == read.start.line && read.end.column == read.start.column);

    ys_free_token_reader(tokens);
}

// An error's text is handed out as a string, not a span, so it must be NUL-terminated — the writer takes its length
// with strlen. A message that fills the reader's buffer exactly is where a missing terminator reads off the end, and
// where the writer would then overread; the round-trip through the writer is what a sanitizer watches.
static void test_wire_error_text_is_terminated(void) {
    wire_buffer wire = {{0}, 0, 0};
    // Four four-byte codepoints unescape to sixteen bytes, a size the growth lands on exactly.
    const char *written = "# B: 0, C: 0, L: 0, c: 0\n!\\U0001F600\\U0001F600\\U0001F600\\U0001F600\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_reader reader = {wire_read, NULL, &wire};
    ys_token_reader *tokens = ys_new_token_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token));
    TEST_CHECK(token.code == YS_CODE_ERROR_FORMAT);
    TEST_CHECK(strlen(token.text) == 16); // reads to the terminator and no further

    wire_buffer out = {{0}, 0, 0};
    ys_writer writer = {wire_write, NULL, &out};
    TEST_CHECK(ys_write_token(&writer, token)); // the writer strlen's the message, and must not overread

    ys_free_token_reader(tokens);
}

// A bare error carries an empty message, not a missing one: its text is "", never NULL.
static void test_wire_empty_error_text(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *written = "# B: 3, C: 3, L: 0, c: 3\n!\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_reader reader = {wire_read, NULL, &wire};
    ys_token_reader *tokens = ys_new_token_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token));
    TEST_CHECK(token.code == YS_CODE_ERROR_FORMAT);
    TEST_CHECK(token.text != NULL && token.text[0] == '\0'); // empty, but there

    ys_free_token_reader(tokens);
}

// A reader that fails partway is a fault of the wire, not a stream that ended: a YS_CODE_WIRE_ERROR token, distinct
// from the YS_CODE_ERROR_READER token a wire legitimately carries.
static void test_wire_reader_failure(void) {
    drip_source source = {"# B: 0, C: 0, L: 0, c: 0\nThello\n", 8, 0}; // it hands out 8 bytes, then fails
    ys_reader reader = {drip_source_read, NULL, &source};
    ys_token_reader *tokens = ys_new_token_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_CHECK(ys_read_token(tokens, &token));
    TEST_CHECK(token.code == YS_CODE_WIRE_ERROR);
    TEST_CHECK(token.text != NULL);
    TEST_CHECK(!ys_read_token(tokens, &token)); // and nothing follows it

    ys_free_token_reader(tokens);
}

// A reader that fails after the position line, while the token line is being read, is a fault located on that next
// line — a different path through the reader than a failure on the position line itself.
static void test_wire_reader_failure_mid_token(void) {
    drip_source source = {"# B: 0, C: 0, L: 0, c: 0\nThello\n", 25, 0}; // hands out the position line, then fails
    ys_reader reader = {drip_source_read, NULL, &source};
    ys_token_reader *tokens = ys_new_token_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_CHECK(ys_read_token(tokens, &token));
    TEST_CHECK(token.code == YS_CODE_WIRE_ERROR);
    TEST_CHECK(token.start.line == 2); // the token line, the second line of the wire, is where it could not read
    TEST_CHECK(!ys_read_token(tokens, &token));

    ys_free_token_reader(tokens);
}

// A wire error token carries a code that a wire never legitimately holds, so the reader's own trouble is never mistaken
// for the error tokens the wire replays — and its marks locate the fault, so a caller can point at where in the wire.
static void test_wire_error_is_located(void) {
    wire_buffer wire = {{0}, 0, 0};
    // A valid token, then a bad escape on the fourth line, at the sixth character (code 'T', then `ab`, then `\q`).
    const char *written = "# B: 0, C: 0, L: 0, c: 0\nTok\n# B: 2, C: 2, L: 0, c: 2\nTab\\q\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_reader reader = {wire_read, NULL, &wire};
    ys_token_reader *tokens = ys_new_token_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token)); // the good token first, a content token, not a wire error
    TEST_CHECK(token.code == YS_CODE_TEXT);

    TEST_ASSERT(ys_read_token(tokens, &token));
    TEST_CHECK(token.code == YS_CODE_WIRE_ERROR);
    TEST_CHECK(token.start.line == 4);        // the fourth line of the wire
    TEST_CHECK(token.start.column == 3);      // 'T' at column 0, 'a' 1, 'b' 2, the '\' of the bad escape at 3
    TEST_CHECK(token.start.byte_offset == 0); // the wire is at fault, so the parsed-input offsets are 0
    TEST_CHECK(token.end.line == token.start.line && token.end.column == token.start.column); // it spans nothing
    TEST_CHECK(strstr(token.text, "escape") != NULL);
    TEST_CHECK(!ys_read_token(tokens, &token)); // and nothing follows it

    ys_free_token_reader(tokens);
}

// A wire that is not the yeast wire format is rejected, not misread, and each way of being broken says which.
static void test_wire_rejects_rubbish(void) {
    static const struct {
        const char *wire;
        const char *reason; // a word the message must contain
    } cases[] = {
        {"not a token at all\n", "position"},
        {"# B: 0, C: 0, L: 0, c: 0\n", "no token after it"},                    // a position with no token after it
        {"# B: 0, C: 0, L: 0, c: 0\n\n", "no token after it"},                  // an empty token line
        {"# B: nonsense\nT\n", "position"},                                     // a position that does not parse
        {"# B: 0, C: 0, L: 0, c: 0\n\xFF\n", "code"},                           // a code character that is not one
        {"# B: 0, C: 0, L: 0, c: 0\nT\\q\n", "escape"},                         // an escape that is not one
        {"# B: 0, C: 0, L: 0, c: 0\nT\\x2\n", "escape"},                        // an escape cut short
        {"# B: 0, C: 0, L: 0, c: 0\nT\\xZZ\n", "escape"},                       // an escape whose digits are not digits
        {"# B: 0, C: 0, L: 0, c: 0\nT\\uD800\n", "escape"},                     // an escape naming half a surrogate
        {"# B: 0, C: 0, L: 0, c: 0\nT\\U00110000\n", "escape"},                 // an escape past the last codepoint
        {"# B: 0, C: 0, L: 0, c: 0\nT\xC3\xA9\n", "printable"},                 // a raw byte outside printable ASCII
        {"# B: -1, C: 0, L: 0, c: 0\nThello\n", "position"},                    // a position that is not a position
        {"# B: 99999999999999999999999999, C: 0, L: 0, c: 0\nT\n", "position"}, // a position too large to be one
    };

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        wire_buffer wire = {{0}, 0, 0};
        wire.size = strlen(cases[index].wire);
        memcpy(wire.bytes, cases[index].wire, wire.size);

        ys_reader reader = {wire_read, NULL, &wire};
        ys_token_reader *tokens = ys_new_token_reader(reader, NULL);
        TEST_ASSERT(tokens != NULL);

        ys_token token;
        TEST_CHECK(ys_read_token(tokens, &token)); // the fault is a token, so the caller cannot miss it
        TEST_MSG("case %zu was accepted: %s", index, cases[index].wire);
        TEST_CHECK(token.code == YS_CODE_WIRE_ERROR);
        TEST_CHECK(token.text != NULL && strstr(token.text, cases[index].reason) != NULL);
        TEST_MSG("case %zu said: %s", index, token.text);
        TEST_CHECK(!ys_read_token(tokens, &token)); // and nothing follows it

        ys_free_token_reader(tokens);
    }
}

// The FILE* writer adapter: what it writes comes back, and YS_BORROW leaves the stream alone.
static void test_fp_writer(void) {
    FILE *file = tmpfile();
    TEST_ASSERT(file != NULL);
    if (file != NULL) {
        ys_writer writer = ys_fp_writer(file, YS_OWN);
        TEST_CHECK(writer.write(writer.context, "hi", 2) == 2);
        TEST_ASSERT(fflush(file) == 0);
        TEST_ASSERT(fseek(file, 0, SEEK_SET) == 0);

        char buffer[8];
        TEST_CHECK(fread(buffer, 1, sizeof(buffer), file) == 2);
        TEST_CHECK(memcmp(buffer, "hi", 2) == 0);
        ys_close_writer(&writer);
        TEST_CHECK(writer.close == NULL); // closing twice is not closing twice
        ys_close_writer(&writer);
    }

    ys_writer borrowed = ys_fp_writer(stdout, YS_BORROW);
    TEST_CHECK(borrowed.close == NULL); // YS_BORROW leaves the stream alone
    ys_close_writer(&borrowed);
}

// The file-descriptor writer adapter, over a pipe: what goes in one end comes out the other.
#ifndef _WIN32
static void test_fd_writer(void) {
    int ends[2];
    TEST_ASSERT(pipe(ends) == 0);

    ys_writer writer = ys_fd_writer(ends[1], YS_OWN);
    TEST_CHECK(writer.write(writer.context, "hi", 2) == 2);
    ys_close_writer(&writer); // the reader sees the end of input only once the write end is closed

    char buffer[8];
    TEST_CHECK(read(ends[0], buffer, sizeof(buffer)) == 2);
    TEST_CHECK(memcmp(buffer, "hi", 2) == 0);
    TEST_CHECK(read(ends[0], buffer, sizeof(buffer)) == 0); // end of input
    TEST_CHECK(close(ends[0]) == 0);

    ys_writer borrowed = ys_fd_writer(1, YS_BORROW);
    TEST_CHECK(borrowed.close == NULL); // YS_BORROW leaves the descriptor alone
}
#endif

// A wire stream whose last line has no newline still yields its token, and a reader that owns its source closes it.
static void test_wire_odds_and_ends(void) {
    FILE *file = tmpfile();
    TEST_ASSERT(file != NULL);
    if (file != NULL) {
        TEST_ASSERT(fputs("# B: 1, C: 2, L: 3, c: 4\nI-", file) >= 0); // no trailing newline
        TEST_ASSERT(fseek(file, 0, SEEK_SET) == 0);

        ys_token_reader *tokens = ys_new_token_reader(ys_fp_reader(file, YS_OWN), NULL);
        TEST_ASSERT(tokens != NULL);
        ys_token token;
        TEST_CHECK(ys_read_token(tokens, &token));
        TEST_CHECK(token.code == YS_CODE_INDICATOR);
        TEST_CHECK(token.start.byte_offset == 1 && token.start.column == 4);
        TEST_CHECK(!ys_read_token(tokens, &token)); // and then the stream is over
        ys_free_token_reader(tokens);               // which closes the file it was given to own
    }

    ys_free_token_reader(NULL); // freeing nothing is not an error
}

// A reader held under a cap it cannot meet reports failure rather than growing past it — and a cap it cannot even be
// built under is refused outright, rather than built and then useless.
static void test_wire_memory_cap(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *line = "# B: 0, C: 0, L: 0, c: 0\nThello\n";
    wire.size = strlen(line);
    memcpy(wire.bytes, line, wire.size);
    ys_reader reader = {wire_read, NULL, &wire};

    ys_options tiny = {{NULL, NULL, NULL, NULL}, YS_RESUME_NONE, 8}; // smaller than the reader itself
    TEST_CHECK(ys_new_token_reader(reader, &tiny) == NULL);

    ys_options capped = {{NULL, NULL, NULL, NULL}, YS_RESUME_NONE, 512}; // room for the reader, none for a buffer
    ys_token_reader *tokens = ys_new_token_reader(reader, &capped);
    TEST_ASSERT(tokens != NULL);

    // It cannot buffer a line, and it says so — a fault of reading the wire, not a stream that was empty all along.
    ys_token token;
    TEST_CHECK(ys_read_token(tokens, &token));
    TEST_CHECK(token.code == YS_CODE_WIRE_ERROR);
    TEST_CHECK(token.text != NULL && strstr(token.text, "memory") != NULL);
    TEST_CHECK(!ys_read_token(tokens, &token)); // and nothing follows it

    ys_free_token_reader(tokens);
}

// An allocator that lets so many buffers be made and refuses the next, so that a failure of the second one — the
// reader's text — is reachable without having to guess the size of the reader itself.
static size_t buffers_left;

static void *counted_reallocate(void *context, void *pointer, size_t size) {
    (void)context;
    if (buffers_left == 0) {
        errno = ENOMEM; // an allocator that returns NULL must set errno
        return NULL;
    }
    buffers_left--;
    return realloc(pointer, size);
}

// The reader's text buffer is under the same cap as its line buffer, and its failure is reported the same way.
static void test_wire_text_out_of_memory(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *written = "# B: 0, C: 0, L: 0, c: 0\nThello\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    buffers_left = 1; // enough for the line buffer, and nothing left for the text
    ys_allocator allocator = {NULL, counted_reallocate, NULL, NULL};
    ys_options options = {allocator, YS_RESUME_NONE, 0};
    ys_reader reader = {wire_read, NULL, &wire};
    ys_token_reader *tokens = ys_new_token_reader(reader, &options);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_CHECK(ys_read_token(tokens, &token)); // the line was read; its text could not be unescaped into anything
    TEST_CHECK(token.code == YS_CODE_WIRE_ERROR);
    TEST_CHECK(token.text != NULL && strstr(token.text, "memory") != NULL);
    TEST_CHECK(!ys_read_token(tokens, &token));

    ys_free_token_reader(tokens);
}

// Even a token with no text is left NUL-terminated, which is one allocation — and when that is the one the cap refuses,
// the reader says out of memory rather than handing back a token whose empty text points at nothing.
static void test_wire_terminator_out_of_memory(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *written = "# B: 0, C: 0, L: 0, c: 0\nS\n"; // a begin-scalar marker: a code, no text
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    buffers_left = 1; // enough for the line buffer, and nothing left for the terminator
    ys_allocator allocator = {NULL, counted_reallocate, NULL, NULL};
    ys_options options = {allocator, YS_RESUME_NONE, 0};
    ys_reader reader = {wire_read, NULL, &wire};
    ys_token_reader *tokens = ys_new_token_reader(reader, &options);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_CHECK(ys_read_token(tokens, &token));
    TEST_CHECK(token.code == YS_CODE_WIRE_ERROR);
    TEST_CHECK(token.text != NULL && strstr(token.text, "memory") != NULL);

    ys_free_token_reader(tokens);
}

// A source of `left` identical token records, made as they are read, so that the reader must refill its line buffer
// many times over without the whole stream ever being in memory at once.
typedef struct wire_stream {
    size_t left;     // the records still to come
    char record[40]; // the one being handed out
    size_t size;     // how long it is
    size_t offset;   // how much of it has gone
} wire_stream;

static ptrdiff_t wire_stream_read(void *context, char *bytes, size_t size) {
    wire_stream *stream = context;
    if (stream->offset == stream->size) {
        if (stream->left == 0) {
            return 0;
        }
        stream->left--;
        int written = snprintf(stream->record, sizeof(stream->record), "# B: 0, C: 0, L: 0, c: 0\nThello\n");
        TEST_ASSERT(written > 0 && (size_t)written < sizeof(stream->record));
        stream->size = (size_t)written;
        stream->offset = 0;
    }
    size_t left = stream->size - stream->offset;
    size_t take = size < left ? size : left;
    memcpy(bytes, stream->record + stream->offset, take);
    stream->offset += take;
    return (ptrdiff_t)take;
}

// The line buffer is reused, not grown: the reader discards the lines it has handed back, and only grows when what is
// left really does fill it. So a long stream of short lines reads under a cap that a buffer growing once per refill
// would pass in a moment — and it is a stream, so the cap is the whole point.
static void test_wire_long_stream(void) {
    wire_stream stream = {2000, {0}, 0, 0};
    ys_options options = {{NULL, NULL, NULL, NULL}, YS_RESUME_NONE, 16384};
    ys_reader reader = {wire_stream_read, NULL, &stream};
    ys_token_reader *tokens = ys_new_token_reader(reader, &options);
    TEST_ASSERT(tokens != NULL);

    size_t count = 0;
    ys_token token;
    while (ys_read_token(tokens, &token)) {
        TEST_ASSERT(token.code == YS_CODE_TEXT);
        count++;
    }
    TEST_CHECK(count == 2000);
    TEST_MSG("read %zu of 2000 tokens", count);

    ys_free_token_reader(tokens);
}

// The wire is written with lower-case hexadecimal, but a stream written by another hand may use upper-case, and it
// means the same thing.
static void test_wire_reads_upper_case_hex(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *written = "# B: 0, C: 0, L: 0, c: 0\nT\\xE9\\u4E00\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_reader reader = {wire_read, NULL, &wire};
    ys_token_reader *tokens = ys_new_token_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token));
    TEST_CHECK(token.code == YS_CODE_TEXT);
    TEST_CHECK(token.end.byte_offset == 5); // two bytes for U+00E9, three for U+4E00
    TEST_CHECK(memcmp(token.text, "\xC3\xA9\xE4\xB8\x80", 5) == 0);
    ys_free_token_reader(tokens);
}

TEST_LIST = {
    {"ys_version_matches_components", test_ys_version_matches_components},
    {"string_parser", test_string_parser},
    {"stream_parser", test_stream_parser},
    {"wire_codes", test_wire_codes},
    {"wire_round_trip", test_wire_round_trip},
    {"wire_round_trips_an_error", test_wire_round_trips_an_error},
    {"wire_reader_failure", test_wire_reader_failure},
    {"wire_reader_failure_mid_token", test_wire_reader_failure_mid_token},
    {"wire_error_is_located", test_wire_error_is_located},
    {"wire_error_text_is_terminated", test_wire_error_text_is_terminated},
    {"wire_empty_error_text", test_wire_empty_error_text},
    {"wire_rejects_rubbish", test_wire_rejects_rubbish},
    {"wire_odds_and_ends", test_wire_odds_and_ends},
    {"wire_memory_cap", test_wire_memory_cap},
    {"wire_long_stream", test_wire_long_stream},
    {"wire_text_out_of_memory", test_wire_text_out_of_memory},
    {"wire_terminator_out_of_memory", test_wire_terminator_out_of_memory},
    {"wire_reads_upper_case_hex", test_wire_reads_upper_case_hex},
    {"alloc_failure", test_alloc_failure},
    {"bad_arguments", test_bad_arguments},
    {"owned_reader_is_closed_when_construction_fails", test_owned_reader_is_closed_when_construction_fails},
    {"free_null", test_free_null},
    {"fp_reader", test_fp_reader},
    {"fp_writer", test_fp_writer},
#ifndef _WIN32
    {"fd_reader", test_fd_reader},
    {"fd_writer", test_fd_writer},
#endif
    {"counting_allocator", test_counting_allocator},
    {NULL, NULL},
};
