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
        if (filled == YS_FILL_OUT_OF_MEMORY) {
            ys_parser_halt(parser, YS_CODE_ERROR_MEMORY, ys_message(YS_MESSAGE_OUT_OF_MEMORY));
            return false;
        }
        if (filled == YS_FILL_READER_FAILED) {
            ys_parser_halt(parser, YS_CODE_ERROR_READER, ys_message(YS_MESSAGE_READER_FAILED));
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
    if (ys_is_error_code(pending.code)) {
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
    ys_memory memory;
    ys_parser *parser = ys_memory_new(&memory, options, sizeof(ys_parser));
    if (parser != NULL) {
        parser->memory = memory;
        parser->window.bytes = YS_NO_BYTES;
        parser->error.resume = options != NULL ? options->resume : YS_RESUME_NONE;
        parser->state = YS_STATE_START;
    }
    return parser;
}

ys_parser *ys_new_string_parser(const char *input, size_t length, const ys_options *options) {
    if (input == NULL && length > 0) {
        errno = EINVAL; // a NULL buffer of nonzero length is not an empty input, it is a mistake
        return NULL;
    }
    ys_parser *parser = ys_new_parser(options); // sets errno on failure
    if (parser != NULL) {
        // The window is the caller's buffer, whole: there is no reader to give it more, and no buffer of its own to
        // free. That is what makes a string parser's tokens stable — their text is the caller's bytes.
        if (input != NULL) {
            parser->window.bytes = (const uint8_t *)input;
        }
        parser->window.source.size = length;
        parser->window.source.is_at_end = true;
    }
    return parser;
}

ys_parser *ys_new_stream_parser(ys_reader reader, const ys_options *options) {
    // The reader is handed over whether or not the parser can be built, so an owned one is closed here rather than
    // leaked. errno is set after the close and preserved across it, so the close cannot overwrite the reason.
    if (reader.read == NULL) {
        if (reader.close != NULL) {
            reader.close(reader.context);
        }
        errno = EINVAL; // a reader with nothing to read from
        return NULL;
    }
    ys_parser *parser = ys_new_parser(options); // sets errno on failure
    if (parser == NULL) {
        if (reader.close != NULL) {
            int saved_errno = errno; // the close must not overwrite the reason ys_new_parser gave
            reader.close(reader.context);
            errno = saved_errno;
        }
        return NULL;
    }
    parser->window.source.reader = reader;
    return parser;
}

bool ys_are_tokens_stable(const ys_parser *parser) {
    // A string parser is exactly a parser with nowhere to read more input from.
    return parser->window.source.reader.read == NULL;
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
        if (parser->window.source.reader.close != NULL) {
            parser->window.source.reader.close(parser->window.source.reader.context);
        }
        // The messages are static and the window may be the caller's; what is the parser's own is what it grew.
        ys_allocator allocator = parser->memory.allocator;
        ys_source_free(&parser->window.source, &allocator);
        ys_deallocate(&allocator, parser->queue.tokens);
        ys_deallocate(&allocator, parser->stack.frames);
        ys_deallocate(&allocator, parser);
    }
}
