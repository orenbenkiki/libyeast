// SPDX-License-Identifier: MIT
#include "parser.h"
#include "alloc.h"
#include "messages.h"
#include <stdint.h>
#include <string.h>

// What a buffer holds the first time it is grown. The window's is a page, since it is filled by a read; the queue's and
// the stack's are small, since most documents never reach past them.
#define YS_WINDOW_BYTES 4096
#define YS_QUEUE_TOKENS 16
#define YS_STACK_FRAMES 16

// A window with no buffer of its own yet still has readable bytes to point at — none of them. Pointing at nothing is
// not the same as pointing nowhere: NULL plus zero is undefined, and the sanitizers say so.
static const uint8_t YS_NO_BYTES[1] = {0};

// --- Memory. ---

bool ys_memory_reserve(ys_memory *memory, size_t wanted) {
    if (memory->max_bytes != 0 && wanted > memory->max_bytes - memory->allocated_bytes) {
        return false;
    }
    memory->allocated_bytes += wanted;
    return true;
}

void ys_memory_release(ys_memory *memory, size_t bytes) {
    memory->allocated_bytes -= bytes;
}

void *ys_memory_grow(ys_memory *memory, void *items, size_t *capacity, size_t wanted, size_t item_size) {
    if (*capacity >= wanted) {
        return items;
    }
    size_t doubled = *capacity <= SIZE_MAX / 2 && *capacity * 2 > wanted ? *capacity * 2 : wanted;
    if (doubled > SIZE_MAX / item_size) {
        return NULL; // UNTESTED
    }
    size_t more = (doubled - *capacity) * item_size;
    if (!ys_memory_reserve(memory, more)) {
        return NULL;
    }
    void *grown = ys_reallocate(&memory->allocator, items, doubled * item_size);
    if (grown == NULL) {
        ys_memory_release(memory, more);
        return NULL;
    }
    *capacity = doubled;
    return grown;
}

// --- The window over the input. ---

void ys_window_advance(ys_window *window, ys_run run) {
    window->mark.byte_offset += run.bytes;
    window->mark.char_offset += run.characters;
    window->mark.column += run.characters;
}

void ys_window_break(ys_window *window, ys_run run) {
    window->mark.byte_offset += run.bytes;
    window->mark.char_offset += run.characters;
    window->mark.line += 1;
    window->mark.column = 0;
}

// The offset, in the whole input, of the oldest byte the parser still needs: where the first token it has built but not
// handed back begins, or where the parser itself has reached, if it has built none.
static size_t ys_parser_retained(const ys_parser *parser) {
    if (parser->queue.count > 0) {
        return parser->queue.tokens[parser->queue.head].start.byte_offset;
    }
    return parser->window.mark.byte_offset;
}

// Make room in the window's buffer to read into: discard the bytes no token points at any more, and grow it if that
// leaves no room. Halts the parse if the cap or the allocator refuses.
static bool ys_window_make_room(ys_parser *parser) {
    ys_window *window = &parser->window;
    size_t discard = ys_parser_retained(parser) - window->base;
    if (discard > 0) {
        memmove(window->buffer, window->buffer + discard, window->size - discard);
        window->size -= discard;
        window->base += discard;
    }
    if (window->size < window->capacity) {
        return true;
    }

    size_t wanted = window->capacity == 0 ? YS_WINDOW_BYTES : window->capacity + 1;
    uint8_t *grown = ys_memory_grow(&parser->memory, window->buffer, &window->capacity, wanted, sizeof(uint8_t));
    if (grown == NULL) {
        ys_parser_halt(parser, YS_CODE_ERROR_MEMORY, ys_message(YS_MESSAGE_OUT_OF_MEMORY));
        return false;
    }
    window->buffer = grown;
    window->bytes = grown;
    return true;
}

