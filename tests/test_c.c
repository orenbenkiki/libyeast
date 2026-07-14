// SPDX-License-Identifier: MIT
#include "acutest.h"
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

// The parser is not implemented yet: any pull yields a "not implemented" error at the first character, and the parser
// then stays halted on that error.
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
    TEST_CHECK(again.code == YS_CODE_ERROR_FORMAT); // halted: the same error, forever

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

static void *failing_allocate(void *context, size_t size) {
    (void)context;
    (void)size;
    return NULL;
}

static void test_alloc_failure(void) {
    ys_allocator allocator = {failing_allocate, NULL, NULL, NULL};
    ys_options options = {allocator, YS_RESUME_NONE, 0};
    TEST_CHECK(ys_new_string_parser("x", 1, &options) == NULL);
    TEST_CHECK(ys_new_stream_parser(ys_fp_reader(stdin, YS_BORROW), &options) == NULL);
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
    static const struct {
        const char *name;
        ys_code code;
        const char *text;
        size_t size;
    } cases[] = {
        {"an indicator", YS_CODE_INDICATOR, "-", 1},
        {"a zero-width marker", YS_CODE_BEGIN_SCALAR, NULL, 0},
        {"content", YS_CODE_TEXT, "hello", 5},
        {"a backslash, which must be escaped", YS_CODE_TEXT, "a\\b", 3},
        {"a line break, which must be escaped", YS_CODE_LINE_FEED, "\n", 1},
        {"two-byte non-ASCII", YS_CODE_TEXT, "\xC3\xA9", 2},             // U+00E9
        {"three-byte non-ASCII", YS_CODE_TEXT, "\xE4\xB8\x80", 3},       // U+4E00
        {"beyond the basic plane", YS_CODE_TEXT, "\xF0\x9F\x98\x80", 4}, // U+1F600
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

// A stream that is not the yeast wire format is rejected, not misread.
static void test_wire_rejects_rubbish(void) {
    static const char *cases[] = {
        "not a token at all\n",
        "# B: 0, C: 0, L: 0, c: 0\n",         // a position with no token after it
        "# B: nonsense\nT\n",                 // a position that does not parse
        "# B: 0, C: 0, L: 0, c: 0\n\xFF\n",   // a code character that is not one
        "# B: 0, C: 0, L: 0, c: 0\nT\\q\n",   // an escape that is not one
        "# B: 0, C: 0, L: 0, c: 0\nT\\x2\n",  // an escape cut short
        "# B: 0, C: 0, L: 0, c: 0\nT\\xZZ\n", // an escape whose digits are not digits
    };

    for (size_t index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        wire_buffer wire = {{0}, 0, 0};
        wire.size = strlen(cases[index]);
        memcpy(wire.bytes, cases[index], wire.size);

        ys_reader reader = {wire_read, NULL, &wire};
        ys_token_reader *tokens = ys_new_token_reader(reader, NULL);
        TEST_ASSERT(tokens != NULL);
        ys_token token;
        TEST_CHECK(!ys_read_token(tokens, &token));
        TEST_MSG("case %zu was accepted: %s", index, cases[index]);
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

// A reader held under a cap it cannot meet reports failure rather than growing past it.
static void test_wire_memory_cap(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *line = "# B: 0, C: 0, L: 0, c: 0\nThello\n";
    wire.size = strlen(line);
    memcpy(wire.bytes, line, wire.size);

    ys_options options = {
        {NULL, NULL, NULL, NULL}, YS_RESUME_NONE, 64}; // too small for the reader and any buffer at all
    ys_reader reader = {wire_read, NULL, &wire};
    ys_token_reader *tokens = ys_new_token_reader(reader, &options);
    TEST_ASSERT(tokens != NULL);

    ys_token token;
    TEST_CHECK(!ys_read_token(tokens, &token)); // it cannot buffer a line, so it reads no token
    ys_free_token_reader(tokens);
}

// The wire is written with upper-case hexadecimal, but a stream written by another hand may use lower-case, and it
// means the same thing.
static void test_wire_reads_lower_case_hex(void) {
    wire_buffer wire = {{0}, 0, 0};
    const char *written = "# B: 0, C: 0, L: 0, c: 0\nT\\xe9\\u4e00\n";
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
    {"wire_rejects_rubbish", test_wire_rejects_rubbish},
    {"wire_odds_and_ends", test_wire_odds_and_ends},
    {"wire_memory_cap", test_wire_memory_cap},
    {"wire_reads_lower_case_hex", test_wire_reads_lower_case_hex},
    {"alloc_failure", test_alloc_failure},
    {"free_null", test_free_null},
    {"fp_reader", test_fp_reader},
#ifndef _WIN32
    {"fd_reader", test_fd_reader},
    {"fp_writer", test_fp_writer},
#ifndef _WIN32
    {"fd_writer", test_fd_writer},
#endif
#endif
    {"counting_allocator", test_counting_allocator},
    {NULL, NULL},
};
