// SPDX-License-Identifier: MIT
// The C library's tests, over the surface a caller sees. That is the token sources and sinks, the readers and
// writers, and the allocator and the version. The tests cover what that surface refuses and what it does.

#include "acutest.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <yeast.h>
#ifndef _WIN32
#include <dirent.h>
#include <unistd.h>
#endif

// A `TEST_ASSERT` that the static analyzers trust. acutest's `TEST_ASSERT` aborts the test on a false condition.
// Neither clang-tidy nor cppcheck can see that it does. Past a `TEST_ASSERT(pointer != NULL)` they still treat the
// pointer as possibly-NULL, and warn at the next use.
//
// The trailing abort() states the same fact in a form they read. Both know abort() does not return. `TEST_ASSERT` has
// already aborted by then, and the abort() itself does not run. The whole macro is a single source line. It stays
// covered with no // UNTESTED guard to write. The macro evaluates `cond` twice. `cond` must have no side effects.
#define TEST_ASSERT_HINT(cond)                                                                                         \
    do {                                                                                                               \
        TEST_ASSERT(cond);                                                                                             \
        if (!(cond)) {                                                                                                 \
            abort();                                                                                                   \
        }                                                                                                              \
    } while (0)

// The version string is the components joined with full stops.
static void test_ys_version_matches_components(void) {
    char expected[32];
    int written = snprintf(expected, sizeof(expected), "%d.%d.%d", ys_major(), ys_minor(), ys_patch());
    TEST_CHECK(written > 0 && (size_t)written < sizeof(expected));

    const char *version = ys_version();
    TEST_CHECK(version != NULL);
    if (version != NULL) {
        TEST_CHECK(strcmp(version, expected) == 0);
        TEST_MSG("`ys_version` gave \"%s\" and the components say \"%s\"", version, expected);
    }
}

// The parser does not exist. A read yields a "not implemented" error at the first character. The parse continues
// past that error, the way it continues past a malformed document. A further read yields the error again. The reader
// answers `YS_OK` and hands back the token.
static void test_yaml_memory_parser(void) {
    const char *input = "hello: world\n";
    ys_token_source *source = ys_new_yaml_memory_parser(input, strlen(input), NULL);
    TEST_ASSERT(source != NULL);
    TEST_CHECK(ys_are_tokens_stable(source)); // memory input. text points into the caller's buffer

    ys_token token;
    TEST_CHECK(ys_read_token(source, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_ERROR);
    TEST_CHECK(token.text != NULL && strcmp(token.text, "not implemented") == 0);
    TEST_CHECK(token.start.byte_offset == 0 && token.start.char_offset == 0);
    TEST_CHECK(token.start.line == 0 && token.start.column == 0);
    TEST_CHECK(token.end.byte_offset == token.start.byte_offset);

    ys_token again;
    TEST_CHECK(ys_read_token(source, &again) == YS_OK);
    TEST_CHECK(again.code == YS_CODE_ERROR); // and again, there being nothing else it can do yet

    ys_delete_token_source(source);
}

// The test routes a stream parser through the counting allocator. The test can then assert nothing leaked.
static void test_yaml_stream_parser(void) {
    ys_counting_allocator *counter = ys_new_counting_allocator();
    TEST_ASSERT(counter != NULL);
    ys_options options = {ys_counting_allocator_functions(counter), YS_RESUME_NONE, 0};

    FILE *file = tmpfile();
    TEST_ASSERT(file != NULL);
    ys_token_source *source = ys_new_yaml_stream_parser(ys_fp_reader(file, YS_OWN), &options);
    TEST_ASSERT(source != NULL);
    TEST_CHECK(!ys_are_tokens_stable(source)); // stream input. text is valid until the next call
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);

    ys_token token;
    TEST_CHECK(ys_read_token(source, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_ERROR);

    ys_delete_token_source(source);                               // YS_OWN closes the file, then the source is freed
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 0); // no leak
    ys_delete_counting_allocator(counter);
}

static bool is_closed; // the flag `note_close` sets. A test reads it back to see the close happened.

// A close that records it ran and then fails. The failure lets a test see whose errno a caller gets.
static int note_close(void *context) {
    (void)context;
    is_closed = true;
    errno = EIO; // a close can fail. its errno must not overwrite why construction failed
    return -1;
}

// An allocate that refuses any size a caller asks for. It reaches the out-of-memory path without exhausting the
// machine.
static void *failing_allocate(void *context, size_t size) {
    (void)context;
    (void)size;
    errno = ENOMEM; // an allocator that returns NULL must set errno; libyeast passes it through
    return NULL;
}

// An allocator that refuses reports ENOMEM. The constructor passes that through rather than inventing an errno.
static void test_alloc_failure(void) {
    ys_allocator allocator = {failing_allocate, NULL, NULL, NULL, NULL};
    ys_options options = {allocator, YS_RESUME_NONE, 0};

    errno = 0;
    TEST_CHECK(ys_new_yaml_memory_parser("x", 1, &options) == NULL);
    TEST_CHECK(errno == ENOMEM);

    errno = 0;
    TEST_CHECK(ys_new_yaml_stream_parser(ys_fp_reader(stdin, YS_BORROW), &options) == NULL);
    TEST_CHECK(errno == ENOMEM);
}

// A bad argument is EINVAL. That status tells such a case apart from a memory failure. A string parser given a NULL
// buffer with a length is such a case. So is a stream parser or token reader given a reader with nothing to read from.
static void test_bad_arguments(void) {
    errno = 0;
    TEST_CHECK(ys_new_yaml_memory_parser(NULL, 5, NULL) == NULL);
    TEST_CHECK(errno == EINVAL);

    ys_bytes_reader empty = {NULL, NULL, NULL}; // no read callback
    errno = 0;
    TEST_CHECK(ys_new_yaml_stream_parser(empty, NULL) == NULL);
    TEST_CHECK(errno == EINVAL);
    errno = 0;
    TEST_CHECK(ys_new_yeast_stream_reader(empty, NULL) == NULL);
    TEST_CHECK(errno == EINVAL);

    // A bad reader is handed over too. An owned reader is closed even when that reader failed. The EINVAL survives the
    // close, which sets its own errno.
    ys_bytes_reader owned = {NULL, note_close, NULL}; // no read callback, but an owned resource to close
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_yaml_stream_parser(owned, NULL) == NULL);
    TEST_CHECK(is_closed && errno == EINVAL);
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_yeast_stream_reader(owned, NULL) == NULL);
    TEST_CHECK(is_closed && errno == EINVAL);

    // A NULL buffer with no length is an empty input, not a mistake.
    ys_token_source *parser = ys_new_yaml_memory_parser(NULL, 0, NULL);
    TEST_CHECK(parser != NULL);
    ys_delete_token_source(parser);
}

