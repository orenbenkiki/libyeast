// SPDX-License-Identifier: MIT
/// @file yeast.h
/// @brief Public API for libyeast, a grammar-derived C YAML parser.

#ifndef YEAST_H
#define YEAST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>

/// @mainpage libyeast
///
/// libyeast is a fast, single-pass, pull-driven YAML 1.2 parser in C. The generator derives libyeast from the formal
/// grammar. Conformance follows from that derivation rather than from hand-testing. libyeast parses a document into a
/// stream of @ref ys_token values, and a caller pulls a token at a time. That is the yeast token model, and
/// @ref ys_code holds its codes.
///
/// @note The grammar-derived core does not exist yet. ys_read_token() on a parser fills a single
/// @ref YS_CODE_ERROR token reading "not implemented". The rest of the surface below is complete and works. That is
/// the readers and the writers. It is also the allocator and the wire format.
///
/// @section reading Reading tokens
///
/// Make a @ref ys_token_source. Pull @ref ys_token values from it with ys_read_token() until the source ends. Then
/// delete the source with ys_delete_token_source(). A source comes from a constructor.
///
/// - ys_new_yaml_memory_parser() parses YAML from a caller-owned buffer. libyeast copies nothing. A token's text
///   points into that buffer and stays valid as long as the buffer does. ys_are_tokens_stable() returns true.
/// - ys_new_yaml_stream_parser() parses YAML pulled from a @ref ys_bytes_reader on demand. Build a reader with
///   ys_fd_reader() for a file descriptor or ys_fp_reader() for a `FILE *`. Both take a @ref ys_ownership saying
///   whether to close the underlying resource when ys_delete_token_source() runs. For any other byte source, fill in
///   a @ref ys_bytes_reader of your own.
/// - ys_new_yeast_stream_reader() replays a yeast wire (below) pulled from a @ref ys_bytes_reader. That is the
///   inverse of writing a wire.
///
/// The token text of a stream parser and of a wire replay is valid until the next ys_read_token() call.
/// ys_are_tokens_stable() returns false for both. Reading is the same whichever source made the tokens.
///
/// @code
/// ys_token_source *source = ys_new_yaml_memory_parser(input, length, NULL);
/// ys_token token;
/// while (ys_read_token(source, &token) == YS_OK) {
///     // use token.code and token.start. also token.end and token.text
///     // a clean parse ends on YS_CODE_END_STREAM
/// }
/// ys_delete_token_source(source); // the loop ended on YS_FAILED_ACTION, or YS_FAILED_STREAM/MEMORY (errno set)
/// @endcode
///
/// @section wire The yeast wire format
///
/// libyeast can serialize a token stream as the yeast wire format. That is a character and its escaped text per
/// token. The wire pipes a stream between tools, stores a stream, or compares a stream against what another parser
/// produced.
///
/// ys_new_yeast_stream_writer() makes a @ref ys_token_sink over a @ref ys_bytes_writer. Build a writer with
/// ys_fd_writer() or ys_fp_writer(), the mirror of the reader adapters. ys_write_token() feeds the sink.
/// ys_new_yeast_stream_reader() makes a source that reads the tokens back with ys_read_token().
///
/// @section options Options and memory
///
/// A source constructor takes a @ref ys_options, or NULL for defaults. The options hold a pluggable
/// @ref ys_allocator. They also hold @ref ys_options::max_bytes. That caps the memory the source allocates for
/// itself.
///
/// The @ref ys_counting_allocator is a drop-in allocator that counts live allocations. A test or a consumer can then
/// confirm nothing leaked.
///
/// @section errno The `errno` policy
///
/// Malformed data is no `errno`. A syntax error is a @ref YS_CODE_ERROR token, and that token is part of the stream.
/// So is a wire that is not the wire format.
///
/// A host failure does set `errno`. ys_read_token() returns a negative @ref ys_status. `errno` is then the value the
/// callback set, or `ENOMEM`, or `ENODATA`.
///
/// The constructors and ys_write_token() set `errno` on failure. So do the closers ys_delete_token_source() and
/// ys_delete_token_sink(). `errno` is `EINVAL` for a bad argument and `ENOMEM` for insufficient memory. Otherwise it
/// is the value a failing callback set, and libyeast passes that value through untouched.
///
/// The closers still release what they hold whatever fails. They report which callback's close failed, and `errno`
/// holds what the first failure set. The remaining functions cannot fail and do not touch `errno`. libyeast requires
/// an allocator, reader or writer callback to set `errno` on failure. libyeast passes that value through rather than
/// overriding it.
///
/// The [design document](https://github.com/orenbenkiki/libyeast/blob/main/DESIGN.md) covers the implementation. The
/// [roadmap](https://github.com/orenbenkiki/libyeast/blob/main/PLAN.md) covers what libyeast still owes.

