# A grammar-derived C YAML parser generator — implementation plan

Emit a fast, single-pass, pull-driven YAML 1.2 parser in C **from the formal productions** — so that correctness is a
property of the generator, not of hand-testing. The output is the machine you'd hand-write anyway; the value is that a
proof, not luck, says it's the right language.

- Target: **libyamlstar** ABI-compatible `.so`
- Complexity: **O(n)**, libyaml-class
- API: **pull** · `ys_read_token()`
- Stream: **yeast** · reference-identical codes

Throughout, the grammar's parameters split by two fates and it matters everywhere: **`c`** (context), **`t`** (chomping)
and **`r`** (the resume policy) are finite, so they are resolved at generation time (static); **`n`** (indentation) and
**`m`** (the auto-detected indent) are unbounded, so they are threaded into the runtime automaton.

## §0 — Why this, and why it's hard

Every YAML parser to date sits on one horn of a dilemma.

**YAMLStar** (pure Clojure) is mechanically faithful to the ~211 productions but slow — naive backtracking over the
grammar can go superlinear, and it ships a heavy runtime (GraalVM today, Go tomorrow). The **libyaml-class**
hand-written state machine is fast and O(n) but its conformance is established test-by-test; it has known deviations
from the 1.2 grammar and is "faithful by luck."

The reconciliation is a machine that is *both*: a deterministic, committed, character-at-a-time automaton with an
indentation stack and bounded deferred-token tracking — **generated from the parameterized productions** so that its
speed comes from the state machine and its fidelity comes from the derivation. That generator is the whole prize, and it
is a small compiler, not a weekend parser.

**The one load-bearing fact:** YAML restricts implicit ("simple") keys to a **single line**. That restriction is what
makes determinization finite, makes the deferred-token set bounded, and makes a pull `next_token()` able to return
without draining the whole document. It is doing triple duty and the entire architecture rests on it.

It bounds the *key* deferral, and nothing else. There is a second, and the block scalar is where it lives.

**Indentation detection is not the problem.** A block collection's indentation, and an inline one's, are read straight
off the current column — YamlReference peeks past comment lines first, but `s-l-comments` has already eaten them, so
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

`max_bytes` bounds it, being the same guard a single enormous token needs. The buffered input, the tokens held back with
it, and the stack that deep nesting grows are capped together, since a run that is never resolved grows all three; past
the cap `ys_read_token` returns `YS_FAILED_MEMORY`, the caller's sizing to fix.

## §1 — The central principle: six parameters, two fates

The productions are indexed by six parameters, and the generator's core intellectual move — a binding-time analysis — is
to sort them into two fates and treat those completely differently. `c` and `n` are the exemplars.

- **`c` — context · STATIC.** `c` ranges over a **finite** set (block-in, block-out, flow-in, flow-out, block-key,
  flow-key). Specialize it away at generation time: each `c`-parameterized production monomorphizes into ≤6 concrete
  ones. Compile-time. Gone from the runtime.
- **`n` — indentation · RUNTIME.** `n` is an **unbounded** integer threaded as `s-indent(n)`, `s-indent(<n)`,
  `s-indent(≤n)`. It cannot be specialized away; it must survive into the emitted automaton, carried on the indentation
  stack.

