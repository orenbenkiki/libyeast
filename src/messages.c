// SPDX-License-Identifier: MIT
#include "messages.h"

// An error says what to do about it, where there is anything to be done: running out of memory is the caller's sizing
// to fix, so the message says what to raise. Each is one literal, since a message split across lines inside this table
// would read as two of them.
static const char *const YS_MESSAGES[YS_MESSAGE_COUNT] = {
    [YS_MESSAGE_NOT_IMPLEMENTED] = "not implemented",
    [YS_MESSAGE_WIRE_BAD_POSITION] = "not the yeast wire format: expected a token position line",
    [YS_MESSAGE_WIRE_BAD_CODE] = "not the yeast wire format: unknown token code",
    [YS_MESSAGE_WIRE_BAD_ESCAPE] = "not the yeast wire format: invalid escape sequence",
    [YS_MESSAGE_WIRE_STRAY_BYTE] = "not the yeast wire format: a raw byte outside printable ASCII",
    [YS_MESSAGE_WIRE_TRUNCATED] = "not the yeast wire format: a position line with no token after it",
};

const char *ys_message(ys_message_id id) {
    return YS_MESSAGES[id];
}