// Export control. Public symbols are marked YS_API. The build hides a symbol without it (the build compiles with
// -fvisibility=hidden). Define YS_STATIC when linking libyeast statically (the installed CMake and pkg-config targets
// do this for you).
#if defined(_WIN32) || defined(__CYGWIN__)
#define YS_EXPORT __declspec(dllexport)
#define YS_IMPORT __declspec(dllimport) // the consumer's side of that export.
#else
#define YS_EXPORT __attribute__((visibility("default")))
#define YS_IMPORT // an ELF consumer names an imported symbol the same way it names any other.
#endif

#if defined(YS_STATIC)
#define YS_API // a static link needs neither. The symbol sits in the consumer's objects.
#elif defined(YEAST_BUILDING)
#define YS_API YS_EXPORT
#else
#define YS_API YS_IMPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

/// The libyeast version as a static string. The form is "MAJOR.MINOR.PATCH". A NUL terminates it.
///
/// @return a pointer to the version string. The string is valid for the lifetime of the program. A caller must not
/// free it.
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

/// A source position. The components are `0`-based. `byte_offset` indexes raw bytes, and slices the input. The other
/// components count Unicode codepoints. That is what editors mean by line and column.
typedef struct ys_mark {
    size_t byte_offset; ///< Offset in bytes from the start of the input.
    size_t char_offset; ///< Offset in codepoints from the start of the input.
    size_t line;        ///< Line number.
    size_t column;      ///< Codepoint offset within the line.
} ys_mark;

/// The status an `int`-returning libyeast function reports. `YS_OK` is success. A negative value is a failure with
/// `errno` set. `YS_FAILED_ACTION` is the call failing on its own terms. The remaining values are a delegate failing
/// under the call, and that delegate is a byte transport or an allocator.
///
/// A function's documentation says which values it can return, and the set differs from function to function.
/// ys_read_token() reads a single source. It does not report `YS_FAILED_BOTH`. The closers close things rather than
/// act. They do not report `YS_FAILED_ACTION`.
typedef enum ys_status {
    YS_OK = 0,             ///< Success. The call filled a token, read a character back, or closed what it held.
    YS_FAILED_ACTION = -1, ///< The call could not proceed on its own terms. The input had nothing left to read, and
                           ///< `errno` is then `ENODATA`. Or the call had nothing to write or to read back, and
                           ///< `errno` is then `EINVAL`. A delegate is not at fault.
    YS_FAILED_STREAM = -2, ///< A byte transport failed. That is a reader or a writer callback. `errno` is what the
                           ///< callback set.
    YS_FAILED_MEMORY = -3, ///< An allocator refused, or the parse reached the cap. `errno` is `ENOMEM`.
    YS_FAILED_BOTH = -4    ///< A closer's stream and allocator both failed. `errno` is the stream's reason.
} ys_status;

