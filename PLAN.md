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

## §3 — What validation still owes

The oracles and what each judges are `DESIGN.md`'s. Three things are owed on top of them:

- **The refinement obligation.** Every commit the determinization makes wants an argument that it preserves the language
  of the productions — commit here, *and here is why it is still the same language*. This is what makes the result worth
  more than a hand-written machine, and it is the part that touches formal-methods territory.
- **Structure-aware fuzzing over the grammar**, to hunt the long tail the suite does not reach, especially in the
  semantic rules the BNF does not capture. Nothing fuzzes today.
- **A written threat model** for the YAML-specific denial-of-service classes: billion-laughs and recursive alias
  expansion, pathological nesting depth, unbounded allocation, quadratic blow-up. The pushdown shape removes C-stack
  overflow by construction, and `ys_options::max_bytes` is what bounds the input, the tokens held with it and the depth
  the production stack reaches — one cap over all three rather than a depth cap of its own. What the threat model owes
  is the argument that one cap is the right shape, and the cases that show it.

## §4 — Implementation phases

Phases are ordered by dependency. **Phase 02 is a gate**: the grammar and its fixtures are made spec-complete and
enforcing before the transformation touches them, so Phase 03 never has to go back to the grammar. The deepest research
risk then lives in **phase 03** (the normalization pipeline, where determinization happens); the semantic decisions that
feed it are settled in phase 02. The rest — codegen and the ABI layer — is well-trodden compiler work.

Phases 01 and 02 — the reference interpreter, and the grammar and fixtures made spec-complete — are done, and what they
built is DESIGN.md's to describe. Two things they leave to be scoped when something needs them: the interpreter's
committed mode, wanted where the grammar it judges is one the pipeline has transformed; and the yeast→HTML debug view,
bootstrapped on YamlReference's `yaml2html`, with the differential fuzz corpus CI would run beside the fold.

### Phase 03 — Normalize · the grammar-to-canonical pipeline, every decision committed

*Risk: High — the prize.* This is where the backtracking grammar becomes a committed one, done not as one leap but as a
**series of small, individually-provable, semantics-preserving transformations** carrying the IR to a canonical form a
state machine falls out of. Every step preserves the interpreter's token stream over the whole corpus, and every step
establishes an invariant the steps after it may lean on. What each phase owns and which steps serve it is declared in
`normalize.STEPS`; what the pipeline has reached is DESIGN.md's. What is left is here.

**The order of the work, and each item is a gate on the next.**

1. **Give every way the space of states it may be entered in.** Every guard the grammar carries is a question about one
   axis of a small multidimensional space, and what a parse stands in when it decides is one point of it. A
   **`SubSpace`** is a set of such points, and two of them answer for every way and every production:

   - **The accepted space** — the states the way itself takes, read from below: its own gate and guards, intersected
     with what its actions consume and what its callees accept, as a least fixpoint from the empty space. The walk over
     a way's parts carries `alive`, the states at which it can still stand having taken nothing: a part that must take
     contributes `alive ∩ its own space` and ends the walk, one that may take nothing contributes the same and leaves
     `alive` standing, and a call contributes `alive ∩ what the callee accepts` and narrows `alive` to the states that
     callee can pass taking nothing.
   - **The gated space** — the states a parse may enter it in, read from above and **per call site**: the caller's own
     gated space narrowed by the gate standing where the call is, as a greatest fixpoint from the whole space.

   **`gated ⊆ accepted`, per way, and that is the whole law.** Where it breaks, something enters a way in a state
   nothing behind it takes — the backtracking the shape exists to remove — and the fault is named at the call site that
   admits the state rather than at the way that fails on it. Where the gated space is empty, nothing enters the way at
   all. That is a different question from `every-option-is-reachable`, which asks whether a way in front can be refused,
   and neither answers the other.

   **The axes, read off the guards the grammar already carries.**

   | Axis                   | Its values                                                                             |
   | ---------------------- | -------------------------------------------------------------------------------------- |
   | the character in front | one of 57 literals, one of 8 classes, or the end of the stream                         |
   | the character behind   | `ns-char` or not — the one set all 8 look-behinds ask about                            |
   | the line start         | standing at one, or not                                                                |
   | the indentation        | `n == 0` or not, and seven further comparisons relating `n` to `len`, `column` and `m` |
   | the parse's own state  | `EndMustConsume` and `DidMatchFullSpan`, a bit each                                    |

   Counted over the grammar's ways: 717 carry no gate at all and 982 a gate asking the character in front alone; then 94
   ask the indentation with it, 94 the parse's state, 52 the indentation alone, 18 the line start, 8 the character
   behind with it, 6 the parse's state with it, and 4 the line start with it — 1975 in all. So a single gate is one box,
   but **82 productions offer ways constraining different axes**, and one box cannot hold a production's space. A
   `SubSpace` is a *set* of boxes.

   **Every axis is finite, so a `SubSpace` is exact.** The character axis is not a codepoint range: `chars.Model.key`
   gives every codepoint one word, and the tables generated from it name 57 literals and 8 classes over 20 sets — which
   is the alphabet the emitted parser itself tests, one bit against the key the decoder already made. So the analysis
   speaks the machine's own alphabet: every distinction it can draw the parser can test, and there is none it can draw
   that the parser cannot. Nothing is approximated in either direction, which is what makes the law a law rather than a
   heuristic with a chosen failure mode.

   **It is computed, not carried.** Nothing in the IR holds a `SubSpace`. A stored one is stale the moment a step
   splices a way, and a derived field that takes part in identity stops the sweep merging productions that behave alike.
   It is computed over the grammar a stage has produced, once the sweep has settled it, and computed again for the next
   — a value the grammar decides, not a claim a step leaves behind. Nor is it a `CharSet`: a `CharSet` is something the
   machine runs, and splicing a description in as a match is the mistake the separate type prevents.

   **What the walk asks rather than decides.** Whether a way can take nothing is `_is_nullable`'s answer and is read
   from there, the space needing it to know when to carry on past a part. The end of the stream is a value on the
   character axis, the base grammar naming `<end-of-stream>`, not a flag beside it.

   **The corpus is the independent witness.** An invariant that recomputes what it checks proves nothing. The
   interpreter knows the state each way was actually entered in, so it asserts membership in that way's gated space at
   every entry, which is what catches a space computed too narrow; too wide is what the law catches.

   **The order of the work.** The `SubSpace` type and its operations first, then the accepted space from below, then the
   gated space from above, then the law over the two with the interpreter's assertion beside it. Each stands on its own
   and is gated before the next.

