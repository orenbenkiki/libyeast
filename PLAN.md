# A grammar-derived C YAML parser generator, and the plan to implement it

Emit a fast, single-pass, pull-driven YAML 1.2 parser in C **from the formal productions**. The generator then decides
correctness. Hand-testing does not. A proof shows that the parser accepts the right language.

- The target is a `.so` that is ABI-compatible with **libyamlstar**
- The parser runs in **O(n)**, as libyaml does
- The API is **pull**. A caller pulls a token with `ys_read_token()`
- Stream is **yeast**, and its codes are reference-identical

The pipeline treats a grammar parameter in either of a pair of ways. **`c`** is the context, **`t`** is the chomping,
and **`r`** is the resume policy. Those are finite, and the generator resolves them at generation time. **`n`** is the
indentation and **`m`** is the auto-detected indent. Those have no bound, and the runtime automaton threads them
through.

## section 3 - What validation still owes

`DESIGN.md` holds the oracles and what they judge. The project owes the validation work below.

- **The refinement obligation.** A commit the determinization makes wants an argument that the commit preserves the
  language of the productions.
- **Structure-aware fuzzing over the grammar.** A fuzzer generates inputs the suite does not hold. The fuzzer targets
  the semantic rules the BNF does not capture. A fuzzer has yet to run here.
- **A written threat model** for the YAML-specific denial-of-service classes. Those are pathological nesting depth,
  unbounded allocation and quadratic blow-up. Alias expansion is not among them. A token parser emits an alias and does
  not follow it. The pushdown shape removes C-stack overflow by construction. `ys_options::max_bytes` bounds the input,
  the tokens held with it and the depth the production stack reaches. The option is a single cap over those bounds
  rather than a depth cap of its own. The threat model owes the argument for a single cap. It owes the cases behind that
  argument too.

## section 4 - Implementation milestones

A milestone is a body of work the project delivers. A *phase* is the normalization pipeline's unit. A phase drives the
violations of a single invariant to none. The numbers in section 4 name milestones. `normalize._PHASES` names the
phases.

Dependency orders the milestones. **Milestone 02 is a gate.** That milestone makes the grammar and the fixtures
spec-complete and enforcing. The normalization pipeline touches the grammar and the fixtures only after that milestone.
Milestone 03 then does not have to go back to the grammar. **Milestone 03** is the normalization pipeline, and
determinization happens there. That milestone holds the research risk. Milestone 02 settles the semantic decisions that
feed milestone 03. The codegen and the ABI layer are well-trodden compiler work.

Milestones 01 and 02 are done. Milestone 01 built the reference interpreter. Milestone 02 made the grammar and the
fixtures spec-complete. `DESIGN.md` describes the work of both milestones.

Milestone 01 and milestone 02 leave a pair of things to scope. The first is the interpreter's committed mode. The
interpreter wants that mode when it judges a grammar the pipeline has transformed. The second is the yeast->HTML debug
view. That view bootstraps on the `yaml2html` of Haskell YamlReference. A differential fuzz corpus comes with the debug
view. CI would run that corpus beside the fold.

### Milestone 03 - Normalize, the grammar-to-canonical pipeline that commits a decision

*Risk: High.* Normalize turns the backtracking grammar into a committed grammar. Normalize reaches a canonical form of
the IR through a series of small transformations rather than through a single leap. A transformation preserves the
semantics, and a separate proof covers that transformation. A state machine falls out of that canonical form.

A step preserves the interpreter's token stream over the whole corpus. A step establishes an invariant the steps after
it may lean on. `normalize._PHASES` declares what a phase owns and which steps serve it. `DESIGN.md` says what the
pipeline has reached. The work left is here.

**The work runs in the order below. An item gates the item after it.**

1. **Hold a gate to the space behind it.** `DESIGN.md` owns the space a parse decides in. `DESIGN.md` also owns the part
   of that space a production accepts. The work owes the reverse question. That question asks which part of the space a
   production's *callers* admit. The work also owes the law relating the accepted part to the admitted part.

   - **The gated space** is the states a parse may enter a way in. It is the gated space of the caller. The gate at the
     call site narrows that space. The gated space is a greatest fixpoint that starts from the whole space. It is per
     site rather than per production. A per-site space makes a fault attributable. A report names the caller that
     admitted the state, rather than the way that fails on it.
   - **The law is `gated <= accepted` per way.** A break means something enters a way in a state nothing behind it
     takes. A committed grammar removes that backtracking. The accepted space is sound. A violation is therefore a hole
     the grammar really has. The count of violations is a meter to drive to none. A parse enters no way at all where a
     gated space is empty. That is a different question from `every-option-is-reachable`. That invariant asks whether
     something can refuse a way in front. Neither answers the other.
   - **The corpus checks a weaker claim than the gated space would support.** `check_normalize` asserts over the space
     grown per production rather than over the gated space above. This item owes the change that points the assertion at
     the gated space.
   - **A gate that refuses too much gets a count rather than a ban.** A gate may refuse a state that a way behind it
     would have taken. Such a refusal loses a parse rather than causing a backtrack. The same pair of spaces decides
     such a refusal. A production refusing what it could have taken is a way the caller declines to offer. A committed
     machine counts that refusal as a fault.

1. **Cut by the exit space.** The pipeline already reads the spaces themselves. This item owes a pair of cutting
   invariants, a count apiece, and a step that cuts the ways those invariants find. The first says a parse enters a call
   only in the states where it enters the calling way. A way's entry may have nothing in common with the entry of its
   callee. That call is then a call no parse makes, and the ways behind it are ways nothing reaches. The second says a
   parse can enter a way's continuation at the point where the way's call hands control back. That is the exit space
   intersected with the continuation's entry. A space computed too wide goes uncaught until one of the invariants
   exists. The assertions over real parses catch a space computed too narrow.

   Somebody measured the cut's worth on a grammar the pipeline does not hand on. That grammar has the continuations
   lowered into the conflicts. The pipeline has no step that lowers a continuation into a conflict. On that grammar the
   cut shrank the counts of the invariants the pipeline still owes. Those are `every-choice-way-is-different` and
   `every-conflict-is-a-tail-call`. The fixtures and the suite cases still agreed. Against the grammar the pipeline does
   hand on, nobody has measured the cut.

1. **Settle `every-choice-way-is-different`.** The ways have gates and no pair of them half overlap. A pair of ways the
   input cannot tell apart can remain. A parse enters both ways of such a pair in exactly the same states. A machine
   reading a character gives the same answer on both ways, and takes the way that comes first. The items above settle
   first. A pair then collides through a shared entry state. The question then needs no test that tells a state in an
   overlap from a state just outside it.

   `every-called-alternative-is-unconditional` belongs here rather than to the gating phase. A step takes a callee's
   gate out to the calling ways and puts a guard over the actions the caller performs before the call. The design for
   the work still owed is *Determinize, what remains* and *The provisional mechanism* below. That design covers the
   block-structure factoring, the speculations and the vocabulary they share. **Once the invariants of this phase report
   no fault, the grammar is deterministic.** Milestone 04 needs a deterministic grammar.

   A gate the pipeline makes *relocates* a guard. `hoist-guards-to-gates` and `hoist-guards-to-callers` move guards up.
   `merge-gate-peeks` merges them. `split-consumes-into-gates` mints a gate from a set inside the way itself.
   `flatten-ungated-call-trees` reaches down for the gates of the productions a way can run. `split-overlapping-ways`
   cuts a way by the atoms its choice refines into. The pipeline mints no gate from the characters a way *accepts*. The
   pipeline tells the ways of a choice apart by guard rather than by the characters a way takes. That is the gap to
   close where a pair of gates admit the same character and the spaces behind them differ.

