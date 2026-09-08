// SPDX-License-Identifier: MIT
// The operations over a `ys_parser`'s state. Advancing the window. Filling and draining the token queue. Popping the
// production stack. Building a token from a pending token. Planting an error token where the parser has reached.

#include "parser.h"
#include "memory.h"
#include "messages.h"
#include <errno.h>
#include <stdint.h>
#include <string.h>

// A window with no buffer of its own still has readable bytes to point at. That count comes to `0`. Pointing at
// nothing differs from pointing at no address. The C standard leaves NULL plus `0` undefined, and the sanitizers say
// so.
static const uint8_t YS_NO_BYTES[1] = {0};

// --- Moving the window.

void ys_window_advance(ys_window *window, ys_consumed taken) {
    window->mark.byte_offset += taken.bytes;
    window->mark.char_offset += taken.characters;
    window->mark.column += taken.characters;
}

void ys_window_break(ys_window *window, ys_consumed taken) {
    window->mark.byte_offset += taken.bytes;
    window->mark.char_offset += taken.characters;
    window->mark.line += 1;
    window->mark.column = 0;
}

// The offset, in the whole input, of the oldest byte the parser still needs. That is where the first token it has
// built but not handed back begins. With no token built, it is where the parser itself has reached.
static size_t ys_parser_retained(const ys_parser *parser) {
    if (parser->queue.count > 0) {
        return parser->queue.tokens[parser->queue.head].start.byte_offset;
    }
    return parser->window.mark.byte_offset;
}

int ys_parser_fill(ys_parser *parser, size_t wanted) {
    ys_window *window = &parser->window;
    while (!window->source.is_at_end && ys_window_readable(window) < wanted) {
        // The bytes before the oldest token the parser still holds are wanted no longer. The fill discards them
        // whether or not it goes on to read anything. The window moves past them either way.
        size_t used = ys_parser_retained(parser) - window->base;
        ys_fill filled = ys_source_fill(&window->source, &parser->memory, used, 0);
        window->base += used;
        if (window->source.bytes != NULL) {
            window->bytes = window->source.bytes;
        }
        // A failed fill records the fault. ys_read_token() reports it and marks the source done. The reader is not
        // reached again. The allocator's failure is ENOMEM; the reader's is whatever it left, passed through.
        if (filled == YS_FILL_OUT_OF_MEMORY) {
            parser->fault = YS_FAILED_MEMORY;
            errno = ENOMEM;
            return YS_FAILED_MEMORY;
        }
        if (filled == YS_FILL_READER_FAILED) {
            parser->fault = YS_FAILED_STREAM;
            return YS_FAILED_STREAM;
        }
    }
    return YS_OK;
}

// --- Filling and draining the queue.

// Make room for a further token. Drop the space the tokens already handed back left behind, and grow the queue if
// that leaves no room.
static int ys_queue_make_room(ys_memory *memory, ys_queue *queue) {
    if (queue->head + queue->count < queue->capacity) {
        return YS_OK;
    }
    if (queue->head > 0) {
        // That makes room. The tokens handed back left `head` slots behind them, and there is one at least. So the
        // queue grows only when it is full from its very first slot.
        memmove(queue->tokens, queue->tokens + queue->head, queue->count * sizeof(ys_pending));
        queue->head = 0;
        return YS_OK;
    }

    ys_pending *grown =
        ys_memory_grow(memory, queue->tokens, &queue->capacity, queue->count + 1, YS_MEMORY_ITEMS, sizeof(ys_pending));
    if (grown == NULL) {
        return YS_FAILED_MEMORY;
    }
    queue->tokens = grown;
    return YS_OK;
}