1. **Settle `every-conditional-way-is-gated`.** It stands at 58 — every one a way something decides to enter that
   carries no question the machine can ask before entering it, which is the backtracking the whole shape exists to
   remove. The 58 are two families. **39** tail-call a production offering one way that carries no gate: there is no
   question in the callee to hoist, so what decides has to come from one call deeper, or the callee's way has to be
   written into the caller. **19** call a production offering several ways of which some are gated and at least one is
   not: `expand-called-ways` declines these, since copying the ways out where the call stood would leave a way still
   ungated and buy nothing. Two shapes aimed at the second family are written and kept in `junk-lowering-gates.py`,
   removed from the pipeline as dead rather than lost — one puts the caller's continuation inside the callee, the other
   sends the tail down into the callee instead of copying the ways out.

   With the accepted space computed, a gate is minted from what a way accepts rather than hoisted from a guard that
   happened to be reachable — which is what the pipeline cannot do today. Every gating step it has *relocates* a guard:
   `expand-called-ways` copies ways out, `hoist-guards-to-gates` and `hoist-guards-to-callers` move guards up,
   `merge-gate-peeks` merges them, and `split-consumes-into-gates` mints one from a set standing in the way itself. None
   computes what a call can begin with, which is why a way whose deciding guards sit inside two different callees has
   nothing to hoist and stays ungated.

1. **Settle `every-choice-is-deterministic`.** With the ways gated, what remains is the choices no character tells
   apart. `every-called-alternative-is-unconditional` belongs here rather than to the gating phase: taking a callee's
   gate out to the ways that call it carries a guard over what the caller performs before the call, which nothing can do
   until the gate and the call are made adjacent. The design for the rest — the block-structure factoring, the
   speculations and the one vocabulary they spend — is *Determinize, what remains* and *The provisional mechanism*
   below. **At none, the grammar is deterministic**, and that is what Phase 04 needs.

1. **Adapt the C parser to it** (Phase 04), and what follows from there.

One invariant is owed and belongs to no phase: `no-conditional-production-matches-empty` — that no production something
decides to enter matches empty. No step in the pipeline carries it, and no phase above claims it. **215 ways something
decides to enter can match empty**, in 196 productions, and 63 of those carry a gate already — a gate decides and the
way then takes nothing, which is not itself a fault. What it costs is tightness: a way that may take none makes its
caller's accepted space the union of its own and its continuation's, so the spaces widen wherever one stands.

A phase re-implements what it needs rather than inheriting it: a step is kept only where it earns its place, and the
ones between the phases' goals are derived when their phase arrives.

**The two questions, in order.** A machine that never backtracks needs each way of a choice to carry a gate it can ask
before entering it, and then it needs the gates to be exclusive. They are separate problems: the first is item 2 above,
the second is item 3, and what follows here is the design for the second.

**Where the gate lift belongs.** `every-called-alternative-is-unconditional` — a way calling, where its own gate stood,
a production that offers one way and asks something of its own — is item 2's rather than the gating phase's. The caller
was already admitted on that character, so the callee's question can only refuse where the caller was let through, and
it belongs at the caller. It is asked only where the callee begins where the gate stood: the `first` call, and a tail
call the way performs nothing before. A way carrying on past a call of its own resumes wherever that call left off, and
a tail call past a character the way took begins past it — neither is a position any gate above spoke of.

