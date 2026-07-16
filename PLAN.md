# A grammar-derived C YAML parser generator — implementation plan

Emit a fast, single-pass, pull-driven YAML 1.2 parser in C **from the formal productions** — so that correctness is a
property of the generator, not of hand-testing. The output is the machine you'd hand-write anyway; the value is that a
proof, not luck, says it's the right language.

- Target: **libyamlstar** ABI-compatible `.so`
- Complexity: **O(n)**, libyaml-class
- API: **pull** · `next_token()`
- Stream: **yeast** · reference-identical codes

Throughout, the grammar's parameters split by two fates and it matters everywhere: **`c`** (context) and **`t`**
(chomping) are finite, so they are resolved at generation time (static); **`n`** (indentation) and **`m`** (the
auto-detected indent) are unbounded, so they are threaded into the runtime automaton.

## §0 — Why this, and why it's hard

Every YAML parser to date sits on one horn of a dilemma.

The **reference parser** (pure Clojure, the basis of YAMLStar) is mechanically faithful to the ~211 productions but slow
— naive backtracking over the grammar can go superlinear, and it ships a heavy runtime (GraalVM today, Go tomorrow). The
**libyaml-class** hand-written state machine is fast and O(n) but its conformance is established test-by-test; it has
known deviations from the 1.2 grammar and is "faithful by luck."

The reconciliation is a machine that is *both*: a deterministic, committed, character-at-a-time automaton with an
indentation stack and bounded deferred-token tracking — **generated from the parameterized productions** so that its
speed comes from the state machine and its fidelity comes from the derivation. That generator is the whole prize, and it
is a small compiler, not a weekend parser.

**The one load-bearing fact:** YAML restricts implicit ("simple") keys to a **single line**. That restriction is what
makes determinization finite, makes the deferred-token set bounded, and makes a pull `next_token()` able to return
without draining the whole document. It is doing triple duty and the entire architecture rests on it.

It bounds the *key* deferral, and nothing else. There is a second, and the block scalar is where it lives.

**Indentation detection is not the problem.** A block collection's indentation, and an inline one's, are read straight
off the current column — the reference peeks past comment lines first, but `s-l-comments` has already eaten them, so
there is nothing to peek past. Those two cost no lookahead at all.

**The empty lines that open a block scalar are the problem, and the chomping is why.** An empty line there is content if
a content line follows it — `l-empty`, a `line-feed` — and is chomped away if none does — `b-non-content`, a `break`.
The same line, told apart by something that has not happened yet. So none of those tokens can be handed back until the
parser reaches a content line or the end of the scalar, and the run of them has no bound: YAML bounds lookahead only for
implicit keys, at 1024 characters, and says nothing at all here.

Nor is this an artefact of yeast. The *value* depends on it too — `|-` with two blank lines and nothing after is `""`,
and with `text` after is `"\n\ntext"` — so any parser that produces a value looks exactly as far.

So libyeast queues them. The tokens of the run are built and held, none handed back; when the run resolves, either they
become content and the scalar's end arrives later, or `end-scalar` is **injected ahead of them** and they become the
breaks that were chomped away — the marker's position is what says they were never content. `end-block-scalar` exists to
emit that marker, and without it an empty stripped block scalar opens a scalar it never closes.

`max_token_bytes` bounds it, being the same guard a single enormous token needs: the bytes the parser may buffer before
it can hand a token back. Past the cap it is an error, not an out-of-memory.

## §1 — The central principle: two parameters, two fates

The productions are indexed by two parameters. The generator's core intellectual move — a binding-time analysis — is to
treat them completely differently.

- **`c` — context · STATIC.** `c` ranges over a **finite** set (block-in, block-out, flow-in, flow-out, block-key,
  flow-key). Specialize it away at generation time: each `c`-parameterized production monomorphizes into ≤6 concrete
  ones. Compile-time. Gone from the runtime.
- **`n` — indentation · RUNTIME.** `n` is an **unbounded** integer threaded as `s-indent(n)`, `s-indent(<n)`,
  `s-indent(≤n)`. It cannot be specialized away; it must survive into the emitted automaton, carried on the indentation
  stack.

