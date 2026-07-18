// SPDX-License-Identifier: MIT
#ifndef YEAST_PARSER_H
#define YEAST_PARSER_H

#include "decoder.h"
#include "memory.h"
#include "source.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <yeast.h>

// The parser is one automaton, driven by characters, emitting yeast tokens into a queue. Its whole execution state is
// ys_parser: a window over the input, a stack of the productions it is inside, a queue of the tokens it has built, and
// the state it is in. Nothing of it lives in the C call stack, and that is what lets ys_next_token() hand back a token
// from the middle of a production and resume there on the next call.
//
// There is no second layer. A scanner emitting tokens for a parser to consume would need a vocabulary between them, and
// yeast has none: the automaton's output already is the token stream — the Begin/End markers and the classified spans
// of input. A hand-written scanner would also be the one thing on the hot path the grammar did not derive.

// A state of the automaton. The states themselves are the grammar's, generated into parser_tables.h; this is only their
// type. The automaton begins in state 0, which is the obligation that names it here rather than there.
typedef uint16_t ys_state;
#define YS_STATE_START ((ys_state)0)

// --- The window over the input. ---

// The input, and where the parser has reached in it.
//
// A string parser's window is the caller's buffer: it is never copied, never grown and never freed, and it holds the
// whole input from its first byte, so `base` stays 0 and the source has no reader to give it more. A stream parser's
// window is the source's buffer, holding the bytes it has read and not yet discarded — it keeps every byte a token in
// the queue still points at, and `base` says where in the input its first byte is.
typedef struct ys_window {
    const uint8_t *bytes; // the readable bytes: the caller's input, or the source's buffer once it has one
    ys_source source;     // where more bytes come from, and the buffer they land in
    size_t base;          // the offset, in the whole input, of bytes[0]
    ys_mark mark;         // where the next unread character is, in the whole input
} ys_window;

// The bytes the window has read and the parser has not yet consumed.
static inline size_t ys_window_readable(const ys_window *window) {
    return window->source.size - (window->mark.byte_offset - window->base);
}

// The first of them, which is where the next character is.
static inline const uint8_t *ys_window_at(const ys_window *window) {
    return window->bytes + (window->mark.byte_offset - window->base);
}

// Consume a run of characters, none of which is a line break — which is every run the grammar scans, as
// generator/check_decoder.py holds it to.
void ys_window_advance(ys_window *window, ys_run run);

// Consume a line break, which is one line break however many characters it spans: "\r\n" is two.
void ys_window_break(ys_window *window, ys_run run);

// --- The queue of tokens built but not yet handed back. ---

// A token the parser has built. It holds no text: the window's buffer moves as it grows and compacts, so a token's text
// is worked out from its marks and the window at the moment it is handed back — which is the only moment it is valid.
// An error's text is not in the input at all, and is the parser's own message.
typedef struct ys_pending {
    ys_code code;
    ys_mark start;
    ys_mark end;
} ys_pending;

// The tokens the parser has built but not yet handed back. The first `resolved` of them are decided; the rest are the
// open run, whose codes may still change and ahead of which a marker may still be injected.
//
// That the undecided tokens are a suffix is not an accident. Both runs that cause one — the empty lines that open a
// block scalar, and the line of an implicit key — are decided by something that has not been read yet, and both resolve
// the same way: the run's codes are rewritten, and a marker is injected ahead of the run (`end-scalar` for the block
// scalar whose empty lines were chomped away, `begin-mapping` for the line that turned out to be a key). Only one run
// is open at a time, since neither of the two nests inside the other.
//
// The injected marker is `ahead`, a token of its own rather than one of `tokens`, and it needs no room made for it: it
// always precedes every token in the queue, because the automaton runs only while the queue's first token is undecided,
// so anything decided ahead of the run has already been handed back. Which is also why injecting one cannot fail for
// want of memory — and it must not, since a run that could not be resolved would strand every token it had built.
typedef struct ys_queue {
    ys_pending *tokens;
    size_t head;      // where the first token sits in `tokens`
    size_t count;     // how many tokens there are
    size_t resolved;  // how many of them are decided
    size_t capacity;  // how many tokens `tokens` holds
    bool is_run_open; // the tokens past `resolved` are undecided, and the next one emitted joins them
    ys_pending ahead; // the marker injected ahead of them all
    bool has_ahead;   // there is one
} ys_queue;

// Whether a token is there to be handed back.
static inline bool ys_queue_is_ready(const ys_queue *queue) {
    return queue->has_ahead || queue->resolved > 0;
}

// Add a token to the back of the queue. It is decided, unless a run is open, in which case it joins the run. False if
// the cap or the allocator refused the room for it.
bool ys_queue_emit(ys_memory *memory, ys_queue *queue, ys_code code, ys_mark start, ys_mark end);

// Open a run: the tokens emitted from now on are undecided.
void ys_queue_open_run(ys_queue *queue);

// The `count` tokens of the open run, whose codes the parser rewrites when it learns what they were. The pointer is
// into the queue's own array, so emitting another token invalidates it: rewrite the run, then carry on.
ys_pending *ys_queue_run(ys_queue *queue, size_t *count);

