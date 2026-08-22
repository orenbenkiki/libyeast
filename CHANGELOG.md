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

  A `Step` with no transform is a **claim**: it changes nothing and only says that where it stands, its invariants read
  none. That is how a property the pipeline is handed rather than makes gets written down, and the law holds every step
  behind a claim to it exactly as it holds them to a settled one. Naming such a property on whichever step happens to
  run next reads as that step establishing it, and puts the blame for a later break on the wrong side of the line — so a
  step naming an invariant that was **already none when it was handed the grammar** is itself a fault, one that says to
  write a claim instead. Four properties were named that way and are claims now: `no-gate-decides-nothing` and
  `every-scope-closes-on-the-path-that-opens-it` at the door, `no-unreachable-option` once the specialization has run,
  and `no-production-reaches-itself-unconsumed` once the optionals are ways.

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

  Phase 2 is the character questions. A set of characters is written many ways and asked in several — a character, a
  range, a union of them, a base with exclusions, a reference, the item a lookaround peeks — and all of them come to the
  one bit the parser tests. A difference is one of them only where both its sides are sets, and at four sites it was
  not: `ns-double-char`, `ns-single-char` and the two `ns-tag-char` copies each take whitespace or an indicator out of
  something that is an escape *or* a character — `\` and one more, `''`, `%` and two hex digits — so the subtraction
  stood over a language rather than a set and no bit could say it. `distribute-differences` takes each into the ways it
  subtracts from: the ways keep their order, a run of them that are each one character takes the subtraction once as the
  union they already are, and a way that takes two characters or more keeps its whole language, a subtracted set taking
  one character and so reaching only a match of one. Every difference stands between two sets from there —
  `every-difference-is-between-character-sets` — and `lower-char-sets` folds them all away, `no-diff-nodes` at none and
  the notation gone from the grammar the phase hands on. `lower-char-sets` says each as the sorted disjoint codepoint
  intervals it denotes, so from its end every question about a character is a `CharSet` or a literal. A maximal one is
  taken, not every one inside it, the intervals of a union being its own; a reference a match takes is left standing,
  being the caller's hold on the production where the set is said, and inside a lookaround it is read through, what a
  peek holds being the question rather than the hold. An annotation on what a peek names is read through with it, and a
  peek is counted on the question it asks rather than on the shape naming it — a probe emits nothing and gives back what
  it read, so the code around `c-comment`'s `#` is dead inside one. That peek was the last character question the phase
  left standing, and the six beside it in the grammar the phase hands on are copies the later steps make of the way it
  sits in: all 64 peeks there are a `CharSet`, and what is left looking around is the ten `(exclude)` guards, which ask
  about a line rather than about a character. The phase follows the specialization, a set the context picks denoting
  nothing until a caller is known. Saying it once also makes the spelling canonical, which is what lets the sweep do its
  own work: two productions denoting the same characters differently are structurally unequal and do not merge, the
  merge reading shape rather than extension.

  Phase 3 is the block scalar's leading-empty floor. `clear-f` gives the value an end — the production that reads it,
  `s-indent-floor`, clears it where it returns, the reader and not the writer, since the floor is measured deep inside
  the leading empties and handed up to the one thing that asks about it — and `read-global-f` takes the declaration off
  every production and the argument off every call, each read becoming a `GlobalValue`. The reads hide where the generic
  walker does not go: it carries a `ParamValue` as a value and never visits one a field holds directly, so
  `s-indent-floor`'s `Le(f, n)` is a read both the count and the rewrite had to walk the fields themselves to see. What
  licenses the drop is that the value does not nest, and the interpreter says so rather than the argument: a `(set)`
  puts it on a stack of that global's own, a `(clear)` takes it off, the single slot stands beside it, and the reads
  where the two differ are counted over the whole corpus. The gate holds that at none — and made to nest, the same net
  reports 21.

  Phase 4 is the detected indent, and `clear-m` and `read-global-m` do what `f`'s pair did. What made it possible is
  that nothing reads the value twice over a region something else can write in: the block header measures it and the
  scalar that asked reads it, one construct at a time. A block collection did read it on every turn of its loop, which
  is a value no single slot can hold — every collection or block scalar the loop entered detected one of its own in
  between — and the count of reads a slot could not have answered stood at 843 for exactly that reason. It is the
  grammar that answers for it now, each collection entering its entries at the indentation the first of them
  established, so the count stands at none. `BindTree` maintains the stack beside the slot too — a write is a write
  however it is spelled, and a block header's indicator sets the detected indent through one, so a global written that
  way had been invisible to the net.

  Phase 5 is the indentation, and it is not one of the parse's own values: a nested collection's entries are measured
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

  Phase 6 is the empties, and `lower-optionals` is its first step: `x?` becomes `x | <empty>`, the empty way standing
  beside the one that reads rather than hidden inside a node. It is the same match and the interpreter says so — an
  `OptTree` tries its item with the continuation behind it and, where that fails, rewinds and takes the continuation
  alone, which is that alternation tried in that order. So nothing has to be known about what follows.

  A run over a character class is a scan and not a way, and the interpreter now draws that line where the grammar does.
  Such a run is single-outcome by construction — only ever followed by something off its own set — so it is taken whole
  and judged whole, which is what `s-indent-le`'s "the maximal run, and then its length against `n`" needs: falling back
  to a shorter run would let an over-indented line pass as if it had none. `span-consumes` writes each as the one scan
  it is — `x*` a `ConsumeSpanAction`, `x+` the character and that span behind it, 56 in all — and a counted one the same
  way: `x{n}` over a character class is a run of up to `n` characters of the set and the guard asking whether it reached
  `n`, a count the parse works out being the two ways it is. Seven of those, and `RepTree` is gone from the grammar with
  them.

  A repetition of a way is said as the ways it is, and `lower-runs` says both spellings that way: a turn, a recursion
  taking the rest, and a settled region around the turns after the first. Every turn takes a character, a turn taking
  none being a turn the run did not take, and what the region settles is the turns it holds — so a failure past its
  close gives the whole run up rather than taking fewer turns. The turn never taken is a way of its own where the
  spelling is `(***)` and no way at all where it is `(+++)`, which is the whole of the difference between them, and the
  interpreter had been saying so all along — its two arms differed by that one line. `no-star-or-plus-nodes` settles at
  none over 72 runs, and nothing past that step repeats anything at all.

  The ways are an ordered choice, and what holds one to the longest run is the settled region around the turns rather
  than anything about the choice: a failure past the region's close gives the whole run up instead of taking fewer
  turns. What ends the run is the guard each turn carries and not a gate on the choice — a turn that took no character
  is not a turn the run took, which is a thing the grammar says where it used to be a comparison of positions inside the
  interpreter's own loop. Nothing repeats a way any more, and what scans a character class is two kinds — the maximal
  run and the counted one.

  Every function that dispatches on node kind raises on one it has not heard of, not only the ones that answer yes or
  no. The rule had been read as being about the booleans, where a wrong `False` turns a scan into a way; it is about any
  answer given by default. `validate_grammar.consumed` walked into the children of a kind it did not name, so a new way
  of taking a character would have yielded nothing of its own and every character it took would have passed "every
  character lies within a token annotation" unannotated; `check_grammar_docs.emitted` did the same for a new way of
  emitting, which would have read as documented while saying nothing. `chars.denote` answered "no characters" where it
  meant "no answer"; `grammar2decoder.defined` answered "defines no character", which would have left that character out
  of the decoder tables, and the drift gate can only see a table that changed rather than one that never had the entry.
  `check_decoder` and the character-run invariant each kept their own list of what repeats, which goes stale the moment
  a run is spelled a new way and then reads none because the kinds it names are gone.

  `ir.KINDS` is the net they share: `NOT_ONE_CHAR` and the eight `is_one_char` answers for on their own terms are every
  kind between them, so a kind in neither raises at the first question anyone asks of it. `ir.repeated` says what a node
  takes again and again and `ir.CONSUMING` which kinds take characters themselves, each named once rather than re-listed
  at every site. Sharpening the run reading found seven counted repetitions over a character class that were not spans,
  which is the whole argument for it.

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
  call sites, `e-node` at twenty-six of them — and Phase 6 is finished: `only-root-empties` goes from 50 to none, and
  the six productions still matching empty are the root and the recovery under each resume policy, which a parse enters
  by name. Once the two ways are told apart, what still matches empty is what only ever took nothing: the residue a
  split named, and the five that were actions alone. A name is worth having where it stands for a decision, and there is
  none in a way that consumes nothing and always ends where it began. Each is written out before it is written in, a
  residue holding calls of others; one reaching itself would be a match of nothing at all rather than a match of
  nothing, and the grammar has none.

  The phase leaves 326 productions where it found 331, having gone as wide as 423 in between. No caller now chooses
  whether to enter something that may take nothing — every empty match is a way of the caller's own, where a character
  can decide it.

  A scope that holds what it covers is the pair that brackets it, one step per kind. An alternative —
  `gate actions… [P1 actions…] [P2]` — has a place for an action and none for a node enclosing a call, and a `(token)`
  around a call is an action that must run where the call returns, which is the continuation; so the wrappers come off
  before a way is split into a call and a continuation, or they come off twice. In order, smallest and least stateful
  first so that what the last one moves is the only thing left to look at: `lower-wraps` writes `Wrap(begin, end, x)` as
  `Emit(begin) x Emit(end)`, 150 of them; `lower-windows` a `(max)` as `OpenWindow … CloseWindow`, 3 — windows do not
  nest, only the outermost applying, and the pair counts the opens standing where the wrapper asked whether a ceiling
  was already set; `lower-commits` a `(commit)` as `PushMessage … PopMessage`, 47 — a commit being the error where its
  item never reaches its own end and nothing more, which is what the push records and the pop marks reached; and
  `lower-tokens` a `(token)` as `PushCode … PopCode`, 343, both halves cutting the run, the push setting the code the
  characters between them carry and the pop taking back what it displaced.

  What the last three change is where the displaced thing waits. A Python local — which is to say the frame of the match
  that is running — becomes the parse's own state, and that is the whole point: a frame is gone once the way is split
  into a call and a continuation, and a stack is not. It is also why the balance has to hold while it happens, a pop
  taking back whatever is on top rather than what its own push put there.

  A wrapper is paired by construction — `ir.Wrap` is a node rather than the two markers precisely so a `begin` cannot
  lose its `end` — and unwrapping trades that for a property that has to be checked. `every-scope-closes-on-its-own-way`
  is what takes it over, read at none over all seventeen stages before a wrapper came off and named by every step that
  takes one: a scope opened on a way is closed on that way, the ways of a choice agree on what they leave open, a run's
  turn leaves none since a second turn would open it again, and a lookaround is probed and given back so what is inside
  one touches nothing. It covers the four pairs a normalized grammar can carry — `PushIndentAction`/`PopIndentAction`,
  `PushCodeAction`/`PopCodeAction`, `PushMessageAction`/`PopMessageAction`, `OpenWindowAction`/`CloseWindowAction` — and
  the indent pair had never been held to it. It is not vacuous: dropping the pops from a production reports both the
  scope left open and the ways that no longer agree.

  The markers are not that. A pair of them crosses productions by design — `b-chomped-last` emits `end-scalar` for a
  `begin-scalar` opened elsewhere — so a per-way rule is the wrong one for them, and `check_markers`, which does prove
  their balance with a fixpoint over the callers, reads the grammar as authored rather than following the pipeline.
  `lower-wraps` loses nothing on its own account, the two markers coming out adjacent in one way of one production with
  nothing in the sweep to separate them. What it gives up is the guarantee for later, and the first thing that can put a
  `begin` in one production and its `end` in another is the split into a call and a continuation, so that is the phase
  the marker net is owed by.

  Moving the state out of the frames is what the two faults were about, both of them a scope an abandoned parse left
  standing where the wrapper's frame had taken it with it. An in-grammar `(recover)` put back the `(max)` ceiling of the
  rule it belongs to and not the count of opens beside it, so a key past 1024 characters bounded no later key at all —
  the recovery answering for a window that was still counted open — and a cut that unwound to the stream's own level
  left its committed regions on the emitter where a raise had skipped their closes. A parse that matches now refuses to
  return with a window or a region open, which is the standing net for both: it reads none on the grammar as authored,
  named the regions the moment `lower-commits` landed, and the fixture behind it is a stream whose first implicit key
  overruns and whose second must overrun again.

  The grammar hands on 326 productions with no `(wrap)`, `(max)`, `(commit)` or `(token)` left in it — 343 code pairs,
  48 message pairs, 3 window pairs, 69 indent pairs and 368 markers, every one closing on the way it opens. The eight
  `(recover)` stay: a recovery is a handler and not a scope, with no close whose position means anything, and its home
  is the edge an alternative rides.

  A choice is where the parse decides, and a machine decides in a state — so phase 7 takes the tree apart, an item
  standing in a way being what the machine does where it stands. `no-item-holds-a-match` counts what is left of the tree
  at 578 to begin with: 463 choices, 106 runs, 8 recoveries and one binding, each holding a match inside it where the
  machine has no state to be in. `lift-choices` settles the first of those, `every-choice-is-a-body` at none: a choice
  standing inside a way gets a production of its own and the way holds the call. Minting rather than distributing, which
  is the other way out of a sequence — `a (x | y) b` as `a x b | a y b` runs `a` twice wherever it takes a character or
  pushes anything, and copies whatever `b` calls; the call costs a push and duplicates nothing. 326 productions become
  492, the sweep merging what the 463 mintings duplicate, and the phase's own count falls to 91.

  What it costs is phase 5's shape, and the three counts that say so are one fact: a choice between reading and taking
  nothing, written at the call site because nothing could gate it there, becomes a production a call reaches —
  `no-call-enters-both-ways` at 269, `only-root-empties` at 114 and `every-empty-match-is-a-way` at 13, each a declared
  lapse with that reason, and each what the gates answer for when they arrive. The canonical form has nowhere else to
  put it: `F (X_reads | X_empty)` is a call and then a decision, and a decision after a call is a production.

  And it turned up a cycle nobody had looked at. `no-production-reaches-itself-unconsumed` exempted the stream and the
  recovery by name, on the reading that a recovery is a landing the driver picks rather than a call the grammar makes —
  which is false under a resuming policy: `l-recover` is `l-unparsed` and then the stream again, mutually recursive by
  design so that a resumed document can fail again without a second mechanism. A production minted out of the stream's
  own body lands on that cycle and inherited no exemption. What holds the pair is `l-unparsed`: it takes nothing only
  where the next line is a document boundary or the input has ended, and there the stream consumes the `---` or `...`
  itself, or `<end-of-stream>` answers — so a second recovery costs a character. The exemption is the cycle's now rather
  than a name's, a path through what a parse enters by name being cut before reachability is asked, and what still
  reaches itself is the grammar's own doing and a fault.

  The other three shares follow, and the phase's count reaches none. `lift-runs` gives each run standing in a way a
  production of its own — the machine's loop state, which is a production it jumps back to the top of — settling
  `every-run-is-a-body` at 82, and costing the same three counts again for its own reason: a run of none or more is that
  same choice under another name, take a turn or take none. Naming a run settles nothing about the run itself; it stays
  the possessive scan it was, and whether it takes another turn is a question the gates ask. `lift-recoveries` does the
  same for the eight recoveries, which is where a handler waits until there is an edge to ride — an alternative carries
  one, and until the alternatives are made a production of its own is what a recovery is. `lower-bind` takes the last
  binding, the block header's `Bind(ns-dec-digit, m, atoi(match))`, and writes it as the match and the write that
  follows it: an identity the interpreter states twice over, what a binding does once its condition has matched being
  exactly what a `SetVarAction` does, down to undoing the write where what follows fails so the condition can try its
  next way.

  `no-item-holds-a-match` is none from there, over 532 productions: every item standing in a way is a call, an action, a
  guard, a character taken or nothing at all. What the count itself got wrong is worth keeping — it did not know a
  recovery could *be* a body, so the productions `lift-recoveries` minted read as faults of their own. One list says
  what a body may be now, a choice of ways or a run of one or a way under a handler or a way, and the count and the
  lifting both read it; a binding is deliberately not among them, a body that is one hiding a write behind a match.

  The exclusions are the phase's other half, and `every-exclusion-is-bounded` reads 7. An `(exclude)` is a guard the
  parse carries and tests at every start of line while it stands, so what it asks has to be answerable where it is asked
  — and four of them ask it by name, `c-forbidden`, which a guard would have to run a parse to answer.
  `bound-exclusions` writes what they ask as what it denotes: at a line start, `---` or `...` and then a break, a space,
  a tab or the end — two `LiteralPeekGuard`s of three characters with one follow class between them, which the parser's
  own fill already guarantees. The reading is a peek's: a name is read through, an annotation is dead, and the follow
  test distributes over the two runs because a question is probed and given back, so what is duplicated is a test rather
  than a match.

  The count stops at 3, and they are the phase's declared debt rather than its oversight: those exclusions also ask
  whether the line stands at this indentation with content, which is a run of spaces with no bound — a condition on a
  line start rather than a question about what follows one. It lands where the block-structure work makes a line start a
  decision the grammar spells.

  Phase 9 is the call. An edge of the machine is one push and one jump — the push says where to come back to, the jump
  goes — so a way is what it does before it hands control on, the call it hands it to, and the one production that
  carries on: `a P1 b P2 c` is `a`, the call `P1`, and a production holding `b P2 c`, which splits the same way until
  nothing stands past a call. `a-way-is-actions-a-call-and-a-continuation` reads 227, being 189 ways that go on doing
  things past their call and 38 that hand control on more than twice, and `mint-continuations` settles it, 528
  productions becoming 740. Binarization is no step of its own: the one way with three calls falls out with the rest.

  What that breaks is the scope net's reading, not the pairs it proves. `every-scope-closes-on-its-own-way` was a
  per-way rule, and a way that hands control on is half of a path: `s-indent-le_reads` keeps its `PushCodeAction` while
  its `PopCodeAction` rides into the continuation, which is exactly the cut phase 6 moved the pairs onto the parse's own
  stack for. So the invariant is re-derived rather than lapsed — `every-scope-closes-on-the-path-that-opens-it` — and
  reads none at every stage, before the split and after it.

  Working it out took one thing, and it is what the machine runs on: a call is a **unit**, standing for what it does
  relative to its own entry. Both of a way's calls are on the path, the one it comes back from as much as the one it
  carries on at, and each contributes what it takes off and what it leaves — so a push, a call, and the pop that follows
  balance whatever depth the call reaches, which is what lets `c-flow-sequence` open a message scope, recurse the whole
  of flow content inside it, and close it on the way out. Walked into instead of stood for, that recursion reads as a
  circle entered one scope deeper every turn, and `[ [a] ]` becomes a fault.

  Which leaves the circles, and there the reading is the whole of it: a production that reaches itself has an answer
  only where going round leaves the scopes as it found them. The answers are read one circle of calls at a time, each
  after the ones it calls — Tarjan's components in that order — and round each circle until they stop moving. What says
  a circle does not come back level is the growth: an answer longer than every scope action the circle holds, and every
  scope its outside calls leave, has been round more times than there are pairs to have opened. A round budget in place
  of that reads "has not finished yet" as "is at fault", and which productions it blames depends on the order the walk
  took — the same grammar answered 0, 5 and 18 across three runs before the growth was what decided it.

  That reads what a production does and not where the parse goes, and the second is its own question. A way's two calls
  are alike to a summary and not alike to a path: what a way hands control on to is where the path carries on with
  nothing pushed to come back to, so a loop of hand-offs is the parse genuinely back where it was. Round it once and
  whatever it left is standing; round it again and there is one more, and nothing bounds that. So a loop must be level,
  and only a walk of the hand-offs can say so — a summary is what a production does relative to its own entry, which is
  no answer about what a turn round a loop leaves. One walk per circle of hand-offs, since every loop lies in one and a
  walk from any of its productions meets every loop in it as an edge back to where it already stands: six circles hold
  one, over 29 productions of 793, and every one comes back level.

  Not what the parse can be *reached* at. Two paths may arrive at one production having opened different things and each
  be balanced in its own right, and refusing that is a rule about where a production may be called from rather than
  about scopes — it read 11 faults where there were none.

  A recovery is walked by neither, and not because its halves pair up. The interpreter unwinds to it, reading the stack
  it finds and taking off what the abandoned parse left standing, so what it is entered with is put back rather than
  balanced by anything the grammar writes. Holding it to the pairs on its own path would hold it to something nothing
  does.

  Phase 10 says every body in the machine's own words, and `every-body-is-a-choice-a-run-or-a-set` reads 697. A terminal
  is a set of characters. A loop is not among them: a repetition was said as ways where it was lowered, so the state it
  jumps back to the top of is a production like any other and what stops it is a guard like any other. Everything else
  is an ordered list of alternatives, each a gate to enter on, the actions it performs, the call it hands control to,
  where it carries on when that returns, and the recovery riding the push: `build-alternatives` writes the remaining 685
  bodies that way, over 752 productions.

  It is a change of spelling and not of meaning, and two things say so. The interpreter already ran the canonical form —
  an alternative as the sequence the tree spelt, the gate's peek as a lookahead, the recovery as the `(recover)` scope
  over the call it protects — so nothing had to be taught how to execute one. And the gates are left empty: what a way
  is entered on is a question about the character in front of it, which is the hoist's to answer, and until then the
  alternatives are tried in order, which is what the tree said too.

  What did have to be taught is every reading that walks a body, and each refused rather than guessing: the flatness
  count, the nullability, the split saying which ways a production has, the left corner the cycle check walks. A choice
  says its ways as `alternatives` where it is the machine's and as `items` where it is the tree's, and an alternative
  says its parts by name where a sequence says them in a row — so one reading of each says both and the walks ask it.
  The one that bit was an `AltTree` reaching the helper written for the machine's spelling and getting itself back as
  its own only way, which is a walk that never ends rather than an answer that is wrong. The per-way scope walk went
  with the rewrite, 85 lines of it, the path check having replaced what it read.

  Phase 11 is the gate. A machine that never backtracks takes a way by looking at the character in front of it, so
  `every-way-gated` counts the ways a parse would have to try and give back: 372 of the 600 alternatives that make a
  decision, the last way of each choice being exempt as the unconditional fallthrough and a body with one way being no
  decision at all. `gate-hoist` is the first of the hoists that reduce it, and the one with no analysis behind it: a way
  whose first action takes a character is entered on that character, so the set rises into the gate and a
  `ConsumeCharAction` stands where it did, taking the one the gate has already found. It is done in every alternative
  rather than only where a choice needs telling apart — a way whose first action is a set fails there where the set is
  not, gate or no gate — and it takes the count to 324.

  What is left says what the rest of the phase is: 272 of them begin with a call, and what a call can start with is an
  entry set the grammar has never computed; 47 begin with an action the gate has to look past, which it may, an action
  touching no input; and 5 with a guard, which the gate carries beside the peek rather than in it.

  So the entry sets are computed — `{name: spans}`, the characters a parse of each production can start on, as a least
  fixed point over the calls, erring wide where it errs since a gate too wide costs a parse that fails where it could
  have been refused and a gate too narrow loses one that should have matched. `gate-hoist-call` then enters a
  call-leading way on what its callee can start with: the way cannot match unless the callee does, so the set refuses
  exactly what the way would have failed on one call deeper. `every-way-gated` falls from 324 to 93.

  Two side conditions, and the second is one the corpus found rather than the argument. A callee that can take nothing
  is refused, the way passing through it to whatever stands behind. And a callee that answers a character it cannot
  start on with an *error* rather than a refusal is refused too: a `(commit)` opened before anything has to take a
  character makes the failure the error that region names, so gating the way out of the parse lets the choice go on to a
  way that matches where the parse used to stop. That is a different language and not a narrower one, and the suite said
  so exactly — `2G84/00`, a case libyeast began accepting where the suite rejects it. Nineteen callees are of that
  shape.

