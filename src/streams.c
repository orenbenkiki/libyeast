// SPDX-License-Identifier: MIT
// The adapters here make a `ys_bytes_reader` or a `ys_bytes_writer` out of a file descriptor or a `FILE *`. Both come
// in a reading form and a writing form. The caller's `ys_ownership` argument says whether the adapter owns the handle
// the caller gave. An owning adapter closes that handle.

#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <yeast.h>

#ifdef _WIN32
#include <io.h>
// The byte call that reads a file descriptor. Windows names it with a leading underscore.
#define YS_OS_READ _read
#define YS_OS_WRITE _write // the write side of the same pair.
#define YS_OS_CLOSE _close // the call that releases the descriptor where the adapter owns it.
#else
#include <unistd.h>
#define YS_OS_READ read
#define YS_OS_WRITE write
#define YS_OS_CLOSE close
#endif

// An adapter holds a file descriptor or a stream in its context. An adapter that owns its handle sets a close callback.
// An adapter that borrows its handle sets no close callback.

// --- Reading.

// Read up to `size` bytes from the descriptor into `buffer`. The answer is the count read, `0` at the end, or `-1` with
// errno set. https://pubs.opengroup.org/onlinepubs/9799919799/functions/read.html gives that contract for read().
// https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/read gives it for _read().
static ptrdiff_t ys_fd_read(void *context, char *buffer, size_t size) {
    // read()/_read() report their result in a signed type (ssize_t / int). A single call can transfer at most INT_MAX
    // bytes, far more than any real read. Cap the request there so neither the count nor the return overflows.
    unsigned int capped = size > (unsigned int)INT_MAX ? (unsigned int)INT_MAX : (unsigned int)size;
    return (ptrdiff_t)YS_OS_READ((int)(intptr_t)context, buffer, capped);
}

// Close a descriptor. The readers and the writers share this close. close() already answers `0` or `-1` with errno set.
// https://pubs.opengroup.org/onlinepubs/9799919799/functions/close.html gives that contract for close().
// https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/close gives it for _close().
// A `ys_bytes_reader`'s close and a `ys_bytes_writer`'s close must answer the same way. This translates nothing.
static int ys_fd_close(void *context) {
    return YS_OS_CLOSE((int)(intptr_t)context);
}

ys_bytes_reader ys_fd_reader(int fd, ys_ownership ownership) {
    ys_bytes_reader reader;
    reader.read = ys_fd_read;
    reader.close = ownership == YS_OWN ? ys_fd_close : NULL;
    reader.context = (void *)(intptr_t)fd;
    return reader;
}

// A `FILE *` gets the treatment `ys_fd_read` gives a descriptor. A read of zero bytes is the end of the file. `ferror`
// tells that end from a failure.
static ptrdiff_t ys_fp_read(void *context, char *buffer, size_t size) {
    FILE *file = context;
    size_t read_count = fread(buffer, 1, size, file);
    if (read_count == 0 && ferror(file)) {
        return -1; // UNTESTED
    } else {
        return (ptrdiff_t)read_count;
    }
}

// Close a FILE *. The readers and the writers share this close. fclose answers `0` or EOF rather
// than `0` or `-1`, and EOF may be any negative value. A writer sees that difference. fwrite buffers, and the bytes
// reach the disk at the flush a close performs. https://en.cppreference.com/w/c/io/fclose gives that flush and those
// answers. A full disk turns up here, long after `ys_write_token` has returned
// `YS_OK`.
static int ys_fp_close(void *context) {
    return fclose(context) == 0 ? 0 : -1;
}

ys_bytes_reader ys_fp_reader(FILE *file, ys_ownership ownership) {
    ys_bytes_reader reader;
    reader.read = ys_fp_read;
    reader.close = ownership == YS_OWN ? ys_fp_close : NULL;
    reader.context = file;
    return reader;
}

// --- Writing.

// Write up to `size` bytes of `buffer` to the descriptor. The call returns the count written, or `-1` with errno set.
// https://pubs.opengroup.org/onlinepubs/9799919799/functions/write.html gives that contract for write().
// https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/write gives it for _write().
// The call caps the requested size at `INT_MAX` the way `ys_fd_read` caps its request. A caller needs to write the rest
// of a partially written buffer.
static ptrdiff_t ys_fd_write(void *context, const char *buffer, size_t size) {
    unsigned int capped = size > (unsigned int)INT_MAX ? (unsigned int)INT_MAX : (unsigned int)size;
    return (ptrdiff_t)YS_OS_WRITE((int)(intptr_t)context, buffer, capped);
}

ys_bytes_writer ys_fd_writer(int fd, ys_ownership ownership) {
    ys_bytes_writer writer;
    writer.write = ys_fd_write;
    writer.close = ownership == YS_OWN ? ys_fd_close : NULL;
    writer.context = (void *)(intptr_t)fd;
    return writer;
}

// A `FILE *` writer returns the count written or `-1`, as `ys_fd_write` does. `fwrite` writes the whole buffer or
// fails, as https://en.cppreference.com/w/c/io/fwrite says. A short count is then a failure rather than a partial write
// to continue from.
static ptrdiff_t ys_fp_write(void *context, const char *buffer, size_t size) {
    size_t written = fwrite(buffer, 1, size, context);
    if (written < size) {
        return -1; // UNTESTED
    }
    return (ptrdiff_t)written;
}

ys_bytes_writer ys_fp_writer(FILE *file, ys_ownership ownership) {
    ys_bytes_writer writer;
    writer.write = ys_fp_write;
    writer.close = ownership == YS_OWN ? ys_fp_close : NULL;
    writer.context = file;
    return writer;
}
