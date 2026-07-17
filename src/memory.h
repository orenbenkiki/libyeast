// SPDX-License-Identifier: MIT
#ifndef YEAST_MEMORY_H
#define YEAST_MEMORY_H

#include <stdbool.h>
#include <stddef.h>
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

// Close the allocator, if it has anything to release, and say whether that failed: 0, or -1 with errno set. Called once
// everything allocated through it has been given back, since that memory may be what it releases.
static inline int ys_close_allocator(const ys_allocator *allocator) {
    return allocator->close != NULL ? allocator->close(allocator->context) : 0;
}

// What a buffer holds the first time it grows. A buffer that a read fills gets a page; an array of items gets a few of
// them, most documents never reaching past that.
#define YS_MEMORY_BYTES 4096
#define YS_MEMORY_ITEMS 16

// What something may allocate, and what it has. Everything the parser grows goes through here, so that
// ys_options::max_bytes has exactly one door: the window's buffer, the queue and the stack are the three things that
// grow, and there is no fourth. The reader of the yeast wire format grows its own two buffers through the same door,
// under the same cap, for the same reason.
typedef struct ys_memory {
    ys_allocator allocator;
    size_t max_bytes;       // the cap; 0 for none
    size_t allocated_bytes; // what has been allocated, the owning struct included
} ys_memory;

// The options to build an object with: the caller's, or the defaults where it gave none. A NULL ys_options means a
// zeroed one, which every default is: an allocator of NULL callbacks falls back to C's, a `max_bytes` of 0 is no cap,
// and YS_RESUME_NONE is 0. So what NULL means is decided here, and nowhere else.
ys_options ys_resolved_options(const ys_options *options);

// Build an object of `size` bytes, zeroed, and say in `memory` what it may allocate from here on. Its own size is
// charged to the cap first, so a cap it could not even live under refuses it here rather than leaving it to fail at its
// first allocation. NULL if the cap or the allocator refuses. The caller plants `memory` inside the object it gets.
void *ys_memory_new(ys_memory *memory, const ys_options *options, size_t size);

// Whether `wanted` more bytes are within the cap, charging them if they are.
bool ys_memory_reserve(ys_memory *memory, size_t wanted);

// Grow an array of `item_size`-byte items to hold at least `wanted` of them, doubling its capacity — and never leaving
// it below `initial`, which is what it holds the first time it grows. Returns the array, or NULL if the cap or the
// allocator refuses, in which case it is left as it was.
void *ys_memory_grow(ys_memory *memory, void *items, size_t *capacity, size_t wanted, size_t initial, size_t item_size);

#endif // YEAST_MEMORY_H