The others follow the same two fates: **`t`** (chomping — strip, clip, keep) and **`r`** (the resume policy — what
`ys_options.resume` chooses) are finite, so they are specialized away like `c`; **`m`** (the auto-detected indent) and
**`f`** (the floor a block scalar's leading empty lines set for its first content line) are unbounded integers, so they
are threaded like `n`. So the runtime carries `n`, `m` and `f`, and never sees `c`, `t` or `r` — the emitted C is one
automaton per resume policy, and `ys_options.resume` picks the start state.

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

**The canonical stream is yeast.** The event/node stream the parser emits is **yeast** — YamlReference's own token
model: zero-width `Begin`/`End` markers wrapping the interesting productions, and every consumed character classified as
a leaf token (`Indicator`, `White`, `Indent`, `Break`, `Text`, `Meta`, …). The codes are kept **byte-identical to
YamlReference's `Code` constructors** (already declared as `ys_code` in the public header), and that one decision makes
the single stream do three jobs at once:

- **The load API** — `compose → resolve → serialize` is a downstream fold over yeast. YAMLStar already owns that back
  half and already eats these tokens, so the JSON path is a front-end swap, not new code (phase 05).
- **A debug view** — folding the balanced `Begin`/`End` markers rebuilds the nested productions tree, rendered by the
  package's own `yaml2html` (migrated from YamlReference; phase 04). Identical codes make the port a faithful copy,
  validated against YamlReference's own rendering.
- **The differential oracles** — identical codes make the yeast comparison against YamlReference token-for-token; the
  folded load output is checked value-for-value against YAMLStar (§3).

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
  authoritative for a different half of the pipeline. Against **YamlReference** the comparison is *token-for-token* on
  yeast — codes are identical, so it validates the syntactic layer (production structure, character classes) at the
  finest possible grain. Against **YAMLStar** the comparison is *value-for-value* on the folded load output, validating
  composition and schema resolution — the semantic layer. Agreement with both spans the whole pipeline; a mismatch with
  exactly one half localizes the bug, and the yeast→HTML debug view renders the divergence.
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

Phases are ordered by dependency. **Phase 02 is a gate**: the grammar and its fixtures are made spec-complete and
enforcing before the transformation touches them, so Phase 03 never has to go back to the grammar. The deepest research
risk then lives in **phase 03** (the normalization pipeline, where determinization happens); the semantic decisions that
feed it are settled in phase 02. The rest — codegen and the ABI layer — is well-trodden compiler work.

### Phase 01 — The reference interpreter · the grammar's executor, and the project's oracle

The interpreter is complete: it executes the grammar over the input a byte at a time — a byte that begins no character a
value of its own, `<invalid>`, rather than an exception thrown before the parse starts — so a token's text is the input
bytes as they are, and ill-formed input is proved against fixtures, in the grammar, before the C parser is generated
from it. Everything downstream then has one oracle instead of two.

Still to be scoped, when something needs it: the interpreter's committed mode is phase 03's, where the grammar it judges
is the one that has been transformed.

### Phase 02 — Completeness · the grammar and fixtures made spec-complete, and the gate before transformation

Phase 02 is complete: the grammar is as spec-complete as it can be made, and its gate holds. The BNF is recovered
production-for-production from libyeast's own grammar (`check_vendor_spec`), and every constraint the spec leaves to
prose — the single-line simple-key restriction and its length bound, tab forbidden as indentation, `#` opening a comment
only after whitespace, line-break normalization, and the block-scalar §8.1.1.1 rules — is a rule in the grammar with a
fixture that fails when the behaviour is wrong, or a declared deviation with its reason. The independent net is the YAML
Test Suite, folded to events (`make verify-star`): every case folds to its events or rejects where it must, one declared
divergence apart — where the YAML Test Suite follows YAMLStar past the spec, and libyeast follows the spec. With
`check_vendor_spec` green, the prose inventory closed, and the fold green-or-declared, the grammar is frozen for Phase
03 to carry through its provable steps.

Still to be scoped, when something needs it: the yeast→HTML debug view — the divergence microscope, bootstrapped on
YamlReference's `yaml2html`, later the oracle for the package's own port (Phase 04) — and the differential fuzz corpus
CI runs beside the fold.

### Phase 03 — Normalize · the grammar-to-canonical pipeline, every decision committed

*Risk: High — the prize · ~5–9 mo.* This is where the backtracking grammar becomes a committed one, and it is done not
as one leap but as a **series of small, individually-provable, semantics-preserving transformations** that carry the raw
IR to a canonical form a state machine falls out of. It runs over the grammar frozen at Phase 02's gate: every step
preserves the interpreter's token stream, so a grammar bug is Phase 02's to have caught, never this pipeline's to
introduce. Binding-time, `c`/`t` specialization, determinization, and the lowering to a pushdown shape — the old
separate mechanical phases — are all steps of this one pipeline. Performance of the generator is a non-issue; every step
is written to be simple enough to prove by eye, and checked two ways after it runs.

**The canonical form.** A **terminal production** is a set of characters, nothing more. A **nonterminal production** is
an ordered list of alternatives. An alternative is `gate  actions…  [P1  actions…]  [P2]`:

- The **gate** is a conjunction, tested without consuming: an optional peek — a character set, or a literal
  `LiteralPeek(text, then, barrier)`: the bounded run the input must begin, its follow test one class the character
  after it, if any, must match (`then`) or must not (`barrier`), the end of the input passing either — and zero or more
  zero-width guards. The alternative fires only if every part holds. An **empty** gate is the unconditional fallthrough,
  allowed only as the last alternative.
- **actions** operate on the parser's own state (below). Consuming the peeked character is itself an action, not part of
  the gate — `ConsumePeeked` likewise takes a peeked literal on the gate's word, the bytes never scanned twice.
- **P1, P2** are zero, one, or two production invocations. Two means *run P1 then P2*: push a frame whose return is P2,
  go to P1, and when P1 returns resume at P2 — so P1 is the call, P2 the continuation, and at most one push per edge.
  One is a tail-goto; none returns. There are no actions after P2: a production returns exactly when its P2 does. A
  sequence of three splits through a helper, `A → B A₁`, `A₁ → C D`, and the `_<N>` suffix names where it came from.

Alternatives are tested in order and the first whose gate fires is **committed** — no backtracking. Two alternatives may
share a gate; order resolves the overlap, and proving that the earlier one is safe to commit to is the whole of
determinization.

**The action vocabulary**, four families, each derived from an IR node or the runtime already built in `src/parser.h`:

- **Token run** — `Consume` (push the peeked character into the current run), `PushCode(code)`/`PopCode` (cut the run
  and set the code its characters carry, or take back what the push displaced from the stack), `Emit(code)` (a
  zero-width marker, which also cuts), and `OpenWindow(limit, message)`/`CloseWindow` (open and close the `(max)`
  character window, past which a committed `Consume` fails the window's cut — a count of the opens standing, since
  windows do not nest). No action reads a value off a frame. These are what `Token`/`Wrap`/`Emit`/`(max)` lower to.
- **Provisional run** — `OpenProvisional`, `MarkProvisional`, `InjectBefore(codes, at)`,
  `RetypeProvisional(rest, breaks, region)`, `CommitProvisional`, spelled in full under *The provisional mechanism*
  below, which is also where each speculation's use of them is written out. One-for-one with the `ys_queue` run, and
  only one run is open at a time. There is no discard: a failed hypothesis retypes, it never drops tokens. All five are
  zero-width to the analyses, so a production carrying them certifies by its gates alone.
- **Parameters** — `SetIndentToColumn`, `IncreaseIndentToColumn` (`f = max(f, column)`, what `(increase)` lowers to for
  the block-scalar floor), `AdjustIndent(expr)`, `SetIndentFromDigit`. Only `n`, `m` and `f` are runtime; `c` and `t`
  are specialized away and never appear. Counting indentation is a loop of `[space]` gates with an accumulator action,
  not a special multi-character gate.
- **Guards** — `AtStartOfLine`, `AtEndOfStream`, `IndentLt/Le/Eq(n)`, `WithinKeyLimit`. A guard is a cheap zero-width
  condition on an alternative; the sharp case that forces the gate to be a conjunction is indentation matching, where
  both the eat-another-space and the stop alternatives gate on `[space]` and are told apart only by `column < n`.

The load-bearing rule: **no unbounded lookahead survives.** `Look`/`NegLook`/`LookBehind`/`ExcludeAt` over more than one
character are transformed away — into a char-set gate, a literal peek, a cheap guard, or provisional speculation — so a
canonical grammar holds none of them, and the validator rejects any that remain. The one bounded exception is the gate's
own `LiteralPeek`: the longest literal plus one character of follow test, within the window the parser's fill already
guarantees, lowered to a single comparison.

**The pipeline**, one linear series, grouped only for reading. One law binds every step, present and future: a
transformation is a **local, mechanical rule** — it reads a production's own nodes, plus at most the standard
grammar-wide tables that are themselves defined production-by-production (FIRST, follow, the reference graph), and its
correctness argument is stated against exactly that. No step may lean on a global property of the parse — "this is only
ever attempted at a line start", "this position is always preceded by X" — however true by construction: a rule that
needs one is the wrong rule, and the right one spells the same fact locally, usually in a device the grammar already
owns (a measured quantity is the open run's, never an absolute machine register; a value crossing a minted helper is a
declared parameter, the `code` precedent). The only judgment a step may embody is *where* it applies — a declared target
with a written reason, ledger-style — never *what* the result looks like at a site.

Three more rules bind the pipeline, and `DESIGN.md` states them, being what the generator is rather than what it is to
do: many simple steps and never few clever ones; two things compared by making them look alike and testing equality,
never by an equivalence rule that knows what they mean; and a named site as a code smell whose target is none. The
pipeline does not satisfy them yet — six points of interest carry four declaration tables between them, and the
comparisons the second rule asks for are not all written — and **reaching conformance with them comes before driving the
meter down.** A meter driven down over steps of the wrong shape buys a number and keeps the debt; the same number
reached over steps of the right shape is a grammar that can be read. So the work below is ordered by that, not by what
moves the meter most.

The steps, then:

1. *Parameters.* Specialize `c` away (monomorphize, drop `Case`/`Flip` on `c`, prune unreachable branches); specialize
   `t` (chomping) the same way. Confirm only `n`/`m` remain, and only in indentation predicates and parameter actions.
1. *Structural.* Flatten `Seq`/`Alt`, drop `Empty`, collapse singletons. Lower `Token`/`Wrap` to token actions and
   `(max)` to the window pair. Evaluate `Diff` and single-character `Look`/`NegLook` into plain char-sets. Lower
   `Star`/`Plus`/`Opt`/`Rep` into recursive `_<N>` productions. Lower `SetVar`/`Lt`/`Le`/`StartOfLine`/`EndOfStream`
   into parameter actions and guards (`(if)(set)` already is one).
1. *Proper.* Make the grammar ε-free: a nullable production gains a consuming copy, and each call to it becomes that
   copy against the empty match distributed into the call site, so nothing matches empty by shape but the productions a
   parse enters by name. Declare the parameters a body binds, so a distributed binding still travels out.
1. *Alternative shape.* Split each `Seq` into head-matcher + tail until every alternative is `matcher; actions; tail`.
   Binarize to ≤2 calls. Hoist FIRST-sets into gates, handling a nullable call as FIRST∪FOLLOW. Turn a leading
   unconditional action into an empty-gate alternative.
1. *Determinize* — the hard tier, still small steps. Fold residual one-token lookahead into gates. Left-factor shared
   prefixes into a common gate and a branch, repeated until each decision is one-character-decidable. Insert provisional
   speculation wherever no character decides — the folding decision, the document prefix and the simple-key line, the
   plain scalar's next line, and the block scalar's empty lines — each spending the actions *The provisional mechanism*
   below formalizes. Handle the two indentation gotchas the mechanical steps miss — a zero-indent block sequence nested
   directly in a mapping, and flow context, which suspends indentation entirely. Discharge commit-safety per decision
   point, logging any residual as an assurance gap.
1. *Finish.* Assert every terminal is a pure char-set; run the validator.

**The steps ahead**, in order, and the order is the three rules' rather than the meter's: the standing steps are
repaired first, since every later rule reads what they leave; then the new ones arrive earliest and simplest first, each
a rule with a side condition read off the nodes it touches, so what follows compares with plain equality; then the
points of interest are retired as the universal steps reach them. The meter is watched throughout and driven down only
after — it will move as a consequence of the repairs, and where it moves the other way the shape is what is being paid
for.

**Before every commit, every transform is read for correctness against the semantics of the nodes it moves** — by hand,
whatever the gates say. The gates are a net and not a substitute: a transformation that moves an action into another
frame, or copies one without binding its parameters, changes nothing the corpus can see wherever the sites it hits
happen to be identities, and stays wrong for the next site. Three transforms were found doing exactly that with the
whole suite green, and each was found by reading the code and asking what the node means, never by running it. The
reading is cheapest where the answer is structural, which is what the two changes below are for; it is not skipped
because they have landed.

**And a node kind nothing places is an error, everywhere, always.** Never a default, never the answer that falls out of
a membership test the kind happens not to be in. A classification that answers for what it does not know is a silent
answer to a question nobody asked: `PushIndent` was in no list, `isinstance(action, _ZERO_WIDTH)` said `False` for it,
thirty-three zero-width actions were read as consuming, and a third of the undecided decision points went away — green
corpus, green gates, and the meter reporting progress that had not happened. The check that caught it,
`check_grammar_coverage.is_total`, caught it because it was written to refuse rather than to guess, and it refused on
the first run. So every classifier decides from lists that name every kind and raises on anything else, and adding one
is expected to surface kinds that were being classified by falling through — `Diff`, `Recover`, and every repetition and
scope in an alternative's actions were, until one asked.

**The state invariant.** Every value the parse carries is a **global singleton**, possibly empty, or an entry in **the
one unified stack**. There is no third place — no per-production frame holding a value, no scope implied by the tree
shape, no slot reachable only from where it was written. The C parser is a state machine and that stack; a value fitting
neither is a value it cannot hold, so a transformation producing one has produced something the parser cannot run,
whatever the corpus says.

- *Globals* are what does not nest: the position and its mark, the open run and the code its characters carry, the
  `(max)` window and the count of opens standing over it, `m` and `f` — computed indentations rather than scopes.
- *The unified stack* is what nests: the indentation in force, the code a `(token)` displaced, and where to carry on.
  All three are pushed and popped by actions the grammar writes, never by anything a call does on their behalf.

**And every push is written down.** There is no call, and so nothing a call implicitly pushes or pops: a production that
goes on to another pushes where to carry on and jumps, and where it comes back to is what a pop takes. Reading a call as
"push a continuation, then go" is what keeps the stack safe to transform. A value riding on something a call pushes is a
footgun, because the pipeline inlines — `inline-singles`, `inline-under-gate`, `inline-single-way` and the sweep's
splicing of do-nothing calls each remove one — and a value living on what they remove has nowhere to go and no gate that
could see it coming. Written as actions, an inlining deletes a continuation push and a jump and touches nothing else,
because nothing was ever riding them.

```
call P, carry on at Q       PushContinuation(Q) ; GOTO P
come back                   Pop ; GOTO what it held
the indentation changes     PushIndent(n) … PopIndent
a `(token)` opens           PushCode(code) … PopCode
```

The parser holds one other store, and it is not state: the **pending-token run**, the output a speculation has emitted
but not yet committed to. It is a second stack in the implementation and nothing like the first in kind — the unified
stack holds what the parse must restore, this holds what the parse has produced and may still retype. Only one is open
at a time, its extent is written in the grammar by `OpenProvisional`/`CommitProvisional`, and nothing reads a value out
of it. So the invariant covers state, and the pending run is accounted for separately rather than smuggled into it.

1. *The frame is gone, and the code is on the stack* — done, and what it turned up is worth keeping written down. Four
   values used to live in the interpreter's frame rather than in the nodes: the run `code` a `(token)` restores, the
   `(match)` origin, and the `ceiling` and `ceiling_message` a `(max)` restores. The origin went with the operator that
   needed it — a `(match)` is the open run, so an indentation is the length of the token the rule is building and
   nothing is remembered about where it began. The window is two globals, the window and the count of opens standing,
   since windows do not nest. The code is the unified stack's, its push putting what it displaces there for its own pop
   to take back, so `_bound` reaches every action whole and moving one is a substitution rather than an argument about
   frames.
   - A per-production device was tried first and is the second mechanism the invariant forbids. It cost a whole
     apparatus to make safe — a slot named per nesting depth, a gate on which slots an enclosing open holds, and a
     fixpoint over the call graph proving that a pop whose push a factoring left in a caller reaches a slot nothing
     overwrote. One stack for the parse replaces all of it with one rule: pushes and pops balance, refused at run time
     where they do not. Naming a slot per site, tried before that, was wrong the other way: `factor-prefixes` went dead
     within the minute, because the scan `refine-indents` mints and the scan the lowering produces stopped comparing
     equal. The same actions everywhere is what a factoring compares.
   - The workaround the stack replaces goes with it: the `code` parameter `single-consumes`, `binarize`,
     `alternative-shape`, `factor-prefixes` and `extend-returns` each added, `_moved_actions` refusing to move a run of
     actions closing a scope it did not open, and the fold's own refusal of the same. All three existed because a close
     read its own frame, and none of them has a reason now.
   - What stays implicit is the `(set)` target, a name rather than an expression.
1. *`n` on the same stack* — done. `n` was a parameter, put in scope by the call rather than by an action, and taken
   back out on the way home. `push-indents` mints a `PushIndent` at each of the thirty-three calls measured against an
   `n` other than the one in force — the other seven hundred passed it through and push nothing — and `read-indents`
   then drops the parameter: every read becomes `Indent`, the indentation in force, and the declaration and the argument
   go with it. The final grammar declares `m` and `f` and nothing else, and `prune-params` behind it drops even those
   wherever nothing reads them.
   - What held it: the two mechanisms stood side by side for a whole gate, every read of `n` compared against the stack
     over 694 fixtures and 402 suite cases before the reads became the stack's. The comparison is live still, at
     `push-indents`, which is the last grammar carrying both — so the drop is proved rather than argued, and the
     suppression the two need while both run stays where they both run.
   - The drop decides nothing, and the meter's fall is not its doing: on the step's own output the meter is unmoved at
     473 points, and the 47 it loses are the sweep's, sixteen productions merging once the argument stopped telling them
     apart. Nine of those carried conflicts. Simplification, not determinization, and worth keeping straight.
   - What the interpreter had to be told: `<auto-detect-indent>` read `n` out of the environment directly rather than
     through a `Param`, so no rewrite could see it. The indentation in force is one accessor now, answering from the
     stack where the grammar pushes and from the parameter where it does not.
   - The pop is a production the push carries on at, not a property of the edge: a call measured against its own
     indentation carries on where that indentation comes back off, and then wherever the alternative was carrying on.
     One production is the pop and nothing else — `x-pop-indent`, shared by every push with nowhere else to go — and the
     rest hold a continuation behind it. Each declares no parameters and reads the ambient ones, so a continuation's
     arguments are evaluated inside it, after the pop, where the stack and the parameter agree.
   - Whether a frame is what a pop wants is `sink-pops`', on a grammar where every one of them stands: minting the frame
     and deciding to keep it are two jobs, and split they are two local rules rather than one that looks ahead.
   - What the check caught, none of which an argument would have: a continuation that changes the indentation has no
     return of its alternative's to take it back, and needs a production holding that call alone; and an in-grammar
     `(recover)` left the pushes of the parse it abandoned standing, where `_fail` had always cleared them — a real
     fault, not scaffolding, found because every read compared.
   - Unchecked, and holding by construction rather than by a refusal: a `(recover)` rebinds `n` exactly as the call it
     rides does, at all seven sites, which is why the push made for the call answers for the recovery too. Nothing
     compares the two, and `push-indents` does not look at a recovery's arguments at all.
1. *The call written out.* `first` and `second` are a call and where to carry on, which the machine performs — the last
   thing a production does that the grammar does not spell. `PushContinuation(second)` and a jump to `first` say it, and
   a `Pop` and a jump say the way home. Nothing is left that pushes or pops without an action naming it, and an inlining
   becomes what it should be: deleting a push and a jump, with no value riding either.
1. *A gate verifier, mechanistic and in both directions.* A gate is correct when its character set is exactly what the
   options behind it can consume first: every character the gate admits is consumable by one of them, and every
   character one of them can consume is admitted by the gate. The first direction failing means the gate lets through a
   character nothing takes; the second means it refuses a character the alternative could have matched, which is a lost
   parse.
   - The set is computed by walking an alternative's elements carrying `alive`, the characters at which the walk can
     still stand here having consumed nothing: an element that must consume contributes `alive ∩ its own set` and ends
     the walk, one that may consume nothing contributes the same and leaves `alive` alone, and a call contributes
     `alive ∩ entry(callee)` and narrows `alive` to `alive ∩ empty(callee)`, since passing a call without consuming
     needs that call to match empty right there. `entry` is a least fixpoint and `empty` a greatest one.
   - It computes its own character sets rather than reading the first tables, which err wide on purpose — a certificate
     stands on disjointness, so too wide refuses safely, and an equality check has no use for it. Where the verifier
     cannot decide a set exactly it says so and counts it, rather than passing.
   - It runs after every step from `gate-hoist` on, beside the properness check, and an alternative that can match empty
     is judged on the characters where it can, which its callees' own gates decide.
1. *The standing steps, repaired* — done, and what it turned up is worth keeping written down. The splits landed and
   changed no grammar: `lower-tokens` and `lower-wraps`, `span-consumes` and `literal-consumes`, `hoist-char-runs` and
   `hoist-trimmed-runs`, `gate-hoist` and `gate-hoist-wide`, and the leftover's leading `Lt`/`Le` rising into its gate
   as `hoist-residue-guards`. Three things came out of doing them, none of them a split:
   - `flatten` was not compound at all. Its docstring claimed it expanded a fixed `(k)` repetition into its copies, and
     nothing in it ever did — `span-consumes` removes every `Rep`, literal count and runtime count alike. The claim was
     corrected rather than a step written for it.
   - `factor-prefixes` keeps its maximal-scan admission. It is a side condition on what may join the prefix, not a
     second job: split out it would be two steps over one walk differing by a boolean, which is one rule with a knob and
     worse by the same principle that motivates the splits.
   - Every step must now change the grammar, and one that does not is a fault named where it stands. The check is what
     caught a real regression: only a merge is a rename, and a splice followed as one slid a declared inline off the
     line-prefix wrapper it names and onto the indent scan underneath, dissolving what `refine-indents` refines and
     leaving four steps idle. With splices no longer followed, the two line-prefix inlines retired — the universal
     splicing is that inlining done everywhere — and the way-subsumption step retired with them, its shape reaching it
     nowhere in the pipeline once the splicing had absorbed the spliced separations it was written for.
   - Left alone for now, noted so it is not rediscovered: `monomorphize` specializes `c`, `t` and `r` in one pass over
     their combinations, and three passes of the one generic operation would give the same grammar with three
     separately-diffed steps. The copies are made per combination, so the split wants care it has not earned yet.
1. *The new steps, earliest and simplest first.* The first two are landed — they exist to put the pieces in one place,
   nothing being orderable or comparable while it sits in different frames — and both belong after `split-conflicts`,
   which is what narrows a gate enough for either to see anything. `inline-under-gate` gives a call only the ways its
   caller's gate can reach, splicing the survivor's actions in where one bare way is left; `inline-single-way` splices a
   call whose production has one ungated way, actions and calls alike. What they bought: the block header's two ways
   both go on the chomping call now, told apart by the auto-detect bundle standing in front of it in one of them.
   - `inline-single-way` still refuses a *gated* single way, so a callee whose peek the caller's gate already implies
     stays a frame — `c-chomping-indicator_t_keep` is one, gated on the `'+'` its caller is gated on. Widening the side
     condition to "the callee's peek contains the caller's" is the next small move, and it puts the chomping consume
     into both header lists.
   - Even then the header does not factor, and the reason is worth keeping: the `order-actions` side condition needs a
     non-break consume *earlier in the same list*, and the scalar's own `|`/`>` is consumed two frames up in
     `c-l+folded`. Either that call is inlined too, or the condition is met another way.
   - `order-actions` — within one action list every action moves as early as it may, leaving a canonical order the later
     steps compare with `==`. Four side conditions, each read off the list: a frame-scoped action never crosses its
     partner; an emitter never crosses a consume or another emitter, the stream's order being the output; a
     position-reading action crosses a consume only where that consume's set excludes line breaks and an earlier consume
     in the same list does too, the at-line-start bit being clear on both sides; and a value-reading action never
     crosses what writes what it reads. That third condition is the whole of the block header's difficulty: the
     auto-detected indent reads the position through one bit, the two orderings run it a character apart, and the bit is
     already clear — because the scalar's own indicator was consumed two frames up, which is why the fact has to be
     brought into the list before the rule can see it.
   - `factor-calls` — a shared leading call joins the prefix. *Holds where the call is identical in name and arguments
     across every way and their gates are equal.* One production today; the shape the three steps above create.
   - `subsume-ways` returns when it has work, and wider than it left: dropping an earlier way whose later twin carries
     the same actions and calls but for trailing zero-width guards — the narrower way dies, the streams identical by
     construction. What an elimination leaves wherever a barrier rode one ordering and not the other.
   - `merge-ways` — two ways of one choice that are equal after `order-actions` become one. *Plain equality.* The
     within-production twin of the sweep's behavioural merge, which today reaches whole productions only.
   - `drop-dead-ways` — a way whose gate is disjoint from every character its production can be entered on is
     unreachable. *Reads the root-down entry sets the meter already computes.*
   - `sink-pops` — landed. A pop holder is an ungated way whose actions are the pop and only the pop; where every
     reference to what one calls is such a way, the pop leads that production's own ways instead and the holders stop
     doing it, a frame with nothing left going to the sweep. *There is no other way in for the pop to be wrong for,
     however many holders say it.* Run to a fixpoint, because sinking makes holders — a way that did nothing of its own
     before its call becomes one once the pop above it has come down, which is how the block collections are reached at
     all, on the second round. A continuation whose arguments read the indentation keeps its holders, those being read
     before the pop rather than after, as does one a parse enters by name, and a recovery, which a cut reaches with the
     stack as it stood before the call. Twelve holders come to five. What it is for is `defer-pops` below, which cannot
     see a pop standing behind a frame.
   - Tried and reverted, so it is not tried twice: giving the holders a copy where other ways reach the same production.
     It is correct and universal and costs four meter points, each copy being a production with decision points of its
     own, and it moved no read of `m`. It also cannot be a step of its own — the sweep merges a copy back into what it
     came from, a copy still identical being the same production, and it stops being identical only once the pop leads
     it. The fixpoint reaches the same sites for nothing.
   - `defer-pops` — landed. A `PopIndent` leading every way of a production moves to the end of that way's actions, each
     expression among them equal to the level it restores from becoming `Indent`. *Sound because the stack holds that
     level until the pop runs, so the same measurement is read one action later; held to it by every caller pushing the
     one level, and by the residue — the level masked out — holding no `Indent` the rewrite did not account for.* The
     pop crosses actions only, an alternative's calls running after all of them, so no callee is measured against
     anything new; an action that is itself a call or a scope around one stops the move. What it is for is the pop
     standing against the push that follows it, a scan no longer between them. Its reach is the sink's: one of the four
     sites where a loop re-reads `m`, the other three keeping their pop behind a frame. Where a production that does not
     need a parameter calls into ones that do, it clears it where the last of them returns: `ClearVar(param)`, put after
     that call by the same device the pop uses, and `x-clear-m` where the clear is the whole body. *A production that
     needs a parameter is inside the region it measures, so nothing between the outermost frame to need it and the
     innermost is touched.* Clearing is what restoring would be, there being no outer value to restore to — which is the
     asymmetry with the indentation, and why `n` wanted a stack where `m` and `f` want a clear. Twelve of them, eight
     for `m` and four for `f`. Reading a parameter nothing holds a value for is a fault, so what the clears assert is
     the corpus's to refuse: over 694 fixtures and 402 suite cases no read ever crosses one, and the refusal was proved
     live by putting the clears inside the region instead and watching it fire.
   - `prune-params` — landed. A parameter a production does not need goes, with the argument every call passed it: what
     a production needs is what reaches a read — its own gate and actions, and whatever it hands to a production that
     needs one — and a write counts, a binding a frame does not declare being dropped on return. *A least fixpoint, so a
     parameter a chain of frames only relayed dies through the chain at once.* It took `m` from 91 declarations to 76
     and `f` from 15 to 9, and it decides nothing: the meter is unmoved on the step's own output, and the four points it
     loses are the sweep merging twelve productions the dead arguments had been telling apart.
   - And after each of them, the standing question of the third law: which of the six points does it retire? The
     reordered header choices are the first owed an answer — `order-actions` and `factor-calls` between them should
     leave the two orderings comparing equal, at which point there is nothing to swap and nothing to declare committed,
     and both the reorder and the ledger entry go rather than being carried. A declaration the analysis catches up with
     is refused by the staleness net already; a declaration a transformation makes unnecessary must be removed in the
     same change, not left standing because it still parses.
1. *Carrying the elimination through, which is where the meter's mass actually is.* The grammar is proper at
   `eliminate-empties` but for seven productions it exempts — the root's copy under each resume policy, `l-recover`'s,
   and the one a `(recover)` names — each entered without a call, so holding no choice a call site could have taken.
   Four steps then hand nullability back, and the count is theirs: `lower-star` takes it from 7 to 46, `lift-choices` to
   149, `binarize` to 179, `alternative-shape` to 243, and the rest of the pipeline settles it at 244. Of those, 133
   offer a blind choice between a way that reads and one that does not — the debt, and the decision points that are a
   call to one of them against its zero-width way are the greedy optional. The other 111 match empty single-way, which
   is the shape the canonical form mints on purpose and the invariant below allows. It is not a shape awaiting a
   certificate; it is the elimination not having been carried through. The empty match is moved one node sideways into
   an inline choice, and the first step that gives a choice a production of its own hands it back. Five moves, in this
   order, each corpus-held:
   - *The distribution goes outward, into ways.* `Ref(P)` at a site does not become `P_consuming | residue`; the way
     holding it becomes two ways of the enclosing choice. The shortcut sits in three places and all three must go: the
     call site, the consuming copy's body, and `residue` itself, which builds an alternation of empty matches and is the
     one that keeps handing the shape back. Sequences take the product of their parts' ways, alternations the
     concatenation, and every wrapper — a `(token)`, a `(<<<)`, a `(commit)` — wraps each way it holds. Inside a
     lookahead or a difference a choice is a pattern rather than a decision and stays where it is, but the reference is
     still rewritten, or it keeps the production it names alive and nullable.
   - *The invariant is that no production offers both a way that reads and a way that does not.* Not "nothing matches
     empty": a single-way action bundle — a continuation carrying a `PopMessage`, a guard the canonical form gives its
     own frame — matches empty and decides nothing, and the canonical form mints those deliberately, so the stronger
     rule would forbid the target. What is forbidden is the blind choice between reading and not.
   - *Properness is enforced after every step from the elimination on*, since it is the tail's property and not the
     property of the step that reaches it. Measured against the four steps that break it today — `lower-star`,
     `lift-choices`, `binarize`, `alternative-shape` — the first three break it only because their input is improper,
     and with the distribution carried through they preserve it.
   - *`lower-star` is the one that breaks it by construction*, `_N ::= x _N | <empty>` being a production that decides
     between reading and not. It emits the one-or-more helper and leaves the empty at the site: `_N ::= x _N | x`, and
     `_N | <empty>` where the star stood, which the elimination then distributes like any other.
   - *The elimination must not hand back what the lowerings removed.* Spelling the consuming form of a `x*` as a `x+`
     puts a complex `Plus` back after `lower-plus` has taken them out and multiplies the complex `Star`s from 9 to 36;
     the consuming form has to be written in the vocabulary that stands where the step runs.
1. *Then the certificates.* What the elimination carried through does not dissolve is theirs — the count is not worth
   guessing at until it has been. A subsumption certificate — a way whose language contains a later way's, read through
   one level of inlining — is what retires the assurance ledger's two remaining entries rather than leaving them
   declared. The points that are neither greedy optional nor ledger want the breakdown the greedy optional has before
   anything is designed for them; that classification is cheap and comes first of the two, and it is what would put a
   derived number on both halves rather than the one nothing computed.
1. *And then the determinizer*, pointed by what the meter still flags, through the landings below.

**Determinize, what remains.** The goal is the grammar deterministic **as invoked from the root**, not every production
at every hypothetical entry — a production undecidable on its own is no conflict where every context a root parse
reaches it under decides it, one level of inlining in. So the meter counts root-reachable decision points: each a
production judged under one reachable context's follow, the contexts computed root-down as follow classes. It reads 422
undecided points across 168 productions — 168 also being the isolation count, printed beside it as the diagnostic it now
is — driven to none, at which point the meter becomes a gate. A known over-count is the greedy optional: a call then
nothing, against taking none, which is the shape the ε-elimination distributes to every site a nullable production was
called from — decided by order and the callee's sureness, not by character disjointness, and the certificate for it is
the next to land. They fall into the landings below, one at a time, each corpus-diffed. The speculations among them are
the deep end, and they are one piece of work rather than several: the five cases spend one vocabulary, formalized under
*The provisional mechanism*, and are produced by one determinizer rather than hand-cut, described under *The synthesis*.
The fold is the engine's calibration; the block-structure substrate — the line run and its measured column — lands first
among what remains, because every trailing run that can end at an indented dedent hands its last line to a parent: the
reference puts each exiting construct's end markers before the dedent line's spaces, and the parent then consumes those
spaces as its own indentation, so a site-local committed scan that ate them has taken tokens whose markers and owner it
cannot restore. The block scalar's empties ride the substrate as the engine's first speculation target; the document
prefix and the implicit key land after, spending the same shared scan.

1. The block-structure work — first, because every speculation that ends at an indented dedent hands its last line to a
   parent — decomposed into small provable moves rather than one surgery. The insight is a factoring: the ways a block
   line start can go — this level's next entry, a deeper construct's line, every exiting level's way out — all begin
   with the line's spaces, so the spaces are a common prefix to extract once, ahead of the decision, and the decisions
   behind it become character gates with column guards. The moves, each a language identity or carrying one
   mechanically-checked side condition, every intermediate grammar corpus-green:
   - *Indent refinement* — the enabling move, a pipeline step, applied everywhere its side condition proves. An exact
     count of spaces followed by a non-space is one maximal scan judged after the fact, in the grammar's own spelling —
     the shape `s-indent-le` already is: `s-indent(k)·X` becomes
     `OpenMatch·ConsumeSpan(space)·[Len(Match)==k, as the `Le` pair]·CloseMatch·X` wherever space is not in FIRST(X) and
     X cannot match empty — maximal munch must steal nothing. The measure is the scan's own `(match)` scope, so the
     rewrite is position-independent and reads only the production's nodes and the FIRST table; the `<n`/`≤n` variants
     already stand in this shape. Stream-faithful: accepting paths consume the same spaces under the same code, one
     token either way. What this buys: counted indent consumes of different `k` share no literal prefix, and refined
     they are the identical scan, the differing counts residual guards.
   - *Aggressive common-prefix extraction* — `split-conflicts` and `factor-prefixes` already extract identical prefixes;
     the refinement makes indentation identical, and the extraction is driven to leave no factorable prefix standing —
     where it stops, the stop is one of the named blockers below, never a shrug. Two admissions grow it: an identical
     maximal scan joins the prefix where every leftover's first set is pinned, cannot match empty, and excludes the
     scanned set — a shorter run then leaves a character no leftover admits, so the maximal run is the only one that
     proceeds and the factoring reorders nothing; and a `(match)` scope's opening joins where the minted leftover
     production declares the origin and is passed the caller's own, the `code` parameter's exact twin, so the closing
     half restores what the unfactored close restored. A leftover's leading `Lt`/`Le` assertions rise into its gate's
     guards, judged at the same position, where the certificates read them. Together these are the whole of the local
     factoring, and they cover the seam within any one production.
   - *Seam moves*, demand-driven at conflicts the meter flags, since applied blindly they only duplicate productions —
     and one generic transform embodies all three: `extend-returns` folds a declared site's call-then-continuation so
     the continuation's actions run inside the call's own family, appended to every return path through minted copies, a
     tail recursion folding to its own copy — reassociation, distribution, and the tail-fold in one walk, each
     application a corpus-held identity, so the seam is absorbed frame by frame rather than in one atomic flip. The
     first target is landed: the sequence loop's exit carries its own `end-sequence`, one frame nearer the parent's
     scan; the chain continues through the wrapper and entry frames until the parent's scan is local to the conflict.
   - *Held-prefix factoring* — the one move that is not an identity, and the exact spot the provisional mechanism
     enters. Factoring the scan across a zero-width emission — `(Emit·I·x | I·y)` — would commute the marker past the
     indent token in the stream, which is the dedent's marker order: exiting levels' end markers stand before the dedent
     line's indent. The stream-preserving completion is the hold: the scan's spaces are held in the line run, the mark
     re-taken at each line start (landed), and the factored-out emission becomes an injection before the held indent at
     resolution. Spent only where the meter demands — a held run is runtime buffering.
   - The hand-built rewrite set aside in `junk-surgery/` is these moves' calibration oracle for the sequence loop: what
     the composed identities must reproduce, held to the dedent fixtures already pinned.
1. The block scalar's empty lines, opening and trailing alike, riding the factored line starts: the run holds the breaks
   and the held indents across lines, each line start re-taking the mark, and resolves either into content or into the
   scalar's end injected ahead of it. The chomping split is monomorphize's already — `l-literal-content` and
   `l-folded-content` stand per `t`; what stays `t`-shared is the callee layer the fusion reaches through, `b-l-folded`
   at block, `l-empty`, the empties chains. Per chomping, re-read off the reference at landing: under strip the run
   opens before the chomped last break, `end-scalar` injected at `start`, no retype where the scalar ends and every
   break to `line-feed` where content follows; under clip the last content break is `line-feed` in both readings, so the
   run opens past it and strip's shape follows — the static mark the table below gives clip is expected to dissolve;
   under keep both readings spell every break `line-feed`, so what remains to decide may be the line runs alone. A
   dedent exit is the substrate's: each level injects its own end markers at the mark, and the parent owns the last
   line. The engine work riding along: rooting under callers that are not unique, the injection read off the divergence
   — a marker one path emits where the other holds tokens — and the non-converging walk emitted as the runtime loop the
   fold's hand-built empties loop already shapes. The opening empties weave in the auto-detected indent and the
   `(increase)` floor the leading empties set; the block fold fuse — `b-l-spaced`/`l-nb-spaced-lines`, the block
   `b-l-folded` sites — lands last on all of it.
1. The document-prefix speculation — the positional injection's first exercise. The comment loops between documents hold
   whites both readings claim: the spans agree, and what the decision settles is their codes and where the markers stand
   — `white` where a comment line owns the whites, `indent` where a block collection does, with `begin-document` and the
   node markers behind it standing ahead of them. So the line opens a provisional run and holds the whites; `#`, a break
   or the end of the input decides the comment, and anything else is a document, whose own shape the `:` decides or
   refuses. That is the run the simple-key speculation resolves, so the two are one landing rather than two in that
   order — bounded eager buffering for the key line, resolving key-vs-scalar at the `:` or the break that refuses it,
   inside the `(max) 1024` window the grammar already carries. Covers `l-document-prefix_consuming`, `l-trail-comments`,
   and the implicit-key sites.
1. The plain scalar's next line — whether a multi-line scalar continues at all, the same held-break read, landing with
   the key's since both resolve at a line's end.
1. Per-site separation fusions where a decision hides past optional separation and the paths do not reconverge — the
   properties' separate-then-tag, the flow key and value entries' separate-then-indicator — each read at its site, fused
   fold-style without a retype where the separation's codes agree either way. That agreement is read off the reference
   per site before the fusion is built, never assumed: the document prefix's whites looked like they agreed and did not,
   `white` under one reading and `indent` under the other, and a site whose codes disagree wants a retype and belongs
   with the speculations. No generic absorption exists: the continuations never open on the bare separation the exit's
   rival consumes.
1. The assurance ledger, after the surgery settles the minted names: declared order-commitments with their reasons — the
   fused fold scans, the document loops if the surgery leaves them standing, whatever order-only residue remains — a
   staleness net refusing an entry the grammar lost or the analysis has since proved, and a counted line the gate
   prints, like the vendored spec's declared deviations.
1. The determinizer pointed by detection rather than by name. Each landing above hands the engine a conflict its step
   names — the fold's `b-l-folded_c_flow-in`, the empties' sites next — which is scaffolding, not the end state: the
   engine's input is defined as the group the meter flags, so once the landings prove it case by case, the pipeline's
   determinize step walks every conflict the meter finds and fires on each, and the name lists go. What remains named is
   what remains declared — the assurance ledger's entries, with their reasons.
1. The validator made the gate, and the deferred trim-reuse pass.

**The provisional mechanism**, formalized — the one shape every speculation takes. A run is a contiguous stretch of
pending tokens: built, held, and handed back to no one until it resolves. Their spans are fixed the moment their
characters are consumed, the parser never rewinding input, so what a resolution settles is only the codes those tokens
carry and which decided markers stand among them. It never drops a held token and never grows one out of held
characters. So the readings a run decides between must agree token for token and span for span, and every difference
between them must be a zero-width marker — that is the mechanism's one obligation, and the cases below are where each
speculation discharges it.

**The positions.** A run has a `start`, where it opened, and may take a `mark` inside it; the two cut the held tokens
into the region before the mark and the region from the mark on. A run carries one mark at a time, and taking it again
moves it — the last taken wins, which is how a line scan marks each fresh line and the line that resolves the run is the
one whose mark stands. A mark is a parse position, recorded where its action stands, and not a property of any token.

**The actions.**

- `OpenProvisional` opens a run at the queue's current position. A second open inside an open run is a fault; runs never
  nest.
- `MarkProvisional` records the queue's current position as the run's mark, replacing the one it holds — the last taken
  wins. Outside a run it is a fault.
- `InjectBefore(codes, at)` inserts `codes` — a tuple of zero-width markers, in order — at `at`, the run's `start` or
  its `mark`. What it inserts is decided at once: not held, and reached by no later retype, which is what makes an
  injection at the mark unambiguous, landing past everything before the mark and ahead of everything after it. Injecting
  at `mark` where none was taken is a fault.
- `RetypeProvisional(rest, breaks, region)` rewrites the codes of the held tokens in `region` — `all`, `before_mark` or
  `after_mark` — by kind: a token whose text begins with a carriage return or a line feed takes `breaks`, one whose text
  begins with anything else takes `rest`, and a code named `None` leaves its kind alone. A marker or an error carries no
  text and keeps its code either way. Naming `before_mark` or `after_mark` where no mark was taken is a fault.
- `CommitProvisional` resolves the run and its mark together: the held tokens are decided and may be handed back.

Injections and retypes commute — a retype reaches only held tokens, an injection only adds decided ones — so a
resolution issues them in whatever order reads best at its site.

**Runs never nest**, and no case asks them to: where a held run meets a character that would open another, that
character has already resolved the outer one. The multi-line flow key is the proof — `[1,` and then a break cannot be a
key at all, a key being one line by the spec's own restriction, so the break that opens the fold's run is the same break
that resolves the prefix's.

**The balance net** grows to match: its walk carries `closed`, `open` and `marked` where it carried a third state for
the one injection a run was allowed, and faults on an open inside a run, a mark outside one, a retype or an injection
outside a run, a marked region or a mark injection where no mark was taken, and a commit with no run open. The
one-injection-to-a-run limit goes; the tuple and the two positions are what it had stood in for.

**The runtime** follows: `ys_queue_inject` takes the position and the tuple, and the interpreter's undo trail — which
journals a retype, an injection and where the run stood — journals each mark beside them, a re-take undone to the mark
it replaced, so backtracking rewinds through a marked run as it does through any other.

**The cases, part one — what each resolves to under the formalism.** Every row is read off the reference interpreter.

| Speculation                                    | What the run holds                                                 | What decides it                                                                                      | Injections                                                                                                       | Retype                                                                                     |
| ---------------------------------------------- | ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| The flow fold, `s-flow-folded` — landed        | the break, and the follower's indent and whites                    | a break is an empty line; anything else, the input's end included, is content                        | none                                                                                                             | content: `(None, line-fold, all)`; empty: none                                             |
| The document prefix and the implicit key       | the line's whites, and where a key may stand, the key's own tokens | `#`, a break or the input's end is the comment's; a `:` makes the line a key, a break refuses it one | a document: `(begin-document …, start)`, the node markers its shape needs behind it; a key: `(begin-pair, mark)` | a key or a block collection: `(indent, None, before_mark)`; otherwise none                 |
| The plain scalar's next line                   | the line's breaks                                                  | content at the follower continues the scalar; a marker, a dedent or the input's end ends it          | ends: `(end-scalar end-node, start)`                                                                             | continues on one break: `(None, line-fold, all)`; on more: `(None, line-feed, after_mark)` |
| The block scalar's opening empties             | the breaks before any content, and the indents between them        | content at the scalar's indentation; anything else leaves the scalar empty                           | empty: `(end-scalar, start)`                                                                                     | content: `(None, line-feed, all)`; empty: none                                             |
| The block scalar's trailing empties, `t=strip` | the trailing breaks                                                | the chomping its header fixed                                                                        | `(end-scalar, start)`                                                                                            | none                                                                                       |
| The same, `t=clip`                             | the trailing breaks                                                | the chomping its header fixed                                                                        | `(end-scalar, mark)`                                                                                             | `(None, line-feed, before_mark)`                                                           |
| The same, `t=keep`                             | the trailing breaks                                                | the chomping its header fixed                                                                        | none                                                                                                             | `(None, line-feed, all)`, the scalar's end emitted past the commit                         |

**The cases, part two — how each is reached from the grammar as it stands.**

1. *The flow fold* is landed, as `speculate-folds`; the site is fused by name already and only the two signatures move
   under it — the retype naming `all`, the injection unused.
1. *The document prefix and the implicit key* are one landing, the run being one. The comment loops fuse at their line
   start: open the run, consume the whites, mark past them. `#`, a break or the input's end continues the loop and
   commits, the whites staying the comment's; anything else leaves the loop for a minted document entry that emits none
   of what the injection supplies and does not consume the whites a second time — the fold's consumed-prefix discipline.
   The key rides that same run to the `:` that makes it one or the break that refuses it, inside the `(max) 1024` window
   `ns-s-implicit-yaml-key` and `c-s-implicit-json-key` already carry. Covers `l-document-prefix_consuming_1`,
   `l-trail-comments_1`, and the implicit-key sites.
1. *The plain scalar's next line* opens its run at the break that ends a content line, the follower's gate deciding it;
   the minted continuation does not emit the scalar's end where the injection supplied it.
1. *The block scalar's empties*, opening and trailing alike, want the loop productions split per chomping first — they
   are `t`-shared where the tail's codes are not — after which each branch's resolution is one row above.

**The synthesis — the provisional productions are derived, not hand-cut.** Each speculation above is the output of one
determinizer, not a bespoke rewrite. The input is the group the meter flags: the live alternatives rooted at an
overlapping-gate choice point, pruned to the minimal set no gate can separate — by definition where non-determinism
lives, since a single alternative decides nothing. The output is the provisional-mechanism equivalent. The method is
subset construction: walk the live alternatives in lockstep on the input, and read the divergence between them, which is
always one of four and each maps to one output —

- a character all paths consume over the same span but under different codes is held, and retyped at resolution to the
  surviving path's own code for it;
- a zero-width marker some paths emit and others do not, or emit at a position that falls before an already-held token,
  is deferred and injected at resolution — the run's start where it precedes every held token, the mark where it stands
  between them, in the surviving path's own order;
- a space one path consumes as more indentation where another has reached its level is the same character read as an
  indent comparison — space against the first non-space — and resolves not to a held token but to an `IndentEq`/
  `IndentLt` guard on the measured column, the indent consumed once and nothing held, since only whether more indent
  follows diverges, never its code;
- the character on which the paths' gates first differ is the discriminator, where the run commits to the one path that
  survives it.

The first two outputs are the provisional actions; the third is a guard. So it is one divergence analysis with two kinds
of output, not two mechanisms — a token's code or presence diverging holds and injects, a position against a level
diverging guards.

None of it is a hint. The conflict root, the held token, the retype code, the injected markers with their order and
side, and the mark itself are each read off the grammar's own alternatives. The mark and the injections are actions
placed at the one structural boundary the walk finds — where the shared prefix ends — and at runtime each records the
live queue position on its own, so nothing counts tokens or threads an index: the placement in the production is the
position. The walk either converges to a straight-line region or recurs without converging, which is the signal for a
runtime loop rather than a fault — the block scalar's opening empties the unbounded case, the fold's scan the bounded
one.

The block-structure surgery is not a second mechanism beside the engine — it is the engine's indentation output, the
third divergence form above, applied to the block-collection loops, where continue-vs-exit is a space against the first
non-space at the collection's level. Two things make it the hardest facet rather than a free one, and keep it a distinct
body of work under the one principle. A dedent can cross several nested loops on one character — the inner sequence
exits and the outer continues at the same `-` — so to stay committed the indent must be consumed once at the line start
and read by zero-width guards across all the nested loops, a scan *shared* between conflicts the engine otherwise
resolves one at a time, not the local fix a single-level fold needs. And the level itself is auto-detected: `n+m` is set
by the first entry, so the column the guard compares against is established by the run rather than fixed at synthesis.
The flow fold, the plain scalar's next line, and the block scalar's empties are pure output-deferral and need none of
this; the document prefix and the implicit key need the shared scan, so they land after it. The engine's output is held
to the nets already standing — `deterministic_productions` certifies each decision one-character-decidable, and the
hybrid corpus proves the stream identical — so a wrong synthesis fails loud, never silent, and needs no proof of the
engine beyond the certificate on its result.

It is calibrated against the fold, whose hand-built `speculate-folds` is the known-correct oracle: the engine re-derives
its held break and its `line-fold` retype from the raw conflict, hint-free. It is fired first at the block scalar's
empties — the hardest case it resolves alone: an unbounded run, the clip/keep/strip retype split, the injected
`end-scalar`. One static mark to a run was assumed to suffice, and the trailing empties' dedent exit is the site that
proved otherwise — end markers wanted at the last line's boundary, a spot only hindsight names — which is what the
re-taken mark is for: the line scan marks each fresh line, the last taken wins, and one position serves every line-end
boundary a run can resolve at. The corpus is what settles coverage.

**The validator** is the target invariant and equals "done": every production is a terminal char-set or an ordered list
of canonical alternatives; no `Star`/`Plus`/`Opt`/`Rep`/`Look`/`NegLook`/`Diff`/`Token`/`Wrap`/`Case` survives; every
alternative is gate-led with ≤2 calls and no post-P2 action; and every decision point is commit-safe.

**The verification net** is the **reference interpreter from Phase 01**, now taught the canonical/action nodes this
pipeline introduces and given a second mode. It is a slow, backtracking executor with undo — performance being a
non-issue — and it runs in two modes off one flag:

- **Backtracking mode** is the baseline. Diff its token stream before and after *every* step against the yaml-test
  suite; a step that changes any output is rejected. This is the net for all 30 steps, and the interpreter doubles as an
  early differential oracle against YamlReference.
- **Committed mode** respects the gates and never backtracks. After the determinize steps, both modes must agree on the
  corpus; a divergence means a gate is not commit-safe — the one thing the structural invariants and the backtracking
  interpreter cannot catch on their own.

**Exit** — a canonical grammar the validator passes, on which the interpreter agrees in both modes across the corpus,
every speculation resolving its run correctly with the deferral exercised deliberately, so that emitting the C state
machine (Phase 04) is mechanical rather than clever.

### Phase 04 — C codegen · Emit the C library

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
   sharing the emitted `Code` enum — so the debug view ships *with* the library and carries no YamlReference dependency.
   Validate byte-for-byte against the YamlReference renderer.
1. Build as `cdylib`-style `.so` across Linux/macOS (and Windows once the toolchain is clean).

**Exit** — a self-contained C `.so`, plus the bundled `yaml2html` tool, passing suite + differential + fuzz.

### Phase 05 — ABI layer · Drop-in for libyamlstar

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

### Phase 06 — Harden · Fuzz, tune, and reach libyaml-class speed

*Risk: Medium · ~2–4 mo.* Correct-but-slow is not the goal. Close the algorithmic gaps naive codegen leaves and prove
robustness under hostile input.

1. Continuous structure-aware + byte-level fuzzing (ASan/UBSan) targeting the semantic long tail.
1. Profile; eliminate any residual super-linear behaviour from over-broad lookahead.
1. Benchmark against libyaml on representative corpora; tune hot states and buffering.
1. Build the release library with link-time optimization, so the one-token-at-a-time dispatch inlines across the
   translation units it is split over.
1. Optionally: extend to an emitter (dump), or keep libyaml's emitter alongside for a complete round-trip library.
1. Cut prebuilt binaries per platform so adoption isn't gated on a local native build.

**Exit** — O(n) confirmed, libyaml-competitive, fuzz-clean, packaged.

## §5 — Risk register

| Risk                                                                 | Phase   | Severity | Mitigation                                                                                                                                                                                                                                         |
| -------------------------------------------------------------------- | ------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Malicious input triggers memory-unsafety or resource-exhaustion DoS  | all     | ▪▪▪▪     | Hardening flags on the release build; ASan/UBSan on every run; structure-aware fuzzing from day one; bounded allocation plus a configurable parse-depth cap; billion-laughs / recursive-alias guards; continuous security audit, not a final pass. |
| A step in the normalization pipeline silently changes the language   | 03      | ▪▪▪▪     | Reference IR interpreter diffs the token stream before and after every step; the committed mode catches an unsafe gate the backtracking mode cannot; dual differential oracles against YamlReference and YAMLStar; log assurance gaps.             |
| Semantic rules beyond the BNF encoded wrongly / incompletely         | 02      | ▪▪▪▪     | The grammar is the semantic spec; `check_vendor_spec` tags each rule grammar-vs-deviation; fixtures enforce; fuzz the corners the YAML Test Suite misses.                                                                                          |
| The first working slice is a big leap from IR to emitting tokens     | 01      | ▪▪▪      | Decompose into many small, individually-verified sub-steps; grow the production subset one at a time, staying green; hand-checked expected outputs before the YamlReference and YAMLStar oracles exist.                                            |
| Naive codegen is correct but super-linear                            | 03 / 06 | ▪▪▪      | Commit-safety discharged per decision point in phase 03; profiling and hot-state tuning in phase 06.                                                                                                                                               |
| A pipeline step is subtly non-semantics-preserving and slips the net | 03      | ▪▪▪      | Keep every step small enough to prove by eye; assert its structural post-condition; the interpreter corpus-diff is the behavioural backstop.                                                                                                       |
| Arena/backtracking scratch leaks or corrupts                         | 04      | ▪▪       | Input-bounded lifetimes; ASan/UBSan in CI; discard provisional state through the arena only.                                                                                                                                                       |
| Incumbency: 1.1 quirks are load-bearing in real configs              | —       | ▪▪       | Out of scope to "fix" silently; position as a conformance upgrade, document behavioural deltas from libyaml/1.1.                                                                                                                                   |

## §6 — Future work

Wanted, but not planned, and not on the way to anything else:

- **The column a byte-order mark leaves behind** — a BOM advances the column and does not end the start of a line, so
  after one `column == 1` while `is_sol` holds. The two agree on every other character. Which is right is a question
  about where an error points: a mark carries the column an error message locates itself by, and a BOM is not something
  a reader counts. Staying at column zero looks correct and nothing in the grammar depends on the answer — indentation
  reads the length of the token it builds, which counts spaces either way — so this is a decision to take deliberately
  rather than a bug to fix in passing.

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
    which is what makes libyeast's detection comparable to YamlReference's `Detected` output token for token.

  This is why `YS_CODE_DETECTED` is in the vocabulary already: the finest level is where libyeast emits it, and the wire
  round-trips it in the meantime. A coarser level is cheaper and is all a caller loading a document needs; a finer one
  is what the differential oracle and a debugger want.

- **An event-projecting token source** — a `ys_token_source` that wraps another and is one itself, handing back only the
  event-level tokens: the stream, document, mapping, sequence, scalar and alias markers, a scalar's value already folded
  (a `line-fold` a space, a `line-feed` a newline, an escape resolved) with its anchor and tag attached, everything else
  — the node and pair brackets, indicators, indentation, whitespace, breaks — dropped. It is the C twin of the Python
  fold the YAML Test Suite is checked through: the event stream is a subset of yeast, so the projection is a filter over
  the markers plus the mechanical value fold the codes already settle. A caller wanting YAML events rather than tokens
  reads them straight, without composing a node graph — and because it wraps a source and is a source, it drops in
  wherever tokens already flow.

- **Arena allocators** — revisit the `ys_allocator` API against arena and pool allocators, which free everything at once
  rather than buffer by buffer: whether a no-op `deallocate` is enough as it stands or the shape wants a variant, and
  whether a source's allocations can be arranged so a caller drops the whole parse in a single free. The `close` hook is
  already the seam such an allocator would be torn down through.

- **libc version portability** — deal with the libc-version issues a shared library faces: which symbol versions the
  built `.so` pulls in and their minimums, so a binary built against a newer toolchain still loads on an older target.
  The ABI-compat goal — a libyamlstar drop-in — depends on this not being quietly broken by a libc symbol-version bump.

- **Optimization and benchmarking of the C implementation**, past the tuning that reaches libyaml-class speed. A
  standing benchmark suite over representative corpora — deep nesting, long scalars, wide collections, flow-heavy and
  block-heavy documents — run per build so a regression is caught where it is introduced rather than noticed later.
  Beside libyaml, the interesting comparison is against **JSON parsers**: JSON is a subset of YAML and its parsers are
  the fastest structured-text readers there are, so the ratio between them is the honest measure of what YAML's
  indentation, folding and deferral actually cost. It also says which of those costs are the grammar's and which are the
  generated machine's — a JSON-shaped document read by libyeast exercises almost none of the speculation, so the gap
  that remains on one is the automaton's own overhead.

- Binaries as well as library - yaml2yeast (resume policy in ARGV), yeast2yaml (filtering policy in ARGV), yeast2html
  (based on YamlReference), yaml2event, yeast2event...

## §7 — Shape of the whole

For one very strong engineer who deeply knows both YAML and parser generation, this is a **many-months to
low-single-digit-years** project. The difficulty is lumpy, not uniform:

- **The easy ~70%** — the structural steps of the normalization pipeline, C codegen, the ABI layer. Well-trodden
  compiler work; high effort, low research risk.
- **The hard ~20%** — the determinize steps of the pipeline (phase 03): reducing each decision to a commit-safe one-char
  gate, faithful-by-construction. This is the part that determines whether the result is worth more than libyaml, and it
  touches formal methods.
- **The judgment-heavy long tail** — the semantic layer beyond the BNF, settled into the grammar and its gates (phase
  02), the source of the subtle bugs no single test happens to catch.

The reason this doesn't already exist isn't that any one piece is impossible. It's that the *valuable* version requires
the determinization to be faithful-by-construction — a real proof effort on top of a real compiler — and the set of
people who can do both *and* care enough about YAML specifically is tiny. The generator, not the parsers, is the
mountain. This plan is a route up it.

## §8 — YamlStar upstream notes

A running list of what libyeast's work surfaces that belongs upstream — a fix or an addition to YAMLStar or the test
suites it validates — each with where libyeast found it:

- **A test that appends a line break the input lacks.** YAML Test Suite `JEF9/02` — an empty kept block scalar whose
  input ends in no line break. The spec folds it to the empty scalar: end-of-input counts as a break only in
  `b-chomped-last`, which an all-empty scalar never reaches, and `l-keep-empty`'s `l-empty` needs a real `b-break`. The
  suite's expected single line break comes from YAMLStar appending a trailing break to the input before parsing — so the
  case tests the appended input, not the one on disk. libyeast carries it as its one declared divergence
  (`check_star.DIVERGENCES`); the no-trailing-break form is an interesting edge case worth adding to the suite in its
  own right, with the expectation the spec's reading gives.