- Boolean names audited across the generator, function, variable and parameter alike: a fixpoint's `changed` and `moved`
  are `did_change` and `did_move`, a match's `matched` is `did_match`, the law's `licensed`/`broken`/`settled` are
  `is_licensed`/`is_broken`/`is_settled`, `refuses_softly` and `establishes` are `does_refuse_softly` and
  `does_establish`, and the flags `bisect`/`check`/`apply` are `does_bisect`/`is_checking`/`is_applying`. A CPS
  continuation whose bool means "the rest of the parse matched" keeps its name: it is protocol rather than a predicate,
  named for the act it performs where it stands.

  **The meter, which nothing has printed since the pipeline was reordered.** `every-decision-goes-on-a-character` counts
  a multi-way choice whose gates do not tell its ways apart — a way the gate says nothing about with another behind it,
  or two ways admitting the same character, order being all that separates them. The last way is no fault: an empty gate
  there is the fallthrough, taken where nothing else fired, which is a character deciding by firing nothing. It reads
  **228** where the alternatives are first made and no gate says anything, and the hoists take it to **94**.

  The rest of the gating goes with it. `hoist-past-actions` enters a way on the character its first question asks
  whatever actions stand in front of it — a gate is tested before the way is entered and an action touches no input, so
  the same character chooses either way, and a way that fails the test rewinds whatever its actions did. The walk goes
  through a call that can take nothing as well, what enters the way then being what that call can start on *and* what
  stands behind it. It stops at a commit, which is `gate-hoist-call`'s refusal one call deeper: a `(cut)`, an `(error)`
  or a region opened before the question makes failing there an error rather than a refusal, and a gate that keeps the
  way from being entered turns that error into a way not taken. `hoist-guards` puts a leading `EndOfStreamGuard` or
  look-behind in the gate beside the peek, both being questions the machine can put where it stands — and
  `every-way-gated` says so too now, a way entered at the end of the input having no character to be asked about at all.
  Together: 372 ungated ways down to **36**.

  Those 36 are accounted for rather than left. 18 call a production that answers a wrong character with an error, 6 one
  that can start on nothing, 6 stand behind a commit, and 6 pass through everything they hold so that no character has
  to be in front of them at all. Every one is a question about *when a failure is an error*, which is determinizing's to
  answer and not a hoist's.

  **And the meter is only half of what determinizing owes.** A character picking a way is not the same as committing to
  it being safe: a gate can be perfectly disjoint and still be wrong, the way it admits failing three characters later
  where a backtracking parse would have taken the next one. Nothing measured that —
  `normalize.deterministic_productions` went with the old order, and the interpreter's committed mode has taken an empty
  set ever since. It is back: the productions a character decides are handed to it, the corpus runs **hybrid** —
  committed where a character decides, backtracking everywhere else — and a case the two modes read differently is a
  gate that is disjoint and not safe.

  It read **603 of 697 choices entered committed, and 236 cases the two modes read differently**, which is the honest
  first number rather than a failure: the entry sets a gate is hoisted from err wide on purpose, and a wide gate is
  exactly one that admits a character its way cannot go on to match. Harmless while the parse backtracks, fatal once it
  does not. It is printed every run and is what the transforms ahead are judged by beside the meter; it becomes a gate
  where it reads none, at which point the two modes agreeing is law the way the corpus already is.

  **What the meter counts is not all one thing, and the walk says which.** `determinize.py` walks a conflict's live ways
  in lockstep to their first divergence, and what differs there classifies the site: the characters, and a character
  decides it; the codes over the same spans, and only holding the tokens and retyping them can; the configuration
  repeating, and it is unbounded. Run as a measurement over the 94 the meter flagged, it read **44 a character decides,
  36 the codes differ, 11 it cannot even root, 3 a guard already separates** — the last of which is the meter reading
  peeks only, as it says it does.

  The 11 are the ones worth a step, because they are not verdicts: `_caller_continuation` refuses a conflict reached
  from several places, the follow being several, so the walk has nowhere to stand and the meter is claiming a fault it
  cannot describe. `splice-conflicts` answers it from the other end — a call to such a conflict is spliced where it
  stands, so each copy is the conflict in the context that reaches it and its follow is the rest of the way it now sits
  in. The copies differ by where they are rather than by what they hold, which is what keeps the sweep from folding them
  back into one.

  Three side conditions, and the corpus or a count found each: a way that has taken a character does not splice, the
  callee being entered elsewhere than the way; a way that has **committed** does not, since the callee's ways backtrack
  *inside* that region and spliced out each would open its own, making the first way's failure the error rather than the
  next way's turn — the flow collections' unterminated-bracket commits, which broke 39 fixtures before the condition
  existed; and a callee that carries on at something not level does not, since that call would become one the way comes
  back from with the caller's continuation pushed behind it, which the scope net caught at 25.

  It runs to a fixpoint because splicing makes sites: one pass moves the count up as often as down, and what it leaves
  is a caller that has become the conflict. `every-conflict-can-be-asked` — the walk's own question, and the count that
  says how much of the meter can be worked on at all — goes **11 to 5**, and the two modes now read **129** cases
  differently where they read 236. The meter itself rises, 94 to 165, and the rise is bookkeeping rather than work: the
  walk says a character decides 116 of the 167 where it decided 44 of 94, and the pile that is genuinely hard barely
  moved, 36 to 39.

  The 5 left are each named: four are blocked by a commit standing before the call and want the commit lifted, which is
  the step the 24 ungated ways want too; one wants the tail call it carries on at made level.

  **A prefix can hide behind a call, and a gate cannot see one that does.** Two ways of `b-break` both begin by handing
  control to `b-carriage-return`, so they do the same thing until it returns — a shared beginning like any other, and
  invisible to everything that compares gates. `no-conflict-shares-a-called-head` counts them, 117, and only at
  conflicts: a choice whose gates already tell its ways apart has nothing to move, and inlining there would copy a
  production for no decision at all.

  `inline-shared-heads` splices that callee into the ways that share it, which is the splice's own rewrite pointed the
  other way — down into a conflict rather than up into its callers — and refused on the same three grounds, so the two
  steps share one pass and differ only in which calls they name. A terminal is spliced too, being one way and that way a
  character: said as a gate on its set and a consume, the call stops hiding the prefix. The count falls to 101 and the
  grammar with it, 858 productions to 820, since what a shared head is spliced out of the sweep can drop. One pass, and
  the rounds are a question left open rather than settled: run again over the grammar it leaves, the count falls 101,
  86, 72, 62, 49, 47 and the grammar to 666 productions; run again over the grammar it starts from, it climbs to 277.
  What is left of a conflict after a pass and a hoist is a truer conflict than what stands before either.

  **What stands between a choice and being decided is not that its gates meet — it is that they meet partly.** Two ways
  admitting exactly the same characters are a shared prefix waiting to be factored; two whose gates cross are neither
  told apart nor shared, and the character firing both says take the earlier, which is order deciding rather than the
  input. `no-partial-overlap` counts those, **438**, and `split-gates` settles it: the ways are cut along the groups
  their gates treat alike — the coarsest cut that leaves no gate straddling a group, since cutting at every edge instead
  splits a way along boundaries that have nothing to do with it, ten times the copies for the same answer. The same
  match spread over copies, each standing where the way stood, so order is what it was and the ways a copy shares a gate
  with are exactly the ones it overlapped.

  The meter does not move for it, and should not: an overlap made whole is still an overlap. What it is for is the
  factoring behind it, which reads exactly the gates that are now equal.

  **A way no input refuses is the last way the machine takes.** `no-unreachable-option` counts a way with another behind
  it that nothing can hand back: a choice goes on to its next way exactly where the one in front of it fails and is
  given back, so a way that always matches — or whose failure is the error a commit names, both of which stop the choice
  where it stands — leaves nothing for the ways behind it to be entered on. It reads **none** from the point it is
  claimed through every step, with no lapse anywhere.

  It is one question and not two, which is what took the work. A way is refused where a character it needs is not there,
  where a guard it asks declines, or where its gate turns it away; it is not refused past a `(cut)`, nor inside a
  committed region, `interpreter.match` raising through a region that has not closed and handing back through one that
  has. A counted scan is refused, being all or nothing — a scan of none or more is not. Asking instead whether a way
  refuses *the character it cannot start with* answers a narrower question with a pessimistic bound: it names the first
  place a raise is possible and stops, where a way that raises on one input is handed back on another. That reading
  called 24 ways unreachable, and deleting them broke 11 fixtures — `c-l+literal.n=2.empty`, `header-eof`,
  `keep-empties`, `keep-none`, `JEF9/00` — which is the test a claim of unreachability has to survive. The narrower
  question is the one `gate_hoist_call` asks, and it keeps it.

  **Every way of a choice carries a test the machine can make before entering it.** `every-way-gated` is
  `every-way-carries-a-test`, which is what it counted all along and now says: a machine takes a way by testing
  something first, and what the test is comes second — a character set is the usual one, a guard is one too, asking
  where the parse stands in its line, whether any character is left, how the indentation compares. Whether the tests of
  a choice are exclusive is the next question and a different count.

  `hoist-askable-guards` settles the guards' half of it, taking the count from **75 to 18**. A guard among a way's
  actions is a question asked a moment too late — reached only by entering the way, when entering the way is what it
  could have decided — so it moves to the gate, where the machine asks it. Which guards move is the whole of the step: a
  guard about the input alone passes any action, since none of them writes where the parse stands or what surrounds it;
  a comparison passes only actions that do not write what it reads, an indentation test behind a `PushIndentAction`
  reading what the parse has not done yet; and nothing passes a commit — a `CutAction`, an `ErrorAction`, a
  `PushMessageAction` — past which failing is an error rather than a refusal, so a gate that keeps the way from being
  entered would turn a parse that stopped into one that took another way. `no-guard-left-among-the-actions` counts what
  is still owed and settles at none.

  A test spelled as a call is one the way cannot be entered on either, and the same step lifts those: a production of
  one way that holds no action, makes no call and carries on nowhere *is* its gate, so entering it is asking that gate.
  Eight ways handed control to one such production — the sweep had merged every zero-width end-of-stream helper into a
  single `EndOfStreamGuard`, which kept the block header's name — and the call becomes the guard, said where the call
  stood. Lifting and hoisting feed each other, a callee left holding nothing but its gate being one its own callers can
  lift, so both run until neither finds anything; each round drops a call or moves a guard, and there are finitely many
  of both.

  An empty match among a way's actions is swept away with them, which is what let the eight through: `<empty>` is
  dropped from a sequence and a way's actions are a tuple on an alternative rather than a sequence, so the litter sat
  there and a walk looking for what a way begins with stopped at it. It takes no character and does nothing.

  The 18 left are not this step's to take: each hands control to a production that answers a character it cannot start
  on with an error rather than a refusal, so gating the way out would turn a parse that stops into one that takes
  another way — a different language. They want the commit lifted off the callee.

  **What answers for a failed cut is said, not carried.** A `(recover)` was the one scope phase 6 left as a node, on the
  reading that it is "a handler rather than a scope, with no close whose position means anything". The interpreter says
  otherwise: it takes what the parse holds where the region opens and puts it back where a cut unwinds into it, exactly
  as the window, the message and the code pairs do. What made it different is that it also *resumes*, and none of the
  others do — so its close carries information, and that is why it is the last to be written rather than the fourth.

  `lower-recoveries` writes it as `PushRecovery(recovery, resume)` before the call it covers and `PopRecovery` where
  that call returns, both operands named outright. It runs after `build-alternatives`, since where a way carries on only
  has a name once the way is a call and a continuation, and it mints two productions per site: one holding the pop and
  whatever the way carried on to, which is where the call returns, and one holding that continuation alone, which is
  where the unwind resumes — it must not pop, the unwind having taken the region off itself. The second matches empty
  where the way ended at the call it covered, which is a declared lapse of `only-root-empties`: naming where the parse
  resumes is the point, and the alternative is that it is implied by where the pair sits.

  The interpreter keeps the two lists this needs: the return stack — where each entered production carries on when it
  matches, pushed and taken back with the production trace, one for one — and the open recovery regions, where standing
  on the list is what says a region is open. Neither is checkpointed; each is balanced by its own pushes and pops, like
  the committed regions.

  **This is what a way carrying a scope cost.** `splice-conflicts` builds a spliced way out of a callee's parts and took
  the callee's recovery, dropping the caller's — silently, since a dropped handler only shows as a parse that stops
  where it used to recover. No shape had reached it before; a flattening that wrote out a called run of items did, and
  four recovery fixtures said so. With the pair there is nothing to drop: a rewrite that moves a way moves its actions.

  **A run of items a way calls is written out too, and a gate that decides nothing moves up.** `a P c` where `P` is
  `d e` does four things in a row and shows three, so a reading that walks a way to find what it does first stops at the
  call. `flatten-called-sequences` writes it out — leaving a call that stands last, since what a way does past its call
  is a production of its own by design — and `no-sequence-of-sequences` falls to a residue of 6, the recursion guard's.
  It could not land until a recovery stopped being a field a way carries, which is what the pair before it is for.

  Beside it, `no-gate-decides-nothing`: a gate on the only way of a body selects nothing, there being nothing to select
  between. It is **none at the door** — there are no gates until the ways are re-encoded — so the count names whichever
  step strands one. Five hoists do, 354 between them, each declaring it: they gate every way they can, the only way of a
  body included, and there the gate is an assertion the callers can discharge.

  `lift-gates-to-callers` moves what can move, 354 to **219**, and it moves by **splitting rather than rewriting**:
  `A = Gate A'` with `A' = Stuff`, so `A` goes on meaning exactly what it meant and each caller that can carry the gate
  calls `A'` instead. Rewriting `A` in place needed a side condition for every other thing that entered it — every
  caller must carry it, except productions entered by name, except the ones a fixture runs directly — and all three
  vanished with the split. The 219 left are called from ways that act before the call or reach them in tail position,
  where what the caller was entered on says nothing about the character there.

  What that exposed, and what it is worth: a production every caller gates cannot be seen to refuse, because the parse
  never enters it on a character it cannot start with. The coverage gate already credits a gate refusing to the
  production it guards — "otherwise gating a rule correctly would make it look untested" — and now says the same of a
  production whose refusals its callers have all taken up.

  The corpus cases where a committed run and a backtracking one read differently fall from 108 to **90**. The meter
  rises from 141 to 157 and `every-conflict-can-be-asked` from 5 to 17, which is what the copies cost: 36 ungated forms
  minted and every written-out run adding ways to be undecided about.

  **A choice a way of a choice calls is written out where the call stood.** `a | P | c` where `P` is `d | e` makes four
  decisions and shows three, the fourth behind a call nothing about the outer choice can see; written out,
  `a | d | e | c` is the same four ways in the same order with every one standing where a gate can be put on it.
  `no-choice-of-choices` counts the ones still hidden — **40** where the step runs — and `flatten-called-alternations`
  settles it, running until nothing moves and never into a choice that can reach back, which would write itself out for
  ever. It runs before a way is split into a call and a continuation, so every phase behind it sees the choice whole.

  This is the first thing to move the numbers the goal is measured by. `every-way-carries-a-test` falls **18 to 6**, the
  meter from 164 to **141**, and the corpus cases where a committed run and a backtracking one read differently from 129
  to **108** — on a grammar that got *smaller*, 819 productions to 798.

  The six left are one thing: a way handing control to a production that answers a character it cannot start on with an
  error rather than a refusal, each guarded by a `NegLookGuard`, all of them the auto-detected-indent copies of the
  block scalar's content.

  The same flattening for a called run of items is written and not landed. It hands `splice-conflicts` a way carrying a
  recovery, and that step builds the spliced way with the callee's recovery, dropping the caller's — a latent bug in
  committed code that no shape had reached before. The fix is not a side condition but finishing phase 6: a recovery is
  a snapshot-and-restore scope over the way's first call, the same shape as the three wrappers that became pairs, and it
  is the one that stayed a field on the alternative. `PushRecovery`/`PopRecovery` are defined, and the interpreter now
  keeps the return stack the pair needs — where each entered production carries on when it matches, pushed and taken
  back with the production trace, one for one.

  **Every question about a node is asked through a table that cannot answer for a kind nobody named.** `ir.Question`
  maps node kinds to what to do about them, and holds itself to three rules: a kind it was not told about **raises**,
  naming the reading; a handler nothing ever reaches is **reported** by `unexercised` after a whole-corpus run; and a
  kind named twice will not build. The first two pin every table to exactly the kinds that occur — what is missing
  raises, what is spare is reported — so a reading says nothing about what it cannot see, and a kind added to the IR
  touches only the readings that actually meet it, on the day they do rather than never. `NEVER` is how a reading keeps
  a wide group and takes back the part that cannot arrive, checked rather than believed. Thirteen readings are tables
  now, and every one of them is exercised to the last handler.

  It replaces a chain of `isinstance` tests ending in a fallthrough, which answers permissively for whatever spelling
  its author did not think of and reports its own blindness as a property of the grammar. That is not a hypothetical:
  one invariant was read as 26, then 8, then 44, then 16, then 4 for the *same grammar*, each number a walk silently
  defaulting on a spelling it did not recognise — a character wrapped in a `(token)`, a way's gate rather than its
  items, a repetition, a count the parse works out.

  What it found on landing: `is_one_char` carried a `(case)` branch nothing has ever reached, and `_is_actions_alone`
  claimed all 73 kinds while eleven reach it. Two overlaps that the order of a test chain had been settling silently now
  say which way they go — `ErrorAction` and `PushMessageAction` are actions and commits both, and a walk looking for
  what a way can be refused at must stop at them rather than step over them. And stripping "the scopes written around a
  match" turns out to strip only a `(token)`; no `(max)`, `(recover)` or `(wrap)` ever reaches it.

  Three readings answer with a named `Verdict` rather than a value, which is what lets a walk over a way's items be a
  table as well: take the item, step over it, stop, follow the call it makes, follow what it holds, or treat it as the
  commit past which failing is an error rather than a refusal.

  **What tells a conflict's ways apart, and how far in.** `determinize.verdict` walks the live ways to their first
  divergence: the characters, and factoring the shared prefix down to it puts the decision where the input makes it; the
  codes over the same span, which no depth of factoring separates, so the run is held and retyped instead; or no verdict
  at all, where the walk cannot be rooted, does not converge, or runs past its limit. Of the meter's **164**, **120**
  are decided by a character, **35** by the codes, and **9** have no verdict. `no-lookahead-left-to-factor` sums the
  depths of the first kind — **181** characters over 58 productions — and is the measure a loop of factoring runs on
  rather than the meter: factoring trades an undecided choice at one depth for an undecided choice one character
  shallower, so the meter can sit flat while every round makes real progress, where this cannot. A round takes exactly
  one character off each conflict it targets and no rewrite pushes a discriminator deeper, so it falls by the number of
  targets and never rises. No step names it yet; the round that does is what it was written for.

  Every step's grammar is swept of what the step leaves behind, the four passes running to a fixpoint since each feeds
  the others. Every body is flattened to the shape it denotes: a sequence or a choice of one item is that item, a nested
  one of the same kind is its items in place, and an `<empty>` in a sequence goes, matching where it stood and moving
  nothing — a choice of *nothing* staying, that being the path that never matches. It comes first because the merge
  reads shape rather than meaning, so two productions saying the same thing with a singleton alternation in different
  places do not merge until they are said flat. A production whose whole body is one ungated, action-free call is what
  it calls, so every reference to it becomes a reference to that callee. Productions that behave alike are spelled once:
  same parameters, and the same body once every reference in it is read as the group of what it names rather than by the
  name itself, which is what tells two loops apart from one loop written twice. And last, so it sees what the other
  three strand, every production no parse can enter is purged. None of the four changes what the grammar matches or
  emits. Only a merge is a rename, and only a merge is followed by a point of interest — a name a later step speaks of,
  tracked beside the grammar rather than in it, re-picked among what its holders became, a holder lost without successor
  being a loud fault rather than an absorbed drift.

  A fixture the sweep strands is not dropped: it pins to the last stage whose grammar can run it, guards that grammar
  token for token, and credits coverage from where it stands. The coverage gate holds a minted helper covered by the
  base it came from, as it does a monomorphic copy: a helper is a piece of the base's own body moved, so requiring more
  of it than of the body it came from would ask the corpus for what the untransformed grammar never needed.
  `check_normalize` holds every step token-and-event identical over the whole corpus — 712 conformance fixtures and 402
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
  the other answer untested, so each must also be seen to reject an input — by failing to match, which is what a `(cut)`
  inside it does too, the failure carrying the message the cut named. The exception is a rule that *cannot* say no, and
  those are computed rather than listed: totality is proved from the body's shape, so nothing asks for the fixture where
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

