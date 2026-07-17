// SPDX-License-Identifier: MIT
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <yeast.h>

#ifdef _WIN32
#include <io.h>
#define YS_OS_READ _read
#define YS_OS_WRITE _write
#define YS_OS_CLOSE _close
#else
#include <unistd.h>
#define YS_OS_READ read
#define YS_OS_WRITE write
#define YS_OS_CLOSE close
#endif

// The adapters that make a file descriptor or a FILE * into a ys_reader or a ys_writer. The descriptor or the stream is
// stashed in the context, and the ownership picks whether the close callback is wired up at all.

// --- Reading. ---

static ptrdiff_t ys_fd_read(void *context, char *buffer, size_t size) {
    // read()/_read() report their result in a signed type (ssize_t / int), so a single call can transfer at most
    // INT_MAX bytes — far more than any real read. Cap the request there so neither the count nor the return overflows.
    unsigned int capped = size > (unsigned int)INT_MAX ? (unsigned int)INT_MAX : (unsigned int)size;
    return (ptrdiff_t)YS_OS_READ((int)(intptr_t)context, buffer, capped);
}

// Closing a descriptor is the same act whether it was read from or written to, so the readers and the writers share it,
// and sharing it is why they share a file. close() already answers 0 or -1 with errno set, which is what a ys_reader's
// and a ys_writer's close must answer, so there is nothing to translate.
static int ys_fd_close(void *context) {
    return YS_OS_CLOSE((int)(intptr_t)context);
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

// And closing a FILE * likewise, so the readers and the writers share that too. fclose answers 0 or EOF rather than 0
// or -1, and EOF is only some negative value, so the two are not the same answer and this says so. It matters most for
// a writer: fwrite buffers, so the bytes reach the disk at the flush a close performs, and a full disk is discovered
// here — long after every ys_write_token() has returned true.
static int ys_fp_close(void *context) {
    return fclose(context) == 0 ? 0 : -1;
}

ys_reader ys_fp_reader(FILE *file, ys_ownership ownership) {
    ys_reader reader;
    reader.read = ys_fp_read;
    reader.close = ownership == YS_OWN ? ys_fp_close : NULL;
    reader.context = file;
    return reader;
}

// --- Writing. ---

static ptrdiff_t ys_fd_write(void *context, const char *buffer, size_t size) {
    unsigned int capped = size > (unsigned int)INT_MAX ? (unsigned int)INT_MAX : (unsigned int)size;
    return (ptrdiff_t)YS_OS_WRITE((int)(intptr_t)context, buffer, capped);
}

ys_writer ys_fd_writer(int fd, ys_ownership ownership) {
    ys_writer writer;
    writer.write = ys_fd_write;
    writer.close = ownership == YS_OWN ? ys_fd_close : NULL;
    writer.context = (void *)(intptr_t)fd;
    return writer;
}

static ptrdiff_t ys_fp_write(void *context, const char *buffer, size_t size) {
    size_t written = fwrite(buffer, 1, size, context);
    if (written < size) {
        return -1; // UNTESTED
    }
    return (ptrdiff_t)written;
}

ys_writer ys_fp_writer(FILE *file, ys_ownership ownership) {
    ys_writer writer;
    writer.write = ys_fp_write;
    writer.close = ownership == YS_OWN ? ys_fp_close : NULL;
    writer.context = file;
    return writer;
}

int ys_close_writer(ys_writer *writer) {
    // A writer that borrows what it writes to has nothing to close, and cannot fail here.
    return writer->close != NULL ? writer->close(writer->context) : 0;
}
