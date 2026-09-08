# Conventions proposed and rejected

A review proposes a convention and the author turns it down. The entry below records why. **An entry here comes back as
no finding and as no proposal.**

A record here is why a review converges. A proposal ruled on in conversation and written down in no file comes back the
next round, and the round after that. A recorded proposal closes for good. So the ruling goes here in the same turn as
the author makes it. "We discussed it" is no record.

A rejection passes no verdict on the taste behind the proposal. A rule as stated with no bright line yields a finding on
any module large enough. Such findings come without end. The author turned down the entries below on those grounds. **A
proposal may return in a narrowed and decidable form.** The wording below closes, and the subject stays open.

`.claude/conventions.md` holds the conventions this project *does* hold to.

______________________________________________________________________

1. **`Two names that resemble each other must be held apart by a rule.`** The reviewer put it in the forms below. A
   name's suffix must say the shape it returns. That form cited `normalize._conflicts`, `_conflicted_ways` and
   `_conflicted_productions`. A pair of names in a module may not differ only by an inflection. That form cited
   `interpreter._measuring` beside `_measured`, and `check_documents._named_by_the_c` beside `_named_by_the_code`. It
   cited `gate.defined_names` beside `names_defined_in_modules`, and `spaces._comparisons` beside `_compared`. A helper
   may not share a stem with a function answering a different kind of question. That form cited `check_documents._prose`
   beside `_code_prose` and `_c_prose`. A pair of functions naming a single idea must order their words alike. That form
   cited `gate.defined_names` beside `names_defined_in_modules`. *Rejected.* These forms draw no line. The rule decides
   no suffix for a new shape. It decides no distance a pair of names must keep, and no right word order. A module of any
   size has such pairs, and a shared stem tells a reader that a pair of things are about a single subject. A factory and
   the step it makes take the same word in differing forms. The form that does decide is `a-name-is-not-shadowed`, and
   `pylint` `W0621` holds it.

1. **`Two things declared together must use the same comment style.`** The reviewer cited a pair of
   `interpreter.Emitter` fields. A trailing comment sat on a field, and a comment above sat on another. *Rejected.* A
   comment's length decides its style, and `make reformat` moves a comment between the styles. A rule the formatter
   would break is no rule.

1. **`A module-level function's name is not also a local variable in the same module.`** The reviewer cited
   `review_input._written` beside the local `written`. *Rejected as stated, and the decidable half accepted separately.*
   The cited names differ by the underscore. This is about near-collision rather than shadowing, and no rule decides how
   near is too near. The exact form is `a-name-is-not-shadowed`, and `pylint` `W0621` holds it already.

1. **`A module-level helper that reads the repository through a subprocess and takes no argument is memoised at its definition.`**
   *Rejected in this wide form, and accepted narrowed* as `a-repository-question-with-two-callers-is-asked-once`. The
   wide form applies to `review_input._staged_paths` and `_unstaged`. It applies to `code`, `docs` and `fixtures` too.
   `_staged_paths` has more than a single caller. The other helpers have a single caller, where a cache buys nothing and
   adds a module global apiece. The proposal was about a pair of callers getting differing answers.

1. **`A universal may be written once its check has been run.`** The reviewer proposed an escape hatch in `prose_words`.
   That hook refuses `every`, `never` and `always`. It refuses `nothing else` and `the only` too. The hook offers no way
   to say a writer checked the tree. It had refused `on every turn` in `standing-rules.sh`, where the claim held.
   *Rejected.* The model writes these words freely and does not come back to correct such a word when it stops holding.
   Chasing them afterwards is the cost the ban removes. A sentence wanting the word gets rewritten to say WHICH ONES, or
   the clause goes.

1. **`The prose checker covers a string literal.`** The reviewer raised this after `check_decoder`'s error message and
   `check_vendor_spec._DEVIATIONS` turned out to hold em-dashes no hook sees. `prose_written` holds docstrings and
   comments, and single-quoted literals across the tree hold a pile of faults. *Rejected.* A literal is data as often as
   it is prose. `(match)` and `<column>` and a format string are code. A message writing
   `not the yeast wire format: expected a token position line` has its colon by design. Telling those apart from prose
   needs a judgement no regex makes, and the declared exemptions would outnumber the findings.