/// Token classification, the yeast model. Zero-width `BEGIN_*` and `END_*` markers bracket productions. The remaining
/// codes classify a span of consumed input. A byte of the input falls under a single code.
///
/// The yeast wire format writes a token as a character and its text. That character mapping is the stable contract,
/// and this enum's order makes no such promise. `YS_CODE_TEXT` is `T` and `YS_CODE_META` is `t`.
/// `YS_CODE_INDICATOR` is `I`. A begin-and-end pair is an upper- and a lower-case letter, and so on.
/// `grammar/yeast-spec-1.2.yaml` lists them.
///
/// `YS_CODE_BEGIN_STREAM` and `YS_CODE_END_STREAM` bracket the whole stream. `YS_CODE_UNPARSED_BREAK` gives a skipped
/// line's break its own code. `YS_CODE_ERROR` is the wire's `!`.
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
    YS_CODE_ERROR,            ///< The document is malformed. @ref ys_token::text holds the message. A malformed wire
                              ///< read by ys_read_token() gets this code too. Such a wire is bad data, and so is a
                              ///< bad document. A host failure is not the data's fault, and yields no token and no
                              ///< code. ys_read_token() reports it as a return value. Out of memory and a failed
                              ///< reader are such failures.
    YS_CODE_UNPARSED_TEXT,    ///< Input the parser skipped after a malformed document. libyeast classifies that input
                              ///< rather than losing it. Like any token it stays within a line. A skipped line's
                              ///< break is a @ref YS_CODE_UNPARSED_BREAK.
    YS_CODE_UNPARSED_BREAK,   ///< The line break of skipped input. The break stays unparsed, as the content in front
                              ///< of it does. The break gets a code of its own, and nobody takes that break for a
                              ///< structural break the parser did not find.
    YS_CODE_UNPARSED_INVALID, ///< A run of bytes encoding no valid Unicode character. The run names no character the
                              ///< grammar could match. That holds whatever encoding the reader read the bytes in.
                              ///< @ref ys_token::text holds bytes rather than codepoints here. The run therefore
                              ///< gets a code of its own rather than @ref YS_CODE_UNPARSED_TEXT. On the wire an
                              ///< escape under this code writes the byte, and an escape under another code writes
                              ///< the codepoint. The text under this code must encode no character throughout. A
                              ///< byte of that text is a place where no character begins. The text under another
                              ///< code must encode characters throughout. ys_write_token() holds a writer to both
                              ///< rules. A parse does not emit this code yet, and `PLAN.md` holds the recovery
                              ///< milestone. The wire round-trips the code.
    YS_CODE_DETECTED          ///< libyeast reserves this code for its own detection tokens. `PLAN.md` holds the
                              ///< token-emission levels. A parse does not emit this code yet. The wire holds the
                              ///< code both ways.
} ys_code;

/// The character the yeast wire format writes for a code.
///
/// @ref YS_CODE_ERROR writes `!`, a malformed document. That is the error the wire reports, and its text is the
/// message.
///
/// @param code the token code.
/// @return its wire character, or `'\0'` where the wire writes nothing. A value that is not a code at all writes
/// nothing. A character a wire does hold is printable. `'\0'` is not among them. A line is NUL-terminated,
/// and a code written that way would read back as an empty line.
YS_API char ys_code_char(ys_code code);

/// The code that a yeast wire character writes. This is the inverse of ys_code_char(), as far as an inverse
/// exists.
///
/// `!` yields @ref YS_CODE_ERROR, the failure a wire reports.
///
/// @param character the wire character.
/// @param code the place for the code the character writes. This call leaves it untouched where the character
/// writes none.
/// @return @ref YS_OK with @p code filled, or @ref YS_FAILED_ACTION with `errno` `EINVAL` if @p character writes no
/// code.
YS_API int ys_code_of_char(char character, ys_code *code);

/// A single token produced by ys_read_token(). For a zero-width `BEGIN_*` or `END_*` marker, `start` equals `end` and
/// `text` is NULL. For a leaf token, `text` points at the matched bytes. The byte length is
/// `end.byte_offset - start.byte_offset`.
///
/// An error consumes nothing, and `start` equals `end` there too. Its `text` is the message. That message is not in
/// the input, and that text is not NULL. The input the error failed on comes back behind the error as
/// @ref YS_CODE_UNPARSED_TEXT tokens.
///
/// **No token spans a line.** A leaf token lies within a line, and `start.line` equals `end.line`. A line break is a
/// token of its own. The input skipped after a malformed document obeys this rule too. That input does not come
/// back as a token holding the whole run. A skipped line yields a @ref YS_CODE_UNPARSED_TEXT token for the content,
/// and a @ref YS_CODE_UNPARSED_BREAK for the break. The break stays unparsed as well, and gets a code of its own.
/// Calling the break a @ref YS_CODE_BREAK would claim a structure the parser did not find.
typedef struct ys_token {
    ys_code code;     ///< The token's classification.
    ys_mark start;    ///< Position of the token's first character.
    ys_mark end;      ///< Position just past the token's last character.
    const char *text; ///< The matched bytes for a leaf token, the message for an error, or NULL for a zero-width
                      ///< marker. A message is NUL-terminated. The matched bytes have no NUL terminator.
} ys_token;