Nothing can be lifted yet, and the reason is measured rather than argued. A guard reaching the caller's gate has to pass
what the caller performs before the call, and on the path the guard refuses those actions never happened — a `PushCode`
that did not cut the token run, a `PopMessage` that did not leave the committed region. Every call site performs
something, so the lift moves nothing until the gate and the call are made adjacent, which wants the caller's actions
given a state of their own.

`every-choice-is-deterministic` is the exclusivity question and splits in two: the choices offering a way in front of a
catch-all that nothing enters it on, and the choices whose ways admit the same input, order being what tells them apart.
The second is what factoring and speculation are for. A gate is read as what it admits on each axis independent of the
others — the character in front of the parse, the character behind it, whether the parse stands at a line start, and the
indentation it stands under — so a choice split on the indentation is told apart where one split on the character is.

**A marker net that follows the pipeline.** The scope pairs are held to by the parse: each half carries the pair it
belongs to, and a close whose pair does not meet the open standing on the stack is refused. That is the right shape for
them and the wrong one for the markers, since a marker pair crosses productions by design — `b-chomped-last` emits
`end-scalar` for a `begin-scalar` opened elsewhere — so holding one to a single way would report dozens of faults that
are not. `check_markers` proves the `begin`/`end` balance of the grammar as authored and does not follow the pipeline,
and minting the continuations is what first puts a `begin` in one production and its `end` in another. So the net that
follows the pipeline is owed, and nothing supplies it.

**What the shape phases leave owed** is the lookarounds' only remaining share. Almost every one is a question about a
single character, held to a `CharSet` by `every-peek-is-a-character-set`, and a peek of a set *is* the zero-width guard
the canonical gate carries — one bit tested against the key the decoder already made, and for the look-behinds one
register holding the last character, which is what `is_sol` already is. The exclusions are what is left: they ask for
`c-forbidden`, and some for `s-indent-le-line` as well — "a line at this indentation with content". Both are bounded in
the machine's own steps, the run of spaces being one scan, which is what `every-exclusion-is-bounded` holds them to. So
what is owed is that a condition on a line start is not a question about what follows one, and it lands where the
block-structure work makes a line start a decision the grammar spells.

The three words — gate, peek, guard — the canonical form the pipeline carries the grammar to, what the parse may hold,
and the rules every transformation is held to are all `DESIGN.md`'s, being what the generator is rather than what it is
to do. The emitted parser's own action set is the IR's, node for node.

**What item 2 is made of**, still small steps: fold residual one-token lookahead into gates; left-factor shared prefixes
into a common gate and a branch, repeated until each decision is one-character-decidable; and insert provisional
speculation wherever no character decides — the folding decision, the document prefix and the simple-key line, the plain
scalar's next line, and the block scalar's empty lines, each spending the actions *The provisional mechanism* below
formalizes. Two indentation gotchas the mechanical steps miss are handled by hand: a zero-indent block sequence nested
directly in a mapping, and flow context, which suspends indentation entirely. Commit-safety is discharged per decision
point, and any residual logged as an assurance gap. Last, every terminal is asserted a pure char-set.

**What is still owed, in order.**

1. *The call written out — last of everything, after the meter reads none.* `first` and `second` are a call and where to
   carry on, which the machine performs — the last thing a production does that the grammar does not spell.
   `PushContinuation(second)` and a jump to `first` say it, and a `Pop` and a jump say the way home. Nothing is left
   that pushes or pops without an action naming it, and an inlining becomes what it should be: deleting a push and a
   jump, with no value riding either. The C parser is a state machine, its globals, one stack and the pending tokens,
   and nothing else; while a call is a field the grammar does not spell, codegen would be the thing deciding where a
   push goes, which is the pipeline's job and not its consumer's. So it is required, and it is required last.

   - *Why last, measured rather than assumed.* It takes the continuation out of a typed field and puts it in the action
     list. `second` is read in 50 places across 23 functions, `_alternative_first`, `_follow_classes` and `_is_sure_way`
     among them — the begin sets and the certificates, which is the whole determinize phase. Every one of those reads
     gets worse. Two standing steps also gain a side condition they do not have: `factor-prefixes` factors the longest
     identical run of actions and could take a push away from the jump it belongs to, and every splice moves action
     runs, where moving a push moves where the continuation is pushed. Written out first, the phase pays that on every
     step; written out last, when no step reshapes a way any more, it costs nothing and gives up nothing.
   - *What it would guard is guarded without it.* That no way carries on over a call taking an indentation off is read
     off the grammar, so it stands while a call is still a field. The machine's kind assertions are the stronger answer
     and arrive with the machine; until then nothing rests on a probe.

