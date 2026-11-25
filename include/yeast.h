// SPDX-License-Identifier: MIT
/// @file yeast.h
/// @brief Public API for the libyeast C YAML parser.

#ifndef YEAST_H
#define YEAST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>

/// @mainpage libyeast
///
/// libyeast is a fast, single-pass, pull-driven YAML 1.2 parser in C. libyeast parses a document into a stream of
/// @ref ys_token values, and a caller pulls a token at a time. That is the yeast token model, and
/// @ref ys_code holds its codes.
///
/// @note The grammar-derived core does not exist. @ref ys_read_token() on a parser fills a
/// @ref YS_CODE_ERROR token reading "not implemented". The next call fills the same token again. libyeast implements
/// the rest of the surface below.
///
/// @section reading Reading tokens
///
/// Make a @ref ys_token_source. Pull @ref ys_token values from it with @ref ys_read_token() until the source ends.
/// Then delete the source with @ref ys_delete_token_source(). A source comes from a constructor.
///
/// - @ref ys_new_yaml_memory_parser() parses YAML from a caller-owned buffer. libyeast copies nothing. A token's text
///   points into that buffer and stays valid as long as the buffer does. @ref ys_are_tokens_stable() returns true.
/// - @ref ys_new_yaml_stream_parser() parses YAML pulled from a @ref ys_bytes_reader on demand. Build a reader with
///   @ref ys_fd_reader() for a file descriptor or @ref ys_fp_reader() for a `FILE *`. @ref ys_fd_reader() and @ref
///   ys_fp_reader() take a @ref ys_ownership. That option says whether @ref ys_delete_token_source() closes the
///   underlying resource. For any other byte source, fill in a @ref ys_bytes_reader of your own.
/// - @ref ys_new_yeast_stream_reader() replays a yeast wire (below) pulled from a @ref ys_bytes_reader. That is the
///   inverse of writing a wire.
///
/// The token text of a stream parser and of a wire replay is valid until the next @ref ys_read_token() call.
/// @ref ys_are_tokens_stable() returns false for a stream parser and for a wire replay. Reading is the same for both
/// sources.
///
/// @code
/// ys_token_source *source = ys_new_yaml_memory_parser(input, length, NULL);
/// ys_token token;
/// while (ys_read_token(source, &token) == YS_OK) {
///     // use token.code, token.start, token.end and token.text
///     // a clean parse ends on YS_CODE_END_STREAM
/// }
/// ys_delete_token_source(source); // the loop ended on YS_FAILED_ACTION, or YS_FAILED_STREAM/MEMORY (errno set)
/// @endcode
///
/// @section wire The yeast wire format
///
/// libyeast can serialize a token stream as the yeast wire format. The wire holds a pair of lines for a token. The
/// first line is the token's position. The second line holds the token's code character and escaped text. A caller
/// pipes a stream between tools over the wire, stores a stream, or compares a stream against the tokens another parser
/// produced.
///
/// @ref ys_new_yeast_stream_writer() makes a @ref ys_token_sink over a @ref ys_bytes_writer. Build a writer with @ref
/// ys_fd_writer() or @ref ys_fp_writer(). @ref ys_fd_writer() and @ref ys_fp_writer() mirror the reader adapters. @ref
/// ys_write_token() feeds the sink. The list of constructors above names the source that reads a wire back.
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
/// Malformed data sets no `errno`. A syntax error is a @ref YS_CODE_ERROR token, and that token is part of the stream.
/// So is a wire that is not the wire format.
///
/// A host failure does set `errno`. @ref ys_read_token() returns a negative @ref ys_status. `errno` is then the value
/// the callback set, or `ENOMEM`, or `ENODATA`. A wire replay's first read past the end of its wire sets no `errno`.
///
/// The constructors and @ref ys_write_token() set `errno` on failure. So do the closers @ref ys_delete_token_source()
/// and @ref ys_delete_token_sink(). `errno` is `EINVAL` for a bad argument and `ENOMEM` for insufficient memory.
/// Otherwise it is the value a failing callback set.
///
/// The closers still release what they hold, even where a close fails. They report which callback's close failed, and
/// `errno` holds what the first failure set. libyeast
/// requires an allocator, reader or writer callback to set `errno` on failure. libyeast passes that value through
/// rather than overriding it.
///
/// See the [`DESIGN.md`](https://github.com/orenbenkiki/libyeast/blob/main/DESIGN.md) document for implementation
/// details.