// Deleting NULL is a no-op rather than a crash. The deleters a caller has behave the same way.
static void test_free_null(void) {
    ys_delete_token_source(NULL);       // no-op
    ys_delete_counting_allocator(NULL); // no-op
    TEST_CHECK(true);
}

// A read that reports the end of the input straight away. That is how a test feeds in an empty document.
static ptrdiff_t read_nothing(void *context, char *buffer, size_t size) {
    (void)context;
    (void)buffer;
    (void)size;
    return 0; // an empty input, which is a valid one
}

// Freeing reports its reader's close. A buffered reader surfaces a failure at its close. A free returns
// `YS_FAILED_STREAM` with the close's errno rather than swallowing the failure. The default allocator has no close. The
// reader can fail here and the allocator cannot.
static void test_free_reports_close_failure(void) {
    ys_bytes_reader owned = {read_nothing, note_close, NULL}; // its close fails with EIO

    ys_token_source *parser = ys_new_yaml_stream_parser(owned, NULL);
    TEST_ASSERT(parser != NULL);
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_delete_token_source(parser) == YS_FAILED_STREAM); // the reader's close failed
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == EIO); // and its reason survives
    TEST_MSG("freeing a parser left errno at %d rather than EIO", errno);

    ys_token_source *tokens = ys_new_yeast_stream_reader(owned, NULL);
    TEST_ASSERT(tokens != NULL);
    ys_token token;
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION); // an empty wire ends at once
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_delete_token_source(tokens) == YS_FAILED_STREAM);
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == EIO);
    TEST_MSG("freeing a token reader left errno at %d rather than EIO", errno);
}

// An allocator close that fails with a recognisable errno. A test then sees which close the library reported.
static int allocator_close_fails(void *context) {
    (void)context;
    errno = ENOSPC;
    return -1;
}

// A free can report a pair of failures beyond the reader failure. A failing allocator close is `YS_FAILED_MEMORY`.
// Both together are `YS_FAILED_BOTH`. There errno holds the reader's reason. The reader failed before the allocator
// did. The return says the allocator failed as well. The test uses the counting allocator. The test swaps that
// allocator's close for a failing close. The library really gives the memory back before the close fails.
static void test_free_reports_both_closes(void) {
    ys_counting_allocator *counter = ys_new_counting_allocator();
    TEST_ASSERT(counter != NULL);
    ys_allocator allocator = ys_counting_allocator_functions(counter);
    allocator.close = allocator_close_fails;
    ys_options options = {allocator, YS_RESUME_NONE, 0};

    // A string parser has no reader to close. Only the allocator's close can fail. That is `YS_FAILED_MEMORY`, and
    // its errno stands.
    ys_token_source *string = ys_new_yaml_memory_parser("a: 1\n", 5, &options); // not-prose: The literal is YAML input.
    TEST_ASSERT(string != NULL);
    errno = 0;
    TEST_CHECK(ys_delete_token_source(string) == YS_FAILED_MEMORY);
    TEST_CHECK(errno == ENOSPC);
    TEST_MSG("the allocator's close left errno at %d rather than ENOSPC", errno);

    // A stream parser with a failing reader close and this allocator fails both ways. That is `YS_FAILED_BOTH`. errno
    // is the reader's, the first one.
    ys_bytes_reader owned = {read_nothing, note_close, NULL}; // its close fails with EIO
    ys_token_source *stream = ys_new_yaml_stream_parser(owned, &options);
    TEST_ASSERT(stream != NULL);
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_delete_token_source(stream) == YS_FAILED_BOTH);
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == EIO); // the reader's. both failed, and it is the first
    TEST_MSG("both closes failing left errno at %d rather than EIO", errno);

    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 0); // both frees gave everything back
    ys_delete_counting_allocator(counter);
}

// A reader over a `FILE *` hands back what a test wrote to it. The reader also closes a file it owns.
static void test_fp_reader(void) {
    FILE *file = tmpfile();
    TEST_ASSERT(file != NULL);
    if (file != NULL) {
        TEST_ASSERT(fwrite("hi", 1, 2, file) == 2);
        TEST_ASSERT(fseek(file, 0, SEEK_SET) == 0);

        ys_bytes_reader reader = ys_fp_reader(file, YS_OWN);
        char buffer[8];
        TEST_CHECK(reader.read(reader.context, buffer, sizeof(buffer)) == 2);
        TEST_CHECK(memcmp(buffer, "hi", 2) == 0);
        TEST_CHECK(reader.read(reader.context, buffer, sizeof(buffer)) == 0); // end of input
        reader.close(reader.context);
    }

    ys_bytes_reader borrowed = ys_fp_reader(stdin, YS_BORROW);
    TEST_CHECK(borrowed.close == NULL); // YS_BORROW leaves the stream alone
}

#ifndef _WIN32
// A reader over a file descriptor. A pipe feeds it. Closing the write end marks the end of the input.
static void test_fd_reader(void) {
    int fds[2];
    TEST_ASSERT(pipe(fds) == 0);
    TEST_ASSERT(write(fds[1], "hi", 2) == 2);
    TEST_ASSERT(close(fds[1]) == 0); // closing the write end makes the read end report end of input

    ys_bytes_reader reader = ys_fd_reader(fds[0], YS_OWN);
    char buffer[8];
    TEST_CHECK(reader.read(reader.context, buffer, sizeof(buffer)) == 2);
    TEST_CHECK(memcmp(buffer, "hi", 2) == 0);
    TEST_CHECK(reader.read(reader.context, buffer, sizeof(buffer)) == 0); // end of input
    reader.close(reader.context);

    ys_bytes_reader borrowed = ys_fd_reader(-1, YS_BORROW);
    TEST_CHECK(borrowed.close == NULL); // YS_BORROW leaves the descriptor alone
}
#endif

// The counting allocator counts what is live across the callbacks. The close then reports a leak.
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

    // The close ys_counting_allocator_functions() installs, called directly. `first` is still live. The close reports
    // the leak as an allocator failure. That is ENOMEM, over the memory the counter still holds.
    errno = 0;
    TEST_CHECK(ys_close_counting_allocator(counter) !=
               0); // close(2)'s contract. nonzero is the failure, not -1 exactly
    TEST_CHECK(errno == ENOMEM);

    allocator.deallocate(allocator.context, first);
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 0);

    TEST_CHECK(ys_close_counting_allocator(counter) == 0); // nothing live now. it is content

    ys_delete_counting_allocator(counter);
}

