// SPDX-License-Identifier: MIT
// The C library's tests, over the surface a caller sees. The surface holds the token sources and sinks. It holds the
// readers and writers. It holds the wire codes, the allocator and the version. The tests cover what that surface
// refuses and what it does.

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

// A `TEST_ASSERT` a static analyzer trusts. acutest's `TEST_ASSERT` aborts the test on a false condition. The macro
// adds a trailing `abort()`. `TEST_ASSERT` has already aborted by then, and the `abort()` itself does not run. The
// macro expands `cond` more than once. `cond` should not have side effects.
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

// The parser's automaton awaits generation. A read yields a "not implemented" error at the first character. The parser
// stores a resume policy for a malformed document but does not read it. The "not implemented" error leaves the parser
// nothing to continue to. A further read yields the error again. A read answers `YS_OK` and hands back the error token.
static void test_yaml_memory_parser(void) {
    const char *input = "hello: world\n";
    ys_token_source *source = ys_new_yaml_memory_parser(input, strlen(input), NULL);
    TEST_ASSERT(source != NULL);
    TEST_CHECK(ys_are_tokens_stable(
        source)); // The error token's text is a static message, and the source reports its tokens stable.

    ys_token token;
    TEST_CHECK(ys_read_token(source, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_ERROR);
    TEST_CHECK(token.text != NULL && strcmp(token.text, "not implemented") == 0);
    TEST_CHECK(token.start.byte_offset == 0 && token.start.char_offset == 0);
    TEST_CHECK(token.start.line == 0 && token.start.column == 0);
    TEST_CHECK(token.end.byte_offset == token.start.byte_offset);

    ys_token again;
    TEST_CHECK(ys_read_token(source, &again) == YS_OK);
    TEST_CHECK(again.code == YS_CODE_ERROR);

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
    TEST_CHECK(!ys_are_tokens_stable(source)); // A stream parser's text is valid until the next call.
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);

    ys_token token;
    TEST_CHECK(ys_read_token(source, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_ERROR);

    ys_delete_token_source(source); // `YS_OWN` closes the file. The delete then frees the source.
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 0);
    ys_delete_counting_allocator(counter);
}

static bool is_closed; // the flag `note_close` sets. A test reads it back to see the close happened.

// A close that records it ran and then fails. The failure lets a test see whose errno a caller gets.
static int note_close(void *context) {
    (void)context;
    is_closed = true;
    errno = EIO; // A close can fail. The construction failure keeps its own errno.
    return -1;
}

// An allocate that refuses any size a caller asks for. It reaches the out-of-memory path without exhausting the
// machine.
static void *failing_allocate(void *context, size_t size) {
    (void)context;
    (void)size;
    errno = ENOMEM; // An allocator that returns NULL sets errno. libyeast passes that errno through.
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

// A constructor given a bad argument sets errno to EINVAL. EINVAL differs from a memory failure. A NULL buffer with a
// length is a bad argument. A reader with no read callback is a bad argument.
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

    // A failing constructor still closes an owned reader. `note_close` sets errno to EIO. The caller still sees EINVAL.
    ys_bytes_reader owned = {NULL, note_close, NULL}; // no read callback, but an owned resource to close
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_yaml_stream_parser(owned, NULL) == NULL);
    TEST_CHECK(is_closed && errno == EINVAL);
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_yeast_stream_reader(owned, NULL) == NULL);
    TEST_CHECK(is_closed && errno == EINVAL);

    // A NULL buffer with no length is an empty input.
    ys_token_source *parser = ys_new_yaml_memory_parser(NULL, 0, NULL);
    TEST_CHECK(parser != NULL);
    ys_delete_token_source(parser);
}

// Deleting a NULL source or a NULL counting allocator does nothing.
static void test_free_null(void) {
    ys_delete_token_source(NULL);
    ys_delete_counting_allocator(NULL);
    TEST_CHECK(true);
}

// Report the end of the input straight away. A test feeds in an empty input this way.
static ptrdiff_t read_nothing(void *context, char *buffer, size_t size) {
    (void)context;
    (void)buffer;
    (void)size;
    return 0; // Hand back no bytes.
}

