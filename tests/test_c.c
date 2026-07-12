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
    TEST_CHECK(token.code == YS_CODE_ERROR);
    TEST_CHECK(token.text != NULL && strcmp(token.text, "not implemented") == 0);
    TEST_CHECK(token.start.byte_offset == 0 && token.start.char_offset == 0);
    TEST_CHECK(token.start.line == 0 && token.start.column == 0);
    TEST_CHECK(token.end.byte_offset == token.start.byte_offset);

    ys_token again = ys_next_token(parser);
    TEST_CHECK(again.code == YS_CODE_ERROR); // halted: the same error, forever

    ys_free_parser(parser);
}

// A stream parser routed through the counting allocator, which lets the test assert nothing leaked.
static void test_stream_parser(void) {
    ys_counting_allocator *counter = ys_new_counting_allocator();
    TEST_ASSERT(counter != NULL);
    ys_options options = {ys_counting_allocator_functions(counter), 0};

    FILE *file = tmpfile();
    TEST_ASSERT(file != NULL);
    ys_parser *parser = ys_new_stream_parser(ys_fp_reader(file, YS_OWN), &options);
    TEST_ASSERT(parser != NULL);
    TEST_CHECK(!ys_are_tokens_stable(parser)); // stream input: text is valid only until the next call
    TEST_CHECK(ys_counting_allocator_live_buffers(counter) == 1);

    TEST_CHECK(ys_next_token(parser).code == YS_CODE_ERROR);

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
    ys_options options = {allocator, 0};
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

TEST_LIST = {
    {"ys_version_matches_components", test_ys_version_matches_components},
    {"string_parser", test_string_parser},
    {"stream_parser", test_stream_parser},
    {"alloc_failure", test_alloc_failure},
    {"free_null", test_free_null},
    {"fp_reader", test_fp_reader},
#ifndef _WIN32
    {"fd_reader", test_fd_reader},
#endif
    {"counting_allocator", test_counting_allocator},
    {NULL, NULL},
};
