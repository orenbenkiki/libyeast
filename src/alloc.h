// SPDX-License-Identifier: MIT
#ifndef YEAST_ALLOC_H
#define YEAST_ALLOC_H

#include <stdlib.h>
#include <yeast.h>

// Allocation through a ys_allocator. Each of its callbacks that is NULL falls back to its C counterpart on its own, so
// a zeroed allocator is malloc/realloc/free, and setting only some of the callbacks mixes the two.

static inline void *ys_allocate(const ys_allocator *allocator, size_t size) {
    if (allocator->allocate != NULL) {
        return allocator->allocate(allocator->context, size);
    } else {
        return malloc(size);
    }
}

static inline void *ys_reallocate(const ys_allocator *allocator, void *pointer, size_t size) {
    if (allocator->reallocate != NULL) {
        return allocator->reallocate(allocator->context, pointer, size);
    } else {
        return realloc(pointer, size);
    }
}

static inline void ys_deallocate(const ys_allocator *allocator, void *pointer) {
    if (allocator->deallocate != NULL) {
        allocator->deallocate(allocator->context, pointer);
    } else {
        free(pointer);
    }
}

#endif // YEAST_ALLOC_H