1. **`Prose replacing prose does not come out longer than what it replaced.`** The reviewer proposed a `PostToolUse`
   hook on `Edit`. That hook would compare the word count of prose in `old_string` against the count in `new_string`.
   *Rejected.* The discipline is right, and `.claude/hooks/standing-rules.sh` injects it as a rule on a turn. A word
   count buys nothing the rule does not buy. The count fires on a correction wanting more words than the text it
   replaces.

1. **"A `continue` or `pass` that is the last statement of a loop body comes out, along with the condition guarding
   it."** The reviewer cited `check_conventions._cached_names`. *Rejected.* The cited `continue` is an early skip with
   code after it. A trailing no-op is a different shape. A guarded or bare `continue` ends a loop in `gate.modules()` or
   `gate.hook_modules()` at no site. `pylint` `W0107` holds the shape where such a statement appears.

1. **`A gate holding a declared-exemption list in both directions reports it through one shared helper.`** The reviewer
   cited `check_conventions._check` beside `check_failures._check`. *Rejected.* `check_failures._check` holds no
   exemption list. `check_conventions._check` holds the two-direction loop, and a helper for a single caller is no
   helper.

1. **"The universal checker in `prose_rules` fires on the absolutes by themselves, and a distributive `each` or `every`
   passes."** The reviewer raised this after the tree came back red on `each` and on `every`. *Rejected.* A named set
   does not make a claim checkable. `This holds for every tea leaf in china` names its set, and a reader can settle
   nothing. The rewrite that says WHICH ONES is the fix, and the escape-hatch form above closed the same subject.

1. **"`check_prose` drops `claim_faults` and leaves the claim rules to the write-time hooks."** The reviewer argued that
   a reworded sentence dodges the regex and keeps the claim. *Rejected.* The rewrite is what the rule is for. The hook
   stops the shape as the writer types the text, and the gate holds the tree to the same bar. A writer who rewords a
   sentence to say WHICH ONES has fixed that sentence rather than disguised it.

1. **"`prose_words` refuses a bare `nothing`, `none`, `no more` and `only` wherever they appear."** Readers of the
   linguistic pass proposed it. *Rejected.* A negation in subject position claims about a class. A negation in object
   position denies a property of a single thing. The subject checker in `prose_rules` takes the first form. A sweep of
   the tree found the second form reading `the grammar has no say in it` and `it names no allocate callback`. It reads
   `an error consumes nothing` and `blocking nothing` too. Refusing those refuses plain English.

1. **"`prose_shape` refuses a clause after `, and` that holds no finite verb."** The reviewer cited
   `The block scalar's opening empties are the unbounded case, and the fold's consume the bounded one.` *Rejected after
   building it.* The tail checker grew wider. It stripped a leading conjunction and then asked for a verb. The cited
   sentence stayed unreported. Its tail runs past the length a hung phrase may take. The checker refused
   `A copy is made, and the caller frees it` instead. `_A_FINITE_VERB` is a list of verb forms rather than a checker. A
   clause holding `frees` then reads as verbless. The subject returns with a way to tell a verb from a noun.

1. **"`prose_shape` refuses a form of `be` followed by a past participle. It also refuses a sentence opening on an `-ed`
   word and a preposition."** A pair of readers proposed it. Both were aiming at the passive voice. *Rejected in this
   wide form, and later accepted narrowed.* A passive naming its agent reads well, as in
   `counted by the way rather than by the item`. The wide form refuses that too. The narrowed form asks whether a `by`
   follows the participle, and it refuses the passive where none does. `prose_rules` holds that form as
   `a-sentence-names-its-actor`. The author ruled it in after watching the checker catch a run of agentless passives in
   a single fragment. It costs a large sweep, and the author took that cost knowingly.

1. **"A rewrite may not take a long run of words over from the prose it replaces."** The reviewer raised it after round
   upon round of patching faulty prose until the pattern stopped matching. A hook would compare the sentences an edit
   takes out against the sentences it puts in. It would refuse a shared run past the cap, where the old prose had a
   fault. *Rejected after building the hook and running its own oracle.* Rewrites the author had ruled on went through
   the hook. The hook refused a rewrite the author had accepted. It passed a rewrite the author had refused. The refused
   pass is fatal. That rewrite got through by reordering a list. The reorder kept the noun pile and broke the word run.
   Permuting is not writing. Permuting is the move the rule exists to stop. A later form asks whether a sentence of the
   old prose survived the edit. That form fails the same way, a sentence at a time, and it refuses a rewrite that
   legitimately lands on a sentence that was already good. A rule comparing new prose to old prose is routable in
   principle. `faulty-prose-is-re-said-not-patched` replaced it. That rule judges the new prose without the old, and
   refuses opaquely.

