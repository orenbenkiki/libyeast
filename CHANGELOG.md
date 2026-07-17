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
  token years later. Every `begin-` marker must be closed by its own `end-`, on every path and for every context,
  chomping and resume policy. And every rule that emits tokens must say which, checked against the grammar itself, so a
  note that is wrong fails as surely as one that is missing.

- Indentation detection, which the official grammar declares a "special rule" and never defines, and which it elsewhere
  leaves as an integer added to the string `"auto-detect"`. libyeast defines it, and its two departures from the
  official grammar are declared, with their reasons: `m` is an indentation now, and `s-l+block-indented` sets the `m` it
  had been reading and never setting.

- The yeast wire format: `ys_write_token` writes a token stream — a character and its escaped text per token — and
  `ys_read_token` reads one back, so a stream can be piped between tools, stored, or compared against another parser's.
  `ys_writer` mirrors `ys_reader`, with the same file-descriptor and `FILE *` adapters. An escape spells a codepoint
  under every code but `YS_CODE_UNPARSED_INVALID`, and a byte under that one, so each holds the other's text to being
  what it claims: writing `\x80` for a raw `0x80` under a code that means codepoints says U+0080 and reads back as two
  bytes that were never given, and `YS_CODE_UNPARSED_INVALID` exists to carry exactly the bytes that encode no character
  — so its text must encode none of them, every byte of it a place where none begins, and every other code's must encode
  them all. `ys_write_token` holds both halves and answers `EINVAL`, which the errno policy already promised it would
  for a bad argument and which it had never once checked. The validation is `decoder.c`'s, which had it all along:
  `ys_codepoint` assembled continuation bits without ever asking whether they were continuation bytes, and silently
  turned `"\xE0ab"` into different bytes. `YS_CODE_UNPARSED` is `YS_CODE_UNPARSED_TEXT` now, so the three say what they
  are together. The reader's search for a line's break resumes where the last one gave up rather than starting over, so
  a line arriving in pieces costs its length and not its length squared — 16MB on one line took 2.17s and takes 0.09s. A
  wire read from a pipe is what the format is for and is exactly what arrives in pieces, and `max_bytes` is unlimited by
  default, so the cost was a denial of service against the format's own purpose.

- Errors tell the caller what to do about them. A malformed document is `YS_CODE_ERROR_FORMAT`, running out of memory is
  `YS_CODE_ERROR_MEMORY`, and a reader that fails is `YS_CODE_ERROR_READER`; the last two end the parse for good — the
  input must be parsed again, by a new parser with a larger cap. On the wire all three are `!`, since a consumer of the
  wire has no choice to make between them. What the parser does with the input after a malformed document is
  `ys_options.resume`: by default the error ends the parse and the rest of the input comes back as `YS_CODE_UNPARSED`
  tokens, which is what the reference parser does, so the two token streams stay comparable on every input, valid or
  not. `YS_RESUME_DOCUMENT` instead carries on at the next document, so that one malformed document in a stream does not
  cost the caller the others; `YS_RESUME_INDENT` carries on at the next line no more indented than the entry that
  failed, inside the document, so that a malformed entry does not cost the caller the rest of its container either. Each
  gives up less of the input than the one before it, and where a policy has nothing to resume at it is the one before it
  — at the price that only the input before the first error stays comparable with the reference. A skipped line is two
  tokens, its content a `YS_CODE_UNPARSED` and its break a `YS_CODE_UNPARSED_BREAK` — the break its own code, since it
  is not a structural break the parser found.

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
  production the grammar still has, every output a token stream whose marks chain and whose markers balance — a fixture
  of the root being a whole parse, which must balance exactly, where one of a rule run by itself may close what its
  caller would have opened but may still not leave a marker open. That last is what `check_markers` cannot reach: it
  settles the grammar's clean paths and says nothing about what an error leaves behind, which is where both of the
  imbalances found so far have been. A fixture whose name calls its input invalid must have one: the production either
  refuses it or stops short of its end, never matching the whole of it cleanly — the name being a claim, and an
  unchecked claim being how `c-printable.invalid` came to hold a character `c-printable` accepts. The bytes are held
  verbatim, CR and CRLF included, out of line-ending normalization.

