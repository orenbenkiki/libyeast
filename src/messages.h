// SPDX-License-Identifier: MIT
#ifndef YEAST_MESSAGES_H
#define YEAST_MESSAGES_H

// What libyeast says to its caller. Each message is named by an index into one table rather than written where it is
// used, so that the whole of what the library ever says can be read in one place — and swapped, wholesale, for another
// language.
//
// These are the messages that do not depend on the grammar. The ones that do — the production the parser was inside and
// what it expected there — are a table of their own, generated into parser_tables.h and indexed by the automaton's
// state. A translation replaces both, and nothing else.
typedef enum ys_message_id {
    YS_MESSAGE_NOT_IMPLEMENTED,
    YS_MESSAGE_OUT_OF_MEMORY,
    YS_MESSAGE_READER_FAILED,
    YS_MESSAGE_COUNT // how many there are, and no message itself
} ys_message_id;

// The text of a message. It is a static string, so it outlives every token whose text it is.
const char *ys_message(ys_message_id id);

#endif // YEAST_MESSAGES_H
