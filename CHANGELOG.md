# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The ABI is unstable through `0.x`.

## [Unreleased]

### Added

- Project framework: CMake build (shared + static, hardened, symbol-visibility controlled), an incremental pre-commit
  `make` gate, tests (acutest), coverage with a `// UNTESTED` contract, Doxygen docs with a completeness gate, and a
  package-consumption test.

- Continuous integration: per-sub-gate GitHub Actions workflows (static quality, C tests, docs) with independent status
  badges and CodeQL analysis, and published API docs plus an HTML coverage report on GitHub Pages.

- Version API: `ys_version`, `ys_major`, `ys_minor`, `ys_patch`.

- Pull-parser API surface: `ys_new_string_parser` / `ys_new_stream_parser` / `ys_next_token` / `ys_free_parser`, with
  `ys_fd_reader`/`ys_fp_reader` reader adapters, a pluggable allocator, and the `ys_counting_allocator` leak counter.
  The parser core is not implemented yet, so `ys_next_token` returns a "not implemented" error.

- Character decoder: UTF-8 input is validated and classified against the grammar without a Unicode codepoint ever being
  assembled. Each character becomes a 32-bit key — the id of the character where the grammar names it, one bit per
  character set the grammar tests, and the bytes it consumed — so a test in the parser is a single comparison or a
  single AND. The tables are generated from the grammar and gated against drift.

- Annotated grammar: `grammar/yeast-spec-1.2.yaml` is libyeast's own grammar, and the source everything is generated
  from. It carries the YAML 1.2 rules together with the yeast tokens they emit, which the official grammar cannot
  express — it inlines the indicator characters, losing the structure the token layer hangs on, and names no token at
  all. It is also the only place the yeast token format is written down: the notation, the codes, and, rule by rule,
  what each emits and why. Four gates keep it honest. Erasing libyeast's additions recovers the official grammar
  exactly, so the grammar is hand-authored where it must be and machine-proved where it can be. Every character the
  parser consumes must lie within a token action, so a forgotten one fails the build rather than emitting an `unparsed`
  token years later. Every `begin-` marker must be closed by its own `end-`, on every path and for every context and
  chomping. And every rule that emits tokens must say which, checked against the grammar itself, so a note that is wrong
  fails as surely as one that is missing.

- Indentation detection, which the official grammar declares a "special rule" and never defines, and which it elsewhere
  leaves as an integer added to the string `"auto-detect"`. libyeast defines it, and its two departures from the
  official grammar are declared, with their reasons: `m` is an indentation now, and `s-l+block-indented` sets the `m` it
  had been reading and never setting.

- The yeast wire format: `ys_write_token` writes a token stream — a character and its escaped text per token — and
  `ys_read_token` reads one back, so a stream can be piped between tools, stored, or compared against another parser's.
  `ys_writer` mirrors `ys_reader`, with the same file-descriptor and `FILE *` adapters.

- Errors tell the caller what to do about them. A malformed document is `YS_CODE_ERROR_FORMAT`, running out of memory is
  `YS_CODE_ERROR_MEMORY`, and a reader that fails is `YS_CODE_ERROR_READER`; the last two end the parse for good — the
  input must be parsed again, by a new parser with a larger cap. On the wire all three are `!`, since a consumer of the
  wire has no choice to make between them. What the parser does with the input after a malformed document is
  `ys_options.resume`: by default the error ends the parse and the rest of the input comes back as `YS_CODE_UNPARSED`
  tokens, which is what the reference parser does, so the two token streams stay comparable on every input, valid or
  not. `YS_RESUME_DOCUMENT` instead carries on at the next document, so that one malformed document in a stream does not
  cost the caller the others — at the price that only the input before the first error can be compared.

