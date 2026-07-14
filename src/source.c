// SPDX-License-Identifier: MIT
#include "source.h"

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

void ys_source_free(ys_source *source, const ys_allocator *allocator) {
    ys_deallocate(allocator, source->bytes);
}
