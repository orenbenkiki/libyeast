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

- Grammar normalization: an ordered pipeline of semantics-preserving grammar-to-grammar transformations that carry the
  hand-authored grammar toward the canonical form a state machine falls out of — each terminal a character set, each run
  a repetition of one. `lift-chomping` makes the chomping `t` lexical: `c-chomping-indicator` matches an indicator and
  sets `t`, which the block scalar reads two productions on through the env, a set that is no switch — so it inverts the
  setter into a `(case) t` matching the indicator for a given `t`, and turns the block scalar into an ordered choice
  over strip/keep/clip, each branch fixing `t` to the literal it hands the header and the content alike. `monomorphize`
  then specializes all three finite parameters — the context `c`, the now-lexical chomping `t`, and the resume policy
  `r` — away: every production it reaches copied once per combination of their values, its `(case)`/`(flip)` on them
  evaluated to the copy's, and the values fixed into the copy's name (`ns-plain-char_c_flow-in`) rather than passed,
  following references from the root's copy under each resume policy — the machine's start states — so only combinations
  that occur are made; a value at its default (the no-resume `r`) is not in the name, so the root stays
  `l-yeast-stream`, and the recovery re-enters the copy the resume policy names. Only the integers `n`, `m` and `f` stay
  parameters. It rests on a rule the grammar now keeps: a finite parameter is only ever switched on, so where an
  implicit key's commit softens by context — a key that will not parse being simply not this key — the grammar says so
  in a `(case) c`, its key branches the bare item and its `else` the commit, and the parser's `(commit)` is the same
  hard cut everywhere. `(case)` grew that `else` for it. `lower-optionals` and `lower-plus` drop the `x?` and complex
  `x+` spellings. `eliminate-empties` then makes the grammar proper — no production, the ones a parse enters by name and
  a `(recover)`'s target aside, matches the empty string by shape. A nullable production keeps its own body and gains a
  minted copy of it that must read; each `Ref(P)` becomes `P_consuming | residue`, the copy that reads tried first and
  the zero-width residue — the guards, markers and emitters the empty match is made of — its fallback. The choice stands
  exactly where the call stood, so a `Push`/`Pop` or `Open`/`Close` pair around it is never split or duplicated, and
  what was decided blind inside `P` becomes a choice sitting next to what follows it, whose first characters are what
  decide it. Inside a repetition the residue is dropped rather than distributed: a repetition already means "as many as
  there are, including none", so it absorbs the empty match, where a way that takes nothing would spin — which is what
  lets a `x*` or `x+` over a nullable `x` become a recursive helper at all. A purely empty production, `e-node` among
  them, has no consuming copy and dissolves into the residue at each site. The original is left as it stands, and with
  every call to it rewritten the purge is what takes it — so a fixture that enters it directly pins to the stage before,
  and the error path the original spells is untouched, an illegal `--- |0` rejecting exactly as it did. A copy is made
  to consume by keeping only the ways that read: an alternation drops its empty way, a sequence that already reads
  distributes its calls in place, and a sequence every part of which may be empty splits into an ordered choice over
  which part is the first to read, the parts before it held to their empty match in the order the parse already tried
  them — which is where a `<start-of-line>` or an `<end-of-stream>` comes up, those being what `s-separate-in-line` and
  `b-comment` match empty *by*. What a run over a character class takes, none of it included, is not a way but a value
  the scan decides off the input — `s-indent(0)`, an `s-indent(≤n)` at a line's start — and the test for that is
  `span-consumes`' own, so the two agree by construction on what a scan is. The productions a parse enters without a
  call — the root's copy under every resume policy and `l-recover`, where a failed cut lands — keep their empty ways,
  having no call site to hold the choice. The step checks its own post-condition on what the parse can enter: a
  production still matching empty, or a repetition still repeating what can, is a fault raised where it was made rather
  than a puzzle for a later step. `declare-bindings` completes it: a value leaves a production only through a declared
  parameter passed by reference, so a binding a production does not declare is written into its own frame and dropped on
  return, surviving only where the write stands above every frame that reads it — which a distributed residue, or any
  later step minting a helper out of the middle of a body, is exactly what breaks. Each production is given the
  parameters its own body binds, the `(if)` that will lower to a `(set)` counted with the `(set)`s, so every minted
  helper carries them and the write reaches the reader wherever the split lands; no call site changes, an argument a
  caller does not give leaving the parameter the ambient value it read before. What a production binds for itself is
  what it detects — the block header's auto-detected indent `m`, the leading empties' floor `f` — which is why no caller
  passes one and no fixture names one: a fixture entering a production that declares one enters with it unset, as a
  fresh parse does. `trim-runs` recognizes a plain or quoted scalar's in-line run `(s-white* content)*` and rewrites it
  as a single trimmed run that keeps inner whitespace and gives back trailing; `hoist-char-runs` factors a run over an
  almost-character-set — a URI, a tag, quoted content, its handful of escapes and guards the exception — into a
  character-set bulk with a slow path, seeing through a `(---)` difference to reach the set beneath, and
  `hoist-trimmed-runs` factors a trimmed run the same way so its common runs are the two-set trimming scan
  (`trim-run (trim* uncommon trim-run)*`, the leading `trim*` re-taking what the run before it gave back, which keeps
  the whitespace before a mid-scalar `:`); `lower-star` turns each remaining complex `x*` into a right-recursive helper;
  `lower-tokens` dissolves the `(token)` scope into actions — `PushCode(code)` … `PopCode` around its item, the run code
  an explicit value rather than a scope the tree shape implies, the push putting the code it displaces on the parse's
  own stack for its own pop to take back. One stack and not one per production, so a pair a factoring splits across a
  call closes off the same stack it opened and where the halves stand is nothing the mechanism has to know; the same two
  actions stand everywhere, which is what a factoring compares. A pop with nothing pushed is refused, and a raise clears
  the stack with the codes it would have restored. `lower-wraps` dissolves the `(wrap)` into the pair of `(emit)`s it
  always was. `lower-windows` turns a `(max)` into `OpenWindow(limit, message) … CloseWindow`: windows do not nest —
  only the outermost applies, an inner one being inside the budget the outer already bounds — so the pair is a count of
  the opens standing, the window set where the count leaves zero and cleared where it returns to it, and a close with
  none open is refused outright. The overflow past the edge fails the window's cut in the run itself rather than a
  wrapper catching it. `lower-binds` rewrites each `(if)(set)` as its condition and the `(set)` that reads what it
  matched, with no scope between them. `lower-commits` dissolves the last scope: a `(commit)` becomes
  `PushMessage(message) … PopMessage`, the committed region bracketed exactly where the scope stood — a failure that
  unwinds past an unclosed push raises its message, one past the pop backtracks softly, the `reached` flag of the old
  scope now the pop having run. The extent is written in the grammar rather than implied by the tree shape, so it
  survives every later split; a helper may hold one half of the pair, the pop pairing with its push dynamically and
  reading no frame value back, which is what lets it be cut where a `(token)`'s code must be passed. A gate is never
  hoisted past a `PushMessage` — refusing entry to a region the grammar committed to must stay the error it names, not
  soften into a skip — which `gate-hoist` and the alternative shaping both hold to. `flatten` then splices nested
  `Seq`/`Alt`, drops the `Empty` no-ops a sequence carries, and unwraps singleton `Seq`/`Alt`. `span-consumes` then
  rewrites every repetition over a character class as the single scan the canonical form spells: a `Star` becomes a
  `ConsumeSpan`, a `TrimStar` a `ConsumeTrimmedSpan`, a `({N})` repetition a `ConsumeCountedSpan` — a run of exactly so
  many, which keeps an escape's eight hex digits and an indent's `n` spaces each one scan rather than a state per
  character. `literal-consumes` rewrites what a sequence's own shape spells rather than a repetition: characters
  standing in a row become a `ConsumeLiteral`, one comparison that either stands whole or takes nothing — `---`, `...`,
  a directive's `YAML` or `TAG`, a break's carriage return and line feed — and the same character class standing in a
  row a `ConsumeCountedSpan`, a URI escape's two hex digits among them. `lift-choices` gives every nested choice a
  production of its own, so a choice is only ever a whole body — the canonical shape, where a production is either a
  terminal character set or an ordered list of alternatives; a choice in a character class or a lookahead is a set or a
  pattern, not a decision, and stays where it is. `single-consumes` splits an alternative down to the one gate-needing
  terminal its gate peeks — a single character, or a char-set `x+`, whose at-least-one is exactly what a gate on `[x]`
  proves — and `binarize` down to the canonical form's two production calls, each moving what follows into a fresh
  `_<N>` helper called in its place, so `A -> B C D` becomes `A -> B A_1` and `A_1 -> C D`. Two calls is one stack push
  per edge. What a helper may hold is bounded by the scopes a production's frame carries: a `(max)` window is not
  passed, so a moved segment opens and closes it together, while a `(token)`'s code is — a helper split out of the
  middle of one takes the code its caller was entered under as a parameter, so its close restores the outer code rather
  than the pushed one, and a declared parameter beats the scope in force where a production is entered.
  `alternative-shape` then writes each production the way the state machine reads it: a terminal character class, or a
  `Choice` of `Alternative`s, each a `Gate` to enter on — the character the next one must be, and the zero-width
  conditions that must hold with it — the actions it performs, and up to two productions, the call and the continuation
  to resume at when it returns, which is one frame pushed per edge. Nothing follows the continuation, since a production
  returns exactly when it does, so an alternative is cut at its first call and what follows becomes a continuation of
  its own — a scalar's `end` marker after its last call included, which is how it gets a state to sit in. A gated
  character is taken by a `ConsumeChar`, which consumes exactly one, always: the gate has found it, so one that finds
  nothing is a gate that did not do its job, and the interpreter and the generated parser both say so rather than
  matching nothing. A char-set `x+` becomes its gate's peek with a `ConsumeSpan` behind it — the gate proves the span
  takes at least one, so a plus costs no node of its own — while a `x*` stays an action, a scan that cannot fail needing
  no decision. `lower-recovers` then moves each `(recover)` from the action it stood in onto the edge it protects: the
  alternative calls the guarded production as its `first` and names the recovery in `recover`, so the frame pushed for
  the call is the one a cut unwinds to — the handler is the frame, the resume point its own return, and the calls the
  alternative already had move behind it, into a minted continuation helper where there were two. Failure carries no
  continuation of its own: a dead-end is an error, the unwind searches the stack for the nearest recovery-carrying
  frame, and the parse resumes at that frame's return as though the guarded call had matched — which is why a recovery
  rides the push where a message brackets a region. With it the residue is fully spelled: no scope and no repetition
  stands where the canonical form wants a gate, an action or a call, and the count the check keeps is the net that puts
  a leftover back on the board. `refine-indents` then turns each exact-count indentation call into the one maximal scan
  judged after the fact, in the shape `s-indent-le` already spells: an alternative calling `s-indent(k)` with a
  continuation behind it takes `PushCode(indent) ConsumeSpan(space) Le·Le PopCode` inline — the count an equality on the
  length of the indent token the scan is building — and promotes the continuation to the call, wherever the
  continuation's first set is pinned, excludes the space, and cannot match empty, so the maximal scan steals nothing an
  exact count would have left. Counted consumes of different `k` share no literal prefix; refined, they are the
  identical scan with the counts as residual guards, which is what the prefix factoring needs to see. `factor-prefixes`
  grew two admissions to match, each locally checked: an identical maximal scan joins the prefix where every leftover's
  first set is pinned, cannot match empty, and excludes the scanned set — a shorter run then leaves a character no
  leftover admits, so the maximal run is the only one that proceeds and the factoring reorders nothing, a condition the
  refined indents meet by construction. `gate-hoist` then gives an alternative that goes on a call the characters that
  call can begin with, so the decision is made where it is taken rather than one production down — a first set falls
  straight out of the shaped form, being the union of a production's alternatives' peeks. A union too wide is safe,
  since the peek only has to hold wherever the call could match, and one that cannot be pinned down leaves the gate as
  it was; an alternative whose actions reach a `(cut)` before the call is left alone, since the cut has committed and a
  gate refusing first would take that commitment away. A character to go on is carried by 1037 of the 1183 alternatives
  that consume or call; the 146 without one are determinize's to give. An alternative the call hoisting cannot reach — a
  nullable callee, a run before the call, a chain of both — is `gate-hoist-wide`'s, peeked as its whole begin set,
  actions, call and continuation together, where that is pinned down and cannot match empty: an empty match must stay
  enterable with no character left to peek, so a nullable alternative keeps its empty gate for the follow-set
  certificate to decide. A hoisted gate makes the decision the production it calls used to make — where the character is
  not one that call can begin with, the call never happens — so the coverage gate counts the gate saying no as that
  production saying no, or gating a rule correctly would make it look untested. The coverage gate holds a minted helper
  covered by the base it came from, as it does a monomorphic copy: a helper is a piece of the base's own body moved, so
  requiring more of it than of the body it came from would ask the corpus for what the untransformed grammar never
  needed. Determinism is then tracked production by production rather than claimed all at once:
  `deterministic_productions` names every production whose decisions are statically proved one-gate-decidable — a
  terminal and a single-alternative choice decide nothing, and alternatives peeking pairwise-disjoint character sets can
  hold at most one gate, so committing to the first that holds is the parse backtracking finds; a guard on a gate only
  narrows the one candidate its peek admits, deciding nothing between alternatives, and its refusal falls through
  exactly as backtracking does — and the interpreter enters exactly those committed, the whole gate evaluated, no second
  try, backtracking everywhere else. An ungated last alternative — the empty way out of a loop, or a call whose first
  set no gate could pin — certifies too, where everything it can begin with, its own first set widened by the
  production's follow set where it may match empty, is pinned down and disjoint from every peek: where a gate holds the
  last way cannot succeed, entered fresh or backtracked into, and for a last alternative entered-and-failed is the same
  as not entered. `split-conflicts` then confines every overlap the gates still hold: the characters only one
  alternative accepts stay its own, and the characters a set of alternatives share go to a minted production holding
  those alternatives in their order, called behind a gate on exactly them — the original's gates become disjoint, and
  the overlap waits in a helper whose alternatives all peek the same characters, ready for their common prefix to be
  factored. Two inlinings then put what a factoring must see into one list. `inline-under-gate` gives a call only the
  ways its caller's gate can reach: an alternative that peeks a character and then calls, taking nothing on the way,
  enters the callee where it peeked, so a way whose own peek admits no character the caller's does can never fire and a
  minted copy holding the rest is what the call means there — and where one way is left and it reads nothing, its
  actions splice into the caller's and the continuation becomes the call, which is how the block header's indentation
  side surfaces as the auto-detect it is rather than a call to a choice. `inline-single-way` carries the sweep's
  splicing of a do-nothing frame to a frame that does something: a call whose production has one ungated way decides
  nothing, so its actions join the caller's and its own calls become the caller's, each parameter bound to the argument
  the call passes. Both refuse where the frame is load-bearing — a gated way is a decision, the canonical form holds two
  calls and not three, a recovery rides the very frame a splice removes, and an action binding a parameter the call
  renames is a loud fault. `factor-prefixes` factors it: the longest identical run of zero-width actions and fixed-width
  consumes — a length-ambiguous run stops it, backtracking over it being an order a factoring must not reshuffle, as
  does a frame-scoped pair's half — moves into one alternative that calls a minted decision production holding what
  remains of each way, handed the code where a leftover closes a `(token)` the prefix opened; a second gate hoisting
  then gives each leftover the characters it can go on, one character deeper than the gate the alternatives shared. One
  round reaches the fixpoint: what stands after it differs in its emissions before the decision or is committed to by
  order — the deep end the certificates are still to reach. The provisional run is what reaches past a character: a run
  of held tokens, its `start` and a `MarkProvisional` cutting it into the region before the mark and the region from it
  on — re-taken within a run the mark moves, the last taken winning, what a line scan spends per fresh line — opened by
  `OpenProvisional` and resolved by `CommitProvisional`, with two actions that read it —
  `RetypeProvisional(rest, breaks, region)` rewriting the held tokens in a region by kind, a break-consumed token to one
  code, any other to the second, `None` keeping a kind its own; and `InjectBefore(codes, at)` inserting decided markers,
  in order, at the run's start or its mark. One obligation makes the mechanism sound: no held token is dropped or grown,
  so the readings a run decides between agree token for token and every difference between them is a zero-width marker.
  The five are one-for-one with the `ys_queue` runtime and zero-width to every analysis, held balanced by a net that
  walks the run's state — closed, open, marked — through the call graph, refusing a mark or an injection or a retype
  that names a mark none was taken, and undone in the interpreter through a trail its rewind pops — a retyped code, an
  injected marker — the run start and its mark values the checkpoint restores, so backtracking rewinds through any
  provisional action and a hybrid run rewinds through a commit. `speculate-folds` spends them first: the flow fold's
  break is emitted provisionally and the next line read through — an indent and whites whose codes no outcome changes —
  to the one character that decides it, a break committing the trimmed way, anything else, the stream's end included,
  retyping the held break to `line-fold` with the follower's prefix already consumed. The site is fused by name, the
  first step to name a production rather than a shape — fusion being forced, a content line's spaces being the
  follower's, read before the decision, by a runtime that never rewinds input — and a certificate lemma lets two
  alternatives share a peek where their guards are complementary, `Lt(x, y)` against `Le(y, x)`, the indentation loop's
  own case. The first and follow sets behind all of this are computed over the shaped grammar as codepoint intervals,
  every answer erring wide — a certificate stands on disjointness, so too wide refuses safely; the invalid-byte class
  rides an alternation peek as its own unit, which is what pins the unparsed recovery's any-byte loop. A gate holds a
  literal whole: `LiteralPeek(text, then, barrier)` enters where the input begins the text and the character after it,
  if any, matches `then` or avoids `barrier` — the end of the input passing either, the polarity each literal's own —
  and `ConsumePeeked` takes what the gate found without scanning the bytes twice. The CR LF break certifies so, in place
  and without a state per character, and the certificate reads a literal-gated alternative as backtracking's own: where
  the gate refuses, the next way is exactly where the failed literal lands, and where it holds, the literal commits
  whole, the way the grammar means one. The first sets reach through what used to fog them: a difference begins with
  what its base does, less every exclusion that is one character class — the interpreter probes exclusions before the
  base, which makes the subtraction exact — and a recovery widens nothing, riding the call's edge to resume at the
  frame's return, changing what may follow but never what an alternative begins with entered fresh. Those two facts
  emptied the unpinned-fallthrough category whole: every follow set the certificates consult is pinned, and the hoisting
  mints the gates it had been starved of — the quoted continuation lines gating on printable-non-white the way their
  seam grapheme always meant. A literal gate climbs to where the decision is made: an alternative whose whole way in is
  a call opening on a literal-gated production takes that gate as its own, the follow test declared rather than derived
  — a first set cannot see the line structure that decides a marker, and a derived class admitted `----` to the marker
  reading the spec gives a plain scalar, which the hybrid net and the marker fixtures caught on the spot — so the
  explicit document commits on `---`-then-boundary at the stream's own choice, `c-forbidden`'s class carried as the
  gate's `then`, and the certificate reads a literal spelled through single-way calls as one spelled in place. A way
  guarded by the end of the input begins with no character, so its begins pin empty — while staying nullable, the gate
  hoisting keeping an empty match enterable with nothing left to peek, and only the certificate knowing that nothing
  follows the end — which certifies the break-or-end tails whole, the comment ends and the chomped last breaks, the
  comment chain cascading behind them. The directive keywords carry their name barrier — `YAML` and `TAG` are the whole
  name only where no `ns-char` follows, the reserved directive's own greedy name claiming a longer one, and a seen
  keyword commits, its version's faults the grammar's own cut to judge — so `%YAMLX` is a reserved directive named
  `YAMLX` where `%YAML` alone is the committed keyword's error. The block header reads chomping first through
  `reorder-declared` — one generic swap of a two-way choice whose targets and reasons are data, `DECLARED_REORDERS`,
  never a name recognized in transformation code — and the reasons are per-copy arguments that hold only after
  monomorphize: the strip and keep copies gate their chomping way on a literal, no empty match left to enter through,
  where in the base grammar clip's empty chomping branch would let a chomp-first ordering swallow nothing on `|2-` and
  commit the valid trailing `-` into an error. Alternative order is semantics under backtracking-with-commits, and a
  reorder's soundness is position-dependent — the same swap is unsound in the base and proved where the step stands.
  `extend-returns` folds a declared site the same declared way: a call-then-continuation whose continuation is actions
  alone is absorbed into the call's own family — the actions appended to every return path through minted copies, a tail
  call retargeting to its target's copy, a recursion meeting its own copy and folding, every other caller untouched and
  the dead frames purged — a language identity, stream-faithful because the appended actions run exactly where the
  continuation ran, and refused where the continuation closes a scope it does not open. Its first declared targets fold
  the sequence loops' end-marker frames, so each loop's exit way carries its own `end-sequence` inline — the seam toward
  the parent's next scan absorbed frame by declared frame, each fold corpus-held, rather than by one atomic flip. The
  mapping loop's exit folds beside the sequence's and earns its declaration twice: absorbing the frame leaves the way
  that carried it a single call, and a way with a call *and* a continuation cannot hand its pop down — the continuation
  goes on the stack ahead of where the pop would land. So the seam absorbed is also what lets a pop reach the loop's own
  scan, which stops that scan reading an auto-detected indent a nested collection has since replaced. And what no
  disjointness can prove now stands declared: the assurance ledger commits a production on a written reason — the
  header's chomp-first subsumption and a digit at the indicator being the indicator — each entry held to backtracking by
  the hybrid corpus, refused when its name goes stale or the analysis catches up, and counted on its own gate line so
  the declared few never grow quietly. Every step's grammar is then swept of what the step leaves behind, the three
  passes running to a fixpoint since each feeds the others. A production whose whole body is one ungated, action-free
  call, with no continuation and no recovery of its own, is what it calls, so every reference to it becomes a reference
  to that callee — a frame that decides nothing and does nothing costing a push either way. Productions that behave
  alike are spelled once: same parameters, and the same body once every reference in it is read as the group of what it
  names rather than by the name itself, which is what tells two loops apart from one loop written twice — `b-l-spaced`'s
  empty-line scan, `b-l-trimmed`'s and `l-keep-empty`'s are one production, where comparing the bodies as written sees
  three. The groups are the coarsest partition that stays stable under that reading. And last, so it sees what the other
  two strand, every production no parse can enter is purged — each IR node spells the productions it references, and
  reachability closes over the ones a parse enters by name — so the fold's old family is gone the step its fused
  replacement lands, and the meter stands on what the machine holds. None of the three changes what the grammar matches
  or emits. Only a merge is a rename, and only a merge a point of interest follows: two productions that behave alike
  are one thing under two names, so what tracked either tracks the one kept, where a spliced frame is consumed rather
  than renamed and its callee holds none of the frame's role — following one slid a declaration meant for a line-prefix
  wrapper onto the indent scan underneath it and dissolved the very call the indent refinement exists to refine. That
  splicing is the declared line-prefix inlines done universally, so both of those entries retired when the universal
  rule caught up with them, leaving two. A fixture the sweep strands is not dropped: it pins to the last stage whose
  grammar can run it, guards that grammar token for token, and credits coverage from where it stands — 185 of the 694,
  the fold family's four, `c-reserved`'s three (a production the spec defines and nothing references, the base grammar's
  own), the frames the declared extensions absorbed, the nullable productions their consuming copies replaced, and the
  bare monomorphic copies only a fixture enters, the root reaching an escape char, a tag property or an alias
  context-pinned alone. A base that is total where the fixtures run it is excused the coverage gate's rejection: nothing
  can be seen to refuse what matches at every position, and a consuming copy that says no where the base matched empty
  would be asking the corpus for a refusal the untransformed grammar had nowhere to show. The corpus parses green in
  that hybrid the whole way, so the meter is honest at every step: 588 of 756 productions run committed, and the 168
  still backtracking are the determinize work itself, driven to none, at which point it becomes a gate. The first
  conflict the local moves determinized whole is the empty line's: `inline-singles` splices the declared prefix wrappers
  so `l-empty`'s two ways surface as the scans they are, the refinement and the factoring leave one shared scan with
  `==n` against `<n` residue, the factoring raises a leftover's leading assertions into its gate's guards, and a new
  certificate reads a choice of guard-led ways that are pairwise complementary — at most one enterable anywhere, so
  committing to the first that holds is the way backtracking finds — which is what took the meter below its 240 floor.
  Beside it stand two more local moves: the sure-way certificate — a peek-gated way with refusal-free actions and no
  calls cannot fail once entered, so against a fallthrough the gate's answer is backtracking's own, which proves the
  in-line separation and retires the indentation indicator's ledger entry, the staleness net demanding the removal — and
  `hoist-residue-guards`, raising a leading `Lt` or `Le` into the gate's guards, where it is judged at the very position
  the gate is, so a choice of guard-led ways is a shape the certificates can read. And the meter itself is corrected:
  the goal is the grammar deterministic as invoked from the root, not every production at every hypothetical entry, so
  the meter counts root-reachable decision points — each a production judged under one reachable context's follow, the
  contexts computed root-down as follow classes, a nullable way's begins widened by its context exactly as one level of
  inlining would place them. The certificates sharpened the same way — every ungated way judged by its effective begins,
  not the last alone — which alone proved nine more productions in isolation; the isolation count stays printed as a
  diagnostic beside the true meter's 422 points across 168 productions, among them the greedy optional — a way that
  reads against the zero-width residue behind it, the shape the elimination distributes and the one the certificates
  still owe. Every step must change the grammar, and one that does not is a fault named where it stands: a step goes
  idle when what it looks for has stopped reaching it — a shape an earlier step now spells differently, a declared site
  whose content moved — which is a regression in the step before it rather than a step to leave standing, and it is what
  caught the line-prefix declarations dissolving the indent scan. The check reads a transform's own output, before the
  sweep, so a step is judged on what it did rather than on what the sweep did after it; the way-subsumption step retired
  to it, its shape reaching it nowhere in the pipeline once the universal splicing had absorbed the spliced separations
  it was written for. `check_normalize` holds every step token-and-event identical over the whole corpus — 694
  conformance fixtures and 402 YAML Test Suite cases, seven of them pinning the document-marker boundary the spec's
  `c-forbidden` spells and the Clojure reference agrees on: `---foo`, `---#foo`, `----` and their `...` kin are content,
  `--- foo` a boundary, `... foo` malformed; four pinning the sequence dedent hand-off any committed block structure
  must reproduce — at a dedent each exiting level's end markers stand before the dedent line's indent token, the owner
  level continues past it, and a zero-column dedent carries no indent token at all; and two pinning the sequence entry's
  committed dash, the Clojure reference agreeing both are errors — `- @` is an entry whose body fails inside the entry's
  own committed region, where `-b` is refused at the gate, the sequence closing before a stream-level error —
  backtracking and hybrid alike — and ends on two own-gates over the result: every long text token, a scalar's text or a
  name's or the unparsed recovery's, is matched in bulk rather than one character per loop; and every run consumes a
  character set — a `ConsumeTrimmedSpan` both sets, a `ConsumeSpan` its set, a `Star` its element or, until determinize
  supplies the guard that lowers them, a nullable production. No action reads a value off a frame, and the one rule left
  over what the parse gives back is that pushes and pops balance — held by the run itself, where a `(token)` code popped
  with none pushed and a `(max)` window closed with none open are each refused where they happen rather than deduced.
  The apparatus that existed because a close read its own frame is gone with the reason for it: the `code` parameter
  `single-consumes`, `binarize`, `alternative-shape`, `factor-prefixes` and `extend-returns` each added, the refusal to
  move a run of actions closing a scope it did not open, and the fold's own refusal of the same.

