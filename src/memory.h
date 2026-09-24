// SPDX-License-Identifier: MIT
// libyeast allocates memory through this header. The header wraps a `ys_allocator` in inline functions. The header
// defines a cap an object allocates under. The header charges array growth to that cap.

#ifndef YEAST_MEMORY_H
#define YEAST_MEMORY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <yeast.h>

// libyeast allocates through a `ys_allocator`. A callback that is NULL falls back to the C counterpart. A zeroed
// allocator is `malloc`, `realloc` and `free`. An allocator may set a callback and leave a sibling NULL. Such an
// allocator draws from the callbacks and from the C library.

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

// The size a buffer holds the first time it grows. A buffer that a read fills gets a page. An array of items gets
// `16` slots.
#define YS_MEMORY_BYTES 4096
#define YS_MEMORY_ITEMS 16 // the array's floor, in items rather than bytes.

// The allowance an object allocates under, and the amount it holds. The parser grows through here.
// This struct is the door `ys_options::max_bytes` caps. The window's buffer, the queue and the stack grow. The reader
// of the yeast wire format grows its own buffers through the same door and under the same cap.
typedef struct ys_memory {
    ys_allocator allocator;
    size_t max_bytes;       // the cap. `0` for none.
    size_t allocated_bytes; // the amount allocated. The amount counts the owning struct in.
} ys_memory;

// The options to build an object with. The options the caller gave, or the defaults where the caller gave none. A NULL
// `ys_options` means a zeroed struct, and a zeroed struct is the default throughout. An allocator of NULL callbacks
// falls back to the C functions. A `max_bytes` of `0` means no cap. `YS_RESUME_NONE` has the value `0` as well.
ys_options ys_resolved_options(const ys_options *options);

// Build a zeroed object of `size` bytes. Say in `memory` what the object may allocate from here on. The call charges
// the size of the object to the cap first. A cap too small to hold the object refuses the build. NULL if the cap or the
// allocator refuses. The caller plants `memory` inside the object it gets.
void *ys_memory_new(ys_memory *memory, const ys_options *options, size_t size);

// Charge `wanted` more bytes against the cap. `YS_OK` where the cap holds those bytes. `YS_FAILED_MEMORY` where the cap
// falls short.
int ys_memory_reserve(ys_memory *memory, size_t wanted);

// Grow an array of `item_size`-byte items to hold at least `wanted` of them. The capacity doubles. The array stays at
// `initial` or above. That is the size an array holds the first time it grows. Returns the array, or NULL where the
// cap or the allocator refuses. A refusal leaves the array unchanged.
void *ys_memory_grow(ys_memory *memory, void *items, size_t *capacity, size_t wanted, size_t initial, size_t item_size);

#endif // YEAST_MEMORY_H
