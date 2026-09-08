# A grammar-derived C YAML parser generator, and the plan to implement it

Emit a fast, single-pass, pull-driven YAML 1.2 parser in C **from the formal productions**. Correctness is then a
property of the generator rather than of hand-testing. The output is the machine you'd hand-write anyway. The value is
that a proof rather than luck says this is the right language.

- Target is a **libyamlstar** ABI-compatible `.so`
- Complexity is **O(n)**, libyaml-class
- API is **pull**, through `ys_read_token()`
- Stream is **yeast**, and its codes are reference-identical

A pair of fates split the grammar's parameters, and that split reaches the whole pipeline. **`c`** is the context,
**`t`** the chomping and **`r`** the resume policy. Those are finite, and the generator resolves them at generation
time. **`n`** is the indentation and **`m`** the auto-detected indent. Those have no bound, and the runtime automaton
threads them through.

## section 3 - What validation still owes

The oracles and what they judge are `DESIGN.md`'s. The project owes these things on top of those.

- **The refinement obligation.** A commit the determinization makes wants an argument that it preserves the language of
  the productions. Commit here, *and here is why it is still the same language*. This is what makes the result worth
  more than a hand-written machine, and it is the part that touches formal-methods territory.
- **Structure-aware fuzzing over the grammar**, to hunt the long tail the suite does not reach. The semantic rules the
  BNF does not capture are where that tail lives. A fuzzer has yet to run here.
- **A written threat model** for the YAML-specific denial-of-service classes. Those are pathological nesting depth,
  unbounded allocation and quadratic blow-up. Alias expansion is not among them. A token parser emits an alias and does
  not follow it. The pushdown shape removes C-stack overflow by construction. `ys_options::max_bytes` bounds the input,
  the tokens held with it and the depth the production stack reaches. That is a single cap over those rather than a
  depth cap of its own. The threat model owes the argument that a single cap is the right shape, and the cases that show
  it.

## section 4 - Implementation milestones

A milestone is a body of work the project delivers in. A *phase* is the normalization pipeline's unit, and it drives a
single invariant to none. A milestone and a phase are different things. section 4's numbers are milestones.
`normalize._PHASES` names the phases.

Milestones are ordered by dependency. **Milestone 02 is a gate.** That milestone makes the grammar and the fixtures
spec-complete and enforcing. The transformation only touches them afterwards. Milestone 03 then does not have to go back
to the grammar. The deepest research risk lives in **milestone 03**, the normalization pipeline, where determinization
happens. Milestone 02 settles the semantic decisions that feed it. The codegen and the ABI layer are well-trodden
compiler work.

Milestones 01 and 02 are done. They are the reference interpreter, and the grammar and fixtures made spec-complete.
DESIGN.md describes what they built.

Milestone 01 and milestone 02 leave a pair of things to scope when something needs them. The first is the interpreter's
committed mode, wanted where the grammar it judges has been through the pipeline. The second is the yeast->HTML debug
view. That view bootstraps on the `yaml2html` of Haskell YamlReference. With it goes the differential fuzz corpus that
CI would run beside the fold.

### Milestone 03 - Normalize, the grammar-to-canonical pipeline that commits a decision

*Risk: High, and the prize.* This is where the backtracking grammar becomes a committed grammar. It comes as a **series
of small, individually-provable, semantics-preserving transformations** rather than as a single leap. They take the IR
to a canonical form a state machine falls out of.

A step preserves the interpreter's token stream over the whole corpus. A step establishes an invariant the steps after
it may lean on. `normalize._PHASES` declares what a phase owns and which steps serve it. DESIGN.md says what the
pipeline has reached. The work left is here.

**The order of the work, and an item is a gate on the next.**

1. **Hold a gate to what lies behind it.** `DESIGN.md` owns the space a parse decides in, and what a production accepts
   of it. The question from the other direction is still to come. That is what a production's *callers* admit, rather
   than what the production itself accepts. The law relating the pair comes with it.

   - **The gated space** is the states a parse may enter a way in, read from above and **per call site**. It is the
     gated space of the caller, narrowed by the gate at the call site, as a greatest fixpoint from the whole space. It
     is per site rather than per production. That is what makes a fault attributable. The name reported is the caller
     that admitted the state, rather than the way that fails on it.
   - **The law, `gated <= accepted` per way.** A break means something enters a way in a state nothing behind it takes.
     That is the backtracking the shape exists to remove. The accepted space is sound. A violation is therefore a hole
     the grammar really has, and the count is a meter to drive to none. A parse enters no way at all where a gated space
     is empty. That is a different question from `every-option-is-reachable`. That invariant asks whether something can
     refuse a way in front. Neither answers the other.
   - **The corpus checks a weaker claim than the gated space would support.** The assertion `check_normalize` makes
     reads the space grown per production, rather than the gated space above. Making it read that instead is part of
     what this item owes.
   - **A gate that refuses too much gets a count rather than a ban.** A gate refusing a state that a way behind it would
     have taken loses a parse rather than causing a backtrack. The same pair of spaces answer for it. A production
     refusing what it could have taken is a way the caller declines to offer. In a committed machine that counts as a
     fault. This direction gets a count and no law.

1. **Cut by the exit space.** The pipeline already reads the spaces themselves. This item owes a pair of cutting
   invariants, a count apiece, and a step that cuts the ways they find. The first says a parse enters a call only in the
   states where it enters the way that makes the call. A way's entry may have nothing in common with the entry of what
   it calls. That call is then a call no parse makes, and the ways behind it are ways nothing reaches. The second says a
   parse can enter what a way continues to where its call hands control back. That is the exit space intersected with
   the continuation's entry. Until either of them exists nothing catches a space computed too wide. Too narrow is what
   the assertions over real parses already catch.

   Somebody measured the cut's worth once, and on a grammar the pipeline does not hand on. That grammar has the
   continuations lowered into the conflicts. The pipeline has no step that does that. There the cut took the invariants
   the pipeline still owes down by better than half. Those are `every-choice-way-is-different` and
   `every-conflict-is-a-tail-call`. The fixtures and the suite cases still agreed. Against the grammar the pipeline does
   hand on, nobody has measured the cut.

1. **Settle `every-choice-way-is-different`.** The ways have gates and no pair of them half overlap. The ways the input
   cannot tell apart at all remain. Those are a pair entered in exactly the same states. A machine reading a character
   has the same answer for both, and takes the way that comes first. With the earlier items settled, a shared entry
   state is how a pair can collide. That is what makes this question simple. The shape that needed a state in an overlap
   told apart from a state just outside it is gone.

   `every-called-alternative-is-unconditional` belongs here rather than to the gating phase. Taking a callee's gate out
   to the ways that call it puts a guard over what the caller performs before the call. The design for what remains is
   *Determinize, what remains* and *The provisional mechanism* below. That design covers the block-structure factoring,
   the speculations and the vocabulary they share. **At none, the grammar is deterministic**, and that is what Milestone
   04 needs.

   A gate the pipeline makes *relocates* a guard. `hoist-guards-to-gates` and `hoist-guards-to-callers` move guards up.
   `merge-gate-peeks` merges them. `split-consumes-into-gates` mints a gate from a set inside the way itself.
   `flatten-ungated-call-trees` reaches down for the gates of what a way can run. `split-overlapping-ways` cuts a way by
   the atoms its choice refines into. The pipeline mints no gate from what a way *accepts*. A choice is told apart by
   whichever guards its ways happened to have rather than by what those ways take. That is the gap to close where a pair
   of gates admit the same character and the spaces behind them differ.