bool ys_parser_fill(ys_parser *parser, size_t wanted) {
    ys_window *window = &parser->window;
    while (!window->is_at_end && ys_window_readable(window) < wanted) {
        if (!ys_window_make_room(parser)) {
            return false;
        }
        ptrdiff_t read_count = window->reader.read(window->reader.context, (char *)window->buffer + window->size,
                                                   window->capacity - window->size);
        if (read_count < 0) {
            ys_parser_halt(parser, YS_CODE_ERROR_READER, ys_message(YS_MESSAGE_READER_FAILED));
            return false;
        }
        if (read_count == 0) {
            window->is_at_end = true;
        } else {
            window->size += (size_t)read_count;
        }
    }
    return true;
}

// --- The queue of tokens built but not yet handed back. ---

// Make room for one more token: drop the space the tokens already handed back left behind, and grow the queue if that
// leaves no room.
static bool ys_queue_make_room(ys_memory *memory, ys_queue *queue) {
    if (queue->head + queue->count < queue->capacity) {
        return true;
    }
    if (queue->head > 0) {
        // Which always makes room, since the tokens handed back left `head` slots behind them, and there is one at
        // least. So the queue grows only when it is full from its very first slot.
        memmove(queue->tokens, queue->tokens + queue->head, queue->count * sizeof(ys_pending));
        queue->head = 0;
        return true;
    }

    size_t wanted = queue->capacity == 0 ? YS_QUEUE_TOKENS : queue->capacity + 1;
    ys_pending *grown = ys_memory_grow(memory, queue->tokens, &queue->capacity, wanted, sizeof(ys_pending));
    if (grown == NULL) {
        return false;
    }
    queue->tokens = grown;
    return true;
}

bool ys_queue_emit(ys_memory *memory, ys_queue *queue, ys_code code, ys_mark start, ys_mark end) {
    if (!ys_queue_make_room(memory, queue)) {
        return false;
    }
    ys_pending *token = queue->tokens + queue->head + queue->count;
    token->code = code;
    token->start = start;
    token->end = end;
    queue->count++;
    if (!queue->is_run_open) {
        queue->resolved++;
    }
    return true;
}

void ys_queue_open_run(ys_queue *queue) {
    queue->is_run_open = true;
}

ys_pending *ys_queue_run(ys_queue *queue, size_t *count) {
    *count = queue->count - queue->resolved;
    return queue->tokens + queue->head + queue->resolved;
}

void ys_queue_inject(ys_queue *queue, ys_code code, ys_mark start, ys_mark end) {
    queue->ahead.code = code;
    queue->ahead.start = start;
    queue->ahead.end = end;
    queue->has_ahead = true;
}

void ys_queue_resolve_run(ys_queue *queue) {
    queue->resolved = queue->count;
    queue->is_run_open = false;
}

ys_pending ys_queue_pop(ys_queue *queue) {
    if (queue->has_ahead) {
        queue->has_ahead = false;
        return queue->ahead;
    }
    ys_pending token = queue->tokens[queue->head];
    queue->head++;
    queue->count--;
    queue->resolved--;
    return token;
}

// --- The stack of productions the parser is inside. ---

bool ys_stack_push(ys_memory *memory, ys_stack *stack, ys_state return_state, ptrdiff_t indent) {
    if (stack->depth == stack->capacity) {
        size_t wanted = stack->capacity == 0 ? YS_STACK_FRAMES : stack->capacity + 1;
        ys_frame *grown = ys_memory_grow(memory, stack->frames, &stack->capacity, wanted, sizeof(ys_frame));
        if (grown == NULL) {
            return false;
        }
        stack->frames = grown;
    }
    stack->frames[stack->depth].return_state = return_state;
    stack->frames[stack->depth].indent = indent;
    stack->depth++;
    return true;
}

ys_frame ys_stack_pop(ys_stack *stack) {
    stack->depth--;
    return stack->frames[stack->depth];
}

// --- Failures. ---

// The error token: it consumes nothing, so it sits at where the parser has reached, and its text is the message.
static void ys_parser_error(ys_parser *parser, ys_code code, const char *message) {
    parser->error.message = message;
    parser->error.token.code = code;
    parser->error.token.start = parser->window.mark;
    parser->error.token.end = parser->window.mark;
}

