// SPDX-License-Identifier: MIT
// The buffered input a parse reads through. This header declares the buffer with its reader, the answer a fill gives,
// and the calls `source.c` implements.

#ifndef YEAST_SOURCE_H
#define YEAST_SOURCE_H

#include "memory.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <yeast.h>

// Bytes read from a `ys_bytes_reader`, and the buffer they land in. `ys_window` and `ys_wire` both hold such a
// source. `ys_source_fill` grows that buffer and fills it.
typedef struct ys_source {
    ys_bytes_reader reader; // the reader the bytes come from. `read` is NULL where the source has no reader.
    uint8_t *bytes;         // the buffer the source owns. NULL until the first fill.
    // the count of readable bytes. They sit in `bytes`, unless the source has no reader. A string parser reads the
    // caller's buffer, and the source neither owns that buffer nor copies it. `ys_new_yaml_memory_parser` writes that
    // buffer's length here.
    size_t size;
    size_t capacity; // the size `bytes` holds.
    bool is_at_end;  // the source has given out what it has.
} ys_source;

// The way a fill went.
typedef enum ys_fill {
    YS_FILL_READ,          // the source read further bytes.
    YS_FILL_AT_END,        // the source has no more bytes.
    YS_FILL_OUT_OF_MEMORY, // the buffer could not grow.
    YS_FILL_READER_FAILED  // the reader reported an error.
} ys_fill;

// Read more bytes. The `used` bytes at the front have served their purpose. This call drops them. The buffer grows only
// where the drop leaves no room. A long stream of short lines therefore leaves the buffer small.
//
// A fill leaves the `spare` bytes at the end untouched. `ys_wire` keeps a spare byte for a last line with no newline.
//
// The call drops the `used` bytes before it can fail. The caller advances past them either way.
ys_fill ys_source_fill(ys_source *source, ys_memory *memory, size_t used, size_t spare);

// Close a byte transport, where a `close` callback exists. That callback and its `context` come from a
// `ys_bytes_reader` or a `ys_bytes_writer`. Answers `0`, or `-1` with errno set, as the callback does. A transport
// with no close cannot fail.
int ys_close_transport(int (*close)(void *), void *context);

// Close a transport and discard what it says. `ys_new_sink`, `ys_new_yaml_stream_parser` and
// `ys_new_yeast_stream_reader` make this call where they are already failing. The constructor reports its own reason.
// errno still holds that reason afterwards. A failing close reports nothing.
//
// A caller hands a transport over whether or not the constructor can build the object that would use it. The
// constructor closes that transport rather than leaking it.
void ys_discard_transport(int (*close)(void *), void *context);

// Close the transport, give the `count` buffers back, and close the allocator. `ys_delete_token_source` and
// `ys_delete_token_sink` tear down this way.
//
// The teardown keeps that order. The allocator's close may release the memory the buffers go back to. The caller
// passes the object itself as the last buffer. A NULL buffer is nothing to give back.
//
// The teardown runs to the end under any failure along the way. A close that fails still leaves nothing leaked. The
// answer says which failed. `YS_FAILED_STREAM` names the transport's close, and `YS_FAILED_MEMORY` names the
// allocator's close. `YS_FAILED_BOTH` names both, and `YS_OK` names neither. errno holds the first failure's reason.
// That is the transport's reason where both failed. errno stays as it was where neither failed.
int ys_teardown(int (*close)(void *), void *close_context, ys_allocator allocator, void *const *buffers, size_t count);

#endif // YEAST_SOURCE_H