/// A byte source for a stream token source (ys_new_yaml_stream_parser() or ys_new_yeast_stream_reader()). It abstracts
/// over any stream, and over more than files and file descriptors. libyeast ships adapters for common sources
/// separately.
typedef struct ys_bytes_reader {
    /// Read up to `size` bytes into `buffer`. Return the number of bytes read, or `0` at end of input, or a negative
    /// value on error. On error it must set `errno`. libyeast passes that value through untouched. The value survives
    /// alongside the @ref YS_FAILED_STREAM that ys_read_token() then returns. The `context` argument is @ref context.
    ptrdiff_t (*read)(void *context, char *buffer, size_t size);
    /// Release @ref context where it needs releasing. Return `0`, or `-1` with `errno` set. That is `close(2)`'s
    /// contract, as @ref read is `read(2)`'s. May be NULL. Whatever the caller hands the reader to calls this exactly
    /// once. That is ys_delete_token_source() for a reader a constructor took, and the constructor itself for a
    /// reader it refused. The caller does not call this either way. The caller has handed the reader over.
    ///
    /// A constructor that is already failing discards the result. The constructor has a reason to report of its own,
    /// and `errno` still holds that reason afterwards.
    int (*close)(void *context);
    void *context; ///< Opaque state passed to @ref read and @ref close.
} ys_bytes_reader;

/// Whether a reader adapter owns the underlying resource or merely borrows it. An owner closes the resource.
typedef enum ys_ownership {
    YS_BORROW, ///< Leave the underlying descriptor or stream open when ys_delete_token_source() runs.
    YS_OWN     ///< Close the underlying descriptor or stream when ys_delete_token_source() runs.
} ys_ownership;

/// Build a reader that pulls from a file descriptor.
///
/// @param fd the file descriptor to read from.
/// @param ownership @ref YS_OWN to close `fd` when ys_delete_token_source() runs, or @ref YS_BORROW to leave it open.
/// @return a reader to hand to a stream token source.
YS_API ys_bytes_reader ys_fd_reader(int fd, ys_ownership ownership);

/// Build a reader that pulls from a `FILE *` stream.
///
/// @param file the stream to read from.
/// @param ownership @ref YS_OWN to `fclose(file)` when ys_delete_token_source() runs, or @ref YS_BORROW to leave it
/// open.
/// @return a reader to hand to a stream token source.
YS_API ys_bytes_reader ys_fp_reader(FILE *file, ys_ownership ownership);

/// A byte sink. It mirrors @ref ys_bytes_reader. The abstraction covers any stream, and covers more than files and
/// file descriptors.
typedef struct ys_bytes_writer {
    /// Write `size` bytes from `buffer`. Return the number written, or a negative value on error. On error it must
    /// set `errno`. ys_write_token() passes that value through. The caller reads that value after ys_write_token()
    /// returns @ref YS_FAILED_STREAM. The `context` argument is @ref context.
    ptrdiff_t (*write)(void *context, const char *buffer, size_t size);
    /// Release @ref context where it needs releasing. Return `0`, or `-1` with `errno` set. That is `close(2)`'s
    /// contract, as @ref write is `write(2)`'s. May be NULL. ys_delete_token_sink() calls this and reports what the
    /// callback said. A buffered write does not reach the disk until the flush a close performs. A close is where a
    /// full disk turns up, long after the ys_write_token() calls have returned @ref YS_OK.
    int (*close)(void *context);
    void *context; ///< Opaque state passed to @ref write and @ref close.
} ys_bytes_writer;

/// Build a writer that pushes to a file descriptor.
///
/// @param fd the file descriptor to write to.
/// @param ownership @ref YS_OWN to close `fd` when ys_delete_token_sink() runs, or @ref YS_BORROW to leave it open.
/// @return a writer to hand to ys_new_yeast_stream_writer().
YS_API ys_bytes_writer ys_fd_writer(int fd, ys_ownership ownership);

/// Build a writer that pushes to a `FILE *` stream.
///
/// @param file the stream to write to.
/// @param ownership @ref YS_OWN to `fclose(file)` when ys_delete_token_sink() runs, or @ref YS_BORROW to leave it
/// open.
/// @return a writer to hand to ys_new_yeast_stream_writer().
YS_API ys_bytes_writer ys_fp_writer(FILE *file, ys_ownership ownership);

/// A custom allocator. A NULL callback falls back to its C counterpart. A zeroed struct uses `malloc`, `realloc` and
/// `free`. Setting some of the callbacks mixes custom and standard behavior.
///
/// `allocate` and `reallocate` must set `errno` when they return NULL, as `malloc` and `realloc` do. libyeast passes
/// that value through the constructor that was allocating. The caller reads that value after the constructor returns
/// NULL. A debug build asserts a failing allocator set it.
typedef struct ys_allocator {
    void *(*allocate)(void *context, size_t size);                  ///< Allocate `size` bytes, or NULL (set `errno`).
    void *(*reallocate)(void *context, void *pointer, size_t size); ///< Resize `pointer` to `size` bytes, or NULL.
    void (*deallocate)(void *context, void *pointer);               ///< Free `pointer`.
    /// Release @ref context where it needs releasing. Return `0`, or `-1` with `errno` set. That is `close(2)`'s
    /// contract, and the reader's close and the writer's close follow the same contract. May be NULL, and that is the
    /// common case. An allocator that is no more than a set of functions has nothing to release. Whatever the caller
    /// built with it calls this once. That call comes after the last @ref deallocate, as the caller frees the thing.
    /// An arena or a pool can then go down with what the caller built out of it.
    int (*close)(void *context);
    void *context; ///< Opaque state passed to the callbacks.
} ys_allocator;