// Export control. The header marks a public symbol `YS_API`. `CMakeLists.txt` sets `C_VISIBILITY_PRESET` to `hidden`.
// The build hides a symbol without `YS_API`. Define `YS_STATIC` when linking libyeast statically. The installed CMake
// target for the static library defines it for you. `cmake/yeast.pc.in` defines it for `pkg-config --static`.
#if defined(_WIN32) || defined(__CYGWIN__)
#define YS_EXPORT __declspec(dllexport)
#define YS_IMPORT __declspec(dllimport) // `YS_IMPORT` marks the consumer's side of a symbol libyeast exports.
#else
#define YS_EXPORT __attribute__((visibility("default")))
#define YS_IMPORT // a consumer off Windows and Cygwin names an imported symbol the same way it names any other.
#endif

#if defined(YS_STATIC)
#define YS_API // a static link takes neither `YS_EXPORT` nor `YS_IMPORT`.
#elif defined(YEAST_BUILDING)
#define YS_API YS_EXPORT
#else
#define YS_API YS_IMPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

/// The libyeast version as a static string. The string holds the major, the minor and the patch, with a dot between
/// them. A NUL terminates it.
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

/// A source position. The components are `0`-based. `byte_offset` indexes raw bytes, and slices the input.
/// `char_offset` and `column` count Unicode codepoints. `line` counts line breaks.
typedef struct ys_mark {
    size_t byte_offset; ///< Offset in bytes from the start of the input.
    size_t char_offset; ///< Offset in codepoints from the start of the input.
    size_t line;        ///< Line number.
    size_t column;      ///< Codepoint offset within the line.
} ys_mark;

/// The status an `int`-returning libyeast function reports. `YS_OK` is success. A negative value is a failure. A
/// failure sets `errno`, except where a wire source first reaches its end. `YS_FAILED_ACTION` is the call failing by
/// its own standard. A remaining value names a delegate failing under the call. That delegate is a byte transport or an
/// allocator.
///
/// A function's documentation says which values it can return, and the set differs from function to function.
/// @ref ys_read_token() reads a single source. It does not report `YS_FAILED_BOTH`. The closers close things rather
/// than act. They do not report `YS_FAILED_ACTION`.
typedef enum ys_status {
    YS_OK = 0,             ///< Success. A read filled a token, or a write wrote one. @ref ys_code_of_char() filled a
                           ///< code. A close closed what it held.
    YS_FAILED_ACTION = -1, ///< The call could not proceed by its own standard. The input had nothing left to read, or a
                           ///< failure had ended the source. A read past the end sets `errno` to `ENODATA`. A read
                           ///< after a failure sets `ENODATA` as well. A wire source's first read at its end sets no
                           ///< `errno`. @ref ys_write_token() sets `errno` to `EINVAL` for a token it cannot write.
                           ///< @ref ys_code_of_char() sets `EINVAL` for a character that writes no code. A delegate is
                           ///< not at fault.
    YS_FAILED_STREAM = -2, ///< A byte transport failed. That is a reader, a writer or a close callback. `errno` holds
                           ///< the value the callback set.
    YS_FAILED_MEMORY = -3, ///< An allocator refused, or a parse or a wire replay reached the cap. A reached cap sets
                           ///< `errno` to `ENOMEM`. A failing allocator close leaves the `errno` that close set.
    YS_FAILED_BOTH = -4    ///< A closer's stream and allocator both failed. `errno` is the stream's reason.
} ys_status;