1. **Settle `every-conflict-is-a-tail-call`.** A conflict the input cannot decide is answerable where what follows the
   call decides it. That wants the call at the end of the way. A step putting a conflict in that position is still to
   come. `normalize.lower_continuations_into_conflicts` makes the move a way at a time, and sits outside the pipeline.
   Over the whole grammar the move takes the invariant *up*, and the copies hold the call sites their originals held. A
   step has to answer for those copies. The first answer is a rule that folds without minting a copy per site. The
   second is an argument that a copy's call sites are decidable where the call sites of the original were not decidable.
   *The empties* below wants the same fold for eps elimination, and comes at the duplication from the other side.

1. **Adapt the C parser to it** (Milestone 04), and what follows from there.

An invariant belonging to no phase is still to come. **`no-conditional-production-matches-empty`** has no test. The ways
the grammar breaks it go uncounted, and what follows comes off the shapes rather than off a measurement.

Such a way may have a gate already. A gate decides and the way then takes nothing. That is not itself a fault. The cost
is tightness. A way that may take none makes its caller's accepted space the union of the way's space and the
continuation's space. The spaces widen wherever such a way appears.

A callee matching empty also leaves a gate saying nothing about the input. A parse enters such a way on where the parse
is, and the way accepts whatever is there. The gate says nothing about which way of the choice the input wants.

A phase re-implements what it needs rather than inheriting anything. A step stays where it earns a place. The steps
between the phases' goals arrive with their phase.

**The questions come in order.** A machine that does not backtrack needs a way of a choice to have a gate. That machine
asks the gate before entering the way. Then it needs the gates to be exclusive. They are separate problems. The first
has landed, and `every-conditional-way-is-gated` reads none where the pipeline ends. The second is *Settle
`every-choice-way-is-different`* above, whose design is what follows here.

**Where the gate lift belongs.** `every-called-alternative-is-unconditional` is about a way that calls a production
where the way's gate sat. That callee offers a single way and asks something of its own. That belongs to *Settle
`every-choice-way-is-different`* rather than to the gating phase. The gate admitted the caller on that character. The
callee's question can refuse where the caller was let through and not elsewhere. It belongs at the caller. The parse
asks the question where the callee begins where the gate sat. That is at the `first` call, and at a tail call with
nothing performed before it. A way continuing past a call of its own resumes wherever that call left off. A tail call
past a character the way took begins past it. Neither is a position any gate above spoke of.

The lift cannot happen yet, and the measurement rather than an argument says so. A guard reaching the caller's gate has
to pass what the caller performs before the call. On the path the guard refuses, those actions did not happen. There is
a `PushCode` that did not cut the token run, and a `PopMessage` that did not leave the committed region. A call site
performs something. The lift moves nothing until the gate and the call sit adjacent. That wants the caller's actions
given a state of their own.

`every-choice-is-deterministic` is the exclusivity question and splits in half. There are the choices offering a way in
front of a fallback that nothing enters. There are the choices whose ways admit the same input, where order is what
tells them apart. The second is what factoring and speculation are for.

A gate says what it admits per axis, and an axis is independent of another. The axes are the character in front of the
parse and the character behind it. They are also whether the parse is at a line start, and the indentation the parse
sits under. Telling a choice apart on the indentation works as it does on the character.

**A marker net that follows the pipeline.** The parse holds the scope pairs to their shape. A half names the pair it
belongs to, and the parse refuses a close whose pair differs from the open on the stack. That is the right shape for
them and the wrong shape for the markers. A marker pair crosses productions by design. `b-chomped-last` emits
`end-scalar` for a `begin-scalar` opened elsewhere. Holding a marker pair to a single way would report a fault where
there is none. `check_markers` proves the `begin` and `end` balance of the grammar as authored, and does not follow the
pipeline. Minting the continuations is what first puts a `begin` in a production and its `end` in another. So the net
that follows the pipeline is still to come. Nobody supplies it yet.

**Of the lookarounds, the shape phases leave the exclusions owed.** A peek asks about a single character, and
`every-peek-is-a-character-set` holds it to a `CharSet`. A peek of a set *is* the zero-width guard the canonical gate
holds. That is a bit tested against the key the decoder already made. For the look-behinds it is a register holding the
last character. `is_sol` is that register.

The exclusions are the remainder. They ask for `c-forbidden`, and some ask for `s-indent-le-line` as well. That second
condition means "a line at this indentation with content". The machine's steps bound both, and the run of spaces is a
single consume. That is what `every-exclusion-is-bounded` holds them to.

So a way to ask a condition *on* a line start is still to come. That is a different question from what follows a line
start. It lands where the block-structure work makes a line start a decision the grammar writes.

`DESIGN.md` holds the vocabulary. That is the words gate and peek and guard. It is the canonical form the pipeline takes
the grammar to. It is what the parse may hold, and the rules a transformation obeys. Those say what the generator is,
rather than what it is to do. The emitted parser has the IR's action set. The match is node for node.

**Settling `every-choice-way-is-different` comes in small steps.** Fold residual single-token lookahead into gates.
Left-factor shared prefixes into a common gate and a branch. Repeat that until a decision turns on a single character.
Insert provisional speculation wherever no character decides.

The folding decision is where nothing decides, and so are the document prefix and the simple-key line. The plain
scalar's next line is another, and so are the block scalar's empty lines. Those spend the actions *The provisional
mechanism* below formalizes.

A pair of indentation gotchas the mechanical steps miss get handled by hand. The first is a block sequence at
indentation `0` nested directly in a mapping. The second is flow context. Flow context suspends indentation. We
discharge commit-safety per decision point, and log any residual as an assurance gap. Last, an assertion holds a
terminal to a pure char-set.

**What the project still owes, and the order it comes in.**

1. *The call written out last, after the meter reads none.* `first` and `second` are a call and where to continue. The
   machine performs that at the end, and the grammar does not write it. `PushContinuation(second)` and a jump to `first`
   say it. A pop of that continuation with a jump says the way home.

   A push or a pop then has an action naming it. An inlining becomes what it should be, and deletes a push and a jump
   with no value riding either. The C parser is a state machine with its globals, a single stack and the pending tokens.
   So long as a call is a field the grammar does not write, codegen is what decides where a push goes. That decision is
   the pipeline's job rather than the consumer's job. So the pipeline needs that, and needs it last.

   - *Why last.* The reads are pervasive and named. The move takes the continuation out of a typed field and puts it in
     the action list. `normalize.py` reads `second` throughout. `_asked_where_entered` reads it for where something
     enters a production. `_called_first` reads it for what a way enters first. A walk that follows a way's end reads it
     for where a way hands that end on. Those readers together answer what the determinizing's questions rest on, and
     the reads only get worse.

     The steps this item adds gain a side condition too. A prefix factoring takes the longest identical run of actions,
     and could take a push away from the jump it belongs to. A splice moves action runs. Moving a push moves where the
     continuation goes on the stack. A phase that writes the call out first pays that per step. A phase that writes it
     out last pays nothing. At that point no later step reshapes a way.

   - *Something else already guards what this would guard.* The grammar shows that no way continues over a call taking
     an indentation off. That holds while a call is still a field. The machine's kind assertions are the stronger
     answer, and arrive with the machine. Until then nothing depends on checking it as the parse runs.