// Freeing reports its reader's close. A free returns `YS_FAILED_STREAM` with the close's errno rather than swallowing
// the failure. The default allocator has no close. The reader can fail here and the allocator cannot.
static void test_free_reports_close_failure(void) {
    ys_bytes_reader owned = {read_nothing, note_close, NULL}; // The reader's close fails with EIO.

    ys_token_source *parser = ys_new_yaml_stream_parser(owned, NULL);
    TEST_ASSERT(parser != NULL);
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_delete_token_source(parser) == YS_FAILED_STREAM);
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == EIO);
    TEST_MSG("freeing a parser left errno at %d rather than EIO", errno);

    ys_token_source *tokens = ys_new_yeast_stream_reader(owned, NULL);
    TEST_ASSERT(tokens != NULL);
    ys_token token;
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION);
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
// A failing reader close and a failing allocator close together are `YS_FAILED_BOTH`. Under `YS_FAILED_BOTH`, errno
// holds the reader's reason. The reader failed before the allocator did. The return says the allocator failed as well.
// The test uses the counting allocator. The test swaps that allocator's close for a failing close. The library really
// gives the memory back before the close fails.
static void test_free_reports_both_closes(void) {
    ys_counting_allocator *counter = ys_new_counting_allocator();
    TEST_ASSERT(counter != NULL);
    ys_allocator allocator = ys_counting_allocator_functions(counter);
    allocator.close = allocator_close_fails;
    ys_options options = {allocator, YS_RESUME_NONE, 0};

    // A string parser has no reader to close. The free reports the allocator's failed close as `YS_FAILED_MEMORY`.
    ys_token_source *string = ys_new_yaml_memory_parser("a: 1\n", 5, &options); // not-prose: The literal is YAML input.
    TEST_ASSERT(string != NULL);
    errno = 0;
    TEST_CHECK(ys_delete_token_source(string) == YS_FAILED_MEMORY);
    TEST_CHECK(errno == ENOSPC);
    TEST_MSG("the allocator's close left errno at %d rather than ENOSPC", errno);

    // A stream parser with a failing reader close and this allocator fails both ways. That is `YS_FAILED_BOTH`. The
    // reader's close fails first. errno holds its EIO.
    ys_bytes_reader owned = {read_nothing, note_close, NULL};
    ys_token_source *stream = ys_new_yaml_stream_parser(owned, &options);
    TEST_ASSERT(stream != NULL);
    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_delete_token_source(stream) == YS_FAILED_BOTH);
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == EIO);
    TEST_MSG("both closes failing left errno at %d rather than EIO", errno);

    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 0);
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
    TEST_CHECK(borrowed.close == NULL); // `YS_BORROW` sets no close callback.
}

#ifndef _WIN32
// A pipe feeds a reader over a file descriptor.
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
    TEST_CHECK(borrowed.close == NULL); // `YS_BORROW` sets no close callback.
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

    first = allocator.reallocate(allocator.context, first, 32); // A resize keeps the count.
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);

    void *second =
        allocator.reallocate(allocator.context, NULL, 8); // A reallocation of a NULL pointer allocates a fresh buffer.
    TEST_CHECK(second != NULL);
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 2);

    TEST_CHECK(allocator.reallocate(allocator.context, second, 0) ==
               NULL); // A reallocation to no bytes frees the buffer and returns NULL.
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);

    // The test calls the close that `ys_counting_allocator_functions()` installs. `first` is still live. The close
    // reports the leak as an allocator failure. The failure sets errno to ENOMEM.
    errno = 0;
    TEST_CHECK(ys_close_counting_allocator(counter) != 0);
    TEST_CHECK(errno == ENOMEM);

    allocator.deallocate(allocator.context, first);
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 0);

    TEST_CHECK(ys_close_counting_allocator(counter) == 0); // The test frees `first`. The close then succeeds.

    ys_delete_counting_allocator(counter);
}