/// The policy the parser follows for the input it could not parse.
///
/// A policy gives up less of the input than the policy before it, and loses no byte. The parser hands skipped input
/// back as @ref YS_CODE_UNPARSED_TEXT content and @ref YS_CODE_UNPARSED_BREAK breaks. A caller sees what the parser
/// passed over. A policy with no place to resume at behaves as the policy before it.
typedef enum ys_resume {
    /// The error ends the parse. The rest of the input comes back as @ref YS_CODE_UNPARSED_TEXT tokens. The parser
    /// parses none of that input, and loses none of it silently. This is the default.
    YS_RESUME_NONE,
    /// Skip to the next document and continue parsing there. A malformed document in a stream then does not cost the
    /// caller the documents behind it. The lines skipped to reach that document come back as @ref
    /// YS_CODE_UNPARSED_TEXT tokens.
    YS_RESUME_DOCUMENT,
    /// Skip to the next line no more indented than the entry that failed. Continue parsing *inside* the document. A
    /// malformed entry then does not cost the caller the rest of its container. The recovered entry is a sibling of
    /// what failed rather than a child of it. The parser closes the markers of the abandoned entry in front of that
    /// entry. A failure nothing encloses has no indentation to bound it by, and this is then @ref YS_RESUME_DOCUMENT.
    YS_RESUME_INDENT
} ys_resume;

/// Parser construction options. A zeroed struct selects the defaults.
typedef struct ys_options {
    ys_allocator allocator; ///< Custom allocator. A zeroed allocator selects `malloc`, `realloc` and `free`.
    ys_resume resume;       ///< The policy for a malformed document. The default is @ref YS_RESUME_NONE.
    /// Cap on the memory the parser allocates for itself. The unit is bytes, and `0` means unlimited. Reaching the cap
    /// is a @ref YS_FAILED_MEMORY return, and it ends the parse for good. There is no way to raise the cap and go
    /// on. To parse the input after such a failure, build a new parser with a larger cap and parse the input again
    /// from its start. The cap is not an allocation failure, and @ref allocator does not see it.
    ///
    /// The cap covers what grows, and covers those things together. The input a stream parser buffers is the first. A
    /// single enormous token fills that buffer, and so does a run of tokens whose codes the parser has still to
    /// decide. The empty lines that open a block scalar are content where a content line follows. The parser chomps
    /// those lines away where no content line follows. The parser holds such a line back until it finds out which.
    ///
    /// The tokens themselves are the second. The parser holds them back with the input. The parser's stack is the
    /// third. Deep nesting grows that stack, and the size of the input places no bound on it. YAML bounds no part of
    /// that. YAML bounds lookahead for an implicit key at `1024` characters. An error's message is a static string,
    /// and the cap does not cover it.
    ///
    /// The cap does not count the input of a string parser. That input belongs to the caller, and libyeast does not
    /// copy it. A document of any size parses under a small cap. The held-back tokens and the stack are what the
    /// parser owns.
    ///
    /// The cap is a convenience rather than the last word. A custom @ref allocator that refuses beyond some limit caps
    /// the parser just as well. The cap and the allocator are independent, and neither knows the other exists.
    size_t max_bytes;
} ys_options;

/// An opaque leak-checking allocator. It wraps `malloc`, `realloc` and `free`, and counts live allocations. A test or
/// a consumer can then confirm the count came back to `0`. Its overhead over plain `malloc` and `free` is a single
/// counter. It is cheap enough to leave enabled in a release build if desired. It does not detect memory corruption.
/// Use a sanitizer for that.
typedef struct ys_counting_allocator ys_counting_allocator;

/// Create a counting allocator.
///
/// @return a new counting allocator (free it with ys_delete_counting_allocator()), or NULL on allocation failure, with
/// `errno` set to `ENOMEM` by the underlying `malloc`.
YS_API ys_counting_allocator *ys_new_counting_allocator(void);

