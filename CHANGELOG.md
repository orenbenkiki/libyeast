# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The ABI is unstable through `0.x`.

## [Unreleased]

### Added

- Project framework: CMake build (shared + static, hardened, symbol-visibility controlled), an incremental pre-commit
  `make` gate, tests (acutest), coverage with a `// UNTESTED` contract, Doxygen docs with a completeness gate, and a
  package-consumption test.

- Continuous integration: per-sub-gate GitHub Actions workflows — static quality, C tests, the generator pipeline, and
  the docs — with independent status badges and CodeQL analysis, and published API docs plus an HTML coverage report on
  GitHub Pages. Every one of them runs on a pull request as well as on `main`, so what `make pc` refuses locally cannot
  land remotely. The docs workflow did not, and it is where the `// UNTESTED` coverage contract and the docs
  completeness check live: a public symbol left undocumented, or an uncovered line left unannotated, failed only after
  it had merged. Its build runs on a pull request now and only its deploy is `main`'s.

- Version API: `ys_version`, `ys_major`, `ys_minor`, `ys_patch`.

- Token-source API surface: `ys_new_yaml_memory_parser` / `ys_new_yaml_stream_parser` / `ys_new_yeast_stream_reader`
  make a `ys_token_source`, `ys_read_token` pulls from it, and `ys_delete_token_source` releases it — with
  `ys_fd_reader`/`ys_fp_reader` reader adapters, a pluggable allocator, and the `ys_counting_allocator` leak counter.
  Tokens parsed from YAML and tokens replayed from a yeast wire are the same source to a caller, read the same way, so
  code over tokens does not know or care which made them: the two are a tagged union whose arms hold genuinely different
  state, with only the kind above them. `ys_read_token` fills the caller's token and returns a `ys_status` — `YS_OK`
  with a token, or a negative status with `errno` set — rather than repeating a halt token: a delegate failing is
  `YS_FAILED_STREAM` (the reader) or `YS_FAILED_MEMORY` (the allocator), while reading past the end is
  `YS_FAILED_ACTION`, the call failing on its own terms rather than a delegate's. A host failure ends the source there,
  with no `end-stream` to close the `begin-stream`, the missing close being the sign it did not finish. So the three
  codes a document is never the cause of — a parser running out of memory, its reader failing, and the wire reader's own
  trouble — leave the token model, and `YS_CODE_ERROR` (a malformed document, or a malformed wire, which is bad data
  like a bad document) is the sole `!` the wire writes. The parser core is not implemented yet, so a parser's
  `ys_read_token` returns a "not implemented" error.

- Token-sink API surface, the mirror: `ys_new_yeast_stream_writer` makes a `ys_token_sink` over a `ys_bytes_writer`,
  `ys_write_token` feeds it, and `ys_delete_token_sink` releases it — so a token stream is sent onward the same way
  whatever its destination. Two arms: the yeast writer serializes tokens to a wire, and `ys_new_yaml_stream_emitter`
  writes the bytes each token spans, so a wire replayed through the emitter reconstructs the YAML it came from — the
  round-trip the wire exists for, tested over the whole fixture corpus. `ys_write_token` returns a `ys_status`: `YS_OK`,
  `YS_FAILED_STREAM` if the byte transport failed, or `YS_FAILED_ACTION` for a token that cannot be written — a code the
  wire spells nothing for, text that lies about its code, or a `YS_CODE_ERROR` handed to the emitter, which renders
  rather than judges and so refuses one for a caller to filter above. It moved from a `ys_bytes_writer` to the sink; the
  byte transport stays underneath, as a `ys_bytes_reader` does for a source. `ys_delete_token_sink` replaces
  `ys_close_writer`, and its flush is where a buffered write finally fails. The `ys_status` failure names dropped their
  direction — `YS_FAILED_STREAM` for a reader or a writer, `YS_FAILED_MEMORY` for the allocator — since a source reads
  and a sink writes but both close a transport the same way.

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

- A byte order mark takes no column. It is no character of the line — it does not end the line's start, and now it does
  not advance the column either — so a token that follows one on the same line reports the column it would have had
  without it, and `l-document-prefix.bom-comment`'s `#` is at column 0 rather than 1. What reads a column is what a
  construct is indented by, and a mark standing before the indentation is not part of it.