1. **Settle `every-conflict-is-a-tail-call`.** The text after a call may decide a conflict the input cannot decide. Such
   a decision wants the call at the end of its way. A step putting a conflict in that position is still to come.
   `normalize.lower_continuations_into_conflicts` makes the move a way at a time, and sits outside the pipeline. Over
   the whole grammar the move takes the invariant *up*. The move copies productions, and a copy holds the call sites its
   original held. A step has to justify those copies. The first answer is a rule that folds without minting a copy per
   site. The second is an argument that a copy's call sites are decidable where the call sites of the original were not
   decidable. *The empties* below wants the same fold for eps elimination, and comes at the duplication from the other
   side.

1. **Adapt the C parser to the canonical grammar** (Milestone 04).

An invariant belonging to no phase is still to come. **`no-conditional-production-matches-empty`** has no test. Nobody
has counted the ways the grammar breaks that invariant. The paragraphs below read the shapes of the grammar.

A way like that may have a gate already. A gate decides and the way then takes nothing. That is not itself a fault. Such
a way costs the grammar tightness. A way may have no gate. Its caller then accepts the union of the way's space and the
continuation's space. The spaces widen wherever such a way appears.

A callee may match empty. A parse enters a way at the current position. That way calls such a callee. The way accepts
the character at that position. The gate says nothing about which way the input wants.

A phase re-implements what it needs rather than inheriting anything. A step stays where it earns a place. A step sitting
between a pair of phases arrives with the phase that needs it.

**The questions come in order.** A machine that does not backtrack needs a gate on a way of a choice. That machine asks
the gate before entering the way. That machine also needs the gates to be exclusive. The gate and the exclusion are
separate problems. The first has landed, and `every-conditional-way-is-gated` finds no ungated way at the end of the
pipeline. The second is *Settle `every-choice-way-is-different`* above. The paragraphs below design that item.

**Where the gate lift belongs.** `every-called-alternative-is-unconditional` is about a way that calls a production
where the way's gate sat. That callee offers a single way, and that way asks a question of its own. The rule belongs to
*Settle `every-choice-way-is-different`* rather than to the gating phase. The gate admitted the caller on the character
at the gate's position. The callee's question can refuse a character the gate admitted. The lifted question belongs at
the caller. The lift holds where the callee begins at the gate's position. The `first` call begins there. So does a tail
call that no action precedes. A way may continue past a call. Such a way resumes at the position that call reached. A
tail call placed after a character the way took begins past that character. A gate above names neither the resumed
position nor the position past a taken character.

The lift cannot happen yet, and a measurement says so rather than an argument. A guard reaching the caller's gate has to
pass the actions the caller performs before the call. On the path the guard refuses, those actions did not happen. A
`PushCode` did not cut the token run, and a `PopMessage` did not leave the committed region. The lift moves nothing
until the gate and the call sit adjacent. The caller's actions need a state of their own first.

`every-choice-is-deterministic` is the exclusivity question and splits in half. A choice of the first kind offers a way
in front of a fallback that nothing enters. A choice of the second kind offers ways that admit the same input. The order
of those ways decides the choice. Factoring and speculation settle a choice of the second kind.

A gate says what it admits per axis, and an axis is independent of another. The axes are the character in front of the
parse and the character behind it. A further axis is whether the parse sits at a line start. Another axis is the
indentation the parse sits under. A gate tells a choice apart on the indentation the same way it does on the character.

**A marker net that follows the pipeline.** The parse holds the scope pairs to their shape. A half names the pair it
belongs to, and the parse refuses a close whose pair differs from the open on the stack. The scope pairs take that
shape, and the markers take another. A marker pair crosses productions by design. `b-chomped-last` emits `end-scalar`
for a `begin-scalar` opened elsewhere. Holding a marker pair to a single way would report a fault where there is none.
`check_markers` proves the `begin` and `end` balance of the grammar as authored, and does not follow the pipeline.
Minting the continuations first puts a `begin` in a production and its `end` in another. The project owes a marker net
that follows the pipeline.

**Of the lookarounds, the shape phases leave the exclusions owed.** A peek asks about a single character, and
`every-peek-is-a-character-set` holds it to a `CharSet`. A peek of a set *is* the zero-width guard the canonical gate
holds. The canonical gate tests a bit against the key the decoder already made. A look-behind guard reads a register
holding the last character. `is_sol` is that register.

The exclusions are the remainder. They ask for `c-forbidden`, and an exclusion may ask for `s-indent-le-line` as well.
`s-indent-le-line` means "a line at this indentation with content". The machine's steps bound `c-forbidden` and
`s-indent-le-line`, and the run of spaces is a single consume. `every-exclusion-is-bounded` checks that bound.

The project still owes a way to ask a condition *on* a line start. A condition on a line start differs from a condition
on the text after a line start. The block-structure work makes a line start a decision the grammar writes. The way to
ask a condition on a line start lands with the block-structure work.

`DESIGN.md` holds the vocabulary. The vocabulary is the words gate and peek and guard. It is the canonical form the
pipeline takes the grammar to. It names the values the parse may hold. It names the rules a transformation obeys. Those
terms describe the generator rather than its work. The emitted parser matches the IR's action set node for node.

**Settling `every-choice-way-is-different` comes in small steps.** Fold residual single-token lookahead into gates.
Left-factor shared prefixes into a common gate and a branch. Repeat that until a decision turns on a single character.
Insert provisional speculation wherever no character decides.

A single character leaves the folding decision open. It leaves the document prefix and the simple-key line open too. It
leaves the plain scalar's next line and the block scalar's empty lines open as well. An open decision spends the actions
that *The provisional mechanism* below formalizes.

The mechanical steps miss a pair of indentation gotchas. A writer handles that pair by hand. The first is a block
sequence at indentation `0` nested directly in a mapping. The second is flow context. Flow context suspends indentation.
A writer discharges commit-safety at a decision point. A point left open goes in the log as an assurance gap. Last, an
assertion holds a terminal to a pure char-set.

**The list below orders the work the project still owes.**

1. *The call written out last, after the meter reads none.* `first` names a call, and `second` names the continuation.
   The machine performs the call at the end. The grammar does not write the call. `PushContinuation(second)` and a jump
   to `first` write it. A pop of that continuation with a jump says the way home.

   A push or a pop then has an action naming it. An inlining then deletes a push and a jump that hold no value. The C
   parser is a state machine with its globals, a single stack and the pending tokens. While a call stays a field the
   grammar does not write, codegen decides where a push goes. That decision is the pipeline's job rather than the
   consumer's job. So the pipeline writes the call out as an action, and does so last.

   - *Why last.* The move takes the continuation out of a typed field and puts it in the action list. `normalize.py`
     reads `second` throughout. `_asked_where_entered` reads it for the place something enters a production.
     `_called_first` reads it for the production a way enters first. A walk that follows a way's end reads it for the
     place a way hands that end on. The questions the determinizing asks rest on those readers. A step that adds a read
     of `second` makes the move cost more.

     The steps this item adds gain a side condition too. A prefix factoring takes the longest identical run of actions,
     and could take a push away from the jump it belongs to. A splice moves action runs. Moving a push moves the stack
     position of the continuation. A phase that writes the call out first pays for moving a push once per step. A phase
     that writes the call out last pays nothing. The steps after that phase reshape no way.

   - *The grammar already guards the call this assertion would check.* The grammar shows that no way continues over a
     call taking an indentation off. That holds while a call is still a field. The machine's kind assertions are the
     stronger answer. Until the machine arrives, the parse runs without such an assertion.