// A shared counter serves both parsers here. Deleting the first parser would fire the installed close. The second
// parser still holds live buffers then. So the test sets close to NULL. The test closes the counter by hand after
// both parsers are gone. The close then expects no live buffer.
static void test_counting_allocator_shared(void) {
    ys_counting_allocator *counter = ys_new_counting_allocator();
    TEST_ASSERT(counter != NULL);
    ys_allocator allocator = ys_counting_allocator_functions(counter);
    allocator.close = NULL;
    ys_options options = {allocator, YS_RESUME_NONE, 0};

    ys_token_source *first = ys_new_yaml_memory_parser("a: 1\n", 5, &options);  // not-prose: The literal is YAML input.
    ys_token_source *second = ys_new_yaml_memory_parser("b: 2\n", 5, &options); // not-prose: The literal is YAML input.
    TEST_ASSERT(first != NULL && second != NULL);
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 2);

    TEST_CHECK(ys_delete_token_source(first) == YS_OK);
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);
    TEST_CHECK(ys_delete_token_source(second) == YS_OK);

    TEST_CHECK(ys_close_counting_allocator(counter) == 0);
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
    TEST_CHECK(ys_code_of_char('@', &code) == YS_FAILED_ACTION); // `@` is no wire character.
    TEST_CHECK(errno == EINVAL);
    TEST_CHECK(ys_code_of_char('\0', &code) == YS_FAILED_ACTION);
}

// A sink and a source over a single buffer. The wire tests then want no file. They exercise the `ys_bytes_writer` and
// `ys_bytes_reader` abstractions rather than the FILE* adapters. `test_fp_reader` and `test_fd_reader` test the reader
// adapters. `test_fp_writer` and `test_fd_writer` test the writer adapters.
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

// The wire that `src/wire.c` writes records a token's code, start mark and text. The reader in `src/wire.c` works out
// the end mark from the start and the text.
static void test_wire_round_trip(void) {
    // `characters` and `breaks` count what the text holds. `column` counts from the last break where the text holds
    // one.
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
        {"two-byte non-ASCII", YS_CODE_TEXT, "\xC3\xA9", 2, 1, 0, 0},
        {"three-byte non-ASCII", YS_CODE_TEXT, "\xE4\xB8\x80", 3, 1, 0, 0},
        {"beyond the basic plane", YS_CODE_TEXT, "\xF0\x9F\x98\x80", 4, 1, 0, 0},
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
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION);
    ys_delete_token_source(tokens);
}

// Under any code but `YS_CODE_UNPARSED_INVALID`, the wire escapes by codepoint and has no form for a byte that is not a
// codepoint. The writer refuses a `YS_CODE_TEXT` token holding such a byte. The writer writes a
// `YS_CODE_UNPARSED_INVALID` token only where its text holds no byte that begins a character. The wire reader refuses a
// `YS_CODE_UNPARSED_INVALID` token whose text holds a byte that begins a character. `ys_unescape` makes that check.
static void test_wire_refuses_ill_formed_text(void) {
    static const struct {
        const char *name;
        const char *text;
        size_t size;
        bool throughout; // `throughout` says whether no byte of the text begins a character. `YS_CODE_UNPARSED_INVALID`
                         // may hold such text.
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

        // The test then gives the token the code `YS_CODE_UNPARSED_INVALID` and keeps its bytes. The writer refuses it
        // where a byte of that text begins a character.
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
        {"ASCII before ill-formed bytes", "a\x80", 2}, // The run must be ill-formed throughout.
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

// Hand the caller the bytes it asks for up to a chosen offset, and then fail with `EIO`. The parse then fails in the
// middle.
static ptrdiff_t drip_source_read(void *context, char *bytes, size_t size) {
    drip_source *source = context;
    if (source->offset == source->good) {
        errno = EIO; // A reader sets errno when it fails. libyeast passes that errno through.
        return -1;
    }
    size_t left = source->good - source->offset;
    size_t take = size < left ? size : left;
    memcpy(bytes, source->bytes + source->offset, take);
    source->offset += take;
    return (ptrdiff_t)take;
}

// A caller hands a reader over to the constructor. An owned reader then belongs to the source. A caller whose source
// fails to build holds nothing to free the reader with. The constructor closes the reader itself. The memory failure's
// ENOMEM survives the close. The close sets an errno of its own.
static void test_owned_reader_is_closed_when_construction_fails(void) {
    ys_options tiny = {{0}, YS_RESUME_NONE, 8}; // The cap is smaller than either object.
    wire_buffer wire = {{0}, 0, 0};
    ys_bytes_reader reader = {wire_read, note_close, &wire};

    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_yaml_stream_parser(reader, &tiny) == NULL);
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == ENOMEM);

    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_new_yeast_stream_reader(reader, &tiny) == NULL);
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == ENOMEM);
}