/// The allocator functions to place in @ref ys_options::allocator so allocations route through the counter. Its
/// @ref ys_allocator::close is ys_close_counting_allocator(). A single source or sink built on the counter checks
/// itself at deletion time, and the caller has nothing to remember.
///
/// Set `allocator.close` to NULL to hand a counter to more than a single object. Otherwise freeing the first object
/// reports the still-live buffers of the objects behind it as a leak. Call ys_close_counting_allocator() yourself once
/// the last object is gone. That is the moment the count means anything.
///
/// @param counter the counting allocator.
/// @return a ys_allocator backed by @p counter.
YS_API ys_allocator ys_counting_allocator_functions(ys_counting_allocator *counter);

/// Check that nothing allocated through the counter is still live. This is the @ref ys_allocator::close
/// ys_counting_allocator_functions() installs, and a caller can call it directly with the same meaning. A leak is
/// memory the counter still holds. The call reports a leak as an allocator failure rather than asserting. The caller
/// decides what a leak means.
///
/// @param counter the counting allocator, as a `void *`. This is then a @ref ys_allocator::close.
/// @return the value `0` where nothing is live, or `-1` with `errno` set to `ENOMEM` where anything is live.
YS_API int ys_close_counting_allocator(void *counter);

/// The count of allocations made through the counter that are still live. The count is positive while a source or a
/// sink is in use. Check that the count is back to `0` after you free the buffers, to confirm there was no leak.
///
/// @param counter the counting allocator.
/// @return the live buffer count.
YS_API size_t ys_counting_allocator_live_buffers(const ys_counting_allocator *counter);

/// Free a counting allocator. Does not free anything allocated through it.
///
/// @param counter the counting allocator to free. It may be NULL, in which case this is a no-op.
YS_API void ys_delete_counting_allocator(ys_counting_allocator *counter);

/// An opaque source of yeast tokens. It is YAML parsed from memory by ys_new_yaml_memory_parser(), or from a stream by
/// ys_new_yaml_stream_parser(). It is also a yeast wire replayed from a stream by ys_new_yeast_stream_reader().
///
/// A caller pulls tokens from a source with ys_read_token(). That holds whichever constructor made the source. The
/// caller deletes the source with ys_delete_token_source(). Code over tokens runs unchanged whether a parser or a
/// replay made them.
typedef struct ys_token_source ys_token_source;

/// Create a source that parses YAML from an in-memory buffer. The buffer must outlive the source. A token's text
/// points directly into that buffer, and ys_are_tokens_stable() returns true.
///
/// @param input the bytes to parse. They must remain valid until ys_delete_token_source(). NULL is fine with a
/// @p length of `0`, and that names an empty input.
/// @param length the count of bytes in @p input.
/// @param options construction options, or NULL for defaults.
/// @return a new source, or NULL on failure. `errno` is then set. It is `EINVAL` where @p input is NULL and @p length
/// is other than `0`. Otherwise it is `ENOMEM`. Either @ref ys_options::max_bytes was smaller than a source, or the
/// allocator refused and set the value.
YS_API ys_token_source *ys_new_yaml_memory_parser(const char *input, size_t length, const ys_options *options);

/// Create a source that parses YAML pulled from @p reader on demand. A token's text points into an internal buffer.
/// That text is valid until the next ys_read_token() call. ys_are_tokens_stable() returns false.
///
/// **The source takes @p reader.** That holds even where the constructor fails. A failing constructor closes @p reader
/// before returning NULL. It calls the `close` callback exactly as ys_delete_token_source() would have. A @ref YS_OWN
/// reader's file descriptor therefore does not leak. The caller must not close the reader. The caller has nothing left
/// to close the reader with, and a second close would be a double close.
///
/// @param reader the byte source. Its `read` callback must be non-NULL. The source owns it from this call onwards.
/// @param options construction options, or NULL for defaults.
/// @return a new source, or NULL on failure. A failure closes @p reader. `errno` is `EINVAL` if @p reader has no
/// `read` callback, and `ENOMEM` otherwise.
YS_API ys_token_source *ys_new_yaml_stream_parser(ys_bytes_reader reader, const ys_options *options);

