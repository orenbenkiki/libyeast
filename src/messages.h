// SPDX-License-Identifier: MIT
// The message ids libyeast reports, and the call that gives the text of an id.

#ifndef YEAST_MESSAGES_H
#define YEAST_MESSAGES_H

#include <yeast.h>

// The messages libyeast says to its caller. An index into a table names a message, rather than a literal written where
// the code uses it. A reader then finds the library's words in a single place, and a translator swaps them wholesale
// for another language.
//
// These are the messages that do not depend on the grammar. The grammar-dependent ones live in
// `grammar/messages.yaml`, named by the `(cut)` or `(error)` that reports them. Those name the production the parser
// was inside and what it expected there. A translation replaces both.
//
// A host failure has no message. Such a failure is a return value rather than a token with text. Out of memory is such
// a failure, and so is a failed reader.
typedef enum ys_message_id {
    YS_MESSAGE_NOT_IMPLEMENTED,
    // The messages the reader of the yeast wire format says of a wire it cannot read. A message per way a wire can
    // break.
    YS_MESSAGE_WIRE_BAD_POSITION,
    YS_MESSAGE_WIRE_BAD_CODE,
    YS_MESSAGE_WIRE_BAD_ESCAPE,
    YS_MESSAGE_WIRE_STRAY_BYTE,
    YS_MESSAGE_WIRE_CHAR_IN_INVALID,
    YS_MESSAGE_WIRE_TRUNCATED,
    YS_MESSAGE_COUNT // the count of the ids above, and no message itself.
} ys_message_id;

// The text of a message. The string is static. The string outlives the token that names it.
const char *ys_message(ys_message_id id);

#endif // YEAST_MESSAGES_H