1. *Splitting `monomorphize`, noted so nobody rediscovers it.* The step specializes `c`, `t` and `r` in a single pass
   over their combinations. A pass of the generic operation per parameter would give the same grammar with a
   separately-diffed step per parameter. The step makes the copies per combination. The split wants care it has not
   earned yet.

1. *The new steps, earliest and simplest first*, and nobody has written any. The pipeline reaches none of these shapes.
   That settling derives a shape rather than taking a shape over. They exist to put the pieces in a single place. A
   piece is neither orderable nor comparable while it sits in a different production.

   - *Inlining under a gate* gives a call only the ways its caller's gate can reach. The survivor's actions splice in
     once a single bare way remains. A callee whose peek the caller's gate already implies wants the side condition "the
     callee's peek contains the caller's" rather than "the callee has no gate". `c-chomping-indicator_t_keep` is such a
     callee. Its gate is the `'+'` the caller admits. Widening it puts the chomping consume into both header lists.

   - Even then the block header does not factor, and what stops it is worth keeping. The ordering side condition below
     needs a non-break consume *earlier in the same list*. `c-l+folded` consumes the `|` and the `>` of the scalar. That
     happens a pair of calls up. Either that call gets inlined too, or another route satisfies the condition.

   - *Ordering the actions.* Within an action list an action moves as early as it may. That leaves a canonical order the
     later steps compare with `==`. The side conditions come off the list. A scope action does not cross its partner. An
     emitter does not cross a consume or another emitter, and the stream's order is the output. A position-reading
     action crosses a consume whose set excludes line breaks. An earlier consume in the same list has to do the same.
     That leaves the at-line-start bit clear on both sides. A value-reading action does not cross what writes what it
     reads.

     The third condition is where the block header's difficulty lives. The auto-detected indent reads the position
     through a single bit. The pair of orderings run it a character apart, and the bit is already clear. Something
     consumed the scalar's indicator a pair of calls up. So the fact has to reach the list before the rule can see it.

   - *Factoring a shared leading call* into the prefix. *Holds where the call is identical in name and arguments across
     the ways and their gates are equal.* That is the shape the steps above create.

   - *Subsuming a way* whose later twin has the same actions and calls but for trailing zero-width guards. The narrower
     way dies, and the streams are identical by construction.

   - *Merging a pair of ways of a choice* that are equal once a step has ordered the actions. *Plain equality.* The
     within-production twin of the sweep's behavioural merge. That merge reaches whole productions and stops there.

   - *Dropping a dead way* whose gate is disjoint from the characters a parse can enter its production on. *Reads the
     root-down entry sets the meter computes.*

   - *Sinking a pop.* A pop holder is an ungated way whose actions are only the pop. The pop leads that production's
     ways instead where the references to what the way calls are such ways without exception, and the holders stop doing
     it. *The references being such ways, the sunk pop has a single entry into that production to be wrong at.* Run to a
     fixpoint. Sinking makes holders. A continuation whose arguments read the indentation keeps its holders. A step
     reads those arguments before the pop rather than after. So does a production a parse enters by name. So does a
     recovery, and a cut reaches a recovery with the stack as it was before the call. Nor does a way that calls *and*
     continues hand its pop down. The pop would then find the continuation rather than the indentation it comes off.

   - *Inlining a call whose production holds actions and no jump.* Being a call buys nothing and costs a push. It
     matters where the caller continues somewhere. The way pushes a continuation ahead of the call, and the call runs
     under it. An indentation coming off there would come back from under that push rather than from under the push that
     set it. Once spliced, the actions run before the way pushes anything.

   - *Hoisting a push out of a production whose ways begin with the push without exception.* The callers make it
     instead. The push comes last among that caller's actions. Then a pop and a push of a single level side by side are
     nothing, and both go. *A gate consumes nothing and refuses where it reads the indentation, and a gate that then
     fails leaves a push the failing path gives back.* The push comes off a **copy**. A caller wants a production that
     does not make it. A parse entering by name wants a production that still does. The gain is the block collections
     pushing once before the loop and popping once after the loop. The alternative pushes and pops per entry.

   - *Stripping the level off a pop*, once the last step to read it has run. Left in place, the pop reads the names it
     holds. The auto-detected indent is among those names. That keeps a value live where the parse has no use for it.

   - *Deferring a pop* that leads a way of a production to the end of that way's actions. An expression among them equal
     to the level the pop restores from becomes `Indent`. *The stack holds that level until the pop runs. The same
     measurement comes a single action later. The level the pop itself names holds it to that.* The pop crosses actions
     and no more. An alternative's calls run after them. A callee therefore faces nothing new. An action that is itself
     a call or a scope around a call stops the move. The move is for the pop right against the push that follows it,
     with no consume in between.

   - A later step may merge a pair of consecutive clears, or drop a clear behind another. A clear then does not block a
     factoring that would otherwise see a common prefix.

   - *Pruning a parameter a production does not need*, with the argument a call passed it. A production needs what
     reaches a read. That is the gate and actions of the production, and whatever it hands to a production that needs
     the parameter. A write counts too. A binding a production does not declare belongs to the call. *A least fixpoint.
     A parameter a chain of productions only relayed dies through the chain at once.*

   - And after any of them, the question `DESIGN.md`'s *nothing is singled out by name* rule asks. Does the step want a
     hand-picked target? Ordering the actions and factoring the shared call should between them leave a pair of
     orderings comparing equal. There is then nothing to swap and nothing to single out. A step that cannot manage it
     wants the missing universal rule found rather than a site named.