// A single counter serving more than a single parser. The installed close would fire on the first free, while the
// second parser's buffers are still legitimately live. So a shared counter sets close to NULL. A test checks the
// counter by hand once the parsers are gone. The count means nothing until then.
static void test_counting_allocator_shared(void) {
    ys_counting_allocator *counter = ys_new_counting_allocator();
    TEST_ASSERT(counter != NULL);
    ys_allocator allocator = ys_counting_allocator_functions(counter);
    allocator.close = NULL; // reused. the freeing of one parser does not check it
    ys_options options = {allocator, YS_RESUME_NONE, 0};

    ys_token_source *first = ys_new_yaml_memory_parser("a: 1\n", 5, &options);  // not-prose: The literal is YAML input.
    ys_token_source *second = ys_new_yaml_memory_parser("b: 2\n", 5, &options); // not-prose: The literal is YAML input.
    TEST_ASSERT(first != NULL && second != NULL);
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 2); // both live at once

    TEST_CHECK(ys_delete_token_source(first) ==
               YS_OK); // no close fired. the other's live buffers were not read as a leak
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);
    TEST_CHECK(ys_delete_token_source(second) == YS_OK);

    TEST_CHECK(ys_close_counting_allocator(counter) == 0); // now, and only now, the count is meant to be zero
    ys_delete_counting_allocator(counter);
}

// A code has a wire character. Reading that character back gives the code again. `YS_CODE_ERROR` is the wire's '!'. It
// reads back as `YS_CODE_ERROR`.
static void test_wire_codes(void) {
    for (int code = YS_CODE_BOM; code <= YS_CODE_DETECTED; code++) {
        char character = ys_code_char((ys_code)code);
        TEST_CHECK(character != '\0');
        TEST_MSG("code %d has no wire character", code);

        ys_code read_back;
        TEST_CHECK(ys_code_of_char(character, &read_back) == YS_OK);
        TEST_CHECK(read_back == (ys_code)code);
        TEST_MSG("code %d wrote '%c' and that reads back as %d", code, character, read_back);
    }
    ys_code code;
    errno = 0;
    TEST_CHECK(ys_code_of_char('@', &code) == YS_FAILED_ACTION); // not a wire character
    TEST_CHECK(errno == EINVAL);
    TEST_CHECK(ys_code_of_char('\0', &code) ==
               YS_FAILED_ACTION); // and nothing reads back the "writes nothing" sentinel

    // Each code the enum names has a wire character. A malformed document is the '!' of `YS_CODE_ERROR`. A host failure
    // is no code at all. So ys_code_char()'s '\0', and the write it refuses, answer an out-of-range code alone. Such a
    // code cannot be handed over from a test without undefined behavior.
}

// A sink and a source over a single buffer. The wire tests then want no file. They exercise the `ys_bytes_writer` and
// `ys_bytes_reader` abstractions rather than the FILE* adapters. The adapters have tests of their own.
typedef struct wire_buffer {
    char bytes[4096];
    size_t size;
    size_t offset;
} wire_buffer;

// A write that appends into a fixed buffer. A test collects a wire there and reads it back.
static ptrdiff_t wire_write(void *context, const char *bytes, size_t size) {
    wire_buffer *wire = context;
    if (wire->size + size > sizeof(wire->bytes)) {
        return -1; // UNTESTED
    }
    memcpy(wire->bytes + wire->size, bytes, size);
    wire->size += size;
    return (ptrdiff_t)size;
}

// The read side of that buffer. The source hands back what the sink collected until it runs out.
static ptrdiff_t wire_read(void *context, char *bytes, size_t size) {
    wire_buffer *wire = context;
    size_t left = wire->size - wire->offset;
    size_t taken = size < left ? size : left;
    memcpy(bytes, wire->bytes + wire->offset, taken);
    wire->offset += taken;
    return (ptrdiff_t)taken;
}

// A token written to the wire and read back is the same token. That is the code, the marks, and the text.
static void test_wire_round_trip(void) {
    // The wire records a token's start alone. Its end is worked out from that start and its text. Each component of
    // the end is, not the byte offset alone. `characters` and `breaks` count what the text holds. `column` is where the
    // text leaves the token when it holds a break, the count having started over.
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
        {"a backslash the writer must escape", YS_CODE_TEXT, "a\\b", 3, 3, 0, 0},
        {"a line break the writer must escape", YS_CODE_LINE_FEED, "\n", 1, 1, 1, 0},
        {"a pair of lines and what follows them", YS_CODE_TEXT, "a\nb\ncd", 6, 6, 2, 2},
        {"two-byte non-ASCII", YS_CODE_TEXT, "\xC3\xA9", 2, 1, 0, 0},             // U+00E9
        {"three-byte non-ASCII", YS_CODE_TEXT, "\xE4\xB8\x80", 3, 1, 0, 0},       // U+4E00
        {"beyond the basic plane", YS_CODE_TEXT, "\xF0\x9F\x98\x80", 4, 1, 0, 0}, // U+1F600
    };
    const size_t count = sizeof(cases) / sizeof(cases[0]);

    wire_buffer wire = {{0}, 0, 0};
    ys_token_sink *writer = ys_new_yeast_stream_writer((ys_bytes_writer){wire_write, NULL, &wire}, NULL);
    for (size_t index = 0; index < count; index++) {
        ys_token token;
        token.code = cases[index].code;
        token.start = (ys_mark){10 + index, 20 + index, 30 + index, 40 + index};
        token.end = token.start;
        token.end.byte_offset += cases[index].size;
        token.text = cases[index].text;
        TEST_CHECK(ys_write_token(writer, token) == YS_OK);
        TEST_MSG("%s: could not be written", cases[index].name);
    }
    ys_delete_token_sink(writer);

    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);
    for (size_t index = 0; index < count; index++) {
        ys_token token;
        TEST_ASSERT(ys_read_token(tokens, &token) == YS_OK);
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
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION); // the stream is exhausted
    ys_delete_token_source(tokens);
}

