// SPDX-License-Identifier: MIT
#include <errno.h>
#include <stdlib.h>
#include <yeast.h>

// A malloc/free wrapper that counts the allocations still live, so that a test — or a consumer — can confirm everything
// allocated through it was freed. Its overhead over plain malloc/free is a single counter.

struct ys_counting_allocator {
    size_t live_buffers;
};

static void *ys_counting_allocate(void *context, size_t size) {
    void *pointer = malloc(size);
    if (pointer != NULL) {
        ((ys_counting_allocator *)context)->live_buffers++;
    }
    return pointer;
}

static void ys_counting_deallocate(void *context, void *pointer) {
    if (pointer != NULL) {
        ((ys_counting_allocator *)context)->live_buffers--;
    }
    free(pointer);
}

static void *ys_counting_reallocate(void *context, void *pointer, size_t size) {
    // Handle the edge cases explicitly instead of leaving realloc's implementation-defined size==0 behavior to skew
    // the count: size 0 frees, a NULL pointer allocates, and a genuine resize keeps the count (realloc frees the old
    // block and returns the new one itself).
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
        errno = ENOMEM; // a leak: something allocated through the counter was never freed, so its memory is still held
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