1. *The empties, and no step of the pipeline holds them.* `no-conditional-production-matches-empty` says no production
   something decides to enter matches empty. That is what this is for. A production that a parse enters by name is
   exempt. Those are the root's copy under a resume policy, `l-recover`'s own, and the copy a `(recover)` names. A parse
   reaches such a copy without a call. It holds no choice a call site could have taken.

   Once somebody writes the count, it covers the productions offering a blind choice. That choice is between a way that
   reads and a way that does not read. A production matching empty with a single way, or with the ways it offers, is a
   shape the canonical form mints on purpose. The invariant below allows it. The step moves the empty match a node
   sideways into an inline choice. The first step that gives a choice a production of its own hands that back.

   **A single operation does the work.** An eps does not travel. It *dies* where a neighbour reads. `Seq(a, (X|eps), b)`
   is `Alt(Seq(a,X,b), Seq(a,b))`. Both ways read where `a` or `b` reads, and the eps is gone. So "move the empties to
   the root" is really this. Push an eps outward a node at a time. It evaporates the moment a reader turns up. Whatever
   survives to a body's top belongs to that production. That production hands the eps to its callers. The steps that do
   it are dumb, and have an invariant apiece.

   1. **`explicit-empties`.** An emptiness is an `AltTree` holding a zero-width way. `x?` becomes `(x | eps)`. `x*`
      becomes `(x+ | eps)`. A sequence of zero-width parts already is such a tree. *Invariant. A node matches empty only
      as an `AltTree` with a zero-width way.*

   1. **`distribute-empties`.** Push that `AltTree` outward, a node at a time. Through a sequence,
      `SeqTree(..., AltTree(X, z), ...)` becomes `AltTree(SeqTree(..., X, ...), SeqTree(..., z, ...))`. Through an
      alternation, flatten. Through a wrapper the step wraps the ways, and a `(token)` or a `(<<<)` or a `(commit)` is
      such a wrapper. A way then keeps a whole pair.

      **Not** through a repetition. `(x|z)*` is not `x*|z*`. A repetition already means as many as there are, and none
      is a legal count. It absorbs. **Not** into a lookahead or a difference, where a choice is a pattern rather than a
      decision. *Invariant. An `AltTree` with a zero-width way is the body of a production, and a deeper node holds
      none.* Absorption wants no step of its own. The rule above does it on reaching a reader.

   1. **`lift-empties`.** A non-root body's zero-width way goes to its call sites. Drop it from the body and write
      `(RefCall(P) | residue)` at a reference. That makes a fresh inline `AltTree`, and the step above runs again.
      *Invariant, and the goal. A production offering a way that reads beside a way that reads nothing is a production a
      parse enters by name.*

   The distributing and lifting steps iterate to a fixpoint. It **terminates**. A round moves an eps strictly up the
   call graph, and the step cuts a cycle. An empty match whose route back is itself is an infinite parse rather than a
   way. It also **converges** rather than treadmilling. The absorbing runs before the re-creating.

   **An eps moves up where the call leads its way, and that is the wall.** `A ::= X C` with `X ::= D | eps` distributes
   to `A ::= X C | C`. `X` has nothing before it. A part runs at most once. `A ::= F X` does not. Taking eps out gives
   `A ::= F X | F`. The second runs **`F` a second time** where the first way's `D` fails. That is a different parse
   where `F` has more than a single parse, and a second push where it pushes.

   Nor is there another way to write it. Something makes the choice between `D` and nothing where `F` returns. `second`
   names what runs there. A production minted to hold it *is* `X`. **This shape is the wall.** Of the blind choices the
   step cannot take, this shape was the majority at the last hand measurement. A blind choice outside it has an empty
   way that calls something, or an empty way with a gate. This invariant has no test, and the figure goes stale.

   **So pull the continuation down instead of pushing the eps up.** `X ::= A Y` with `A ::= B C` and `C ::= D | eps`
   becomes `X ::= B Z` with `Z ::= D Y | Y`. The fold puts the trailing `Y` into the callee's family, where it sits
   beside the eps and both ways read. `normalize.lower_continuations_into_conflicts` already makes that move. It mints a
   copy of the callee with the continuing run past what the callee does, and the other callers go untouched. It aims at
   a conflict rather than an eps. It is out of the pipeline for having no invariant. This item wants the same move,
   aimed by the rule below.

   1. **The invariant first, and watched to fail.** It names a production reached by a way with a trailing continuation.
      The tail chain of that way ends in a choice holding an empty way. It counts the distinct callee-and-continuation
      pairs. A pair costs a copy.
   1. **Drive the fold from the invariant.** The invariant says where it applies. A step names no site of its own.
   1. **The eps then dies to the rule above.** A reader then follows its way. The elimination logic stays put.
   1. **Outward from there.** A site with nothing trailing makes its *caller's* tail end in the blind choice. The caller
      is the next candidate. It stops at a root, and a root may have an empty way.

   **What it buys beyond the elimination.** A copy with its continuation has **a single follow**. That is the
   precondition a general prefix-extraction step needs and does not have. Such a step must refuse a conflict reached
   with more than a single follow. A conflict silently gaining a second caller is what stops a speculation resolving.
   This is the move that serves both, and nobody has found another.

   **Unknown, and to read before anybody builds it.** Whether the fold generalizes past the pairwise shape. The reach of
   a fold along the tail chain, given that the count is of pairs rather than copies. The direction the meter moves in,
   given that a copy is a production with decision points of its own.

   **The shape of the work.** A step that lifts to the call sites and does not push *makes* inline empties rather than
   removing them. The step that gives an empty a production again undoes that. The pair are inverses, and the nullable
   population sits at a fixed point. Absorption breaks the circle. Absorption takes an empty way into the sequence
   around it wherever a way that sequence makes reads. The eps dies rather than gaining a new name.
   `l-comment ::= s-separate-in-line (c-nb-comment-text)? b-comment` is the case it is for. Distribution then hands what
   survives to the call sites.

   **The ordering constraint falls out.** The step that gives a choice a production of its own waits for the absorption
   to finish.

   **The cost is bounded.** A sequence holds few parts that split. The product stays small before absorption. Absorption
   then takes, at once, a product way whose surrounding sequence reads. A residue is an action chain rather than a bare
   eps. The residue's contents decide whether it moves. A residue with `PopCode`, `RetypeProvisional` or
   `CommitProvisional` may move. Those pair dynamically off the stack of the parse or the queue's run. They read nothing
   the call holds. A residue with a `CloseWindow` may not move. The window's pair is the pair that is not dynamic.

   The rest of what this section sketched answered the product. The bound above says the product was not the problem.
   That sketch split sequences toward binary first, and associated outward from a reader. The text below still holds.

   - *The invariant is that no production offers both a way that reads and a way that reads nothing.* It is not "nothing
     matches empty". A single-way action bundle matches empty and decides nothing. A continuation with a `PopMessage` is
     such a bundle, and a guard the canonical form gives its own production is another. The canonical form mints those
     deliberately, and the stronger rule would forbid the target. The blind choice between consuming and consuming
     nothing is what the invariant forbids.
   - *A checker comes after a step from the elimination on*, rather than at the step that makes it. It counts the blind
     choice rather than whatever matches empty. So the invariant breaks at the step that gives an inline
     `Alt(reads, empty)` a production of its own. That is the debt entire, in a declared lapse the distributing and
     absorbing steps pay down. A broad checker that counts whatever matches empty measures the wrong thing. It names
     steps that mint nothing beyond a single-way bundle, and the invariant permits that. A lapse written for such a step
     goes stale the moment somebody narrows the checker.
     - What it took to read at all. `_is_nullable` knew the pre-canonical vocabulary and nothing beyond it, and refused
       a `ChoiceState`. The check could not run past `build-alternatives`. Reading an alternative means reading its
       parts. The gate and its guards take nothing, and a recovery is no way an alternative offers. That leaves the
       actions and the pair of calls. The repetition half read `node.item` on a `TrimStar`, and a `TrimStar` writes that
       `full`. That stayed latent for as long as the check ran at a single stage.
   - *Lowering a run of none or more is what breaks it by construction.* `_N ::= x _N | <empty>` is a production that
     decides between consuming and consuming nothing. It wants the run that must take a turn instead, with the empty
     left at the site. That is `_N ::= x _N | x`, with `_N | <empty>` left at the site the run had. The elimination then
     distributes it like any other.
   - *The elimination must not hand back what `lower-runs` removed.* Write the consuming form of a run of none or more
     as a run that must take a turn. The node the lowering took out is then back. That form has to reach the vocabulary
     in place where the step runs.

1. *Then the certificates.* What the elimination took through does not dissolve is theirs. Guessing the count is
   worthless until that has happened. A subsumption certificate is a way whose language contains a later way's language,
   read through a single level of inlining. That certificate retires the assurance ledger's remaining entries rather
   than leaving them declared.

   The points that are neither greedy optional nor ledger want the breakdown the greedy optional has. That breakdown
   comes before anybody designs for them. That classification is cheap and comes first of the pair. It would put a
   derived number on both halves. The number written there came from no computation.

1. *And then the determinizer.* What the meter still flags points it through the landings below.

