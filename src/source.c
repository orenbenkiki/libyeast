// SPDX-License-Identifier: MIT
#include "source.h"

#include <errno.h>
#include <string.h>

ys_fill ys_source_fill(ys_source *source, ys_memory *memory, size_t used, size_t spare) {
    if (used > 0) {
        memmove(source->bytes, source->bytes + used, source->size - used);
        source->size -= used;
    }
    if (source->size + spare >= source->capacity) {
        uint8_t *grown = ys_memory_grow(memory, source->bytes, &source->capacity, source->size + spare + 1,
                                        YS_MEMORY_BYTES, sizeof(uint8_t));
        if (grown == NULL) {
            return YS_FILL_OUT_OF_MEMORY;
        }
        source->bytes = grown;
    }

    ptrdiff_t read_count = source->reader.read(source->reader.context, (char *)source->bytes + source->size,
                                               source->capacity - source->size - spare);
    if (read_count < 0) {
        return YS_FILL_READER_FAILED;
    }
    if (read_count == 0) {
        source->is_at_end = true;
        return YS_FILL_AT_END;
    }
    source->size += (size_t)read_count;
    return YS_FILL_READ;
}

int ys_close_transport(int (*close)(void *), void *context) {
    return close != NULL ? close(context) : 0;
}

void ys_discard_transport(int (*close)(void *), void *context) {
    int saved_errno = errno;
    (void)ys_close_transport(close, context);
    errno = saved_errno;
}

int ys_teardown(int (*close)(void *), void *close_context, ys_allocator allocator, void *const *buffers, size_t count) {
    int saved_errno = errno;
    int result = YS_OK;
    if (ys_close_transport(close, close_context) != 0) {
        result = YS_FAILED_STREAM;
        saved_errno = errno; // the reader's, kept whatever the allocator's close does next
    }

    // A deallocation cannot fail, but may set errno all the same, so the reason for the failure is carried across them.
    for (size_t index = 0; index < count; index++) {
        ys_deallocate(&allocator, buffers[index]);
    }

    if (ys_close_allocator(&allocator) != 0) {
        if (result == YS_OK) {
            result = YS_FAILED_MEMORY;
            saved_errno = errno; // only the allocator failed, so its reason is the one to keep
        } else {
            result = YS_FAILED_BOTH; // both failed; errno stays the reader's, the first
        }
    }
    errno = saved_errno;
    return result;
}