- The indentation is on the same stack as the code. `push-indents` mints a `PushIndent` at each of the thirty-three
  calls measured against an `n` other than the one in force — the other seven hundred pass it through and push nothing —
  and the call carries on at a production that takes it back: the pop, and then wherever the alternative was carrying
  on. So the point an indentation stops applying is an action the grammar writes, in the one place nothing of the
  alternative's own runs, rather than a property of the edge that a shared continuation could not have said for itself.
  Whether a frame is what a pop wants is `sink-pops`', on a grammar where every one of them stands. A pop holder is an
  ungated way whose actions are the pop and only the pop; where every reference to what one calls is such a way, the pop
  leads that production's own ways instead and the holders stop doing it, a frame with nothing left going to the sweep.
  There is no other way in for it to be wrong for, however many holders say it. It runs to a fixpoint, because sinking
  makes holders — a way that did nothing of its own before its call becomes one once the pop above it has come down,
  which is the only way the block collections are reached, on a second round. A continuation whose arguments read the
  indentation keeps its holders, those being read before the pop rather than after, as does one a parse enters by name,
  and a recovery, which a cut reaches with the stack as it stood before the call ran. Twelve holders come to five. What
  it is for is `defer-pops`, which cannot see a pop behind a frame. One production is the pop and nothing else,
  `x-pop-indent`, shared by the nineteen pushes with nowhere else to go; the other fourteen hold a continuation behind
  it. Each declares no parameters and reads the ambient ones, so a continuation's arguments are evaluated after the pop,
  where the stack and the parameter agree. A continuation that changes the indentation has no return of its own to take
  it back, so its call goes into a production holding it alone. Every entry on the stack says what kind it is and every
  pop is held to it, so a pair that has been moved across one it must not cross is refused where it happens.

  `read-indents` then drops the parameter: every read of `n` becomes `Indent`, the indentation in force, and the
  declaration and the argument go with it, leaving `m` and `f` the only parameters the final grammar declares. The two
  mechanisms stood side by side for a whole gate first, every read of `n` held to the stack agreeing with it over 694
  fixtures and 402 suite cases — which is what proves the drop, and what found one fault besides: an in-grammar
  `(recover)` left standing the pushes of the parse it abandoned, where a cut reaching `_fail` had always cleared them.
  The recovery now takes back what the abandoned parse never got to. The comparison stays live at `push-indents`, the
  last grammar to carry both.

