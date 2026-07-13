// SPDX-License-Identifier: MIT
#ifndef YEAST_H
#define YEAST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>

/// @file yeast.h
/// @brief Public API for libyeast — a grammar-derived C YAML parser.

/// @mainpage libyeast
///
/// libyeast is a fast, single-pass, pull-driven YAML 1.2 parser in C, generated from the formal grammar so that its
/// conformance is derived rather than hand-tested.
///
/// The public API is the version query (ys_version(), ys_major(), ys_minor(), ys_patch()); the pull-parser surface —
/// create a parser with ys_new_string_parser() or ys_new_stream_parser(), pull @ref ys_token values with
/// ys_next_token(), and release it with ys_free_parser(); and the yeast wire format, which writes a token stream to
/// anywhere (ys_write_token()) and reads one back (ys_read_token()), so that a stream can be piped between tools,
/// stored, or compared against another parser's. The grammar-derived core is not implemented yet, so parsing currently
/// yields a single @ref YS_CODE_ERROR_FORMAT token reading "not implemented"; the wire format is complete and works.
///
/// For the architecture, see the
/// [design document](https://github.com/orenbenkiki/libyeast/blob/main/DESIGN.md); for what is left to build, see the
/// [roadmap](https://github.com/orenbenkiki/libyeast/blob/main/PLAN.md).

// Export control. Public symbols are marked YS_API; everything else is hidden by default (the build compiles with
// -fvisibility=hidden). Define YS_STATIC when linking libyeast statically (the installed CMake and pkg-config targets
// do this for you).
#if defined(_WIN32) || defined(__CYGWIN__)
#define YS_EXPORT __declspec(dllexport)
#define YS_IMPORT __declspec(dllimport)
#else
#define YS_EXPORT __attribute__((visibility("default")))
#define YS_IMPORT
#endif

#if defined(YS_STATIC)
#define YS_API
#elif defined(YEAST_BUILDING)
#define YS_API YS_EXPORT
#else
#define YS_API YS_IMPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

/// Return the libyeast version as a static, NUL-terminated "MAJOR.MINOR.PATCH" string.
///
/// @return a pointer to the version string; valid for the lifetime of the program, and the caller must not free it.
YS_API const char *ys_version(void);

/// Return the major component of the libyeast version.
///
/// @return the major version number.
YS_API int ys_major(void);

/// Return the minor component of the libyeast version.
///
/// @return the minor version number.
YS_API int ys_minor(void);

/// Return the patch component of the libyeast version.
///
/// @return the patch version number.
YS_API int ys_patch(void);

/// A source position. Every component is 0-based. `byte_offset` indexes raw bytes (use it to slice the input); the
/// other three count Unicode codepoints, which is what editors mean by line and column.
typedef struct ys_mark {
    size_t byte_offset; ///< Offset in bytes from the start of the input.
    size_t char_offset; ///< Offset in codepoints from the start of the input.
    size_t line;        ///< Line number.
    size_t column;      ///< Codepoint offset within the line.
} ys_mark;

