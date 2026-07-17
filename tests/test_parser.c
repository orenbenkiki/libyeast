// SPDX-License-Identifier: MIT
#include "acutest.h"
#include "messages.h"
#include "parser.h"
#include <errno.h>
#include <string.h>

// An allocator that hands out memory but refuses to grow it, so that every ys_memory_grow() failure path is reachable
// without having to exhaust the machine.
static void *test_allocate(void *context, size_t size) {
    (void)context;
    return malloc(size);
}

static void *test_refuse_to_grow(void *context, void *pointer, size_t size) {
    (void)context;
    (void)pointer;
    (void)size;
    return NULL;
}

static void test_deallocate(void *context, void *pointer) {
    (void)context;
    free(pointer);
}

static ys_allocator ungrowable_allocator(void) {
    ys_allocator allocator = {test_allocate, test_refuse_to_grow, test_deallocate, NULL, NULL};
    return allocator;
}

static ys_mark mark_of(size_t byte_offset, size_t column) {
    ys_mark mark = {byte_offset, byte_offset, 0, column};
    return mark;
}

// The cap counts what an object allocates for itself, and refuses what would pass it. A cap it cannot even be built
// under refuses it outright, rather than leaving it to fail at its first allocation.
static void test_memory_cap(void) {
    ys_memory memory = {{0}, 100, 0};
    TEST_CHECK(ys_memory_reserve(&memory, 60));
    TEST_CHECK(memory.allocated_bytes == 60);
    TEST_CHECK(!ys_memory_reserve(&memory, 41)); // one byte past the cap
    TEST_CHECK(memory.allocated_bytes == 60);    // and a refusal charges nothing
    TEST_CHECK(ys_memory_reserve(&memory, 40));  // exactly the cap is within it

    ys_memory uncapped = {{0}, 0, 0};
    TEST_CHECK(ys_memory_reserve(&uncapped, SIZE_MAX / 2)); // 0 is no cap at all

    ys_options tight = {{0}, YS_RESUME_NONE, 4};
    ys_memory built;
    TEST_CHECK(ys_memory_new(&built, &tight, 64) == NULL); // no room for the object itself

    void *object = ys_memory_new(&built, NULL, 64);
    TEST_ASSERT(object != NULL);
    TEST_CHECK(built.allocated_bytes == 64);                                       // its own size is charged
    TEST_CHECK(((const char *)object)[0] == 0 && ((const char *)object)[63] == 0); // and it comes zeroed
    free(object);
}

// Growing doubles, never falls below the first block, charges the cap, and leaves the array alone when it cannot.
static void test_memory_grow(void) {
    ys_memory memory = {{0}, 0, 0};
    size_t capacity = 0;

    int *items = ys_memory_grow(&memory, NULL, &capacity, 1, 4, sizeof(int)); // the first block, not the one wanted
    TEST_ASSERT(items != NULL);
    TEST_CHECK(capacity == 4);
    TEST_CHECK(memory.allocated_bytes == 4 * sizeof(int));

    items[0] = 7;
    int *same = ys_memory_grow(&memory, items, &capacity, 4, 4, sizeof(int)); // already big enough: untouched
    TEST_CHECK(same == items);
    TEST_CHECK(capacity == 4);

    items = ys_memory_grow(&memory, items, &capacity, 5, 4, sizeof(int)); // doubles rather than creeping
    TEST_ASSERT(items != NULL);
    TEST_CHECK(capacity == 8);
    TEST_CHECK(items[0] == 7); // and carries what was there
    TEST_CHECK(memory.allocated_bytes == 8 * sizeof(int));

    items = ys_memory_grow(&memory, items, &capacity, 100, 4, sizeof(int)); // and past what doubling would give
    TEST_ASSERT(items != NULL);
    TEST_CHECK(capacity == 100);

    ys_memory capped = {{0}, 8, 0};
    size_t narrow = 0;
    TEST_CHECK(ys_memory_grow(&capped, NULL, &narrow, 100, 4, sizeof(int)) == NULL); // the cap refuses
    TEST_CHECK(narrow == 0);
    TEST_CHECK(capped.allocated_bytes == 0);

    ys_memory refusing = {ungrowable_allocator(), 0, 0};
    size_t none = 0;
    TEST_CHECK(ys_memory_grow(&refusing, NULL, &none, 4, 4, sizeof(int)) == NULL); // the allocator refuses
    TEST_CHECK(none == 0);
    TEST_CHECK(refusing.allocated_bytes == 0); // and the refusal is not charged

    free(items);
}