// The wire escapes by codepoint. The wire has no form for a byte that is not a codepoint. The writer refuses a token
// holding such a byte. An answer holding that byte would read back as different bytes.
static void test_wire_refuses_ill_formed_text(void) {
    static const struct {
        const char *name;
        const char *text;
        size_t size;
        bool throughout; // whether each byte of it begins no character. UNPARSED_INVALID may hold such text
    } cases[] = {
        {"a lone continuation byte", "\x80", 1, true},
        {"an overlong encoding of '/'", "\xC0\xAF", 2, true},
        {"a surrogate UTF-8 cannot encode", "\xED\xA0\x80", 3, true},
        {"a codepoint past U+10FFFF", "\xF7\xBF\xBF\xBF", 4, true},
        {"a good lead and a bad second continuation", "\xE1\x80\xC0", 3, true},
        {"a lead byte whose continuation is ASCII",
         "\xE0"
         "ab",
         3, false}, // the lead begins nothing, but the 'a' and 'b' are characters
    };

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        wire_buffer wire = {{0}, 0, 0};
        ys_token_sink *writer = ys_new_yeast_stream_writer((ys_bytes_writer){wire_write, NULL, &wire}, NULL);
        ys_token token = {.code = YS_CODE_TEXT, .text = cases[index].text};
        token.start = (ys_mark){0, 0, 1, 0};
        token.end = token.start;
        token.end.byte_offset = cases[index].size;

        errno = 0;
        TEST_CHECK(ys_write_token(writer, token) == YS_FAILED_ACTION);
        TEST_MSG("%s: was written rather than refused", cases[index].name);
        TEST_CHECK(errno == EINVAL);
        TEST_MSG("%s: errno is %d, not EINVAL", cases[index].name, errno);
        ys_delete_token_sink(writer);

        // The token holds the bytes `YS_CODE_UNPARSED_INVALID` covers. A byte of that text encodes no character.
        wire_buffer held = {{0}, 0, 0};
        ys_token_sink *to_held = ys_new_yeast_stream_writer((ys_bytes_writer){wire_write, NULL, &held}, NULL);
        token.code = YS_CODE_UNPARSED_INVALID;
        TEST_CHECK((ys_write_token(to_held, token) == YS_OK) == cases[index].throughout);
        TEST_MSG("%s: `YS_CODE_UNPARSED_INVALID` wanted the token %s", cases[index].name,
                 cases[index].throughout ? "written" : "refused");
        ys_delete_token_sink(to_held);
    }
}

// An escape under `YS_CODE_UNPARSED_INVALID` writes a byte. The writer refuses
// text that encodes characters there. Such escapes would name characters rather than bytes.
static void test_wire_refuses_well_formed_invalid_text(void) {
    static const struct {
        const char *name;
        const char *text;
        size_t size;
    } cases[] = {
        {"plain ASCII", "ab", 2},
        {"a two-byte character", "\xC3\xA9", 2},       // U+00E9
        {"ASCII before ill-formed bytes", "a\x80", 2}, // the run must be ill-formed throughout, not merely somewhere
    };

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        wire_buffer wire = {{0}, 0, 0};
        ys_token_sink *writer = ys_new_yeast_stream_writer((ys_bytes_writer){wire_write, NULL, &wire}, NULL);
        ys_token token = {.code = YS_CODE_UNPARSED_INVALID, .text = cases[index].text};
        token.start = (ys_mark){0, 0, 1, 0};
        token.end = token.start;
        token.end.byte_offset = cases[index].size;

        errno = 0;
        TEST_CHECK(ys_write_token(writer, token) == YS_FAILED_ACTION);
        TEST_MSG("%s: `YS_CODE_UNPARSED_INVALID` wrote the token rather than refusing it", cases[index].name);
        TEST_CHECK(errno == EINVAL);
        TEST_MSG("%s: errno is %d, not EINVAL", cases[index].name, errno);
        ys_delete_token_sink(writer);
    }
}

// A source that hands out a set number of bytes and then reports a failure rather than an end.
typedef struct drip_source {
    const char *bytes;
    size_t good; // the bytes the source hands out before it fails.
    size_t offset;
} drip_source;

// A read that hands over a byte at a time and then fails at a chosen offset. It reaches the mid-parse reader failure.
static ptrdiff_t drip_source_read(void *context, char *bytes, size_t size) {
    drip_source *source = context;
    if (source->offset == source->good) {
        errno = EIO; // a reader must set errno when it fails; libyeast passes the value through
        return -1;
    }
    size_t left = source->good - source->offset;
    size_t take = size < left ? size : left;
    memcpy(bytes, source->bytes + source->offset, take);
    source->offset += take;
    return (ptrdiff_t)take;
}

// A caller hands a reader over to the constructor. An owned reader then belongs to the parser. A caller whose parser
// fails to build holds nothing to free the reader with. The constructor closes the reader itself. The memory failure's
// ENOMEM survives the close. The close sets an errno of its own.
static void test_owned_reader_is_closed_when_construction_fails(void) {
    ys_options tiny = {{0}, YS_RESUME_NONE, 8}; // smaller than either object
    wire_buffer wire = {{0}, 0, 0};
    ys_bytes_reader reader = {wire_read, note_close, &wire}; // nothing reads it. there is nothing to read it into

    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_yaml_stream_parser(reader, &tiny) == NULL);
    TEST_CHECK(is_closed);       // the parser could not be built, and closed what it had been given
    TEST_CHECK(errno == ENOMEM); // the cap was too small, and the close's EIO did not overwrite that

    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_yeast_stream_reader(reader, &tiny) == NULL);
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == ENOMEM);
}

// An error's text is its message. The message is not in the input and spans no part of it. The marks say `0`. The wire
// therefore cannot take the length from them. Reading the token back must not take the marks from the length.
static void test_wire_round_trips_an_error(void) {
    const char *message = "inside production `ns-plain`, expected `:` or a line break";

    wire_buffer wire = {{0}, 0, 0};
    ys_token_sink *writer = ys_new_yeast_stream_writer((ys_bytes_writer){wire_write, NULL, &wire}, NULL);
    ys_token written;
    written.code = YS_CODE_ERROR;
    written.start = (ys_mark){7, 7, 1, 3};
    written.end = written.start; // an error consumes nothing
    written.text = message;
    TEST_CHECK(ys_write_token(writer, written) == YS_OK);
    ys_delete_token_sink(writer);

    TEST_CHECK(strstr(wire.bytes, message) != NULL); // the message reached the wire, and is not merely a bare '!'

    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token read;
    TEST_ASSERT(ys_read_token(tokens, &read) == YS_OK);
    TEST_CHECK(read.code == YS_CODE_ERROR);
    TEST_CHECK(read.text != NULL && strncmp(read.text, message, strlen(message)) == 0);
    TEST_CHECK(read.start.byte_offset == 7 && read.start.line == 1 && read.start.column == 3);
    TEST_CHECK(read.end.byte_offset == read.start.byte_offset); // and it still consumes nothing
    TEST_CHECK(read.end.line == read.start.line && read.end.column == read.start.column);

    ys_delete_token_source(tokens);
}

// An error's text comes out as a string rather than as a span. The text must end on a NUL byte. The writer takes its
// length with strlen.
//
// A message may fill the reader's buffer exactly. A missing terminator then makes the writer read off the end. A
// sanitizer watches the round-trip through the writer.
static void test_wire_error_text_is_terminated(void) {
    wire_buffer wire = {{0}, 0, 0};
    // Four four-byte codepoints unescape to sixteen bytes, a size the growth lands on exactly.
    const char *written = "# B: 0, C: 0, L: 0, c: 0\n!\\U0001F600\\U0001F600\\U0001F600\\U0001F600\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_ERROR);
    TEST_CHECK(strlen(token.text) == 16); // reads to the terminator and no further

    wire_buffer out = {{0}, 0, 0};
    ys_token_sink *sink = ys_new_yeast_stream_writer((ys_bytes_writer){wire_write, NULL, &out}, NULL);
    TEST_CHECK(ys_write_token(sink, token) == YS_OK); // the writer strlen's the message, and must not overread

    ys_delete_token_sink(sink);
    ys_delete_token_source(tokens);
}

