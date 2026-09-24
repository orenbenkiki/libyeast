// SPDX-License-Identifier: MIT
// A wrapper around `malloc` and `free` that counts the allocations still live. A test or a consumer can confirm that
// the code freed what it allocated. The wrapper adds a single counter to the cost of plain `malloc` and `free`.

#include <errno.h>
#include <stdlib.h>
#include <yeast.h>

struct ys_counting_allocator {
    size_t live_buffers;
};

// Allocate `size` bytes and count the buffer as live. NULL where malloc refused. The count does not move.
static void *ys_counting_allocate(void *context, size_t size) {
    void *pointer = malloc(size);
    if (pointer != NULL) {
        ((ys_counting_allocator *)context)->live_buffers++;
    }
    return pointer;
}

// Free `pointer` and take the buffer off the count. A NULL pointer frees nothing and counts as nothing.
static void ys_counting_deallocate(void *context, void *pointer) {
    if (pointer != NULL) {
        ((ys_counting_allocator *)context)->live_buffers--;
    }
    free(pointer);
}

// Resize `pointer` to `size` bytes. The count stays right when `pointer` is NULL, when `size` asks for no bytes, or
// when the realloc call fails.
static void *ys_counting_reallocate(void *context, void *pointer, size_t size) {
    // Handle the edge cases explicitly instead of leaving realloc's implementation-defined size==0 behavior to skew
    // the count. A size of `0` frees. A NULL pointer allocates. A genuine resize keeps the count. Realloc frees the old
    // block and returns the new block itself.
    if (size == 0) {
        ys_counting_deallocate(context, pointer);
        return NULL;
    } else if (pointer == NULL) {
        return ys_counting_allocate(context, size);
    } else {
        return realloc(pointer, size);
    }
}

ys_counting_allocator *ys_new_counting_allocator(void) {
    ys_counting_allocator *counter = malloc(sizeof(*counter));
    if (counter != NULL) {
        counter->live_buffers = 0;
    }
    return counter;
}

ys_allocator ys_counting_allocator_functions(ys_counting_allocator *counter) {
    ys_allocator allocator;
    allocator.allocate = ys_counting_allocate;
    allocator.reallocate = ys_counting_reallocate;
    allocator.deallocate = ys_counting_deallocate;
    allocator.close = ys_close_counting_allocator;
    allocator.context = counter;
    return allocator;
}

int ys_close_counting_allocator(void *counter) {
    if (((const ys_counting_allocator *)counter)->live_buffers > 0) {
        errno = ENOMEM; // a leak. Something allocated through the counter was not freed, and its memory is still held
        return -1;
    }
    return 0;
}

size_t ys_counting_allocator_live_buffers(const ys_counting_allocator *counter) {
    return counter->live_buffers;
}

void ys_delete_counting_allocator(ys_counting_allocator *counter) {
    free(counter);
}
