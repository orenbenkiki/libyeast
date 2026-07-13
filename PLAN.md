# A grammar-derived C YAML parser generator — implementation plan

Emit a fast, single-pass, pull-driven YAML 1.2 parser in C **from the formal productions** — so that correctness is a
property of the generator, not of hand-testing. The output is the machine you'd hand-write anyway; the value is that a
proof, not luck, says it's the right language.

- Target: **libyamlstar** ABI-compatible `.so`
- Complexity: **O(n)**, libyaml-class
- API: **pull** · `next_token()`
- Stream: **yeast** · reference-identical codes

Throughout, two grammar parameters are treated differently and it matters everywhere: **`c`** (context) is resolved at
generation time (static), **`n`** (indentation) is threaded into the runtime automaton.

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

It bounds the *key* deferral, and nothing else. Indentation detection is a second deferral, and it is not line-bounded:
a block collection's indentation is measured past any number of comment lines, and a block scalar's past any number of
empty ones. The Haskell reference looks ahead across them and re-parses them afterwards, which a backtracking parser can
afford. libyeast cannot — unbounded lookahead is unbounded input retention, and for a streaming parser that is a memory
bound an attacker chooses. So libyeast **consumes** where the reference peeks: the tokens of those lines do not depend
on the indentation being measured, so they are emitted as the lines are crossed, and only then is the indentation read
off the first line that is not skippable. One forward pass, nothing retained.

## §1 — The central principle: two parameters, two fates

The productions are indexed by two parameters. The generator's core intellectual move — a binding-time analysis — is to
treat them completely differently.

- **`c` — context · STATIC.** `c` ranges over a **finite** set (block-in, block-out, flow-in, flow-out, block-key,
  flow-key). Specialize it away at generation time: each `c`-parameterized production monomorphizes into ≤6 concrete
  ones. Compile-time. Gone from the runtime.
- **`n` — indentation · RUNTIME.** `n` is an **unbounded** integer threaded as `s-indent(n)`, `s-indent(<n)`,
  `s-indent(≤n)`. It cannot be specialized away; it must survive into the emitted automaton, carried on the indentation
  stack.

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

**Two coupled layers:**

- **Scanner** — a character state machine that emits a queue of tokens. It over-reads eagerly *only* within the one
  bounded region it must: the simple-key line. Internally it buffers that line, resolves key-vs-scalar, and still hands
  tokens out one at a time.
- **Parser** — consumes the token queue against the (`c`-specialized, `n`-threaded) grammar automaton, emitting the
  event/node stream.

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
  half and already eats these tokens, so the JSON path is a front-end swap, not new code (phase 10).
- **A debug view** — folding the balanced `Begin`/`End` markers rebuilds the nested productions tree, rendered by the
  package's own `yaml2html` (migrated from the Haskell reference; phase 09). Identical codes make the port a faithful
  copy, validated against the Haskell reference's own rendering.
- **The differential oracles** — identical codes make the yeast comparison against the Haskell reference
  token-for-token; the folded load output is checked value-for-value against the Clojure reference (§3).

**Where the rewind problem went:** a backtracking parser would have to discard emitted events on every failed
alternative. The determinized automaton (phase 06) doesn't backtrack — committed transitions emit on commit, so there is
nothing to rewind. The only provisional events are those inside the line-bounded simple-key lookahead; they live in the
deferred queue above and are discarded there if the key hypothesis fails. The single-line rule that bounds that deferral
bounds its event retention too. Indentation detection, the other deferral, retains nothing at all: it consumes and emits
as it goes (§0).

**The grammar is libyeast's own.** `grammar/annotated.yaml` holds the productions *and* the yeast codes they emit; the
vendored `yaml-spec-1.2.yaml` holds neither the token layer nor the structure it needs, having inlined the indicator
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

Phases are ordered by dependency. The first real leap is **phase 01** — a working slice of the parser — and it is the
biggest single jump in the plan. The deepest research risk lives in **phase 06** (determinize), with **phase 05**
(semantics) close behind. The rest — grammar IR, `c`-specialization, pushdown lowering, codegen, and the ABI layer — is
well-trodden compiler work.

### Phase 00 — Grammar IR · Ingest the productions into a typed IR

**Done.** `grammar/annotated.yaml` is the generator's source, `generator/ir.py` the typed IR, and four gates hold them:
the grammar round-trips through the IR losslessly, every reference resolves with a matching arity, every character the
parser consumes lies within a token annotation, and erasing libyeast's additions recovers the vendored official grammar
production for production. The decoder is generated from it. `DESIGN.md` says where each piece lives; `CHANGELOG.md`
records what it is.

