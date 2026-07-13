# Decoder design — UTF-8 ingest and grammar-derived character classification

## Scope

The decoder is the bottom layer of libyeast: it turns input bytes into classified characters that the generated parser
can branch on. It answers exactly one question per character — *what kind of character is this, as far as the YAML
grammar is concerned* — and it answers it without ever assembling a Unicode codepoint.

In scope: the character key encoding, the generator that derives it from the grammar IR, the UTF-8 validation rules, the
`next_char` / `scan_set` interface, and the test strategy.

Out of scope: buffer management (how a stream parser fills, grows, and pins buffers so token spans survive a refill),
and the parser itself. The decoder is written against a plain `(bytes, size)` window so that those decisions stay
independent of it.

## The key insight: the parser never needs a codepoint

Tokens are spans of input bytes. Content is copied or spanned verbatim; nothing compares a character's numeric value.
Three sites need a raw byte, and all three read it directly out of the input at a known offset, doing arithmetic rather
than classification:

- `(ord)` on the block-scalar indent indicator digit (`b - '0'`),
- the hex digits of `\xNN` / `\uNNNN` / `\UNNNNNNNN` escapes,
- error messages, which reconstruct `U+XXXX` lazily on a path that is already dying.

`ys_mark` counts codepoints (`char_offset`, `line`, `column`), but a count is not a value: it increments once per
character, and lines advance on the break classes.

So the decoder is a **validate-and-classify** function, not a decoder in the usual sense. It never builds a `uint32_t`
codepoint, which is strictly less work than any decode-first design and is why no existing UTF-8 library fits (see
"Prior art", below).

## What the grammar actually demands

Derived mechanically from the 211-production IR:

| quantity                                         | value                      |
| ------------------------------------------------ | -------------------------- |
| distinct literal characters named by the grammar | 57                         |
| distinct character sets the grammar tests        | 19                         |
| literal characters above U+007F                  | 2 (`x85` NEL, `xFEFF` BOM) |
| character classes needed above U+007F            | 6                          |

A *tested set* is a **maximal** character-set node in a matching position — maximal meaning its parent is not itself a
character set. The four ranges inside `c-printable`'s union are constituents, not tested sets: nothing asks about them
on their own, only about `c-printable`. Deduplicating the maximal nodes by denotation (two productions describing the
same codepoints share one entry) leaves 19.

These numbers are computed by the generator, not asserted here; if the vendored grammar changes, they change with it.

## The character key

The key is a **precomputed answer sheet**. There are only 19 questions the grammar can ask about a character, so the
decoder answers all of them once, per character, and stores the answers as bits.

```c
typedef uint32_t ys_char;   // [ 27..25 len | 24..6 set bits | 5..0 literal id ]

#define YS_LIT(ch) ((ch) & 0x3Fu) ///< 0 = not a named literal; 1..57 = the grammar's named characters; 58/59 sentinels.
#define YS_LEN(ch) (((ch) >> 25) & 0x7u) ///< Bytes of input the character consumed, 0..4.
```

Twenty-eight of thirty-two bits used. One 32-bit load out of a 512-byte, 128-entry ASCII table, travelling in one
register.

Every test the generated parser emits is one of two shapes, both against 32-bit immediates:

```c
(ch & YS_SET_NS_CHAR) != 0     // any character set — union, range, or subtraction alike
YS_LIT(ch) == YS_LIT_DASH      // a single named literal
```

Subtraction and union never appear at a test site. `ns-char = c-printable - x0A - x0D - xFEFF - x20 - x09` is evaluated
by the generator against every codepoint, and the answer becomes one bit. The generator does no bit reasoning — it
evaluates each set's IR definition per codepoint and lights the bit — so a wrong mask is not a failure mode that exists.

One bit per *tested set* rather than one bit per *minimal generating atom*: the minimal family is 18 bits, one narrower,
but it stores a character's equivalence class rather than the answers, so a production must reconstruct membership from
a multi-bit mask. Same instruction count, one bit saved, one layer of indirection added — and with 4 bits spare there is
nothing to buy. The key holds answers.

Bitfields are deliberately avoided: C leaves bitfield allocation order implementation-defined, and the generator emits
the table as raw `uint32_t` constants, so a bitfield layout would have to be assumed rather than controlled. Explicit
shifts and masks let the generator own the layout, and let a set test be a single AND against the whole word rather than
a field extraction followed by an AND.

### Sentinels