1. *Splitting `monomorphize`, noted here against a rediscovery.* The step specializes `c`, `t` and `r` in a single pass
   over their combinations. A pass of the generic operation per parameter would give the same grammar. The diff would
   then show a step per parameter. The step makes the copies per combination. The split wants care it has not earned.

1. *The new steps, earliest and simplest first*. Nobody has written such a step. The pipeline reaches none of the shapes
   the items below name. The new steps derive a shape rather than taking a shape over. Such a step gathers the pieces of
   a shape into a single production. A piece is neither orderable nor comparable while it sits in a different
   production.

   - *Inlining under a gate* gives a call only the ways its caller's gate can reach. Inlining splices the surviving
     way's actions into the caller once a single bare way remains. A caller's gate may already imply the callee's peek.
     The callee then takes the side condition "the callee's peek contains the caller's peek" rather than "the callee has
     no gate". `c-chomping-indicator_t_keep` is such a callee. Its gate is the `'+'` the caller admits. Widening the
     side condition puts the chomping consume into the header lists.

   - Even then the block header does not factor. The ordering side condition below needs a non-break consume *earlier in
     the same list*. `c-l+folded` consumes the `|` and the `>` of the scalar. That happens a pair of calls up. Either
     the `c-l+folded` call gets inlined too, or another route satisfies the condition.

   - *Ordering the actions.* Within an action list an action moves as early as it may. That leaves a canonical order the
     later steps compare with `==`. The side conditions come off the list. A scope action does not cross its partner. An
     emitter does not cross a consume or another emitter, and the stream's order is the output. A position-reading
     action crosses a consume whose set excludes line breaks. An earlier consume in the same list excludes line breaks
     too. That leaves the at-line-start bit clear on both sides. A value-reading action does not cross an action that
     writes the value it reads.

     The third condition is where the block header's difficulty lives. The auto-detected indent reads the position
     through a single bit. The pair of orderings run the position a character apart, and the bit is already clear. A
     consume that ran a pair of calls earlier took the scalar's indicator. That consume has to reach the list before a
     rule can see it.

   - *Factoring a shared leading call* into the prefix. *Holds where the call is identical in name and arguments across
     the ways and their gates are equal.* That is the shape the steps above create.

   - *Subsuming a way* whose later twin has the same actions and calls but for trailing zero-width guards. The narrower
     way dies, and the streams are identical by construction.

   - *Merging a pair of ways of a choice* that are equal once a step has ordered the actions. The step compares the ways
     with plain equality. The step is the within-production twin of the sweep's behavioural merge. That merge reaches
     whole productions and stops there.

   - *Dropping a dead way* whose gate is disjoint from the characters a parse can enter its production on. *Reads the
     root-down entry sets the meter computes.*

   - *Sinking a pop.* A pop holder is an ungated way whose actions are only the pop. The step moves the pop into a
     called production whose callers are pop holders without exception. The pop then leads that production's ways, and
     the holders stop popping. *A wrong pop then shows at a single place, where the sunk pop enters.* Run to a fixpoint.
     Sinking makes holders. A continuation whose arguments read the indentation keeps its holders. A step reads those
     arguments before the pop rather than after. A production a parse enters by name keeps its holders too. So does a
     recovery, and a cut reaches a recovery with the stack as it was before the call. Nor does a way that calls *and*
     continues hand its pop down. The pop would then find the continuation rather than the indentation it comes off.

   - *Inlining a call whose production holds actions and no jump.* A call buys nothing and costs a push. Inlining
     matters where the caller continues after the call. The caller's way then pushes a continuation ahead of the call.
     An indentation that ends inside the call pops back to the continuation's level. The indentation's own level goes
     missing. A step that splices the production in runs the actions before the way pushes anything.

   - *Hoisting a push out of a production whose ways begin with the push without exception.* The callers make it
     instead. The push comes last among that caller's actions. Then a pop and a push of a single level side by side are
     nothing, and both go. *A gate consumes nothing. A gate reading the indentation can refuse. The failing path then
     gives back the hoisted push.* The push comes off a **copy**. A caller wants a production that does not make it. A
     parse entering by name wants a production that still does. The gain is the block collections pushing once before
     the loop and popping once after the loop. The alternative pushes and pops per entry.

   - *Stripping the level off a pop*, once the last step to read it has run. With the level left in place, the pop reads
     the names it holds. The auto-detected indent is among those names. That keeps a value live where the parse has no
     use for it.

   - *Deferring a pop* that leads a way of a production to the end of that way's actions. The step rewrites an action's
     expression to `Indent` where that expression equals the level the pop restores from. *The stack holds that level
     until the pop runs. `Indent` then reads the same level a single action later. The level the pop itself names keeps
     that measurement unchanged.* The pop crosses actions and no more. An alternative's calls run after those actions. A
     callee therefore faces nothing new. An action that is itself a call or a scope around a call stops the move. The
     move is for the pop right against the push that follows it, with no consume in between.

   - A later step may merge a pair of consecutive clears, or drop a clear behind another. A clear then does not block a
     factoring that would otherwise see a common prefix.

   - *Pruning a parameter a production does not need*, with the argument a call passed it. A production needs what
     reaches a read. That is the gate and actions of the production, and the argument it hands to a production that
     needs the parameter. A write counts too. A binding a production does not declare belongs to the call. *The step
     computes a least fixpoint. A parameter a chain of productions only relayed dies through the chain at once.*

   - After any step above, ask the question from the *nothing is singled out by name* rule of `DESIGN.md`. Does the step
     want a hand-picked target? A step orders the actions, and a step factors out a shared call. Together these steps
     should leave a pair of orderings equal. Equal orderings leave no site to swap or to single out. A step that leaves
     the orderings unequal wants a universal rule rather than a named site.