- Parser state: the window over the input, the stack of productions the parser is inside, the queue of tokens it has
  built but not handed back, and the state it is in — the whole of it in one struct, none of it in the C call stack,
  which is what lets `ys_next_token` hand back a token from the middle of a production and resume there. The queue holds
  a run of undecided tokens, whose codes are rewritten and ahead of which a marker is injected when the parser learns
  what they were, and the stack's frames carry the grammar's one runtime parameter, `n`. The automaton that drives them
  is not generated yet, so `ys_next_token` still returns a "not implemented" error.

- A conformance suite, `tests/spec/`. It was built once from the reference parser's vendored `tests/` — the fixtures
  that align with libyeast's grammar, each expected output turned into what libyeast emits rather than what the
  reference does: a production libyeast flattens to a character class becomes plain unparsed, no token spans a line, a
  byte-order mark is the character it matched and not the reference's encoding name, an error keeps its position but not
  its wording, the reference's isolated-run commit artifacts are dropped, and where the reference itself departs from
  the spec (the plain-scalar `:`/`#` factoring) libyeast follows the spec. Fixtures in encodings libyeast does not read,
  or for the reference's own internal productions, are left out. From there the suite is libyeast's to own — the
  one-time build is not kept; `generator/check_spec_tests.py` keeps the suite intact, every input paired, every name a
  production the grammar still has, every output a token stream whose marks chain. The bytes are held verbatim, CR and
  CRLF included, out of line-ending normalization.

- A reference interpreter of the grammar, `generator/interpreter.py`: a slow, obviously-correct backtracking matcher
  that runs a production against an input and emits its yeast tokens, checked fixture by fixture against the conformance
  suite so libyeast's grammar is proved to produce the reference's tokens before any C runs. It matches the
  character-level nodes — a literal, a range, a subtraction, a sequence, an alternation, a reference — and produces
  tokens from the annotation nodes, giving a run its code, bracketing a match in `begin`/`end` markers, and emitting a
  marker on its own. It reproduces every fixture that rests on only those; its coverage grows a node family at a time,
  toward running `l-yaml-stream` on the whole suite.

### Changed

- `ys_options.max_token_bytes` becomes `max_bytes`, and caps the memory the parser allocates rather than the bytes it
  buffers for one token. Three things grow — the buffered input, the tokens held back with it, and the parser's stack,
  which deep nesting grows and no quantity of input bounds — and one cap now bounds them together.

- `src/yeast.c` is gone, split by topic: the version query and its load-time sanity check, the counting allocator, the
  stream adapters, and the yeast wire format each have a file of their own. Allocation and the `max_bytes` accounting
  are one place, `src/memory.c`, rather than one copy in the parser and another in the wire-format reader; a reader held
  under a cap it cannot even be built in is now refused outright, as the parser already was.

- The reader of the yeast wire format tells a broken wire from the tokens a wire carries. Every token code, the three
  error codes included, is content the wire legitimately replays, so the reader cannot signal its own trouble with one
  of them. A wire it cannot read — not the wire format, out of the memory to read it, or a byte source that failed —
  ends the stream on a `YS_CODE_WIRE_ERROR` token, a code no parse emits and no writer produces. Its text says what was
  wrong, one message per way the wire can be broken, and its marks say where: the line and column in the wire. So
  `false` from `ys_read_token` means one thing, the wire ended, and a caller reading until then still learns why it
  stopped. The reader validates its input as the parser does: a byte that a conformant wire would have escaped, an
  escape naming no Unicode codepoint, a position that is not a number — each is a located `YS_CODE_WIRE_ERROR`, not a
  misread.

- An `errno` policy across the API. A function that returns a token reports through the token and leaves `errno` for the
  callback that set it, so a reader's or allocator's `errno` survives beside the error token it became. A function that
  fails without a token — a constructor, `ys_write_token` — sets `errno`: `EINVAL` for a bad argument (a stream parser
  or token reader with no `read` callback, a string parser given a NULL buffer with a length), `ENOMEM` for insufficient
  memory, or the value a failing callback set, passed through. An allocator or reader callback must set `errno` when it
  fails; a debug build asserts a failing allocator did.