1. *The other direction of the gate, once the spaces stand.* Item 1's law is that a gate never admits a state the way
   behind it refuses. The reverse — that a gate never refuses a state a way behind it would have taken — is a lost parse
   rather than a backtrack, and the spaces answer it the same way, a way's gated space against what its own parts
   accept. It is a count and not yet a law: while a parse may fail and be handed back, a production refusing what it
   could have taken is a way its caller declines to offer, and only a committed machine makes that a fault. It runs
   beside the law, on the same two computations.

1. *Splitting `monomorphize`, noted so it is not rediscovered.* It specializes `c`, `t` and `r` in one pass over their
   combinations, where three passes of the one generic operation would give the same grammar with three
   separately-diffed steps. The copies are made per combination, so the split wants care it has not earned yet.

1. *The new steps, earliest and simplest first*, none of them written: the pipeline as it stands reaches none of these
   shapes, so each is derived when item 2 arrives rather than carried over. They exist to put the pieces in one place,
   nothing being orderable or comparable while it sits in different productions.

   - *Inlining under a gate* gives a call only the ways its caller's gate can reach, splicing the survivor's actions in
     where one bare way is left. A callee whose peek the caller's gate already implies wants the side condition "the
     callee's peek contains the caller's" rather than "the callee is ungated" — `c-chomping-indicator_t_keep` is one,
     gated on the `'+'` its caller is gated on, and widening it puts the chomping consume into both header lists.
   - Even then the block header does not factor, and the reason is worth keeping: the ordering side condition below
     needs a non-break consume *earlier in the same list*, and the scalar's own `|`/`>` is consumed two calls up in
     `c-l+folded`. Either that call is inlined too, or the condition is met another way.
   - *Ordering the actions* — within one action list every action moves as early as it may, leaving a canonical order
     the later steps compare with `==`. Four side conditions, each read off the list: a scope action never crosses its
     partner; an emitter never crosses a consume or another emitter, the stream's order being the output; a
     position-reading action crosses a consume only where that consume's set excludes line breaks and an earlier consume
     in the same list does too, the at-line-start bit being clear on both sides; and a value-reading action never
     crosses what writes what it reads. That third condition is the whole of the block header's difficulty: the
     auto-detected indent reads the position through one bit, the two orderings run it a character apart, and the bit is
     already clear — because the scalar's own indicator was consumed two calls up, which is why the fact has to be
     brought into the list before the rule can see it.
   - *Factoring a shared leading call* into the prefix. *Holds where the call is identical in name and arguments across
     every way and their gates are equal* — the shape the steps above create.
   - *Subsuming a way* whose later twin carries the same actions and calls but for trailing zero-width guards: the
     narrower way dies, the streams identical by construction.
   - *Merging two ways of one choice* that are equal once the actions are ordered. *Plain equality.* The
     within-production twin of the sweep's behavioural merge, which reaches whole productions only.
   - *Dropping a dead way* whose gate is disjoint from every character its production can be entered on. *Reads the
     root-down entry sets the meter computes.*
   - *Sinking a pop.* A pop holder is an ungated way whose actions are the pop and only the pop; where every reference
     to what one calls is such a way, the pop leads that production's own ways instead and the holders stop doing it.
     *There is no other way in for the pop to be wrong for, however many holders say it.* Run to a fixpoint, since
     sinking makes holders. A continuation whose arguments read the indentation keeps its holders, those being read
     before the pop rather than after, as does one a parse enters by name, and a recovery, which a cut reaches with the
     stack as it stood before the call. Nor does a way that calls *and* carries on hand its pop down: where to carry on
     goes on the stack ahead of where the pop would land, so the pop would meet that rather than the indentation it
     comes off.
   - *Inlining a call whose production is actions alone.* It goes nowhere, so being a call buys nothing and costs a
     push. It matters where the caller carries on somewhere: the call is made under the continuation the way pushes
     ahead of it, so an indentation coming off there would be taken back from under that push rather than from under the
     one that set it. Spliced, the actions run before the way pushes anything.
   - *Hoisting a push out of a production every way of which begins with it*, the callers making it instead, last among
     their actions — and then a pop and a push of one level standing next to each other are nothing and both go. *A gate
     consumes nothing and is refused where it reads the indentation, and a gate that then fails leaves a push the
     failing path gives back.* The push comes off a **copy**: what a caller wants is a production that no longer makes
     it and what a parse entering by name wants is one that still does. What this buys is the block collections pushing
     once before their loop and popping once after it, where they push and pop per entry.
   - *Stripping the level off a pop*, once the last step to read it has run. Left standing it is a read of what it names
     at every pop — the auto-detected indent among them — which keeps a value live where the parse has no use for it.
   - *Deferring a pop* that leads every way of a production to the end of that way's actions, each expression among them
     equal to the level it restores from becoming `Indent`. *Sound because the stack holds that level until the pop
     runs, so the same measurement is read one action later; held to it by the level the pop itself carries.* The pop
     crosses actions only, an alternative's calls running after all of them, so no callee is measured against anything
     new; an action that is itself a call or a scope around one stops the move. What it is for is the pop standing
     against the push that follows it, a scan no longer between them.
   - A later step may merge two consecutive clears, or drop one behind another, so that a clear never stands in the way
     of a factoring that would otherwise see a common prefix.
   - *Pruning a parameter a production does not need*, with the argument every call passed it: what a production needs
     is what reaches a read — its own gate and actions, and whatever it hands to a production that needs one — and a
     write counts, a binding a production does not declare being the call's own. *A least fixpoint, so a parameter a
     chain of productions only relayed dies through the chain at once.*
   - And after each of them, the standing question of the fourth rule: which named site does it retire? Ordering the
     actions and factoring the shared call should between them leave two orderings comparing equal, at which point there
     is nothing to swap and nothing to declare committed. A declaration a transformation makes unnecessary is removed in
     the same change, not left standing because it still parses.