1. **"A part-of-speech tagger. The shape rules could then ask about a verb rather than about a word."** The reviewer
   proposed it to unblock a run of rules at once. `_A_FINITE_VERB` is a list of words, and `frees` then reads as a noun.
   `_AN_ABSOLUTE_CLAUSE` matches an `-ing` suffix, and `a binding` then reads as a participle. *Rejected.* A tagger is a
   better trap, and a trap is the thing this routes around. The author's ruling is that piling on checkers has no end to
   it. The subject may return once a rule exists that a tagger would decide and that nothing routes around.

1. **"The pronoun count in `prose_rules` covers `this`, `that`, `these` and `those` beside the personal pronouns."** The
   reviewer proposed it to catch `over the prose this hands it`, where `this` points at a module no sentence names.
   *Rejected after measuring it.* `that` is a relative pronoun far more often than a demonstrative here. The checker
   would refuse `a claim that may be false, and a grep settles it` and
   `a number that depends on the tree is refused as it is written`. Both read well. `this` before a noun is a determiner
   and reads well too, as in `This hook names the word it refuses`. The count dropped to a single personal pronoun
   instead. That reaches the same class without the false refusals.

1. **"`prose_shape` refuses a bare demonstrative as the subject anywhere in a fragment."** The reviewer proposed it
   alongside the fragment-opener rule. *Rejected.* The opener is where the fault lives, and `fragment_faults` takes it.
   A demonstrative later in a fragment points at the sentence before it, and that is what a demonstrative is for.
   `That is what lets a rejection be tested at all` names its referent a line up.

1. **"`everywhere` and `zero-width` are terms of art. `prose_rules` exempts both words."** The reviewer proposed it
   after `spaces.EVERYWHERE` and the grammar's zero-width guards kept faulting. *Split, and the `everywhere` half
   rejected.* `zero-width` names a guard that takes no width, and it went into
   `check_documents._NOT_A_NUMBER_OF_THE_TREE` with that reason. `everywhere` stays a refused universal. A claim about
   the whole tree is what the rule exists to catch. The constant is `spaces.COMPLETE`, and prose that wanted the word
   says `admits throughout` instead.

1. **"The critic drops its vague-quantifier item."** Proposed on the grounds that `prose_rules` already refuses that
   class. *Rejected on measurement.* `_VAGUE` lists words rather than reading the class. `many`, `various` and `often`
   pass it. `CHANGELOG.md` holds "Many pairs of ways" under a green gate. The item stays, and it names what the machine
   takes and what the reader takes.

1. **"`prose_rules._VAGUE` widens to `many`, `various`, `often` and `much of`."** Proposed after "Many pairs of ways"
   passed the gate. *Rejected on measurement.* The tree writes `many` inside `how many lines it runs`. It writes `often`
   inside `as often as the input allows`. Those are an interrogative and a comparative rather than a quantifier. The
   tree writes `various`, `numerous` and `plenty of` in no fragment at all. A list of words would refuse what reads well
   and catch words nobody writes. Telling `Many pairs` from `how many` wants a judgment, and the critic makes it.

1. **`A clause with no finite verb is refused past the tail the checker takes.`** The critic put it in the forms below.
   Drop the word cap on a tail opening on `with`, `over` or `for`. Ask about any comma rather than the last one. Ask
   about the whole sentence. Refuse a sentence ending on `not`. *Rejected.* A way of putting it decides `holds a verb`
   by `_A_FINITE_VERB`. That name holds a list of verb forms. The entry above closed that checker once already. A clause
   holding `frees` still reads as verbless. The subject returns with a way to tell a verb from a noun.