int ys_queue_emit(ys_memory *memory, ys_queue *queue, ys_code code, ys_mark start, ys_mark end) {
    if (ys_queue_make_room(memory, queue) != YS_OK) {
        return YS_FAILED_MEMORY;
    }
    ys_pending *token = queue->tokens + queue->head + queue->count;
    token->code = code;
    token->start = start;
    token->end = end;
    queue->count++;
    if (!queue->is_run_open) {
        queue->resolved++;
    }
    return YS_OK;
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

// --- Pushing and popping frames.

int ys_stack_push(ys_memory *memory, ys_stack *stack, ys_state return_state, ptrdiff_t indent) {
    if (stack->depth == stack->capacity) {
        ys_frame *grown = ys_memory_grow(memory, stack->frames, &stack->capacity, stack->depth + 1, YS_MEMORY_ITEMS,
                                         sizeof(ys_frame));
        if (grown == NULL) {
            return YS_FAILED_MEMORY;
        }
        stack->frames = grown;
    }
    stack->frames[stack->depth].return_state = return_state;
    stack->frames[stack->depth].indent = indent;
    stack->depth++;
    return YS_OK;
}

ys_frame ys_stack_pop(ys_stack *stack) {
    stack->depth--;
    return stack->frames[stack->depth];
}

// --- Reporting a failure.

// The error token. It consumes nothing. The token sits where the parser has reached, and its text is the message.
static void ys_parser_error(ys_parser *parser, ys_code code, const char *message) {
    parser->error.message = message;
    parser->error.token.code = code;
    parser->error.token.start = parser->window.mark;
    parser->error.token.end = parser->window.mark;
}

void ys_parser_fail(ys_parser *parser, const char *message) {
    ys_parser_error(parser, YS_CODE_ERROR, message);
}

// --- Driving the parser.

ys_token ys_parser_token(const ys_parser *parser, ys_pending pending) {
    ys_token token;
    token.code = pending.code;
    token.start = pending.start;
    token.end = pending.end;
    if (pending.code == YS_CODE_ERROR) {
        token.text = parser->error.message;
    } else if (pending.end.byte_offset > pending.start.byte_offset) {
        const uint8_t *at = parser->window.bytes + (pending.start.byte_offset - parser->window.base);
        token.text = (const char *)at;
    } else {
        token.text = NULL;
    }
    return token;
}

void ys_parser_init(ys_parser *parser, ys_memory memory, const ys_options *options) {
    // The rest of the arm is the zeroed state ys_memory_new left. A fault of `0`, is_done false, an empty queue and
    // stack.
    parser->memory = memory;
    parser->window.bytes = YS_NO_BYTES;
    parser->error.resume = ys_resolved_options(options).resume;
    parser->state = YS_STATE_START;
}

int ys_parser_read(ys_parser *parser, ys_token *token) {
    if (parser->is_done) {
        errno = ENODATA; // the stream ended, or the source faulted, and is being read past
        return YS_FAILED_ACTION;
    }

    // The automaton is not generated yet. There is nothing to step, and a call fails the same way each time. It is a
    // format error. A real parse would continue past it by its resume policy. Here there is nothing to continue to.
    if (!ys_queue_is_ready(&parser->queue) && parser->error.message == NULL) {
        ys_parser_fail(parser, ys_message(YS_MESSAGE_NOT_IMPLEMENTED));
    }

    // A fill during the step above may have failed. The source is spent, and the failure is the return value, not a
    // token. errno was set where the fill failed.
    if (parser->fault != 0) {
        parser->is_done = true;
        return parser->fault;
    }

    if (ys_queue_is_ready(&parser->queue)) {
        *token = ys_parser_token(parser, ys_queue_pop(&parser->queue));
        if (token->code == YS_CODE_END_STREAM) {
            parser->is_done = true; // the stream is closed; the next call is the end
        }
        return YS_OK;
    }

    // The error goes behind the tokens the queue holds. Handing it back clears it. There is no longer an error to
    // hand back. Whether the parse continues past it is ys_error::resume's business. The message that it points at is
    // static, and the token stays good however long the caller keeps it.
    *token = ys_parser_token(parser, parser->error.token);
    parser->error.message = NULL;
    return YS_OK;
}
