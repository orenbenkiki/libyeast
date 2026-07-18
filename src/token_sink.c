// SPDX-License-Identifier: MIT
#include "token_sink.h"

#include "memory.h"
#include "source.h"
#include "wire.h"
#include <errno.h>
#include <yeast.h>

// A sink of yeast tokens, whichever way they are consumed: serialized to a yeast wire, or emitted as YAML. The mirror
// of a ys_token_source: ys_write_token() feeds it, ys_delete_token_sink() releases it, and code writing tokens does not
// know or care where they go.

// Allocate a sink over `writer`, or NULL with `errno` set — in which case the writer it was handed is closed. On
// success `*memory` is what allocated it, for the caller to keep the allocator from.
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
    // The emitter: a token stream is byte-complete, so emitting it is writing the bytes each token spans. A marker
    // spans none, and an error spans none either but is no token to render — its text is a message, not input — so it
    // is refused rather than skipped: a stream to emit must be filtered of errors above the emitter.
    if (token.code == YS_CODE_ERROR) {
        errno = EINVAL;
        return YS_FAILED_ACTION;
    }
    size_t span = token.end.byte_offset - token.start.byte_offset;
    if (span == 0) {
        return YS_OK; // a zero-width marker: nothing to write
    }
    return ys_put(&sink->writer, token.text, span) ? YS_OK : YS_FAILED_STREAM;
}

int ys_delete_token_sink(ys_token_sink *sink) {
    if (sink == NULL) {
        return 0; // deleting nothing cannot fail
    }
    // The sink's only allocation is itself; the byte transport is closed, flushing what it buffered.
    void *buffers[] = {sink};
    return ys_teardown(sink->writer.close, sink->writer.context, sink->allocator, buffers,
                       sizeof(buffers) / sizeof(buffers[0]));
}
