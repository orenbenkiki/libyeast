// SPDX-License-Identifier: MIT
// The message ids libyeast reports, and the call that gives the text of an id.

#ifndef YEAST_MESSAGES_H
#define YEAST_MESSAGES_H

#include <yeast.h>

// A `ys_message_id` names a message libyeast says to its caller. An index into a table names a message, rather than a
// literal written where the code uses it.
//
// These are the messages that do not depend on the grammar. The grammar-dependent ones live in `grammar/messages.yaml`.
// A `(cut)` token or an `(error)` token reports such a message. That token names the message. That message names the
// production the parser was inside and what it expected there. A translation replaces the production name and the
// expectation.
//
// A host failure has no message. Such a failure is a return value rather than a token with text. Out of memory is such
// a failure. A failed reader is such a failure as well.
typedef enum ys_message_id {
    YS_MESSAGE_NOT_IMPLEMENTED,
    // The reader of the yeast wire format says these messages when it cannot read a wire. A message names a way a wire
    // can break.
    YS_MESSAGE_WIRE_BAD_POSITION,
    YS_MESSAGE_WIRE_BAD_CODE,
    YS_MESSAGE_WIRE_BAD_ESCAPE,
    YS_MESSAGE_WIRE_STRAY_BYTE,
    YS_MESSAGE_WIRE_CHAR_IN_INVALID,
    YS_MESSAGE_WIRE_TRUNCATED,
    YS_MESSAGE_COUNT // This id counts the ids above and names no message.
} ys_message_id;

// The text of a message. The string is static. The string outlives the token that names it.
const char *ys_message(ys_message_id id);

#endif // YEAST_MESSAGES_H