// An error's text is its message. The message is not in the input and spans no part of it. The token's start and end
// marks give a length of `0`. The wire therefore cannot take the length from those marks. Reading the token back must
// not take the marks from the length.
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

    TEST_CHECK(strstr(wire.bytes, message) != NULL); // The message reached the wire.

    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token read;
    TEST_ASSERT(ys_read_token(tokens, &read) == YS_OK);
    TEST_CHECK(read.code == YS_CODE_ERROR);
    TEST_CHECK(read.text != NULL && strncmp(read.text, message, strlen(message)) == 0);
    TEST_CHECK(read.start.byte_offset == 7 && read.start.line == 1 && read.start.column == 3);
    TEST_CHECK(read.end.byte_offset == read.start.byte_offset);
    TEST_CHECK(read.end.line == read.start.line && read.end.column == read.start.column);

    ys_delete_token_source(tokens);
}

// An error's text comes out as a string rather than as a span. The text must end on a NUL byte. The writer takes its
// length with strlen.
//
// A message may fill the reader's buffer in `src/wire.c` exactly. A missing terminator then makes the writer read off
// the end.
static void test_wire_error_text_is_terminated(void) {
    wire_buffer wire = {{0}, 0, 0};
    // The escaped codepoints unescape to the exact size the buffer grows to.
    const char *written = "# B: 0, C: 0, L: 0, c: 0\n!\\U0001F600\\U0001F600\\U0001F600\\U0001F600\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_ERROR);
    TEST_CHECK(strlen(token.text) == 16);

    wire_buffer out = {{0}, 0, 0};
    ys_token_sink *sink = ys_new_yeast_stream_writer((ys_bytes_writer){wire_write, NULL, &out}, NULL);
    TEST_CHECK(ys_write_token(sink, token) == YS_OK); // The writer measures the message with strlen.

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
    TEST_CHECK(token.text != NULL && token.text[0] == '\0');

    ys_delete_token_source(tokens);
}

// A reader that fails partway is a host failure. Such a failure is not a stream that ended, and it is not a malformed
// wire. It is `YS_FAILED_STREAM`. The reader hands back its errno and no token. The source is then spent.
static void test_wire_reader_failure(void) {
    drip_source source = {"# B: 0, C: 0, L: 0, c: 0\nThello\n", 8, 0}; // The source fails partway through the line.
    ys_bytes_reader reader = {drip_source_read, NULL, &source};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    errno = 0;
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_STREAM);
    TEST_CHECK(errno == EIO);
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION);

    ys_delete_token_source(tokens);
}

// A reader can fail after the position line, before any byte of the token line. That is a different path through the
// reader than a failure on the position line itself. The reader reports the same `YS_FAILED_STREAM`.
static void test_wire_reader_failure_mid_token(void) {
    drip_source source = {"# B: 0, C: 0, L: 0, c: 0\nThello\n", 25, 0}; // The source fails after the position line.
    ys_bytes_reader reader = {drip_source_read, NULL, &source};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_STREAM);
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION);

    ys_delete_token_source(tokens);
}