What that phase taught, which the rest of this plan is written against: **the vendored grammar cannot be the source of
truth.** It inlines the indicator characters, so it cannot say that a quotation mark opens a scalar as an indicator but
is meta inside an escape, and it names no token at all. libyeast's grammar restores that structure and annotates it, and
the erasure gate is what keeps the addition from becoming a change.

**What is left of it: make `grammar/annotated.yaml` a document, not a dump.** It works, and it is gated, but it was
written by a machine and reads like it — no header, no sections, no word about any production. The vendored
`yaml-spec-1.2.yaml` is a reference someone can learn the grammar from; ours is the only place the *yeast token format*
is written down anywhere, and it should be at least as good:

1. A header explaining the notation — the operator vocabulary, and libyeast's three additions (`(token)`, `(wrap)`,
   `(emit)`), with what a token annotation means: a code scoped over what a node consumes, cut into runs at its edges.
1. The yeast codes themselves, named and explained, since no other document lists them.
1. The spec's own chapter structure, so a production can be found where the reader expects it.
1. Per production: its number, its BNF, and — where it carries one — what it emits and why that is the right code.
   `c-quoted-quote` marking the second quote `meta` while the first is an `indicator` is a decision, and a reader should
   not have to reverse-engineer it.

The comments are free to be written by hand: the round-trip gate compares parsed data, so nothing a comment says can
break it, and nothing regenerates the file.

Still to be scoped, when the parser needs them: an independent correctness leg running the yaml-test-suite through the
yaml-grammar harness against our IR, and the hand-off into `grammar2parser.py`. (The grammar is version-stable — 1.2 and
1.2.2 share productions — so it also matches the `yamlreference` token oracle of phase 02.)

### Phase 01 — First productions · A walking skeleton that emits real tokens

*Risk: High — the first real leap.* **This is the biggest single jump in the plan**, and it will be broken into many
small, individually-verified sub-steps when it is actually tackled; it is listed here as one phase only to mark it as
the next major goal. Take a handful of grammar productions all the way through — IR → minimal lowering → C — so
`ys_next_token` emits genuine yeast tokens for simple inputs, replacing the "not implemented" stub for those cases.
Checked against **hand-written expected token streams** (the reference-parser oracles come next, once there is output to
compare). The point is a *thin* vertical slice that proves the whole pipeline on something small before the machinery is
built out properly.

Illustrative shape (to be decomposed carefully when this phase begins):

1. Pick a minimal, self-contained subset of productions (e.g. a plain scalar, a flow sequence) that exercises the token
   model without the hard indentation/lookahead machinery.
1. Build the thinnest end-to-end path that emits the correct yeast `Code` stream for that subset.
1. Assert output against hand-written expected token streams for a small set of inputs.
1. Grow the subset one production at a time, staying green at every step — the incremental discipline the rest of the
   generator work will follow.

**Exit** — `ys_next_token` produces correct yeast tokens for a first, hand-checked subset of YAML; the pipeline is
proven end-to-end on something small.

### Phase 02 — Differential oracles · Pin the output against the reference parsers

*Risk: Low · ~2–4 wks.* Now that the parser emits real tokens, stand up the two things that judge every later phase.

1. Vendor the YAML Test Suite; build a runner that consumes its event-stream expectations.
1. Wire *both* reference parsers as differential oracles behind stable diff interfaces: the Haskell `yamlreference` for
   token-level (yeast) diffs, the Clojure reference for value-level (load/event) diffs.
1. Bootstrap the yeast→HTML debug view on the Haskell reference's `yaml2html` as the divergence microscope; it later
   serves as the reference oracle for the package's own port (phase 09).
1. Set up CI: every commit runs suite + differential fuzz corpus, reports first divergence.

**Exit** — a red/green harness scores the parser against both oracles on every commit.

### Phase 03 — Binding-time · Classify each parameter static vs runtime

*Risk: Medium · ~2–4 wks.* The analysis that decides the machine's whole shape. Mark `c` compile-time, `n` runtime, and
prove nothing leaks across the line.

1. Enumerate the finite domain of `c`; confirm it is closed and bounded across all productions.
1. Trace `n`'s propagation; verify it only ever appears in indentation predicates (`<n`, `≤n`, `=n`).
1. Flag any production where a parameter's binding-time is ambiguous — resolve by hand, document the ruling.
1. Emit an annotated IR tagging every parameter occurrence static or dynamic.

**Exit** — every parameter occurrence carries a proven binding-time tag.

### Phase 04 — Specialize c · Monomorphize context away

*Risk: Low · ~3–5 wks.* Partial-evaluate over `c`. Each context-parameterized production expands into its concrete
instances; the runtime never sees `c` again.