**Determinize, what remains.** The goal is the grammar deterministic **as invoked from the root**, rather than a
production at a hypothetical entry. A production undecidable on its own is no conflict where the contexts a root parse
reaches that production under decide the matter. That checker goes a single level of inlining in.

So the meter counts root-reachable decision points, a production apiece judged under a reachable context's follow. A
root-down pass computes the contexts as follow classes. The work drives the meter to none, and the meter then becomes a
gate.

The greedy optional is a known over-count. That is a call then nothing, and the rival takes none. Order and the callee's
sureness decide it rather than character disjointness. The certificate for it is the first owed. The other decision
points fall into the landings below, a case at a time, and a corpus diff answers for the move.

The speculations among them are the deep end, and the speculations are a single piece of work rather than several. The
cases here spend a shared vocabulary, formalized under *The provisional mechanism*. A determinizer produces the
speculations rather than a hand cut, and *The synthesis* describes it.

The fold is the engine's calibration. The block-structure substrate is the line run and its consumed column, and that
substrate lands first among what remains. A trailing run that can end at an indented dedent hands its last line to a
parent. The reference puts an exiting construct's end markers before the dedent line's spaces. The parent then consumes
those spaces as its own indentation. A site-local committed consume that ate them has taken tokens whose markers and
owner nothing can restore.

The block scalar's empties ride the substrate as the engine's first speculation target. The document prefix and the
implicit key land after, and they spend the same shared consume.

1. The block-structure work comes first. The indented dedent above is the case it answers. Small provable moves take it
   rather than a single surgery.

   The insight is a factoring. A block line start can go to this level's next entry, or to a deeper construct's line, or
   out by an exiting level's way. Those begin with the line's spaces. So the spaces are a common prefix, and a step
   extracts them once ahead of the decision. The decisions behind it become character gates with column guards.

   A move is a language identity, or has a single mechanically-checked side condition. An intermediate grammar is
   corpus-green.

   - *Indent refinement* is the enabling move, a pipeline step, applied wherever its side condition proves. An exact
     count of spaces followed by a non-space is a maximal consume judged after the fact, and the grammar writes it that
     way. `s-indent-le` already has that shape. `s-indent(k)-X` becomes
     `OpenMatch-ConsumeSpan(space)-[Len(Match)==k, as the Le pair]-CloseMatch-X`. That holds wherever space is not in
     FIRST(X) and X cannot match empty. Maximal munch then has nothing to steal.

     The measure is the `(match)` scope of the consume. The rewrite is position-independent, and reads the production's
     nodes and the FIRST table and no more. The `<n` and `<=n` variants already have this shape. It is stream-faithful.
     Paths consuming the same spaces under the same code accept, and the token count matches.

     The gain is a shared prefix. Indent consumes of different `k` share no literal prefix. The refinement makes them
     the identical consume. The differing counts become residual guards.

   - *Aggressive common-prefix extraction*. The pipeline does none of it yet. The step that extracts an identical prefix
     from the ways of a choice is still to come. The refinement above makes indentation identical. The extraction runs
     until the ways share no factorable prefix. The stop is a named blocker below rather than a shrug.

     A pair of admissions grow it. An identical maximal consume joins the prefix where the grammar knows a leftover's
     accepted space, that space cannot match empty, and it excludes the scanned characters. A shorter run then leaves a
     character no leftover admits. The maximal run is what proceeds. The factoring reorders nothing. A `(match)` scope's
     opening joins where the minted leftover production declares the origin and takes the origin of the caller. That
     origin is the exact twin of the `code` parameter. The closing half then restores what the unfactored close
     restored.

     A leftover's leading indentation comparisons rise into the gate's guards, judged at the same position, where the
     certificates read those guards. Together these are the local factoring entire, and they cover the seam within a
     production.

   - *Seam moves*, demand-driven at conflicts the meter flags. A blind application does no more than duplicate
     productions. A single generic transform covers them. That is a fold of a call-then-continuation. The continuation's
     actions run inside the family of the call, appended to a return path through minted copies. A tail recursion folds
     to its own copy. Reassociation, distribution and the tail-fold come in a single walk.

     An application is a corpus-held identity. The seam absorbs a helper at a time rather than in a single atomic flip.
     The first target is the sequence loop's exit. That exit takes its `end-sequence` a helper nearer the parent's
     consume. The chain continues through the wrapper and entry helpers until the parent's consume is local to the
     conflict.

     The mapping loop's exit goes beside it. A second point there is worth keeping. Absorbing the end-marker helper
     leaves the way that held it a single call. A way with a call *and* a continuation cannot hand its pop down. The
     continuation goes on the stack ahead of where the pop would land. So absorbing the seam is also what lets a pop
     sink to the consume of the loop. That stops it reading an `m` a nested write has since replaced.

   - *Held-prefix factoring* is the move that is not an identity, and the exact spot the provisional mechanism enters.
     Factoring the consume across a zero-width emission, `(Emit-I-x | I-y)`, would commute the marker past the indent
     token in the stream. That is the dedent's marker order. An exiting level's end marker comes before the dedent
     line's indent.

     The stream-preserving completion is the hold. The line run holds the consume's spaces. A line start re-takes the
     mark. The factored-out emission becomes an injection before the held indent at resolution. The parse spends it
     where the meter demands and stops there. A held run is runtime buffering.

   - The hand-built rewrite set aside in `junk-surgery/` is these moves' calibration oracle for the sequence loop. The
     composed identities must reproduce it, held to the dedent fixtures already pinned.

1. The block scalar's empty lines ride the factored line starts. That holds for the opening lines and for the trailing
   lines. The run holds the breaks and the held indents across lines, and a line start re-takes the mark. That run
   resolves into content, or into the scalar's end injected ahead of it.

   The chomping split is monomorphize's already. `l-literal-content` and `l-folded-content` have a copy per `t`. The
   callee layer the fusion reaches through stays `t`-shared. That is `b-l-folded` at block, `l-empty`, and the empties
   chains.

   Per chomping, re-read off the reference at landing. Under strip the run opens before the chomped last break, and
   `end-scalar` goes in at `start`. There is no retype where the scalar ends, and a break goes to `line-feed` where
   content follows. Under clip the last content break is `line-feed` in both meanings. The run opens past it and strip's
   shape follows. The static mark the table below gives clip should dissolve. Under keep both meanings write a break
   `line-feed`, and the line runs may be what remains to decide.

   A dedent exit belongs to the substrate. A level injects its own end markers at the mark, and the parent owns the last
   line. The engine work rides along. There is rooting under callers that are not unique. There is the injection read
   off the divergence, a marker a single path emits where the other holds tokens. There is the non-converging walk
   emitted as the runtime loop the fold's hand-built empties loop already shapes.

   The opening empties weave in the auto-detected indent and the `(increase)` floor the leading empties set. The block
   fold fuse lands last on what this item holds. That fuse is `b-l-spaced` and `l-nb-spaced-lines`, and the block
   `b-l-folded` sites.

1. The document-prefix speculation is the positional injection's first exercise. The comment loops between documents
   hold whites both meanings claim. The spans agree. The decision settles their codes and where the markers go. The code
   is `white` where a comment line owns the whites, and `indent` where a block collection does. `begin-document` and the
   node markers behind it come ahead of the whites.

   So the line opens a provisional run and holds the whites. A `#`, a break or the end of the input decides the comment.
   Anything else is a document, whose own shape the `:` decides or refuses.

   The simple-key speculation resolves that run. The pair are a single landing rather than a pair in that order. That
   landing does bounded eager buffering for the key line. It resolves key-vs-scalar at the `:`, or at the break that
   refuses the key. It sits inside the `(max) 1024` window the grammar already has. It covers the consuming half of
   `l-document-prefix`, and `l-trail-comments`, and the implicit-key sites.