// A malformed wire comes back as a `YS_CODE_ERROR` token. `ys_wire_read` also replays an error token off a sound wire
// under that code. The token's marks locate the fault in the wire.
static void test_wire_error_is_located(void) {
    wire_buffer wire = {{0}, 0, 0};
    // The wire holds a valid token and then a bad escape. The line with the bad escape holds the code character 'T',
    // then `ab`, then `\q`.
    const char *written = "# B: 0, C: 0, L: 0, c: 0\nTok\n# B: 2, C: 2, L: 0, c: 2\nTab\\q\n";
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    ys_bytes_reader reader = {wire_read, NULL, &wire};
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_ASSERT(ys_read_token(tokens, &token) ==
                YS_OK); // The good token comes first. It is a content token rather than a wire error.
    TEST_CHECK(token.code == YS_CODE_TEXT);

    TEST_ASSERT(ys_read_token(tokens, &token) == YS_OK);
    TEST_CHECK(token.code == YS_CODE_ERROR);
    TEST_CHECK(token.start.line == 4); // The fault sits on the wire line holding the bad escape.
    TEST_CHECK(token.start.column ==
               3); // The column counts from the code character. It lands on the backslash of the bad escape.
    TEST_CHECK(token.start.byte_offset ==
               0); // The fault lies in the wire. So the byte and codepoint offsets stay at the start of the input.
    TEST_CHECK(token.end.line == token.start.line &&
               token.end.column == token.start.column); // The error token ends where it starts.
    TEST_CHECK(strstr(token.text, "escape") != NULL);
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION); // A read past the error token fails.

    ys_delete_token_source(tokens);
}

// The reader rejects a wire that is not the yeast wire format rather than misreading it. The answer says which way the
// wire broke.
static void test_wire_rejects_rubbish(void) {
    static const struct {
        const char *wire;
        const char *reason; // This field holds a word that an error message must contain.
    } cases[] = {
        {"not a token at all\n", "position"},
        // not-prose: The strings below are records of the yeast wire format.
        {"# B: 0, C: 0, L: 0, c: 0\n", "no token after it"},    // This position has nothing after it.
        {"# B: 0, C: 0, L: 0, c: 0\n\n", "no token after it"},  // This token line is empty.
        {"# B: nonsense\nT\n", "position"},                     // This position does not parse.
        {"# B: 0, C: 0, L: 0, c: 0\n\xFF\n", "code"},           // This byte is not a code character.
        {"# B: 0, C: 0, L: 0, c: 0\nT\\q\n", "escape"},         // This backslash starts an unknown escape.
        {"# B: 0, C: 0, L: 0, c: 0\nT\\x2\n", "escape"},        // This escape is cut short.
        {"# B: 0, C: 0, L: 0, c: 0\nT\\xZZ\n", "escape"},       // This escape has digits that are not hex digits.
        {"# B: 0, C: 0, L: 0, c: 0\nT\\uD800\n", "escape"},     // This escape names a lone surrogate.
        {"# B: 0, C: 0, L: 0, c: 0\nT\\U00110000\n", "escape"}, // This escape names a value past the last codepoint.
        {"# B: 0, C: 0, L: 0, c: 0\nT\xC3\xA9\n", "printable"}, // This raw byte is outside printable ASCII.
        {"# B: -1, C: 0, L: 0, c: 0\nThello\n", "position"},    // This position is negative.
        {"# B: 99999999999999999999999999, C: 0, L: 0, c: 0\nT\n", "position"}, // This position is too large to read.
        // A token cannot start at this position. The reader in `src/wire.c` can read the position. The token text
        // pushes the end past `SIZE_MAX`. The end then wraps around. The marks would hand a caller a span running
        // backwards.
        {"# B: 18446744073709551615, C: 0, L: 1, c: 0\nThi\n", "position"},
        {"# B: 0, C: 18446744073709551615, L: 1, c: 0\nThi\n", "position"},
        // This unparsed-invalid token holds bytes that begin a character. `ys_wire_write` refuses to write such a
        // token. The reader refuses to read such a token back. The reader treats such a token as a broken wire, like an
        // escape outside the codepoint range.
        {"# B: 0, C: 0, L: 1, c: 0\n~\\xc3\\xa9\n", "unparsed-invalid"}, // These escapes encode e with an acute accent.
        {"# B: 0, C: 0, L: 1, c: 0\n~\\x41\n", "unparsed-invalid"},      // This escape encodes 'A'.
        {"# B: 0, C: 0, L: 1, c: 0\n~q\n", "escape"},     // This unparsed-invalid unit is not a byte escape.
        {"# B: 0, C: 0, L: 1, c: 0\n~\\xZZ\n", "escape"}, // This byte escape has digits that are not hex digits.
    };

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        wire_buffer wire = {{0}, 0, 0};
        wire.size = strlen(cases[index].wire);
        memcpy(wire.bytes, cases[index].wire, wire.size);

        ys_bytes_reader reader = {wire_read, NULL, &wire};
        ys_token_source *tokens = ys_new_yeast_stream_reader(reader, NULL);
        TEST_ASSERT(tokens != NULL);

        ys_token token;
        TEST_CHECK(ys_read_token(tokens, &token) == YS_OK); // The reader hands the fault over as an error token.
        TEST_MSG("case %zu was accepted: %s", index, cases[index].wire);
        TEST_CHECK(token.code == YS_CODE_ERROR);
        TEST_CHECK(token.text != NULL && strstr(token.text, cases[index].reason) != NULL);
        TEST_MSG("case %zu said: %s", index, token.text);
        TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION); // The next read fails.

        ys_delete_token_source(tokens);
    }
}