- Two invariants say what they are about. `every-end-of-stream-gates-a-leaf-way` asked two things at once: that an
  `EndOfStreamGuard` stands in a gate, and that the way it gates calls nothing. The second is untrue — a way that
  reaches the end still has the wrapping up to do and may hand it on, a continuation being where a callee left off and
  not a call made where nothing is left to give it — and it fired twenty times the moment a callee's ways were written
  into a caller that had one. What is left is `every-end-of-stream-stands-in-a-gate`, the same shape as
  `every-consume-is-protected-by-a-gate`: whether a character is there is a question, so it belongs where a way's
  questions are asked and nowhere else.

  `every-way-has-actions-or-a-call` is now `every-ungated-way-has-actions-or-a-call`, and reads none. A way that carries
  a gate, does its actions and then calls is one edge the machine runs; nothing forbids it. The rule has content only
  for a way with **no** gate, which is one still to be given one — and the two ways to give it one, hoisting a guard up
  out of its callee or writing the callee's ways in, both need those guards to reach where the way is entered, which
  `GUARD_CROSSES_ACTION` says they cannot do past what the way performs. So it is scaffolding that empties itself: where
  `every-conditional-way-is-gated` stands at none there are no ways left for it to be about. What "ungated" means is one
  reading, `_ungated_ways`, that both of them ask.

