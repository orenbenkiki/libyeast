// SPDX-License-Identifier: MIT
#ifndef YEAST_TOKEN_SINK_H
#define YEAST_TOKEN_SINK_H

#include "memory.h"
#include <yeast.h>

// Which arm a ys_token_sink is: the mirror of a ys_token_source's kind. One kind now — the yeast wire writer; a YAML
// emitter (ys_new_yaml_stream_emitter) will add another, and code writing tokens will not know which consumed them.
typedef enum ys_sink_kind {
    YS_SINK_WIRE // ys_new_yeast_stream_writer
} ys_sink_kind;

// A sink of yeast tokens. The wire-writer arm needs no per-token state — a token serializes to the byte transport from
// a stack buffer — but the sink is a heap object so a caller can hold it opaquely, and so the emitter's arm has
// somewhere to keep its state. The two arms hold different state, so they are a union with only the kind above them.
struct ys_token_sink {
    ys_sink_kind kind;
    union {
        struct {
            ys_memory memory;
            ys_writer writer; // where the wire is written
        } wire;
    } as;
};

#endif // YEAST_TOKEN_SINK_H