// A bare error has an empty message rather than a missing one. Its text is "" rather than NULL.
static void test_wire_empty_error_text(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *written = "# B: 3, C: 3, L: 0, c: 3\n!\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_ERROR);
    TEST_CHECK(token.text != NULL && token.text[0] == '\0'); // empty, but there

    ys_delete_token_source(tokens);
}

// A reader that fails partway is a host failure. Such a failure is not a stream that ended, and it is not a malformed
// wire. It is `YS_FAILED_STREAM`. The reader hands back its errno and no token. The source is then spent.
static void test_wire_reader_failure(void) {
    drip_source source = {"# B: 0, C: 0, L: 0, c: 0\nThello\n", 8, 0}; // it hands out 8 bytes, then fails
    ys_bytes_reader reader = {drip_source_read, NULL, &source};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    errno = 0;
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_STREAM);
    TEST_CHECK(errno == EIO);                                      // the reader's own, passed through
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION); // and nothing follows it

    ys_delete_token_source(tokens);
}

// A reader can fail after the position line, part way through the token line. That is a different path through the
// reader than a failure on the position line itself. The reader reports the same `YS_FAILED_STREAM`.
static void test_wire_reader_failure_mid_token(void) {
    drip_source source = {"# B: 0, C: 0, L: 0, c: 0\nThello\n", 25, 0}; // hands out the position line, then fails
    ys_bytes_reader reader = {drip_source_read, NULL, &source};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_STREAM);
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION);

    ys_delete_token_source(tokens);
}

// A wire error token gets a code that a legitimate wire does not hold. A caller then does not mistake the reader's
// trouble for the error tokens the wire replays. The token's marks locate the fault. A caller can point at the place in
// the wire.
static void test_wire_error_is_located(void) {
    wire_buffer wire = {{0}, 0, 0};
    // A valid token, then a bad escape on the fourth line at the sixth character. The code is 'T', then `ab`, then
    // `\q`.
    const char *written = "# B: 0, C: 0, L: 0, c: 0\nTok\n# B: 2, C: 2, L: 0, c: 2\nTab\\q\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token) == YS_OK); // the good token first, a content token, not a wire error
    TEST_CHECK(token.code == YS_CODE_TEXT);

    TEST_ASSERT(ys_read_token(tokens, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_ERROR);
    TEST_CHECK(token.start.line == 4);        // the fourth line of the wire
    TEST_CHECK(token.start.column == 3);      // 'T' at column 0 and 'a' at 1. 'b' at 2, the bad escape's '\' at 3
    TEST_CHECK(token.start.byte_offset == 0); // the wire is at fault. the parsed-input offsets are 0
    TEST_CHECK(token.end.line == token.start.line && token.end.column == token.start.column); // it spans nothing
    TEST_CHECK(strstr(token.text, "escape") != NULL);
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION); // and nothing follows it

    ys_delete_token_source(tokens);
}

// The reader rejects a wire that is not the yeast wire format rather than misreading it. The answer says which way the
// wire broke.
static void test_wire_rejects_rubbish(void) {
    static const struct {
        const char *wire;
        const char *reason; // a word the message must contain
    } cases[] = {
        {"not a token at all\n", "position"},
        // not-prose: a wire record, as `tests/spec/*.output` holds it
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
        // A position that a token cannot start at. It is readable. Its own text puts the end of it past where
        // counting stops and back around. The marks would hand a caller a span running backwards.
        {"# B: 18446744073709551615, C: 0, L: 1, c: 0\nThi\n", "position"},
        {"# B: 0, C: 18446744073709551615, L: 1, c: 0\nThi\n", "position"},
        // An unparsed-invalid token whose bytes begin a character. The writer does not put one under this code. The
        // reader refuses to read one back. It is as much a broken wire as an escape that names no codepoint.
        {"# B: 0, C: 0, L: 1, c: 0\n~\\xc3\\xa9\n", "unparsed-invalid"}, // \xc3\xa9 is a valid U+00E9
        {"# B: 0, C: 0, L: 1, c: 0\n~\\x41\n", "unparsed-invalid"},      // \x41 is a valid 'A'
        {"# B: 0, C: 0, L: 1, c: 0\n~q\n", "escape"},                    // an unparsed-invalid unit that is not \xXX
        {"# B: 0, C: 0, L: 1, c: 0\n~\\xZZ\n", "escape"},                // a byte escape whose digits are not digits
    };

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        wire_buffer wire = {{0}, 0, 0};
        wire.size = strlen(cases[index].wire);
        memcpy(wire.bytes, cases[index].wire, wire.size);

        ys_bytes_reader reader = {wire_read, NULL, &wire};
        ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
        TEST_ASSERT(tokens != NULL);

        ys_token token;
        TEST_CHECK(ys_read_token(tokens, &token) == YS_OK); // the fault is a token. the caller cannot miss it
        TEST_MSG("case %zu was accepted: %s", index, cases[index].wire);
        TEST_CHECK(token.code == YS_CODE_ERROR);
        TEST_CHECK(token.text != NULL && strstr(token.text, cases[index].reason) != NULL);
        TEST_MSG("case %zu said: %s", index, token.text);
        TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION); // and nothing follows it

        ys_delete_token_source(tokens);
    }
}

// An unparsed-invalid token round-trips its raw bytes. The writer writes a byte \xXX. The reader hands back the raw
// bytes themselves. The token holds a span of a pair of bytes. The codepoints \x80 and \x81 would name a different
// span.
static void test_wire_reads_unparsed_invalid(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *written = "# B: 3, C: 3, L: 1, c: 3\n~\\x80\\x81\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_UNPARSED_INVALID);
    TEST_CHECK(token.end.byte_offset - token.start.byte_offset == 2); // two raw bytes, not two codepoints' worth
    TEST_CHECK((unsigned char)token.text[0] == 0x80u && (unsigned char)token.text[1] == 0x81u);
    TEST_CHECK(token.end.char_offset - token.start.char_offset == 2); // one column per byte
    TEST_CHECK(token.start.byte_offset == 3 && token.start.column == 3);

    ys_delete_token_source(tokens);
}

