// SPDX-License-Identifier: MIT
// A single automaton parses. Characters drive it, and the automaton emits yeast tokens into a queue. `ys_parser` holds
// the whole execution state. That state is a window over the input and a stack of the productions the parser is
// inside. The state also holds a queue of the tokens the parser has built, and the state the automaton is in.
//
// The C call stack holds none of that state. ys_next_token() can therefore hand back a token from the middle of a
// production and resume there on the next call.
//
// The parser has a single layer. A scanner emitting tokens for a parser to consume would need a vocabulary between the
// scanner and the parser, and yeast writes none. The automaton's output already is the token stream. That stream is
// the Begin/End markers and the classified spans of input. A hand-written scanner would also sit on the hot path, and
// the grammar would not derive it.

#ifndef YEAST_PARSER_H
#define YEAST_PARSER_H

#include "decoder.h"
#include "memory.h"
#include "source.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <yeast.h>

// A state of the automaton. The grammar gives the states themselves, and the generator writes them into
// parser_tables.h. This is only their type. The automaton begins in the state `YS_STATE_START` names, and this file
// writes that state rather than the generated tables.
typedef uint16_t ys_state;
#define YS_STATE_START ((ys_state)0) // the state a parse opens in. This file names it rather than the generated tables.

// --- The window over the input.

// The input, and where the parser has reached in it.
//
// A string parser's window is the caller's buffer. Nobody copies, grows or frees that buffer. The window holds the
// whole input from its first byte. `base` stays `0`, and the source has no reader to give the window more.
//
// A stream parser's window is the source's buffer. That buffer holds the bytes the source has read and has not yet
// discarded. It keeps a byte a token in the queue still points at. `base` says where in the input the buffer's first
// byte sits.
typedef struct ys_window {
    const uint8_t *bytes; // the readable bytes. That is the caller's input, or the buffer the source allocated.
    ys_source source;     // the origin of more bytes, and the buffer those bytes land in.
    size_t base;          // the offset of `bytes[0]` in the whole input.
    ys_mark mark;         // the offset of the next unread character in the whole input.
} ys_window;

// The bytes the window has read and the parser has not yet consumed.
static inline size_t ys_window_readable(const ys_window *window) {
    return window->source.size - (window->mark.byte_offset - window->base);
}

// The first readable byte. The next character starts there.
static inline const uint8_t *ys_window_at(const ys_window *window) {
    return window->bytes + (window->mark.byte_offset - window->base);
}

// Move past what a consume took. Such a consume holds no line break, and generator/check_decoder.py holds the grammar
// to that.
void ys_window_advance(ys_window *window, ys_consumed taken);

// Move past a line break. A break is a single break however many characters it spans. `\r\n` spans a pair.
void ys_window_break(ys_window *window, ys_consumed taken);

// --- The queue of tokens built but not yet handed back.

// A token the parser has built. It holds no text. The window's buffer moves as the buffer grows and compacts. The
// parser works a token's text out from the token's marks and the window at the moment it hands the token back. The
// text is valid then and no later. An error's text is not in the input at all, and is the parser's message.
typedef struct ys_pending {
    ys_code code;
    ys_mark start;
    ys_mark end;
} ys_pending;