- Every invariant is held to changing no grammar, and every gate to finishing. A test is a question and never a change —
  which is what lets the counting pass hand the same grammars to all of them at once, in whatever order the cores take
  them — so `Invariant.__call__` reads the grammar before and after and refuses one that differs, in a `finally`, since
  a grammar a reading could not answer about is one the next invariant is handed all the same. And `gate.spread` stands
  a watchdog over each item and over its own wait for an answer: it names the item, prints where every thread stands,
  and takes the run down. A check that spins printed its last line and then nothing, and which line that was said only
  which worker happened to print last.

  Three walks were doing quadratically what they could do once. What is asked where each production is entered scanned
  every production for every name, every round, and every invariant re-ran it — now one index of the call sites, kept
  per grammar. `no-production-reaches-itself-unconsumed` grew each name's whole reach, a relation the square of the
  grammar — now one walk of Tarjan's components, which is what answers both who reaches themselves and what may be read
  before what. And the scope signature ran the whole grammar to a standstill, pinned whatever still moved, and began
  again; over 736 productions that is half a million walks and it does not finish. The pipeline's own check runs in
  twenty seconds.

- A step says which of three things it does to an invariant, `establishes` being the third: what reads none of the
  grammar it hands on and was no question in front of it, the shape it is about being what that step builds. `settles`
  is then only what it says — a count handed over broken and taken to none — and a step naming one that was already none
  is the fault it always was. A **claim** is a step that transforms nothing and establishes what it names, which is what
  `holds-at-the-door` and `holds-once-optionals-are-ways` were saying with the wrong word.

  What that buys is a hard fault where there was a silence. An invariant used to be asked of every stage, and a reading
  that met a shape the pipeline had not built yet raised, and the raise was caught and read as "not a question here" —
  so a reading broken outright and a reading asked too early were the same answer. Each invariant is asked from its own
  step onward now, and a reading that cannot answer there is a fault.

