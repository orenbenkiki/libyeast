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
/// conformance is derived rather than hand-tested. It parses a document into a stream of @ref ys_token values — the
/// yeast token model, whose codes are @ref ys_code — pulled one at a time.
///
/// @note The grammar-derived core is not implemented yet, so ys_read_token() on a parser currently fills a single
/// @ref YS_CODE_ERROR token reading "not implemented". The rest of the surface below — readers, writers, the
/// allocator, and the wire format — is complete and works.
///
/// @section reading Reading tokens
///
/// Make a @ref ys_token_source, pull @ref ys_token values from it with ys_read_token() until it ends, then delete it
/// with ys_delete_token_source(). A source comes from one of three constructors:
///
/// - ys_new_yaml_memory_parser() parses YAML from a caller-owned buffer. Nothing is copied, so a token's text points
///   into that buffer and stays valid as long as it does; ys_are_tokens_stable() returns true.
/// - ys_new_yaml_stream_parser() parses YAML pulled from a @ref ys_reader on demand. Build one with ys_fd_reader() for
///   a file descriptor or ys_fp_reader() for a `FILE *` — each takes a @ref ys_ownership saying whether to close the
///   underlying resource when the source is deleted — or fill in a @ref ys_reader of your own for any other byte
///   source.
/// - ys_new_yeast_stream_reader() replays a yeast wire (below) pulled from a @ref ys_reader, the inverse of writing
/// one.
///
/// A stream parser's and a wire replay's token text is valid only until the next ys_read_token() call, so
/// ys_are_tokens_stable() returns false for both. Reading is the same whichever source made the tokens.
///
/// @code
/// ys_token_source *source = ys_new_yaml_memory_parser(input, length, NULL);
/// ys_token token;
/// while (ys_read_token(source, &token) == YS_OK) {
///     // use token.code, token.start, token.end, token.text; a clean parse ends on YS_CODE_END_STREAM
/// }
/// ys_delete_token_source(source); // the loop ended on YS_FAILED_EOF, or YS_FAILED_READER/ALLOCATOR (errno set)
/// @endcode
///
/// @section wire The yeast wire format
///
/// A token stream can be serialized — to pipe it between tools, store it, or compare it against another parser's — as
/// the yeast wire format, a character and its escaped text per token. ys_write_token() writes one token to a
/// @ref ys_writer (build one with ys_fd_writer() or ys_fp_writer(), the mirror of the reader adapters), and
/// ys_new_yeast_stream_reader() makes a source that reads them back with ys_read_token().
///
/// @section options Options and memory
///
/// All three source constructors take a @ref ys_options, or NULL for defaults: a pluggable @ref ys_allocator, and
/// @ref ys_options::max_bytes to cap the memory the source allocates for itself. The @ref ys_counting_allocator is a
/// drop-in allocator that counts live allocations, so a test or a consumer can confirm nothing leaked.
///
/// @section errno The `errno` policy
///
/// Malformed data is never an `errno`: a syntax error or a wire that is not the wire format is a @ref YS_CODE_ERROR
/// token, part of the stream. A host failure is: ys_read_token() returns -1/-2/-3 with `errno` the reader's, `ENOMEM`,
/// or `ENODATA`; and the constructors, ys_write_token(), and the closers ys_close_writer() and ys_delete_token_source()
/// set `errno` on failure — `EINVAL` for a bad argument, `ENOMEM` for insufficient memory, or the value a failing
/// callback set, passed through untouched. The closers still release everything whatever fails, and return which
/// callback's close failed while `errno` holds the first one's reason. Every other function cannot fail and does not
/// touch `errno`. libyeast requires an allocator or reader callback to set `errno` on failure, and passes that value
/// through rather than overriding it.
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

/// The status an `int`-returning libyeast function reports. `YS_OK` is success; a negative value is a specific host
/// failure, with `errno` set. Each function's documentation says which of these it can return — no function returns all
/// of them: ys_read_token() reads one source, so it never reports `YS_FAILED_BOTH`, and the closers close things rather
/// than read, so they never report `YS_FAILED_EOF`.
typedef enum ys_status {
    YS_OK = 0,                ///< Success: a token was filled, or a closer closed everything cleanly.
    YS_FAILED_READER = -1,    ///< A reader callback failed; `errno` is what it set.
    YS_FAILED_ALLOCATOR = -2, ///< An allocator refused, or the cap was reached; `errno` is `ENOMEM`.
    YS_FAILED_BOTH = -3,      ///< A closer's reader and allocator both failed; `errno` is the reader's.
    YS_FAILED_EOF = -4        ///< A source was read past its end (ys_read_token() only); `errno` is `ENODATA`.
} ys_status;

