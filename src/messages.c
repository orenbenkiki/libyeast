// SPDX-License-Identifier: MIT
#include "messages.h"

// An error says what to do about it, where there is anything to be done: running out of memory is the caller's sizing
// to fix, so the message says what to raise. Each is one literal, since a message split across lines inside this table
// would read as two of them.
static const char *const YS_MESSAGES[YS_MESSAGE_COUNT] = {
    [YS_MESSAGE_NOT_IMPLEMENTED] = "not implemented",
    [YS_MESSAGE_OUT_OF_MEMORY] = "out of memory: raise ys_options::max_bytes, or use an allocator that can meet it",
    [YS_MESSAGE_READER_FAILED] = "the reader failed: it reported an error before the end of the input",
};

const char *ys_message(ys_message_id id) {
    return YS_MESSAGES[id];
}