/// Create a source that replays a yeast wire pulled from @p reader on demand. This is the inverse of a yeast writer. A
/// token's text points into an internal buffer. That text is valid until the next ys_read_token() call.
/// ys_are_tokens_stable() returns false.
///
/// **The source takes @p reader.** That holds even where the constructor fails. A failing constructor closes @p reader
/// before returning NULL, exactly as ys_delete_token_source() would have.
///
/// @param reader the byte source. Its `read` callback must be non-NULL. The source owns it from this call onwards.
/// @param options construction options, or NULL for defaults. The source consults @ref ys_options::allocator and
/// @ref ys_options::max_bytes.
/// @return a new source, or NULL on failure. A failure closes @p reader. `errno` is `EINVAL` if @p reader has no
/// `read` callback, and `ENOMEM` otherwise.
YS_API ys_token_source *ys_new_yeast_stream_reader(ys_bytes_reader reader, const ys_options *options);

/// Report whether token text pointers stay valid for the source's whole lifetime.
///
/// @param source the source to query.
/// @return true for a memory parser. Its text points into the caller's buffer, and an error's message is a static
/// string. It returns false for a stream parser or a wire replay. Their text points into a buffer the next
/// ys_read_token() call may overwrite.
YS_API bool ys_are_tokens_stable(const ys_token_source *source);

/// Read the next token into @p token. A byte of a parsed input falls under a token. That holds for the ill-formed
/// bytes as well.
///
/// A **malformed input** is a syntax error in the YAML, or a wire that is not the wire format. It fills @p token with
/// a @ref YS_CODE_ERROR whose @ref ys_token::text is the message, and returns `0` like any other token.
///
/// @ref ys_options::resume decides what follows for a parser. By default the parse ends and the rest of the input
/// comes back as @ref YS_CODE_UNPARSED_TEXT. libyeast loses nothing silently. @ref YS_RESUME_DOCUMENT continues at
/// the next document. @ref YS_RESUME_INDENT continues at the next line no more indented than the entry that failed.
///
/// A wire replay has no input left after the error. The message names what the parser expected rather than what it
/// found. The first @ref YS_CODE_UNPARSED_TEXT token behind the error begins at exactly the byte that failed.
///
/// A **host failure** is not a token but the return value. The token model stays about the data, and not about the
/// machine running on it. @ref YS_FAILED_STREAM is the reader failing. @ref YS_FAILED_MEMORY is the allocator
/// refusing. It is also the parse reaching @ref ys_options::max_bytes. Both hold the callback's `errno`.
/// @ref YS_FAILED_ACTION comes with `errno` `ENODATA` once a caller reads past the end of the stream.
///
/// A resource failure ends the source for good. The stream has no `end-stream` to close the `begin-stream`. That
/// missing close is the sign the parse did not finish. A later call returns @ref YS_FAILED_ACTION. There is no way to
/// raise the cap and go on, and that is deliberate. A resumable allocation would have to survive the point it ran
/// out at. The mistake is in the caller's sizing rather than in the data.
///
/// To read the input after such a failure, build a new source with a larger @ref ys_options::max_bytes. An allocator
/// that can satisfy it does as well. Then read the input again from its start.
///
/// The caller does not free @ref ys_token::text. A single rule covers a token whatever its code. The text stays valid
/// for as long as ys_are_tokens_stable() promises. An error's message is a static string, and the rule covers it too.
///
/// @param source the source to advance.
/// @param token the place for the token read. A negative return leaves it untouched.
/// @return @ref YS_OK with @p token filled, or @ref YS_FAILED_STREAM, @ref YS_FAILED_MEMORY or @ref YS_FAILED_ACTION.
YS_API int ys_read_token(ys_token_source *source, ys_token *token);

/// Delete a token source and what it owns. The call says whether the closeable things closed cleanly. A stream parser
/// or a wire replay holds a reader, and that reader is the first, where its @ref ys_bytes_reader::close is not NULL.
/// The allocator is the second, where @ref ys_allocator::close is not NULL. The call closes both and frees the memory.
/// That holds whichever of them fails. A close that fails leaks nothing.
///
/// @param source the source to delete. It may be NULL, in which case this is a no-op returning @ref YS_OK.
/// @return @ref YS_OK if the closes succeeded. It is @ref YS_FAILED_STREAM if the reader's close failed, and
/// @ref YS_FAILED_MEMORY if the allocator's did. It is @ref YS_FAILED_BOTH if both did. `errno` holds what the first
/// failure set, and what the reader set where both failed. That is the documented limit of what a single `errno` can
/// say. A caller wanting both records them in the `context` of a callback.
YS_API int ys_delete_token_source(ys_token_source *source);