// The bytes the FILE* writer adapter writes come back, and `YS_BORROW` leaves the stream untouched.
static void test_fp_writer(void) {
    FILE *file = tmpfile();
    TEST_ASSERT(file != NULL);
    if (file != NULL) {
        ys_bytes_writer writer = ys_fp_writer(file, YS_OWN);
        TEST_CHECK(writer.write(writer.context, "hi", 2) == 2);
        TEST_ASSERT(fflush(file) == 0);
        TEST_ASSERT(fseek(file, 0, SEEK_SET) == 0);

        char buffer[8];
        TEST_CHECK(fread(buffer, 1, sizeof(buffer), file) == 2);
        TEST_CHECK(memcmp(buffer, "hi", 2) == 0);
        TEST_CHECK(writer.close(writer.context) == 0); // the flush succeeded. the close did too
    }

    ys_bytes_writer borrowed = ys_fp_writer(stdout, YS_BORROW);
    TEST_CHECK(borrowed.close == NULL); // YS_BORROW leaves the stream alone, nothing to close
}

// The file-descriptor writer adapter. A pipe passes the bytes, and what goes in at either end comes out the other.
#ifndef _WIN32
static void test_fd_writer(void) {
    int ends[2];
    TEST_ASSERT(pipe(ends) == 0);

    ys_bytes_writer writer = ys_fd_writer(ends[1], YS_OWN);
    TEST_CHECK(writer.write(writer.context, "hi", 2) == 2);
    writer.close(writer.context); // the reader sees the end of input only once the write end is closed

    char buffer[8];
    TEST_CHECK(read(ends[0], buffer, sizeof(buffer)) == 2);
    TEST_CHECK(memcmp(buffer, "hi", 2) == 0);
    TEST_CHECK(read(ends[0], buffer, sizeof(buffer)) == 0); // end of input
    TEST_CHECK(close(ends[0]) == 0);

    ys_bytes_writer borrowed = ys_fd_writer(1, YS_BORROW);
    TEST_CHECK(borrowed.close == NULL); // YS_BORROW leaves the descriptor alone
}
#endif

// A writer with no write callback is a bad argument, and the answer is EINVAL. The constructor closes an owned writer
// even so. A reader with no read callback closes the same way when a source constructor refuses it.
//
// The constructor closes the writer when the allocator refuses, and the answer is ENOMEM. The sink gets the writer
// whether or not the constructor succeeds.
static void test_yeast_stream_writer_bad_argument(void) {
    ys_bytes_writer empty = {NULL, NULL, NULL};
    errno = 0;
    TEST_CHECK(ys_new_yeast_stream_writer(empty, NULL) == NULL);
    TEST_CHECK(errno == EINVAL);

    ys_bytes_writer owned = {NULL, note_close, NULL}; // no write callback, but an owned resource to close
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_yeast_stream_writer(owned, NULL) == NULL);
    TEST_CHECK(is_closed && errno == EINVAL);

    ys_allocator refusing = {failing_allocate, NULL, NULL, NULL, NULL};
    ys_options refused = {refusing, YS_RESUME_NONE, 0};
    ys_bytes_writer valid = {wire_write, note_close, NULL}; // a valid write, and an owned close
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_yeast_stream_writer(valid, &refused) == NULL);
    TEST_CHECK(is_closed && errno == ENOMEM); // the allocator refused, and the writer was closed anyway
}

// Deleting a sink reports its writer's close. That close is the flush where a buffered write finally fails. The
// source's delete reports its reader's close the same way.
static void test_sink_delete_reports_close_failure(void) {
    wire_buffer wire = {{0}, 0, 0};
    ys_bytes_writer owned = {wire_write, note_close, &wire}; // its close fails with EIO
    ys_token_sink *sink = ys_new_yeast_stream_writer(owned, NULL);
    TEST_ASSERT(sink != NULL);

    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_delete_token_sink(sink) == YS_FAILED_STREAM);
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == EIO);

    TEST_CHECK(ys_delete_token_sink(NULL) == YS_OK); // deleting nothing cannot fail
}

// An error is not
// a token the emitter can emit. `ys_write_token` refuses an error with `YS_FAILED_ACTION` and EINVAL. A caller filters
// an error above that call.
static void test_yaml_emitter_refuses_error(void) {
    wire_buffer out = {{0}, 0, 0};
    ys_token_sink *emitter = ys_new_yaml_stream_emitter((ys_bytes_writer){wire_write, NULL, &out}, NULL);
    TEST_ASSERT(emitter != NULL);

    ys_token error = {.code = YS_CODE_ERROR, .text = "not the wire format"};
    error.start = (ys_mark){0, 0, 1, 0};
    error.end = error.start;
    errno = 0;
    TEST_CHECK(ys_write_token(emitter, error) == YS_FAILED_ACTION);
    TEST_CHECK(errno == EINVAL);
    TEST_CHECK(out.size == 0); // and nothing of it reached the writer

    TEST_CHECK(ys_delete_token_sink(emitter) == YS_OK);
}

#ifndef _WIN32
// A growable byte buffer. An emitter writes output of no fixed size.
typedef struct grow_buffer {
    char *bytes;
    size_t size;
    size_t capacity;
} grow_buffer;

// A write into a buffer that doubles to fit. A test collects an emitter's whole output without picking a size first.
static ptrdiff_t grow_write(void *context, const char *bytes, size_t size) {
    grow_buffer *buffer = context;
    if (buffer->size + size > buffer->capacity) {
        size_t capacity = buffer->capacity == 0 ? 256 : buffer->capacity;
        while (capacity < buffer->size + size) {
            capacity *= 2;
        }
        char *grown = realloc(buffer->bytes, capacity);
        if (grown == NULL) {
            return -1; // UNTESTED
        }
        buffer->bytes = grown;
        buffer->capacity = capacity;
    }
    memcpy(buffer->bytes + buffer->size, bytes, size);
    buffer->size += size;
    return (ptrdiff_t)size;
}

// Serve the bytes of a file to a `ys_bytes_reader`. The wire reader can then replay a fixture's wire.
typedef struct file_bytes {
    char *bytes;
    size_t size;
    size_t offset;
} file_bytes;

// A read over bytes already in memory. It feeds a stream parser what a file would have held.
static ptrdiff_t file_bytes_read(void *context, char *into, size_t size) {
    file_bytes *source = context;
    size_t left = source->size - source->offset;
    size_t take = size < left ? size : left;
    memcpy(into, source->bytes + source->offset, take);
    source->offset += take;
    return (ptrdiff_t)take;
}

// Read a whole file into a freshly-malloc'd buffer, or fail the test. The caller frees it.
static char *slurp(const char *path, size_t *size) {
    FILE *file = fopen(path, "rb");
    TEST_ASSERT_HINT(file != NULL);
    TEST_ASSERT(fseek(file, 0, SEEK_END) == 0);
    long length = ftell(file);
    TEST_ASSERT_HINT(length >= 0); // so the allocation size below is a positive length, not a wrapped-around zero
    TEST_ASSERT(fseek(file, 0, SEEK_SET) == 0);
    char *bytes = malloc((size_t)length + 1);
    TEST_ASSERT_HINT(bytes != NULL);
    TEST_ASSERT(fread(bytes, 1, (size_t)length, file) == (size_t)length);
    TEST_CHECK(fclose(file) == 0);
    *size = (size_t)length;
    return bytes;
}