- `<column>`, a special rule beside `<empty>` and `<start-of-line>`: the column the parse stands at, counted from zero.
  What a construct is indented by, read where that indentation has just been consumed rather than worked out by looking
  ahead for it.

  A compact collection is the first to be written that way. `s-l+block-indented` took the spaces after its `-` or `?` as
  a peek — `<auto-detect-in-line-indent>`, at least one — and then consumed exactly that many and entered the collection
  at `n+1+m`. It takes the run as its own indent token now and enters at `<column>`, which is that same value: the run
  begins one past an indicator at column `n`, so where it leaves the parse is what the collection is indented by. A run
  over a character class is possessive and gives nothing back, so peeking a length and then consuming it is the parse
  consuming it. The value needs no name — both compact alternatives are tried at the same position — and naming it `n`
  would have been wrong: a parameter passed as itself is passed by reference, so the write would have escaped into
  `c-l-block-seq-entry` and from there into the enclosing sequence's own loop, which
  `l-yeast-stream.seq-dedent-multilevel` catches.

  `<auto-detect-in-line-indent>` is retired with the peek it served, that rule having been its only site.

  The block collections follow. `l+block-sequence` and `l+block-mapping` looked ahead for the first line holding
  something other than a space — past the current line where the parse stood mid-line, past however many empty lines
  followed, unbounded — and measured every entry against what they found. The parse is already standing at the start of
  the line the first entry is on, so that line's own run of spaces is taken as the indent token and `<column>` is the
  `n+m` the entries are measured against, which must be deeper than the indentation in force. The entries then run at
  it: `l-block-seq-entries` and `l-block-map-entries` are libyeast's own, taking that indentation as their `n`, the
  first entry having had its consumed already and every later one beginning with `s-indent(n)` — which is what ends the
  collection where a line is indented less. Passed as an argument rather than written: a parameter passed as itself is
  passed by reference, and `s-l+block-collection` calls the mapping with a bare `n` where the sequence goes through
  `seq-spaces`, so a write would have escaped one of the two.

  With the collections answering for themselves, the pipeline step that had been doing it — hoisting each loop into a
  production entered at what it measured — has nothing left to find, and a step that finds nothing is a fault by the
  pipeline's own rule. It is gone, and the phase is `clear-m` and `read-global-m`.

  The block scalar is the last of them and the hardest: its indicator decides, and its first content line, which is what
  the decision is about, is three calls away. So the decision travels as `i` — `given` where the indicator named the
  indentation, `detected` where it did not — carried by the content rules to `s-indent-floor`, where the two differ by
  one node: exactly `n` spaces, or one span with the guards on what it measured, which is also where `n` is established.
  It is a finite parameter, so `monomorphize` specializes it into the names as it does the context and the generated
  parser tests no mode; `lift-chomping` becomes `lift-setters` and inverts both setters, and Phase 0 settles
  `no-i-t-parameters`.

  Where the scalar has no content line at all, nothing establishes an indentation and the trailing empty lines are what
  would have said it — so each is taken whole and the widest of them is the floor the trailing comment is measured
  against. That matters for `keep`, where those lines are the scalar's own content and the indentation decides which of
  them belong to it: the YAML Test Suite's `JEF9/01`, `- |+` and a three-space line, is what says so.

  Nothing libyeast runs reads ahead of what it has consumed now. `<auto-detect-indent>` has no site, the interpreter
  evaluates none, and the IR node stays only because the vendored grammar spells it and one reader loads them both.

- `check_normalize` carries a crash back as a failure. It runs its check in a thread for the stack depth the
  interpreter's recursion wants, and caught only the `SystemExit` the gate reports through — so any other exception was
  printed by the thread's own excepthook while the main thread exited zero. A green `make pc` over a check that never
  finished, and it took a step going idle to notice.

- A fixture may say where its input starts: `o=N` in the name, beside `n`, `c`, `t` and `r`. A rule entered in the
  middle of a line — a compact collection just past its `-` — is measured against the column it stands at, which a run
  starting at column zero cannot say, so `s-l+block-indented`'s fixtures name `o=3` beside their `n=2`. It feeds what
  the grammar measures against and leaves the emitted marks alone: a fixture's input is a slice of a line, and its
  bytes, characters and lines already count from its own start.