1. The plain scalar's next line asks whether a multi-line scalar continues at all. It is the same held-break read. It
   lands with the key's read, and both resolve at a line's end.

1. Per-site separation fusions, where a decision hides past optional separation and the paths do not reconverge. Those
   are the properties' separate-then-tag, and the flow key and value entries' separate-then-indicator. A measurement
   takes them at the site. The fusion is fold-style, and needs no retype where the separation's codes agree either way.

   A measurement takes that agreement off the reference per site before anybody builds the fusion. Nobody assumes it.
   The document prefix's whites looked like they agreed and turned out not to. A measurement gave `white` and the other
   gave `indent`. A site whose codes disagree wants a retype, and belongs with the speculations. Generic absorption does
   not exist here. The continuations do not open on the bare separation the exit's rival consumes.

1. The assurance ledger, after the surgery settles the minted names. The ledger holds declared order-commitments with
   their reasons. Those are the fused fold consumes, the document loops if the surgery leaves them in place, and
   whatever order-only residue remains. It has a staleness net refusing an entry the grammar lost or the analysis has
   since proved. It has a counted line the gate prints, like the vendored spec's declared deviations.

1. The determinizer pointed by detection rather than by name. A landing above hands the engine a conflict its step
   names, the fold's `b-l-folded_c_flow-in` first and the empties' sites next. That is scaffolding rather than the end
   state. The group the meter flags defines the engine's input. The landings prove it case by case. Then the pipeline's
   determinize step walks the conflicts the meter finds and fires on them, and the name lists go. The names left are the
   assurance ledger's entries with their reasons.

1. The validator made the gate, and the deferred trim-reuse pass.

**The provisional mechanism**, formalized. A speculation takes this shape. A run is a contiguous stretch of pending
tokens. The parser builds a run and holds it. Nobody sees the run until it resolves. A span settles the moment the
parser consumes its characters, and the parser does not rewind input.

A resolution settles the codes those tokens take, and which decided markers go among them. It drops no held token and
grows none out of held characters. So the meanings a run decides between must agree token for token and span for span. A
difference between them must be a zero-width marker. That is the mechanism's single obligation, and the cases below are
where a speculation discharges it.

The provisional actions and what they do are `ir.py`'s. The mapping is node by node. A balance net over them comes with
the first step that writes a net. An action is zero-width to the other analyses. Those analyses cannot see an open
inside a run, or a mark outside a run. They cannot see a retype or an injection naming a mark nobody took, or a commit
with no run.

The cases below lean on a pair of properties. Injections and retypes commute. A retype reaches held tokens only, and an
injection adds decided tokens only. A resolution issues them in whatever order reads best at the site. And **runs do not
nest**, and no case asks them to.

The multi-line flow key is what keeps them flat. `[1,` and then a break cannot be a key at all. The spec restricts a key
to a single line. So the break that opens the fold's run is the same break that resolves the run of the prefix.

**The first batch of cases, and what they resolve to under the formalism.** A row comes off the reference interpreter.

| Speculation                                         | The run's contents                                          | The decider                                                                                                     | Injections                                                                                                                      | Retype                                                                                                  |
| --------------------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| The flow fold, `s-flow-folded`                      | the break, and the follower's indent and whites             | a break is an empty line. Anything else is content, and the input's end counts as content                       | none                                                                                                                            | content gives `(None, line-fold, all)`. Empty gives none                                                |
| The document prefix and the implicit key            | the line's whites, and the key's tokens where a key may go  | `#`, a break or the input's end belongs to the comment. A `:` makes the line a key, and a break refuses the key | a document gives `(begin-document ..., start)` and the node markers the shape needs behind it. A key gives `(begin-pair, mark)` | a key or a block collection gives `(indent, None, before_mark)`. Anything else gives none               |
| The plain scalar's next line                        | the line's breaks                                           | content at the follower continues the scalar. A marker, a dedent or the input's end ends it                     | ends: `(end-scalar end-node, start)`                                                                                            | a single break continues with `(None, line-fold, all)`. More break with `(None, line-feed, after_mark)` |
| The block scalar's opening empties                  | the breaks before any content, and the indents between them | content at the scalar's indentation. Anything else leaves the scalar empty                                      | empty: `(end-scalar, start)`                                                                                                    | content gives `(None, line-feed, all)`. Empty gives none                                                |
| The block scalar's trailing empties under `t=strip` | the trailing breaks                                         | the chomping its header fixed                                                                                   | `(end-scalar, start)`                                                                                                           | none                                                                                                    |
| The same under `t=clip`                             | the trailing breaks                                         | the chomping its header fixed                                                                                   | `(end-scalar, mark)`                                                                                                            | `(None, line-feed, before_mark)`                                                                        |
| The same under `t=keep`                             | the trailing breaks                                         | the chomping its header fixed                                                                                   | none                                                                                                                            | `(None, line-feed, all)`, the scalar's end emitted past the commit                                      |

**The second batch of cases. The grammar reaches them this way.**

1. *The flow fold* is the engine's calibration and the plainest of the landings. The site fuses by name. A pair of
   signatures move under it and no more. The retype names `all` and the injection goes unused.

1. *The document prefix and the implicit key* are a single landing, and the run is a single run. The comment loops fuse
   at their line start. The parse opens the run, consumes the whites, and marks past them. A `#`, a break or the input's
   end continues the loop and commits, and the comment keeps the whites. Anything else leaves the loop for a minted
   document entry. That entry emits nothing the injection supplies, and does not consume the whites a second time. That
   is the fold's consumed-prefix discipline.

   The key rides that same run to the `:` that makes a key, or to the break that refuses the key. It stays inside the
   `(max) 1024` window `ns-s-implicit-yaml-key` and `c-s-implicit-json-key` already have. It covers `l-document-prefix`,
   `l-trail-comments` and the implicit-key sites.

1. *The plain scalar's next line* opens its run at the break that ends a content line. The follower's gate decides that
   run. The minted continuation does not emit the scalar's end where the injection supplied it.

1. *The block scalar's empties* want the loop productions split per chomping first. That holds for the opening empties
   and for the trailing empties. They are `t`-shared, and the tail's codes differ. A branch's resolution is then a row
   above.

**The synthesis. A determinizer derives the provisional productions rather than a hand cut.** A speculation above is the
output of a single determinizer, and no bespoke rewrite. The input is the group the meter flags. That is the live
alternatives rooted at an overlapping-gate choice point, pruned to the minimal set no gate can separate. By definition
that is where non-determinism lives. A single alternative decides nothing. The output is the provisional-mechanism
equivalent.

The method is subset construction. Walk the live alternatives in lockstep on the input, and read the divergence between
them. It is a kind below, and a kind maps to a single output.

- A character the paths consume over the same span but under different codes stays held. The resolution retypes it to
  the code of the surviving path.
- A zero-width marker a path emits and another does not waits, and the resolution injects it. So does a marker emitted
  at a position that falls before an already-held token. It goes at the run's start where the marker precedes the held
  tokens. It goes at the mark where the marker falls among the held tokens. Either way it goes in the order of the
  surviving path.
