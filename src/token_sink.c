// SPDX-License-Identifier: MIT
#include "token_sink.h"

#include "memory.h"
#include "source.h"
#include "wire.h"
#include <errno.h>
#include <yeast.h>

// A sink of yeast tokens, whichever way they are consumed: serialized to a yeast wire, or — later — emitted as YAML.
// The mirror of a ys_token_source: ys_write_token() feeds it, ys_delete_token_sink() releases it, and code writing
// tokens does not know or care where they go.

ys_token_sink *ys_new_yeast_stream_writer(ys_writer writer, const ys_options *options) {
    if (writer.write == NULL) {
        errno = EINVAL; // a writer with nothing to write to
        ys_discard_transport(writer.close, writer.context);
        return NULL;
    }
    ys_memory memory;
    ys_token_sink *sink = ys_memory_new(&memory, options, sizeof(*sink)); // sets errno on failure
    if (sink == NULL) {
        ys_discard_transport(writer.close, writer.context);
        return NULL;
    }
    sink->kind = YS_SINK_WIRE;
    sink->as.wire.memory = memory;
    sink->as.wire.writer = writer;
    return sink;
}

bool ys_write_token(ys_token_sink *sink, ys_token token) {
    return ys_wire_write(&sink->as.wire.writer, token);
}

int ys_delete_token_sink(ys_token_sink *sink) {
    if (sink == NULL) {
        return 0; // deleting nothing cannot fail
    }
    // The wire writer's only allocation is the sink itself; the byte transport is closed, flushing what it buffered.
    void *buffers[] = {sink};
    return ys_teardown(sink->as.wire.writer.close, sink->as.wire.writer.context, sink->as.wire.memory.allocator,
                       buffers, sizeof(buffers) / sizeof(buffers[0]));
}
