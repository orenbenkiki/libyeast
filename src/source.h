// SPDX-License-Identifier: MIT
// The buffered input a parse reads through. The buffer with its reader, what a fill answers, and what `source.c`
// implements over the buffer.

#ifndef YEAST_SOURCE_H
#define YEAST_SOURCE_H

#include "memory.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <yeast.h>

// Bytes read from a ys_bytes_reader, and the buffer they land in. The parser reads its input through such a source,
// and so does the reader of the yeast wire format. Both once had a buffer apiece, compacted and grown the same
// way. The parser's copy had a check the wire's copy lacked. This source holds that check, and holds it once.
typedef struct ys_source {
    ys_bytes_reader reader; // the reader the bytes come from. Its `read` is NULL once no more are coming.
    uint8_t *bytes;         // the buffer the source owns. NULL until the first fill.
    // the count of readable bytes. They sit in `bytes`, unless the source has no reader. A string parser reads the
    // caller's buffer, and the source neither owns that buffer nor copies it. This length covers that case too.
    size_t size;
    size_t capacity; // the size `bytes` holds.
    bool is_at_end;  // the source has given out what it has.
} ys_source;

// The way a fill went.
typedef enum ys_fill {
    YS_FILL_READ,          // the source read further bytes.
    YS_FILL_AT_END,        // the source has no more to be had.
    YS_FILL_OUT_OF_MEMORY, // the cap or the allocator refused the room for them.
    YS_FILL_READER_FAILED  // the reader reported an error.
} ys_fill;

// Read more bytes. The `used` bytes at the front have served their purpose, and this drops those bytes. The buffer
// grows only when that leaves no room. That keeps a long stream of short lines from growing a buffer the size of the
// stream.
//
// A fill leaves the `spare` bytes at the end untouched. The wire's reader keeps a spare byte, to terminate a last line
// that has no newline of its own, and the parser keeps none.
//
// This drops the `used` bytes whatever the outcome. The caller advances past them either way.
ys_fill ys_source_fill(ys_source *source, ys_memory *memory, size_t used, size_t spare);

// Close a byte transport, where a `close` callback exists. That callback and its `context` come from a
// `ys_bytes_reader` or a `ys_bytes_writer`. Answers `0`, or `-1` with errno set, as the callback does. A transport
// with no close cannot fail.
int ys_close_transport(int (*close)(void *), void *context);

// Close a transport and discard whatever it says. errno survives that. For a constructor that is already failing. The
// constructor has a reason of its own to report, the EINVAL for a transport with no callback or the allocator's errno.
// errno still holds that afterwards. A close that fails sets its own reason.
//
// A caller hands a transport over whether or not the constructor can build the object that would use it. The
// constructor closes that transport rather than leaking it. The constructor has no place to report a failure to
// anyway. It is already returning NULL for a different reason, and that reason serves the caller better.
void ys_discard_transport(int (*close)(void *), void *context);

// Close the transport, give the `count` buffers back, and close the allocator. This is the teardown of anything built
// on a byte transport and a ys_memory. A token source over a reader, or a token sink over a writer.
//
// The order is forced. The allocator closes last. The memory the allocator releases may be the memory going back to
// it. The object itself is the last buffer, and the earlier buffers hang off it. A NULL buffer is nothing to give back.
//
// The teardown runs to the end whatever fails along the way. A close that fails still leaves nothing leaked. The
// answer says which failed. `YS_OK`. `YS_FAILED_STREAM` for the transport's close. `YS_FAILED_MEMORY` for the
// allocator's. `YS_FAILED_BOTH` for both. errno holds the first failure's reason. That is the transport's reason
// where both failed. errno stays as it was where neither failed.
int ys_teardown(int (*close)(void *), void *close_context, ys_allocator allocator, void *const *buffers, size_t count);

#endif // YEAST_SOURCE_H
