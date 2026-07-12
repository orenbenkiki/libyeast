// SPDX-License-Identifier: MIT
#include <yeast.h>

#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#ifdef _WIN32
#include <io.h>
#define YS_OS_READ _read
#define YS_OS_CLOSE _close
#else
#include <unistd.h>
#define YS_OS_READ read
#define YS_OS_CLOSE close
#endif

// Version components injected by the build system (CMake) from the one source of truth: project(yeast VERSION ...). The
// 0.0.0 fallback lets out-of-build tooling parse this file; a binary actually built at 0.0.0 refuses to load (below).
#ifndef YS_VERSION_MAJOR
#define YS_VERSION_MAJOR 0
#endif
#ifndef YS_VERSION_MINOR
#define YS_VERSION_MINOR 0
#endif
#ifndef YS_VERSION_PATCH
#define YS_VERSION_PATCH 0
#endif

// Compose "MAJOR.MINOR.PATCH" from the components at preprocess time.
#define YS_STRINGIFY_(x) #x
#define YS_STRINGIFY(x) YS_STRINGIFY_(x)
#define YS_VERSION_STRING                                                                                              \
    YS_STRINGIFY(YS_VERSION_MAJOR) "." YS_STRINGIFY(YS_VERSION_MINOR) "." YS_STRINGIFY(YS_VERSION_PATCH)

#if defined(__GNUC__) || defined(__clang__)
// Refuse to load a library built without a real version (all components zero).
__attribute__((constructor)) static void ys_assert_version(void) {
    if (YS_VERSION_MAJOR == 0 && YS_VERSION_MINOR == 0 && YS_VERSION_PATCH == 0) {
        (void)fputs("libyeast: built without a version number; refusing to load\n", stderr); // UNTESTED
        abort();                                                                             // UNTESTED
    }
}
#endif

const char *ys_version(void) {
    return YS_VERSION_STRING;
}

int ys_major(void) {
    return YS_VERSION_MAJOR;
}

int ys_minor(void) {
    return YS_VERSION_MINOR;
}

int ys_patch(void) {
    return YS_VERSION_PATCH;
}

// --- Allocation. Each ys_allocator callback that is NULL falls back to its C counterpart. ---

static void *ys_allocate(const ys_allocator *allocator, size_t size) {
    if (allocator->allocate != NULL) {
        return allocator->allocate(allocator->context, size);
    } else {
        return malloc(size);
    }
}

static void ys_deallocate(const ys_allocator *allocator, void *pointer) {
    if (allocator->deallocate != NULL) {
        allocator->deallocate(allocator->context, pointer);
    } else {
        free(pointer);
    }
}

// --- Counting allocator: a malloc/free wrapper that counts live allocations, for leak checking. ---

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
    allocator.context = counter;
    return allocator;
}

size_t ys_counting_allocator_live_buffers(const ys_counting_allocator *counter) {
    return counter->live_buffers;
}

void ys_free_counting_allocator(ys_counting_allocator *counter) {
    free(counter);
}

// --- Parser. ---

struct ys_parser {
    ys_allocator allocator;
    size_t max_token_bytes;
    const char *input;
    size_t length;
    ys_reader reader;
    bool tokens_stable;
};

static ys_parser *ys_new_parser(const ys_options *options) {
    ys_options resolved = options != NULL ? *options : (ys_options){0};
    ys_parser *parser = ys_allocate(&resolved.allocator, sizeof(*parser));
    if (parser != NULL) {
        parser->allocator = resolved.allocator;
        parser->max_token_bytes = resolved.max_token_bytes;
        parser->input = NULL;
        parser->length = 0;
        parser->reader = (ys_reader){0};
        parser->tokens_stable = false;
    }
    return parser;
}

ys_parser *ys_new_string_parser(const char *input, size_t length, const ys_options *options) {
    ys_parser *parser = ys_new_parser(options);
    if (parser != NULL) {
        parser->input = input;
        parser->length = length;
        parser->tokens_stable = true;
    }
    return parser;
}

ys_parser *ys_new_stream_parser(ys_reader reader, const ys_options *options) {
    ys_parser *parser = ys_new_parser(options);
    if (parser != NULL) {
        parser->reader = reader;
    }
    return parser;
}

bool ys_are_tokens_stable(const ys_parser *parser) {
    return parser->tokens_stable;
}

ys_token ys_next_token(ys_parser *parser) {
    (void)parser; // no state consulted yet: parsing is not implemented, so every call yields the same error.
    ys_token token;
    token.code = YS_CODE_ERROR;
    token.start = (ys_mark){0, 0, 0, 0};
    token.end = token.start;
    token.text = "not implemented";
    return token;
}

void ys_free_parser(ys_parser *parser) {
    if (parser != NULL) {
        if (parser->reader.close != NULL) {
            parser->reader.close(parser->reader.context);
        }
        ys_deallocate(&parser->allocator, parser);
    }
}

// --- Reader adapters. The fd/FILE* is stashed in the reader context; ownership picks whether close is wired up. ---

static ptrdiff_t ys_fd_read(void *context, char *buffer, size_t size) {
    // read()/_read() report their result in a signed type (ssize_t / int), so a single call can transfer at most
    // INT_MAX bytes — far more than any real read. Cap the request there so neither the count nor the return overflows.
    unsigned int capped = size > (unsigned int)INT_MAX ? (unsigned int)INT_MAX : (unsigned int)size;
    return (ptrdiff_t)YS_OS_READ((int)(intptr_t)context, buffer, capped);
}

static void ys_fd_close(void *context) {
    YS_OS_CLOSE((int)(intptr_t)context);
}

ys_reader ys_fd_reader(int fd, ys_ownership ownership) {
    ys_reader reader;
    reader.read = ys_fd_read;
    reader.close = ownership == YS_OWN ? ys_fd_close : NULL;
    reader.context = (void *)(intptr_t)fd;
    return reader;
}

static ptrdiff_t ys_fp_read(void *context, char *buffer, size_t size) {
    FILE *file = context;
    size_t read_count = fread(buffer, 1, size, file);
    if (read_count == 0 && ferror(file)) {
        return -1; // UNTESTED
    } else {
        return (ptrdiff_t)read_count;
    }
}

static void ys_fp_close(void *context) {
    (void)fclose(context); // a read stream's close error is not actionable here
}

ys_reader ys_fp_reader(FILE *file, ys_ownership ownership) {
    ys_reader reader;
    reader.read = ys_fp_read;
    reader.close = ownership == YS_OWN ? ys_fp_close : NULL;
    reader.context = file;
    return reader;
}
