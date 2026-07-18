// SPDX-License-Identifier: MIT
#ifndef YEAST_WIRE_H
#define YEAST_WIRE_H

#include "memory.h"
#include "source.h"
#include <stdbool.h>
#include <stddef.h>
#include <yeast.h>

// The wire-replay arm of a ys_token_source: it reads the yeast wire format back into tokens, the inverse of
// ys_write_token(). It owns the storage a token's text is unescaped into, which is why it is state and not a function.
typedef struct ys_wire {
    ys_memory memory;
    ys_source source;     // the wire's bytes, and the buffer they land in
    size_t consumed;      // how many of them the lines handed back have taken
    size_t scanned;       // how far the search for the current line's break has looked; never before `consumed`
    size_t wire_line;     // how many lines of the wire have been handed back; the current one is one past it
    char *text;           // the text of the token last read, unescaped
    size_t text_size;     // how many bytes of it there are
    size_t text_capacity; // how many the text buffer holds
    int fault;            // resource failure ys_next_line hit: 0 none, YS_FAILED_STREAM reader, YS_FAILED_MEMORY alloc
    bool is_done;         // the wire is spent — it ended, a malformed token was handed back, or it faulted — no more
} ys_wire;

// Ready a freshly-allocated wire arm to replay through `memory`; the rest is the zeroed state ys_memory_new left. The
// caller sets ys_wire::source's reader after.
void ys_wire_init(ys_wire *wire, ys_memory memory);

// Read the next token off the wire into `token`. Returns YS_OK with `token` filled — a malformed wire is a
// YS_CODE_ERROR token like a malformed document, and the wire is spent after it; YS_FAILED_STREAM if the reader failed,
// YS_FAILED_MEMORY if the allocator did, with `errno` the callback's; YS_FAILED_ACTION with `errno` ENODATA once the
// wire has ended and been read past.
int ys_wire_read(ys_wire *wire, ys_token *token);

// Write `token` to `writer` in the yeast wire format — the wire-writer arm of a token sink. YS_OK if it was written;
// YS_FAILED_STREAM with `errno` the writer's, or YS_FAILED_ACTION with `errno` EINVAL if the token cannot be written.
int ys_wire_write(ys_bytes_writer *writer, ys_token token);

// Write a whole buffer to a writer, handling short writes: true if all of it reached the writer, false with `errno` set
// if the write callback failed. Shared by the wire writer and the YAML emitter.
bool ys_put(ys_bytes_writer *writer, const char *bytes, size_t size);

#endif // YEAST_WIRE_H
