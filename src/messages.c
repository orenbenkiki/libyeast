// SPDX-License-Identifier: MIT
// The text of the messages libyeast reports, and the lookup from a `ys_message_id` to its text.

#include "messages.h"

// The messages libyeast says of data it cannot read. A host failure has no message at all. Such a failure is a
// return value rather than a token with text. A message is a single literal. A message split across lines inside this
// table would read as a pair.
static const char *const YS_MESSAGES[YS_MESSAGE_COUNT] = {
    [YS_MESSAGE_NOT_IMPLEMENTED] = "not implemented",
    [YS_MESSAGE_WIRE_BAD_POSITION] = "not the yeast wire format: expected a token position line",
    [YS_MESSAGE_WIRE_BAD_CODE] = "not the yeast wire format: unknown token code",
    [YS_MESSAGE_WIRE_BAD_ESCAPE] = "not the yeast wire format: invalid escape sequence",
    [YS_MESSAGE_WIRE_STRAY_BYTE] = "not the yeast wire format: a raw byte outside printable ASCII",
    [YS_MESSAGE_WIRE_CHAR_IN_INVALID] = "not the yeast wire format: a valid character in an unparsed-invalid token",
    [YS_MESSAGE_WIRE_TRUNCATED] = "not the yeast wire format: a position line with no token after it",
};

const char *ys_message(ys_message_id id) {
    return YS_MESSAGES[id];
}