- Grammar normalization: an ordered pipeline of semantics-preserving grammar-to-grammar transformations that carry the
  hand-authored grammar toward the canonical form a state machine falls out of — each terminal a character set, each run
  a repetition of one. It is built one goal at a time: the pipeline is a sequence of phases, each owning one invariant,
  and a phase adds steps until that count is none. From a phase's end the invariant is enforced — the law's "none stays
  none" makes every later step keep it — and a phase finished with a green corpus is a checkpoint that lands on its own.

  A step is `Step(name, transform, invariants, reduces, lapses)`. An invariant is a count and not a yes-or-no: its test
  gives back the places the grammar breaks it, so the length is the count and the contents say where. A step naming one
  is taken to finish it, that being what a step is for, and `reduces` names the ones among them it only lowers.
  `invariant_faults` holds every step to the law — a count never rises, a settling step leaves none, and none stays none
  — and `lapses` is the only licence to break it, `{invariant: reason}`; one naming an invariant no step carries, or one
  the step does not actually break, is a stale declaration and a fault. `untested_steps` counts the steps promising what
  nothing checks, each naming either an invariant or the reason it can have none, and `standing_invariants` counts what
  the final grammar still breaks whatever the steps settle between them.

  Phase 0 is the two parameters the grammar sets by matching: the chomping `t` and the block scalar's indentation mode
  `i`. Each is set by an indicator and read productions later through the environment, so a read of either means nothing
  until a caller is known. `lift-setters` inverts each setter into a `(case)` on its parameter matching the condition
  for a given value, and turns each production holding one as a local out-parameter into an ordered choice over its
  values — the block scalar becoming a choice over strip/keep/clip and over given/detected alike, every branch fixing
  the parameter to a literal it hands the setter and the reader both. `monomorphize` then specializes all four finite
  parameters — the context `c`, the now-lexical `t` and `i`, and the resume policy `r` — away: every production it
  reaches copied once per combination of their values, its `(case)`/`(flip)` on them evaluated to the copy's, and the
  values fixed into the copy's name (`ns-plain-char_c_flow-in`) rather than passed, following references from the root's
  copy under each resume policy — the machine's start states — so only combinations that occur are made; a value at its
  default (the no-resume `r`) is not in the name, so the root stays `l-yeast-stream`, and the recovery re-enters the
  copy the resume policy names. Only the integers `n`, `m` and `f` stay parameters. It rests on a rule the grammar
  keeps: a finite parameter is only ever switched on, so where an implicit key's commit softens by context — a key that
  will not parse being simply not this key — the grammar says so in a `(case) c`, its key branches the bare item and its
  `else` the commit, and the parser's `(commit)` is the same hard cut everywhere. `(case)` grew that `else` for it.
  Nothing declares, passes or reads `t` or `i` from there on.

  Phase 1 is the character questions. A set of characters is written many ways and asked in several — a character, a
  range, a union of them, a base with exclusions, a reference, the item a lookaround peeks — and all of them come to the
  one bit the parser tests. `lower-char-sets` says each as the sorted disjoint codepoint intervals it denotes, so from
  its end every question about a character is a `CharSet` or a literal. A maximal one is taken, not every one inside it,
  the intervals of a union being its own; a reference a match takes is left standing, being the caller's hold on the
  production where the set is said, and inside a lookaround it is read through, what a peek holds being the question
  rather than the hold. The phase follows the specialization, a set the context picks denoting nothing until a caller is
  known. Saying it once also makes the spelling canonical, which is what lets the sweep do its own work: two productions
  denoting the same characters differently are structurally unequal and do not merge, the merge reading shape rather
  than extension.

  Phase 2 is the block scalar's leading-empty floor. `clear-f` gives the value an end — the production that reads it,
  `s-indent-floor`, clears it where it returns, the reader and not the writer, since the floor is measured deep inside
  the leading empties and handed up to the one thing that asks about it — and `read-global-f` takes the declaration off
  every production and the argument off every call, each read becoming a `Global`. The reads hide where the generic
  walker does not go: it carries a `Param` as a value and never visits one a field holds directly, so `s-indent-floor`'s
  `Le(f, n)` is a read both the count and the rewrite had to walk the fields themselves to see. What licenses the drop
  is that the value does not nest, and the interpreter says so rather than the argument: a `(set)` puts it on a stack of
  that global's own, a `(clear)` takes it off, the single slot stands beside it, and the reads where the two differ are
  counted over the whole corpus. The gate holds that at none — and made to nest, the same net reports 21.

  Phase 3 is the detected indent, and `clear-m` and `read-global-m` do what `f`'s pair did. What made it possible is
  that nothing reads the value twice over a region something else can write in: the block header measures it and the
  scalar that asked reads it, one construct at a time. A block collection did read it on every turn of its loop, which
  is a value no single slot can hold — every collection or block scalar the loop entered detected one of its own in
  between — and the count of reads a slot could not have answered stood at 843 for exactly that reason. It is the
  grammar that answers for it now, each collection entering its entries at the indentation the first of them
  established, so the count stands at none. `Bind` maintains the stack beside the slot too — a write is a write however
  it is spelled, and a block header's indicator sets the detected indent through one, so a global written that way had
  been invisible to the net.

  Phase 4 is the indentation, and it is not one of the parse's own values: a nested collection's entries are measured
  against their own, so it goes on the parse's stack rather than into a slot. `push-indents` puts a push before every
  call measured against an indentation other than the one in force and a pop behind it — both halves in one way of one
  production, the level being known nowhere else — and `read-indents` then takes the parameter off every declaration,
  every call and every read, leaving the stack the one place it is. What says the pushes stand where they should is the
  parameter staying beside them while they go in: the interpreter compares the two at every read of `n` over the whole
  corpus, and only then does the parameter go. Skew every push by one and it refuses ten fixtures, so the agreement is a
  check rather than a coincidence.

  `hold-established-indents` goes first, for the one indentation a call hands back rather than is entered under. A block
  scalar cannot know what its content is indented by until its first content line is read, so that line measures it and
  the value travels out through the calls that passed the parameter itself — a write whose readers are a production
  away, which the parameter's removal would silently take apart, and which nothing local can check. The chain is inlined
  until the write and what reads it are one way, and the write is then the push that way ends by taking back. With it in
  front, `push-indents` has one rule instead of two.

  Two of the pushes' levels were wrong in a way only the corpus could say. A pop that names the level it takes off
  re-evaluates that expression when it runs, and `<column>` or `n+1` means something else by then — so a pop takes what
  is on top and the pairing is what the stack's own kinds refuse. And working out what to push is not a read of the
  indentation in force: where the level is the parameter itself the two differ there and nowhere else, which is the same
  exemption the arguments of a call already had.

  Phase 5 is the empties, and `lower-optionals` is its first step: `x?` becomes `x | <empty>`, the empty way standing
  beside the one that reads rather than hidden inside a node. It is the same match and the interpreter says so — an
  `Opt` tries its item with the continuation behind it and, where that fails, rewinds and takes the continuation alone,
  which is that alternation tried in that order. So nothing has to be known about what follows.

  A run over a character class is a scan and not a way, and the interpreter now draws that line where the grammar does.
  Such a run is single-outcome by construction — only ever followed by something off its own set — so it is taken whole
  and judged whole, which is what `s-indent-le`'s "the maximal run, and then its length against `n`" needs: falling back
  to a shorter run would let an over-indented line pass as if it had none. A run over a *way* is no such thing. It is a
  choice between the maximal run and none at all, with no count between them, which is exactly what an ordered
  `x+ | <empty>` offers. That distinction was argued for character classes when repetitions became possessive and never
  drawn for ways; drawn, it makes the two lowerings below identities rather than arguments.

  `span-consumes` writes a run over a character class as the one scan it is — `x*` a `ConsumeSpan`, `x+` the character
  and that span behind it, 56 in all — and `lower-stars` writes what is left as `x+ | <empty>`, 62 of them, settling
  `no-star-nodes`. Distributed, `P x* Q` becomes `P x+ Q | P Q`, and what decides between them is the character the run
  begins with: in `x`'s set the parse takes the run, outside it the way that does not. Which is the shape the machine
  wants, and the reason the empty match is worth making a way of.

  `mint-consuming-and-residue` gives every production that may match empty a name for each of the two things it is —
  `<name>_reads` for the ways that take a character, `<name>_empty` for the ways that take none — and the production
  becomes the choice between them, 67 of them split and the grammar 100 productions wider for it. A caller entering one
  was choosing blind, and the choice cannot be put on a character while both answers live under a single name. The
  residue gets a name rather than an inlined tree, so nothing has to be worked out bottom-up: a body's parts split by
  what their own names already say. `every-empty-match-is-a-way` goes from 57 to none.

  It is the same match in the same order. A sequence's ways come out as its parts already offer them — `a b` reading is
  `a_reads b` and then `a_empty b_reads`, which enumerates exactly as `a b` does — and no alternation in the grammar has
  an empty way ahead of a reading one, so nothing is reordered. Four shapes cannot say the two apart as they stand and
  are said differently instead. A possessive scan takes nothing exactly where its set is not there, so its empty way is
  that negative peek — one character each, which is the question a gate already asks, and eleven more of them standing
  once the duplicates merge. A counted repetition whose count the parse works out matches nothing where that count is
  not positive, so its ways are told apart by the count: the reading one says the turn it takes rather than leaning on
  the count that admitted it, which is what `s-indent`'s indentation of none needed. A run over an item that may take
  nothing ends on a turn that takes none, which the interpreter keeps once — and every such turn in this grammar leaves
  nothing behind, checked rather than assumed, so the reading way is the reading turns and the empty way is the turn
  that took none. And a commit is the error where its item cannot match, so splitting it in place would make the reading
  way's failure that error instead of a step on the way to the empty way; where everything before it takes no character
  and always matches, `A (commit m: X)` and `(commit m: A X)` are the same match, so it is lifted over the choice and
  one message scope stands around both ways.

  Seventeen fixtures were added rather than crediting the new names from the ones they were split off. The coverage gate
  would have held each covered by its base, as it does a monomorphic copy, but a base's coverage cannot say which of the
  two ways an input took — so the corpus was made to take both: an empty and a non-empty single-quoted scalar in a block
  key and in flow, a double-quoted one that is empty and one that is nothing but a space, the chomped last line at end
  of stream under each chomping, a block header at end of file, a kept block scalar with and without trailing empty
  lines, a folded line at the leading-empty floor, an error recovered behind an indented line, and one recovered at end
  of input, where the recovery has nothing to give up.

  `distribute-residues` writes that choice where the caller stands rather than behind the production's own name:
  `A ::= F (X_reads | X_empty)`, at 205 call sites, taking `no-call-enters-both-ways` from 205 to none and the grammar
  55 productions narrower for the names it no longer needs. Naming the two ways was half of it — while the choice sits
  behind one name a caller still enters without knowing whether anything will be taken, and there is nowhere to put a
  gate. The way around it is not split to do that: `A ::= F X_reads | F` would run `F` twice, where one alternation
  inside the sequence duplicates nothing. One pass and no iteration, since what such a production's body holds is the
  two calls and nothing else. The root and the recovery are left whole and the five calls between them are no part of
  the count: a parse enters both by name, so nobody chooses to enter one, and each being two things would make the other
  a choice on nothing at all.

  The coverage gate reads `_reads` and `_empty` as the minted-helper suffixes they are, so a form credits the base it
  was split off as a monomorphic copy already does — which its own note had anticipated as "the base held to the matches
  that consume". The credit is the floor rather than the ceiling: the corpus reaches both ways of every split production
  in its own right, and what it answers for is a base like `l-recover-entry`, a resume policy that declines and so
  matches nowhere, which no fixture could reach before it was split either.

  `dissolve-residues` writes what is left taking no character into the call sites that enter it — 50 productions at 329
  call sites, `e-node` at twenty-six of them — and Phase 5 is finished: `only-root-empties` goes from 50 to none, and
  the six productions still matching empty are the root and the recovery under each resume policy, which a parse enters
  by name. Once the two ways are told apart, what still matches empty is what only ever took nothing: the residue a
  split named, and the five that were actions alone. A name is worth having where it stands for a decision, and there is
  none in a way that consumes nothing and always ends where it began. Each is written out before it is written in, a
  residue holding calls of others; one reaching itself would be a match of nothing at all rather than a match of
  nothing, and the grammar has none.

  The phase leaves 328 productions where it found 333, having gone as wide as 433 in between. No caller now chooses
  whether to enter something that may take nothing — every empty match is a way of the caller's own, where a character
  can decide it.

  Every step's grammar is swept of what the step leaves behind, the three passes running to a fixpoint since each feeds
  the others. A production whose whole body is one ungated, action-free call is what it calls, so every reference to it
  becomes a reference to that callee. Productions that behave alike are spelled once: same parameters, and the same body
  once every reference in it is read as the group of what it names rather than by the name itself, which is what tells
  two loops apart from one loop written twice. And last, so it sees what the other two strand, every production no parse
  can enter is purged. None of the three changes what the grammar matches or emits. Only a merge is a rename, and only a
  merge is followed by a point of interest — a name a later step speaks of, tracked beside the grammar rather than in
  it, re-picked among what its holders became, a holder lost without successor being a loud fault rather than an
  absorbed drift.

  A fixture the sweep strands is not dropped: it pins to the last stage whose grammar can run it, guards that grammar
  token for token, and credits coverage from where it stands. The coverage gate holds a minted helper covered by the
  base it came from, as it does a monomorphic copy: a helper is a piece of the base's own body moved, so requiring more
  of it than of the body it came from would ask the corpus for what the untransformed grammar never needed.
  `check_normalize` holds every step token-and-event identical over the whole corpus — 711 conformance fixtures and 402
  YAML Test Suite cases, seven of them pinning the document-marker boundary the spec's `c-forbidden` spells and the
  Clojure reference agrees on: `---foo`, `---#foo`, `----` and their `...` kin are content, `--- foo` a boundary,
  `... foo` malformed; four pinning the sequence dedent hand-off any committed block structure must reproduce — at a
  dedent each exiting level's end markers stand before the dedent line's indent token, the owner level continues past
  it, and a zero-column dedent carries no indent token at all; and two pinning the sequence entry's committed dash, the
  Clojure reference agreeing both are errors — `- @` is an entry whose body fails inside the entry's own committed
  region, where `-b` is refused at the gate, the sequence closing before a stream-level error. Every step must change
  the grammar, and one that does not is a fault named where it stands: a step goes idle when what it looks for has
  stopped reaching it, which is a regression in the step before it rather than a step to leave standing. The check reads
  a transform's own output, before the sweep, so a step is judged on what it did rather than on what the sweep did after
  it.