- A space a path consumes as more indentation is the same character another has reached its level on. A measurement
  takes it as an indent comparison of space against the first non-space. It resolves to an indentation comparison on the
  measured column rather than to a held token. The parse consumes the indent once and holds nothing. Whether more indent
  follows is what diverges, and the code stays put.
- The character on which the paths' gates first differ is the discriminator. The run commits there to the path that
  survives it.

The first pair of outputs are the provisional actions, and the third is a guard. So it is a single divergence analysis
with a pair of output kinds rather than a pair of mechanisms. A token's code or presence diverging holds and injects. A
position against a level diverging guards.

The alternatives give these rather than a hint. The conflict root, the held token and the retype code come off the
alternatives of the grammar. So do the injected markers with their order and side, and the mark itself. The mark and the
injections are actions placed at the structural boundary the walk finds, where the shared prefix ends. At runtime they
record the live queue position by themselves. The runtime counts no tokens and threads no index. The placement in the
production is the position.

The walk converges to a straight-line region. It may instead recur without converging. Recurring is the signal for a
runtime loop rather than a fault. The block scalar's opening empties are the unbounded case, and the fold's consume is
the bounded case.

The block-structure surgery is no second mechanism beside the engine. It is the engine's indentation output, the third
divergence form above, applied to the block-collection loops. There continue-vs-exit is a space against the first
non-space at the collection's level.

A pair of things make this facet costly. The same pair keep the facet a distinct body of work under the same principle.
A dedent can cross nested loops on a single character. The inner sequence exits and the outer continues at the same `-`.
To stay committed the parse must consume the indent once at the line start and read it by zero-width guards across the
nested loops. That is a consume *shared* between conflicts the engine otherwise resolves a case at a time. It is not the
local fix a single-level fold needs. And the level itself is auto-detected. The first entry sets `n+m`, and the run
establishes the column the guard compares against rather than the synthesis fixing it.

The flow fold, the plain scalar's next line, and the block scalar's empties are pure output-deferral and need nothing of
this. The document prefix and the implicit key need the shared consume, and land after it.

A pair of nets hold the engine's output. The first is the certificate that a decision turns on a single character. It
comes with the engine, and names the productions the engine tells the interpreter to enter committed. The second is the
hybrid corpus proving the stream identical, and that corpus exists already. So a wrong synthesis fails loud rather than
silent, and wants no proof of the engine beyond the certificate on its result.

The fold calibrates it. That landing is the plainest, and the table above gives its resolution in full. The engine
re-derives the held break and the `line-fold` retype from the raw conflict, and takes no hint. The first firing is at
the block scalar's empties. That case has an unbounded run, the clip/keep/strip retype split, and the injected
`end-scalar`.

A static mark to a run looked like enough. The trailing empties' dedent exit is the site that proved otherwise. That
site wants end markers at the last line's boundary, and only hindsight names the spot. That is what the re-taken mark is
for. A rule reading line by line marks a fresh line, and the last taken wins. That position then serves the line-end
boundaries a run can resolve at. The corpus is what settles coverage.

**The validator** is the target invariant and equals "done". A production is a terminal character set or an ordered list
of canonical alternatives. A `StarTree` or a `PlusTree` does not survive. Nor does an `OptTree` or a `DiffSet`. Nor does
a `TokenWrapper` or a `Wrapper` or a `CaseTree`. A `LookGuard` or `NegLook` does survive, and those are what a gate's
guards write. An alternative is gate-led with at most a pair of calls and nothing past the second. A decision point is
commit-safe. The invariants `normalize.STEPS` already names hold that. The last clause is the exception.

**The verification net** is the reference interpreter. It already diffs the token stream across a stage. The committed
mode is still to come in full. That mode enters the productions a step proves, and takes the first alternative whose
gate holds. Once the determinize steps land, both modes must agree across the corpus. A divergence means a gate is not
commit-safe. That is what the structural invariants and the backtracking mode cannot catch on their own.

**Exit** is a canonical grammar the validator passes, on which the interpreter agrees in both modes across the corpus. A
speculation resolves its run correctly, with the deferral exercised deliberately. Emitting the C state machine in
Milestone 04 is then mechanical rather than clever.

### Milestone 04 - C codegen

*Risk: Low - ~1-2 mo.* The easy end of a compiler. Turn the lowered IR into a switch-on-state character loop with arena
allocation.

1. Emit the state dispatcher and the transition tables into `src/parser_tables.h`, as portable C99 with no external
   deps, over the runtime `src/parser.c` already provides.
1. Emit, per state, the production it belongs to. Emit what its outgoing edges expect. That is a table of static strings
   shaped like `src/messages.c`'s. The text of a format error goes with it. The parser's finding sits outside them, and
   stays outside. The first `unparsed` token behind an error begins at exactly the byte that failed.
1. Arena-allocate what the parse holds. Lifetimes are input-bounded. Free the arena on parser teardown, and no GC runs.
1. Handle backtracking-region scratch within the arena. Ensure the parse reclaims discarded provisional state cleanly.
1. Emit the pull surface. That is `new`, `next_token` and `free`. A structured error extraction goes with them.
1. Emit the `Code` enum and the compose fold from yeast to node graph. Event retention is trace-mode only. The committed
   hot path emits and consumes without buffering.
1. Migrate `yaml2html` into the package. It is a small C companion. It folds the yeast stream to colorized nested HTML.
   It shares the emitted `Code` enum. The debug view then ships *with* the library and needs no Haskell YamlReference
   dependency. Validate byte-for-byte against the Haskell YamlReference renderer.
1. Build as `cdylib`-style `.so` across Linux and macOS, and Windows once the toolchain is clean.

**Exit** is a self-contained C `.so` plus the bundled `yaml2html` tool. It passes suite and differential and fuzz.

### Milestone 05 - ABI layer

*Risk: Low - ~3-5 wks.* YAMLStar built the existing ABI as a swappable seam. It is thin, and JSON strings go in and out.
It exposes no structs. So this is nearly free. The existing bindings work unchanged.

1. Reimplement the create/destroy/`load`/`load_all`/`version` entry points over the new core.
1. Route `load` through the yeast fold `compose -> resolve -> serialize`. Reuse YAMLStar's existing resolver and dumper
   rather than reimplementing them. Ship the other consumers of the same stream. Those are the bundled `yaml2html` debug
   view and the differential harness.
1. Keep GraalVM-era lifecycle calls as cheap no-ops or lightweight context handles. They are vestigial and harmless.
1. Serialize errors into the exact type/cause/message shape the bindings parse back out.
1. Reproduce the JSON-interchange contract faithfully (including its documented `.inf`/`.nan` limitation) for true
   drop-in behaviour.
1. Run the existing binding test suites unmodified against the new `.so`. Those are Python and Go, Rust and C#, and the
   other bindings.

**Exit** is the new `.so` slotting in where the GraalVM blob sat, and the bindings stay green.

### Milestone 06 - Harden. Fuzz, tune, and reach libyaml-class speed

*Risk: Medium - ~2-4 mo.* Correct-but-slow is not the goal. Close the algorithmic gaps naive codegen leaves and prove
robustness under hostile input.

