// SPDX-License-Identifier: MIT
// `parser.h` declares the parse types. `ys_parser` holds the whole state of a parse. `ys_parser_read` reports a parse
// it cannot run.

#ifndef YEAST_PARSER_H
#define YEAST_PARSER_H

#include "decoder.h"
#include "memory.h"
#include "source.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <yeast.h>

// A state of the automaton. The automaton begins in the state `YS_STATE_START` names.
typedef uint16_t ys_state;
#define YS_STATE_START ((ys_state)0) // the state a parse opens in. This file names it rather than the generated tables.

// --- The window over the input.

// The input, and where the parser has reached in it.
//
// A string parser's window is the caller's buffer. `ys_new_yaml_memory_parser` points the window at it. The parser does
// not copy, grow or free that buffer.
//
// A stream parser's window is the buffer `ys_source` owns. `base` says where in the input the buffer's first byte
// sits.
typedef struct ys_window {
    const uint8_t *bytes; // the readable bytes. That is the caller's input, or the buffer the source allocated.
    ys_source source;     // the origin of more bytes, and the buffer those bytes land in.
    size_t base;          // the offset of `bytes[0]` in the whole input.
    ys_mark mark;         // the position of the next unread character in the whole input.
} ys_window;

// The bytes the window has read and the parser holds unconsumed.
static inline size_t ys_window_readable(const ys_window *window) {
    return window->source.size - (window->mark.byte_offset - window->base);
}

// The byte at the window's mark. The next character starts there.
static inline const uint8_t *ys_window_at(const ys_window *window) {
    return window->bytes + (window->mark.byte_offset - window->base);
}

// Move past the characters a consume took. Such a consume holds no line break, and `generator/check_decoder.py` holds
// the grammar to that.
void ys_window_advance(ys_window *window, ys_consumed taken);

// Move past a line break. The caller passes the characters the break spans in `taken`.
void ys_window_break(ys_window *window, ys_consumed taken);

// --- The queue of tokens built and still held.

// A token the parser has built. It holds no text. A stream parser's buffer moves as the buffer grows and compacts. The
// parser works a token's text out from the token's marks and the window. The parser does that at the moment it hands
// the token back. The text then stays valid for as long as `ys_are_tokens_stable` promises. An error's text is not in
// the input at all, and is the parser's message.
typedef struct ys_pending {
    ys_code code;
    ys_mark start;
    ys_mark end;
} ys_pending;

// The tokens the parser has built and still holds. The queue has decided the first `resolved` of them. The
// tokens past that count are the open run.
//
// `ys_queue_inject` puts a marker ahead of an open run. `ys_queue_resolve_run` then resolves the run.
//
// The injected marker is `ahead`. `ahead` is a token of its own rather than a token `tokens` holds. The queue needs
// no room for `ahead`. Injecting a marker therefore cannot fail for want of memory.
typedef struct ys_queue {
    ys_pending *tokens;
    size_t head;      // the place the first token sits in `tokens`.
    size_t count;     // the count of tokens in the queue.
    size_t resolved;  // the count of tokens the queue has decided.
    size_t capacity;  // the count of tokens `tokens` has room for.
    bool is_run_open; // the next token the parser emits joins the open run.
    ys_pending ahead; // the marker the parser injected ahead of the queue.
    bool has_ahead;   // `ahead` holds a marker.
} ys_queue;

// Whether the queue holds a token to hand back.
static inline bool ys_queue_is_ready(const ys_queue *queue) {
    return queue->has_ahead || queue->resolved > 0;
}

// Add a token to the back of the queue. The queue decides that token, unless a run is open. An open run takes the
// token instead. `YS_OK`, or `YS_FAILED_MEMORY` where the queue could not grow to hold it.
int ys_queue_emit(ys_memory *memory, ys_queue *queue, ys_code code, ys_mark start, ys_mark end);

// Open a run. The run takes the tokens the parser emits from here on. The parser has still to decide those tokens.
void ys_queue_open_run(ys_queue *queue);

// The `count` tokens of the open run. The pointer points into the queue's array. Emitting another token invalidates the
// pointer. Rewrite the run, then continue.
ys_pending *ys_queue_run(ys_queue *queue, size_t *count);

// Put a decided token ahead of an open run, and ahead of the rest of the queue.
void ys_queue_inject(ys_queue *queue, ys_code code, ys_mark start, ys_mark end);

// Close the run. The queue has decided the run's tokens.
void ys_queue_resolve_run(ys_queue *queue);

// Take the token `ys_queue_is_ready` says is there.
ys_pending ys_queue_pop(ys_queue *queue);

// --- The stack of productions the parser is inside.

// The `n` of a production that has none.
#define YS_NO_INDENT ((ptrdiff_t)-2)