// `ys_wire_write` writes a byte of an unparsed-invalid token as `\xXX`. The reader hands back the raw bytes themselves.
// The token holds a span of a pair of bytes.
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
    TEST_CHECK(token.end.byte_offset - token.start.byte_offset == 2); // The span counts raw bytes.
    TEST_CHECK((unsigned char)token.text[0] == 0x80u && (unsigned char)token.text[1] == 0x81u);
    TEST_CHECK(token.end.char_offset - token.start.char_offset ==
               2); // The character offset advances by a character per raw byte.
    TEST_CHECK(token.start.byte_offset == 3 && token.start.column == 3);

    ys_delete_token_source(tokens);
}

// The test reads back the bytes the FILE* writer adapter wrote. A writer built with `YS_BORROW` has no close.
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
    TEST_CHECK(borrowed.close == NULL); // `YS_BORROW` sets no close callback.
}

// The test checks the file-descriptor writer adapter. A pipe passes the bytes. The test writes into the write end of
// the pipe and reads the same bytes at the read end.
#ifndef _WIN32
static void test_fd_writer(void) {
    int ends[2];
    TEST_ASSERT(pipe(ends) == 0);

    ys_bytes_writer writer = ys_fd_writer(ends[1], YS_OWN);
    TEST_CHECK(writer.write(writer.context, "hi", 2) == 2);
    writer.close(writer.context);

    char buffer[8];
    TEST_CHECK(read(ends[0], buffer, sizeof(buffer)) == 2);
    TEST_CHECK(memcmp(buffer, "hi", 2) == 0);
    TEST_CHECK(read(ends[0], buffer, sizeof(buffer)) == 0);
    TEST_CHECK(close(ends[0]) == 0);

    ys_bytes_writer borrowed = ys_fd_writer(1, YS_BORROW);
    TEST_CHECK(borrowed.close == NULL); // `YS_BORROW` sets no close callback.
}
#endif

// A writer with no write callback is a bad argument, and the answer is EINVAL. The constructor closes an owned writer
// even so. A reader with no read callback closes the same way when `ys_new_yeast_stream_reader` refuses it.
//
// The constructor closes the writer when the allocator refuses, and the answer is ENOMEM. The constructor takes the
// writer whether or not it succeeds.
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
    TEST_CHECK(is_closed && errno == ENOMEM); // The allocator refused. The call closed the writer anyway.
}