/// Token classification — the yeast model. Zero-width `BEGIN_*`/`END_*` markers bracket productions; every other code
/// classifies a span of consumed input, and every byte of the input falls under exactly one of them.
///
/// The yeast wire format writes a token as one character and its text, and it is that mapping — not this enum's order,
/// which is ours — that the reference parser shares, so that the two token streams can be compared token for token.
/// `YS_CODE_TEXT` is `T`, `YS_CODE_META` is `t`, `YS_CODE_INDICATOR` is `I`, a `BEGIN_`/`END_` pair is an upper- and a
/// lower-case letter, and so on; `grammar/yeast-spec-1.2.yaml` lists them all.
///
/// libyeast adds three things to that vocabulary. `YS_CODE_BEGIN_STREAM` and `YS_CODE_END_STREAM` bracket the whole
/// stream, which nothing else does — the reference declares them and emits them nowhere. And the two failures that are
/// not the document's fault get codes of their own, so that a caller can tell whether to keep pulling; on the wire all
/// three failures are the same `!`, since a consumer of the wire has no choice to make either way.
typedef enum ys_code {
    YS_CODE_BOM,              ///< A byte-order mark.
    YS_CODE_TEXT,             ///< Content text characters.
    YS_CODE_META,             ///< Non-content (meta) characters.
    YS_CODE_BREAK,            ///< A line break.
    YS_CODE_LINE_FEED,        ///< A line break preserved inside a scalar.
    YS_CODE_LINE_FOLD,        ///< A line break folded to a space.
    YS_CODE_INDICATOR,        ///< A syntax indicator character.
    YS_CODE_WHITE,            ///< Separation white space.
    YS_CODE_INDENT,           ///< Indentation spaces.
    YS_CODE_DIRECTIVES_END,   ///< The `---` directives-end marker.
    YS_CODE_DOCUMENT_END,     ///< The `...` document-end marker.
    YS_CODE_BEGIN_ESCAPE,     ///< Start of an escape sequence.
    YS_CODE_END_ESCAPE,       ///< End of an escape sequence.
    YS_CODE_BEGIN_COMMENT,    ///< Start of a comment.
    YS_CODE_END_COMMENT,      ///< End of a comment.
    YS_CODE_BEGIN_DIRECTIVE,  ///< Start of a directive.
    YS_CODE_END_DIRECTIVE,    ///< End of a directive.
    YS_CODE_BEGIN_TAG,        ///< Start of a tag property.
    YS_CODE_END_TAG,          ///< End of a tag property.
    YS_CODE_BEGIN_HANDLE,     ///< Start of a tag handle.
    YS_CODE_END_HANDLE,       ///< End of a tag handle.
    YS_CODE_BEGIN_ANCHOR,     ///< Start of an anchor property.
    YS_CODE_END_ANCHOR,       ///< End of an anchor property.
    YS_CODE_BEGIN_PROPERTIES, ///< Start of a node's properties.
    YS_CODE_END_PROPERTIES,   ///< End of a node's properties.
    YS_CODE_BEGIN_ALIAS,      ///< Start of an alias node.
    YS_CODE_END_ALIAS,        ///< End of an alias node.
    YS_CODE_BEGIN_SCALAR,     ///< Start of scalar content.
    YS_CODE_END_SCALAR,       ///< End of scalar content.
    YS_CODE_BEGIN_SEQUENCE,   ///< Start of a sequence.
    YS_CODE_END_SEQUENCE,     ///< End of a sequence.
    YS_CODE_BEGIN_MAPPING,    ///< Start of a mapping.
    YS_CODE_END_MAPPING,      ///< End of a mapping.
    YS_CODE_BEGIN_PAIR,       ///< Start of a mapping key/value pair.
    YS_CODE_END_PAIR,         ///< End of a mapping key/value pair.
    YS_CODE_BEGIN_NODE,       ///< Start of a node.
    YS_CODE_END_NODE,         ///< End of a node.
    YS_CODE_BEGIN_DOCUMENT,   ///< Start of a document.
    YS_CODE_END_DOCUMENT,     ///< End of a document.
    YS_CODE_BEGIN_STREAM,     ///< Start of the token stream.
    YS_CODE_END_STREAM,       ///< End of the token stream.
    YS_CODE_ERROR_FORMAT,     ///< The document is malformed; @ref ys_token::text holds the message. Parsing resumes.
    YS_CODE_ERROR_MEMORY,     ///< The parser reached @ref ys_options::max_bytes, or its allocator refused. It halts.
    YS_CODE_ERROR_READER,     ///< The reader failed; @ref ys_token::text holds the message. The parser halts.
    YS_CODE_UNPARSED,         ///< Input skipped after a malformed document, classified rather than lost.
    YS_CODE_DETECTED          ///< An internally detected token.
} ys_code;

/// The character the yeast wire format writes for a code.
///
/// The three failures all write `!`: a consumer of the wire has no choice to make between them, since either
/// @ref YS_CODE_UNPARSED tokens follow or the stream ends. The message says which it was.
///
/// @param code the token code.
/// @return its wire character, or `?` if @p code is not a code.
YS_API char ys_code_char(ys_code code);

/// The code a yeast wire character stands for — the inverse of ys_code_char(), as far as it has one.
///
/// `!` yields @ref YS_CODE_ERROR_FORMAT, since the wire does not distinguish the three failures.
///
/// @param character the wire character.
/// @param code where to put the code it stands for; untouched if there is none.
/// @return true if @p character is a wire character.
YS_API bool ys_code_of_char(char character, ys_code *code);

