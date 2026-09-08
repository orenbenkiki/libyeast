// SPDX-License-Identifier: MIT
// The layout of a `ys_token_sink`. `yeast.h` declares that type to a caller as incomplete.

#ifndef YEAST_TOKEN_SINK_H
#define YEAST_TOKEN_SINK_H

#include <yeast.h>

// The arm a ys_token_sink is. The kind says how ys_write_token() renders a token. Both arms hold a byte transport and
// nothing besides. The arms differ in what they write for a token. The sink is a tagged struct rather than a union.
// The wire writer writes a token in the wire format. The YAML emitter writes the bytes it spans.
typedef enum ys_sink_kind {
    YS_SINK_WIRE,   // made by ys_new_yeast_stream_writer.
    YS_SINK_EMITTER // made by ys_new_yaml_stream_emitter.
} ys_sink_kind;

// A sink of yeast tokens. Writing a token needs no per-token state. The wire writer works from a stack buffer, and the
// emitter copies the bytes of the token. The sink grows nothing. The sink keeps the allocator to free itself, and
// holds no ys_memory to track a cap against. The sink lives on the heap. A caller holds it opaquely.
struct ys_token_sink {
    ys_sink_kind kind;
    ys_allocator allocator; // the allocator that made the sink. The sink keeps it to unmake itself.
    ys_bytes_writer writer; // the writer the tokens go to. The kind says that.
};

#endif // YEAST_TOKEN_SINK_H