/// A `ys_code` classifies a token under the yeast model. Zero-width `BEGIN_*` and `END_*` markers bracket productions.
/// The remaining codes other than `YS_CODE_ERROR` and `YS_CODE_DETECTED` classify a span of consumed input. An error's
/// text is a message, and the error spans none of the input.
///
/// The yeast wire format writes a token as a character and its text. That character mapping is the stable contract,
/// and libyeast makes no such promise about this enum's order. `YS_CODE_TEXT` is `T` and `YS_CODE_META` is `t`.
/// `YS_CODE_INDICATOR` is `I`. A begin-and-end pair is an upper- and a lower-case letter. @ref ys_code_char() gives
/// the letter of a code.
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
    YS_CODE_BEGIN_PAIR,       ///< Start of a mapping pair of a key and a value.
    YS_CODE_END_PAIR,         ///< End of a mapping pair of a key and a value.
    YS_CODE_BEGIN_NODE,       ///< Start of a node.
    YS_CODE_END_NODE,         ///< End of a node.
    YS_CODE_BEGIN_DOCUMENT,   ///< Start of a document.
    YS_CODE_END_DOCUMENT,     ///< End of a document.
    YS_CODE_BEGIN_STREAM,     ///< Start of the token stream.
    YS_CODE_END_STREAM,       ///< End of the token stream.
    YS_CODE_ERROR,            ///< The document is malformed. @ref ys_token::text holds the message. A malformed wire
                   ///< read by @ref ys_read_token() gets this code too. A host failure yields no token and no
                   ///< code. @ref ys_read_token() reports such a failure as a return value. Out of memory and a
                   ///< failed reader are such failures.
    YS_CODE_UNPARSED_TEXT,    ///< Names input the parser skipped after a malformed document.
    YS_CODE_UNPARSED_BREAK,   ///< Names the line break of skipped input.
    YS_CODE_UNPARSED_INVALID, ///< A run of bytes encoding no valid Unicode character. @ref ys_token::text holds bytes
                              ///< rather than codepoints here. The run therefore gets a code of its own rather than
                              ///< @ref YS_CODE_UNPARSED_TEXT. On the wire an escape under this code writes the byte,
                              ///< and an escape under another code writes the codepoint. The text under this code must
                              ///< encode no character throughout. The text under another code must encode characters
                              ///< throughout. @ref ys_write_token() holds a writer to both rules.
    YS_CODE_DETECTED          ///< Names libyeast's own detection tokens.
} ys_code;

/// The character the yeast wire format writes for a code.
///
/// The line a @ref YS_CODE_ERROR writes holds the error message as its text.
///
/// @param code the token code.
/// @return its wire character, or `'\0'` where the wire writes nothing. A value that is not a code at all writes
/// nothing. A wire character is printable, and no code writes `'\0'`.
YS_API char ys_code_char(ys_code code);

/// The code that a yeast wire character writes. This is the inverse of @ref ys_code_char(), as far as an inverse
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

/// A single token produced by @ref ys_read_token(). For a zero-width `BEGIN_*` or `END_*` marker, `start` equals `end`
/// and `text` is NULL. For a leaf token, `text` points at the matched bytes. The byte length is `end.byte_offset -
/// start.byte_offset`.
///
/// An error consumes nothing, and `start` equals `end` there too. Its `text` is the message. That message is not in
/// the input, and that text is not NULL.
typedef struct ys_token {
    ys_code code;     ///< The token's classification.
    ys_mark start;    ///< Position of the token's first character.
    ys_mark end;      ///< Position just past the token's last character.
    const char *text; ///< The token's text. The struct's description says which form the text takes. A message is
                      ///< NUL-terminated. A message a wire replay reads is NUL-terminated too. The matched bytes are a
                      ///< span, and a caller takes their length from the marks rather than from a NUL terminator.
} ys_token;

/// A byte source for a stream token source (@ref ys_new_yaml_stream_parser() or @ref ys_new_yeast_stream_reader()). It
/// abstracts over a stream rather than over a file or a file descriptor. @ref ys_fd_reader() adapts a file descriptor,
/// and @ref ys_fp_reader() adapts a `FILE *`.
typedef struct ys_bytes_reader {
    /// Read up to `size` bytes into `buffer`. Return the number of bytes read, or `0` at end of input, or a negative
    /// value on error. On error it must set `errno`. libyeast leaves that value untouched. The value survives
    /// alongside the @ref YS_FAILED_STREAM that @ref ys_read_token() then returns. The `context` argument is @ref
    /// context.
    ptrdiff_t (*read)(void *context, char *buffer, size_t size);
    /// Release @ref context where it needs releasing. Return `0`, or `-1` with `errno` set. May be NULL. The object the
    /// caller hands the reader to calls this exactly once. That is @ref ys_delete_token_source() for a reader a
    /// constructor took. The constructor itself calls this for a reader it refused. The caller does not call this
    /// either way. The caller has handed the reader over.
    ///
    /// A constructor that is already failing discards the result. The constructor has a reason to report of its own,
    /// and `errno` still holds that reason afterwards.
    int (*close)(void *context);
    void *context; ///< Opaque state passed to @ref read and @ref close.
} ys_bytes_reader;