/// Token classification — the yeast model. Zero-width `BEGIN_*`/`END_*` markers bracket productions; every other code
/// classifies a span of consumed input, and every byte of the input falls under exactly one of them.
///
/// The yeast wire format writes a token as one character and its text; it is that character mapping, not this enum's
/// order, that is the stable contract. `YS_CODE_TEXT` is `T`, `YS_CODE_META` is `t`, `YS_CODE_INDICATOR` is `I`, a
/// `BEGIN_`/`END_` pair is an upper- and a lower-case letter, and so on; `grammar/yeast-spec-1.2.yaml` lists them all.
///
/// `YS_CODE_BEGIN_STREAM` and `YS_CODE_END_STREAM` bracket the whole stream. `YS_CODE_UNPARSED_BREAK` gives a skipped
/// line's break its own code. `YS_CODE_ERROR` is the wire's one `!`: a host failure that is not the data's fault (out
/// of memory, a failed reader) is not a token and so has no code — it is ys_read_token()'s return value.
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
    YS_CODE_ERROR,            ///< The document is malformed; @ref ys_token::text holds the message. A malformed wire
                              ///< read by ys_read_token() is one of these too — bad data, like a bad document. A host
                              ///< failure that is not the data's fault (out of memory, a failed reader) is not a token:
                              ///< it is ys_read_token()'s return value.
    YS_CODE_UNPARSED_TEXT,    ///< Input skipped after a malformed document, classified rather than lost. Like every
                           ///< token it stays within one line; a skipped line's break is a @ref YS_CODE_UNPARSED_BREAK.
    YS_CODE_UNPARSED_BREAK,   ///< The line break of skipped input. Unparsed like the content it follows, but its own
                              ///< code so it is not taken for a structural break the parser never found.
    YS_CODE_UNPARSED_INVALID, ///< A run of bytes encoding no valid Unicode character, and so naming no character the
                              ///< grammar could match — whatever the encoding they were read in. Its
                              ///< @ref ys_token::text is bytes rather than codepoints, which is why it has a code of
                              ///< its own rather than being @ref YS_CODE_UNPARSED_TEXT: on the wire an escape under
                              ///< this code spells the byte, and under any other the codepoint. Its text must encode no
                              ///< character throughout — every byte of it a place where none begins — as every other
                              ///< code's must encode them all, and ys_write_token() holds both to it. No parse emits it
                              ///< yet (see `PLAN.md` §4); the wire round-trips it.
    YS_CODE_DETECTED          ///< Reserved for libyeast's own detection tokens (see the token-emission levels in
                              ///< `PLAN.md`). No parse emits it yet; the wire round-trips it.
} ys_code;

/// The character the yeast wire format writes for a code.
///
/// The three failures all write `!`: a consumer of the wire has no choice to make between them, since either
/// @ref YS_CODE_UNPARSED_TEXT tokens follow or the stream ends. The message says which it was.
///
/// @param code the token code.
/// @return its wire character, or `'\0'` where the wire spells nothing: a value that is not a code at all, and
/// @ref YS_CODE_ERROR, which is one but which no wire carries and no writer emits. Every character a wire does
/// carry is printable, so `'\0'` can never be one of them — a line is NUL-terminated, and a code written as one would
/// read back as an empty line.
YS_API char ys_code_char(ys_code code);

/// The code a yeast wire character stands for — the inverse of ys_code_char(), as far as it has one.
///
/// `!` yields @ref YS_CODE_ERROR, since the wire does not distinguish the three failures.
///
/// @param character the wire character.
/// @param code where to put the code it stands for; untouched if there is none.
/// @return true if @p character is a wire character.
YS_API bool ys_code_of_char(char character, ys_code *code);