The other two follow the same two fates: **`t`** (chomping — strip, clip, keep) is finite, so it is specialized away
like `c`; **`m`** (the auto-detected indent) is an unbounded integer, so it is threaded like `n`. So the runtime carries
`n` and `m`, and never sees `c` or `t`.

Getting this split right is the crux: partial-evaluate over `c` while *preserving* `n`.

```
# a production, before and after c-specialization
ns-plain(n, c)          ::= parameterized on both

  # becomes, at generation time:
ns-plain-blockKey(n)    # c pinned → concrete automaton fragment
ns-plain-flowIn(n)      # n still threaded → indentation stack
```

## §2 — Target architecture: what the generated parser looks like

The pull-API requirement (caller invokes `next_token()`; the parser does *not* call the caller back) forces the lowering
target and, helpfully, agrees with the speed and streaming requirements. All three want the same non-recursive machine.

**One automaton, not two layers.** A scanner emitting tokens for a parser to consume would need a vocabulary between
them, and yeast has none: the automaton's output already *is* the token stream — the `Begin`/`End` markers and the
classified spans of input. A hand-written scanner would also be the one thing on the hot path the grammar did not
derive. So there is a single character-driven automaton, `c`-specialized and `n`-threaded, emitting yeast tokens into an
output queue; `next_token()` hands back the queue's first token once it is decided.

**An explicit pushdown automaton, not a call stack.** A recursive-descent shape keeps "where am I" in the C
return-address chain, which cannot suspend to return a token. So the generator emits a **state enum + explicit heap
stack + single dispatch loop**. Suspension is then free: run the loop until a token is produced, save the state struct,
return. This is exactly libyaml's shape — and libyaml's API is already pull for the same reason.

**The deferred set is a token queue with a resolution tag.** The "possibly-key, possibly-scalar" hypothesis becomes a
literal queue entry marked *provisional*. `next_token()` returns the frontmost *resolved* token, advancing input only
when the head is still ambiguous or the queue is empty. Because the ambiguity is line-bounded, the eager buffering
before an honest return is bounded too.

**The canonical stream is yeast.** The event/node stream the parser emits is **yeast** — the Haskell reference parser's
own token model (from `yamlreference`): zero-width `Begin`/`End` markers wrapping the interesting productions, and every
consumed character classified as a leaf token (`Indicator`, `White`, `Indent`, `Break`, `Text`, `Meta`, …). The codes
are kept **byte-identical to the Haskell reference's `Code` constructors** (already declared as `ys_code` in the public
header), and that one decision makes the single stream do three jobs at once:

- **The load API** — `compose → resolve → serialize` is a downstream fold over yeast. YAMLStar already owns that back
  half and already eats these tokens, so the JSON path is a front-end swap, not new code (phase 07).
- **A debug view** — folding the balanced `Begin`/`End` markers rebuilds the nested productions tree, rendered by the
  package's own `yaml2html` (migrated from the Haskell reference; phase 06). Identical codes make the port a faithful
  copy, validated against the Haskell reference's own rendering.
- **The differential oracles** — identical codes make the yeast comparison against the Haskell reference
  token-for-token; the folded load output is checked value-for-value against the Clojure reference (§3).

**Where the rewind problem went:** a backtracking parser would have to discard emitted events on every failed
alternative. The determinized automaton (phase 03) doesn't backtrack — committed transitions emit on commit, so there is
nothing to rewind. The only provisional events are those inside the line-bounded simple-key lookahead; they live in the
deferred queue above and are discarded there if the key hypothesis fails. The single-line rule that bounds that deferral
bounds its event retention too. Indentation detection, the other deferral, retains nothing at all: it consumes and emits
as it goes (§0).

**The grammar is libyeast's own.** `grammar/yeast-spec-1.2.yaml` holds the productions *and* the yeast codes they emit;
the vendored `yaml-spec-1.2.yaml` holds neither the token layer nor the structure it needs, having inlined the indicator
characters. A gate erases libyeast's additions and recovers the official grammar exactly, so the generator's input is
hand-authored where it must be and machine-proved where it can be.

```c
/* the emitted C surface — pull, suspendable, arena-backed (see include/yeast.h for the full declared API) */
ys_parser *ys_new_string_parser(const char *input, size_t length, const ys_options *options);
ys_token ys_next_token(ys_parser *parser); /* one token, or ERROR, then halts */
void ys_free_parser(ys_parser *parser);
```