/// Whether a reader or writer adapter owns the underlying resource or merely borrows it. An owner closes the resource.
typedef enum ys_ownership {
    YS_BORROW, ///< Leave the underlying descriptor or stream open when @ref ys_delete_token_source() runs.
    YS_OWN     ///< Close the underlying descriptor or stream when @ref ys_delete_token_source() or @ref
               ///< ys_delete_token_sink() runs.
} ys_ownership;

/// Build a reader that pulls from a file descriptor.
///
/// @param fd the file descriptor to read from.
/// @param ownership @ref YS_OWN to close `fd` when @ref ys_delete_token_source() runs, or @ref YS_BORROW to leave it
/// open.
/// @return a reader to hand to a stream token source.
YS_API ys_bytes_reader ys_fd_reader(int fd, ys_ownership ownership);

/// Build a reader that pulls from a `FILE *` stream.
///
/// @param file the stream to read from.
/// @param ownership @ref YS_OWN to `fclose(file)` when @ref ys_delete_token_source() runs, or @ref YS_BORROW to leave
/// it open.
/// @return a reader to hand to a stream token source.
YS_API ys_bytes_reader ys_fp_reader(FILE *file, ys_ownership ownership);

/// A byte sink. It mirrors @ref ys_bytes_reader.
typedef struct ys_bytes_writer {
    /// Write `size` bytes from `buffer`. Return the number written, or a negative value on error. On error it must
    /// set `errno`. @ref ys_write_token() turns a negative return value into @ref YS_FAILED_STREAM. The caller reads
    /// `errno` after @ref ys_write_token() returns @ref YS_FAILED_STREAM. The `context` argument is @ref context.
    ptrdiff_t (*write)(void *context, const char *buffer, size_t size);
    /// Release @ref context where it needs releasing. Return `0`, or `-1` with `errno` set. May be NULL. @ref
    /// ys_delete_token_sink() calls this and reports what the callback said. A buffered write does not reach the disk
    /// until the [flush](https://pubs.opengroup.org/onlinepubs/9799919799/functions/fclose.html) a close performs. A
    /// close is where a full disk turns up, long after the @ref ys_write_token() calls have returned @ref YS_OK.
    int (*close)(void *context);
    void *context; ///< Opaque state passed to @ref write and @ref close.
} ys_bytes_writer;

/// Build a writer that pushes to a file descriptor.
///
/// @param fd the file descriptor to write to.
/// @param ownership @ref YS_OWN to close `fd` when @ref ys_delete_token_sink() runs, or @ref YS_BORROW to leave it
/// open.
/// @return a writer to hand to @ref ys_new_yeast_stream_writer().
YS_API ys_bytes_writer ys_fd_writer(int fd, ys_ownership ownership);

/// Build a writer that pushes to a `FILE *` stream.
///
/// @param file the stream to write to.
/// @param ownership @ref YS_OWN to `fclose(file)` when @ref ys_delete_token_sink() runs, or @ref YS_BORROW to leave it
/// open.
/// @return a writer to hand to @ref ys_new_yeast_stream_writer().
YS_API ys_bytes_writer ys_fp_writer(FILE *file, ys_ownership ownership);