End of input and invalid UTF-8 are literal ids (`YS_LIT_EOF` = 58, `YS_LIT_INVALID` = 59) carrying no set bits. Because
they belong to no set, **every** generated membership test fails at them automatically — no end-of-input special case is
needed at any of the thousands of test sites. `YS_LEN` is 0 for EOF and 1 for invalid, so an error path can skip the
offending byte and keep collecting diagnostics rather than halting at the first.

The sentinels are distinguishable from an unclassified byte (a C0 control such as `x01` belongs to no set and is no
named literal, so its literal id is 0) precisely because their literal ids are non-zero.

There is no `INCOMPLETE` sentinel. A multi-byte sequence truncated by the end of the window is `INVALID`, which is the
right answer at true end of input; a stream buffer that hands the decoder a partial sequence before its true end has a
bug in the buffer layer, not in the decoder.

## Interface

```c
/// The next character at the head of the window, or EOF when the window is empty.
static inline ys_char ys_next_char(const uint8_t *bytes, size_t size);

/// Advance while the character is in `set`; return the number of bytes consumed.
size_t ys_scan_set(const uint8_t *bytes, size_t size, ys_set_id set);
```

`ys_set_id` is a generated enum naming the 19 sets. `ys_scan_set` takes the id rather than the bit mask because the
vector kernels index a generated table of nibble-table pairs by it; the scalar loop recovers the mask from the id.

Both are pure functions of a `(bytes, size)` window; the caller advances by `YS_LEN(ch)`. `size` is the true number of
readable bytes — no padding contract, because `ys_new_string_parser()` takes the caller's buffer and cannot be asked to
pad it.

`ys_next_char` is `static inline` in the internal header. A function call per character would negate this entire design:

```c
static inline ys_char ys_next_char(const uint8_t *bytes, size_t size) {
    if (size == 0) return YS_KEY_EOF;
    if (bytes[0] < 0x80) return YS_ASCII[bytes[0]];  // one load; over 99% of real YAML bytes
    return ys_next_char_slow(bytes, size);           // out-of-line, cold, bounds-checked
}
```

`bytes` is `const uint8_t *`, never `const char *`: `char` is signed on the platforms we target, and a byte ≥ 0x80 would
index the table negatively.

`ys_scan_set` exists because the bytes go into *runs* — plain scalars, comments, indentation, quoted content — and a run
should not cost one `next_char` per byte. Every `(***)` and `(+++)` over a character class in the grammar compiles to
one `ys_scan_set` call. It ships as a scalar byte loop; see "Vectorization".

## Decoder internals

`ys_next_char_slow` handles bytes ≥ 0x80. A 256-entry lead-byte table gives the sequence length and the legal range of
the *first continuation byte*; that range is what rejects overlong encodings (`E0` requires `A0..BF`), surrogates (`ED`
requires `80..9F`), and out-of-range codepoints (`F4` requires `80..8F`) — with no codepoint arithmetic at all. The
remaining continuation bytes are checked against `80..BF`.

Classification then falls out of the byte pattern, because the grammar names only a handful of non-ASCII characters:

| bytes                    | class                                                |
| ------------------------ | ---------------------------------------------------- |
| `C2 85`                  | NEL — a named literal                                |
| `C2 80..9F`              | C1 control zone — in `nb-json`, not in `c-printable` |
| `EF BB BF`               | BOM — a named literal                                |
| `EF BF BE`, `EF BF BF`   | noncharacter — in `nb-json`, not in `c-printable`    |
| any other valid sequence | ordinary content character — one shared constant key |
| anything else            | `YS_LIT_INVALID`                                     |

## Generator

`generator/grammar2decoder.py` emits `decoder_tables.h` from the IR. It emits **data only**:

- the 128-entry ASCII key table,
- the named-literal ids and the sentinel ids,
- the 19 set-bit constants, computed by evaluating each set's IR definition per codepoint,
- the non-ASCII class constants,
- the SIMD nibble tables, once the vector kernels land (see below).

A set's name is the character-set production that defines it; a set the grammar tests inline, with no production of its
own, is named for its enclosing production plus an index (`ns-plain-first` holds two of them, so they must be
distinguished).

It does **not** emit the UTF-8 mechanics. Those are fixed by RFC 3629, not by YAML; generating them would be generating
a constant. They live hand-written in `decoder.h` / `decoder.c`.