/// A single token produced by ys_read_token(). For a zero-width `BEGIN_*`/`END_*` marker, `start` equals `end` and
/// `text` is NULL. For a leaf token, `text` points at the matched bytes and the byte length is
/// `end.byte_offset - start.byte_offset`. An error consumes nothing, so `start` equals `end` there too — but its `text`
/// is the message, which is not in the input, and is never NULL; the input it failed on comes back behind it as
/// @ref YS_CODE_UNPARSED_TEXT tokens.
///
/// **No token spans a line.** Every leaf token lies within one line, so `start.line` equals `end.line` for all of them,
/// and a line break is always a token of its own. The input skipped after a malformed document obeys this like
/// everything else, rather than coming back as one token holding the lot: each skipped line yields a
/// @ref YS_CODE_UNPARSED_TEXT token for its content and a @ref YS_CODE_UNPARSED_BREAK for its break. The break is
/// unparsed as well, but its own code, since calling it a @ref YS_CODE_BREAK would claim a structure the parser never
/// found.
typedef struct ys_token {
    ys_code code;     ///< The token's classification.
    ys_mark start;    ///< Position of the token's first character.
    ys_mark end;      ///< Position just past the token's last character.
    const char *text; ///< The matched bytes (leaf tokens), the message (errors), or NULL for a zero-width marker. Not
                      ///< NUL-terminated, except for a message, which is.
} ys_token;