/// A custom allocator. A NULL callback falls back to its C counterpart. A zeroed struct uses `malloc`, `realloc` and
/// `free`. Setting some of the callbacks mixes custom and standard behavior.
///
/// `allocate` and `reallocate` must set `errno` when they return NULL, as `malloc` and `realloc` do. libyeast passes
/// that value through the constructor that was allocating. The caller reads that value after the constructor returns
/// NULL. A debug build asserts a failing allocator set `errno`.
typedef struct ys_allocator {
    void *(*allocate)(void *context, size_t size);                  ///< Allocate `size` bytes, or NULL (set `errno`).
    void *(*reallocate)(void *context, void *pointer, size_t size); ///< Resize `pointer` to `size` bytes, or NULL.
    void (*deallocate)(void *context, void *pointer);               ///< Free `pointer`. A NULL `pointer` frees nothing.
    /// Release @ref context where it needs releasing. Return `0`, or `-1` with `errno` set. The reader's close and
    /// the writer's close follow that contract too. May be NULL, and that is the
    /// common case. An allocator that is no more than a set of functions has nothing to release. Deleting an object
    /// built with the allocator calls this once. That call comes after the last @ref deallocate. The final @ref
    /// deallocate before this call frees the object itself. An arena or a pool can then go down with the object the
    /// caller built out of it.
    int (*close)(void *context);
    void *context; ///< Opaque state passed to the callbacks.
} ys_allocator;

/// The policy a parser stores for the input it could not parse. The parser ignores this policy.
typedef enum ys_resume {
    YS_RESUME_NONE,     ///< Names ending the parse at an error. This is the default.
    YS_RESUME_DOCUMENT, ///< Names resuming at the next document.
    YS_RESUME_INDENT    ///< Names resuming at the next line no more indented than the entry that failed.
} ys_resume;

/// Construction options for a source or a sink. A zeroed struct selects the defaults.
typedef struct ys_options {
    ys_allocator allocator; ///< Custom allocator. A zeroed allocator takes the defaults @ref ys_allocator states.
    ys_resume resume;       ///< The policy for a malformed document. The default is @ref YS_RESUME_NONE.
    /// Cap on the memory the parser allocates for itself. The unit is bytes, and `0` means unlimited. Reaching the cap
    /// while parsing is a @ref YS_FAILED_MEMORY return. That return ends the parse for good. A cap smaller than the
    /// parser makes the constructor return NULL with `errno` set to `ENOMEM`. The cap is not an allocation failure. The
    /// @ref allocator does not see it.
    ///
    /// The cap counts the parser object and the parser's stack. The cap also counts the input a stream parser buffers
    /// and the tokens the parser holds back.
    ///
    /// The cap does not count the input of a string parser. That input belongs to the caller, and libyeast does not
    /// copy it. A long document parses under a small cap. The cap still counts the held-back tokens and the stack.
    ///
    /// A custom @ref allocator that refuses beyond some limit caps
    /// the parser just as well. The cap and the allocator are independent.
    size_t max_bytes;
} ys_options;

/// An opaque leak-checking allocator. It wraps `malloc`, `realloc` and `free`, and counts live allocations. A test or
/// a consumer can then confirm the count came back to `0`. Its overhead over plain `malloc` and `free` is a single
/// counter. It does not detect memory corruption.
/// Use a sanitizer for that.
typedef struct ys_counting_allocator ys_counting_allocator;

/// Create a counting allocator.
///
/// @return a new counting allocator, or NULL on allocation failure. The underlying `malloc` sets `errno` to `ENOMEM`,
/// as https://pubs.opengroup.org/onlinepubs/9799919799/functions/malloc.html says.
/// @ref ys_delete_counting_allocator() frees the result.
YS_API ys_counting_allocator *ys_new_counting_allocator(void);

/// Build the allocator functions that route an allocation through the counter. Place the result in @ref
/// ys_options::allocator. The result's @ref ys_allocator::close is @ref ys_close_counting_allocator(). That call
/// reports a leak when the caller deletes a source or a sink built on the counter.
///
/// Set `allocator.close` to NULL to hand a counter to more than a single object. Otherwise the first object's close
/// sees the buffers of the other objects. It reports those buffers as a leak. Call @ref ys_close_counting_allocator()
/// yourself once the last object is gone.
///
/// @param counter the counting allocator.
/// @return a @ref ys_allocator backed by @p counter.
YS_API ys_allocator ys_counting_allocator_functions(ys_counting_allocator *counter);