- Every gate reads what stands in front of it at most once. Two peeks in one gate are one question said twice — two
  `LookGuard`s are the set they both admit, a `LookGuard` beside a `NegLookGuard` the set the first admits and the
  second does not — and the hoists are what bring them together, one moving a question to where another already stands.
  So `every-gate-looks-ahead-at-most-once` is established where ways with gates are built, each hoist declares its lapse
  of it, and a `merge-gate-peeks` behind each says the sets as one. A gate mixing kinds that cannot fold into one set —
  an `EndOfStreamGuard`, which holds no set, or a `LiteralPeekGuard`, which holds a run — raises rather than being
  stepped over.

- A call is written into the way that calls it where doing so pays, and the site itself is what says so. What decides is
  what the site would leave, not what it was handed: one way nothing has gated goes, the callee's ways stand where the
  call did, and where any of those comes out ungated the site is left alone. So every site taken lowers the count by
  one, the walk has a floor, and it runs until nothing moves — eight rounds. Judged instead by what a site is handed, it
  writes a way out on the promise that a later pass will gate it, and that promise is what has no floor: the state such
  a way carries on at is composed afresh every round, so each pass finds a new one to write out and descends. Every
  guard against that — a bound on the passes, a circle to refuse, states named by what they hold — is a guard against a
  rule that was simply wrong. `every-conditional-way-is-gated` reads 58 over 1024 productions, and the phase is three
  steps: the expansion, the hoist to the callers with its merge, and the expansion again.

