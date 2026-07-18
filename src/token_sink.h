// SPDX-License-Identifier: MIT
#ifndef YEAST_TOKEN_SINK_H
#define YEAST_TOKEN_SINK_H

#include <yeast.h>

// Which arm a ys_token_sink is: how ys_write_token() renders a token. The two arms hold the same state — a byte
// transport — and differ only in what they write for a token, so the sink is a tagged struct, not a union: the wire
// writer spells a token in the wire format, the YAML emitter writes the bytes it spans.
typedef enum ys_sink_kind {
    YS_SINK_WIRE,   // ys_new_yeast_stream_writer
    YS_SINK_EMITTER // ys_new_yaml_stream_emitter
} ys_sink_kind;

// A sink of yeast tokens. Writing a token needs no per-token state — the wire writer works from a stack buffer, the
// emitter copies the token's own bytes — and the sink grows nothing, so it keeps the allocator to free itself and no
// ys_memory to track a cap against. It is a heap object only so a caller can hold it opaquely.
struct ys_token_sink {
    ys_sink_kind kind;
    ys_allocator allocator; // the one that made the sink, kept to unmake it
    ys_bytes_writer writer; // where the tokens go, spelled by the kind
};

#endif // YEAST_TOKEN_SINK_H
