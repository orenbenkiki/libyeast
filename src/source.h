// SPDX-License-Identifier: MIT
#ifndef YEAST_SOURCE_H
#define YEAST_SOURCE_H

#include "memory.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <yeast.h>

// Bytes read from a ys_reader, and the buffer they land in. The parser reads its input through one of these, and so
// does the reader of the yeast wire format. They had a buffer each, compacted and grown the same way — and one of them
// had a check the other did not, which is why they now have it once.
typedef struct ys_source {
    ys_reader reader; // where the bytes come from; its `read` is NULL when no more are coming
    uint8_t *bytes;   // the buffer, which is the source's own; NULL until the first fill
    size_t size;      // the readable bytes there are. They are in `bytes`, unless there is no reader — a string parser
                      // reads the caller's buffer, which the source neither owns nor copies, and this is its length
    size_t capacity;  // how many bytes `bytes` holds
    bool is_at_end;   // the source has given everything it has
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

// Free the buffer, if the source ever had one.
void ys_source_free(ys_source *source, const ys_allocator *allocator);

#endif // YEAST_SOURCE_H