### Fixed

- The yeast wire format dropped an error's message. It took a token's text to be the input the token spans, and an error
  spans none — so a malformed document wrote `!` and nothing else, where the reference writes `!` and the message. The
  wire exists to compare token streams against the reference, and an invalid document is exactly where two parsers
  differ, so the comparison was broken precisely where it was worth the most.

- The wire reader handed out a token text that was not NUL-terminated, and `ys_write_token` took an error's length with
  `strlen` — so reading an error token off a wire and writing it back, the read-then-write pipe the wire exists for,
  overread the heap whenever the text filled its buffer exactly. The reader now leaves every text terminated, and a bare
  error reads back with an empty text rather than a NULL one, as the header always promised.

- A reader was leaked when the parser it was handed to could not be built. A reader is handed over, so an owned file
  descriptor is the caller's no longer — and a NULL return left them with nothing to close it with. Both constructors
  now close what they were given, and preserve the `errno` that named the failure across the close.

- `ys_hex` accumulated eight hexadecimal digits into a signed `long`, which overflows wherever a `long` is 32 bits —
  MSVC among them — and a wire could name a codepoint Unicode does not have, or half of a surrogate pair, and have it
  written into the reader's own text as bytes that are not UTF-8. And `ys_scan` read a position with `strtoul`, which
  takes a sign, so `# B: -1` was a position of `SIZE_MAX`.

- The marker gate could not see inside a `(<<<)`. It walked every other node and passed over that one, discarding what
  it held — so an unclosed `begin-` marker inside an indentation bound passed all six grammar gates. The gate that
  proves every marker is closed had a blind spot, and it was the only gate looking.

- The coverage gate passed on a report that covered nothing. The day gcovr's filters stopped matching, the `// UNTESTED`
  contract would have evaporated in silence while the badge still showed a percentage.

- The reader of the yeast wire format grew its line buffer on every refill, whether or not the lines it had handed back
  had already left room — so it grew to the size of the whole stream, and under a cap it stopped partway and read as a
  stream that had simply ended. A stream of 2000 short lines under a 16 KB cap yielded 2 tokens. It now grows only when
  what is left really does fill the buffer, which is what the parser's window already did: the two had the same shape
  and only one of them had the check, which is the argument for their now growing through the same code.

- Nothing in the build system keeps a list of files by hand. `CMakeLists.txt` globs the sources and the tests with
  `CONFIGURE_DEPENDS`, as the `Makefile` already globbed its inputs — and the one list that was still hand-kept, the
  `Makefile`'s set of files to lint, had already gone stale: it named neither `src/parser.c` nor `src/messages.c`, so
  neither had ever been linted.

- The `FILE *` writer adapter is tested on Windows, where it had never run: its test was portable but sat behind the
  guard that hides the file-descriptor ones, and behind that guard a second copy of the guard.

- The reference parser does not resume after an error, and libyeast had been built to match a reading of it that said it
  did: it emits the error token, hands back the input behind it as unparsed, and stops. So resuming at the next document
  was not fidelity but a departure, and the one thing it was adopted to protect — the token-for-token comparison against
  the reference — is exactly what it broke. Not resuming is now the default, and resuming is an option the caller asks
  for, knowing what it costs.

- An error's message is a static string, so its lifetime is no longer an exception to the rule every other token's text
  follows. It cannot be, since the message names the production the parser was inside and what it expected there, both
  of which are the grammar's and not the input's. What the parser found is not in the message and does not need to be:
  the first `YS_CODE_UNPARSED` token behind an error begins at exactly the byte that failed.

- No token spans a line — not text, not a comment, and not the input skipped after a malformed document, which comes
  back as one `YS_CODE_UNPARSED` token for a line's content and another for its break. A token that spanned a line would
  have made a stream parser's output depend on how much of the input its buffer happened to hold.

_No release has been tagged yet; the YAML parser itself is not implemented._