1. For each production, generate one specialization per reachable `c` value.
1. Prune unreachable specializations (not every context reaches every production).
1. Simplify: collapse now-constant branches, dead-alternative elimination.
1. Verify against oracle that the specialized grammar recognizes exactly the original language.

**Exit** — a `c`-free grammar, still parameterized only on `n`, language-equivalent to the source.

### Phase 05 — Semantics · Specify what the BNF doesn't say

*Risk: High · ~4–8 wks.* The productions alone don't fully specify a parser. The spec leans on prose for rules that must
become an explicit, auditable input to the generator — this is where bugs get smuggled in undetected.

1. Encode the single-line simple-key restriction and its hard length bound as a first-class constraint.
1. Specify tab handling (forbidden as indentation), and the comment rule (`#` starts a comment only after whitespace:
   `foo#bar` is a scalar).
1. Decide and document duplicate-mapping-key policy (error / last-wins / first-wins) — the spec underspecifies it.
1. Specify line-break normalization, BOM handling, and error-recovery states.
1. **Indentation detection** — the official grammar leaves it undefined and says so: `<auto-detect-indent>` is declared
   a "special rule" and never given a meaning, and a block scalar's `m` is set to the *string* `"auto-detect"`, so that
   `l-literal-content(n + m)` is an IOU nothing redeems. Three productions close it — the indentation of a block
   collection, of a block scalar, and of an inline collection — and libyeast writes them **consuming**, where the
   reference peeks: the tokens of the lines crossed on the way do not depend on the indentation being measured, so they
   are emitted as they are crossed, and only then is the indentation read off the first line that is not skippable.
   Needs value-returning productions in the IR (the reference's `do m <- …`), and the first entry in
   `check_vendor_spec.py`'s `DEVIATIONS`, saying that libyeast consumes where the official grammar is silent.
1. **Settle a real divergence first.** The reference treats a leading all-space line longer than the detected indent as
   *content*; the spec calls a line with no non-space character *empty*, and an over-indented empty line an *error*.
   They disagree, the differential oracle will trip on it, and the answer must be decided before the productions are
   written rather than after.
1. Mark each rule as "grammar" vs "asserted semantic action" so the fidelity claim is honest about its boundary.

**Exit** — a written semantic spec, versioned alongside the IR, covering every rule beyond the BNF.

### Phase 06 — Determinize · Committed, bounded-lookahead automaton

*Risk: High — the prize · ~3–6 mo.* Convert the priority-and-lookahead grammar into a deterministic, backtrack-free
recognizer that runs in O(n). This is where fidelity is won or lost, and the only phase with real research risk.

1. Compute, per decision point, the lookahead needed to commit; prove it is bounded by the single-line key rule.
1. Replace ordered-alternative backtracking with peek-k-then-commit transitions.
1. Attach the yeast `Begin`/`End` emission points to productions and confirm they survive determinization: committed
   transitions emit on commit, so no rewind is needed; provisional events stay confined to the bounded-lookahead region.
1. For each commitment, discharge a refinement obligation: the committed choice preserves the source language.
1. Where a full proof is infeasible, isolate the decision and pin it with exhaustive differential + fuzz coverage; log
   it as an assurance gap.
1. Handle the two indentation gotchas explicitly: zero-indent sequences under a key, and flow context suspending
   indentation entirely.

**Exit** — a deterministic automaton IR with a documented fidelity argument per commitment point.

### Phase 07 — Pushdown IR · Lower to an explicit, suspendable stack machine

*Risk: Medium · ~2–3 mo.* Target the pull API. Emit state + explicit stack + dispatch loop so continuation is a
serializable struct — never the C call stack.

1. Choose the stack representation: unified grammar+indentation frames, or two coupled stacks. Decide early; it colours
   all codegen.
1. Lower automaton transitions to a state enum and a single-loop dispatcher.
1. Thread `n` through the indentation stack; encode `<n`/`≤n`/`=n` as stack comparisons.
1. Define the suspend/resume points so `next_token()` can save-and-return at any token boundary.
1. Model errors as a terminal "return ERROR, stay halted" state, not a longjmp.

**Exit** — a lowered IR whose execution state is fully captured by a serializable struct.

### Phase 08 — Scanner split · Two-layer scanner/parser with a provisional queue

*Risk: Medium · ~2–3 mo.* Make pull genuinely manageable by separating the character scanner from the grammar parser,
with the deferred-token set as an explicit tagged queue.

1. Build the scanner as its own character state machine emitting tokens with a provisional/resolved flag.
1. Implement bounded eager buffering for the simple-key line; resolve key-vs-scalar on line completion or `:`.
1. Implement the "is the head resolved?" predicate that gates `next_token()`.
1. Feed resolved tokens to the parser layer; verify end-to-end event output against the oracle.

**Exit** — tokens flow scanner → queue → parser → events, pull-driven, oracle-clean.

### Phase 09 — C codegen · Emit the C library

*Risk: Low · ~1–2 mo.* The easy end of every compiler. Turn the lowered IR into a switch-on-state character loop with
arena allocation.

1. Emit the state dispatcher, stack ops, and scanner as portable C99 with no external deps.
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

### Phase 10 — ABI layer · Drop-in for libyamlstar

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

### Phase 11 — Harden · Fuzz, tune, and reach libyaml-class speed

*Risk: Medium · ~2–4 mo.* Correct-but-slow is not the goal. Close the algorithmic gaps naive codegen leaves and prove
robustness under hostile input.

1. Continuous structure-aware + byte-level fuzzing (ASan/UBSan) targeting the semantic long tail.
1. Profile; eliminate any residual super-linear behaviour from over-broad lookahead.
1. Benchmark against libyaml on representative corpora; tune hot states and buffering.
1. Optionally: extend to an emitter (dump), or keep libyaml's emitter alongside for a complete round-trip library.
1. Cut prebuilt binaries per platform so adoption isn't gated on a local native build.

**Exit** — O(n) confirmed, libyaml-competitive, fuzz-clean, packaged.

## §5 — Risk register

| Risk                                                                | Phase   | Severity | Mitigation                                                                                                                                                                                                                                         |
| ------------------------------------------------------------------- | ------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Malicious input triggers memory-unsafety or resource-exhaustion DoS | all     | ▪▪▪▪     | Hardening flags on the release build; ASan/UBSan on every run; structure-aware fuzzing from day one; bounded allocation plus a configurable parse-depth cap; billion-laughs / recursive-alias guards; continuous security audit, not a final pass. |
| Determinization silently diverges from the productions              | 06      | ▪▪▪▪     | Per-commitment refinement obligation; dual differential oracles on every input — token-for-token against the Haskell `yamlreference` and value-level against the Clojure reference; log assurance gaps explicitly rather than hiding them.         |
| Semantic rules beyond the BNF encoded wrongly / incompletely        | 05      | ▪▪▪▪     | Written, versioned semantic spec; each rule tagged grammar vs asserted; fuzz the corners the suite misses.                                                                                                                                         |
| The first working slice is a big leap from IR to emitting tokens    | 01      | ▪▪▪      | Decompose into many small, individually-verified sub-steps; grow the production subset one at a time, staying green; hand-checked expected outputs before the reference oracles exist.                                                             |
| Naive codegen is correct but super-linear                           | 06 / 11 | ▪▪▪      | Lookahead-boundedness proof in phase 06; profiling and hot-state tuning in phase 11.                                                                                                                                                               |
| Binding-time analysis mis-classifies a parameter                    | 03      | ▪▪▪      | Prove `c`'s domain closed and `n` confined to indentation predicates; oracle-check the specialized grammar.                                                                                                                                        |
| Stack representation choice fights the pull API                     | 07      | ▪▪       | Decide unified-vs-coupled stack early; prototype suspend/resume before full codegen.                                                                                                                                                               |
| Arena/backtracking scratch leaks or corrupts                        | 09      | ▪▪       | Input-bounded lifetimes; ASan/UBSan in CI; discard provisional state through the arena only.                                                                                                                                                       |
| Incumbency: 1.1 quirks are load-bearing in real configs             | —       | ▪▪       | Out of scope to "fix" silently; position as a conformance upgrade, document behavioural deltas from libyaml/1.1.                                                                                                                                   |

## §6 — Shape of the whole

For one very strong engineer who deeply knows both YAML and parser generation, this is a **many-months to
low-single-digit-years** project. The difficulty is lumpy, not uniform:

- **The easy ~70%** — grammar IR, `c`-specialization, pushdown lowering, C codegen, the ABI layer. Well-trodden compiler
  work; high effort, low research risk.
- **The hard ~20%** — provably-faithful determinization (phase 06). This is the part that determines whether the result
  is worth more than libyaml, and it touches formal methods.
- **The judgment-heavy long tail** — the semantic spec beyond the BNF (phase 05), continuous throughout, the source of
  the subtle bugs no single test happens to catch.

The reason this doesn't already exist isn't that any one piece is impossible. It's that the *valuable* version requires
the determinization to be faithful-by-construction — a real proof effort on top of a real compiler — and the set of
people who can do both *and* care enough about YAML specifically is tiny. The generator, not the parsers, is the
mountain. This plan is a route up it.