- The sweep's duplicate-merge reads each body once instead of once a round. Two productions behave alike when their
  bodies match with every reference read as the group of what it names, which is a partition refined until a round
  splits nothing — and each round used to write every body out afresh with the group numbers substituted in. A round
  only ever moves a name between groups, never moves where it stands, so the shape with the names taken out is built
  once and a round compares a tuple of group numbers beside it. `check_normalize` runs in 14 seconds rather than 41.

- The crossing table's three values are read by the gate, which is what they were for. `GUARD_CROSSES_ACTION` answers
  `True`, `False`, or nothing at all, an unnamed pair refusing the move *and* being recorded — so that "no" and "not
  yet" cannot be mistaken for each other. Nothing called `unnamed()` or `unconsulted()`, so the difference existed and
  was never reported: a walk took an unnamed pair for a worked-out refusal and looked settled while it was only
  ignorant. Wired to the gate, it named `LookGuard` in front of `OpenWindow` at once — the pair that was holding a flow
  mapping's implicit key ungated, and one the table already answers a line above, a window bounding what a committed
  consume may take and no lookaround at all. It named four cells nothing asks, which are gone.

- **A comparison is named for comparing, not for a column.** `ColumnLtGuard` and `ColumnLeGuard` hold two values and
  assert an order between them. The column appears in 12 of the 70 the grammar holds and is never the left operand; 58
  mention no column at all, relating `n`, the block scalar's floor, the length of a match and the literal zero. The
  docstrings said "the first indentation is less than the second", wrong the same way — two of the eight shapes name
  neither an indentation nor a column. They are `IsLessThanGuard` and `IsLessEqualGuard`, spelled `(<)` and `(<=)` where
  the grammar writes them, and the two `_admits` handlers, each named for one shape out of the four it answers, are
  `_is_less_than_admits` and `_is_less_equal_admits`.

  The crossing table said a comparison crosses a `SetVarAction` because "every comparison names the indentation, a
  literal or the length of a match". It does not: `f <= column` and `f <= n` name the floor, which is one of the two
  slots such a write writes. No instance collides, so the grammar was never wrong — but the entry licensed one that
  would be, the same shape that crashed `b-l-folded` when a turn's close was let past a code's pop. It is asked of the
  two in hand now, `_reads_nothing_the_write_writes` reading the operands for the slot; `_is_using` cannot answer it,
  looking for a `ParamValue` where a read of `f` is a `GlobalValue` by then.

- **A turn that closes where it opened takes a path no input takes, and the path goes.**
  `every-conditional-way-is- gated` is **settled**, where it stood at 58 when the phase began. The three ways left were
  one shape: a path opening a turn that must take a character and reaching that same turn's close — the same `pair` on
  both — with nothing between them that takes one. The close asks whether anything was taken since its own open, so on
  such a path it refuses whatever the input is; the way it stands in fails there and falls to the one behind it, which
  is what happens with the path dropped, one refusal sooner. Dropped and not cancelled: what refuses everything is the
  turn's whole point, and annulling the open against the close would make the path *succeed* and a run over a body
  matching empty spin. `l-yaml-stream` wraps `l-document-prefix`, whose two halves each offer an empty way, which is why
  the shape arises there and only there. `("EndMustConsumeGuard", "StartMustConsumeAction")` is gone from the crossing
  table with the paths that asked it.