// Put a decided token ahead of the run, and of everything else in the queue.
void ys_queue_inject(ys_queue *queue, ys_code code, ys_mark start, ys_mark end);

// Close the run: its tokens are decided, and may be handed back.
void ys_queue_resolve_run(ys_queue *queue);

// Take the token ys_queue_is_ready() says is there.
ys_pending ys_queue_pop(ys_queue *queue);

// --- The stack of productions the parser is inside. ---

// The `n` of a production that has none.
#define YS_NO_INDENT ((ptrdiff_t)-2)

// A production the parser is inside. `indent` is its `n` — the grammar's one runtime parameter, the other being
// resolved away when the parser is generated — and `return_state` is where the automaton resumes when the production
// matches. The indentation is signed because a block sequence nested directly in a mapping is entered at `n - 1`, which
// is -1 when the mapping is at the left margin.
typedef struct ys_frame {
    ys_state return_state;
    ptrdiff_t indent;
} ys_frame;

// The productions the parser is inside, innermost last. Nothing but ys_options::max_bytes bounds its depth: no quantity
// of input does, since a document nests as deeply as it says it does.
typedef struct ys_stack {
    ys_frame *frames;
    size_t depth;
    size_t capacity;
} ys_stack;

// The innermost production's frame.
static inline const ys_frame *ys_stack_top(const ys_stack *stack) {
    return &stack->frames[stack->depth - 1];
}

// Enter a production with parameter `n`, to resume at `return_state` when it matches.
bool ys_stack_push(ys_memory *memory, ys_stack *stack, ys_state return_state, ptrdiff_t indent);

// Leave the innermost production, and hand back the frame it ran in.
ys_frame ys_stack_pop(ys_stack *stack);

// --- Failures. ---

// What the parser failed with, and what it does about it.
//
// Every message is a static string, from messages.h or — for what depends on the grammar, the production the parser was
// inside and what it expected there — from the table the generator writes into parser_tables.h. So nothing is allocated
// on the failure path, which the failure that runs out of memory could not do anyway; nothing is freed; and there is no
// lifetime to explain, a token's text being either the input's or a static string's, so that ys_are_tokens_stable() is
// true of every code alike. What the parser found is not in the message, and does not need to be: the first
// YS_CODE_UNPARSED_TEXT token behind the error begins at exactly the byte that failed.
//
// The error token, like the injected marker, is a token of its own and not one of the queue's — it always follows every
// token in it — so queueing it needs no room either, and reporting a malformed document cannot fail for want of memory.
typedef struct ys_error {
    const char *message; // the error's text, and what says there is an error to hand back at all; NULL when there is
                         // not. Handing a format error back clears it, the parse carrying on past one.
    ys_pending token;    // the error's token, handed back behind everything the queue holds
    ys_resume resume;    // what the parser does with the input after a malformed document
} ys_error;

// --- The parser. ---

// The parsing arm of a ys_token_source: the whole execution state of the automaton, none of it in the C call stack, so
// ys_read_token() can hand back a token from the middle of a production and resume there on the next call.
typedef struct ys_parser {
    ys_memory memory;
    ys_window window;
    ys_queue queue;
    ys_stack stack;
    ys_error error;
    ys_state state; // where the automaton is
    int fault;      // a resource failure ys_read_token() is to report: 0 none, -1 the reader failed, -2 the allocator
    bool is_done;   // the source is spent — the stream ended, or it faulted — and answers -3 from here on
} ys_parser;

// Initialize a freshly-allocated parser arm: `memory` is what it may allocate, and `options` gives the resume policy.
// The window's bytes and reader are the constructor's to set, string or stream.
void ys_parser_init(ys_parser *parser, ys_memory memory, const ys_options *options);

// Read the next token into `token`: 0 with it filled, -1 the reader failed, -2 the allocator did (with `errno` the
// callback's), YS_FAILED_EOF with `errno` ENODATA once the stream has ended and been read past.
int ys_parser_read(ys_parser *parser, ys_token *token);

// Make at least `wanted` bytes readable, reading from the source if need be — so that the decoder never sees a
// character the window's edge cut in half, and a run of them is never cut into two tokens. Fewer bytes are readable
// only at the end of the input. False if a fill failed — the source's reader, or the cap or the allocator — which sets
// ys_parser::fault and ends the source.
bool ys_parser_fill(ys_parser *parser, size_t wanted);

// Hand back a malformed document: the error token, with `message` as its text, at where the parser has reached. What
// the parser does with the rest of the input is ys_error::resume. An open run must be resolved before this is called:
// an error decides one, since it ends the block scalar or the key line the run was waiting on.
void ys_parser_fail(ys_parser *parser, const char *message);

// The token a pending one is: its text is the input the window still holds, or the parser's message when it is an
// error, or nothing at all when it is a zero-width marker.
ys_token ys_parser_token(const ys_parser *parser, ys_pending pending);

#endif // YEAST_PARSER_H
