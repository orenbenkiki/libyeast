// SPDX-License-Identifier: MIT
#ifndef YEAST_MESSAGES_H
#define YEAST_MESSAGES_H

#include <yeast.h>

// What libyeast says to its caller. Each message is named by an index into one table rather than written where it is
// used, so that the whole of what the library ever says can be read in one place — and swapped, wholesale, for another
// language.
//
// These are the messages that do not depend on the grammar. The ones that do — the production the parser was inside and
// what it expected there — are `grammar/messages.yaml`'s, named by the `(cut)` or `(error)` that reports them, and will
// be a table of their own once there is an automaton to index it by state. A translation replaces both, and nothing
// else. A host failure — out of memory, a failed reader — has no message: it is a return value, not a token with text.
typedef enum ys_message_id {
    YS_MESSAGE_NOT_IMPLEMENTED,
    // What the reader of the yeast wire format says of a wire it cannot read, one message per way it can be broken.
    YS_MESSAGE_WIRE_BAD_POSITION,
    YS_MESSAGE_WIRE_BAD_CODE,
    YS_MESSAGE_WIRE_BAD_ESCAPE,
    YS_MESSAGE_WIRE_STRAY_BYTE,
    YS_MESSAGE_WIRE_TRUNCATED,
    YS_MESSAGE_COUNT // how many there are, and no message itself
} ys_message_id;

// The text of a message. It is a static string, so it outlives every token whose text it is.
const char *ys_message(ys_message_id id);

#endif // YEAST_MESSAGES_H