/// Check that nothing allocated through the counter is still live. @ref ys_counting_allocator_functions() installs this
/// call as the @ref ys_allocator::close. A caller may also call this function directly. The call reports a leak as an
/// allocator failure rather than asserting. The caller decides what a leak means.
///
/// @param counter the counting allocator, as a `void *`.
/// @return the value `0` where nothing is live, or `-1` with `errno` set to `ENOMEM` where anything is live.
YS_API int ys_close_counting_allocator(void *counter);

/// The count of live allocations made through the counter. A deallocation through the counter lowers it. Check that the
/// count is back to `0` after you free the buffers.
///
/// @param counter the counting allocator.
/// @return the live buffer count.
YS_API size_t ys_counting_allocator_live_buffers(const ys_counting_allocator *counter);

/// Free a counting allocator. Does not free anything allocated through it.
///
/// @param counter the counting allocator to free. It may be NULL. The call then frees nothing.
/// https://en.cppreference.com/w/c/memory/free says `free` of NULL does nothing.
YS_API void ys_delete_counting_allocator(ys_counting_allocator *counter);

/// An opaque source of yeast tokens. It is YAML parsed from memory by @ref ys_new_yaml_memory_parser(), or from a
/// stream by @ref ys_new_yaml_stream_parser(). It is also a yeast wire replayed from a stream by
/// @ref ys_new_yeast_stream_reader().
///
/// A caller pulls tokens from a source with @ref ys_read_token(). The
/// caller deletes the source with @ref ys_delete_token_source(). Code over tokens runs unchanged whether a parser or a
/// replay made them.
typedef struct ys_token_source ys_token_source;

/// Create a source that parses YAML from an in-memory buffer. The buffer must outlive the source. A token's text
/// points directly into that buffer, and @ref ys_are_tokens_stable() returns true.
///
/// @param input the bytes to parse. They must remain valid until @ref ys_delete_token_source(). NULL is fine with a
/// @p length of `0`, and that names an empty input.
/// @param length the count of bytes in @p input.
/// @param options construction options, or NULL for defaults.
/// @return a new source, or NULL on failure. `errno` is then `EINVAL` where @p input is NULL and @p length
/// is other than `0`. `errno` is `ENOMEM` where @ref ys_options::max_bytes was smaller than a source. A refusing
/// allocator sets `errno` itself.
YS_API ys_token_source *ys_new_yaml_memory_parser(const char *input, size_t length, const ys_options *options);

/// Create a source that parses YAML pulled from @p reader on demand. A token's @ref ys_token::text points into an
/// internal buffer. An error token's text is the parser's message instead, and a zero-width token has NULL text. That
/// text is valid until the next @ref ys_read_token() call. @ref ys_are_tokens_stable() returns false.
///
/// **The source takes @p reader.** That holds even where the constructor fails. A failing constructor closes @p reader
/// before returning NULL. It calls the `close` callback exactly as @ref ys_delete_token_source() would have. A reader
/// whose `close` callback closes a file descriptor therefore leaks no descriptor. The caller must not close the reader.
/// A second close would run the `close` callback twice.
///
/// @param reader the byte source. Its `read` callback must be non-NULL. The source owns it from this call onwards.
/// @param options construction options, or NULL for defaults. @return a new source, or NULL on failure. A failure
/// closes @p reader. `errno` is `EINVAL` if @p reader has no `read` callback, and `ENOMEM` if the memory cap is too
/// small. A failing allocator sets `errno` itself.
YS_API ys_token_source *ys_new_yaml_stream_parser(ys_bytes_reader reader, const ys_options *options);

/// Create a source that replays a yeast wire pulled from @p reader on demand. This is the inverse of @ref
/// ys_new_yeast_stream_writer(). A token's text points into an internal buffer, and a marker has NULL text. That text
/// is valid until the next @ref ys_read_token() call. @ref ys_are_tokens_stable() returns false.
///
/// **The source takes @p reader.** @ref ys_new_yaml_stream_parser() documents that ownership, and it holds here as
/// well.
///
/// @param reader the byte source. Its `read` callback must be non-NULL. The source owns it from this call onwards.
/// @param options construction options, or NULL for defaults. The source consults @ref ys_options::allocator and @ref
/// ys_options::max_bytes. @return a new source, or NULL on failure. A failure closes @p reader. `errno` is `EINVAL` if
/// @p reader has no `read` callback, `ENOMEM` if the cap refuses, and the allocator's own `errno` if the allocator
/// refuses.
YS_API ys_token_source *ys_new_yeast_stream_reader(ys_bytes_reader reader, const ys_options *options);

