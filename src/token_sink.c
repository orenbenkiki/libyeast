// SPDX-License-Identifier: MIT
// A sink takes yeast tokens. A sink serializes the tokens to a yeast wire, or emits YAML. A sink mirrors a
// `ys_token_source`. `ys_write_token` feeds a sink and `ys_delete_token_sink` releases a sink. Code
// writing tokens does not know or care where they go.

#include "token_sink.h"

#include "memory.h"
#include "source.h"
#include "wire.h"
#include <errno.h>
#include <yeast.h>

// Allocate a sink over `writer`, or answer NULL with `errno` set. A failure closes the writer. On success `*memory`
// holds the allocator the sink came from, for the caller to keep.
static ys_token_sink *ys_new_sink(ys_sink_kind kind, ys_bytes_writer writer, const ys_options *options,
                                  ys_memory *memory) {
    if (writer.write == NULL) {
        errno = EINVAL; // a writer with nothing to write to
        ys_discard_transport(writer.close, writer.context);
        return NULL;
    }
    ys_token_sink *sink = ys_memory_new(memory, options, sizeof(*sink)); // sets errno on failure
    if (sink == NULL) {
        ys_discard_transport(writer.close, writer.context);
        return NULL;
    }
    sink->kind = kind;
    sink->allocator = memory->allocator;
    sink->writer = writer;
    return sink;
}

ys_token_sink *ys_new_yeast_stream_writer(ys_bytes_writer writer, const ys_options *options) {
    ys_memory memory;
    return ys_new_sink(YS_SINK_WIRE, writer, options, &memory);
}

ys_token_sink *ys_new_yaml_stream_emitter(ys_bytes_writer writer, const ys_options *options) {
    ys_memory memory;
    return ys_new_sink(YS_SINK_EMITTER, writer, options, &memory);
}

int ys_write_token(ys_token_sink *sink, ys_token token) {
    if (sink->kind == YS_SINK_WIRE) {
        return ys_wire_write(&sink->writer, token);
    }
    // The emitter. A token stream is byte-complete, and emitting it is writing the bytes a token spans. A marker
    // spans none. An error spans none either, but is no token to render. Its text is a message, not input. An error is
    // refused rather than skipped. A stream to emit must be filtered of errors above the emitter.
    if (token.code == YS_CODE_ERROR) {
        errno = EINVAL;
        return YS_FAILED_ACTION;
    }
    size_t span = token.end.byte_offset - token.start.byte_offset;
    if (span == 0) {
        return YS_OK; // a zero-width marker, with nothing to write
    }
    return ys_put(&sink->writer, token.text, span);
}

int ys_delete_token_sink(ys_token_sink *sink) {
    if (sink == NULL) {
        return YS_OK; // deleting nothing cannot fail
    }
    // The sink allocates itself and no more. The byte transport is closed, flushing what it buffered.
    void *buffers[] = {sink};
    return ys_teardown(sink->writer.close, sink->writer.context, sink->allocator, buffers,
                       sizeof(buffers) / sizeof(buffers[0]));
}