1. *The empties, and no step of the pipeline holds them.* `no-conditional-production-matches-empty` says no production a
   parse enters on a choice matches empty. A production that a parse enters by name is exempt. Those are the root's copy
   under a resume policy, the copy `l-recover` holds, and the copy a `(recover)` names. A parse reaches such a copy
   without a call. Such a copy holds no choice a call site could have taken.

   Once somebody writes the count, it covers the productions offering a blind choice. That choice is between a way that
   reads and a way that does not read. A production matching empty with a single way, or with the ways it offers, is a
   shape the canonical form mints on purpose. The invariant below allows it. The step moves the empty match a node
   sideways into an inline choice. The first step that gives a choice a production of its own hands the empty match
   back.

   **A single operation does the work.** An eps does not travel. It *dies* where a neighbour reads. `Seq(a, (X|eps), b)`
   is `Alt(Seq(a,X,b), Seq(a,b))`. Both ways read where `a` or `b` reads, and the eps is gone. So "move the empties to
   the root" is really this. Push an eps outward a node at a time. It evaporates the moment a reader turns up. A node
   that survives to the top of a production's body belongs to that production. That production hands the eps to its
   callers. The steps below push an eps outward, and a step holds an invariant of its own.

   1. **`explicit-empties`.** An emptiness is an `AltTree` holding a zero-width way. `x?` becomes `(x | eps)`. `x*`
      becomes `(x+ | eps)`. A sequence of zero-width parts already is such a tree. *Invariant. A node matches empty only
      as an `AltTree` with a zero-width way.*

   1. **`distribute-empties`.** Push an `AltTree` holding a zero-width way outward, a node at a time. A sequence turns
      `SeqTree(..., AltTree(X, z), ...)` into `AltTree(SeqTree(..., X, ...), SeqTree(..., z, ...))`. Through an
      alternation, flatten. Through a wrapper the step wraps the ways, and a `(token)` or a `(<<<)` or a `(commit)` is
      such a wrapper. A way then keeps a whole pair.

      **Not** through a repetition. `(x|z)*` is not `x*|z*`. A repetition already means as many as there are, and none
      is a legal count. It absorbs. **Not** into a lookahead or a difference, where a choice is a pattern rather than a
      decision. *Invariant. An `AltTree` with a zero-width way is the body of a production, and a deeper node holds
      none.* Absorption wants no step of its own. The rule above does it on reaching a reader.

   1. **`lift-empties`.** A non-root body's zero-width way goes to its call sites. Drop it from the body and write
      `(RefCall(P) | residue)` at a reference. That makes a fresh inline `AltTree`, and the step above runs again.
      *Invariant, and the goal. A parse enters a production by name where the production pairs a zero-width way with a
      way that reads.*

   The distributing and lifting steps iterate to a fixpoint. The iteration **terminates**. A round moves an eps strictly
   up the call graph, and the step cuts a cycle. An empty match whose route back is itself is an infinite parse rather
   than a way. The iteration also **converges** rather than treadmilling. The absorbing step runs before the re-creating
   step.

   **An eps moves up where the call leads its way, and that is the wall.** `A ::= X C` with `X ::= D | eps` distributes
   to `A ::= X C | C`. `X` has nothing before it. A part runs at most once. `A ::= F X` has `F` before `X`. Taking eps
   out gives `A ::= F X | F`. The second way runs **`F` a second time** where the first way's `D` fails. That is a
   different parse where `F` has more than a single parse. The rerun pushes a second time where `F` pushes.

   Nor is there another way to write `A ::= F X`. Something makes the choice between `D` and nothing where `F` returns.
   `second` names what runs there. A production minted to hold that choice *is* `X`. **This shape is the wall.** A blind
   choice outside this shape has an empty way that calls something, or an empty way with a gate.

   **So pull the continuation down instead of pushing the eps up.** `X ::= A Y` with `A ::= B C` and `C ::= D | eps`
   becomes `X ::= B Z` with `Z ::= D Y | Y`. The fold puts the trailing `Y` into the callee's family, where it sits
   beside the eps and both ways read. `normalize.lower_continuations_into_conflicts` already makes that move. It mints a
   copy of the callee that runs the continuation after the callee's own parts. The other callers go untouched. That
   function aims at a conflict rather than an eps. It is out of the pipeline for having no invariant. This item wants
   the same move, aimed by the rule below.

   1. **Write the invariant first, and watch it fail.** It names a production reached by a way with a trailing
      continuation. The tail chain of that way ends in a choice holding an empty way. The invariant counts the distinct
      callee-and-continuation pairs. A pair costs a copy.
   1. **Drive the fold from the invariant.** The invariant says where the fold applies. A step names no site of its own.
   1. **The eps then dies to the rule above.** A reader then follows the way that held the eps. The elimination logic
      stays put.
   1. **Then climb outward.** A site with nothing trailing makes its *caller's* tail end in the blind choice. The caller
      is the next candidate. The climb outward stops at a root, and a root may have an empty way.

   **What it buys beyond the elimination.** A copy with its continuation has **a single follow**. That is the
   precondition a general prefix-extraction step needs and does not have. Such a step must refuse a conflict reached
   with more than a single follow. A speculation cannot resolve a conflict that has silently gained a second caller. A
   copy with its continuation serves the prefix-extraction step and the speculation alike.

   **Unknown, and to read before anybody builds it.** Whether the fold generalizes past the pairwise shape is open. The
   reach of a fold along the tail chain is open too, and the count there is of pairs rather than copies. The direction
   the meter moves in is open too. A copy is a production with decision points of its own.

   **The shape of the work.** A step that lifts to the call sites and does not push *makes* inline empties rather than
   removing them. The step that gives an empty a production again undoes that. The pair are inverses, and the nullable
   population sits at a fixed point. Absorption breaks the circle. Absorption takes an empty way into the sequence
   around it. It does so where the sequence then makes a way that reads. The eps dies rather than gaining a new name.
   Absorption is for the case `l-comment ::= s-separate-in-line (c-nb-comment-text)? b-comment`. Distribution then hands
   what survives to the call sites.

   **The ordering constraint falls out.** The step that gives a choice a production of its own waits for the absorption
   to finish.

   **The cost is bounded.** A sequence holds few parts that split. The product stays small before absorption. Absorption
   then takes, at once, a product way whose surrounding sequence reads. A residue is an action chain rather than a bare
   eps. The residue's contents decide whether it moves. A residue with `PopCode`, `RetypeProvisional` or
   `CommitProvisional` may move. Those pair dynamically off the stack of the parse or the queue's run. They read nothing
   the call holds. A residue with a `CloseWindow` may not move. A window pairs statically rather than off the stack.

   The other sketches in this section answered the product. The bound above says the product was not the problem. That
   sketch split sequences toward binary first, and associated outward from a reader. The text below still holds.

   - *The invariant is that no production offers both a way that reads and a way that reads nothing.* It is not "nothing
     matches empty". A single-way action bundle matches empty and decides nothing. A continuation with a `PopMessage` is
     such a bundle, and a guard the canonical form gives its own production is another. The canonical form mints those
     deliberately. The rule that nothing matches empty would forbid such a bundle.
   - *A checker comes after a step from the elimination on*, rather than at the step that establishes the invariant. It
     counts the blind choice rather than the ways that match empty. So the invariant breaks at the step that gives an
     inline `Alt(reads, empty)` a production of its own. A declared lapse records that break. The distributing and
     absorbing steps pay the lapse down. A broad checker that counts the ways matching empty measures the wrong thing.
     It names steps that mint nothing beyond a single-way bundle, and the invariant permits that. A lapse written for
     such a step goes stale the moment somebody narrows the checker.
     - The checker needed fixes before it could read at all. `_is_nullable` knew the pre-canonical vocabulary and
       nothing beyond it, and refused a `ChoiceState`. The check could not run past `build-alternatives`. Reading an
       alternative means reading its parts. The gate and its guards take nothing, and a recovery is no way an
       alternative offers. That leaves the actions and the pair of calls. The repetition half read `node.item` on a
       `TrimStar`, and a `TrimStar` names that field `full`. That stayed latent for as long as the check ran at a single
       stage.
   - *Lowering a run of none or more breaks the invariant by construction.* `_N ::= x _N | <empty>` is a production that
     decides between consuming and consuming nothing. The lowering wants a run that must take a turn instead. Such a run
     reads `_N ::= x _N | x`. The site the old run had keeps `_N | <empty>`. The elimination then distributes that
     `_N | <empty>` like any other.
   - *The elimination must not hand back what `lower-runs` removed.* Write the consuming form of a run of none or more
     as a run that must take a turn. The node the lowering took out is then back. That form has to reach the vocabulary
     in place where the step runs.

1. *Then the certificates.* The certificates take what the elimination leaves undissolved. Nobody can guess the count
   before the elimination has run. A subsumption certificate is a way whose language contains a later way's language.
   The certificate reads that containment through a single level of inlining. That certificate retires the assurance
   ledger's remaining entries rather than leaving them declared.

   The points that are neither greedy optional nor ledger want the breakdown the greedy optional has. That breakdown is
   cheap and comes before anybody designs for such a point. The breakdown would put a derived number on both halves. The
   number written there came from no computation.

1. *And then the determinizer.* The determinizer takes what the meter still flags. The landings below say how.