/// Report whether token text pointers stay valid for the source's whole lifetime.
///
/// @param source the source to query.
/// @return true for a memory parser. Its text points into the caller's buffer, and an error's message is a static
/// string. It returns false for a stream parser or a wire replay. Their text points into a buffer the next
/// @ref ys_read_token() call may overwrite.
YS_API bool ys_are_tokens_stable(const ys_token_source *source);

/// Read the next token into @p token.
///
/// A **malformed input** is a syntax error in the YAML, or a wire that is not the wire format. The call fills @p token
/// with a @ref YS_CODE_ERROR whose @ref ys_token::text is the message. The call returns `0` like any other token.
///
/// A wire replay has no input left after the error.
///
/// A **host failure** is not a token but the return value. @ref YS_FAILED_STREAM is the reader failing. @ref
/// YS_FAILED_MEMORY is the allocator refusing. @ref YS_FAILED_STREAM leaves the reader's `errno`. @ref YS_FAILED_MEMORY
/// sets `errno` to `ENOMEM`.
/// @ref YS_FAILED_ACTION comes once a caller reads past the end of the stream. A wire sets no `errno` on the first such
/// read. A later read sets `errno` to `ENODATA`.
///
/// A resource failure ends the source for good. The stream has no `end-stream` to close the `begin-stream`. That
/// missing close is the sign the parse did not finish. A later call returns @ref YS_FAILED_ACTION.
///
/// The caller does not free @ref ys_token::text. A single rule covers a token under any code. The text stays valid
/// for as long as @ref ys_are_tokens_stable() promises. A message the source raises itself is a static string. An error
/// a wire replay reads off the wire holds its message in the replay's buffer. The rule covers both.
///
/// @param source the source to advance.
/// @param token the place for the token read. A negative return fills no token. A wire replay may already have written
/// @ref ys_token::code.
/// @return @ref YS_OK with @p token filled, or @ref YS_FAILED_STREAM, @ref YS_FAILED_MEMORY or @ref YS_FAILED_ACTION.
YS_API int ys_read_token(ys_token_source *source, ys_token *token);

/// Delete a token source and what it owns. The call says whether the closeable things closed cleanly. A stream parser
/// or a wire replay holds a reader. The call closes that reader first, where its @ref ys_bytes_reader::close is not
/// NULL. It closes the allocator after that, where @ref ys_allocator::close is not NULL. The call frees the memory even
/// where a close fails.
///
/// @param source the source to delete. It may be NULL. The call then does nothing and returns @ref YS_OK. @return @ref
/// YS_OK if the closes succeeded. It is @ref YS_FAILED_STREAM if the reader's close failed, and @ref YS_FAILED_MEMORY
/// if the allocator's close failed. It is @ref YS_FAILED_BOTH if both did. `errno` holds what the first failure set,
/// and what the reader set where both failed. That is the documented limit of a single `errno`. A caller wanting both
/// records them in the `context` of a callback.
YS_API int ys_delete_token_source(ys_token_source *source);

/// An opaque sink of yeast tokens. It mirrors @ref ys_token_source. @ref ys_new_yeast_stream_writer() makes a sink
/// that serializes tokens to a yeast wire. @ref ys_write_token() feeds a sink, and @ref ys_delete_token_sink()
/// releases a sink. Code
/// writing tokens does not know or care where they go.
typedef struct ys_token_sink ys_token_sink;