// The tokens the parser has built and has not yet handed back. The queue has decided the first `resolved` of them. The
// tokens past that count are the open run. The parser may still change a run's codes, and may still inject a marker
// ahead of the run.
//
// The undecided tokens form a suffix, and that is no accident. A pair of runs make such a suffix. The empty lines that
// open a block scalar are the first, and the line of an implicit key is the second. Input the parser has still to read
// decides a run.
//
// A run resolves the same way. The parser rewrites the run's codes and injects a marker ahead of the run. That marker
// is `end-scalar` for a block scalar whose empty lines the parser chomped away. It is `begin-mapping` for a line that
// turned out to be a key. A single run is open at a time, and a run does not nest inside another run.
//
// The injected marker is `ahead`, a token of its own rather than a token `tokens` holds. The queue therefore needs no
// room for it. The marker precedes what the queue holds. The automaton runs only while the queue's first token belongs
// to the open run. A decided token ahead of the run has already gone back to the caller.
//
// Injecting a marker therefore cannot fail for want of memory. A run the parser could not resolve would strand the
// tokens the run had built.
typedef struct ys_queue {
    ys_pending *tokens;
    size_t head;      // the place the first token sits in `tokens`.
    size_t count;     // the count of tokens in the queue.
    size_t resolved;  // the count of tokens the queue has decided.
    size_t capacity;  // the count of tokens `tokens` has room for.
    bool is_run_open; // the tokens past `resolved` belong to the open run, and the next token the parser emits joins
                      // them.
    ys_pending ahead; // the marker the parser injected ahead of the queue.
    bool has_ahead;   // `ahead` holds a marker.
} ys_queue;

// Whether the queue holds a token to hand back.
static inline bool ys_queue_is_ready(const ys_queue *queue) {
    return queue->has_ahead || queue->resolved > 0;
}

// Add a token to the back of the queue. The queue decides that token, unless a run is open. An open run takes the
// token instead. YS_OK, or YS_FAILED_MEMORY where the cap or the allocator refused the room for it.
int ys_queue_emit(ys_memory *memory, ys_queue *queue, ys_code code, ys_mark start, ys_mark end);

// Open a run. The run takes the tokens the parser emits from here on. The parser has still to decide those tokens.
void ys_queue_open_run(ys_queue *queue);

// The `count` tokens of the open run. The parser rewrites the codes of those tokens once the parser learns what the
// run held. The pointer points into the queue's array. Emitting another token invalidates the pointer. Rewrite the
// run, then continue.
ys_pending *ys_queue_run(ys_queue *queue, size_t *count);

// Put a decided token ahead of the run, and of the rest of the queue.
void ys_queue_inject(ys_queue *queue, ys_code code, ys_mark start, ys_mark end);

// Close the run. The queue has decided the run's tokens, and hands them back on demand.
void ys_queue_resolve_run(ys_queue *queue);

// Take the token ys_queue_is_ready() says is there.
ys_pending ys_queue_pop(ys_queue *queue);

// --- The stack of productions the parser is inside.

// The `n` of a production that has none.
#define YS_NO_INDENT ((ptrdiff_t)-2)

// A production the parser is inside. `indent` is its `n`. `n` is the runtime parameter the grammar still holds, and
// the generator resolves the other parameter away. `return_state` is the state the automaton resumes in when the
// production matches.
//
// The indentation is signed. The parser enters a block sequence nested directly in a mapping at `n - 1`. That is `-1`
// where the mapping begins at the left margin.
typedef struct ys_frame {
    ys_state return_state;
    ptrdiff_t indent;
} ys_frame;

// The productions the parser is inside. The innermost production sits last. `ys_options::max_bytes` bounds the
// depth. The size of the input places no bound on it. A document nests as deeply as the document says.
typedef struct ys_stack {
    ys_frame *frames;
    size_t depth;
    size_t capacity;
} ys_stack;

// The innermost production's frame.
static inline const ys_frame *ys_stack_top(const ys_stack *stack) {
    return &stack->frames[stack->depth - 1];
}

// Enter a production with parameter `n`, to resume at `return_state` when it matches. YS_OK, or YS_FAILED_MEMORY where
// the cap or the allocator refused the frame.
int ys_stack_push(ys_memory *memory, ys_stack *stack, ys_state return_state, ptrdiff_t indent);

// Leave the innermost production, and hand back the frame it ran in.
ys_frame ys_stack_pop(ys_stack *stack);

// --- Failures.