- **A crossing the kinds cannot answer is answered of the two in hand.** Whether a lookaround may be asked in front of
  the write that says what may not match at a start of line depends on which lookaround and which set: it matches its
  item through that refusal, so it reads the set standing where it is asked — but where the states it admits and the
  states what is forbidden can begin taking a character in do not meet, no position answers it differently either side.
  A `Crossing` entry may now name a question rather than an answer, and `_reads_none_of_the_forbidden` is the one that
  does, meeting `_admits` with the accepted space. Named with a question, the pair is worked out — the instance decides
  — so it is neither of the table's two faults. `l-explicit-document` forbids `---` and `...`, whose accepted space is
  `-` and `.`, and the gates its ungated way reaches ask about a space, a tab, a carriage return, a line feed and a
  `\r\n`: disjoint, so the write cannot change one of their answers. `every-conditional-way-is-gated` reads **3**, where
  it read 7, and the three left are one shape — a turn's close behind its own open, which in front of that open has
  nothing to take off the stack at all.

- **A way nothing has gated is said as the paths it is.** `flatten-ungated-call-trees` walks such a way through what it
  calls *and* what it carries on to, and every path reaches a gate: 710 of them, none running out, none circling, none
  ending on a body that is not a choice. A way holding no call is not the end of a path — what runs next is the
  innermost thing still pending, and the gate stands in there, which is what a walk that stopped at the missing call
  reported as 83 paths reaching nothing. Each path becomes one way: the gate it found, everything the path performed in
  front of that gate, the call standing there, and a chain of states running what is left — the leaf's own continuation
  first, then each pending one innermost outward. Where the leaf only carries on, the head of that chain takes the free
  slot rather than being buried a state deeper, since a question about a limited run is asked by the first call a way
  makes and one state further on the run has been taken away. `every-conditional-way-is-gated` reads **7** over 1324
  productions, where it read 58.

  The seven are one refused crossing each. Four reach gates asking a lookaround behind a `SetForbiddenAction`, three a
  turn's close behind its own open. Nothing splits a gate — every one of the 710 holds a single guard — so asking the
  guards that may come up here and the rest a state deeper, which would gate a way on part of its question, has no case
  in this grammar and is not written.

- **`expand-called-ways` is gone, and the two steps that served it with it.** Writing a callee's ways where the call
  stood is one level of what the flattening does over the whole tree, and the count lands at 7 either way. What it was
  worth was size — 1165 productions against 1297 — and what it cost was hiding a defect: with the expansions out,
  `hoist-guards-to-callers` breaks the corpus by itself. `merge-gate-peeks-2` had nothing left to merge and
  `hoist-guards-to-callers` no longer lapses `every-gate-looks-ahead-at-most-once`. The pipeline is 41 steps.

- **A turn's close is asked by taking its own open off the stack, so a code standing above it refuses the question.**
  `GUARD_CROSSES_ACTION` let an `EndMustConsumeGuard` cross a `PopCodeAction`, reasoning only about where the parse
  stands — a code being neither the input nor a count. But the guard *pops*, and a code closed behind it was opened in
  front of it: asked early, the open it reaches for is the code's. `hoist-guards-to-callers` moved one into a gate in
  front of exactly such a pop, and `b-l-folded` crashed on it. The pair says `False`.

  Four pairs the table had never been asked are named — a comparison in front of a `SetVarAction`, which reads the
  indentation, a literal or a match's length and never the two variables the parse holds; and a `StartOfLineGuard` in
  front of a `PushIndentAction` or a `SetForbiddenAction`, neither of which is where the parse stands in its line.
  Thirteen pairs only the expansion ever asked are gone, the table staying pinned to what occurs.

- **No action is ever handed back; only a guard refuses.** A refusal is where a choice goes on to its next way, and an
  action reached through a gate that admitted it does its work or the gate lied — which is a crash and not a parse.
  `_can_be_refused` said exactly that in words and its table said otherwise, listing the consumes among the kinds an
  input can hand back. It now says one thing: every action and every consume answers no, every guard answers yes, and a
  set standing where a match is expected answers yes because it is a match rather than either.

- **A consume names the set it consumes, and consumes it.** `ConsumeCharAction` was defined as "the character the gate
  found", so a gate hoisted to a caller took the consume's meaning with it and what a way consumed had to be worked out
  from whichever guard happened to stand in front. It carries its own `set` now. With that, the interpreter can ask of
  each consume what it actually did — rather than re-probing the set, which answers a prediction instead of the event —
  and the answer over every stage and the whole corpus is that **no consume ever consumed nothing**. A run of a class is
  written as a gate that found the class beside a scan, so the scan always consumes; `ConsumeSpanAction`,
  `ConsumeLimitedSpanAction` and `ConsumeTrimmedSpanAction` join `ALWAYS_CONSUMES`, and `_does_scan_read` — which
  recovered that fact from the gate — goes, along with `_ahead_of_gate`, `_ahead_of_any`, `_ahead_of`,
  `_narrowed_ahead`, `_entering_guards`, `_spans_meeting` and both scan splits.

- **`ConsumeLiteralAction` is gone, never having been made.** Nothing in the generator ever constructed one — the whole
  history has no call — because every literal the grammar spells, `---` and `...` and a directive's `YAML`, is consumed
  by `ConsumePeekedAction` under the `LiteralPeekGuard` minted beside it. A kind every question had to answer for and no
  input could reach.

- **The two subspaces are read from different halves of a way.** `_accepted_way` intersected the way's own gate, so
  `accept ⊆ gate` held by construction and the two could not disagree. It reads consumes and calls only now, the gated
  subspace reads guards only, and their agreement is worth something. `accepted-and-gated-charsets-are-equal` compares
  the union over the paths into a production rather than each path alone: `l-folded-content` decides whether a space
  stands here and its two ways carry on into the same tail, so the branch that consumed a space reaches that tail
  knowing nothing about the next character while the branch that found none still knows there is no space — one path
  narrower than the tail consumes, and their union exactly what it consumes.

- **A leaf way takes what it is entered on, and every path reaches one.** The ways holding no call answer entirely by
  their own actions, so the space a parse enters one in and the space it accepts can be held to each other before
  anything is moved. `_asked_where_entered` already gave the guards asked along each path into a production, gathered
  from the last take onward; met with a way's own gate those are the states it may be entered in, and two invariants
  read them. `accepted-and-gated-charsets-are-equal` compares standing by standing rather than whole subspaces: a caller
  knowing it stands at a line start where the way asks only about the character knows more than the way asks, which is
  not the two disagreeing — 68 leaf ways take a character, over 78 paths, and the characters agree on every one.
  `every-path-reaches-a-leaf-way` asks for some way rather than every way, `l-document-prefix` offering one that takes a
  byte order mark and one that takes nothing where the path reaching it at the end of the stream can take only the
  second. Read the stronger way it called that a fault, which is how the relaxation was found.

- **The subspace algebra stops being the cost of asking.** Reading the two invariants at every stage put
  `accepted_spaces` on the counting pass, where it took twenty seconds and tripped the watchdog that refuses an
  invariant slower than ten. Three things were wrong and none of them was the shape: `under` scanned the regions for a
  standing where a lookup answers, and was called fifteen million times; an intersection was computed as the complement
  of one side subtracted from the other, where `chars.intersected_spans` merges the two directly; and the fixpoint swept
  every production every round, where a production's answer can only change when a callee's has, which is a list of what
  is left to do. Together 20s to 1.6s, the same answer to the span.

- **A match nothing makes says so.** A `(case)` on a finite parameter named some of its values and was silent about the
  rest, which its own reading takes as declining them — so what a production did under such a value was read off an
  absence, and "it matches nothing" and "nobody asks" are two different things an absence cannot tell apart. The
  specialization made that silence an alternation of no ways, and every walk past it had to read an emptiness as a
  refusal: `_split_ways` reported `l-recover-entry` as having no empty way, which is true of a production with no ways
  at all and means the opposite of what it reads as. Now `<fail>` is the twin of `<empty>` — one is what every input
  makes taking nothing, the other what none makes at all — the six cases that were silent name every value of their
  parameter, and `validate_grammar.check_total_cases` holds every case to that, so the specialization raises where a
  value has no branch instead of minting the emptiness.

- **The ways nothing takes come out, and nothing after sees one.** `prune-failures` is a phase of its own behind the
  specialization, which is the earliest it could be: until the case is specialized a `<fail>` is a branch, and what a
  branch says is not yet what a production does. A choice drops the ways nothing enters and is itself one where that
  leaves none; a run holding one never matches; a call of a production that matches nothing matches nothing; and a
  recovery whose handler nothing enters is no recovery, the cut going on unwinding to the handler above as it did with
  one that never matched. A commit keeps what stands under it, a refusal there being the error it names rather than the
  choice's. Twelve declines go to one at the specialization and to none at the phase, the grammar loses 21 productions
  nothing could reach, and `no-fails` reads none from there on. Dropping the dead recovery stopped the non-recovering
  policies' compact mappings merging with the indentation-bounded one's, which showed the corpus had no `r=i` fixture
  holding a compact mapping — a hole the merge had been hiding, and `l-yeast-stream.recover-compact.r=i` closes it.
  `generator/regen_fixture.py` is what wrote it: a fixture's stream comes from the interpreter, an indent or a white
  token carrying trailing spaces that a hand loses.