- A reference interpreter of the grammar, `generator/interpreter.py`: a slow, obviously-correct backtracking matcher
  that runs a production against an input and emits its yeast tokens, checked fixture by fixture against the conformance
  suite so libyeast's grammar is proved to produce the reference's tokens before any C runs. It matches every node
  family — the character-level nodes, the repetitions, the parameter machinery that threads `n`/`m`/`c`/`t`/`r` and
  detects indentation, and the assertions and lookahead, including the ongoing `(exclude)` guard that stops a plain
  scalar at a document boundary — and produces tokens from the annotation nodes, giving a run its code, bracketing a
  match in `begin`/`end` markers, emitting a marker on its own, and writing an error token that names what was expected.
  It backtracks in the success-continuation style, re-entering an alternation when a later element fails as the
  reference does, and reproduces every fixture, `l-yeast-stream` and the malformed inputs included, token for token. A
  malformed input is where the grammar's `(cut)` earns its keep: a cut commits, and if the parse then fails, the
  interpreter emits an error token naming what the cut expected, closes the markers the abandoned parse left open, and
  hands the rest of the input to `l-recover` — the grammar's own recovery rule — which brings it back as unparsed. A
  failure that passed no cut is not an error but a production simply rejecting its input, reported where what matched
  ends.

- Error reporting lives in the grammar. Eighteen `(cut)` points mark where a parse commits, and an `(error)` is an error
  token the grammar writes where it already knows the parse cannot go on; each names a message in
  `grammar/messages.yaml` — the one source the interpreter reads and the C message table generates from, gated so the
  two cannot drift. `l-unparsed` is the recovery rule they hand the rest of the input to, bringing it back a line at a
  time as `YS_CODE_UNPARSED` content and `YS_CODE_UNPARSED_BREAK` breaks; it consumes anything, so it earns the
  decoder's twentieth character set, freed by moving the key's length field up into spare bits. The block header gained
  a lookahead so its two orderings no longer need the backtracking a cut would block — a declared deviation, the
  official header being ambiguous there.

- `l-yeast-stream` is the root the parser runs: a YAML stream, and then the end of the input. Every part of the spec's
  `l-yaml-stream` is optional, so on input that is no stream at all — a `]`, say — it matches nothing and would leave
  the whole of the input unaccounted for, silently; the root makes that an error and the input comes back unparsed, so
  every byte reaches the caller whatever it holds. Its second alternative always matches, so the first way
  `l-yaml-stream` finds is the one taken and nothing backtracks into it. It is libyeast's own, as `l-unparsed` is, and
  the spec's rule 211 is untouched — the official grammar still comes back from libyeast's rule for rule.

- The resume policy is the grammar's fifth parameter. `ys_options.resume` chooses what the parser does with input it
  cannot parse, and the grammar says what each choice means: `l-unparsed(n,r)` guards its own run, so under
  `YS_RESUME_DOCUMENT` it stops at the next `---` or `...` instead of eating the rest of the input, and `l-recover(n,r)`
  brings that run back and then parses the stream again from the marker. The two are mutually recursive, so a second
  error inside a resumed document needs no mechanism of its own, and a failed cut arrives at the same rule the root does
  — a cut says where the unwind lands and nothing more. `r` is finite, so it takes `c`'s and `t`'s fate rather than
  `n`'s: it specializes away at generation time, and the emitted C will be one automaton per policy with
  `ys_options.resume` choosing the start state. A fixture names the policy it runs under, `.r=d` beside the `.n`/`.c`/
  `.t` its filename already carries, and one that names none runs under the default a zeroed `ys_options` selects.

  The three policies are one hierarchy, each adding a place the run stops, so none gives up more of the input than the
  one before it: nothing, then `c-forbidden`, then `c-forbidden` or `s-indent-le-line(n)`. That last is
  `YS_RESUME_INDENT`, which carries on *inside* the document rather than at the next one: a malformed entry costs its
  container that entry and not the rest of them, and what is recovered is a sibling of what failed rather than a child
  of it. `c-forbidden` is not redundant there — `s-indent-le-line` cannot hold below an indentation of 0, so a run
  bounded by nothing would go straight past a document marker — and where nothing encloses the failure the indent guard
  is dead and the policy is exactly `YS_RESUME_DOCUMENT`. `le`, not `lt`: a sibling entry sits at exactly the
  container's indentation, so `lt` would skip the entries the policy exists to keep; and not `eq` either, since a
  container whose last entry is malformed would then eat the rest of the document hunting a sibling that never comes.
  `s-indent-le-line` is pinned by a lookahead for content, which forces the line's whole indentation to be measured and
  keeps a blank line and a comment line from being boundaries — neither has an indentation of its own to speak of. It is
  two lookaheads rather than the one character class `ns-char - c-comment` because a difference is a character set, and
  the decoder's key has twenty of those and no room for a twenty-first.