1. *The empties, which no step of the pipeline carries.* `no-conditional-production-matches-empty` — that no production
   something decides to enter matches empty — is what this is for. A production a parse enters by name is exempt: the
   root's copy under each resume policy, `l-recover`'s, and the one a `(recover)` names, each entered without a call, so
   holding no choice a call site could have taken. What the count is of, when it is written, is the productions offering
   a blind choice between a way that reads and one that does not; a production matching empty with a single way or with
   every way is the shape the canonical form mints on purpose and the invariant below allows. The empty match is moved
   one node sideways into an inline choice, and the first step that gives a choice a production of its own hands it
   back.

   **The one operation.** An ε does not travel — it *dies* where something beside it reads. `Seq(a, (X|ε), b)` is
   `Alt(Seq(a,X,b), Seq(a,b))`, and where `a` or `b` reads, both ways read and the ε is gone. So "move every empty to
   the root" is really "push each ε outward one node at a time; it evaporates the moment it meets a reader, and what
   survives to a body's top belongs to that production, which hands it to its callers". Three steps, each dumb, each
   with an invariant:

   1. **`explicit-empties` — every emptiness is an `AltTree` holding a zero-width way.** `x?` becomes `(x | ε)`, `x*`
      becomes `(x+ | ε)`, an all-zero-width sequence already is one. *Invariant: no node matches empty except an
      `AltTree` with a zero-width way.*
   1. **`distribute-empties` — push that `AltTree` outward, one node at a time.** Through a sequence,
      `SeqTree(…, AltTree(X, z), …)` becomes `AltTree(SeqTree(…, X, …), SeqTree(…, z, …))`; through an alternation,
      flatten; through a wrapper — a `(token)`, a `(<<<)`, a `(commit)` — wrap each way, so each keeps a whole pair.
      **Not** through a repetition: `(x|z)*` is not `x*|z*`, and a repetition already means "as many as there are,
      including none", so it absorbs. **Not** into a lookahead or a difference, where a choice is a pattern and not a
      decision. *Invariant: an `AltTree` with a zero-width way stands only as a production's own body.* Absorption wants
      no step of its own — it is what this rule does when it meets a reader.
   1. **`lift-empties` — a non-root body's zero-width way goes to its call sites.** Drop it from the body and write
      `(RefCall(P) | residue)` at every reference. That makes a fresh inline `AltTree`, so the step above runs again.
      *Invariant, and the goal: no production a parse does not enter by name offers a way that reads and a way that does
      not.*

   Two and three iterate to a fixpoint. It **terminates** because each round moves an ε strictly up the call graph and a
   cycle is cut — an empty match reachable only through itself is an infinite parse, not a way — and it **converges**
   rather than treadmilling because two absorbs before three re-creates.

   **An ε moves up only where the call leads its way, and that is the wall.** `A ::= X C` with `X ::= D | ε` distributes
   to `A ::= X C | C`: nothing stood before `X`, so nothing is run twice. `A ::= F X` does not. Taking ε out gives
   `A ::= F X | F`, and where the first way's `D` fails the second runs **`F` a second time** — a different parse where
   `F` has more than one, a second push where it pushes. Nor can it be written any other way: the choice between `D` and
   nothing is made where `F` returns, the only thing running there is what `second` names, and a production minted to
   hold it *is* `X`. Measured, the wall is most of what is left: of the blind choices the step cannot take, **65 are
   this shape**, against 14 whose empty way calls something and 6 whose empty way carries a gate.

   **So pull the continuation down instead of pushing the ε up.** `X ::= A Y` with `A ::= B C` and `C ::= D | ε` becomes
   `X ::= B Z` with `Z ::= D Y | Y` — the trailing `Y` folded into the callee's family, where it stands beside the ε and
   both ways read. The move is already in the pipeline: `extend-returns` folds a continuation into a callee, minting
   copies along the tail and continuation chains so every other caller stands untouched. It is driven by
   `DECLARED_EXTENSIONS`, a written table; this drives it from a rule.

   1. **The invariant first, and watched to fail**: a production reached by a way with a trailing continuation, whose
      tail chain ends in a choice holding an empty way. What it counts is the distinct (callee, trailing continuation)
      pairs, one copy each.
   1. **Drive the fold from it** rather than from a written table of sites, the table shrinking as the rule grows.
   1. **The ε then dies to the rule above**, its way now followed by something that reads. No new elimination logic.
   1. **Outward for the rest.** A site with nothing trailing makes its *caller's* tail end in the blind choice, so the
      caller is the next candidate. It stops at a root, where an empty way is entitled.

   **What it buys beyond the elimination.** A copy carrying its continuation has **one follow**, which is the
   precondition a general prefix-extraction step needs and does not have: such a step must refuse a conflict reached
   with several follows, and a conflict silently gaining a second caller is what stops a speculation resolving. This is
   the only move found so far that serves both.

   **Unknown, and to be read before it is built**: whether the fold generalizes past the two-way shape; how far each
   fold walks the tail chain, the count being of pairs and not copies; and which way the meter moves, a copy being a
   production with decision points of its own.

   **What the shape of the work is.** A step that lifts to the call sites and never pushes *makes* inline empties rather
   than removing them, and the step that gives each one a production again undoes it — two that are inverses, the
   nullable population sitting at a fixed point. What breaks the circle is absorption: taking an empty way into the
   sequence around it wherever every way it makes reads, so the ε dies rather than being renamed.
   `l-comment ::= s-separate-in-line (c-nb-comment-text)? b-comment` is the case it is for. Distribution then hands what
   survives to the call sites.

   **The ordering constraint falls out**: giving a choice a production of its own may not run until the absorption has
   had its turn.

   **The cost is bounded**: a sequence holds few parts that split, so the product stays small before absorption, and
   absorption takes most of them at once. A residue is an action chain and not a bare ε: the one distributed out of
   `s-flow-folded_6` carries `PopCode`, `RetypeProvisional` and `CommitProvisional`, each of which may be moved, pairing
   dynamically off the parse's own stack or the queue's run rather than reading anything the call holds. One carrying a
   `CloseWindow` may not, the window's pair being the one that is not dynamic.

   The rest of what was sketched here — splitting sequences toward binary first, associating outward from a reader — was
   an answer to the product, which the bound above says was never the problem. What follows still stands:

   - *The invariant is that no production offers both a way that reads and a way that does not.* Not "nothing matches
     empty": a single-way action bundle — a continuation carrying a `PopMessage`, a guard the canonical form gives its
     own production — matches empty and decides nothing, and the canonical form mints those deliberately, so the
     stronger rule would forbid the target. What is forbidden is the blind choice between reading and not.
   - *It is read after every step from the elimination on*, and not at the one step that makes it. Because it counts the
     blind choice and not everything matching empty, the step that gives an inline `Alt(reads, empty)` a production of
     its own is the one that breaks it — the whole of the debt in one declared lapse, which the distributing and
     absorbing steps pay down. A broad reading that counts everything matching empty measures the wrong thing: it names
     steps that only mint the single-way bundle the invariant permits, and every lapse written for it goes stale the
     moment it is narrowed.
     - What it took to read at all: `_is_nullable` knew only the pre-canonical vocabulary and refused a `ChoiceState`,
       so the check could not run past `alternative-shape`; the gate and its guards take nothing and a recovery is no
       way an alternative offers, which leaves the actions and the two calls. It answers a different question from
       `_alternative_first`'s nullability and keeps its own convention — a span run is a value the scan decides, not a
       way the parse chooses — so the two stay separate deliberately. The repetition half read `node.item` on a
       `TrimStar`, which spells it `full`: latent for as long as the check ran at one stage only.
   - *Lowering a run of none or more is what breaks it by construction*, `_N ::= x _N | <empty>` being a production that
     decides between reading and not. What it wants instead is the one-or-more helper with the empty left at the site:
     `_N ::= x _N | x`, and `_N | <empty>` where the run stood, which the elimination then distributes like any other.
   - *The elimination must not hand back what `lower-runs` removed.* Spelling the consuming form of a run of none or
     more as a run that must take a turn puts back the very node the lowering took out; the consuming form has to be
     written in the vocabulary that stands where the step runs.

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
production judged under one reachable context's follow, the contexts computed root-down as follow classes. Driven to
none, at which point the meter becomes a gate. One known over-count is the greedy optional: a call then nothing, against
taking none, decided by order and the callee's sureness rather than by character disjointness, and the certificate for
it is the first owed. The rest fall into the landings below, one at a time, each corpus-diffed. The speculations among
them are the deep end, and they are one piece of work rather than several: the five cases spend one vocabulary,
formalized under *The provisional mechanism*, and are produced by one determinizer rather than hand-cut, described under
*The synthesis*. The fold is the engine's calibration; the block-structure substrate — the line run and its measured
column — lands first among what remains, because every trailing run that can end at an indented dedent hands its last
line to a parent: the reference puts each exiting construct's end markers before the dedent line's spaces, and the
parent then consumes those spaces as its own indentation, so a site-local committed scan that ate them has taken tokens
whose markers and owner it cannot restore. The block scalar's empties ride the substrate as the engine's first
speculation target; the document prefix and the implicit key land after, spending the same shared scan.

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
     maximal scan joins the prefix where every leftover's accepted space is known, cannot match empty, and excludes the
     scanned characters — a shorter run then leaves a character no leftover admits, so the maximal run is the only one
     that proceeds and the factoring reorders nothing; and a `(match)` scope's opening joins where the minted leftover
     production declares the origin and is passed the caller's own, the `code` parameter's exact twin, so the closing
     half restores what the unfactored close restored. A leftover's leading `Lt`/`Le` assertions rise into its gate's
     guards, judged at the same position, where the certificates read them. Together these are the whole of the local
     factoring, and they cover the seam within any one production.
   - *Seam moves*, demand-driven at conflicts the meter flags, since applied blindly they only duplicate productions —
     and one generic transform embodies all three: `extend-returns` folds a declared site's call-then-continuation so
     the continuation's actions run inside the call's own family, appended to every return path through minted copies, a
     tail recursion folding to its own copy — reassociation, distribution, and the tail-fold in one walk, each
     application a corpus-held identity, so the seam is absorbed one helper at a time rather than in one atomic flip.
     The first target is the sequence loop's exit, carrying its own `end-sequence` one helper nearer the parent's scan;
     the chain continues through the wrapper and entry helpers until the parent's scan is local to the conflict. The
     mapping loop's exit goes beside it, for a second reason worth keeping: absorbing its end-marker helper leaves the
     way that carried it a single call, and a way with a call *and* a continuation cannot hand its pop down — the
     continuation goes on the stack ahead of where the pop would land. So absorbing the seam is also what lets a pop
     sink to the loop's own scan, which stops it reading an `m` a nested write has since replaced.
   - *Held-prefix factoring* — the one move that is not an identity, and the exact spot the provisional mechanism
     enters. Factoring the scan across a zero-width emission — `(Emit·I·x | I·y)` — would commute the marker past the
     indent token in the stream, which is the dedent's marker order: exiting levels' end markers stand before the dedent
     line's indent. The stream-preserving completion is the hold: the scan's spaces are held in the line run, the mark
     re-taken at each line start, and the factored-out emission becomes an injection before the held indent at
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