- **A guard says which states it lets a parse through in.** `normalize._admits` reads every guard as a
  `spaces.SubSpace`, and `accepted_spaces` says where each production can begin taking a character — the walk over a way
  carrying what it can still stand in having taken nothing, so that a guard past a take, which asks about a later
  position, narrows nothing. Sound rather than decisive: a comparison between two of the parse's own values fixes no
  coordinate and admits everywhere, which is true of it, and a literal's first character is a constraint where the rest
  of the literal is a residual the axes never speak for. So a state a gate admits that the space refuses is a hole the
  grammar really has. Of 1370 guards in the final grammar 1316 name a coordinate, the 54 that do not being the
  indentation comparisons that relate `n`, the column, a match's length and the floor to each other; and 959 of 1055
  productions carry a real character set. Whether a way can take nothing stays `_split_ways`' answer and is not folded
  in: a production that succeeds taking nothing succeeds anywhere, which is true and says nothing, and holding the two
  apart is what stops it poisoning every caller through the fixpoint.

- **A subspace says which states a parse can decide in.** Every guard asks about one axis of a small space — the
  character in front, the character behind, whether the parse stands at a line start, whether it stands under
  indentation, and two bits of its own bookkeeping — so what a parse stands in when it decides is one point of it, and
  what a gate admits, what a way takes and what a call site can reach are each a subset. `spaces.SubSpace` is that
  subset: the characters admitted under each of the 32 standings, exact because every axis is finite, with union,
  intersection and containment computed standing by standing. The end of the stream is the character axis's own value
  and not the absence of a character, a way entered there being a way entered somewhere; a comparison relating two of
  the parse's own values is no axis at all, and a guard asking one constrains nothing. `check_spaces` judges the algebra
  by the states it holds rather than by itself — the operations are compared against set arithmetic over an enumeration
  of every standing and an alphabet spanning each boundary the cases name.

- **The span algebra is said once.** `normalize` carried its own copy of merging and subtracting codepoint intervals,
  character for character the one in `chars`, where the character model the decoder is built from already keeps it.

- **Nothing a way performs can fail.** A counted scan took `n` characters of a class or none at all, and no gate could
  protect it: a gate speaks for the character in front of it and not for `n` of them, so a scan asked for more than is
  there was a way failing on what it does rather than on what it decided — 52 of them in the final grammar, the one
  failure edge in the machine that no question stood in front of. `x{n}` is now a run of up to `n` of the class, which
  takes what is there and says whether it reached the limit, and the guard behind it asks. The taking always matches,
  the refusing is a question like any other, and `ConsumeCountedSpan` is gone from the IR. The shape is `x+`'s, which
  the pipeline already wrote: a scan that may take none, made to take one by the `LookGuard` in front of it. So the
  split on the count is made where the run and its guard are minted, and `split-counted-spans-on-the-count` — which
  gated the scan afterwards — is gone with the kind it gated.

- **A question says what it has not met, and answers for it or does not.** `ir.Question` took `NEVER` for a kind a wide
  group named but the reading never meets, which claimed an impossibility nobody had proved. Two lists replace it, and
  they differ in whether there is an answer: `untested` names a kind a family answers for that nothing has ever asked
  about — one that arrives is answered from the family, recorded, listed and fails the gate, so the decision is made
  rather than passed over — and `unknown` names one nothing answers for at all, which raises where it stands. Naming a
  kind untested that no group of the reading names is refused: there is no answer to call untested. Splitting the two
  standing uses proved the distinction real — seven kinds their families answer for, and `ChoiceState`, which nothing
  does.

- **What a run reaches is recorded by the run.** Coverage was collected by rebinding `interpreter.match` and
  `interpreter.evaluate`, so a handler reaching a production any other way was missed and the report read exactly like a
  covered one. A run now fills an `interpreter.Coverage` where it enters and hands back productions, and the gate reads
  it: there is no outside to bypass. The same reasoning retires `interpreter.match`'s own dispatch chain — it is a table
  over the kinds, but not an `ir.Question`, since a question is called through its type and a few thousand of those
  nested is all a C stack holds, where this matcher recurses once per grammar step.

- **The families of node kinds are named in one place.** Every list of two or more kinds lived beside whichever reading
  used it, so the same idea had several memberships and nothing compared them. They are one section of `ir.py` now, and
  reading them together found two that did not match their own words: the scan family left out the trimmed scan while
  saying "a scan of a character class, which may be asked for none at all", and the zero-width family left out the
  gate's literal form though it matches without consuming, which `validate_grammar` had been patching around inline. A
  third, `TAKES_CHARACTERS`, was `CONSUMING` narrowed to the spellings one phase uses — it made a way holding a
  `OneCharSet` count as taking nothing, and is gone. What a family is called now says which category its kinds are drawn
  from, and `ZERO_WIDTH` is `ASKED_NOT_TAKEN_NODES`: taking nothing is what every action and guard does, and holding
  characters that are asked about and never taken is what those five have.

- `no-empty-nodes`, settled by `build-alternatives`. A way is its gate, its actions and the calls it hands control to,
  and an empty match is none of the three — a way matching the empty input is the way with no gate, no action and no
  call — so there is nowhere in the machine's own words for one to stand. The lowerings mint them freely, 5 in the base
  grammar rising to 160, and the last 99 go where the ways are built.

- `Lt` and `Le` are `ColumnLtGuard` and `ColumnLeGuard`, which is what they compare.

- **A kind's name says the category it is in.** An action, a guard, a call, a wrapper, a character set, a tree the
  lowerings remove, a state the machine has, a value the parse works out, a part a node holds: every kind ends in the
  one it belongs to, so a use site says which without being read against the families in `ir.py`. `CharSet` and
  `Wrapper` needed nothing, their names already ending in their category, and `Char` is `OneCharSet` — the set of one,
  `CharSet` being taken by the one holding spans. `GUARD_CROSSES_ACTION` keys its pairs by `type(node).__name__`, so its
  68 answers are renamed with the classes: left behind, the table would name no pair at all, and an unnamed pair refuses
  the move exactly as a worked-out no does. That is what the step-changed-nothing assertion caught.

- `every-span-question-follows-its-run`, established where the run and the question about it are made. The guard reads
  what the action in front of it did, so that action has to be the run: the item before it among the things a way
  performs, or — where minting the guard states makes it the head of a state — the last thing every way calling that
  state performs, the call being the first that way makes. A call made past another call is entered wherever that one
  left off, and what the run did has been taken away by whatever the callee performed. The interpreter already refused
  this where it happened; asked of the grammar instead, a step that moves the two apart is a fault where it stands.

- **Every question about the grammar is a reading.** Five were still answered by an `isinstance` chain, and the audit
  that found them is worth keeping: 43 functions name three or more kinds, but a family named in a membership test is
  what the families are *for*; of the 13 that answer differently per kind, 10 are step transforms reshaping named kinds
  and passing the rest through, which is a rewrite and not a question. The five are `validate_grammar.consumed`, what a
  node takes and whether an annotation covers it; `check_grammar_docs.emitted`, the codes it emits;
  `normalize._peeked_question`, what a peek asks; `normalize._reached_forbidden`, what is forbidden where a match ends;
  and the walker inside `every-character-question-is-a-character-set`. Two of them answer by `yield from` and by
  appending rather than by returning, which is why a search for a chain of returns does not find them.

  The peek's was the one that mattered. Its tail handed the node back as its own question, so a shape nothing had looked
  at said "ask about this" and three readers believed it — `lower-runs`, `span-consumes` and the character-set
  invariant. The other two chains ending in `elif isinstance(node, ir.KINDS)` said in their own docstrings that a kind
  named nowhere raises, and could not: that arm names every kind there is. Both read the vendored grammar before any
  lowering, so the canonical spellings are what they now call untested rather than what they walk into by default.

- The nine categories are named as families — `TREES`, `STATES`, `PARTS` and `CALLS` join the five that were already
  there — and between them they cover all 79 kinds exactly. What made that worth doing is that a reading naming "every
  other kind" by category raises for a kind in no category, where one naming `KINDS` cannot.

- A `Prod` is no kind of node and is not among them. `KINDS` is every kind of thing that stands inside a body, and a
  production is what a body hangs off — a name, a parameter list and a body — so no walk of a body meets one, which is
  why it alone fell into no category. A reading naming it is refused now, a table answering for it being one that
  answers for what no walk asks.

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
