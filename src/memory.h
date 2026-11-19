// SPDX-License-Identifier: MIT
// libyeast allocates memory through this header. The header wraps a `ys_allocator` in inline functions. The header
// defines a cap an object allocates under. `ys_memory_grow` charges array growth to that cap.

#ifndef YEAST_MEMORY_H
#define YEAST_MEMORY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <yeast.h>

// libyeast allocates through a `ys_allocator`. A NULL `allocate`, `reallocate` or `deallocate` falls back to the C
// counterpart. A NULL `close` releases nothing and returns `0`. A zeroed allocator is `malloc`, `realloc` and `free`.
// An allocator may set a callback and leave a sibling NULL. Such an allocator draws from the callbacks and from the C
// library.

// Allocate `size` bytes through `allocator`, or through malloc where it names no allocate callback.
static inline void *ys_allocate(const ys_allocator *allocator, size_t size) {
    if (allocator->allocate != NULL) {
        return allocator->allocate(allocator->context, size);
    } else {
        return malloc(size);
    }
}

// Resize `pointer` to `size` bytes through `allocator`, or through realloc where it names no reallocate callback.
static inline void *ys_reallocate(const ys_allocator *allocator, void *pointer, size_t size) {
    if (allocator->reallocate != NULL) {
        return allocator->reallocate(allocator->context, pointer, size);
    } else {
        return realloc(pointer, size);
    }
}

// Free `pointer` through `allocator`, or through free where it names no deallocate callback.
static inline void ys_deallocate(const ys_allocator *allocator, void *pointer) {
    if (allocator->deallocate != NULL) {
        allocator->deallocate(allocator->context, pointer);
    } else {
        free(pointer);
    }
}

// Close the allocator where it holds anything to release. Say whether that failed. `0`, or `-1` with errno set.
//
// The caller calls this once it has given back the memory the allocator handed out. That memory may be what the
// allocator releases.
static inline int ys_close_allocator(const ys_allocator *allocator) {
    return allocator->close != NULL ? allocator->close(allocator->context) : 0;
}

// The least a buffer holds the first time it grows. A buffer that a read fills gets a page. An array of items gets
// `YS_MEMORY_ITEMS` slots.
#define YS_MEMORY_BYTES 4096
#define YS_MEMORY_ITEMS 16 // the array's floor, in items rather than bytes.

// The allowance an object allocates under, and the amount it holds. The parser grows through here.
// `ys_options::max_bytes` sets the allowance. `ys_memory_reserve` enforces it.
typedef struct ys_memory {
    ys_allocator allocator;
    size_t max_bytes;       // the cap. `0` means no cap.
    size_t allocated_bytes; // the amount allocated.
} ys_memory;

// The options to build an object with. The options the caller gave, or the defaults where the caller gave none. A NULL
// `ys_options` means a zeroed struct, and a zeroed struct is the default throughout. A `ys_allocator` of NULL callbacks
// falls back to the C functions. A `max_bytes` of `0` means no cap. `YS_RESUME_NONE` of `ys_resume` has the value `0`
// as well.
ys_options ys_resolved_options(const ys_options *options);

// Build a zeroed object of `size` bytes. Say in `memory` what the object may allocate from here on. The call charges
// the size of the object to the cap first. A cap too small to hold the object refuses the build. NULL if the cap or the
// allocator refuses. A caller hands `memory` on to the code that allocates for the object, as `ys_new_source` does.
void *ys_memory_new(ys_memory *memory, const ys_options *options, size_t size);

// Charge `wanted` more bytes against the cap. `YS_OK` where `max_bytes` sets no cap or the cap holds those bytes.
// `YS_FAILED_MEMORY` where the cap falls short.
int ys_memory_reserve(ys_memory *memory, size_t wanted);

// Grow an array of `item_size`-byte items to hold at least `wanted` of them. A capacity below `wanted` doubles. The
// doubled capacity then rises to `wanted` and to `initial` where either is higher. Returns the array. Returns NULL
// where the byte count overflows `size_t`, and where the cap or the allocator refuses. An overflow or a cap refusal
// leaves the array unchanged. So does a refusal of the default allocator. Its `realloc` keeps the old block, as
// https://en.cppreference.com/w/c/memory/realloc says. A custom `reallocate` decides what its refusal leaves.
void *ys_memory_grow(ys_memory *memory, void *items, size_t *capacity, size_t wanted, size_t initial, size_t item_size);

#endif // YEAST_MEMORY_H