- `(recover)` says where a failed cut stops unwinding. A cut unwinds past every frame between it and whatever answers
  for it; this is a rule saying "that is me". The block collections wrap their entry in one, naming the `n+m` they have
  already computed, so no indentation is recovered from the runtime and the recovery reads the parameters of the rule
  that declares it rather than of whatever failed below it. The error is emitted, the markers the entry opened are
  closed down to that depth and no further, the run is given up, and the parse carries on as though the entry had
  matched — so the collection's own repetition takes its next turn, `s-indent(n+m)` matching where the next entry begins
  and failing where the collection ends, and nothing has to know which of the two the run stopped at. The node holds no
  policy: a rule reached under one that recovers elsewhere has no branch to take, so it does not match and the cut goes
  on unwinding, which is why the other two policies are byte-identical to what they were. Flow collections get none —
  recovery is by indentation and a flow node is one level of it, so there is nothing inside one to resume at.

- An error closes the markers it opened. A raise skipped the frames that would have emitted them, so a malformed
  document used to end with its `begin-` markers hanging: harmless while everything after an error was unparsed, and
  wrong the moment the parse resumes, because the next document then parses as a child of the one that failed rather
  than its sibling — and a caller reaching for the documents an error did not cost it would find them nested inside the
  wreck of the one that did. They are closed at the error now, after the error token and before the first `unparsed`:
  all three are zero-width at the byte that failed, so only their order says that the error is inside what failed and
  that the unparsed run is inside nothing. A `begin-` gets its `end-` on every path, which is what lets the fold that
  rebuilds the production tree stand on an errored stream at all.

- A grammar-coverage gate, `make verify-grammar-base-coverage` via `generator/check_grammar_coverage.py`: every
  production must be exercised by the fixtures, both ways. Coverage is dynamic, not by name — a production counts when
  running a reproducible fixture actually reaches it, so a production with no fixture of its own is covered by the
  fixtures that reach it, and one nothing reaches is a gap. It takes the grammar as an argument, so it re-runs on each
  structurally-transformed grammar as those arrive. The suite gained an empty stripped literal (`|-`) so the
  scalar-closing `end-block-scalar` is exercised by a clean fixture rather than only an error one.

  Reaching a rule is half of exercising it. A rule is a decision, and a fixture that only ever watches it say yes leaves
  the other answer untested, so each must also be seen to reject an input — by failing to match, or by a `(cut)` inside
  it raising, a rule holding a cut never returning "no". The exception is a rule that *cannot* say no, and those are
  computed rather than listed: totality is proved from the body's shape, so nothing asks for the fixture where
  `l-yaml-stream` fails, which every part of being optional makes impossible. That a rule can never say no is worth
  knowing anyway — it is exactly what let `l-yaml-stream` swallow a whole input before `l-yeast-stream` was written to
  say so. A `(cut)` is a decision too: one that never fires is a commit point nothing shows is reachable and a message
  nothing shows is right, so each must appear in some fixture's expected output — checked against the suite rather than
  by watching the interpreter, which is stricter, proving the error survived to be handed back where a cut raising
  inside a lookahead would prove only that it can raise. Ten fixtures close what this found: seven cuts that had never
  fired, and `c-reserved`/`ns-tag-prefix`/`ns-global-tag-prefix`, which no input had ever made refuse.

  The interpreter enters the top production as a reference to it, the way every other rule is entered, rather than by
  matching its body — a rule run at the top is still a rule, and the gate that watches references could not see it
  otherwise. That is what had hidden five of these: their fixtures existed and rejected all along.

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