The five actions and what each does are `ir.py`'s, node by node; `check_provisional` is the balance net over them. Two
properties the cases below lean on: injections and retypes commute — a retype reaches only held tokens, an injection
only adds decided ones — so a resolution issues them in whatever order reads best at its site; and **runs never nest**,
which no case asks them to. The multi-line flow key is why they need not: `[1,` and then a break cannot be a key at all,
a key being one line by the spec's own restriction, so the break that opens the fold's run is the same break that
resolves the prefix's.

**The cases, part one — what each resolves to under the formalism.** Every row is read off the reference interpreter.

| Speculation                                    | What the run holds                                                 | What decides it                                                                                      | Injections                                                                                                       | Retype                                                                                     |
| ---------------------------------------------- | ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| The flow fold, `s-flow-folded`                 | the break, and the follower's indent and whites                    | a break is an empty line; anything else, the input's end included, is content                        | none                                                                                                             | content: `(None, line-fold, all)`; empty: none                                             |
| The document prefix and the implicit key       | the line's whites, and where a key may stand, the key's own tokens | `#`, a break or the input's end is the comment's; a `:` makes the line a key, a break refuses it one | a document: `(begin-document …, start)`, the node markers its shape needs behind it; a key: `(begin-pair, mark)` | a key or a block collection: `(indent, None, before_mark)`; otherwise none                 |
| The plain scalar's next line                   | the line's breaks                                                  | content at the follower continues the scalar; a marker, a dedent or the input's end ends it          | ends: `(end-scalar end-node, start)`                                                                             | continues on one break: `(None, line-fold, all)`; on more: `(None, line-feed, after_mark)` |
| The block scalar's opening empties             | the breaks before any content, and the indents between them        | content at the scalar's indentation; anything else leaves the scalar empty                           | empty: `(end-scalar, start)`                                                                                     | content: `(None, line-feed, all)`; empty: none                                             |
| The block scalar's trailing empties, `t=strip` | the trailing breaks                                                | the chomping its header fixed                                                                        | `(end-scalar, start)`                                                                                            | none                                                                                       |
| The same, `t=clip`                             | the trailing breaks                                                | the chomping its header fixed                                                                        | `(end-scalar, mark)`                                                                                             | `(None, line-feed, before_mark)`                                                           |
| The same, `t=keep`                             | the trailing breaks                                                | the chomping its header fixed                                                                        | none                                                                                                             | `(None, line-feed, all)`, the scalar's end emitted past the commit                         |

