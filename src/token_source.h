// SPDX-License-Identifier: MIT
#ifndef YEAST_TOKEN_SOURCE_H
#define YEAST_TOKEN_SOURCE_H

#include "parser.h"
#include "wire.h"

// Which arm a ys_token_source is: tokens parsed from YAML, or replayed from a yeast wire. A caller reads either the
// same way, through ys_read_token(); the kind picks which arm answers.
typedef enum ys_source_kind {
    YS_SOURCE_PARSER, // ys_new_yaml_stream_parser / ys_new_yaml_memory_parser
    YS_SOURCE_WIRE    // ys_new_yeast_stream_reader
} ys_source_kind;

// A source of yeast tokens, whichever way they are made. The two arms hold genuinely different state — the parser a
// window, queue, stack and automaton; the wire a line buffer — so they are a union, with only the kind above it. Each
// arm keeps its own ys_memory, so teardown reads the live one.
struct ys_token_source {
    ys_source_kind kind;
    union {
        ys_parser parser;
        ys_wire wire;
    } as;
};

#endif // YEAST_TOKEN_SOURCE_H