- `m` and `f` are globals, and with them the last parameter goes. `read-globals` takes the declaration off every
  production and the argument off every call, and every read becomes a `Global`. The final grammar declares no
  parameters and passes no arguments anywhere: `Indent` reads the stack, `Global` reads the one slot, and every value
  the parse carries is one of those two — a global singleton or an entry in the unified stack, with no third place left
  for anything to live. A global is carried out of every call where nothing declares it; until `read-globals` runs they
  are parameters, scoped like any other, which is what leaves the base grammar's own runs alone.

  What makes them globals is that they do not nest, and that had to be made true rather than assumed. While the block
  collections pushed their indentation per entry they re-read `m` on every turn to rebuild what they had just popped, so
  a nested collection writing the one slot in between took the enclosing loop's value away. A first attempt at this
  failed on exactly that, named by a sequence inside a sequence. Reading a global nothing holds a value for is a fault,
  and the clears say where each region ends.

  The block collections push their indentation once before their loop and pop it once after, where they pushed and
  popped it per entry. `hoist-pushes` and `cancel-indent-pairs` are what take the pair out: a production every way of
  which begins with the same push makes it however it is entered, so the callers make it instead, last among their own
  actions — a gate consumes nothing and is refused where it reads the indentation, and a gate that then fails leaves a
  push the failing path gives back. The push comes off a copy rather than the production itself, what a caller wants
  being a production that no longer makes it and what a parse entering by name wants being one that still does; the
  original is left whole and swept where nothing enters it, which is what pins a fixture naming one to the stage before.
  Then a pop and a push of one level standing next to each other are nothing, and both go.

  So `m` is read at the top of the construct that measures it and the loop runs with `n+m` in force throughout, where it
  used to be re-read on every entry to rebuild what had just been popped. Ways reading it outside a pop level fall from
  31 to 18, which is the whole point of the pop carrying a level and of everything that moved a pop to reach this.

  A pop says which indentation it takes off. `PushIndent` and `PopIndent` are minted together and the level is written
  on both, carried with the pop wherever it moves and re-scoped where it crosses a call, so a step moving the pop — or
  moving something past it — can name what the actions around it measure against, with no table pairing the two ends. It
  is not what the pop restores from, a pop taking off whatever is on top; it is read once the pop has happened, that
  being the scope it was written in, and checked there, so the pairing is refused rather than claimed. A tag naming the
  pair would have done as much and cost the sweep's merge, two otherwise-equal productions with different tags never
  collapsing; a level is a value, so two pops of the same level are the same action still. `strip-pop-levels` takes it
  off once `defer-pops`, the last step to read it, has run — left standing it is a read of the auto-detected indent at
  every pop, keeping a value live where the parse has no use for it.

  `defer-pops` moves a `PopIndent` that leads every way of a production to the end of that way's actions, rewriting each
  expression among them equal to the level it restores from into `Indent`. The stack holds that level until the pop
  runs, so the measurement is the same read one action later — held to it by the level the pop itself carries, and by
  the residue with the level masked out holding no `Indent` the rewrite did not account for. It crosses actions and
  nothing else: an alternative's calls run after all of them, so nothing a callee is measured against changes, and an
  action that is itself a call or a scope around one stops the move. The block sequence's next-entry scan compares
  against `Indent` directly now, and the pop stands against the push that follows it with no scan between.

  A global is run as a stack beside itself, so that what is asked of it can be counted rather than gambled on. A `(set)`
  puts its value on a stack of that global's own and a `(clear)` takes it off, and a read takes the top — right however
  the writes nest, so the parse stands whatever the number says. Beside it stands the one slot a global would be, and
  one counter says how far apart they are: the reads where the top and the slot differ, which one value for the parse
  could not have answered. It reads zero, which is what licenses `m` and `f` as globals — and at zero the slot is the
  stack.

  What that buys is a transformation judged by a number instead of by whether the corpus survives it: narrowing
  `sink-pops` to refuse a holder carrying its own continuation — what the unified stack wants once calls are written
  out, and what costs the mapping loop its hoist — becomes a distance rather than a wall.

  A clear is idempotent and the stack does not refuse an unbalanced one. There are 48, all the same thing: a clear
  stands on every way that reads a parameter, and a value routinely has more than one reader. "From here nothing holds a
  value" is as true said twice as once, so they are not an imbalance to repair — and a later step may merge two
  consecutive clears, or drop one behind another, so that a clear never stands in the way of a factoring that would
  otherwise see a common prefix. What that trades away is a structural refusal, and what stands in its place is the
  corpus: a clear misplaced far enough to matter takes a value from a read that wanted it, which the fixtures and the
  fold do catch.

  `clear-params` says where a parameter stops applying: the way that **reads** it clears it where it returns, behind its
  calls, there being nothing of its own after them. A value ends where the last thing wanting it is done, and that is a
  point the parse passes through rather than a set of productions — the way holding the read takes the value, uses it,
  and by the time it comes back nothing else wants it.

  The reader and not the writer, which is the whole of it. A block scalar's indentation is written deep inside the
  header and handed up to the scalar that asked for it, so clearing where it was written takes it from the one thing
  that wanted it. And not the frame above a region of the call graph either: a production reached between two reads by
  an enclosing loop is outside such a region and inside the parse's, so a clear left there takes the value away between
  two of a loop's own reads. Both were tried and both failed loudly, which is how the reader came to be the answer.

  Reading a parameter nothing holds a value for is a fault, so the placement is the corpus's to refuse rather than an
  argument's to make. No read crosses a clear over 694 fixtures and 402 suite cases; withholding the clears changes
  nothing, which is what correct placement looks like, and misplacing them at the writer instead fires at once, which is
  what says the refusal is live rather than decorative.

  `prune-params` drops a parameter no production needs, with the argument every call passed it: what a production needs
  is what reaches a read — its own gate and actions, and whatever it hands to a production that needs one — and a write
  counts, since a binding a frame does not declare is dropped on return. A least fixpoint, so a parameter a chain of
  frames only relayed dies through the chain at once: `m` goes from 91 declarations to 76 and `f` from 15 to 9. It found
  `_is_using` answering `False` for a `Param` a field holds directly, the generic walker never visiting one — latent,
  its two callers asking only about the parameters where the two agree, and corrected here.

  Neither step decides anything, and the meter's fall is not theirs. Each leaves it exactly where it found it on its own
  output — 473 points through `read-indents`, 426 through `prune-params` — and every point that goes, goes to the sweep:
  sixteen productions merging once the indentation argument stopped telling them apart, nine of them carrying conflicts,
  and twelve more once the dead arguments did. Simplification, not determinization, and the two are worth keeping apart.

  `<auto-detect-indent>` had to be told: it read `n` out of the environment rather than through a `Param`, where no
  rewrite of the grammar could reach it, and it took the block scalars and the dedents down with it until it was. The
  indentation in force is one accessor now — the stack's where the grammar pushes, the parameter's where it does not.

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
  what they were, and the stack's frames carry the grammar's one runtime parameter, `n`. The automaton that drives them
  is not generated yet, so `ys_read_token` still returns a "not implemented" error.

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