// A mark advances by bytes and by characters, which differ above ASCII; a line break resets the column and counts a
// line, however many characters it spans.
static void test_window_marks(void) {
    ys_parser *parser = ys_new_string_parser("", 0, NULL);
    TEST_ASSERT(parser != NULL);
    ys_window *window = &parser->window;

    ys_run run = {5, 3}; // three characters, five bytes
    ys_window_advance(window, run);
    TEST_CHECK(window->mark.byte_offset == 5 && window->mark.char_offset == 3);
    TEST_CHECK(window->mark.line == 0 && window->mark.column == 3);

    ys_run line_break = {2, 2}; // "\r\n" is one break of two characters
    ys_window_break(window, line_break);
    TEST_CHECK(window->mark.byte_offset == 7 && window->mark.char_offset == 5);
    TEST_CHECK(window->mark.line == 1 && window->mark.column == 0);

    ys_free_parser(parser);
}

// A string parser's window is the caller's buffer, whole, from the first call.
static void test_string_window(void) {
    const char *input = "hello";
    ys_parser *parser = ys_new_string_parser(input, 5, NULL);
    TEST_ASSERT(parser != NULL);

    TEST_CHECK(ys_are_tokens_stable(parser));
    TEST_CHECK(ys_window_readable(&parser->window) == 5);
    TEST_CHECK((const char *)ys_window_at(&parser->window) == input);
    TEST_CHECK(ys_parser_fill(parser, 4096)); // there is nothing to fill from, and that is not a failure
    TEST_CHECK(ys_window_readable(&parser->window) == 5);
    TEST_CHECK(parser->window.source.bytes == NULL); // the caller's bytes are never copied
    TEST_CHECK(parser->memory.allocated_bytes == sizeof(ys_parser));

    ys_free_parser(parser);
}

// A reader that hands out its bytes one at a time, so that filling has to loop.
typedef struct drip {
    const char *bytes;
    size_t size;
    size_t offset;
    bool is_failing;
} drip;

static ptrdiff_t drip_read(void *context, char *buffer, size_t size) {
    drip *source = context;
    if (source->is_failing) {
        return -1;
    }
    if (source->offset == source->size || size == 0) {
        return 0;
    }
    buffer[0] = source->bytes[source->offset];
    source->offset++;
    return 1;
}

static ys_reader drip_reader(drip *source) {
    ys_reader reader = {drip_read, NULL, source};
    return reader;
}

// A stream parser's window is its own: it fills from the reader, and stops filling at the end of the input.
static void test_stream_window(void) {
    drip source = {"hello", 5, 0, false};
    ys_parser *parser = ys_new_stream_parser(drip_reader(&source), NULL);
    TEST_ASSERT(parser != NULL);

    TEST_CHECK(!ys_are_tokens_stable(parser));
    TEST_CHECK(ys_window_readable(&parser->window) == 0);

    TEST_CHECK(ys_parser_fill(parser, 3));
    TEST_CHECK(ys_window_readable(&parser->window) >= 3);
    TEST_CHECK(memcmp(ys_window_at(&parser->window), "hel", 3) == 0);
    TEST_CHECK(parser->window.source.bytes != NULL);

    TEST_CHECK(ys_parser_fill(parser, 100)); // asking past the end is not a failure
    TEST_CHECK(ys_window_readable(&parser->window) == 5);
    TEST_CHECK(parser->window.source.is_at_end);

    ys_free_parser(parser);
}

// The window discards the bytes no token points at any more, and keeps every byte the queue still does.
static void test_window_compacts_to_the_queue(void) {
    drip source = {"hello", 5, 0, false};
    ys_parser *parser = ys_new_stream_parser(drip_reader(&source), NULL);
    TEST_ASSERT(parser != NULL);

    TEST_CHECK(ys_parser_fill(parser, 5));
    ys_window_advance(&parser->window, (ys_run){2, 2}); // read past "he", with nothing queued
    TEST_CHECK(ys_parser_fill(parser, 1));
    TEST_CHECK(parser->window.base == 0); // nothing was read, so nothing was discarded

    // Queue a token over "l", then consume the rest and refill: the window may drop what precedes the token, and not a
    // byte more.
    TEST_CHECK(ys_queue_emit(&parser->memory, &parser->queue, YS_CODE_TEXT, mark_of(2, 2), mark_of(3, 3)));
    ys_window_advance(&parser->window, (ys_run){3, 3});
    TEST_CHECK(ys_parser_fill(parser, 1)); // at the end of the input, which forces the read that compacts
    TEST_CHECK(parser->window.base == 2);  // the queued token's first byte, and no further

    ys_token token = ys_parser_token(parser, ys_queue_pop(&parser->queue));
    TEST_CHECK(token.text != NULL && token.text[0] == 'l'); // still the right byte after the buffer moved

    ys_free_parser(parser);
}