/// Create a sink that serializes tokens to a yeast wire pushed to @p writer. This is the inverse of
/// @ref ys_new_yeast_stream_reader().
///
/// **The sink takes @p writer.** That holds even where the constructor fails. A failing constructor closes @p writer
/// before returning NULL. @ref ys_delete_token_sink() would close it the same way.
///
/// @param writer the byte sink. Its `write` callback must be non-NULL. The sink owns it from this call onwards.
/// @param options construction options, or NULL for defaults. The sink consults @ref ys_options::allocator and
/// @ref ys_options::max_bytes.
/// @return a new sink, or NULL on failure. A failure closes @p writer. `errno` is `EINVAL` if @p writer has no
/// `write` callback. It is `ENOMEM` if @ref ys_options::max_bytes cannot hold the sink. It holds what the allocator set
/// if the allocator refuses.
YS_API ys_token_sink *ys_new_yeast_stream_writer(ys_bytes_writer writer, const ys_options *options);

/// Create a sink that emits tokens as YAML pushed to @p writer. This is the inverse of a YAML parser. The token
/// stream is byte-complete, and the emitter writes the bytes a token spans. The emitter reconstructs the input. A
/// structure marker spans nothing.
///
/// The emitter renders rather than judges. A @ref YS_CODE_ERROR is not a token the emitter can emit. The text of such
/// a token is a message rather than input. @ref ys_write_token() refuses such a token and returns @ref YS_FAILED_ACTION
/// with `EINVAL`. A caller may drop comments or unparsed input. Such a caller filters the token stream above the
/// emitter. The emitter writes what the caller hands it.
///
/// **The sink takes @p writer.** That holds even where the constructor fails. A failing constructor closes @p writer
/// before returning NULL.
///
/// @param writer the byte sink. Its `write` callback must be non-NULL. The sink owns it from this call onwards.
/// @param options construction options, or NULL for defaults. The sink consults @ref ys_options::allocator and
/// @ref ys_options::max_bytes.
/// @return a new sink, or NULL on failure. `errno` is `EINVAL` if @p writer has no
/// `write` callback. `errno` is `ENOMEM` where @ref ys_options::max_bytes cannot hold the sink. A failing allocator
/// sets `errno` itself.
YS_API ys_token_sink *ys_new_yaml_stream_emitter(ys_bytes_writer writer, const ys_options *options);

/// Write a token to a sink. For a yeast writer, a token is a pair of lines. The first line is its position. The second
/// line holds the code character and then the text.
///
/// @code
/// # B: 12, C: 12, L: 1, c: 4 I- @endcode
///
/// The writer escapes the text. It writes a printable ASCII character other than a backslash as itself. It writes any
/// other character as `\xXX`, `\uXXXX` or `\UXXXXXXXX`. A zero-width marker has no text, and its second line holds only
/// the code character.
///
/// @param sink the sink to write to. @param token the token to write. @return @ref YS_OK on success. The return value
/// is @ref YS_FAILED_STREAM where the position line overflows its buffer or a write fails. After a failed write,
/// `errno` holds what the byte transport's `write` callback set. A callback that writes nothing need not set `errno`.
/// The return value is @ref YS_FAILED_ACTION with `errno` `EINVAL` where the writer cannot write the token at all. The
/// failure covers a code the wire writes nothing for. It covers text whose bytes do not match what @p token's code
/// says. It also covers a @ref YS_CODE_ERROR handed to an emitter.
YS_API int ys_write_token(ys_token_sink *sink, ys_token token);

/// Delete a token sink and what it owns. The call says whether the closeable things closed cleanly. The byte transport
/// under the sink closes first, where its @ref ys_bytes_writer::close is not NULL. The sink's allocator closes next.
///
/// A buffered transport such as @ref ys_fp_writer() flushes at its close, as
/// https://en.cppreference.com/w/c/io/fclose says. A stream written without a fault can still fail there. The call
/// closes both and frees the memory even where a close fails.
///
/// @param sink the sink to delete. It may be NULL, in which case this is a no-op returning @ref YS_OK.
/// @return @ref YS_OK if the closes succeeded. It is @ref YS_FAILED_STREAM if the transport's close failed, and
/// @ref YS_FAILED_MEMORY if the allocator's close failed. It is @ref YS_FAILED_BOTH if both closes failed. `errno`
/// holds what the first failure set, and what the transport set where both failed.
YS_API int ys_delete_token_sink(ys_token_sink *sink);

#ifdef __cplusplus
}
#endif

#endif // YEAST_H
