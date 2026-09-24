// SPDX-License-Identifier: MIT
// The layout of a `ys_token_source`. `yeast.h` declares that type to a caller as incomplete.

#ifndef YEAST_TOKEN_SOURCE_H
#define YEAST_TOKEN_SOURCE_H

#include "parser.h"
#include "wire.h"

// The arm a `ys_token_source` is. A parser arm parses tokens from YAML. A wire arm replays tokens from a yeast wire.
// `ys_read_token` reads either arm the same way. The kind picks which arm answers.
typedef enum ys_source_kind {
    YS_SOURCE_PARSER, // `ys_new_yaml_stream_parser` or `ys_new_yaml_memory_parser` makes this arm.
    YS_SOURCE_WIRE    // `ys_new_yeast_stream_reader` makes this arm.
} ys_source_kind;

// A `ys_token_source` hands out yeast tokens from a producer of either kind. The source holds an arm per kind. The
// parser holds a window and a queue. The parser also holds a stack and an automaton. The wire holds a line buffer. The
// arms are a union. The kind sits above that union. An arm keeps a `ys_memory` of its own. Teardown reads the live arm.
struct ys_token_source {
    ys_source_kind kind;
    union {
        ys_parser parser;
        ys_wire wire;
    } as;
};

#endif // YEAST_TOKEN_SOURCE_H