**The cases, part two — how each is reached from the grammar as it stands.**

1. *The flow fold* is the engine's calibration and the simplest of the five: the site fuses by name, and only the two
   signatures move under it — the retype naming `all`, the injection unused.
1. *The document prefix and the implicit key* are one landing, the run being one. The comment loops fuse at their line
   start: open the run, consume the whites, mark past them. `#`, a break or the input's end continues the loop and
   commits, the whites staying the comment's; anything else leaves the loop for a minted document entry that emits none
   of what the injection supplies and does not consume the whites a second time — the fold's consumed-prefix discipline.
   The key rides that same run to the `:` that makes it one or the break that refuses it, inside the `(max) 1024` window
   `ns-s-implicit-yaml-key` and `c-s-implicit-json-key` already carry. Covers `l-document-prefix`, `l-trail-comments`
   and the implicit-key sites.
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
  indent comparison — space against the first non-space — and resolves not to a held token but to an `Lt`/`Le` guard on
  the measured column, the indent consumed once and nothing held, since only whether more indent follows diverges, never
  its code;
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

It is calibrated against the fold, the simplest of the five and the one whose resolution is known in full: the engine
re-derives its held break and its `line-fold` retype from the raw conflict, hint-free. It is fired first at the block
scalar's empties — the hardest case it resolves alone: an unbounded run, the clip/keep/strip retype split, the injected
`end-scalar`. One static mark to a run was assumed to suffice, and the trailing empties' dedent exit is the site that
proved otherwise — end markers wanted at the last line's boundary, a spot only hindsight names — which is what the
re-taken mark is for: the line scan marks each fresh line, the last taken wins, and one position serves every line-end
boundary a run can resolve at. The corpus is what settles coverage.

