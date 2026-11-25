// SPDX-License-Identifier: MIT
// Reading and writing the yeast wire format. This header declares the state a replay holds and the calls `wire.c`
// implements over it. It also declares the calls that write to a `ys_bytes_writer`.

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
    size_t consumed;  // the count of bytes in the buffer taken by the lines handed back.
    size_t scanned;   // the point the search for the current line break has reached. That search starts at `consumed`.
    size_t wire_line; // the count of wire lines handed back. The current line's number is that count.
    char *text;       // the unescaped text of the token last read.
    size_t text_size; // the size of that text in bytes.
    size_t text_capacity; // the size the text buffer holds.
    // a resource failure. `ys_next_line` sets it where the reader or the allocator fails. `ys_append` and
    // `ys_append_byte` set it where the allocator fails. `ys_wire_read` sets it there too. The value `0` means no
    // failure. `YS_FAILED_STREAM` names the reader and `YS_FAILED_MEMORY` names the allocator.
    int fault;
    bool is_done; // the wire is spent. The wire ended, a malformed token went back to the caller, or the arm faulted.
} ys_wire;

// Ready a freshly-allocated wire arm to replay through `memory`. A field this leaves alone keeps the zeroed state
// `ys_memory_new` left. The caller sets the reader of `ys_wire::source` after.
void ys_wire_init(ys_wire *wire, ys_memory memory);

// Read the next token off the wire into `token`. Returns `YS_OK` with `token` filled. A malformed wire comes back as a
// `YS_CODE_ERROR` token, the way a malformed document does. That spends the wire. Returns `YS_FAILED_STREAM` where the
// reader failed. Returns `YS_FAILED_MEMORY` with `errno` ENOMEM where the allocator failed.
// Returns `YS_FAILED_ACTION` where the wire ends, and sets no `errno`. A caller reading past that end, or past a fault,
// gets `YS_FAILED_ACTION` with `errno` ENODATA.
int ys_wire_read(ys_wire *wire, ys_token *token);

// Write `token` to `writer` in the yeast wire format. A token sink calls this for its wire writer. The result is
// `YS_OK` where the write landed. The result is `YS_FAILED_STREAM` where a write to `writer` failed. `errno` then holds
// what the writer's callback set. A callback that writes nothing need not set `errno`. The result is also
// `YS_FAILED_STREAM` where the position line overflows its buffer. The result is `YS_FAILED_ACTION` where the code of
// `token` has no wire character. The result is also `YS_FAILED_ACTION` where the text of `token` disagrees with its
// code. `errno` is `EINVAL` in both cases.
int ys_wire_write(ys_bytes_writer *writer, ys_token token);

// `ys_put` writes a whole buffer to a writer. After a short write, `ys_put` calls the callback again with the unwritten
// bytes. `ys_put` returns `YS_OK` once the whole buffer has reached the writer. `ys_put` returns `YS_FAILED_STREAM`
// where the callback fails or writes nothing. A failing callback has set `errno`. A callback that writes nothing need
// not set `errno`. The wire writer and the YAML emitter both call `ys_put`.
int ys_put(ys_bytes_writer *writer, const char *bytes, size_t size);

#endif // YEAST_WIRE_H