// A reader that fails halts the parse, for good.
static void test_reader_failure_halts(void) {
    drip source = {"hello", 5, 0, true};
    ys_parser *parser = ys_new_stream_parser(drip_reader(&source), NULL);
    TEST_ASSERT(parser != NULL);

    TEST_CHECK(!ys_parser_fill(parser, 1));
    TEST_CHECK(ys_parser_is_halted(parser));

    ys_token token = ys_next_token(parser);
    TEST_CHECK(token.code == YS_CODE_ERROR_READER);
    TEST_CHECK(token.text == ys_message(YS_MESSAGE_READER_FAILED));
    TEST_CHECK(ys_next_token(parser).code == YS_CODE_ERROR_READER); // and the same token, forever

    ys_free_parser(parser);
}

// A window that cannot grow halts the parse, out of memory.
static void test_window_out_of_memory_halts(void) {
    drip source = {"hello", 5, 0, false};
    ys_options options = {ungrowable_allocator(), YS_RESUME_NONE, 0};
    ys_parser *parser = ys_new_stream_parser(drip_reader(&source), &options);
    TEST_ASSERT(parser != NULL);

    TEST_CHECK(!ys_parser_fill(parser, 1));
    TEST_CHECK(ys_parser_is_halted(parser));
    TEST_CHECK(ys_next_token(parser).code == YS_CODE_ERROR_MEMORY);
    TEST_CHECK(ys_next_token(parser).text == ys_message(YS_MESSAGE_OUT_OF_MEMORY));

    ys_free_parser(parser);
}

// A cap smaller than the parser itself refuses to build one at all — for either kind, and because of the cap. Both are
// given something valid to read, since a constructor rejects a bad argument before it ever consults the cap, and a
// refusal for that reason would prove nothing about this one: `errno` is what tells the two apart.
static void test_cap_below_the_parser(void) {
    ys_options options = {{0}, YS_RESUME_NONE, 1};

    errno = 0;
    TEST_CHECK(ys_new_string_parser("x", 1, &options) == NULL);
    TEST_CHECK(errno == ENOMEM);
    TEST_MSG("a string parser under a cap of one byte: errno is %d, not ENOMEM", errno);

    drip source = {"x", 1, 0, false};
    errno = 0;
    TEST_CHECK(ys_new_stream_parser(drip_reader(&source), &options) == NULL);
    TEST_CHECK(errno == ENOMEM);
    TEST_MSG("a stream parser under a cap of one byte: errno is %d, not ENOMEM", errno);
}

// Tokens come back in the order they were emitted, and each carries the bytes it spans. A zero-width marker carries
// none.
static void test_queue_order(void) {
    const char *input = "ab";
    ys_parser *parser = ys_new_string_parser(input, 2, NULL);
    TEST_ASSERT(parser != NULL);
    ys_memory *memory = &parser->memory;
    ys_queue *queue = &parser->queue;

    TEST_CHECK(ys_queue_emit(memory, queue, YS_CODE_BEGIN_SCALAR, mark_of(0, 0), mark_of(0, 0)));
    TEST_CHECK(ys_queue_emit(memory, queue, YS_CODE_TEXT, mark_of(0, 0), mark_of(2, 2)));
    TEST_CHECK(ys_queue_is_ready(queue)); // nothing is undecided, so both may be handed back

    ys_token begin = ys_parser_token(parser, ys_queue_pop(queue));
    TEST_CHECK(begin.code == YS_CODE_BEGIN_SCALAR);
    TEST_CHECK(begin.text == NULL); // zero-width: it spans no bytes

    ys_token text = ys_parser_token(parser, ys_queue_pop(queue));
    TEST_CHECK(text.code == YS_CODE_TEXT);
    TEST_CHECK(text.text == input && text.end.byte_offset - text.start.byte_offset == 2);
    TEST_CHECK(!ys_queue_is_ready(queue));

    ys_free_parser(parser);
}