- `(match)` is the text of the open run — the token the rule is building — and the `(<<<)` origin it used to be measured
  from is gone with the operator, along with `OpenMatch`, `CloseMatch`, the `match_start` parameter and the
  `lower-bounds` step. An indentation is the length of the indent token the rule builds, which is what `s-indent-lt` and
  `s-indent-le` read; the block header's indicator is `(atoi)` of the one digit it just consumed, `(ord)` having been a
  single-character operator where this is a whole string. Two grammars justify the reading differently and each is held
  to its own: here a `(match)` must stand inside a `(token)` it is the whole of, and not read a run a nested `(token)`
  has cut; in the official grammar, which carries no token annotations at all, every `(match)` it reads must be one
  libyeast reads in the same production — `s-indent-lt`, `s-indent-le` and `c-indentation-indicator`, and no other. The
  official spellings are read into this vocabulary where the comparison needs them: `(<<<)` wraps a repetition that
  matches possessively here and so hands back nothing already, and `(ord)` is applied to a single digit.

- Decoder ABI: `ys_span_trim_sets` scans two character sets in one forward pass — the whole run under `full`, and how
  far the last character not in `trim` reached — returning a `ys_trim` of the `span` kept and the given-back `trim` run
  after it. It is what a plain or a quoted scalar's line compiles to: its inner spaces kept, its trailing ones handed to
  the caller as that caller's own `s-white*`, the input scanned but once. The generated parser does not call it yet.