// A fixture named `name` begins past a prefix of `input`. The bytes of that prefix are the answer. The `p=N` in the
// name counts the UTF-8 characters before the rule. The count walks the characters rather than adding them.
// A name without a `p=N` begins at the first byte.
static size_t reached_through(const char *name, const char *input, size_t input_size) {
    const char *named = strstr(name, ".p=");
    if (named == NULL) {
        return 0;
    }
    size_t characters = (size_t)strtoul(named + 3, NULL, 10);
    size_t offset = 0;
    while (characters > 0 && offset < input_size) {
        do {
            offset++;
        } while (offset < input_size && ((unsigned char)input[offset] & 0xC0u) == 0x80u); // over its continuations
        characters--;
    }
    return offset;
}

// The test runs the round-trip through the emitter. The emitter renders a fixture's tokens as YAML. A filter above the
// emitter drops the errors. The YAML reconstructs the input the tokens came from. A fixture begins past a prefix. The
// render leaves that prefix out. The parse takes the prefix before the token run begins. That run matches no part of
// the prefix.
//
// A fixture's `.output` is a yeast wire. The emitter reads such a wire back. That pass is the yeast -> YAML half of the
// pipeline. The test runs that pass over the corpus. The tokens are byte-complete. A consumed byte lies in a single
// token, and an error spans no byte at all.
static void test_emitter_reconstructs_fixtures(void) {
    DIR *directory = opendir(YS_SPEC_DIR);
    TEST_ASSERT_HINT(directory != NULL);

    size_t checked = 0;
    struct dirent *entry;
    while ((entry = readdir(directory)) != NULL) {
        size_t name_length = strlen(entry->d_name);
        if (name_length < 7 || strcmp(entry->d_name + name_length - 7, ".output") != 0) {
            continue; // only the token streams; each has a sibling `.input`
        }
        if (strstr(entry->d_name, ".invalid.") != NULL) {
            // An invalid fixture tests a sub-production rejecting in isolation. That covers the prefix it matched and
            // no more. Recovery into unparsed tokens is `l-yeast-stream`'s, not a sub-production's.
            //
            // That the whole input comes back even when malformed is instead the valid recovery fixtures. Those are
            // `l-recover`, `l-unparsed` and `l-yeast-stream`. They are byte-complete, and reconstruct here like any
            // other.
            continue;
        }

        char output_path[512];
        char input_path[512];
        int output_written = snprintf(output_path, sizeof(output_path), "%s/%s", YS_SPEC_DIR, entry->d_name);
        int input_written = snprintf(input_path, sizeof(input_path), "%s/%.*s.input", YS_SPEC_DIR,
                                     (int)(name_length - 7), entry->d_name);
        TEST_ASSERT(output_written > 0 && (size_t)output_written < sizeof(output_path));
        TEST_ASSERT(input_written > 0 && (size_t)input_written < sizeof(input_path));

        size_t wire_size = 0;
        size_t input_size = 0;
        char *wire = slurp(output_path, &wire_size);
        char *input = slurp(input_path, &input_size);

        file_bytes served = {wire, wire_size, 0};
        ys_token_source *source = ys_new_yeast_stream_reader((ys_bytes_reader){file_bytes_read, NULL, &served}, NULL);
        grow_buffer emitted = {NULL, 0, 0};
        ys_token_sink *emitter = ys_new_yaml_stream_emitter((ys_bytes_writer){grow_write, NULL, &emitted}, NULL);
        TEST_ASSERT(source != NULL && emitter != NULL);

        ys_token token;
        while (ys_read_token(source, &token) == YS_OK) {
            if (token.code == YS_CODE_ERROR) {
                continue; // filtered above the emitter, which renders rather than judges
            }
            TEST_CHECK(ys_write_token(emitter, token) == YS_OK);
        }
        TEST_CHECK(ys_delete_token_source(source) == YS_OK);
        TEST_CHECK(ys_delete_token_sink(emitter) == YS_OK);

        size_t reached = reached_through(entry->d_name, input, input_size);
        size_t matched = input_size - reached;
        TEST_CHECK(emitted.size == matched && memcmp(emitted.bytes, input + reached, matched) == 0);
        TEST_MSG("%s: emitted %zu bytes, and the input runs %zu past the %zu the parse reached through", entry->d_name,
                 emitted.size, matched, reached);

        free(wire);
        free(input);
        free(emitted.bytes);
        checked++;
    }
    closedir(directory);

    TEST_CHECK(checked > 0);
    TEST_MSG("%s holds no fixture", YS_SPEC_DIR);
}
#endif

// A wire stream whose last line has no newline still yields its token. A reader owning a source closes it.
static void test_wire_odds_and_ends(void) {
    FILE *file = tmpfile();
    TEST_ASSERT(file != NULL);
    if (file != NULL) {
        TEST_ASSERT(fputs("# B: 1, C: 2, L: 3, c: 4\nI-", file) >= 0); // no trailing newline
        TEST_ASSERT(fseek(file, 0, SEEK_SET) == 0);

        ys_token_source *tokens = ys_new_yeast_stream_reader(ys_fp_reader(file, YS_OWN), NULL);
        TEST_ASSERT(tokens != NULL);
        ys_token token;
        TEST_CHECK(ys_read_token(tokens, &token) == YS_OK);
        TEST_CHECK(token.code == YS_CODE_INDICATOR);
        TEST_CHECK(token.start.byte_offset == 1 && token.start.column == 4);
        TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION); // and then the stream is over
        ys_delete_token_source(tokens);                                // which closes the file it was given to own
    }

    ys_delete_token_source(NULL); // freeing nothing is not an error
}

// A cap may be too small for the input. A reader under such a cap reports failure rather than growing past that cap.
// The constructor refuses a cap too small to build a reader under.
static void test_wire_memory_cap(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *line = "# B: 0, C: 0, L: 0, c: 0\nThello\n";
    wire.size = strlen(line);
    memcpy(wire.bytes, line, wire.size);
    ys_bytes_reader reader = {wire_read, NULL, &wire};

    ys_options tiny = {{0}, YS_RESUME_NONE, 8}; // smaller than the reader itself
    TEST_CHECK(ys_new_yeast_stream_reader(reader, &tiny) == NULL);

    ys_options capped = {{0}, YS_RESUME_NONE, 512}; // room for the reader, none for a buffer
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, &capped);
    TEST_ASSERT(tokens != NULL);

    // It cannot buffer a line, and it says so. That is YS_FAILED_MEMORY with ENOMEM. It is not a stream that was empty
    // all along.
    ys_token token;
    errno = 0;
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_MEMORY);
    TEST_CHECK(errno == ENOMEM);
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION); // and nothing follows it

    ys_delete_token_source(tokens);
}