// An open run is undecided: its tokens are held back, their codes rewritten when the parser learns what they were, and
// a marker injected ahead of them — which is how a block scalar's chomped-away empty lines get their end-scalar.
static void test_queue_run(void) {
    ys_parser *parser = ys_new_string_parser("\n\n", 2, NULL);
    TEST_ASSERT(parser != NULL);
    ys_memory *memory = &parser->memory;
    ys_queue *queue = &parser->queue;

    ys_queue_open_run(queue);
    TEST_CHECK(ys_queue_emit(memory, queue, YS_CODE_LINE_FEED, mark_of(0, 0), mark_of(1, 1)));
    TEST_CHECK(ys_queue_emit(memory, queue, YS_CODE_LINE_FEED, mark_of(1, 0), mark_of(2, 1)));
    TEST_CHECK(!ys_queue_is_ready(queue)); // held back: nobody knows yet whether they are content

    size_t count = 0;
    ys_pending *run = ys_queue_run(queue, &count);
    TEST_CHECK(count == 2);
    for (size_t index = 0; index < count; index++) {
        run[index].code = YS_CODE_BREAK; // no content line followed, so they were chomped away
    }
    ys_queue_inject(queue, YS_CODE_END_SCALAR, mark_of(0, 0), mark_of(0, 0));
    ys_queue_resolve_run(queue);
    TEST_CHECK(ys_queue_is_ready(queue));

    // The scalar ends before the breaks that were never in it — the marker's position is what says so.
    TEST_CHECK(ys_queue_pop(queue).code == YS_CODE_END_SCALAR);
    TEST_CHECK(ys_queue_pop(queue).code == YS_CODE_BREAK);
    TEST_CHECK(ys_queue_pop(queue).code == YS_CODE_BREAK);
    TEST_CHECK(!ys_queue_is_ready(queue));

    ys_free_parser(parser);
}

// The queue grows past its first block, reuses the room the handed-back tokens leave behind, and gives up when it may
// not grow.
static void test_queue_grows(void) {
    ys_parser *parser = ys_new_string_parser("", 0, NULL);
    TEST_ASSERT(parser != NULL);

    ys_queue_open_run(&parser->queue); // hold them all, so that none of the room is ever given back
    for (size_t index = 0; index < 100; index++) {
        TEST_ASSERT(ys_queue_emit(&parser->memory, &parser->queue, YS_CODE_TEXT, mark_of(0, 0), mark_of(0, 0)));
    }
    TEST_CHECK(parser->queue.count == 100);
    TEST_CHECK(parser->queue.capacity >= 100);

    ys_queue_resolve_run(&parser->queue);
    for (size_t index = 0; index < 100; index++) {
        (void)ys_queue_pop(&parser->queue);
    }

    // Fill the block to its end, so that the only room left is what the handed-back tokens gave up at its front.
    size_t capacity = parser->queue.capacity;
    while (parser->queue.head + parser->queue.count < capacity) {
        TEST_ASSERT(ys_queue_emit(&parser->memory, &parser->queue, YS_CODE_TEXT, mark_of(0, 0), mark_of(0, 0)));
    }
    TEST_CHECK(parser->queue.head > 0);
    TEST_CHECK(ys_queue_emit(&parser->memory, &parser->queue, YS_CODE_TEXT, mark_of(0, 0), mark_of(0, 0)));
    TEST_CHECK(parser->queue.capacity == capacity); // that room was enough, so the queue did not grow
    TEST_CHECK(parser->queue.head == 0);            // it moved back to the front of its block instead

    ys_free_parser(parser);

    ys_options options = {ungrowable_allocator(), YS_RESUME_NONE, 0};
    ys_parser *refusing = ys_new_string_parser("", 0, &options);
    TEST_ASSERT(refusing != NULL);
    TEST_CHECK(!ys_queue_emit(&refusing->memory, &refusing->queue, YS_CODE_TEXT, mark_of(0, 0), mark_of(0, 0)));
    ys_free_parser(refusing);
}