- Indentation detection, which the official grammar declares a "special rule" and never defines, and which it elsewhere
  leaves as an integer added to the string `"auto-detect"`. libyeast defines it, and its two departures from the
  official grammar are declared, with their reasons: `m` is an indentation now, and `s-l+block-indented` sets the `m` it
  had been reading and never setting.

- The yeast wire format: `ys_write_token` writes a token stream — a character and its escaped text per token — and
  `ys_read_token` reads one back, so a stream can be piped between tools, stored, or compared against another parser's.
  `ys_bytes_writer` mirrors `ys_bytes_reader`, with the same file-descriptor and `FILE *` adapters. An escape spells a
  codepoint under every code but `YS_CODE_UNPARSED_INVALID`, and a byte under that one, so each holds the other's text
  to being what it claims: writing `\x80` for a raw `0x80` under a code that means codepoints says U+0080 and reads back
  as two bytes that were never given, and `YS_CODE_UNPARSED_INVALID` exists to carry exactly the bytes that encode no
  character — so its text must encode none of them, every byte of it a place where none begins, and every other code's
  must encode them all. `ys_write_token` holds both halves and answers `EINVAL`, which the errno policy already promised
  it would for a bad argument and which it had never once checked. The validation is `decoder.c`'s, which had it all
  along: `ys_codepoint` assembled continuation bits without ever asking whether they were continuation bytes, and
  silently turned `"\xE0ab"` into different bytes. `YS_CODE_UNPARSED` is `YS_CODE_UNPARSED_TEXT` now, so the three say
  what they are together. The reader's search for a line's break resumes where the last one gave up rather than starting
  over, so a line arriving in pieces costs its length and not its length squared — 16MB on one line took 2.17s and takes
  0.09s. A wire read from a pipe is what the format is for and is exactly what arrives in pieces, and `max_bytes` is
  unlimited by default, so the cost was a denial of service against the format's own purpose. The reader also refuses a
  position a token cannot start at — one it can read, but whose own text carries the end of it past where counting stops
  and back around, so that a caller comparing or slicing the two marks would be handed a span running backwards. It is
  the same fault as a position too large to read at all, found one step later, and says so. A code the wire spells
  nothing for is the last of the bad arguments it took: it wrote the code character out unchecked, so a code with no
  wire character wrote a line no reader could read back, and `EINVAL` covers it now. `ys_code_char` answers `'\0'` there
  rather than `'?'`, which was safe only for as long as nothing claimed `?` as a code. Nothing can claim `'\0'`: a line
  is NUL-terminated, so a code written as one would read back as an empty line, and `check_wire.py` holds every
  character in the table to being printable, which is what a wire being text meant all along. Every code the enum names
  has a character now, so this answers only an out-of-range code — a caller's mistake, not a code the library ever
  produces.

- Ill-formed UTF-8 is settled in the grammar, against fixtures, before any C parser exists to get it wrong. The
  reference interpreter reads its input a character at a time out of the bytes — a byte that begins no character a value
  of its own, `<invalid>`, rather than an exception thrown before the parse starts — so a token's text is the input
  bytes as they are, and a fixture can be written for ill-formed input at last. Recovery's `l-unparsed` interleaves runs
  of such bytes as `YS_CODE_UNPARSED_INVALID` tokens among the `unparsed-text` and the breaks, each a maximal run ending
  where valid UTF-8 resumes or at the end of the input. On the wire that token's `\xXX` is a raw byte, not the codepoint
  it would be under any other code, and the reader holds a `~` token to being ill-formed throughout — a valid character
  anywhere among its bytes is a malformed wire — the mirror of the writer, which already refused to spell one.

- Errors tell the caller what to do about them. A malformed document — or a malformed wire, bad data like a bad document
  — is `YS_CODE_ERROR`, its text the message and its wire character `!`. A host failure that is not the data's fault,
  running out of memory or a reader failing, is `ys_read_token`'s return value, a `ys_status`, not a token: it ends the
  source for good, the input to be read again by a new source with a larger cap. What the parser does with the input
  after a malformed document is `ys_options.resume`: by default the error ends the parse and the rest of the input comes
  back as `YS_CODE_UNPARSED` tokens, which is what YamlReference does, so the two token streams stay comparable on every
  input, valid or not. `YS_RESUME_DOCUMENT` instead carries on at the next document, so that one malformed document in a
  stream does not cost the caller the others; `YS_RESUME_INDENT` carries on at the next line no more indented than the
  entry that failed, inside the document, so that a malformed entry does not cost the caller the rest of its container
  either. Each gives up less of the input than the one before it, and where a policy has nothing to resume at it is the
  one before it — at the price that only the input before the first error stays comparable with YamlReference. A skipped
  line is two tokens, its content a `YS_CODE_UNPARSED` and its break a `YS_CODE_UNPARSED_BREAK` — the break its own
  code, since it is not a structural break the parser found.