void ys_parser_halt(ys_parser *parser, ys_code code, const char *message) {
    ys_parser_error(parser, code, message);
    parser->error.is_halted = true;
}

void ys_parser_fail(ys_parser *parser, const char *message) {
    ys_parser_error(parser, YS_CODE_ERROR_FORMAT, message);
}

// --- The parser. ---

ys_token ys_parser_token(const ys_parser *parser, ys_pending pending) {
    ys_token token;
    token.code = pending.code;
    token.start = pending.start;
    token.end = pending.end;
    if (pending.code == YS_CODE_ERROR_FORMAT || pending.code == YS_CODE_ERROR_MEMORY ||
        pending.code == YS_CODE_ERROR_READER) {
        token.text = parser->error.message;
    } else if (pending.end.byte_offset > pending.start.byte_offset) {
        const uint8_t *at = parser->window.bytes + (pending.start.byte_offset - parser->window.base);
        token.text = (const char *)at;
    } else {
        token.text = NULL;
    }
    return token;
}

static ys_parser *ys_new_parser(const ys_options *options) {
    ys_options resolved = options != NULL ? *options : (ys_options){0};
    ys_memory memory = {resolved.allocator, resolved.max_bytes, 0};
    if (!ys_memory_reserve(&memory, sizeof(ys_parser))) {
        return NULL;
    }
    ys_parser *parser = ys_allocate(&memory.allocator, sizeof(*parser));
    if (parser != NULL) {
        *parser = (ys_parser){0};
        parser->memory = memory;
        parser->window.bytes = YS_NO_BYTES;
        parser->error.resume = resolved.resume;
        parser->state = YS_STATE_START;
    }
    return parser;
}

ys_parser *ys_new_string_parser(const char *input, size_t length, const ys_options *options) {
    ys_parser *parser = ys_new_parser(options);
    if (parser != NULL) {
        // The window is the caller's buffer, whole: there is no reader to give it more, and no buffer of its own to
        // free. That is what makes a string parser's tokens stable — their text is the caller's bytes.
        if (input != NULL) {
            parser->window.bytes = (const uint8_t *)input;
        }
        parser->window.size = length;
        parser->window.is_at_end = true;
    }
    return parser;
}

ys_parser *ys_new_stream_parser(ys_reader reader, const ys_options *options) {
    ys_parser *parser = ys_new_parser(options);
    if (parser != NULL) {
        parser->window.reader = reader;
    }
    return parser;
}

bool ys_are_tokens_stable(const ys_parser *parser) {
    // A string parser is exactly a parser with nowhere to read more input from.
    return parser->window.reader.read == NULL;
}

ys_token ys_next_token(ys_parser *parser) {
    if (ys_parser_is_halted(parser)) {
        return ys_parser_token(parser, parser->error.token);
    }
    if (!ys_queue_is_ready(&parser->queue) && parser->error.message == NULL) {
        // The automaton is not generated yet, so there is nothing to step and every call fails the same way.
        ys_parser_fail(parser, ys_message(YS_MESSAGE_NOT_IMPLEMENTED));
    }
    if (ys_queue_is_ready(&parser->queue)) {
        return ys_parser_token(parser, ys_queue_pop(&parser->queue));
    }

    // The error goes behind every token the queue holds. Handing it back clears it — there is no longer an error to
    // hand back — and whether the parse carries on past it is ys_error::resume's business. The message it points at is
    // static, so the token stays good however long the caller keeps it.
    ys_token token = ys_parser_token(parser, parser->error.token);
    parser->error.message = NULL;
    return token;
}

void ys_free_parser(ys_parser *parser) {
    if (parser != NULL) {
        if (parser->window.reader.close != NULL) {
            parser->window.reader.close(parser->window.reader.context);
        }
        // The messages are static and the window may be the caller's; what is the parser's own is what it grew.
        ys_allocator allocator = parser->memory.allocator;
        ys_deallocate(&allocator, parser->window.buffer);
        ys_deallocate(&allocator, parser->queue.tokens);
        ys_deallocate(&allocator, parser->stack.frames);
        ys_deallocate(&allocator, parser);
    }
}