/// A byte source for a stream token source (ys_new_yaml_stream_parser() or ys_new_yeast_stream_reader()). It abstracts
/// over any stream — not only files or file descriptors; adapters for common sources are provided separately.
typedef struct ys_reader {
    /// Read up to `size` bytes into `buffer`, returning the number of bytes read, 0 at end of input, or a negative
    /// value on error. On error it must set `errno`; libyeast passes that value through untouched, so it survives
    /// alongside the @ref YS_CODE_ERROR_READER or @ref YS_CODE_ERROR token the failure becomes. The `context`
    /// argument is @ref context.
    ptrdiff_t (*read)(void *context, char *buffer, size_t size);
    /// Release @ref context, if it needs releasing, returning 0 or -1 with `errno` set — `close(2)`'s contract, as
    /// @ref read is `read(2)`'s. May be NULL. Whatever the reader is handed to calls it exactly once:
    /// ys_delete_token_source() when it was built, and the constructor itself when it was not. Either way the caller
    /// never calls it, having handed the reader over.
    ///
    /// A constructor that is already failing discards the result: it has a reason to report of its own, and `errno`
    /// still holds it afterwards.
    int (*close)(void *context);
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
/// @return a reader to hand to a stream token source.
YS_API ys_reader ys_fd_reader(int fd, ys_ownership ownership);

/// Build a reader that pulls from a `FILE *` stream.
///
/// @param file the stream to read from.
/// @param ownership @ref YS_OWN to `fclose(file)` when the parser is freed, or @ref YS_BORROW to leave it open.
/// @return a reader to hand to a stream token source.
YS_API ys_reader ys_fp_reader(FILE *file, ys_ownership ownership);

/// A byte sink, the mirror of @ref ys_reader. It abstracts over any stream, not only files or file descriptors.
typedef struct ys_writer {
    /// Write `size` bytes from `buffer`, returning the number written, or a negative value on error. On error it must
    /// set `errno`; ys_write_token() passes that value through, so it is what the caller reads after ys_write_token()
    /// returns false. The `context` argument is @ref context.
    ptrdiff_t (*write)(void *context, const char *buffer, size_t size);
    /// Release @ref context, if it needs releasing, returning 0 or -1 with `errno` set — `close(2)`'s contract, as
    /// @ref write is `write(2)`'s. May be NULL. ys_close_writer() calls it and reports what it said, which is the whole
    /// reason it says anything: a buffered write does not reach the disk until the flush a close performs, so this is
    /// where a full disk is discovered, long after every ys_write_token() has returned true.
    int (*close)(void *context);
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

/// Release whatever a writer owns, and say whether that succeeded: it calls the writer's `close` callback, if it has
/// one, and a buffered stream does not reach its destination until this flushes it. So a token stream written without a
/// fault can still fail here, at the last, and this is where a full disk or a broken pipe is finally seen — the return
/// of the last ys_write_token() is not the whole story, and this is the rest of it. Call it exactly once.
///
/// @param writer the writer to close.
/// @return 0 on success, or -1 with `errno` set to what the `close` callback reported.
YS_API int ys_close_writer(ys_writer *writer);

/// Write a token to a writer, in the yeast wire format.
///
/// A token is two lines, the first its position and the second its code character followed by its text:
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
/// @return true if it was written; false if the writer failed, in which case `errno` is what the writer's `write`
/// callback set, or `EINVAL` if the token cannot be written at all: a code the wire spells nothing for, or text whose
/// bytes are not what @p token's code says they are.
YS_API bool ys_write_token(ys_writer *writer, ys_token token);

/// A custom allocator. Each callback that is NULL falls back individually to its C counterpart, so a zeroed struct
/// uses `malloc`/`realloc`/`free`, and setting only some callbacks mixes custom and standard behavior.
///
/// `allocate` and `reallocate` must set `errno` when they return NULL, as `malloc` and `realloc` do. libyeast passes
/// that value through the constructor that was allocating, so it is what the caller reads after the constructor returns
/// NULL; a debug build asserts a failing allocator set it.
typedef struct ys_allocator {
    void *(*allocate)(void *context, size_t size);                  ///< Allocate `size` bytes, or NULL (set `errno`).
    void *(*reallocate)(void *context, void *pointer, size_t size); ///< Resize `pointer` to `size` bytes, or NULL.
    void (*deallocate)(void *context, void *pointer);               ///< Free `pointer`.
    /// Release @ref context, if it needs releasing, returning 0 or -1 with `errno` set — `close(2)`'s contract, as the
    /// reader's and the writer's closes are. May be NULL, which is the common case: an allocator that is only a set of
    /// functions has nothing to release. Whatever was built with it calls this once, when it is freed, after the last
    /// @ref deallocate — so an arena or a pool can be torn down with the thing that was built out of it.
    int (*close)(void *context);
    void *context; ///< Opaque state passed to the callbacks.
} ys_allocator;

/// What the parser does with the input it could not parse.
///
/// Each gives up less of the input than the one before it, and none of them loses a byte: whatever is skipped comes
/// back as @ref YS_CODE_UNPARSED_TEXT content and @ref YS_CODE_UNPARSED_BREAK breaks, so a caller can always see what
/// was passed over. Where a policy has nothing to resume at it behaves as the one before it.
typedef enum ys_resume {
    /// The error ends the parse. The rest of the input comes back as @ref YS_CODE_UNPARSED_TEXT tokens, and nothing in
    /// it is
    /// parsed — so nothing is silently lost, which is why it is the default.
    YS_RESUME_NONE,
    /// Skip to the next document and carry on parsing there, so that one malformed document in a stream does not cost
    /// the caller the others. The lines skipped to reach it come back as @ref YS_CODE_UNPARSED_TEXT tokens.
    YS_RESUME_DOCUMENT,
    /// Skip to the next line no more indented than the entry that failed, and carry on parsing *inside* the document,
    /// so that a malformed entry does not cost the caller the rest of its container. What is recovered is a sibling of
    /// what failed rather than a child of it — the markers of the abandoned entry are closed before it. Where nothing
    /// encloses the failure there is no indentation to bound it by, and this is @ref YS_RESUME_DOCUMENT.
    YS_RESUME_INDENT
} ys_resume;

/// Parser construction options. A zeroed struct selects all defaults.
typedef struct ys_options {
    ys_allocator allocator; ///< Custom allocator; a zeroed allocator selects `malloc`/`realloc`/`free`.
    ys_resume resume;       ///< What to do after a malformed document; the default is @ref YS_RESUME_NONE.
    /// Cap on the memory the parser allocates for itself, in bytes. 0 means unlimited. Reaching it is a
    /// @ref YS_CODE_ERROR_MEMORY token, and it ends the parse for good: there is no way to raise the cap and carry on.
    /// To parse the input after such a failure, build a new parser with a larger cap and parse it again from its start.
    /// It is not an allocation failure, and @ref allocator never sees it.
    ///
    /// Three things grow, and this caps them together. The input a stream parser buffers, which a single enormous token
    /// fills, and so does a run of tokens whose codes are not yet decided — the empty lines that open a block scalar
    /// are content if a content line follows them and are chomped away if none does, so none of them can be handed back
    /// until the parser finds out which. The tokens themselves, held back with them. And the parser's stack, which deep
    /// nesting grows and no quantity of input bounds. YAML bounds none of it: it bounds lookahead only for implicit
    /// keys, at 1024 characters. An error's message is not among them, being a static string.
    ///
    /// It does not count the input of a string parser, which belongs to the caller and is never copied — so a document
    /// of any size parses under a small cap, since only the held-back tokens and the stack are the parser's own.
    ///
    /// This is a convenience, not the only way. A custom @ref allocator that refuses beyond some limit caps the parser
    /// just as well, and the two are independent: neither knows the other exists.
    size_t max_bytes;
} ys_options;

/// An opaque leak-checking allocator. It wraps `malloc`/`realloc`/`free` and counts live allocations, so a test — or a
/// consumer — can confirm everything allocated through it was freed. Its overhead over plain `malloc`/`free` is a
/// single counter, so it is cheap enough to leave enabled in a release build if desired. It does not detect memory
/// corruption; use a sanitizer for that.
typedef struct ys_counting_allocator ys_counting_allocator;

/// Create a counting allocator.
///
/// @return a new counting allocator (free it with ys_delete_counting_allocator()), or NULL on allocation failure, with
/// `errno` set to `ENOMEM` by the underlying `malloc`.
YS_API ys_counting_allocator *ys_new_counting_allocator(void);

/// The allocator functions to place in @ref ys_options::allocator so allocations route through the counter. Its
/// @ref ys_allocator::close is ys_counting_allocator_check(), so whatever is built with it checks itself when it is
/// freed and there is nothing to remember to do.
///
/// That makes the @ref ys_options single-use, as it does for any allocator carrying a `close`: the object being freed
/// closes the allocator, so a second object built from the same options would find it already closed. To hand one
/// counter to several objects, set `allocator.close` to NULL and call ys_counting_allocator_check() yourself once they
/// are all freed — which is the only moment the count means anything anyway.
///
/// @param counter the counting allocator.
/// @return a ys_allocator backed by @p counter.
YS_API ys_allocator ys_counting_allocator_functions(ys_counting_allocator *counter);

/// Check that nothing allocated through the counter is still live — the @ref ys_allocator::close
/// ys_counting_allocator_functions() installs, and callable directly with the same meaning. A debug build asserts,
/// since a leak is a bug in the code being counted and a stack trace is worth more than a return value; a release build
/// reports it.
///
/// @param counter the counting allocator, as a `void *` so that this is a @ref ys_allocator::close.
/// @return 0 if nothing is live, or -1 with `errno` set to `EBUSY` if anything is.
YS_API int ys_counting_allocator_check(void *counter);

/// The number of allocations made through the counter that are still live. It is positive while a parser is in use;
/// check that it is back to 0 after you free everything (e.g. the parser) to confirm there was no leak.
///
/// @param counter the counting allocator.
/// @return the live buffer count.
YS_API size_t ys_counting_allocator_live_buffers(const ys_counting_allocator *counter);

/// Free a counting allocator. Does not free anything allocated through it.
///
/// @param counter the counting allocator to free; may be NULL, in which case this is a no-op.
YS_API void ys_delete_counting_allocator(ys_counting_allocator *counter);

/// An opaque source of yeast tokens: YAML parsed from memory (ys_new_yaml_memory_parser()) or a stream
/// (ys_new_yaml_stream_parser()), or a yeast wire replayed from a stream (ys_new_yeast_stream_reader()). A caller pulls
/// tokens from it with ys_read_token() the same way whichever it is, and deletes it with ys_delete_token_source() — so
/// code over tokens runs unchanged whether they were parsed or replayed.
typedef struct ys_token_source ys_token_source;

/// Create a source that parses YAML from an in-memory buffer. The buffer must outlive the source; token text points
/// directly into it, so ys_are_tokens_stable() returns true.
///
/// @param input the bytes to parse; must remain valid until ys_delete_token_source(). NULL is allowed only with a
/// @p length of 0, an empty input.
/// @param length the number of bytes in @p input.
/// @param options construction options, or NULL for defaults.
/// @return a new source, or NULL on failure, with `errno` set: `EINVAL` if @p input is NULL and @p length is not 0;
/// otherwise `ENOMEM` — @ref ys_options::max_bytes is smaller than a source, or the allocator refused (and set it).
YS_API ys_token_source *ys_new_yaml_memory_parser(const char *input, size_t length, const ys_options *options);

/// Create a source that parses YAML pulled from @p reader on demand. Token text points into an internal buffer valid
/// only until the next ys_read_token() call, so ys_are_tokens_stable() returns false.
///
/// **The source takes @p reader, whether or not it can be built.** If it cannot, it closes @p reader before returning
/// NULL — calling its `close` callback exactly as ys_delete_token_source() would have. So a @ref YS_OWN reader's file
/// descriptor is never leaked, and the caller must not close it themselves: they no longer have anything to close it
/// with, and doing so would close it twice.
///
/// @param reader the byte source; its `read` callback must be non-NULL. Owned by the source from this call onwards.
/// @param options construction options, or NULL for defaults.
/// @return a new source, or NULL on failure — in which case @p reader has been closed — with `errno` set: `EINVAL` if
/// @p reader has no `read` callback; otherwise `ENOMEM`.
YS_API ys_token_source *ys_new_yaml_stream_parser(ys_reader reader, const ys_options *options);

/// Create a source that replays a yeast wire pulled from @p reader on demand — the inverse of a yeast writer. Token
/// text points into an internal buffer valid only until the next ys_read_token() call, so ys_are_tokens_stable()
/// returns false.
///
/// **The source takes @p reader, whether or not it can be built.** If it cannot, it closes @p reader before returning
/// NULL, exactly as ys_delete_token_source() would have.
///
/// @param reader the byte source; its `read` callback must be non-NULL. Owned by the source from this call onwards.
/// @param options construction options, or NULL for defaults. Only @ref ys_options::allocator and
/// @ref ys_options::max_bytes are consulted.
/// @return a new source, or NULL on failure — in which case @p reader has been closed — with `errno` set: `EINVAL` if
/// @p reader has no `read` callback; otherwise `ENOMEM`.
YS_API ys_token_source *ys_new_yeast_stream_reader(ys_reader reader, const ys_options *options);

/// Report whether token text pointers stay valid for the source's whole lifetime.
///
/// @param source the source to query.
/// @return true for a memory parser (text points into the caller's buffer, and an error's message is a static string);
/// false for a stream parser or a wire replay (text points into a buffer the next ys_read_token() call may overwrite).
YS_API bool ys_are_tokens_stable(const ys_token_source *source);

/// Read the next token into @p token. Every byte of a parsed input is accounted for by exactly one token, the
/// ill-formed bytes included.
///
/// A **malformed input** — a syntax error in the YAML, or a wire that is not the wire format — fills @p token with a
/// @ref YS_CODE_ERROR whose @ref ys_token::text is the message, and returns 0 like any other token. For a parser, what
/// happens next is what @ref ys_options::resume says: by default the parse ends and the rest of the input comes back as
/// @ref YS_CODE_UNPARSED_TEXT, so nothing is silently lost; @ref YS_RESUME_DOCUMENT carries on at the next document and
/// @ref YS_RESUME_INDENT at the next line no more indented than the entry that failed. For a wire replay, the wire is
/// spent after the error. The message names what was expected, not what was found — the first
/// @ref YS_CODE_UNPARSED_TEXT token behind the error begins at exactly the byte that failed.
///
/// A **host failure** is not a token but the return value, so the token model stays about the data and not about the
/// machine running on it. @ref YS_FAILED_READER is the reader failing, @ref YS_FAILED_ALLOCATOR the allocator refusing
/// (or @ref ys_options::max_bytes reached) — each with `errno` the callback's — and @ref YS_FAILED_EOF, with `errno`
/// `ENODATA`, once the stream has ended and been read past. A resource failure ends the source for good: there is no
/// `end-stream` to close the `begin-stream`, the missing close being the sign it did not finish, and every later call
/// returns @ref YS_FAILED_EOF. There is no way to raise the cap and carry on, deliberately: it would burden every
/// allocation with being resumable at the point it ran out, for a mistake in the caller's sizing and not in the data.
/// To read the input after such a failure, build a new source with a larger @ref ys_options::max_bytes, or an allocator
/// that can meet it, and read it again from its start.
///
/// The caller never frees @ref ys_token::text; one rule covers every token, whatever its code — it stays valid for as
/// long as ys_are_tokens_stable() promises, an error's message being a static string and no exception.
///
/// @param source the source to advance.
/// @param token where to put the token read; left untouched on a negative return.
/// @return @ref YS_OK with @p token filled, or @ref YS_FAILED_READER, @ref YS_FAILED_ALLOCATOR or @ref YS_FAILED_EOF.
YS_API int ys_read_token(ys_token_source *source, ys_token *token);

/// Delete a token source and everything it owns, and say whether the two closeable things it holds closed cleanly: the
/// reader it was built over (a stream parser's or a wire replay's, if its @ref ys_reader::close is not NULL) and its
/// allocator (if @ref ys_allocator::close is not NULL). Both are closed, and everything is freed, whichever of them
/// fails — so a close that fails leaks nothing.
///
/// @param source the source to delete; may be NULL, in which case this is a no-op returning @ref YS_OK.
/// @return @ref YS_OK if every close succeeded; @ref YS_FAILED_READER if the reader's close failed,
/// @ref YS_FAILED_ALLOCATOR if the allocator's did, @ref YS_FAILED_BOTH if both did. `errno` holds the first failure's
/// reason — the reader's, where both failed, the second being the documented limit of what one `errno` can say. A
/// caller needing both must record them in its own callbacks' @ref context.
YS_API int ys_delete_token_source(ys_token_source *source);

#ifdef __cplusplus
}
#endif

#endif // YEAST_H
