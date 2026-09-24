// SPDX-License-Identifier: MIT
// Reading and writing the yeast wire format. The state a replay holds, and the calls `wire.c` implements over it.

#ifndef YEAST_WIRE_H
#define YEAST_WIRE_H

#include "memory.h"
#include "source.h"
#include <stdbool.h>
#include <stddef.h>
#include <yeast.h>

// The wire-replay arm of a `ys_token_source`. The arm reads the yeast wire format back into tokens. That inverts
// `ys_write_token`. The arm owns the storage that holds a token's text. The arm unescapes that text as it reads the
// token. The arm is state and not a function.
typedef struct ys_wire {
    ys_memory memory;
    ys_source source; // the wire's bytes, and the buffer they land in.
    size_t consumed;  // the count of bytes taken by the lines handed back.
    size_t scanned;   // the point the search for the current line break has reached. That search starts at `consumed`.
    size_t wire_line; // the count of wire lines handed back. The current line sits just past that count.
    char *text;       // the unescaped text of the token last read.
    size_t text_size; // the size of that text in bytes.
    size_t text_capacity; // the size the text buffer holds.
    // a resource failure that `ys_next_line` hit. `0` for none. `YS_FAILED_STREAM` names the reader and
    // `YS_FAILED_MEMORY` names the allocator.
    int fault;
    bool is_done; // the wire is spent. The wire ended, a malformed token went back to the caller, or the arm faulted.
} ys_wire;

// Ready a freshly-allocated wire arm to replay through `memory`. A field this leaves alone keeps the zeroed state
// `ys_memory_new` left. The caller sets the reader of `ys_wire::source` after.
void ys_wire_init(ys_wire *wire, ys_memory memory);

// Read the next token off the wire into `token`. Returns `YS_OK` with `token` filled. A malformed wire comes back as a
// `YS_CODE_ERROR` token, the way a malformed document does. That spends the wire. Returns `YS_FAILED_STREAM` where the
// reader failed. Returns `YS_FAILED_MEMORY` where the allocator failed, and `errno` holds the callback's value.
// Returns `YS_FAILED_ACTION` with `errno` ENODATA once the wire has ended and a caller reads past that end.
int ys_wire_read(ys_wire *wire, ys_token *token);

// Write `token` to `writer` in the yeast wire format. This is the wire-writer arm of a token sink. `YS_OK` where the
// write landed. `YS_FAILED_STREAM` with `errno` the writer's value. `YS_FAILED_ACTION` with `errno` EINVAL where the
// token has no wire form.
int ys_wire_write(ys_bytes_writer *writer, ys_token token);

// Write a whole buffer to a writer, and handle a short write. `YS_OK` where the whole buffer reached the writer, and
// `YS_FAILED_STREAM` with `errno` set where the write callback failed. The wire writer and the YAML emitter share this.
int ys_put(ys_bytes_writer *writer, const char *bytes, size_t size);

#endif // YEAST_WIRE_H