// The failure the parser stopped on, and the policy the parser follows after that failure.
//
// A message is a static string, from messages.h or from the table the generator writes into parser_tables.h. That
// table holds what depends on the grammar. That is the production the parser was inside and what the parser expected
// there.
//
// The failure path therefore allocates nothing. A failure that runs out of memory could not allocate anyway. The
// failure path frees nothing. A lifetime needs no explaining here. A token's text comes from the input or from a
// static string, and ys_are_tokens_stable() is true whatever the code.
//
// The message does not say what the parser found, and does not need to. The first YS_CODE_UNPARSED_TEXT token behind
// the error begins at exactly the byte that failed.
//
// The error token, like the injected marker, is a token of its own rather than a token the queue holds. It follows
// what the queue holds. Queueing the error needs no room either. Reporting a malformed document therefore cannot fail
// for want of memory.
typedef struct ys_error {
    const char *message; // the error's text. A NULL message says the parser has no error to hand back. Handing a
                         // format error back clears the message, and the parse continues.
    ys_pending token;    // the error's token. The parser hands it back behind the tokens the queue holds.
    ys_resume resume;    // the policy the parser follows for the input after a malformed document.
} ys_error;

// --- The parser.

// The parsing arm of a ys_token_source. The whole execution state of the automaton, with no part of it in the C call
// stack. ys_read_token() can hand back a token from the middle of a production and resume there on the next call.
typedef struct ys_parser {
    ys_memory memory; // the allocator it may use, and the cap on how much.
    ys_window window; // the readable bytes and where the next character sits among them.
    ys_queue queue;   // the tokens built and not yet handed back, the undecided ones among them.
    ys_stack stack;   // the frames the automaton must give back on its way out. The innermost frame sits last.
    ys_error error;   // the failure a malformed document came to, and the policy for the rest of the input.
    ys_state state;   // the state the automaton is in.
    // A resource failure ys_read_token() is to report. `0` for none, YS_FAILED_STREAM where the reader failed, and
    // YS_FAILED_MEMORY where the allocator did. The name goes here rather than the number, and ys_status names the
    // numbers.
    int fault;
    // the source has nothing left, and answers YS_FAILED_ACTION from here on. The stream ended, or the source faulted.
    bool is_done;
} ys_parser;

// Initialize a freshly-allocated parser arm. `memory` is what it may allocate, and `options` gives the resume policy.
// The constructor sets the window's bytes and reader. That holds for a string source and for a stream source.
void ys_parser_init(ys_parser *parser, ys_memory memory, const ys_options *options);

// Read the next token into `token`. YS_OK with it filled. YS_FAILED_STREAM the reader failed. YS_FAILED_MEMORY the
// allocator did. `errno` holds the callback's value. YS_FAILED_ACTION with `errno` ENODATA once a caller reads past
// the end of the stream.
int ys_parser_read(ys_parser *parser, ys_token *token);

// Make at least `wanted` bytes readable. The parser reads from the source where it must. The decoder then does not see
// a character the window's edge cut in half. A consume of those characters then stays a single token. Fewer bytes are
// readable only at the end of the input.
//
// YS_OK, or the fault a failed fill sets in `ys_parser::fault` before ending the source. That is YS_FAILED_STREAM where
// the source's reader failed, and YS_FAILED_MEMORY where the cap or the allocator did.
int ys_parser_fill(ys_parser *parser, size_t wanted);

// Hand back a malformed document. That is the error token, with `message` as its text, at where the parser has
// reached. `ys_error::resume` says what the parser does with the rest of the input.
//
// A caller resolves an open run before calling this. An error decides a run. That ends the block scalar or the key
// line the run waited on.
void ys_parser_fail(ys_parser *parser, const char *message);

// The token a pending token comes to. Its text is the input the window still holds. The text is the parser's message
// where the token is an error. A zero-width marker has no text.
ys_token ys_parser_token(const ys_parser *parser, ys_pending pending);

#endif // YEAST_PARSER_H