// An allocator that hands out a set number of buffers and refuses the next. A test then reaches a failure of the second
// allocation without guessing the reader's size. The second allocation is the reader's text.
static size_t buffers_left;

// A reallocate that refuses once the allocator has handed out a set number of buffers. It fails a growth at a chosen
// depth.
static void *counted_reallocate(void *context, void *pointer, size_t size) {
    (void)context;
    if (buffers_left == 0) {
        errno = ENOMEM; // an allocator that returns NULL must set errno
        return NULL;
    }
    buffers_left--;
    return realloc(pointer, size);
}

// The reader's text buffer is under the same cap as its line buffer. The reader reports a text failure the same way.
// The text grows through `ys_append` for a character token, and through `ys_append_byte` for an unparsed-invalid one.
static void test_wire_text_out_of_memory(void) {
    static const char *wires[] = {
        "# B: 0, C: 0, L: 0, c: 0\nThello\n", // characters, unescaped via ys_append
        "# B: 0, C: 0, L: 1, c: 0\n~\\x80\n", // a raw byte, appended via ys_append_byte
    };
    for (size_t index = 0; index < sizeof(wires) / sizeof(wires[0]); index++) {
        wire_buffer wire = {{0}, 0, 0};
        wire.size = strlen(wires[index]);
        memcpy(wire.bytes, wires[index], wire.size);

        buffers_left = 1; // enough for the line buffer, and nothing left for the text
        ys_allocator allocator = {NULL, counted_reallocate, NULL, NULL, NULL};
        ys_options options = {allocator, YS_RESUME_NONE, 0};
        ys_bytes_reader reader = {wire_read, NULL, &wire};
        ys_token_source *tokens = ys_new_yeast_stream_reader(reader, &options);
        TEST_ASSERT(tokens != NULL);

        ys_token token;
        errno = 0;
        TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_MEMORY); // the line read; its text could not be unescaped
        TEST_CHECK(errno == ENOMEM);
        TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION);

        ys_delete_token_source(tokens);
    }
}

// Even a token with no text gets a NUL terminator. That terminator costs an allocation. A cap refusing that
// allocation makes the reader say out of memory. The reader does not hand back a token whose empty text points at
// nothing.
static void test_wire_terminator_out_of_memory(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *written = "# B: 0, C: 0, L: 0, c: 0\nS\n"; // a begin-scalar marker. a code with no text
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    buffers_left = 1; // enough for the line buffer, and nothing left for the terminator
    ys_allocator allocator = {NULL, counted_reallocate, NULL, NULL, NULL};
    ys_options options = {allocator, YS_RESUME_NONE, 0};
    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, &options);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    errno = 0;
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_MEMORY);
    TEST_CHECK(errno == ENOMEM);

    ys_delete_token_source(tokens);
}

// A source of `left` identical token records. The source makes a record as the reader reads it. The reader must then
// refill its line buffer as the records arrive. The whole stream is not in memory at once.
typedef struct wire_stream {
    size_t left;     // the records still to come.
    char record[40]; // the record going out.
    size_t size;     // the length of that record.
    size_t offset;   // the bytes of that record already sent.
} wire_stream;

// A read that replays a single wire token per call. A stream reader then gets fed the way a real stream feeds it.
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

// The reader reuses the line buffer rather than growing it. The reader discards a line it has handed back. The buffer
// grows where the remainder really does fill it. So a long stream of short lines reads under a cap.
// A buffer that grows once per refill passes that cap.
static void test_wire_long_stream(void) {
    wire_stream stream = {2000, {0}, 0, 0};
    ys_options options = {{0}, YS_RESUME_NONE, 16384};
    ys_bytes_reader reader = {wire_stream_read, NULL, &stream};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, &options);
    TEST_ASSERT(tokens != NULL);

    size_t count = 0;
    ys_token token;
    while (ys_read_token(tokens, &token) == YS_OK) {
        TEST_ASSERT(token.code == YS_CODE_TEXT);
        count++;
    }
    TEST_CHECK(count == 2000);
    TEST_MSG("read %zu of 2000 tokens", count);

    ys_delete_token_source(tokens);
}

// The writer writes lower-case hexadecimal. A stream written by another hand may use upper-case. Upper-case
// means the same thing.
static void test_wire_reads_upper_case_hex(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *written = "# B: 0, C: 0, L: 0, c: 0\nT\\xE9\\u4E00\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_TEXT);
    TEST_CHECK(token.end.byte_offset == 5); // two bytes for U+00E9, three for U+4E00
    TEST_CHECK(memcmp(token.text, "\xC3\xA9\xE4\xB8\x80", 5) == 0);
    ys_delete_token_source(tokens);
}

TEST_LIST = {
    {"ys_version_matches_components", test_ys_version_matches_components},
    {"yaml_memory_parser", test_yaml_memory_parser},
    {"yaml_stream_parser", test_yaml_stream_parser},
    {"wire_codes", test_wire_codes},
    {"wire_round_trip", test_wire_round_trip},
    {"wire_round_trips_an_error", test_wire_round_trips_an_error},
    {"wire_reader_failure", test_wire_reader_failure},
    {"wire_reader_failure_mid_token", test_wire_reader_failure_mid_token},
    {"wire_error_is_located", test_wire_error_is_located},
    {"wire_error_text_is_terminated", test_wire_error_text_is_terminated},
    {"wire_empty_error_text", test_wire_empty_error_text},
    {"wire_refuses_ill_formed_text", test_wire_refuses_ill_formed_text},
    {"wire_refuses_well_formed_invalid_text", test_wire_refuses_well_formed_invalid_text},
    {"wire_reads_unparsed_invalid", test_wire_reads_unparsed_invalid},
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
    {"free_reports_close_failure", test_free_reports_close_failure},
    {"free_reports_both_closes", test_free_reports_both_closes},
    {"fp_reader", test_fp_reader},
    {"fp_writer", test_fp_writer},
#ifndef _WIN32
    {"fd_reader", test_fd_reader},
    {"fd_writer", test_fd_writer},
#endif
    {"yeast_stream_writer_bad_argument", test_yeast_stream_writer_bad_argument},
    {"sink_delete_reports_close_failure", test_sink_delete_reports_close_failure},
    {"yaml_emitter_refuses_error", test_yaml_emitter_refuses_error},
    {"counting_allocator", test_counting_allocator},
    {"counting_allocator_shared", test_counting_allocator_shared},
#ifndef _WIN32
    {"emitter_reconstructs_fixtures", test_emitter_reconstructs_fixtures},
#endif
    {NULL, NULL},
};