/// A single token produced by ys_next_token(). For a zero-width `BEGIN_*`/`END_*` marker, `start` equals `end` and
/// `text` is NULL. For a leaf token, `text` points at the matched bytes and the byte length is
/// `end.byte_offset - start.byte_offset`.
typedef struct ys_token {
    ys_code code;     ///< The token's classification.
    ys_mark start;    ///< Position of the token's first character.
    ys_mark end;      ///< Position just past the token's last character.
    const char *text; ///< The matched bytes (leaf tokens), or NULL for zero-width markers; not NUL-terminated.
} ys_token;

/// A byte source for ys_new_stream_parser(). It abstracts over any stream — not only files or file descriptors;
/// adapters for common sources are provided separately.
typedef struct ys_reader {
    /// Read up to `size` bytes into `buffer`, returning the number of bytes read, 0 at end of input, or a negative
    /// value on error. The `context` argument is @ref context.
    ptrdiff_t (*read)(void *context, char *buffer, size_t size);
    /// Release @ref context, if it needs releasing. May be NULL. ys_free_parser() calls it exactly once.
    void (*close)(void *context);
    void *context; ///< Opaque state passed to @ref read and @ref close.
} ys_reader;

/// Whether a reader adapter takes ownership of its underlying resource (closing it) or merely borrows it.
typedef enum ys_ownership {
    YS_BORROW, ///< Leave the underlying descriptor/stream open when the parser is freed.
    YS_OWN     ///< Close the underlying descriptor/stream when the parser is freed.
} ys_ownership;

/// Build a reader that pulls from a file descriptor.
///
/// @param fd the file descriptor to read from.
/// @param ownership @ref YS_OWN to close `fd` when the parser is freed, or @ref YS_BORROW to leave it open.
/// @return a reader to hand to ys_new_stream_parser().
YS_API ys_reader ys_fd_reader(int fd, ys_ownership ownership);

/// Build a reader that pulls from a `FILE *` stream.
///
/// @param file the stream to read from.
/// @param ownership @ref YS_OWN to `fclose(file)` when the parser is freed, or @ref YS_BORROW to leave it open.
/// @return a reader to hand to ys_new_stream_parser().
YS_API ys_reader ys_fp_reader(FILE *file, ys_ownership ownership);

/// A byte sink, the mirror of @ref ys_reader. It abstracts over any stream, not only files or file descriptors.
typedef struct ys_writer {
    /// Write `size` bytes from `buffer`, returning the number written, or a negative value on error. The `context`
    /// argument is @ref context.
    ptrdiff_t (*write)(void *context, const char *buffer, size_t size);
    /// Release @ref context, if it needs releasing. May be NULL.
    void (*close)(void *context);
    void *context; ///< Opaque state passed to @ref write and @ref close.
} ys_writer;

/// Build a writer that pushes to a file descriptor.
///
/// @param fd the file descriptor to write to.
/// @param ownership @ref YS_OWN to close `fd` when ys_close_writer() is called, or @ref YS_BORROW to leave it open.
/// @return a writer to hand to ys_write_token().
YS_API ys_writer ys_fd_writer(int fd, ys_ownership ownership);

/// Build a writer that pushes to a `FILE *` stream.
///
/// @param file the stream to write to.
/// @param ownership @ref YS_OWN to `fclose(file)` when ys_close_writer() is called, or @ref YS_BORROW to leave it open.
/// @return a writer to hand to ys_write_token().
YS_API ys_writer ys_fp_writer(FILE *file, ys_ownership ownership);

/// Release whatever a writer owns. Calls its `close` callback, if it has one.
///
/// @param writer the writer to close.
YS_API void ys_close_writer(ys_writer *writer);

/// Write a token to a writer, in the yeast wire format — two lines, the first its position and the second its code
/// character followed by its text:
///
///     # B: 12, C: 12, L: 1, c: 4
///     I-
///
/// The text is escaped: a printable ASCII character other than a backslash is written as itself, and every other
/// character as `\xXX`, `\uXXXX` or `\UXXXXXXXX`. A zero-width marker has no text, so its second line is its code
/// character alone.
///
/// @param writer where to write.
/// @param token the token to write.
/// @return true if it was written; false if the writer failed.
YS_API bool ys_write_token(ys_writer *writer, ys_token token);