- Parser state: the window over the input, the stack of productions the parser is inside, the queue of tokens it has
  built but not handed back, and the state it is in — the whole of it in one struct, none of it in the C call stack,
  which is what lets `ys_read_token` hand back a token from the middle of a production and resume there. The queue holds
  a run of undecided tokens, whose codes are rewritten and ahead of which a marker is injected when the parser learns
  what they were, and the stack carries the grammar's one runtime parameter, `n`. The automaton that drives them is not
  generated yet, so `ys_read_token` still returns a "not implemented" error.

- A conformance suite, `tests/spec/`. It was built once from YamlReference's vendored `tests/` — the fixtures that align
  with libyeast's grammar, each expected output turned into what libyeast emits rather than what YamlReference does: a
  production libyeast flattens to a character class becomes plain unparsed, no token spans a line, a byte-order mark is
  the character it matched and not YamlReference's encoding name, an error keeps its position but not its wording,
  YamlReference's isolated-run commit artifacts are dropped, and where YamlReference itself departs from the spec (the
  plain-scalar `:`/`#` factoring) libyeast follows the spec. Fixtures in encodings libyeast does not read, or for
  YamlReference's own internal productions, are left out. From there the fixtures are libyeast's to own — the one-time
  build is not kept; `generator/check_spec_tests.py` keeps them intact, every input paired, every name a production the
  grammar still has, every output a token stream whose marks chain and whose markers balance — a fixture of the root
  being a whole parse, which must balance exactly, where one of a rule run by itself may close what its caller would
  have opened but may still not leave a marker open. That last is what `check_markers` cannot reach: it settles the
  grammar's clean paths and says nothing about what an error leaves behind, which is where both of the imbalances found
  so far have been. A fixture whose name calls its input invalid must have one: the production either refuses it or
  stops short of its end, never matching the whole of it cleanly — the name being a claim, and an unchecked claim being
  how `c-printable.invalid` came to hold a character `c-printable` accepts. The bytes are held verbatim, CR and CRLF
  included, out of line-ending normalization.

- A reference interpreter of the grammar, `generator/interpreter.py`: a slow, obviously-correct backtracking matcher
  that runs a production against an input and emits its yeast tokens, checked fixture by fixture against the conformance
  suite so libyeast's grammar is proved to produce YamlReference's tokens before any C runs. It matches every node
  family — the character-level nodes, the repetitions, the parameter machinery that threads `n`/`m`/`c`/`t`/`r`/`f` and
  detects indentation, and the assertions and lookahead, including the ongoing `(exclude)` guard that stops a plain
  scalar at a document boundary — and produces tokens from the annotation nodes, giving a run its code, bracketing a
  match in `begin`/`end` markers, emitting a marker on its own, and writing an error token that names what was expected.
  It backtracks in the success-continuation style, re-entering an alternation when a later element fails as the
  reference does, and reproduces every fixture, `l-yeast-stream` and the malformed inputs included, token for token. All
  of that rests on one promise the emitter makes and nothing checked — that a checkpoint captures the whole of the
  state, so an alternative that fails can be undone — and `make verify-emitter` now checks it: every field is restored,
  and restored the same way twice, an alternation rewinding to one checkpoint once per branch. It was not true. A
  checkpoint handed out its parameters rather than a copy of them, so a discarded branch's `(set)` reached into what the
  branch after it rewound to; no fixture could see it, the grammar's only three sites setting the same parameter in
  every branch of the alternation, so whatever leaked was overwritten by the branch that matched. A malformed input is
  where the grammar's `(cut)` earns its keep: a cut commits, and if the parse then fails, the interpreter emits an error
  token naming what the cut expected, closes the markers the abandoned parse left open, and hands the rest of the input
  to `l-recover` — the grammar's own recovery rule — which brings it back as unparsed. A failure that passed no cut is
  not an error but a production simply rejecting its input, reported where what matched ends.

- Error reporting lives in the grammar. Twenty `(cut)` points mark where a parse commits, and an `(error)` is an error
  token the grammar writes where it already knows the parse cannot go on; each names a message in
  `grammar/messages.yaml` — the one source the interpreter reads and the C message table generates from, gated so the
  two cannot drift. `l-unparsed` is the recovery rule they hand the rest of the input to, bringing it back a line at a
  time as `YS_CODE_UNPARSED` content and `YS_CODE_UNPARSED_BREAK` breaks; it consumes anything, so it earns the
  decoder's twentieth character set, freed by moving the key's length field up into spare bits. The block header gained
  a lookahead so its two orderings no longer need the backtracking a cut would block — a declared deviation, the
  official header being ambiguous there. An anchor commits after its `&` as an alias does after its `*`: both are
  indicators, so neither can begin anything else where a node's properties may start, and `&` with no name is a mistake
  rather than a rule declining to match. A message says what its own cut expects and no more — a `...` marker requires a
  comment or a line break, which is what its cut guards, where it had claimed a new document was required after it and a
  stream of nothing but `...` has always been valid.

