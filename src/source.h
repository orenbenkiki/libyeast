// SPDX-License-Identifier: MIT
#ifndef YEAST_SOURCE_H
#define YEAST_SOURCE_H

#include "memory.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <yeast.h>

// Bytes read from a ys_bytes_reader, and the buffer they land in. The parser reads its input through one of these, and
// so does the reader of the yeast wire format. They had a buffer each, compacted and grown the same way — and one of
// them had a check the other did not, which is why they now have it once.
typedef struct ys_source {
    ys_bytes_reader reader; // where the bytes come from; its `read` is NULL when no more are coming
    uint8_t *bytes;         // the buffer, which is the source's own; NULL until the first fill
    size_t size;     // the readable bytes there are. They are in `bytes`, unless there is no reader — a string parser
                     // reads the caller's buffer, which the source neither owns nor copies, and this is its length
    size_t capacity; // how many bytes `bytes` holds
    bool is_at_end;  // the source has given everything it has
} ys_source;

// How a fill went.
typedef enum ys_fill {
    YS_FILL_READ,          // there are more bytes than there were
    YS_FILL_AT_END,        // there are no more to be had
    YS_FILL_OUT_OF_MEMORY, // the cap or the allocator refused the room for them
    YS_FILL_READER_FAILED  // the reader reported an error
} ys_fill;

// Read more bytes. The `used` bytes at the front are wanted no longer and are discarded, and the buffer grows only when
// that leaves no room — which is what keeps a long stream of short lines from growing a buffer the size of the stream.
// The `spare` bytes at the end are never read into: the wire's reader keeps one, to terminate a last line that carries
// no newline of its own, and the parser keeps none.
//
// The `used` bytes are discarded whatever the outcome, so the caller advances past them either way.
ys_fill ys_source_fill(ys_source *source, ys_memory *memory, size_t used, size_t spare);

// Close a byte transport — a `ys_bytes_reader`'s or a `ys_bytes_writer`'s `close` callback and its `context` — if it
// has one: 0, or -1 with errno set, which is what the callback answers. A transport with no close cannot fail.
int ys_close_transport(int (*close)(void *), void *context);

// Close a transport and discard whatever it says, preserving errno. For a constructor that is already failing: it has a
// reason of its own to report — the EINVAL for a transport with no callback, the allocator's errno — and errno still
// holds it afterwards, since a close that fails sets its own. A transport is handed over whether or not the object that
// would use it can be built, so the constructor closes it rather than leaking it, and has nowhere to report a failure
// to anyway: it is already returning NULL for a different reason, and that reason is the more useful one.
void ys_discard_transport(int (*close)(void *), void *context);

// Close the transport, give the `count` buffers back, and close the allocator: the teardown of anything built on a byte
// transport and a ys_memory — a token source over a reader, a token sink over a writer. The order is the only one that
// works — the allocator closes last, since what it releases may be the very memory being given back to it, and the
// object itself is the last buffer, being what the others hang off. A NULL buffer is nothing to give back.
//
// The whole of it runs whatever fails in it, so a close that fails still leaves nothing leaked. What answers is which
// failed: `YS_OK`, `YS_FAILED_STREAM` for the transport's close, `YS_FAILED_MEMORY` for the allocator's,
// `YS_FAILED_BOTH` for both. errno is the first failure's reason — the transport's, where both failed — and is left as
// it was found where none failed.
int ys_teardown(int (*close)(void *), void *close_context, ys_allocator allocator, void *const *buffers, size_t count);

#endif // YEAST_SOURCE_H
