// SPDX-License-Identifier: MIT
#include "token_source.h"

#include "memory.h"
#include "source.h"
#include <errno.h>
#include <stdint.h>
#include <yeast.h>

// A source of yeast tokens, whichever way they are made: YAML parsed from memory or a stream, or a yeast wire replayed
// from a stream. The three constructors build the one type; ys_read_token(), ys_are_tokens_stable() and
// ys_delete_token_source() work on it whichever arm it is, so code over tokens does not know or care which made them.

// Allocate a source and plant its kind. On success `*memory` is what it may allocate, for the caller to hand to the
// arm it goes on to build; on failure it is NULL with `errno` set, and the caller closes any reader it was handed.
static ys_token_source *ys_new_source(ys_source_kind kind, const ys_options *options, ys_memory *memory) {
    ys_token_source *source = ys_memory_new(memory, options, sizeof(*source)); // sets errno on failure
    if (source != NULL) {
        source->kind = kind;
    }
    return source;
}

ys_token_source *ys_new_yaml_memory_parser(const char *input, size_t length, const ys_options *options) {
    if (input == NULL && length > 0) {
        errno = EINVAL; // a NULL buffer of nonzero length is not an empty input, it is a mistake
        return NULL;
    }
    ys_memory memory;
    ys_token_source *source = ys_new_source(YS_SOURCE_PARSER, options, &memory);
    if (source != NULL) {
        ys_parser_init(&source->as.parser, memory, options);
        // The window is the caller's buffer, whole: no reader to give it more, no buffer of its own to free. That is
        // what makes a memory parser's tokens stable — their text is the caller's bytes.
        if (input != NULL) {
            source->as.parser.window.bytes = (const uint8_t *)input;
        }
        source->as.parser.window.source.size = length;
        source->as.parser.window.source.is_at_end = true;
    }
    return source;
}

ys_token_source *ys_new_yaml_stream_parser(ys_bytes_reader reader, const ys_options *options) {
    if (reader.read == NULL) {
        errno = EINVAL; // a reader with nothing to read from
        ys_discard_transport(reader.close, reader.context);
        return NULL;
    }
    ys_memory memory;
    ys_token_source *source = ys_new_source(YS_SOURCE_PARSER, options, &memory);
    if (source == NULL) {
        ys_discard_transport(reader.close, reader.context);
        return NULL;
    }
    ys_parser_init(&source->as.parser, memory, options);
    source->as.parser.window.source.reader = reader;
    return source;
}

ys_token_source *ys_new_yeast_stream_reader(ys_bytes_reader reader, const ys_options *options) {
    if (reader.read == NULL) {
        errno = EINVAL; // a reader with nothing to read from
        ys_discard_transport(reader.close, reader.context);
        return NULL;
    }
    ys_memory memory;
    ys_token_source *source = ys_new_source(YS_SOURCE_WIRE, options, &memory);
    if (source == NULL) {
        ys_discard_transport(reader.close, reader.context);
        return NULL;
    }
    ys_wire_init(&source->as.wire, memory);
    source->as.wire.source.reader = reader;
    return source;
}

int ys_read_token(ys_token_source *source, ys_token *token) {
    return source->kind == YS_SOURCE_PARSER ? ys_parser_read(&source->as.parser, token)
                                            : ys_wire_read(&source->as.wire, token);
}

bool ys_are_tokens_stable(const ys_token_source *source) {
    // A memory parser reads the caller's buffer, whole, with no reader whose next fill could overwrite a token's text.
    // A stream parser and a wire replay both read into a buffer the next call may reuse.
    return source->kind == YS_SOURCE_PARSER && source->as.parser.window.source.reader.read == NULL;
}

int ys_delete_token_source(ys_token_source *source) {
    if (source == NULL) {
        return 0; // deleting nothing cannot fail
    }
    if (source->kind == YS_SOURCE_PARSER) {
        ys_parser *parser = &source->as.parser;
        // The messages are static and the window may be the caller's; what is the parser's own is what it grew.
        void *buffers[] = {parser->window.source.bytes, parser->queue.tokens, parser->stack.frames, source};
        return ys_teardown(parser->window.source.reader.close, parser->window.source.reader.context,
                           parser->memory.allocator, buffers, sizeof(buffers) / sizeof(buffers[0]));
    }
    ys_wire *wire = &source->as.wire;
    void *buffers[] = {wire->source.bytes, wire->text, source};
    return ys_teardown(wire->source.reader.close, wire->source.reader.context, wire->memory.allocator, buffers,
                       sizeof(buffers) / sizeof(buffers[0]));
}
