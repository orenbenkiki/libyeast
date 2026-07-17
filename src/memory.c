// SPDX-License-Identifier: MIT
#include "memory.h"

#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>

// Give `bytes` back to the cap. Only a growth that the allocator then refused ever does, since everything else that is
// freed is freed with the ys_memory itself.
static void ys_memory_release(ys_memory *memory, size_t bytes) {
    memory->allocated_bytes -= bytes;
}

ys_options ys_resolved_options(const ys_options *options) {
    return options != NULL ? *options : (ys_options){0};
}

void *ys_memory_new(ys_memory *memory, const ys_options *options, size_t size) {
    ys_options resolved = ys_resolved_options(options);
    memory->allocator = resolved.allocator;
    memory->max_bytes = resolved.max_bytes;
    memory->allocated_bytes = 0;
    if (!ys_memory_reserve(memory, size)) {
        errno = ENOMEM; // the cap is smaller than the object; there is nowhere to build it. This one is ours to name
        return NULL;
    }

    // The allocator's failure is the allocator's to name: it must set errno, and this passes that through rather than
    // overriding it. errno is cleared first so a debug build can catch an allocator that returns NULL without setting
    // it, and a release build still yields something sensible if one does. On success errno is left as it was, since a
    // function that succeeds has no business changing it.
    int saved_errno = errno;
    errno = 0;
    void *object = ys_allocate(&memory->allocator, size);
    if (object == NULL) {
        assert(errno != 0 && "a ys_allocator that returns NULL must set errno");
        errno = errno != 0 ? errno : ENOMEM; // a broken allocator in a release build still yields something sensible
        return NULL;
    }
    errno = saved_errno;
    memset(object, 0, size);
    return object;
}

bool ys_memory_reserve(ys_memory *memory, size_t wanted) {
    if (memory->max_bytes != 0 && wanted > memory->max_bytes - memory->allocated_bytes) {
        return false;
    }
    memory->allocated_bytes += wanted;
    return true;
}

void *ys_memory_grow(ys_memory *memory, void *items, size_t *capacity, size_t wanted, size_t initial,
                     size_t item_size) {
    if (*capacity >= wanted) {
        return items;
    }
    size_t grown = *capacity <= SIZE_MAX / 2 ? *capacity * 2 : SIZE_MAX;
    if (grown < wanted) {
        grown = wanted;
    }
    if (grown < initial) {
        grown = initial;
    }
    if (grown > SIZE_MAX / item_size) {
        return NULL; // UNTESTED
    }
    size_t more = (grown - *capacity) * item_size;
    if (!ys_memory_reserve(memory, more)) {
        return NULL;
    }
    void *items_grown = ys_reallocate(&memory->allocator, items, grown * item_size);
    if (items_grown == NULL) {
        ys_memory_release(memory, more);
        return NULL;
    }
    *capacity = grown;
    return items_grown;
}