**Determinize, what remains.** The goal is a grammar that is deterministic **as invoked from the root**. A production
need not be deterministic at a hypothetical entry. A root parse reaches a production under contexts. A production
undecidable in isolation is no conflict where those contexts decide the matter. The checker for that goal inlines a
single level deep.

So the meter counts root-reachable decision points. The meter judges a production under the follow of a context that
reaches it. A root-down pass computes the contexts as follow classes. The milestone's work goes on until the meter
counts no decision point. The meter then becomes a gate.

The meter over-counts the greedy optional. That optional holds a call and then an empty match, and its rival consumes
nothing. Branch order and the callee's sureness settle the choice there. Character disjointness plays no part. The
project owes a certificate for that choice first. The other decision points move into the landings below a case at a
time. A corpus diff justifies a move.

The speculations form a single piece of work rather than several. *The provisional mechanism* formalizes the vocabulary
these cases share. A determinizer produces the speculations rather than a hand cut, and *The synthesis* describes the
determinizer.

The fold is the engine's calibration. The block-structure substrate is the line run and its consumed column, and that
substrate lands first among what remains. A trailing run that can end at an indented dedent hands its last line to a
parent. The reference puts an exiting construct's end markers before the dedent line's spaces. The parent then consumes
those spaces as its own indentation. Suppose a committed consume at the dedent ate those spaces. The parent then cannot
restore the end markers, and cannot say which construct owns the tokens that consume took.

The block scalar's empties ride the substrate as the engine's first speculation target. The document prefix and the
implicit key land after, and they spend the same shared consume.

1. The block-structure work comes first, and it answers the indented dedent. A run of small provable moves does that
   work rather than a single surgery.

   A block line start can go to this level's next entry, or to a deeper construct's line, or out by an exiting level's
   way. Those ways begin with the line's spaces. So the spaces are a common prefix, and a step extracts them once ahead
   of the decision. The decisions behind the shared prefix become character gates with column guards.

   A move is a language identity, or has a single mechanically-checked side condition. An intermediate grammar is
   corpus-green.

   - *Indent refinement* is the enabling move. The pipeline step applies wherever a check proves its side condition. An
     exact count of spaces followed by a non-space is a maximal consume judged after the fact, and the grammar writes
     the count that way. `s-indent-le` already has that shape. `s-indent(k)-X` becomes
     `OpenMatch-ConsumeSpan(space)-[Len(Match)==k, as the Le pair]-CloseMatch-X`. The rewrite holds wherever space is
     not in FIRST(X) and X cannot match empty. Maximal munch then has nothing to steal.

     The measure is the `(match)` scope of the consume. The rewrite is position-independent, and reads the production's
     nodes and the FIRST table and no more. The `<n` and `<=n` variants already have this shape. The rewrite is
     stream-faithful. Paths consuming the same spaces under the same code accept, and the token count matches.

     The gain is a shared prefix. Indent consumes of different `k` share no literal prefix. The refinement makes them
     the identical consume. The differing counts become residual guards.

   - *Aggressive common-prefix extraction*. The pipeline does none of it. The step that extracts an identical prefix
     from the ways of a choice is still to come. The refinement above makes indentation identical. The extraction runs
     until the ways share no factorable prefix. The stop is a named blocker below rather than a shrug.

     A pair of admissions grow the shared prefix. An identical maximal consume joins the prefix where the grammar knows
     a leftover's accepted space. That space must not match empty, and it must exclude the scanned characters. A shorter
     run then leaves a character no leftover admits. The maximal run proceeds. The factoring reorders nothing. A
     `(match)` scope's opening joins where the minted leftover production declares the origin. That production takes the
     origin of the caller. That origin is the exact twin of the `code` parameter. The closing half then restores what
     the unfactored close restored.

     A leftover compares its leading indentation, and those comparisons rise into the guards of a gate. A guard judges
     such a comparison at the same position, where the certificates read the guards. The pair of admissions and this
     rise make up the local factoring. The local factoring covers the seam within a production.

   - *Seam moves* run on demand, at conflicts the meter flags. A blind application does no more than duplicate
     productions. A single generic transform covers the seam moves. The transform folds a call-then-continuation. The
     continuation's actions run inside the family of the call. The fold appends those actions to a return path through
     minted copies. A tail recursion folds to its own copy. Reassociation, distribution and the tail-fold come in a
     single walk.

     An application is a corpus-held identity. The seam absorbs a helper at a time rather than in a single atomic flip.
     The first target is the sequence loop's exit. That exit takes its `end-sequence` a helper nearer the parent's
     consume. The chain continues through the wrapper and entry helpers until the parent's consume is local to the
     conflict.

     The mapping loop's exit goes beside the sequence loop's exit. Absorbing the end-marker helper leaves the way that
     held it a single call. A way with a call *and* a continuation cannot hand its pop down. The continuation goes on
     the stack ahead of where the pop would land. Absorbing the seam therefore also lets a pop sink to the consume of
     the loop. The sunk pop then reads no `m` a nested write has since replaced.

   - *Held-prefix factoring* is the move that is not an identity. The provisional mechanism enters at that move.
     Factoring the consume across a zero-width emission, `(Emit-I-x | I-y)`, would commute the marker past the indent
     token in the stream. The commuted order is the dedent's marker order. An exiting level's end marker comes before
     the dedent line's indent.

     The stream-preserving completion is the hold. The line run holds the consume's spaces. A line start re-takes the
     mark. The factored-out emission becomes an injection before the held indent at resolution. The parse spends it
     where the meter demands and stops there. A held run is runtime buffering.

   - The project set aside a hand-built rewrite in `junk-surgery/`. That rewrite is the oracle these moves calibrate
     against for the sequence loop. The composed identities must reproduce that rewrite. The dedent fixtures the project
     pinned already check that match.

1. The block scalar's opening and trailing empty lines ride the factored line starts. A line run holds the breaks and
   held indents of those lines. A line start re-takes the mark. The line run resolves into content or into the scalar's
   end. The parse injects that end ahead of the line run.

   Monomorphize makes the chomping split already. `l-literal-content` and `l-folded-content` have a copy per `t`. The
   fusion reaches through a callee layer, and that layer stays `t`-shared. That layer holds `b-l-folded` at block,
   `l-empty`, and the empties chains.

   A writer re-reads the chomping rules off the reference when the work lands. Under strip the run opens before the
   chomped last break, and `end-scalar` goes in at `start`. There is no retype where the scalar ends, and a break goes
   to `line-feed` where content follows. Under clip the last content break is `line-feed` in both meanings. The run
   opens past it and strip's shape follows. The table below gives clip a static mark, and that mark should dissolve.
   Under keep both meanings write a break `line-feed`, and the line runs remain to decide.

   A dedent exit belongs to the substrate. A level injects its own end markers at the mark, and the parent owns the last
   line. The engine work rides along. The engine roots a production whose callers are not unique. The engine reads the
   injection off the divergence. A single path emits a marker there, and the other path holds tokens. The engine turns
   the non-converging walk into a runtime loop. The fold's hand-built empties loop already has that shape.

   The opening empties weave in the auto-detected indent and the `(increase)` floor the leading empties set. The block
   fold fuse lands last in this item. The fuse covers `b-l-spaced`, `l-nb-spaced-lines` and the block `b-l-folded`
   sites.