1. **`A word list refuses a decorative sentence.`** The critic put it in the forms below. The list would hold
   `is where`, `is when` and `is why`. It would hold `is the point`, `and so on` and `There is`. It would hold
   `notoriously`, a word repeated across `is a`, and a comparative with no `than`. *Rejected.* The comment over
   `_UNIVERSAL` in `prose_rules` argues against this shape. A list of words is a game of whack-a-mole, and a writer
   refused `is where` writes `is the place`. The count rules survive the same objection. A count is a class.

1. **`A sentence ending on a demonstrative or a pronoun is refused.`** The critic put it in the forms below. The words
   cited were `this`, `that`, `these` and `those`. `either`, `it` and `them` came up too. *Rejected on measurement.* The
   tree ends sentences on those words, and on `it` more than on the other words. The fault the critic points at is a
   referent outside the sentence, and no character rule sees that. The checker would refuse prose that reads well.

1. **`A pair of fragments of one file may not share a run of four, of six or of eight words.`** *Accepted at the widest
   run, built, and taken back out.* A shorter run reports more pairs, and the shortest form reports far more than the
   widest. The widest form found pairs where both fragments say something the other leaves out. A checker cannot
   separate a repeated claim from a shared premise. Sibling docstrings rest on a shared premise and draw a different
   conclusion from it. That shape is what the checker kept finding. The groups repeating a run across a batch of
   fragments were worth fixing by hand, and the other reports were pairs. The checker also wanted a line-keyed exemption
   list, and a line key goes stale when an edit above it moves a line.

1. **"`prose_rules` refuses a semicolon."** *Rejected.* `prose_rules` refuses a semicolon at `_over`, and `check_prose`
   holds the tree to that refusal.

1. **`A colon is refused where its left side runs to fewer than three words.`** The reviewer raised it against the
   `Makefile` comment writing `1` and then a colon and then `it rewrote a file`. *Rejected.* A short left side comes in
   shapes the checker cannot tell apart. `README.md` writes a bold term leading its line, and that form passes. `ir.py`
   writes docstrings naming a grammar operator in a code span, and those pass. The `Makefile` comment is the appositive.
   A single checker refuses them together. That comment is an ordinary finding instead.

1. **`word_faults refuses most where the word behind it is not of.`** *Rejected, and the other form accepted.*
   `at most once` states a bound and `the most rounds` names a top, and this refuses both. `most of what governs a step`
   is the quantifier, and this spares it. The accepted form reads the word in front of `most` instead.

1. **`shape_faults refuses a sentence whose first word is Its, Their, His, Her or Theirs.`** *Rejected.* The opener
   points at the sentence in front of it, and that is what a possessive pronoun is for. `include/yeast.h` writes
   `the byte source. Its read callback must be non-NULL`. `DESIGN.md`, `CHANGELOG.md` and the C tests write the same
   shape throughout. `counting_allocator.c` drew the fault. Its referent sits further back than the sentence before. A
   checker over words cannot tell that apart from a referent a sentence back. A narrowing that spares a fragment's first
   sentence came next, and the author turned it down on the same grounds. The `yeast.h` opener is a second sentence with
   a referent behind it. The narrowing refuses that opener too.

1. **`A sentence begins with a capital letter, unless it begins with a code span or an identifier.`** The reviewer
   raised it after the em-dash sweep left `normalize.py` writing `handed the grammar. it is a claim rather than a step`.
   *Rejected.* The tree opens sentences on a lower-case letter throughout, and a bare identifier is what they open on.
   `Makefile` writes `ctest runs the tests for correctness` and `clang-tidy is clang-based`. `README.md`, `DESIGN.md`
   and `SECURITY.md` open sentences on `libyeast`. `annotated2ir.py` documents a field with `the values t may take`.
   Telling a bare identifier from a word needs a roster of the names the tree and its tools use. The entry above turned
   that roster down under `The prose checker covers a string literal`. The author fixed the `normalize.py` messages as
   ordinary findings instead.

1. **`The word rules spare a quantifier the printing code has verified.`** The proposal covered a gate verdict. It
   covered an `ir.Question` description and an `ir.rounds` name. It covered a fault message. The words `each` and
   `every` there run over the set the code walks. *Rejected.* The author reworded them instead. A reader of the message
   cannot see the walk. The message has to state the claim on its own. The tree already says it another way.
   `check_conventions` reports `what a gate can decide holds`. `check_prose` reports
   `the tree says nothing a write-time hook would refuse`.