// A frame carries the production's `n` and where to resume; the stack grows past its first block, and gives up when it
// may not grow.
static void test_stack(void) {
    ys_parser *parser = ys_new_string_parser("", 0, NULL);
    TEST_ASSERT(parser != NULL);

    for (size_t index = 0; index < 100; index++) {
        TEST_ASSERT(ys_stack_push(&parser->memory, &parser->stack, (ys_state)index, (ptrdiff_t)index));
    }
    TEST_CHECK(parser->stack.depth == 100);
    TEST_CHECK(ys_stack_top(&parser->stack)->return_state == 99);
    TEST_CHECK(ys_stack_top(&parser->stack)->indent == 99);

    ys_frame frame = ys_stack_pop(&parser->stack);
    TEST_CHECK(frame.return_state == 99 && frame.indent == 99);
    TEST_CHECK(parser->stack.depth == 99);

    // A block sequence at the left margin is entered at -1, and a production with no `n` carries none.
    TEST_CHECK(ys_stack_push(&parser->memory, &parser->stack, 0, -1));
    TEST_CHECK(ys_stack_top(&parser->stack)->indent == -1);
    TEST_CHECK(ys_stack_push(&parser->memory, &parser->stack, 0, YS_NO_INDENT));
    TEST_CHECK(ys_stack_top(&parser->stack)->indent == YS_NO_INDENT);

    ys_free_parser(parser);

    ys_options options = {ungrowable_allocator(), YS_RESUME_NONE, 0};
    ys_parser *refusing = ys_new_string_parser("", 0, &options);
    TEST_ASSERT(refusing != NULL);
    TEST_CHECK(!ys_stack_push(&refusing->memory, &refusing->stack, 0, 0));
    ys_free_parser(refusing);
}

// An error consumes nothing and its text is the message, not the input. It comes back behind everything the queue
// holds, and handing it back leaves the parser running.
static void test_error_behind_the_queue(void) {
    ys_parser *parser = ys_new_string_parser("ab", 2, NULL);
    TEST_ASSERT(parser != NULL);

    TEST_CHECK(ys_queue_emit(&parser->memory, &parser->queue, YS_CODE_TEXT, mark_of(0, 0), mark_of(2, 2)));
    ys_parser_fail(parser, ys_message(YS_MESSAGE_NOT_IMPLEMENTED));
    TEST_CHECK(!ys_parser_is_halted(parser)); // a malformed document does not end the parse for good

    ys_token text = ys_next_token(parser);
    TEST_CHECK(text.code == YS_CODE_TEXT); // the queue first

    ys_token error = ys_next_token(parser);
    TEST_CHECK(error.code == YS_CODE_ERROR_FORMAT);
    TEST_CHECK(error.text == ys_message(YS_MESSAGE_NOT_IMPLEMENTED));
    TEST_CHECK(error.start.byte_offset == error.end.byte_offset); // it consumed nothing
    TEST_CHECK(parser->error.message == NULL);                    // and there is no error left to hand back

    ys_free_parser(parser);
}

// The parser is not implemented yet, so every pull yields the same error at the first character.
static void test_not_implemented(void) {
    ys_parser *parser = ys_new_string_parser("hello: world\n", 13, NULL);
    TEST_ASSERT(parser != NULL);

    for (int pull = 0; pull < 3; pull++) {
        ys_token token = ys_next_token(parser);
        TEST_CHECK(token.code == YS_CODE_ERROR_FORMAT);
        TEST_CHECK(token.text != NULL && strcmp(token.text, "not implemented") == 0);
        TEST_CHECK(token.start.byte_offset == 0 && token.end.byte_offset == 0);
    }

    ys_free_parser(parser);
}

// An input of no bytes at all is a window pointing at nothing, which is not a window pointing nowhere.
static void test_empty_input(void) {
    ys_parser *parser = ys_new_string_parser(NULL, 0, NULL);
    TEST_ASSERT(parser != NULL);
    TEST_CHECK(ys_window_readable(&parser->window) == 0);
    TEST_CHECK(ys_next_char(ys_window_at(&parser->window), 0) == YS_LIT_KEY_EOF);
    ys_free_parser(parser);
}

TEST_LIST = {
    {"memory_cap", test_memory_cap},
    {"memory_grow", test_memory_grow},
    {"window_marks", test_window_marks},
    {"string_window", test_string_window},
    {"stream_window", test_stream_window},
    {"window_compacts_to_the_queue", test_window_compacts_to_the_queue},
    {"reader_failure_halts", test_reader_failure_halts},
    {"window_out_of_memory_halts", test_window_out_of_memory_halts},
    {"cap_below_the_parser", test_cap_below_the_parser},
    {"queue_order", test_queue_order},
    {"queue_run", test_queue_run},
    {"queue_grows", test_queue_grows},
    {"stack", test_stack},
    {"error_behind_the_queue", test_error_behind_the_queue},
    {"not_implemented", test_not_implemented},
    {"empty_input", test_empty_input},
    {NULL, NULL},
};