1. The document-prefix speculation is the positional injection's first exercise. The comment loops between documents
   hold whites that both meanings claim, and both meanings agree on the spans of those whites. The speculation settles
   the codes of those whites and where the markers go. The code is `white` where a comment line owns the whites, and
   `indent` where a block collection does. `begin-document` and the node markers behind it come ahead of the whites.

   So the line opens a provisional run and holds the whites. A `#`, a break or the end of the input decides the comment.
   Anything else opens a document, and the `:` decides or refuses its shape.

   The simple-key speculation resolves that run. The pair are a single landing rather than a pair in that order. The
   landing does bounded eager buffering for the key line. It resolves key-vs-scalar at the `:`, or at the break that
   refuses the key. It sits inside the `(max) 1024` window the grammar already has. It covers the consuming half of
   `l-document-prefix`, and `l-trail-comments`, and the implicit-key sites.

1. The plain scalar's next line asks whether a multi-line scalar continues at all. It is the same held-break read. It
   lands with the key's read, and both resolve at a line's end.

1. Fuse the separation per site where a decision hides past optional separation and the paths do not reconverge. Those
   are the properties' separate-then-tag, and the flow key and value entries' separate-then-indicator. A measurement
   takes them at the site. The fusion is fold-style, and needs no retype where the separation's codes agree either way.

   A measurement takes that agreement off the reference per site before anybody builds the fusion. The document prefix's
   whites disagree. A measurement found `white` on a path and `indent` on its rival. A site whose codes disagree wants a
   retype, and belongs with the speculations. Generic absorption does not exist here. The continuations do not open on
   the bare separation the exit's rival consumes.

1. Build the assurance ledger once the surgery settles the minted names. The ledger holds declared order-commitments
   with their reasons. The commitments are the fused fold consumes, the document loops if the surgery leaves them in
   place, and the order-only residue that remains. The ledger refuses an entry the grammar lost or the analysis has
   since proved. The gate prints a count of the ledger's entries, like the vendored spec's declared deviations.

1. Detection rather than a name points the determinizer. A landing above hands the engine a conflict its step names. The
   fold's `b-l-folded_c_flow-in` comes first, and the empties' sites come next. Naming the conflicts is scaffolding
   rather than the end state. The group the meter flags defines the engine's input. The landings prove that group case
   by case. Then the pipeline's determinize step walks the conflicts the meter finds and fires on them, and the name
   lists go. The names left are the assurance ledger's entries with their reasons.

1. The validator becomes the gate. The deferred trim-reuse pass lands beside it.

**The provisional mechanism**, formalized. A speculation takes this shape. A run is a contiguous stretch of pending
tokens. The parser builds a run and holds it. Nobody sees the run until it resolves. A span settles the moment the
parser consumes its characters, and the parser does not rewind input.

A resolution settles the codes those tokens take, and which decided markers go among them. It drops no held token and
grows none out of held characters. So the meanings a run decides between must agree token for token and span for span. A
difference between the meanings must be a zero-width marker. That is the mechanism's single obligation, and the cases
below are where a speculation discharges it.

`ir.py` defines the provisional actions per node. The first step that writes a net brings a balance net over the
actions. Other analyses read an action as zero-width. Such an analysis cannot see an open inside a run or a mark outside
a run. It cannot see a retype or an injection naming a mark nobody took, or a commit with no run.

The cases below lean on a pair of properties. Injections and retypes commute. A retype reaches held tokens only, and an
injection adds decided tokens only. A resolution issues injections and retypes in the order that reads best at the site.
**Runs do not nest**, and no case asks them to.

The multi-line flow key keeps runs flat. `[1,` and then a break cannot be a key at all. The spec restricts a key to a
single line. So the break that opens the fold's run is the same break that resolves the run of the prefix.

**The first batch of cases, and what they resolve to under the formalism.** A row comes off the reference interpreter.

| Speculation                                         | The run's contents                                          | The decider                                                                                                     | Injections                                                                                                                      | Retype                                                                                                            |
| --------------------------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| The flow fold, `s-flow-folded`                      | the break, and the follower's indent and whites             | a break is an empty line. Anything else is content, and the input's end counts as content                       | none                                                                                                                            | content gives `(None, line-fold, all)`. Empty gives none                                                          |
| The document prefix and the implicit key            | the line's whites, and the key's tokens where a key may go  | `#`, a break or the input's end belongs to the comment. A `:` makes the line a key, and a break refuses the key | a document gives `(begin-document ..., start)` and the node markers the shape needs behind it. A key gives `(begin-pair, mark)` | a key or a block collection gives `(indent, None, before_mark)`. Anything else gives none                         |
| The plain scalar's next line                        | the line's breaks                                           | content at the follower continues the scalar. A marker, a dedent or the input's end ends it                     | ends: `(end-scalar end-node, start)`                                                                                            | a single break continues with `(None, line-fold, all)`. More breaks continue with `(None, line-feed, after_mark)` |
| The block scalar's opening empties                  | the breaks before any content, and the indents between them | content at the scalar's indentation. Anything else leaves the scalar empty                                      | empty: `(end-scalar, start)`                                                                                                    | content gives `(None, line-feed, all)`. Empty gives none                                                          |
| The block scalar's trailing empties under `t=strip` | the trailing breaks                                         | the chomping its header fixed                                                                                   | `(end-scalar, start)`                                                                                                           | none                                                                                                              |
| The same under `t=clip`                             | the trailing breaks                                         | the chomping its header fixed                                                                                   | `(end-scalar, mark)`                                                                                                            | `(None, line-feed, before_mark)`                                                                                  |
| The same under `t=keep`                             | the trailing breaks                                         | the chomping its header fixed                                                                                   | none                                                                                                                            | `(None, line-feed, all)`. The scalar's end comes past the commit                                                  |

**The second batch of cases, and how the grammar reaches them.**

1. *The flow fold* is the engine's calibration. The site fuses by name. A pair of signatures move under the fold and no
   more. The retype names `all` and the injection goes unused.

1. *The document prefix and the implicit key* are a single landing, and the pair share a single run. The comment loops
   fuse at their line start. The parse opens the run, consumes the whites, and marks past them. A `#`, a break or the
   input's end continues the loop and commits, and the comment keeps the whites. Anything else leaves the loop for a
   minted document entry. That entry emits nothing the injection supplies, and does not consume the whites a second
   time. That is the fold's consumed-prefix discipline.

   The implicit key rides the comment loops' run to the `:` that makes a key, or to the break that refuses the key. The
   key stays inside the `(max) 1024` window `ns-s-implicit-yaml-key` and `c-s-implicit-json-key` already have. The run
   covers `l-document-prefix`, `l-trail-comments` and the implicit-key sites.

1. *The plain scalar's next line* opens its run at the break that ends a content line. The follower's gate decides that
   run. The minted continuation does not emit the scalar's end where the injection supplied it.

1. *The block scalar's empties* want the loop productions split per chomping first. That holds for the opening empties
   and for the trailing empties. The loop productions are `t`-shared, and the tail's codes differ. A branch's resolution
   is then a row above.

**The synthesis. A determinizer derives the provisional productions rather than a hand cut.** A speculation above is the
output of that determinizer. The input is the group the meter flags. The group holds the live alternatives at an
overlapping-gate choice point. The determinizer prunes the group to the minimal set no gate can separate. A single
alternative decides nothing.

The method is subset construction. Walk the live alternatives in lockstep on the input, and read the divergence between
them. The divergence falls under a kind listed below, and a kind maps to a single output.

- A character the paths consume over the same span but under different codes stays held. The resolution retypes it to
  the code of the surviving path.
- A path may emit a zero-width marker that another path does not emit. That marker waits, and the resolution injects it.
  A marker emitted before an already-held token waits too. A marker ahead of the held tokens goes at the run's start. A
  marker among the held tokens goes at the mark. The resolution keeps the emission order of the path that survives.
