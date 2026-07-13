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

Derived mechanically from the 211-production IR, by partitioning all of Unicode by every `Char` and `Range` atom the
grammar tests:

| quantity                                                                | value                      |
| ----------------------------------------------------------------------- | -------------------------- |
| distinct literal characters named by the grammar                        | 57                         |
| distinct composite character sets tested (unions, subtractions, ranges) | 28                         |
| equivalence classes over all of Unicode                                 | 68                         |
| literal characters above U+007F                                         | 2 (`x85` NEL, `xFEFF` BOM) |

The 28 composite sets are semantically distinct — none collapse. Everything above U+007F reduces to six classes, and the
only individually-named non-ASCII characters in the entire grammar are NEL and the BOM.

These numbers are computed by the generator, not asserted here; if the vendored grammar changes, they change with it.

## The character key

```c
typedef struct ys_char {
    uint32_t sets; ///< One bit per composite character set the character belongs to; 28 used, 4 spare.
    uint8_t lit;   ///< The named-literal id: 0 for "not a named literal", else 1..57, or a sentinel id.
    uint8_t len;   ///< Bytes of input the character consumed.
} ys_char;
```

Eight bytes: one aligned load out of the generated ASCII table, and it travels in a single register.

Every test the generated parser emits is one of two shapes, both against immediates:

```c
(ch.sets & YS_SET_NS_CHAR) != 0     // any character set — union, range, or subtraction alike
 ch.lit == YS_LIT_DASH              // a single named literal
```

Subtraction never appears at a test site: `ns-char = nb-char - s-white` is precomputed into the `ns-char` bit when the
generator evaluates the set over each codepoint. A set of literals (`c-indicator`, nineteen of them) is a set like any
other and gets its own bit. The generator does no bit reasoning — it evaluates each set's IR definition per codepoint
and lights the bit — so a wrong mask is not a failure mode that exists.

`sets` and `lit` are separate fields rather than one packed 64-bit word deliberately: 6 + 28 bits does not fit a
`uint32_t`, so packing would exile one field above bit 31 and force 64-bit immediates (`movabs` on x86-64) at every test
site. As two fields, the compiler tests `sets` with a 32-bit immediate and `lit` with an 8-bit compare, out of the same
register pair.

### Sentinels

End of input and invalid UTF-8 are literal ids (`YS_LIT_EOF`, `YS_LIT_INVALID`) with `sets == 0`. Because they belong to
no set, **every** generated membership test fails at them automatically — no end-of-input special case is needed at any
of the thousands of test sites. `len` is 0 for EOF and 1 for invalid, so an error path can skip the offending byte and
keep collecting diagnostics rather than halting at the first.

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

`ys_set_id` is a generated enum naming the 28 sets. `ys_scan_set` takes the id rather than the bit mask because the
vector kernels index a generated table of nibble-table pairs by it; the scalar loop recovers the mask from the id.

Both are pure functions of a `(bytes, size)` window; the caller advances by `len`. `size` is the true number of readable
bytes — no padding contract, because `ys_new_string_parser()` takes the caller's buffer and cannot be asked to pad it.

`ys_next_char` is `static inline` in the internal header. A function call per character would negate this entire design:

```c
static inline ys_char ys_next_char(const uint8_t *bytes, size_t size) {
    if (size == 0) return YS_CHAR_EOF;
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

| bytes                    | class                                                      |
| ------------------------ | ---------------------------------------------------------- |
| `C2 85`                  | NEL — a named literal                                      |
| `C2 80..9F`              | C1 control zone — in `nb-json`, not in `c-printable`       |
| `EF BB BF`               | BOM — a named literal                                      |
| `EF BF BE`, `EF BF BF`   | noncharacter — in `nb-json`, not in `c-printable`          |
| any other valid sequence | ordinary content character — one shared constant `ys_char` |
| anything else            | `YS_LIT_INVALID`                                           |

## Generator

`generator/grammar2decoder.py` emits `decoder_tables.h` from the IR. It emits **data only**:

- the 256-entry ASCII key table,
- the named-literal ids and the sentinel ids,
- the 28 set-bit constants, computed by evaluating each set's IR definition per codepoint,
- the non-ASCII class constants,
- the SIMD nibble tables (see below).

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
handed you a 28-set key. So the code is ours, and correctness comes from the test corpus instead — which is the part
worth borrowing.

## Testing

- **Kuhn's UTF-8 stress test** (`UTF-8-decoder-capability-and-stress-test`): the canonical malformed-input torture file
  — overlongs, lone surrogates, truncated sequences, boundary codepoints. Every case asserts `YS_LIT_INVALID` or the
  expected class.
- **Differential fuzz against Python's decoder**: random byte strings, compare validity and sequence length. Python's
  `bytes.decode("utf-8")` is the oracle for well-formedness; the grammar IR is the oracle for classification.
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
