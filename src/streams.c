// SPDX-License-Identifier: MIT
// The adapters here make a `ys_bytes_reader` or a `ys_bytes_writer` out of a file descriptor or a `FILE *`. Both come
// in a reading form and a writing form. An adapter says whether it owns the handle the caller gave. An owning adapter
// closes that handle.

#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <yeast.h>

#ifdef _WIN32
#include <io.h>
// The byte calls that read and write a file descriptor. Windows names them with a leading underscore.
#define YS_OS_READ _read
#define YS_OS_WRITE _write // the write side of the same pair.
#define YS_OS_CLOSE _close // the call that releases the descriptor where the reader owns it.
#else
#include <unistd.h>
#define YS_OS_READ read
#define YS_OS_WRITE write
#define YS_OS_CLOSE close
#endif

// An adapter holds a file descriptor or a stream in its context. An adapter that owns its handle sets a close callback.
// An adapter that borrows its handle sets no callback.

// --- Reading.

// Read up to `size` bytes from the descriptor into `buffer`. The count read, `0` at the end, or `-1` with errno set.
static ptrdiff_t ys_fd_read(void *context, char *buffer, size_t size) {
    // read()/_read() report their result in a signed type (ssize_t / int). A single call can transfer at most INT_MAX
    // bytes, far more than any real read. Cap the request there so neither the count nor the return overflows.
    unsigned int capped = size > (unsigned int)INT_MAX ? (unsigned int)INT_MAX : (unsigned int)size;
    return (ptrdiff_t)YS_OS_READ((int)(intptr_t)context, buffer, capped);
}

// Close a descriptor. The readers and the writers share this close. close() already answers `0` or `-1` with errno set.
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

// A `FILE *` gets the same treatment. A short read is the end of the file. `ferror` tells that end from a failure.
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
// reach the disk at the flush a close performs. A full disk turns up here, long after `ys_write_token` has returned
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
// The call caps the count the way `ys_fd_read` caps the count it hands back. The caller continues from a short write.
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

// A `FILE *` gets the same treatment. `fwrite` writes the whole buffer or fails. A short count is then a failure
// rather than a partial write to continue from.
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