// A frame of `ys_stack`. `indent` holds the production's `n`, signed, and `YS_NO_INDENT` where it has none.
// `return_state` holds a state, and `ys_stack_pop` gives back the whole frame.
typedef struct ys_frame {
    ys_state return_state;
    ptrdiff_t indent;
} ys_frame;

// The productions the parser is inside. The innermost production sits last. A nonzero `ys_options::max_bytes` bounds
// the depth. The size of the input places no bound on it. A document nests as deeply as the document says.
typedef struct ys_stack {
    ys_frame *frames;
    size_t depth;
    size_t capacity;
} ys_stack;

// The innermost production's frame.
static inline const ys_frame *ys_stack_top(const ys_stack *stack) {
    return &stack->frames[stack->depth - 1];
}

// Enter a production with indentation `indent`. The frame holds `return_state` for `ys_stack_pop`.
// `YS_OK`, or `YS_FAILED_MEMORY` where the cap or the allocator refused the frame.
int ys_stack_push(ys_memory *memory, ys_stack *stack, ys_state return_state, ptrdiff_t indent);

// Leave the innermost production, and hand back the frame it ran in.
ys_frame ys_stack_pop(ys_stack *stack);

// --- Failures.

// The failure the parser stopped on, and the policy stored for the input after that failure.
//
// A message is a static string from `messages.h`.
//
// The failure path therefore allocates nothing. The
// failure path frees nothing. A token's text comes from the input or from a
// static string. The error code does not change what `ys_are_tokens_stable` answers.
//
// The message does not say what the parser found.
//
// The error token is a token of its own rather than a token the queue holds. It follows
// what the queue holds. Holding the error needs no room in the queue either. Reporting a malformed document therefore
// cannot fail for want of memory.
typedef struct ys_error {
    const char *message; // the error's text. A NULL message says the parser has no error to hand back. Handing the
                         // error back clears the message. The parser does not read `resume`.
    ys_pending token;    // the error's token. The parser hands it back behind the tokens the queue holds.
    ys_resume resume;    // the policy stored for the input after a malformed document.
} ys_error;

// --- The parser.

// A `ys_parser` is the parsing arm of a `ys_token_source`. The parser holds the whole execution state of the
// automaton.
typedef struct ys_parser {
    ys_memory memory; // the allocator the parser may use, and the cap on the memory it may allocate.
    ys_window window; // the readable bytes and where the next character sits among them.
    ys_queue queue;   // the tokens built and still held. The undecided ones sit among them.
    ys_stack stack;   // the frames the automaton must give back on its way out. The innermost frame sits last.
    ys_error error;   // the failure a malformed document came to, and the policy for the rest of the input.
    ys_state state;   // the state the automaton is in.
    // A resource failure `ys_read_token` is to report. The field holds `0` for none. It holds `YS_FAILED_STREAM` where
    // the reader failed, and `YS_FAILED_MEMORY` where the cap or the allocator refused.
    int fault;
    // the source has nothing left, and answers `YS_FAILED_ACTION` from here on. The stream ended, or the source
    // faulted.
    bool is_done;
} ys_parser;

// Initialize a freshly-allocated parser arm. `memory` bounds the allocation, and `options` gives the resume policy.
// The call leaves the window's bytes at `YS_NO_BYTES`.
void ys_parser_init(ys_parser *parser, ys_memory memory, const ys_options *options);

// Read the next token into `token`. The call answers `YS_OK` with `token` filled. The call answers `YS_FAILED_STREAM`
// where the reader failed, and `errno` then holds the reader's value. The call answers `YS_FAILED_MEMORY` where the
// allocator failed, and `errno` then holds ENOMEM. The call answers `YS_FAILED_ACTION` with `errno` ENODATA once a
// caller reads past the end of the stream or past a failure.
int ys_parser_read(ys_parser *parser, ys_token *token);

// Make at least `wanted` bytes readable. The parser reads from the source where the window holds fewer than `wanted`
// bytes. Fewer bytes are readable at the end of the input, or after a failed fill.
//
// `YS_OK`, or the fault a failed fill sets in `ys_parser::fault`. That is `YS_FAILED_STREAM`
// where the source's reader failed, and `YS_FAILED_MEMORY` where the cap or the allocator did.
int ys_parser_fill(ys_parser *parser, size_t wanted);

// Hand back a malformed document. That is the error token `ys_parser_error` describes. Its text is `message`.
// `ys_error::resume` says what the parser does with the rest of the input.
//
// This leaves an open run open. `ys_parser_read` does not call `ys_queue_resolve_run` before calling this.
void ys_parser_fail(ys_parser *parser, const char *message);

// The token a pending token comes to. Its text is the input the window still holds. The text is the parser's message
// where the token is an error. A zero-width marker has no text.
ys_token ys_parser_token(const ys_parser *parser, ys_pending pending);

#endif // YEAST_PARSER_H