/// A custom allocator. Each callback that is NULL falls back individually to its C counterpart, so a zeroed struct
/// uses `malloc`/`realloc`/`free`, and setting only some callbacks mixes custom and standard behavior.
typedef struct ys_allocator {
    void *(*allocate)(void *context, size_t size);                  ///< Allocate `size` bytes, or return NULL.
    void *(*reallocate)(void *context, void *pointer, size_t size); ///< Resize `pointer` to `size` bytes.
    void (*deallocate)(void *context, void *pointer);               ///< Free `pointer`.
    void *context;                                                  ///< Opaque state passed to the callbacks.
} ys_allocator;

/// Parser construction options. A zeroed struct selects all defaults.
typedef struct ys_options {
    ys_allocator allocator; ///< Custom allocator; a zeroed allocator selects `malloc`/`realloc`/`free`.
    /// Cap on the memory the parser allocates for itself, in bytes. 0 means unlimited. Reaching it is a
    /// @ref YS_CODE_ERROR_MEMORY token, and it ends the parse for good: there is no way to raise the cap and carry on.
    /// To parse the input after such a failure, build a new parser with a larger cap and parse it again from its start.
    /// It is not an allocation failure, and @ref allocator never sees it.
    ///
    /// Four things grow, and this caps them together. The input a stream parser buffers, which a single enormous token
    /// fills, and so does a run of tokens whose codes are not yet decided — the empty lines that open a block scalar
    /// are content if a content line follows them and are chomped away if none does, so none of them can be handed back
    /// until the parser finds out which. The tokens themselves, held back with them. The parser's stack, which deep
    /// nesting grows and no quantity of input bounds. And an error's message. YAML bounds none of it: it bounds
    /// lookahead only for implicit keys, at 1024 characters.
    ///
    /// It does not count the input of a string parser, which belongs to the caller and is never copied — so a document
    /// of any size parses under a small cap, since only the held-back tokens and the stack are the parser's own.
    ///
    /// This is a convenience, not the only way. A custom @ref allocator that refuses beyond some limit caps the parser
    /// just as well, and the two are independent: neither knows the other exists.
    size_t max_bytes;
} ys_options;

/// An opaque leak-checking allocator: it wraps `malloc`/`realloc`/`free` and counts live allocations, so a test — or a
/// consumer — can confirm everything allocated through it was freed. Its overhead over plain `malloc`/`free` is a
/// single counter, so it is cheap enough to leave enabled in a release build if desired. It does not detect memory
/// corruption; use a sanitizer for that.
typedef struct ys_counting_allocator ys_counting_allocator;

/// Create a counting allocator.
///
/// @return a new counting allocator (free it with ys_free_counting_allocator()), or NULL on allocation failure.
YS_API ys_counting_allocator *ys_new_counting_allocator(void);

/// The allocator functions to place in @ref ys_options::allocator so allocations route through the counter.
///
/// @param counter the counting allocator.
/// @return a ys_allocator backed by @p counter.
YS_API ys_allocator ys_counting_allocator_functions(ys_counting_allocator *counter);

/// The number of allocations made through the counter that are still live. It is positive while a parser is in use;
/// check that it is back to 0 after you free everything (e.g. the parser) to confirm there was no leak.
///
/// @param counter the counting allocator.
/// @return the live buffer count.
YS_API size_t ys_counting_allocator_live_buffers(const ys_counting_allocator *counter);

/// Free a counting allocator. Does not free anything allocated through it.
///
/// @param counter the counting allocator to free; may be NULL, in which case this is a no-op.
YS_API void ys_free_counting_allocator(ys_counting_allocator *counter);

/// An opaque parser instance. Create it with ys_new_string_parser() or ys_new_stream_parser(), pull tokens with
/// ys_next_token(), and release it with ys_free_parser().
typedef struct ys_parser ys_parser;

/// Create a parser over an in-memory buffer. The buffer must outlive the parser; token text points directly into it,
/// so ys_are_tokens_stable() returns true.
///
/// @param input the bytes to parse; must remain valid until ys_free_parser().
/// @param length the number of bytes in @p input.
/// @param options construction options, or NULL for defaults.
/// @return a new parser, or NULL on allocation failure.
YS_API ys_parser *ys_new_string_parser(const char *input, size_t length, const ys_options *options);