| file                   | contents                                                                   |
| ---------------------- | -------------------------------------------------------------------------- |
| `src/decoder_tables.h` | generated, and committed to the repository                                 |
| `src/decoder.h`        | `ys_char`, `ys_set_id`, `ys_next_char` (inline), `ys_scan_set` declaration |
| `src/decoder.c`        | `ys_next_char_slow`, the `ys_scan_set` kernels, and runtime CPU dispatch   |

`decoder_tables.h` is committed rather than built, so that building libyeast from a release tarball needs no Python. A
`make vet` sub-gate regenerates it and diffs against the committed copy, in the same spirit as the grammar round-trip
gate: the checked-in tables cannot drift from the grammar without failing the build.

## Vectorization

`ys_scan_set` ships as a scalar byte loop, behind its final signature. The generated parser calls it either way, so the
vector kernels drop in later without touching a line of generated code — and only once a benchmark can prove they win.

The encoding was chosen so that they *can* drop in. Membership in an ASCII byte set is expressible as a nibble-table
lookup — two 16-byte tables (low nibble → bitmask, high nibble → bitmask), membership is
`(lo[b & 0xF] & hi[b >> 4]) != 0` — which is two `pshufb`, an AND and a compare: sixteen bytes classified in about four
instructions, thirty-two under AVX2, and the same shape under NEON's `vqtbl1q_u8`. Bytes ≥ 0x80 fall out of the set
automatically and break the run, which is exactly right: the scalar path then handles the non-ASCII character. The
generator emits the 32-byte table pair per scannable set, so the vector masks have the same provenance as everything
else.

Kernels must be runtime-dispatched (`pshufb` is SSSE3, while the x86-64 baseline is SSE2), which is why `decoder.c`
exists at all rather than everything living in a header.

## Prior art, and why we write this

Every fast UTF-8 implementation in C solves one of two problems, and neither is this one:

- **Hoehrmann's DFA, `utf8proc`, Rust's `core`** decode to a codepoint. Their state machines exist to accumulate the
  value we established we do not want. What survives butchering is the lead-byte table and the continuation check — the
  handful of lines we would write anyway — while the license notice for the deleted code remains.
- **simdutf, Lemire's lookup algorithm** bulk-validate megabytes and produce no per-character output at all, so they
  cannot classify. (They are also Apache-2.0 against our MIT: combinable, but they drag NOTICE obligations onto the
  project.)

"Validate and classify without decoding" is not a problem anyone else has, because it only exists once the grammar has
handed you a 19-set key. So the code is ours, and correctness comes from the test corpus instead — which is the part
worth borrowing.

## Testing

- **Kuhn's UTF-8 stress test** (`UTF-8-decoder-capability-and-stress-test`): the canonical malformed-input torture file
  — overlongs, lone surrogates, truncated sequences, boundary codepoints. Every case asserts `YS_LIT_INVALID` or the
  expected class.
- **Exhaustive codepoint sweep**: every codepoint UTF-8 can encode is encoded by an encoder written independently of the
  decoder, decoded, and checked for the right length; every surrogate must be rejected. The independence of the two
  implementations is what makes this a check rather than a tautology.
- **Table-versus-grammar property test**: for every codepoint, the generated key must agree with a direct evaluation of
  the IR's set definitions. This is the check that keeps `decoder_tables.h` honest, and it is exhaustive — 1.1 M
  codepoints is a second of CPU.
- **Boundary tests**: empty window, a window ending mid-sequence, a lone continuation byte, `size == 1` with a 4-byte
  lead.

## Not a threat: the one-byte speculative window

`ys_next_char` checks `size == 0` and then loads `bytes[0]`. That branch is predicted not-taken essentially always (it
is true once per parse), so at the real end of input the CPU mispredicts once and *speculatively* loads the byte one
past the buffer, using it to index `YS_ASCII`. That is Spectre-v1 gadget shape.

It is not worth designing around. The out-of-bounds address is not attacker-steerable — it is always exactly
`input + length`, with no attacker-controlled offset — so it cannot be turned into an arbitrary read. Exploiting it
would require a secret placed at precisely the byte following the input buffer, cache timing on the same core, and it
would yield a few bits of that one byte, once per parse. Architecturally there is no out-of-bounds read: the check is
correct.

The alternatives are all worse: an `lfence` per character destroys the design, and demanding a padded input buffer
contradicts `ys_new_string_parser()`'s contract that the buffer is the caller's. Every C parser taking a caller's buffer
has this same one-byte window. The stream parser, which owns its buffer, can keep a readable sentinel and has no window
at all.
