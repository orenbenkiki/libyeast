// SPDX-License-Identifier: MIT
#include "parser.h"
#include "memory.h"
#include "messages.h"
#include <errno.h>
#include <stdint.h>
#include <string.h>

// A window with no buffer of its own yet still has readable bytes to point at — none of them. Pointing at nothing is
// not the same as pointing nowhere: NULL plus zero is undefined, and the sanitizers say so.
static const uint8_t YS_NO_BYTES[1] = {0};

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

bool ys_parser_fill(ys_parser *parser, size_t wanted) {
    ys_window *window = &parser->window;
    while (!window->source.is_at_end && ys_window_readable(window) < wanted) {
        // The bytes before the oldest token the parser still holds are wanted no longer, and the fill discards them
        // whether or not it goes on to read anything, so the window moves past them either way.
        size_t used = ys_parser_retained(parser) - window->base;
        ys_fill filled = ys_source_fill(&window->source, &parser->memory, used, 0);
        window->base += used;
        if (window->source.bytes != NULL) {
            window->bytes = window->source.bytes;
        }
        // A failed fill records the fault; ys_read_token() reports it and marks the source done, so the reader is not
        // reached again. The allocator's failure is ENOMEM; the reader's is whatever it left, passed through.
        if (filled == YS_FILL_OUT_OF_MEMORY) {
            parser->fault = YS_FAILED_MEMORY;
            errno = ENOMEM;
            return false;
        }
        if (filled == YS_FILL_READER_FAILED) {
            parser->fault = YS_FAILED_STREAM;
            return false;
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

    ys_pending *grown =
        ys_memory_grow(memory, queue->tokens, &queue->capacity, queue->count + 1, YS_MEMORY_ITEMS, sizeof(ys_pending));
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
        ys_frame *grown = ys_memory_grow(memory, stack->frames, &stack->capacity, stack->depth + 1, YS_MEMORY_ITEMS,
                                         sizeof(ys_frame));
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

void ys_parser_fail(ys_parser *parser, const char *message) {
    ys_parser_error(parser, YS_CODE_ERROR, message);
}

// --- The parser. ---

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
    // The rest of the arm is the zeroed state ys_memory_new left — fault 0, is_done false, an empty queue and stack.
    parser->memory = memory;
    parser->window.bytes = YS_NO_BYTES;
    parser->error.resume = ys_resolved_options(options).resume;
    parser->state = YS_STATE_START;
}

int ys_parser_read(ys_parser *parser, ys_token *token) {
    if (parser->is_done) {
        errno = ENODATA; // the stream ended, or the source faulted, and is being read past
        return YS_FAILED_EOF;
    }

    // The automaton is not generated yet, so there is nothing to step and every call fails the same way: a format
    // error, which a real parse would carry on past by its resume policy but which here has nothing to carry on to.
    if (!ys_queue_is_ready(&parser->queue) && parser->error.message == NULL) {
        ys_parser_fail(parser, ys_message(YS_MESSAGE_NOT_IMPLEMENTED));
    }

    // A fill during the step above may have failed: the source is spent, and the failure is the return value, not a
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
        return 0;
    }

    // The error goes behind every token the queue holds. Handing it back clears it — there is no longer an error to
    // hand back — and whether the parse carries on past it is ys_error::resume's business. The message it points at is
    // static, so the token stays good however long the caller keeps it.
    *token = ys_parser_token(parser, parser->error.token);
    parser->error.message = NULL;
    return 0;
}