- A path may consume a space as more indentation where another path has reached its level on that same space. A
  measurement takes it as an indent comparison of space against the first non-space. Such a space resolves to an
  indentation comparison on the measured column rather than to a held token. The parse consumes the indent once and
  holds nothing. The paths diverge on whether more indent follows, and the code stays put.
- The character on which the paths' gates first differ is the discriminator. The run commits there to the path that
  survives it.

The first pair of outputs are the provisional actions, and the third is a guard. So a single divergence analysis yields
them, with a pair of output kinds rather than a pair of mechanisms. A divergence in a token's code or presence yields a
hold and an injection. A divergence of a position against a level yields a guard.

The alternatives give the facts below rather than a hint. The conflict root, the held token and the retype code come off
the alternatives of the grammar. So do the injected markers with their order and side, and the mark itself. The walk
finds the structural boundary where the shared prefix ends, and places the mark and the injections there as actions. At
runtime the mark and the injections record the live queue position by themselves. The runtime counts no tokens and
threads no index. The placement in the production is the position.

The walk converges to a straight-line region. It may instead recur without converging. Recurring is the signal for a
runtime loop rather than a fault. The block scalar's opening empties are the unbounded case, and the fold's consume is
the bounded case.

The block-structure surgery is no second mechanism beside the engine. The surgery applies the engine's indentation
output to the block-collection loops. That output is the third divergence form above. A block-collection loop continues
or exits on the space it compares against the first non-space at the collection's level.

A pair of things make this facet costly. The same pair keep the facet a distinct body of work under the same principle.
A dedent can cross nested loops on a single character. The inner sequence exits and the outer continues at the same `-`.
To stay committed the parse must consume the indent once at the line start and read it by zero-width guards across the
nested loops. That is a consume *shared* between conflicts the engine otherwise resolves a case at a time. It is not the
local fix a single-level fold needs. And the parse detects the level itself. The first entry sets `n+m`, and the run
establishes the column the guard compares against, and the synthesis does not fix that column.

The flow fold, the plain scalar's next line, and the block scalar's empties are pure output-deferral and need no shared
consume. The document prefix and the implicit key need the shared consume, and land after it.

A pair of nets hold the engine's output. The first is the certificate that a decision turns on a single character. It
comes with the engine, and names the productions the engine tells the interpreter to enter committed. The second is the
hybrid corpus proving the stream identical, and that corpus exists already. So a wrong synthesis fails loud rather than
silent. The engine then wants no proof beyond the certificate on its result.

The fold calibrates the engine. The table above gives the fold's resolution in full. The engine re-derives the held
break and the `line-fold` retype from the raw conflict, and takes no hint. The engine first fires at the block scalar's
empties. That case has an unbounded run and the injected `end-scalar`. It also has the retype split over clip, keep and
strip.

A static mark to a run looked like enough. The dedent exit after trailing empty lines proved otherwise. That site wants
end markers at the last line's boundary. A rule reading line by line learns which line is last only after passing that
line. The rule therefore takes the mark again at a fresh line, and the last mark taken names the spot. A run can resolve
at a line-end boundary. The winning mark's position then serves such a boundary. The corpus settles coverage.

**The validator** is the target invariant and equals "done". A production is a terminal character set or an ordered list
of canonical alternatives. A `StarTree` or a `PlusTree` does not survive. Nor does an `OptTree` or a `DiffSet`. Nor does
a `TokenWrapper` or a `Wrapper` or a `CaseTree`. A `LookGuard` or `NegLook` does survive. A gate writes its guards as a
`LookGuard` or a `NegLook`. An alternative is gate-led with at most a pair of calls and nothing past the second. A
decision point is commit-safe. The invariants `normalize.STEPS` already names hold the shapes above. Those invariants
leave commit-safety out.

**The verification net** is the reference interpreter. Its backtracking mode already diffs the token stream across a
stage. The committed mode is still to come in full. That mode enters the productions a step proves, and takes the first
alternative whose gate holds. Once the determinize steps land, both modes must agree across the corpus. A divergence
means a gate is not commit-safe. The structural invariants and the backtracking mode cannot catch such a gate between
them.

**Exit** is a canonical grammar the validator passes. Both interpreter modes agree on that grammar across the corpus. A
speculation resolves its run correctly. A test exercises a speculation that defers its decision. Emitting the C state
machine in Milestone 04 is then mechanical rather than clever.

### Milestone 04 - C codegen

*Risk: Low - ~1-2 mo.* This milestone is the easy end of a compiler. Turn the lowered IR into a switch-on-state
character loop with arena allocation.

1. Emit the state dispatcher and the transition tables into `src/parser_tables.h` as portable C99 with no external deps.
   The emitted code runs over the runtime `src/parser.c` already provides.
1. Emit, per state, the production it belongs to. Emit what its outgoing edges expect. That is a table of static strings
   shaped like the table in `src/messages.c`. The text of a format error goes with that table. The parser's finding sits
   outside the table and the error text, and stays outside. The first `unparsed` token behind an error begins at exactly
   the byte that failed.
1. Arena-allocate what the parse holds. Lifetimes are input-bounded. Free the arena on parser teardown, and no GC runs.
1. Allocate the scratch state of a backtracking region within the arena. The parse reclaims that state when it discards
   a provisional branch.
1. Emit the pull surface. The surface holds `new`, `next_token` and `free`. It also holds a structured error extraction.
1. Emit the `Code` enum and the compose fold from yeast to node graph. Trace mode retains events. The committed hot path
   emits and consumes without buffering.
1. Migrate `yaml2html` into the package. It is a C program that folds the yeast stream to colorized nested HTML. It
   shares the emitted `Code` enum. `yaml2html` then ships *with* the library and needs no Haskell YamlReference
   dependency. Validate byte-for-byte against the Haskell YamlReference renderer.
1. Build as `cdylib`-style `.so` across Linux and macOS, and Windows once the toolchain is clean.

**Exit** is a self-contained C `.so` plus the bundled `yaml2html` tool. The `.so` passes the suite, the differential
harness and the fuzzer.

### Milestone 05 - ABI layer

*Risk: Low - ~3-5 wks.* YAMLStar built the existing ABI as a swappable seam. It is thin, and JSON strings go in and out.
It exposes no structs. So this is nearly free. The existing bindings work unchanged.

1. Reimplement the entry points over the new core. Those are create, destroy and `version`. `load` and `load_all` come
   with them.
1. Route `load` through the yeast fold `compose -> resolve -> serialize`. Reuse YAMLStar's existing resolver and dumper
   rather than reimplementing them. Ship the other consumers of the same stream. Those are the bundled `yaml2html` debug
   view and the differential harness.
1. Keep GraalVM-era lifecycle calls as cheap no-ops or lightweight context handles. They are vestigial and harmless.
1. Serialize errors into the exact shape the bindings parse back out. That shape is a type, a cause and a message.
1. Reproduce the JSON-interchange contract faithfully (including its documented `.inf`/`.nan` limitation) for true
   drop-in behaviour.
1. Run the existing binding test suites unmodified against the new `.so`. Those are Python and Go, Rust and C#, and the
   other bindings.

**Exit** comes when the new `.so` slots in where the GraalVM blob sat and the bindings stay green.

### Milestone 06 - Harden. Fuzz, tune, and reach libyaml-class speed

*Risk: Medium - ~2-4 mo.* Correct-but-slow is not the goal. Close the algorithmic gaps naive codegen leaves and prove
robustness under hostile input.

1. Fuzz the parser continuously under ASan and UBSan, at the byte level and with structure-aware inputs. Aim the fuzzers
   at the semantic long tail.