// Deleting a sink reports its writer's close. This test gives the writer `note_close` as its close. The source's
// delete, `ys_delete_token_source`, reports its reader's close the same way.
static void test_sink_delete_reports_close_failure(void) {
    wire_buffer wire = {{0}, 0, 0};
    ys_bytes_writer owned = {wire_write, note_close, &wire}; // `note_close` fails with EIO.
    ys_token_sink *sink = ys_new_yeast_stream_writer(owned, NULL);
    TEST_ASSERT(sink != NULL);

    is_closed = false;
    errno = 0;
    TEST_CHECK(ys_delete_token_sink(sink) == YS_FAILED_STREAM);
    TEST_CHECK(is_closed);
    TEST_CHECK(errno == EIO);

    TEST_CHECK(ys_delete_token_sink(NULL) == YS_OK); // Deleting NULL returns `YS_OK`.
}

// An error is not a token the emitter can emit. `ys_write_token` refuses an error handed to an emitter with
// `YS_FAILED_ACTION` and EINVAL.
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
    TEST_CHECK(out.size == 0);

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

// The function reads bytes already in memory. It feeds a yeast stream reader what a file would have held.
static ptrdiff_t file_bytes_read(void *context, char *into, size_t size) {
    file_bytes *source = context;
    size_t left = source->size - source->offset;
    size_t take = size < left ? size : left;
    memcpy(into, source->bytes + source->offset, take);
    source->offset += take;
    return (ptrdiff_t)take;
}

// Read a whole file into a freshly-malloc'd buffer, or fail the test. The caller frees the buffer.
static char *slurp(const char *path, size_t *size) {
    FILE *file = fopen(path, "rb");
    TEST_ASSERT_HINT(file != NULL);
    TEST_ASSERT(fseek(file, 0, SEEK_END) == 0);
    long length = ftell(file);
    TEST_ASSERT_HINT(length >= 0); // Fail the test on a negative length before the allocation below.
    TEST_ASSERT(fseek(file, 0, SEEK_SET) == 0);
    char *bytes = malloc((size_t)length + 1);
    TEST_ASSERT_HINT(bytes != NULL);
    TEST_ASSERT(fread(bytes, 1, (size_t)length, file) == (size_t)length);
    TEST_CHECK(fclose(file) == 0);
    *size = (size_t)length;
    return bytes;
}

// A fixture named `name` begins past a prefix of `input`. The byte length of that prefix is the answer. The `.p=N` in
// the name counts the UTF-8 characters of that prefix. The count walks the characters rather than adding them. A name
// without a `.p=N` begins at the first byte.
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
        } while (offset < input_size && ((unsigned char)input[offset] & 0xC0u) == 0x80u);
        characters--;
    }
    return offset;
}

// The test runs the round-trip through the emitter. The emitter renders a fixture's tokens as YAML. A filter above the
// emitter drops the errors. The YAML reconstructs the input the tokens came from. A fixture begins past a prefix. The
// render leaves that prefix out. `reached_through` reads a character count off the `.p=N` in the fixture's name. It
// walks that many UTF-8 characters of the input to find the byte length of the prefix.
//
// A fixture's `.output` is a yeast wire. A yeast stream reader reads such a wire back, and the emitter renders the
// tokens it yields. That pass is the yeast -> YAML half of the pipeline. The test runs that pass over the corpus. It
// skips the fixtures whose names hold `.invalid.`.
static void test_emitter_reconstructs_fixtures(void) {
    DIR *directory = opendir(YS_SPEC_DIR);
    TEST_ASSERT_HINT(directory != NULL);

    size_t checked = 0;
    struct dirent *entry;
    while ((entry = readdir(directory)) != NULL) {
        size_t name_length = strlen(entry->d_name);
        if (name_length < 7 || strcmp(entry->d_name + name_length - 7, ".output") != 0) {
            continue; // The test reads a token stream. A `.input` file sits beside its `.output` file.
        }
        if (strstr(entry->d_name, ".invalid.") != NULL) {
            continue; // The test skips an invalid fixture.
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
                continue; // The emitter refuses an error token. The test drops an error token before the emitter.
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

// A wire stream whose last line has no newline still yields its token. A token source handed a reader under `YS_OWN`
// closes the file when `ys_delete_token_source` runs.
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
        TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION);
        ys_delete_token_source(tokens); // The delete closes the file the reader owns.
    }

    ys_delete_token_source(NULL);
}