1. Continuous structure-aware + byte-level fuzzing (ASan/UBSan) targeting the semantic long tail.
1. Profile. Eliminate any residual super-linear behaviour from over-broad lookahead.
1. Benchmark against libyaml on representative corpora. Tune hot states and buffering.
1. Build the release library with link-time optimization. The dispatch takes a token at a time, and then inlines across
   the translation units holding it.
1. Optionally, extend to an emitter for dumping. Or keep libyaml's emitter alongside for a complete round-trip library.
1. Cut prebuilt binaries per platform. A local native build then stops gating adoption.

**Exit** is O(n) confirmed and libyaml-competitive. It is fuzz-clean and packaged.

## section 5 - Risk register

| Risk                                                                 | Milestone | Severity | Mitigation                                                                                                                                                                                                                                 |
| -------------------------------------------------------------------- | --------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Malicious input triggers memory-unsafety or resource-exhaustion DoS  | any       | \*\*\*\* | Hardening flags on the release build. ASan/UBSan on a run. Structure-aware fuzzing. `ys_options::max_bytes` as a single cap. A continuous security audit rather than a final pass.                                                         |
| A step in the normalization pipeline silently changes the language   | 03        | \*\*\*\* | Reference IR interpreter diffs the token stream before and after a step. The committed mode catches an unsafe gate the backtracking mode cannot. Dual differential oracles against Haskell YamlReference and YAMLStar. Log assurance gaps. |
| Naive codegen is correct but super-linear                            | 03 / 06   | \*\*\*   | Commit-safety discharged per decision point in phase 03. Profiling and hot-state tuning in phase 06.                                                                                                                                       |
| A pipeline step is subtly non-semantics-preserving and slips the net | 03        | \*\*\*   | Keep a step small enough to prove by eye. Assert its structural post-condition. The interpreter corpus-diff is the behavioural backstop.                                                                                                   |
| Arena/backtracking scratch leaks or corrupts                         | 04        | \*\*     | Input-bounded lifetimes. ASan/UBSan in CI. Discard provisional state through the arena only.                                                                                                                                               |
| Incumbency, where 1.1 quirks are load-bearing in real configs        | -         | \*\*     | Out of scope to "fix" silently. Position as a conformance upgrade, and document behavioural deltas from libyaml/1.1.                                                                                                                       |

## section 6 - Future work

The project wants the items below. Nobody has planned them, and no work waits on the plan.

- **Lenient wire positions.** Treat a `#` line in the wire as a comment rather than a required field. Use the token
  position a line gives. A line writes it `# B: ..., C: ..., L: ..., c: ...`. A line giving none leaves the position for
  an estimate off the tokens themselves where that is possible. Otherwise give the token an obvious "no position" value
  rather than rejecting the wire. This lets somebody hand-write or trim a wire, position lines included, and still read
  it.

- **Token-emission levels.** A knob in `ys_options` chooses how much of the stream `ys_next_token` emits. The levels run
  coarsest to finest, and a level is a superset of the level before it.

  - The structure markers by themselves, the `begin-` and `end-` pairs that bracket the productions.
  - The payload as well. That is the content characters, and the default emits them.
  - The non-payload characters as well. Those are the indentation and the separation. They are also the breaks and the
    indicators. The table then covers an input byte.
  - The detection values too. Those are `YS_CODE_DETECTED` tokens giving the `m` or `t` an indentation or chomping rule
    computed. They make libyeast's detection comparable to Haskell YamlReference's `Detected` output. The comparison is
    token for token.

  So `YS_CODE_DETECTED` is in the vocabulary already. The finest level is where libyeast emits that code, and the wire
  round-trips it in the meantime. A coarser level is cheaper, and is what a caller loading a document needs. A finer
  level is what the differential oracle and a debugger want.

- **An event-projecting token source.** It is a `ys_token_source` that wraps another and is a source itself. It hands
  back the event-level tokens and no more. Those are the stream and document markers. They are also the mapping and
  sequence markers, and the scalar and alias markers. A scalar's value comes already folded, with its anchor and tag
  attached. A `line-fold` becomes a space, a `line-feed` becomes a newline, and the fold resolves an escape. The
  projection drops the node and pair brackets. It drops indicators and indentation. It drops whitespace and breaks.

  The Python fold the YAML Test Suite runs through has a C twin here. The event stream is a subset of yeast. The
  projection is a filter over the markers, plus the mechanical value fold the codes already settle. A caller wanting
  YAML events rather than tokens reads them straight, and composes no node graph. It wraps a source and is a source, and
  drops in wherever tokens already flow.

- **Arena allocators.** Revisit the `ys_allocator` API against arena and pool allocators. Those free the whole arena at
  once rather than buffer by buffer. Ask whether a no-op `deallocate` is enough as it is, or the shape wants a variant.
  Ask whether a source can arrange its allocations so a caller drops the whole parse in a single free. The `close` hook
  is already the seam for tearing such an allocator down.

- **libc version portability.** Deal with the libc-version issues a shared library faces. Those are which symbol
  versions the built `.so` pulls in, and their minimums. A binary built against a newer toolchain then still loads on an
  older target. The ABI-compat goal is a libyamlstar drop-in. It depends on this not being quietly broken by a libc
  symbol-version bump.

- **Optimization and benchmarking of the C implementation.** That goes past the tuning that reaches libyaml-class speed.
  It wants a permanent benchmark suite over representative corpora. Those are deep nesting and long scalars, wide
  collections, and flow-heavy and block-heavy documents. The suite runs per build. A regression then shows up at the
  build that caused it rather than later.

  The interesting comparison beside libyaml is against **JSON parsers**. JSON is a subset of YAML, and its parsers are
  among the quickest structured-text readers in use. The ratio between them plainly measures the cost of YAML's
  indentation and folding and deferral.

  The comparison also says which of those costs the grammar takes and which the generated machine does. A JSON-shaped
  document read by libyeast exercises little of the speculation. The gap that remains on such a document is the
  automaton's overhead.

- Binaries as well as library. Those are yaml2yeast (resume policy in ARGV) and yeast2yaml (filtering policy in ARGV).
  They are also yeast2html (based on Haskell YamlReference). They are yaml2event and yeast2event.

## section 7 - Shape of the whole

The difficulty in the remaining work is lumpy rather than uniform.

- **The low research risk.** That is the remaining structural steps of the normalization pipeline, the C codegen, and
  the ABI layer. It is well-trodden compiler work at high effort.
- **The hard part.** That is the determinize steps. They reduce a decision to a commit-safe single-character gate and
  stay faithful by construction. This is what decides whether the result is worth more than a hand-written machine. It
  is where the refinement obligation of section 3 falls due.

## section 8 - YAMLStar upstream notes

A running list of what libyeast's work surfaces that belongs upstream. An item is a fix or an addition to YAMLStar, or
to the test suites YAMLStar validates. The entry says where libyeast found it.

- **A test that appends a line break the input lacks.** YAML Test Suite `JEF9/02` is an empty kept block scalar whose
  input ends in no line break. The spec folds it to the empty scalar. `b-chomped-last` is where end-of-input counts as a
  break. A scalar with no content line does not reach that group. `l-keep-empty`'s `l-empty` needs a real `b-break`.

  The suite's expected single line break comes from YAMLStar appending a trailing break to the input before parsing. The
  case tests the appended input rather than the input on disk. libyeast declares the difference in
  `check_star.DIVERGENCES`. The no-trailing-break form is an interesting edge case worth adding to the suite in its own
  right, with the expectation the spec's meaning gives.