/// An opaque sink of yeast tokens. It mirrors @ref ys_token_source. ys_new_yeast_stream_writer() makes a sink that
/// serializes tokens to a yeast wire. ys_write_token() feeds a sink, and ys_delete_token_sink() releases a sink. Code
/// writing tokens does not know or care where they go.
typedef struct ys_token_sink ys_token_sink;

/// Create a sink that serializes tokens to a yeast wire pushed to @p writer. This is the inverse of
/// ys_new_yeast_stream_reader().
///
/// **The sink takes @p writer.** That holds even where the constructor fails. A failing constructor closes @p writer
/// before returning NULL, exactly as ys_delete_token_sink() would have.
///
/// @param writer the byte sink. Its `write` callback must be non-NULL. The sink owns it from this call onwards.
/// @param options construction options, or NULL for defaults. The sink consults @ref ys_options::allocator and
/// @ref ys_options::max_bytes.
/// @return a new sink, or NULL on failure. A failure closes @p writer. `errno` is `EINVAL` if @p writer has no
/// `write` callback, and `ENOMEM` otherwise.
YS_API ys_token_sink *ys_new_yeast_stream_writer(ys_bytes_writer writer, const ys_options *options);

/// Create a sink that emits tokens as YAML pushed to @p writer. This is the inverse of a YAML parser. The token
/// stream is byte-complete, and the emitter writes the bytes a token spans. The emitter reconstructs the input, and a
/// structure marker spans nothing.
///
/// It renders rather than judges. A @ref YS_CODE_ERROR is not a token the emitter can emit. The text of such a token
/// is a message rather than input. ys_write_token() refuses such a token and returns @ref YS_FAILED_ACTION with
/// `EINVAL`. A caller wanting to drop comments or unparsed input filters the token stream above the emitter. The
/// emitter writes what the caller hands it.
///
/// **The sink takes @p writer.** That holds even where the constructor fails. A failing constructor closes @p writer
/// before returning NULL.
///
/// @param writer the byte sink. Its `write` callback must be non-NULL. The sink owns it from this call onwards.
/// @param options construction options, or NULL for defaults. The sink consults @ref ys_options::allocator and
/// @ref ys_options::max_bytes.
/// @return a new sink, or NULL on failure. A failure closes @p writer. `errno` is `EINVAL` if @p writer has no
/// `write` callback, and `ENOMEM` otherwise.
YS_API ys_token_sink *ys_new_yaml_stream_emitter(ys_bytes_writer writer, const ys_options *options);

/// Write a token to a sink. For a yeast writer, a token is a pair of lines. The first line is its position. The second
/// line holds the code character and then the text.
///
/// @code
/// # B: 12, C: 12, L: 1, c: 4
/// I-
/// @endcode
///
/// The writer escapes the text. It writes a printable ASCII character other than a backslash as itself. It writes any
/// other character as `\xXX`, `\uXXXX` or `\UXXXXXXXX`. A zero-width marker has no text, and its second line holds
/// only the code character.
///
/// @param sink the sink to write to.
/// @param token the token to write.
/// @return @ref YS_OK on success. It is @ref YS_FAILED_STREAM with `errno` what the byte transport's `write` callback
/// set. It is @ref YS_FAILED_ACTION with `errno` `EINVAL` where the writer cannot write the token at all. That covers
/// a code the wire writes nothing for. It covers text whose bytes do not match what @p token's code says. It also
/// covers a @ref YS_CODE_ERROR handed to an emitter.
YS_API int ys_write_token(ys_token_sink *sink, ys_token token);

/// Delete a token sink and what it owns. The call says whether the closeable things closed cleanly. The byte transport
/// under the sink is the first, where its @ref ys_bytes_writer::close is not NULL. The sink's allocator is the second.
///
/// The transport's close is where a buffered write finally reaches its destination. A stream written without a fault
/// can still fail at that last moment. A full disk or a broken pipe turns up after the last ys_write_token() returned
/// @ref YS_OK. The call closes both and frees the memory. That holds whichever of them fails.
///
/// @param sink the sink to delete. It may be NULL, in which case this is a no-op returning @ref YS_OK.
/// @return @ref YS_OK if the closes succeeded. It is @ref YS_FAILED_STREAM if the transport's close failed, and
/// @ref YS_FAILED_MEMORY if the allocator's did. It is @ref YS_FAILED_BOTH if both did. `errno` holds what the first
/// failure set, and what the transport set where both failed.
YS_API int ys_delete_token_sink(ys_token_sink *sink);

#ifdef __cplusplus
}
#endif

#endif // YEAST_H