- A block scalar's leading empty lines are held to the spec's prose §8.1.1.1: none may out-indent the first content
  line. The reference and the BNF read such a line as content — its extra spaces fall through `l-empty(n)`'s cap into
  `s-indent(n) nb-char+`, a space being an `nb-char` — where libyeast makes it an error, the divergence declared in
  `check_vendor_spec`. A forward parser with no lookahead cannot know the floor is broken until the content line
  arrives, so that is where it speaks: `l-leading-empties` emits every leading empty whatever its indentation and raises
  `f`, a new runtime floor parameter, to the widest of them with the `(increase)` action — `f = max(f, column)`, made
  explicit rather than magic so the structural transformation has less to infer; then `s-indent-floor` takes the content
  line's own indentation and, only after it, an under-indent error keyed `BLOCK_SCALAR_UNDER_INDENT`. Reporting it there
  — as this line being under-indented rather than a past empty line being over-indented — is also what tells an empty
  scalar, whose content line never comes, from a violating one: there the indentation match fails before the cut and the
  scalar ends with no error. The literal and folded styles share the mechanism, the folded fork into
  folded-versus-spaced lines drawn only after the shared indentation is taken so the floor is checked once; fixtures
  enforce both.

- An implicit mapping key is held to the spec's §7.4.2 bound: a parser resolving whether a `:` makes the entry a key
  must see it within 1024 characters. The official grammar writes this as `(max): 1024` before the key production, a
  length note it never enforces; libyeast makes `(max)` a wrapping window — `(max): [1024, IMPLICIT_KEY_TOO_LONG, key]`
  around the production — that the interpreter runs. The window is the deterministic parser's bounded lookahead:
  matching the key, but no further than 1024 characters, taking the interpreter's own unbounded lookahead to get there
  and then keeping only what fit. A key that runs past the limit is an error, `IMPLICIT_KEY_TOO_LONG`, and unparsed from
  there — the tokens up to exactly the 1024th character emitted first, the run cut where the limit falls so a token
  split across it comes back as its own code, then the error, as a failed cut leaves things. Recovering the official
  grammar undoes the wrapping back to the preceding `(max): 1024`, so `check_vendor_spec` still reads it rule for rule
  with no divergence declared. The single line the key is also restricted to needs no window: the flow-key context
  already binds a key's separation to `s-separate-in-line` and its scalars to one line, so no break is ever consumed
  inside a key — fixtures pin the limit falling inside a token and on the boundary between two, and a flow-collection
  key that a break would carry onto a second line failing as an unterminated flow collection.

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

- `(recover)` says where a failed cut stops unwinding. A cut unwinds past every call between it and whatever answers for
  it; this is a rule saying "that is me". The block collections wrap their entry in one, naming the `n+m` they have
  already computed, so no indentation is recovered from the runtime and the recovery reads the parameters of the rule
  that declares it rather than of whatever failed below it. The error is emitted, the markers the entry opened are
  closed down to that depth and no further, the run is given up, and the parse carries on as though the entry had
  matched — so the collection's own repetition takes its next turn, `s-indent(n+m)` matching where the next entry begins
  and failing where the collection ends, and nothing has to know which of the two the run stopped at. The node holds no
  policy: a rule reached under one that recovers elsewhere has no branch to take, so it does not match and the cut goes
  on unwinding, which is why the other two policies are byte-identical to what they were. Flow collections get none —
  recovery is by indentation and a flow node is one level of it, so there is nothing inside one to resume at.

- An error closes the markers it opened. A raise skipped the returns that would have emitted them, so a malformed
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
  nothing shows is right, so each must appear in some fixture's expected output — checked against the fixtures rather
  than by watching the interpreter, which is stricter, proving the error survived to be handed back where a cut raising
  inside a lookahead would prove only that it can raise. Ten fixtures close what this found: seven cuts that had never
  fired, and `c-reserved`/`ns-tag-prefix`/`ns-global-tag-prefix`, which no input had ever made refuse.

  The interpreter enters the top production as a reference to it, the way every other rule is entered, rather than by
  matching its body — a rule run at the top is still a rule, and the gate that watches references could not see it
  otherwise. That is what had hidden five of these: their fixtures existed and rejected all along.

- The YAML Test Suite, folded to events — the independent net, `generator/star.py`, gated by `make verify-star`.
  Vendored under `third_party/yaml-test-suite/` and written by other hands from the same spec, it catches a grammar bug
  libyeast's own fixtures, migrated from YamlReference, would share. libyeast is a token parser, a level below events,
  so the check is a deterministic fold: the yeast stream's `begin-`/`end-` markers rebuild the production tree and its
  leaf tokens fill it, projected to the events it states — `+STR`/`+DOC`/`+MAP`/`+SEQ`/`=VAL`/`=ALI` — with node and
  pair brackets and all presentation dropped, a scalar's value read off the codes the parser settled (a `line-fold` a
  space, a `line-feed` a newline, an escape resolved) with nothing stripped, since the tokens already separate content
  from whitespace, and a tag resolved through the document's `%TAG` directives over the default `!`/`!!` with its `%XX`
  URI escapes decoded. A valid case must fold to its `test.event`; an error case must come back a rejection — the fold
  reporting even the two resolution errors a token stream cannot show, a named tag handle no `%TAG` defines and a
  repeated `%YAML`. All 402 cases hold green-or-declared against the HTML spec, the source of truth. The one case
  libyeast declines to match is `JEF9/02`: an empty kept block scalar whose input ends in no line break, which YAMLStar
  loads by first appending the break, so the YAML Test Suite expects the line feed that break yields. The spec appends
  nothing — end-of-input is a line break only in `b-chomped-last`, which an all-empty scalar never reaches — so libyeast
  folds it to the empty scalar and declares the divergence from the suite.

  The net earned its keep, five corrections across the grammar and the interpreter that libyeast's own fixtures had
  agreed with. A quoted scalar or flow collection at a document's top or as a block-sequence entry is first tried as a
  block-mapping key; its `UNTERMINATED_*` cut committed at the opening, so one that closed cleanly but found no `:`
  fired the cut rather than backtracking to the scalar it was — a whole `"hello"` document became an error — and the
  four flow cuts are scoped to their item now, the error only where the item never closes. `:` is an `ns-anchor-char`,
  so `*a:` is the alias `a:`; the backtracking interpreter shortened the name to `a` to open a mapping, and a
  `<not_followed_by_an_ns-anchor-char>` guard now holds it to its greedy match, as YamlReference and YAMLStar do. A
  block scalar's last content line at end-of-input keeps its break, the zero-width line feed `b-chomped-last` emits
  where the spec reads end-of-input as a line break; an all-empty block scalar takes its content indentation from the
  widest of its empty lines, the spec's §8.1.1.1 fallback; and the root the parser runs, given no resume policy, takes
  the zeroed one, so trailing content it cannot parse recovers rather than the interpreter asserting the root is total.