1. Profile. Eliminate any residual super-linear behaviour from over-broad lookahead.
1. Benchmark against libyaml on representative corpora. Tune hot states and buffering.
1. Build the release library with link-time optimization. The dispatch takes a token at a time. The compiler then
   inlines the dispatch across the translation units holding it.
1. Optionally, extend to an emitter for dumping. Or keep libyaml's emitter alongside for a complete round-trip library.
1. Cut prebuilt binaries per platform. A local native build then stops gating adoption.

**Exit** comes when the parser runs in confirmed O(n) at libyaml-competitive speed. The parser also runs fuzz-clean and
ships as a package.

## section 5 - Risk register

| Risk                                                                 | Milestone | Severity | Mitigation                                                                                                                                                                                                                                 |
| -------------------------------------------------------------------- | --------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Malicious input triggers memory-unsafety or resource-exhaustion DoS  | any       | \*\*\*\* | Hardening flags on the release build. ASan and UBSan on a run. Structure-aware fuzzing. `ys_options::max_bytes` as a single cap. A continuous security audit rather than a final pass.                                                     |
| A step in the normalization pipeline silently changes the language   | 03        | \*\*\*\* | Reference IR interpreter diffs the token stream before and after a step. The committed mode catches an unsafe gate the backtracking mode cannot. Dual differential oracles against Haskell YamlReference and YAMLStar. Log assurance gaps. |
| Naive codegen is correct but super-linear                            | 03 / 06   | \*\*\*   | Milestone 03 discharges commit-safety per decision point. Profiling and hot-state tuning in milestone 06.                                                                                                                                  |
| A pipeline step is subtly non-semantics-preserving and slips the net | 03        | \*\*\*   | Keep a step small enough to prove by eye. Assert its structural post-condition. The interpreter corpus-diff is the behavioural backstop.                                                                                                   |
| Arena and backtracking scratch leaks or corrupts                     | 04        | \*\*     | Input-bounded lifetimes. ASan and UBSan in CI. Discard provisional state through the arena only.                                                                                                                                           |
| Incumbency, where 1.1 quirks are load-bearing in real configs        | -         | \*\*     | Out of scope to "fix" silently. Position as a conformance upgrade, and document behavioural deltas from libyaml and from YAML 1.1.                                                                                                         |

## section 6 - Future work

The project wants the items below. Nobody has planned them, and no work waits on the plan.

- **Lenient wire positions.** Treat a `#` line in the wire as a comment rather than a required field. Take the token
  position from a line of the form `# B: ..., C: ..., L: ..., c: ...`. Estimate a missing position from the tokens. Give
  a token nobody can place an obvious "no position" value rather than rejecting the wire. A reader then accepts a
  hand-written wire, or a wire somebody stripped of its position lines.

- **Token-emission levels.** A knob in `ys_options` chooses how much of the stream `ys_read_token` emits. The levels run
  coarsest to finest, and a level is a superset of the level before it.

  - The structure markers by themselves, the `begin-` and `end-` pairs that bracket the productions.
  - The payload as well. That is the content characters, and the default emits them.
  - The non-payload characters as well. Those are the indentation and the separation. They are also the breaks and the
    indicators. The stream at this level then covers the input byte for byte.
  - The detection values too. Those are `YS_CODE_DETECTED` tokens. Such a token holds the `m` or `t` that an indentation
    or chomping rule computed. They make libyeast's detection comparable to Haskell YamlReference's `Detected` output.
    The comparison is token for token.

  So `YS_CODE_DETECTED` is in the vocabulary already. libyeast will emit that code at the finest level. Until then the
  wire round-trips the code. A coarser level is cheaper. A caller loading a document wants that coarser level. The
  differential oracle and a debugger want a finer level.

- **An event-projecting token source.** It is a `ys_token_source` that wraps another and is a source itself. It hands
  back the event-level tokens and no more. Those are the stream and document markers. They are also the mapping and
  sequence markers, and the scalar and alias markers. A scalar's value comes already folded, with its anchor and tag
  attached. A `line-fold` becomes a space, a `line-feed` becomes a newline, and the fold resolves an escape. The
  projection drops the node and pair brackets. It drops indicators and indentation. It drops whitespace and breaks.

  The Python fold the YAML Test Suite runs through has a C twin here. The event stream is a subset of yeast. The
  projection is a filter over the markers, plus the mechanical value fold the codes already settle. A caller wanting
  YAML events rather than tokens reads them straight, and composes no node graph. The projecting source drops in
  wherever tokens already flow.

- **Arena allocators.** Revisit the `ys_allocator` API against arena and pool allocators. Those free the whole arena at
  once rather than buffer by buffer. Ask whether a no-op `deallocate` is enough as it is, or the shape wants a variant.
  Ask whether a source can arrange its allocations to let a caller drop the whole parse in a single free. The `close`
  hook is already the seam for tearing such an allocator down.

- **libc version portability.** Deal with the libc-version issues a shared library faces. Those are which symbol
  versions the built `.so` pulls in, and their minimums. A binary built against a newer toolchain then still loads on an
  older target. The ABI-compat goal is a libyamlstar drop-in. A libc symbol-version bump must not quietly break that
  drop-in.

- **Optimization and benchmarking of the C implementation.** That goes past the tuning that reaches libyaml-class speed.
  It wants a permanent benchmark suite over representative corpora. Those are deep nesting and long scalars, wide
  collections, and flow-heavy and block-heavy documents. The suite runs per build. A regression then shows up at the
  build that caused it rather than later.

  The interesting comparison beside libyaml is against **JSON parsers**. JSON is a subset of YAML, and its parsers are
  among the quickest structured-text readers in use. The ratio between libyeast and a JSON parser measures the cost of
  YAML's indentation and folding and deferral.

  The comparison also splits the cost of YAML between the grammar and the generated machine. A JSON-shaped document read
  by libyeast exercises little of libyeast's speculation. The gap that remains on such a document is the automaton's
  overhead.

- The project builds binaries as well as a library. The binaries are yaml2yeast (resume policy in ARGV) and yeast2yaml
  (filtering policy in ARGV). They are also yeast2html (based on Haskell YamlReference). They are yaml2event and
  yeast2event.

## section 7 - Shape of the whole

The difficulty in the remaining work is lumpy rather than uniform.

- **The low research risk.** This part holds the remaining structural steps of the normalization pipeline, the C
  codegen, and the ABI layer. Such work is well-trodden compiler work and wants high effort.
- **The hard part.** This part holds the determinize steps. They reduce a decision to a commit-safe single-character
  gate and stay faithful by construction. Those steps decide whether the result is worth more than a hand-written
  machine. The refinement obligation of section 3 falls due in those steps.

## section 8 - YAMLStar upstream notes

libyeast finds faults that belong upstream. This list names them. An item is a fix or an addition to YAMLStar, or to the
test suites YAMLStar validates. An item says where libyeast found the fault.

- **A test that appends a line break the input lacks.** YAML Test Suite `JEF9/02` is an empty kept block scalar whose
  input ends in no line break. The spec folds it to the empty scalar. `b-chomped-last` is where end-of-input counts as a
  break. A scalar with no content line does not reach that group. `l-keep-empty`'s `l-empty` needs a real `b-break`.

  YAMLStar adds a line break to the end of the input before it parses. The suite case expects the line break that
  YAMLStar added. The case tests that altered input rather than the file on disk. libyeast declares the difference in
  `check_star.DIVERGENCES`. The suite should hold the input without a trailing break as a separate case. That case
  expects the meaning the spec gives.