**The validator** is the target invariant and equals "done": every production is a terminal character set or an ordered
list of canonical alternatives; no `StarTree`, `PlusTree`, `OptTree`, `DiffSet`, `TokenWrapper`, `Wrapper` or `CaseTree`
survives — a `LookGuard` or `NegLook` does, being what a gate's guards are made of; every alternative is gate-led with
at most two calls and nothing past the second; and every decision point is commit-safe. The invariants `normalize.STEPS`
already carries hold most of that; what is missing is the last clause.

**The verification net** is the reference interpreter, which already diffs the token stream across every stage and
already runs committed where a step says a production's decisions are proved. What is owed is the second half of that
second mode: once the determinize steps land, both modes must agree across the corpus, and a divergence means a gate is
not commit-safe — the one thing the structural invariants and the backtracking mode cannot catch on their own.

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

| Risk                                                                 | Phase   | Severity | Mitigation                                                                                                                                                                                                                                                                |
| -------------------------------------------------------------------- | ------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Malicious input triggers memory-unsafety or resource-exhaustion DoS  | all     | ▪▪▪▪     | Hardening flags on the release build; ASan/UBSan on every run; structure-aware fuzzing; `ys_options::max_bytes` bounding the input, the tokens held with it and the depth together; billion-laughs / recursive-alias guards; continuous security audit, not a final pass. |
| A step in the normalization pipeline silently changes the language   | 03      | ▪▪▪▪     | Reference IR interpreter diffs the token stream before and after every step; the committed mode catches an unsafe gate the backtracking mode cannot; dual differential oracles against YamlReference and YAMLStar; log assurance gaps.                                    |
| Naive codegen is correct but super-linear                            | 03 / 06 | ▪▪▪      | Commit-safety discharged per decision point in phase 03; profiling and hot-state tuning in phase 06.                                                                                                                                                                      |
| A pipeline step is subtly non-semantics-preserving and slips the net | 03      | ▪▪▪      | Keep every step small enough to prove by eye; assert its structural post-condition; the interpreter corpus-diff is the behavioural backstop.                                                                                                                              |
| Arena/backtracking scratch leaks or corrupts                         | 04      | ▪▪       | Input-bounded lifetimes; ASan/UBSan in CI; discard provisional state through the arena only.                                                                                                                                                                              |
| Incumbency: 1.1 quirks are load-bearing in real configs              | —       | ▪▪       | Out of scope to "fix" silently; position as a conformance upgrade, document behavioural deltas from libyaml/1.1.                                                                                                                                                          |

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

The difficulty in what is left is lumpy, not uniform:

- **The bulk of it, and the low research risk** — the remaining structural steps of the normalization pipeline, the C
  codegen, the ABI layer. Well-trodden compiler work at high effort.
- **The hard part** — the determinize steps: reducing each decision to a commit-safe one-character gate,
  faithful-by-construction. This is what decides whether the result is worth more than a hand-written machine, and it is
  where the refinement obligation of §3 falls due.

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