// A cap may be too small for the input. A reader under such a cap reports failure rather than growing past that cap.
// The constructor refuses a cap too small to build a reader under.
static void test_wire_memory_cap(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *line = "# B: 0, C: 0, L: 0, c: 0\nThello\n";
    wire.size = strlen(line);
    memcpy(wire.bytes, line, wire.size);
    ys_bytes_reader reader = {wire_read, NULL, &wire};

    ys_options tiny = {{0}, YS_RESUME_NONE, 8}; // The cap is smaller than the reader.
    TEST_CHECK(ys_new_yeast_stream_reader(reader, &tiny) == NULL);

    ys_options capped = {{0}, YS_RESUME_NONE, 512}; // The cap fits the reader and leaves no room for a buffer.
    ys_token_source *tokens = ys_new_yeast_stream_reader(reader, &capped);
    TEST_ASSERT(tokens != NULL);

    // The reader reports `YS_FAILED_MEMORY` with `ENOMEM`.
    ys_token token;
    errno = 0;
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_MEMORY);
    TEST_CHECK(errno == ENOMEM);
    TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION);

    ys_delete_token_source(tokens);
}

// The allocations `counted_reallocate` still grants. A test sets this count, and the allocation after the last of them
// fails.
static size_t buffers_left;

// The reallocate refuses a call once it has granted `buffers_left` calls. It fails a growth at a chosen depth.
static void *counted_reallocate(void *context, void *pointer, size_t size) {
    (void)context;
    if (buffers_left == 0) {
        errno = ENOMEM; // An allocator that returns NULL sets errno.
        return NULL;
    }
    buffers_left--;
    return realloc(pointer, size);
}

// The reader's text buffer is under the same cap as its line buffer. The reader reports a text failure as
// `YS_FAILED_MEMORY` with errno ENOMEM.
// The text grows through `ys_append` for a character token, and through `ys_append_byte` for an unparsed-invalid one.
static void test_wire_text_out_of_memory(void) {
    static const char *wires[] = {
        "# B: 0, C: 0, L: 0, c: 0\nThello\n",
        "# B: 0, C: 0, L: 1, c: 0\n~\\x80\n",
    };
    for (size_t index = 0; index < sizeof(wires) / sizeof(wires[0]); index++) {
        wire_buffer wire = {{0}, 0, 0};
        wire.size = strlen(wires[index]);
        memcpy(wire.bytes, wires[index], wire.size);

        buffers_left = 1; // The allocator grants the line buffer and refuses the text.
        ys_allocator allocator = {NULL, counted_reallocate, NULL, NULL, NULL};
        ys_options options = {allocator, YS_RESUME_NONE, 0};
        ys_bytes_reader reader = {wire_read, NULL, &wire};
        ys_token_source *tokens = ys_new_yeast_stream_reader(reader, &options);
        TEST_ASSERT(tokens != NULL);

        ys_token token;
        errno = 0;
        TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_MEMORY);
        TEST_CHECK(errno == ENOMEM);
        TEST_CHECK(ys_read_token(tokens, &token) == YS_FAILED_ACTION);

        ys_delete_token_source(tokens);
    }
}

// The wire reader NUL-terminates its text buffer even for a token with no text. That terminator costs an allocation. An
// allocator refusing that allocation makes the reader return out of memory. The reader then hands back no token.
static void test_wire_terminator_out_of_memory(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *written = "# B: 0, C: 0, L: 0, c: 0\nS\n"; // `YS_WIRE` writes a begin-scalar marker as `S`. A
                                                           // begin-scalar marker has no text.
    wire.size = strlen(written);
    memcpy(wire.bytes, written, wire.size);

    buffers_left =
        1; // The allocator grants a single buffer. The line buffer takes it. The terminator finds no buffer left.
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

// A read that hands back at most a single wire token per call, and no more bytes than the caller asked for. This read
// feeds a stream reader the way a real stream does.
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

// `ys_wire_write` writes lower-case hexadecimal. A stream written by another hand may use upper-case. Upper-case
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
    TEST_CHECK(token.end.byte_offset == 5);
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