### Changed

- `ys_options.max_token_bytes` becomes `max_bytes`, and caps the memory the parser allocates rather than the bytes it
  buffers for one token. Three things grow — the buffered input, the tokens held back with it, and the parser's stack,
  which deep nesting grows and no quantity of input bounds — and one cap now bounds them together.

- `src/yeast.c` is gone, split by topic: the version query and its load-time sanity check, the counting allocator, the
  stream adapters, and the yeast wire format each have a file of their own. Allocation and the `max_bytes` accounting
  are one place, `src/memory.c`, rather than one copy in the parser and another in the wire-format reader; a reader held
  under a cap it cannot even be built in is now refused outright, as the parser already was. What a NULL `ys_options`
  means is `ys_resolved_options`, so the defaults are named where the struct is read and not again at each field. The
  reader hand-over and the teardown of everything an object owns are each one place — `ys_discard_reader` for a
  constructor that is already failing, `ys_teardown` for a destructor — rather than copies of the same close and the
  same errno care around each. A reader is handed over whether or not the object that would read through it can be
  built, so a constructor that fails closes it rather than leaking it, discarding a close failure it has no channel to
  report and holding on to the reason it is already returning `NULL` for.

- The reader of the yeast wire format tells a broken wire from the tokens a wire carries, the same way the parser tells
  a malformed document from a valid one: a wire that is not the wire format is a `YS_CODE_ERROR` token, bad data like a
  bad document, its text one message per way the wire can be broken and its marks the line and column in the wire. The
  reader validates its input as the parser does — a byte that a conformant wire would have escaped, an escape naming no
  Unicode codepoint, a position that is not a number — each a located `YS_CODE_ERROR`, not a misread; the wire is spent
  after one. A host failure reading the wire — out of the memory to buffer it, or a byte source that fails — is not a
  token but `ys_read_token`'s return, `YS_FAILED_MEMORY` or `YS_FAILED_STREAM`, and reading past the end is
  `YS_FAILED_ACTION`. So a caller reading until a negative return learns why it stopped.

- An `errno` policy across the API. Malformed data is never an `errno` — a syntax error or a broken wire is a
  `YS_CODE_ERROR` token, part of the stream. A host failure is: `ys_read_token` returns a negative `ys_status` with
  `errno` the reader's, `ENOMEM`, or `ENODATA`; and a function that fails without a token — a constructor,
  `ys_write_token`, or one of the closers — sets `errno`: `EINVAL` for a bad argument (a stream source with no `read`
  callback, a memory parser given a NULL buffer with a length), `ENOMEM` for insufficient memory, or the value a failing
  callback set, passed through. An allocator or reader callback must set `errno` when it fails; a debug build asserts a
  failing allocator did.

- Closing reports, because a buffered close is where a write finally reaches its destination and so where a full disk or
  a broken pipe is first seen — long after the last `ys_write_token` returned `YS_OK`. A `ys_bytes_reader`'s,
  `ys_bytes_writer`'s and `ys_allocator`'s `close` each answer `close(2)`'s contract, 0 or -1 with `errno` set, as their
  `read` and `write` already answer `read(2)`'s and `write(2)`'s; and `ys_delete_token_sink` and
  `ys_delete_token_source` return it rather than swallow it. A delete closes the byte transport and then, once
  everything is given back, the allocator — the order that lets the allocator be what the memory lived in — and runs the
  whole of it whatever fails, so a close that fails leaks nothing: `YS_OK`, `YS_FAILED_STREAM` if the transport's close
  failed, `YS_FAILED_MEMORY` the allocator's, `YS_FAILED_BOTH` both, with `errno` the first one's. That one `errno`
  cannot name two failures is the documented limit; a caller needing both records them in its own callbacks. The
  `ys_allocator` gains the `close` for an arena or pool to be torn down with what was built out of it, and the
  `ys_counting_allocator` installs `ys_close_counting_allocator`, which checks nothing leaked and reports a leak as a
  close failure — `-1` with `errno` `ENOMEM`, the memory the counter still holds — so a delete through it surfaces the
  leak as `YS_FAILED_MEMORY` rather than asserting.

### Fixed

- The yeast wire format dropped an error's message. It took a token's text to be the input the token spans, and an error
  spans none — so a malformed document wrote `!` and nothing else, where YamlReference writes `!` and the message. The
  wire exists to compare token streams against YamlReference, and an invalid document is exactly where two parsers
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

- YamlReference does not resume after an error, and libyeast had been built to match a reading of YamlReference that
  said it did: it emits the error token, hands back the input behind it as unparsed, and stops. So resuming at the next
  document was not fidelity but a departure, and the one thing it was adopted to protect — the token-for-token
  comparison against YamlReference — is exactly what it broke. Not resuming is now the default, and resuming is an
  option the caller asks for, knowing what it costs.

- An error's message is a static string, so its lifetime is no longer an exception to the rule every other token's text
  follows. It cannot be, since the message names the production the parser was inside and what it expected there, both
  of which are the grammar's and not the input's. What the parser found is not in the message and does not need to be:
  the first `YS_CODE_UNPARSED` token behind an error begins at exactly the byte that failed.

- No token spans a line — not text, not a comment, and not the input skipped after a malformed document, which comes
  back as one `YS_CODE_UNPARSED` token for a line's content and another for its break. A token that spanned a line would
  have made a stream parser's output depend on how much of the input its buffer happened to hold.

_No release has been tagged yet; the YAML parser itself is not implemented._