## §3 — Validation strategy: how fidelity is actually earned

Passing tests is a floor, not a proof — every place backtracking is replaced by a committed decision is a place the
automaton can diverge from the productions' meaning. So validation runs on three fidelity legs, strongest first — plus a
fourth, orthogonal leg for safety:

- **Refinement obligation (where feasible).** The determinization transform must be shown to preserve the language of
  the productions — commit-here *and here's why it's still the same language*. This is the part that touches
  formal-methods territory and is what makes the result worth more than libyaml.
- **Differential oracles — both reference implementations.** The parser is pinned against *both* references, each
  authoritative for a different half of the pipeline. Against the **Haskell `yamlreference`** the comparison is
  *token-for-token* on yeast — codes are identical, so it validates the syntactic layer (production structure, character
  classes) at the finest possible grain. Against the **Clojure reference** (YAMLStar's basis) the comparison is
  *value-for-value* on the folded load output, validating composition and schema resolution — the semantic layer.
  Agreement with both spans the whole pipeline; a mismatch with exactly one half localizes the bug, and the yeast→HTML
  debug view renders the divergence.
- **YAML Test Suite + fuzzing.** The ~350+ case suite as the empirical floor; structure-aware fuzzing over the grammar
  to hunt the long tail, especially in the semantic rules the BNF doesn't capture.
- **Memory-safety & adversarial auditing.** This library is a building block for sensitive software parsing untrusted,
  possibly hostile input, so safety is a first-class validation axis — not an afterthought. ASan/UBSan run on every
  oracle pass; structure-aware fuzzing runs continuously from day one. A written **threat model** covers the
  YAML-specific denial-of-service classes: billion-laughs / recursive alias expansion, pathological nesting depth,
  unbounded allocation, quadratic blow-up. The pushdown design already removes C-stack overflow; the explicit heap stack
  carries a *configurable depth cap* and every allocation is bounded. Security is audited across the whole build,
  continuously.

## §4 — Implementation phases

Phases are ordered by dependency. The first real leap is **phase 01** — the backtracking interpreter, which proves the
grammar and becomes the oracle — and it is the biggest single jump before the pipeline. The deepest research risk lives
in **phase 03** (the normalization pipeline, where determinization happens), with **phase 04** (semantics) close behind.
The rest — grammar IR, codegen, and the ABI layer — is well-trodden compiler work.

### Phase 00 — Grammar IR · Ingest the productions into a typed IR

**Done.** `grammar/yeast-spec-1.2.yaml` is the generator's source and `generator/ir.py` the typed IR, and five gates
hold them: the grammar round-trips through the IR losslessly; every reference resolves with a matching arity; every
character the parser consumes lies within a token action; every rule that emits tokens says which, in the order it emits
them; and erasing libyeast's additions recovers the vendored official grammar, rule for rule. The decoder is generated
from it. `DESIGN.md` says where each piece lives; `CHANGELOG.md` records what it is.

What that phase taught, which the rest of this plan is written against: **the vendored grammar cannot be the source of
truth.** It inlines the indicator characters, so it cannot say that a quotation mark opens a scalar as an indicator but
is meta inside an escape, and it names no token at all. libyeast's grammar restores that structure and annotates it, and
the erasure gate is what keeps the addition from becoming a change.

Still to be scoped, when the parser needs them: an independent correctness leg running the yaml-test-suite through the
yaml-grammar harness against our IR, and the hand-off into `grammar2parser.py`. (The grammar is version-stable — 1.2 and
1.2.2 share productions — so it also matches the `yamlreference` token oracle of phase 02.)

### Phase 01 — The reference interpreter · the grammar's executor, and the project's oracle

*Risk: High — the first real leap.* Before a single transformation, build a **backtracking interpreter of the grammar as
it is** — a slow, obviously-correct Python executor that runs a production on an input and emits the yeast token stream.
Two things fall out of it at once: it proves libyeast's own grammar (`grammar/yeast-spec-1.2.yaml`) produces the
reference's tokens, before any C exists to be wrong; and it becomes the verification net for the whole normalization
pipeline (Phase 03) — the backtracking mode built here, the committed mode added there.

**The oracle is static, and libyeast's own.** `tests/spec/` holds 637 input/output pairs, each named
`production[.n=N][.c=C][.t=T].case`, the outputs in the yeast format `ys_read_token` already parses. It was built once
from the reference parser's per-production fixtures — those that align with libyeast's grammar, each output rewritten
into what libyeast emits, not what the reference does (see the differences in `DESIGN.md`); the reference's internal
helpers, `m`-based parameterization, non-UTF-8 inputs, and isolated-run commit artifacts are left out, and the one-time
build is not kept. `check_spec_tests.py` keeps the suite intact. The interpreter is thus checked by *reading* fixtures
and diffing — never by compiling or running Haskell. And because the fixtures are per-production, they hand us a
bottom-up build order for free: char-class leaves before token leaves before composites, and `l-yaml-stream` only once
everything under it is green.

Built piece by piece, each a commit gated by the productions it newly covers. Every node family is done — the
character-level nodes (`Char`/`Range`/`Diff`/`Empty`/`Seq`/`Alt`/`Ref`), the annotation nodes (`Token`/`Wrap`/`Emit`),
the repetitions (`Star`/`Plus`/`Opt`/`Rep`), the parameters (`Case`/`Flip`/`Bind`/`SetVar`, the arithmetic, and the
`Lt`/`Le`/`Max`/`Bound` predicates threading `n`/`m`/`c`/`t`), the assertions and lookahead
(`StartOfLine`/`EndOfStream`/`Look`/`NegLook`/`LookBehind`/`ExcludeAt`), and the two auto-detect indentation rules. The
matcher backtracks in the continuation-passing style, so an alternation is re-entered when a later element fails, as the
reference does. It reproduces every fixture that rests on a clean match — `l-yaml-stream` included — token for token.

What is left is error handling. 177 fixtures await it: those whose output carries an error token, and the reference's
`recovery`/`unparsed` productions (rules 185, 194, 208, 210) that emit a parsed prefix and mop the rest up as unparsed.
The interpreter emits neither yet, so `check_interpreter.py` counts these as pending rather than running them.

**Exit** — the interpreter matches every vendored fixture it covers, `l-yaml-stream` and the error cases included;
libyeast's grammar is proven against the reference token-for-token, with a slow executor ready to judge every pipeline
step.

### Phase 02 — Differential oracles · The broader nets around the token fixtures

*Risk: Low · ~2–4 wks.* Phase 01's per-production fixtures pin the *token* layer against the Haskell reference. This
stands up the rest: the *value* layer, the debug view, and the CI that runs them on every commit.

1. Vendor the YAML Test Suite; build a runner that consumes its event-stream expectations — the value layer the token
   fixtures do not cover.
1. Wire the Clojure reference as the value-level (load/event) differential oracle, behind a stable diff interface.
1. Bootstrap the yeast→HTML debug view on the Haskell reference's `yaml2html` as the divergence microscope; it later
   serves as the reference oracle for the package's own port (phase 06).
1. Set up CI: every commit runs the fixtures, the suite, and a differential fuzz corpus, reporting the first divergence.

**Exit** — a red/green harness scores the parser against both oracles on every commit.

### Phase 03 — Normalize · the grammar-to-canonical pipeline

*Risk: High — the prize · ~3–6 mo.* This is where the backtracking grammar becomes a committed one, and it is done not
as one leap but as a **series of small, individually-provable, semantics-preserving transformations** that carry the raw
IR to a canonical form a state machine falls out of. Binding-time, `c`/`t` specialization, determinization, and the
lowering to a pushdown shape — the old separate mechanical phases — are all steps of this one pipeline. Performance of
the generator is a non-issue; every step is written to be simple enough to prove by eye, and checked two ways after it
runs.

**The canonical form.** A **terminal production** is a set of characters, nothing more. A **nonterminal production** is
an ordered list of alternatives. An alternative is `gate  actions…  [P1  actions…]  [P2]`:

- The **gate** is a conjunction, tested without consuming: an optional character-set peek and zero or more zero-width
  guards. The alternative fires only if every part holds. An **empty** gate is the unconditional fallthrough, allowed
  only as the last alternative.
- **actions** operate on the parser's own state (below). Consuming the peeked character is itself an action, not part of
  the gate.
- **P1, P2** are zero, one, or two production invocations. Two means *run P1 then P2*: push a frame whose return is P2,
  go to P1, and when P1 returns resume at P2 — so P1 is the call, P2 the continuation, and at most one push per edge.
  One is a tail-goto; none returns. There are no actions after P2: a production returns exactly when its P2 does. A
  sequence of three splits through a helper, `A → B A₁`, `A₁ → C D`, and the `_<N>` suffix names where it came from.

Alternatives are tested in order and the first whose gate fires is **committed** — no backtracking. Two alternatives may
share a gate; order resolves the overlap, and proving that the earlier one is safe to commit to is the whole of
determinization.

**The action vocabulary**, four families, each derived from an IR node or the runtime already built in `src/parser.h`:

- **Token run** — `Consume` (push the peeked character into the current run), `SetCode(code)`, `CompleteToken` (cut the
  run into a token), `Emit(code)` (a zero-width marker, which also cuts). These are what `Token`/`Wrap`/`Emit` lower to.
- **Provisional run** — `OpenProvisional`, `RetypeProvisional(payload, break)` (the block scalar's held run carries both
  content and the breaks between it, so retyping names two codes), `InjectBefore(code)`, `CommitProvisional`.
  One-for-one with the `ys_queue` run. There is no discard: a failed hypothesis retypes, it never drops tokens.
- **Parameters** — `SetIndentToColumn`, `AdjustIndent(expr)`, `SetIndentFromDigit`. Only `n` and `m` are runtime; `c`
  and `t` are specialized away and never appear. Counting indentation is a loop of `[space]` gates with an accumulator
  action, not a special multi-character gate.
- **Guards** — `AtStartOfLine`, `AtEndOfStream`, `IndentLt/Le/Eq(n)`, `WithinKeyLimit`. A guard is a cheap zero-width
  condition on an alternative; the sharp case that forces the gate to be a conjunction is indentation matching, where
  both the eat-another-space and the stop alternatives gate on `[space]` and are told apart only by `column < n`.

The load-bearing rule: **no lookahead survives.** `Look`/`NegLook`/`LookBehind`/`ExcludeAt` over more than one character
are transformed away — into a char-set gate, a cheap guard, or provisional speculation — so a canonical grammar holds
none of them, and the validator rejects any that remain.

**The pipeline**, one linear series, grouped only for reading:

1. *Parameters.* Specialize `c` away (monomorphize, drop `Case`/`Flip` on `c`, prune unreachable branches); specialize
   `t` (chomping) the same way. Confirm only `n`/`m` remain, and only in indentation predicates and parameter actions.
1. *Structural.* Flatten `Seq`/`Alt`, drop `Empty`, collapse singletons. Lower `Token`/`Wrap` to token actions. Evaluate
   `Diff` and single-character `Look`/`NegLook` into plain char-sets. Lower `Star`/`Plus`/`Opt`/`Rep` into recursive
   `_<N>` productions. Lower `SetVar`/`Bind`/`Lt`/`Le`/`Bound`/`Max`/`StartOfLine`/`EndOfStream` into parameter actions
   and guards.
1. *Alternative shape.* Split each `Seq` into head-matcher + tail until every alternative is `matcher; actions; tail`.
   Binarize to ≤2 calls. Hoist FIRST-sets into gates, handling a nullable call as FIRST∪FOLLOW. Turn a leading
   unconditional action into an empty-gate alternative.
1. *Determinize* — the hard tier, still small steps. Fold residual one-token lookahead into gates. Left-factor shared
   prefixes into a common gate and a branch, repeated until each decision is one-character-decidable. Insert provisional
   speculation for the two unbounded cases — the simple-key line and the block-scalar opening empty lines. Handle the
   two indentation gotchas the mechanical steps miss — a zero-indent block sequence nested directly in a mapping, and
   flow context, which suspends indentation entirely. Discharge commit-safety per decision point, logging any residual
   as an assurance gap.
1. *Finish.* Dead-production elimination; assert every terminal is a pure char-set; run the validator.

**The validator** is the target invariant and equals "done": every production is a terminal char-set or an ordered list
of canonical alternatives; no `Star`/`Plus`/`Opt`/`Rep`/`Look`/`NegLook`/`Diff`/`Token`/`Wrap`/`Case` survives; every
alternative is gate-led with ≤2 calls and no post-P2 action; and every decision point is commit-safe.

**The verification net** is the **reference interpreter from Phase 01**, now taught the canonical/action nodes this
pipeline introduces and given a second mode. It is a slow, backtracking executor with undo — performance being a
non-issue — and it runs in two modes off one flag:

- **Backtracking mode** is the reference. Diff its token stream before and after *every* step against the yaml-test
  suite; a step that changes any output is rejected. This is the net for all sixteen steps, and the interpreter doubles
  as an early differential oracle against the Haskell reference.
- **Committed mode** respects the gates and never backtracks. After the determinize steps, both modes must agree on the
  corpus; a divergence means a gate is not commit-safe — the one thing the structural invariants and the backtracking
  interpreter cannot catch on their own.

**Exit** — a canonical grammar the validator passes, on which the interpreter agrees in both modes across the corpus, so
that emitting the C state machine (Phase 06) is mechanical rather than clever.

### Phase 04 — Semantics · Specify what the BNF doesn't say

*Risk: High · ~4–8 wks.* The productions alone don't fully specify a parser. The spec leans on prose for rules that must
become an explicit, auditable input to the generator — the decisions the Phase 03 lowering steps consume, so this runs
alongside the pipeline rather than strictly after it. This is where bugs get smuggled in undetected.

1. Encode the single-line simple-key restriction and its hard length bound as a first-class constraint.
1. Specify tab handling (forbidden as indentation), and the comment rule (`#` starts a comment only after whitespace:
   `foo#bar` is a scalar).
1. Decide and document duplicate-mapping-key policy (error / last-wins / first-wins) — the spec underspecifies it.
1. Specify line-break normalization and error-recovery states.
1. **Encoding — a conformance gap, open today.** libyeast reads UTF-8 and only UTF-8. YAML 1.2 asks a conformant parser
   to read UTF-16 as well, and UTF-32 where it accepts JSON. Nothing detects an encoding, and `c-byte-order-mark` emits
   the mark it matched and no more, where the reference gives that token the *name* of the encoding it detected — a
   divergence the differential oracle will find. Deciding this decides three things at once: whether the decoder grows a
   transcoding front end or stays UTF-8 and rejects the rest, what a `bom` token's text is, and whether libyeast may
   call itself conformant.
1. **Indentation detection — defined; implement it.** The grammar now says what the official grammar would not:
   `<auto-detect-indent>` is the indentation of the next line holding a character other than a space, less `n`, the
   current line counting only if the parse is at its start; `<auto-detect-in-line-indent>` is the spaces that follow on
   this line, which is what a compact collection is indented by. `m` is an indentation now and never the string
   `"auto-detect"`, and the two departures that took are declared in `check_vendor_spec.py`, with their reasons. What is
   left is for the generator to implement the two markers — and to do so **consuming**, not peeking: the grammar says
   what is measured, not that the parser must read the input twice to measure it.
1. **Settle a real divergence first.** The reference treats a leading all-space line longer than the detected indent as
   *content*; the spec calls a line with no non-space character *empty*, and an over-indented empty line an *error*.
   They disagree, the differential oracle will trip on it, and the answer must be decided before the generator
   implements the markers rather than after.
1. Mark each rule as "grammar" vs "asserted semantic action" so the fidelity claim is honest about its boundary.

**Exit** — a written semantic spec, versioned alongside the IR, covering every rule beyond the BNF.

### Phase 05 — Deferral · The two provisional cases, worked out concretely

*Risk: Medium · ~2–3 mo.* Phase 03's determinize step says "insert provisional speculation for the two unbounded cases"
in one line; this is that line worked out. The queue and its undecided run are already built (`src/parser.h`), so what
is left is the exact sequence of `OpenProvisional`/`RetypeProvisional`/`InjectBefore`/`CommitProvisional` actions for
each case, and the proof — via the interpreter's committed mode — that each resolves the run correctly.

1. Implement bounded eager buffering for the simple-key line; resolve key-vs-scalar on line completion or `:`.
1. Implement the block scalar's opening empty lines: hold the run, and on resolution either make it content or inject
   `end-scalar` ahead of it and make it breaks.
1. Verify end-to-end token output against the oracle, with the deferral exercised deliberately.

**Exit** — the two deferrals resolve correctly, pull-driven, oracle-clean.

### Phase 06 — C codegen · Emit the C library

*Risk: Low · ~1–2 mo.* The easy end of every compiler. Turn the lowered IR into a switch-on-state character loop with
arena allocation.

1. Emit the state dispatcher and the transition tables into `src/parser_tables.h`, as portable C99 with no external
   deps, over the runtime `src/parser.c` already provides.
1. Emit, per state, the production it belongs to and what its outgoing edges expect — a table of static strings shaped
   like `src/messages.c`'s, and the text of every format error. What the parser found is not in them, and need not be:
   the first `unparsed` token behind an error begins at exactly the byte that failed.
1. Arena-allocate everything; lifetimes are input-bounded, so free the arena on parser teardown — no GC.
1. Handle backtracking-region scratch within the arena; ensure discarded provisional state is reclaimed cleanly.
1. Emit the pull surface: `new` / `next_token` / `free`, plus structured error extraction.
1. Emit the `Code` enum and the compose fold (yeast → node graph); event retention is trace-mode only — the committed
   hot path emits and consumes without buffering.
1. Migrate `yaml2html` into the package: a small C companion that folds the yeast stream to colorized nested HTML,
   sharing the emitted `Code` enum — so the debug view ships *with* the library and carries no Haskell/reference
   dependency. Validate byte-for-byte against the Haskell reference renderer.
1. Build as `cdylib`-style `.so` across Linux/macOS (and Windows once the toolchain is clean).

**Exit** — a self-contained C `.so`, plus the bundled `yaml2html` tool, passing suite + differential + fuzz.

### Phase 07 — ABI layer · Drop-in for libyamlstar

*Risk: Low · ~3–5 wks.* The existing YAMLStar ABI was designed as a swappable seam — thin, JSON-string in/out, no
exposed structs — so this is nearly free. Every existing binding works unchanged.

1. Reimplement the create/destroy/`load`/`load_all`/`version` entry points over the new core.
1. Route `load` through the yeast fold — `compose → resolve → serialize` — reusing YAMLStar's existing resolver and
   dumper rather than reimplementing them; ship the two other consumers of the same stream — the now-bundled `yaml2html`
   debug view and the differential harness.
1. Keep GraalVM-era lifecycle calls as cheap no-ops or lightweight context handles (vestigial, harmless).
1. Serialize errors into the exact type/cause/message shape the bindings parse back out.
1. Reproduce the JSON-interchange contract faithfully (including its documented `.inf`/`.nan` limitation) for true
   drop-in behaviour.
1. Run the existing binding test suites (Python, Go, Rust, C#, …) unmodified against the new `.so`.

**Exit** — the new `.so` slots in where the GraalVM blob sat; all bindings green.

### Phase 08 — Harden · Fuzz, tune, and reach libyaml-class speed

*Risk: Medium · ~2–4 mo.* Correct-but-slow is not the goal. Close the algorithmic gaps naive codegen leaves and prove
robustness under hostile input.

1. Continuous structure-aware + byte-level fuzzing (ASan/UBSan) targeting the semantic long tail.
1. Profile; eliminate any residual super-linear behaviour from over-broad lookahead.
1. Benchmark against libyaml on representative corpora; tune hot states and buffering.
1. Optionally: extend to an emitter (dump), or keep libyaml's emitter alongside for a complete round-trip library.
1. Cut prebuilt binaries per platform so adoption isn't gated on a local native build.

**Exit** — O(n) confirmed, libyaml-competitive, fuzz-clean, packaged.

## §5 — Risk register

| Risk                                                                 | Phase   | Severity | Mitigation                                                                                                                                                                                                                                         |
| -------------------------------------------------------------------- | ------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Malicious input triggers memory-unsafety or resource-exhaustion DoS  | all     | ▪▪▪▪     | Hardening flags on the release build; ASan/UBSan on every run; structure-aware fuzzing from day one; bounded allocation plus a configurable parse-depth cap; billion-laughs / recursive-alias guards; continuous security audit, not a final pass. |
| A step in the normalization pipeline silently changes the language   | 03      | ▪▪▪▪     | Reference IR interpreter diffs the token stream before and after every step; the committed mode catches an unsafe gate the backtracking mode cannot; dual differential oracles against the Haskell and Clojure references; log assurance gaps.     |
| Semantic rules beyond the BNF encoded wrongly / incompletely         | 04      | ▪▪▪▪     | Written, versioned semantic spec; each rule tagged grammar vs asserted; fuzz the corners the suite misses.                                                                                                                                         |
| The first working slice is a big leap from IR to emitting tokens     | 01      | ▪▪▪      | Decompose into many small, individually-verified sub-steps; grow the production subset one at a time, staying green; hand-checked expected outputs before the reference oracles exist.                                                             |
| Naive codegen is correct but super-linear                            | 03 / 08 | ▪▪▪      | Commit-safety discharged per decision point in phase 03; profiling and hot-state tuning in phase 08.                                                                                                                                               |
| A pipeline step is subtly non-semantics-preserving and slips the net | 03      | ▪▪▪      | Keep every step small enough to prove by eye; assert its structural post-condition; the interpreter corpus-diff is the behavioural backstop.                                                                                                       |
| Arena/backtracking scratch leaks or corrupts                         | 06      | ▪▪       | Input-bounded lifetimes; ASan/UBSan in CI; discard provisional state through the arena only.                                                                                                                                                       |
| Incumbency: 1.1 quirks are load-bearing in real configs              | —       | ▪▪       | Out of scope to "fix" silently; position as a conformance upgrade, document behavioural deltas from libyaml/1.1.                                                                                                                                   |

## §6 — Future work

Wanted, but not planned, and not on the way to anything else:

- **`YS_RESUME_INDENT`** — resume after a malformed document at the next less-indented line, bounding the error by the
  document's own indentation rather than skipping to the next document wholesale. Finer-grained than
  `YS_RESUME_DOCUMENT`, and harder: it needs the indentation stack to survive an error, which nothing else asks of it.

- **Lenient wire positions** — treat a `#` line in the wire as a comment, not a required field. Where it carries a token
  position (`# B: …, C: …, L: …, c: …`), use it; where it does not, estimate the position from the tokens themselves
  where that is possible, and otherwise give the token an obvious "no position" value rather than rejecting the wire.
  This lets a wire be hand-written or trimmed, position lines and all, and still read.

- **Token-emission levels** — a knob in `ys_options` choosing how much of the stream `ys_next_token` emits, coarsest to
  finest, each a superset of the last:

  - the structure markers alone — the `begin-`/`end-` pairs that bracket the productions;
  - the payload too — the content characters, which is what the default emits;
  - the non-payload characters as well — indentation, separation, breaks, indicators — so every input byte is covered;
  - the detection values too — `YS_CODE_DETECTED` tokens carrying the `m`/`t` an indentation or chomping rule computed,
    which is what makes libyeast's detection comparable to the reference's `Detected` output token for token.

  This is why `YS_CODE_DETECTED` is in the vocabulary already: the finest level is where libyeast emits it, and the wire
  round-trips it in the meantime. A coarser level is cheaper and is all a caller loading a document needs; a finer one
  is what the differential oracle and a debugger want.

## §7 — Shape of the whole

For one very strong engineer who deeply knows both YAML and parser generation, this is a **many-months to
low-single-digit-years** project. The difficulty is lumpy, not uniform:

- **The easy ~70%** — grammar IR, the structural steps of the normalization pipeline, C codegen, the ABI layer.
  Well-trodden compiler work; high effort, low research risk.
- **The hard ~20%** — the determinize steps of the pipeline (phase 03): reducing each decision to a commit-safe one-char
  gate, faithful-by-construction. This is the part that determines whether the result is worth more than libyaml, and it
  touches formal methods.
- **The judgment-heavy long tail** — the semantic spec beyond the BNF (phase 04), continuous throughout, the source of
  the subtle bugs no single test happens to catch.

The reason this doesn't already exist isn't that any one piece is impossible. It's that the *valuable* version requires
the determinization to be faithful-by-construction — a real proof effort on top of a real compiler — and the set of
people who can do both *and* care enough about YAML specifically is tiny. The generator, not the parsers, is the
mountain. This plan is a route up it.