/// Create a parser that pulls input from @p reader on demand. Token text points into an internal buffer valid only
/// until the next ys_next_token() call, so ys_are_tokens_stable() returns false.
///
/// @param reader the byte source; its `read` callback must be non-NULL.
/// @param options construction options, or NULL for defaults.
/// @return a new parser, or NULL on allocation failure.
YS_API ys_parser *ys_new_stream_parser(ys_reader reader, const ys_options *options);

/// Report whether token text pointers stay valid for the parser's whole lifetime.
///
/// @param parser the parser to query.
/// @return true for a string parser (text points into the caller's buffer); false for a stream parser (text is valid
/// only until the next ys_next_token() call).
YS_API bool ys_are_tokens_stable(const ys_parser *parser);

/// Pull the next token. Every byte of the input is accounted for by exactly one token, the ill-formed bytes included.
///
/// A **syntax error** does not end the parse. The returned token has code @ref YS_CODE_ERROR_FORMAT and its
/// @ref ys_token::text is the message; the bytes the parser then skips over are handed back as @ref YS_CODE_UNPARSED
/// tokens, and the parse resumes at the next document. So a stream of several documents, one of them malformed, still
/// yields the tokens of the others — and the malformed one is not silently lost, but classified.
///
/// A **resource error** does end it, for good. Running past @ref ys_options::max_bytes, or an allocator that refuses,
/// yields a @ref YS_CODE_ERROR_MEMORY token; a reader that fails yields a @ref YS_CODE_ERROR_READER one. Every later
/// call returns that same token. There is no way to raise the cap and carry on, and deliberately so: carrying on would
/// mean the parser were in a consistent, resumable state at the very point it ran out, which is a burden on every
/// allocation it makes, for a case that is a mistake in the caller's sizing and not in the document. To parse the input
/// after such a failure, build a new parser with a larger @ref ys_options::max_bytes, or an allocator that can meet it,
/// and parse the input again from its start.
///
/// The caller never frees a token's @ref ys_token::text. For a leaf token, that text stays valid for as long as
/// ys_are_tokens_stable() promises — the input's lifetime for a string parser, or only until the next call for a
/// stream parser. An error message is owned by the parser and stays valid until ys_free_parser().
///
/// @param parser the parser to advance.
/// @return the next token.
YS_API ys_token ys_next_token(ys_parser *parser);

/// An opaque reader of the yeast wire format — the inverse of ys_write_token(). It owns the storage a token's text is
/// unescaped into, which is why it is an object and not a function.
typedef struct ys_token_reader ys_token_reader;

/// Create a reader of the yeast wire format.
///
/// @param reader the byte source; its `read` callback must be non-NULL.
/// @param options construction options, or NULL for defaults. Only @ref ys_options::allocator and
/// @ref ys_options::max_bytes are consulted.
/// @return a new token reader, or NULL on allocation failure.
YS_API ys_token_reader *ys_new_token_reader(ys_reader reader, const ys_options *options);

/// Read the next token of a yeast wire stream.
///
/// The wire records only a token's start, so its @ref ys_token::end is worked out from that start and its text. Its
/// @ref ys_token::text points into storage the reader owns, and is valid only until the next call.
///
/// A `!` on the wire yields @ref YS_CODE_ERROR_FORMAT, since the wire does not distinguish the three failures.
///
/// @param reader the token reader.
/// @param token where to put the token read.
/// @return true if a token was read; false at the end of the stream, or if the stream is not the yeast wire format.
YS_API bool ys_read_token(ys_token_reader *reader, ys_token *token);

/// Free a token reader and everything it owns, including its reader's `close` callback, if it has one.
///
/// @param reader the token reader to free; may be NULL, in which case this is a no-op.
YS_API void ys_free_token_reader(ys_token_reader *reader);

/// Free a parser and everything it owns. If it was created with ys_new_stream_parser() and the reader has a `close`
/// callback, that callback is invoked.
///
/// @param parser the parser to free; may be NULL, in which case this is a no-op.
YS_API void ys_free_parser(ys_parser *parser);

#ifdef __cplusplus
}
#endif

#endif // YEAST_H
