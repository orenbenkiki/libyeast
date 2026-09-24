# Conventions proposed and rejected

A review proposes a convention and the author turns it down. The entry below records why. **An entry here comes back as
no finding and as no proposal.**

A ruling recorded here lets a review converge. The author may rule on a proposal in conversation and write the ruling in
no file. That proposal then comes back the next round, and the round after that. A recorded proposal closes for good. So
the ruling goes here in the same turn as the author makes it. "We discussed it" is no record.

A rejection passes no verdict on the taste behind the proposal. A rule with no bright line yields a finding on a large
module. Such findings come without end. The author turned down the entries below on those grounds. **A proposal may
return in a narrowed and decidable form.** The wording below closes, and the subject stays open.

`.claude/conventions.md` holds the conventions this project *does* follow.

______________________________________________________________________

1. **`Two names that resemble each other must be held apart by a rule.`** The reviewer put it in the forms below. A
   name's suffix must say the shape it returns. That form cited `normalize._conflicts`, `_conflicted_ways` and
   `_conflicted_productions`. A pair of names in a module may not differ only by an inflection. That form cited
   `interpreter._measuring` beside `_measured`, and `check_documents._named_by_the_c` beside `_named_by_the_code`. The
   same form cited `gate.defined_names` beside `names_defined_in_modules`, and `spaces._comparisons` beside `_compared`.
   A helper may not share a stem with a function answering a different kind of question. That form cited
   `check_documents._prose` beside `_code_prose` and `_c_prose`. A pair of functions naming a single idea must order
   their words alike. That form cited `gate.defined_names` beside `names_defined_in_modules`. *Rejected.* These forms
   draw no line. The rule decides no suffix for a new shape, no distance a pair of names must keep, and no word order.
   `normalize`, `interpreter` and `check_documents` hold such pairs. A shared stem tells a reader that a pair of things
   are about a single subject. A factory and the step it makes take the same word in differing forms. The form that does
   decide is `a-name-is-not-shadowed`, and `pylint` `W0621` holds it.

1. **`Two things declared together must use the same comment style.`** The reviewer cited a pair of
   `interpreter.Emitter` fields. A trailing comment sat on a field, and a comment above sat on another. *Rejected.* A
   comment's length decides its style, and `make reformat` moves a comment between the styles. A rule the formatter
   would break is no rule.

1. **`A module-level function's name is not also a local variable in the same module.`** The reviewer cited
   `review_input._written` beside the local `written`. *Rejected as stated.* The author accepted the decidable half
   separately. The cited names differ by the underscore. The proposal is about near-collision rather than shadowing, and
   no rule decides how near is too near. The exact form is `a-name-is-not-shadowed`, and `pylint` `W0621` holds it
   already.

1. **`A module-level helper that reads the repository through a subprocess and takes no argument is memoised at its definition.`**
   *Rejected in this wide form.* The author accepted a narrowed form as
   `a-repository-question-with-two-callers-is-asked-once`. The wide form applies to `review_input._staged_paths` and
   `_unstaged`. It applies to `code`, `docs` and `fixtures` too. `_staged_paths` has more than a single caller. The
   other helpers have a single caller. A cache there buys nothing and adds a module global apiece. The proposal was
   about a pair of callers getting differing answers.

1. **`A universal may be written once its check has been run.`** The reviewer proposed an escape hatch in `prose_words`.
   That hook refuses `every`, `never` and `always`. It refuses `nothing else` and `the only` too. The hook offers no way
   to say a writer checked the tree. It had refused `on every turn` in `standing-rules.sh`. The claim held there.
   *Rejected.* A language model writes these words freely. The model does not come back to correct such a word when its
   claim stops holding. The ban removes the cost of chasing such a word afterwards. A writer rewrites a sentence that
   wants the word. The rewrite says WHICH ONES, or the clause goes.

1. **`The prose checker covers a string literal.`** The reviewer raised this after `check_decoder`'s error message and
   `check_vendor_spec._DEVIATIONS` held em-dashes no hook sees. `prose_written` holds docstrings and comments.
   *Rejected.* A literal is data as often as it is prose. `(match)` and `<column>` and a format string are code. A
   message writing `not the yeast wire format: expected a token position line` has its colon by design. A regex cannot
   tell such a literal from prose. A checker would declare more exemptions than it reports findings.

1. **`Prose replacing prose does not come out longer than what it replaced.`** The reviewer proposed a `PostToolUse`
   hook on `Edit`. That hook would compare the word count of prose in `old_string` against the count in `new_string`.
   *Rejected.* The discipline is right, and `.claude/hooks/standing-rules.sh` injects it as a rule on a turn. A word
   count buys nothing the rule does not buy. The count fires on a correction wanting more words than the text it
   replaces.

1. **"A `continue` or `pass` that is the last statement of a loop body comes out, along with the condition guarding
   it."** The reviewer cited `check_conventions._cached_names`. *Rejected.* The cited `continue` is an early skip with
   code after it. A trailing no-op is a different shape. A sweep of `gate.modules()` and `gate.hook_modules()` found no
   loop ending on a guarded or bare `continue`. `pylint` `W0107` holds the shape where such a statement appears.

1. **`A gate holding a declared-exemption list in both directions reports it through one shared helper.`** The reviewer
   cited `check_conventions._check` beside `check_failures._check`. *Rejected.* `check_failures._check` holds no
   exemption list. `check_conventions._check` holds the two-direction loop, and a helper for a single caller is no
   helper.

1. **"The universal checker in `prose_rules` fires on the absolutes by themselves, and a distributive `each` or `every`
   passes."** The reviewer raised this after the tree came back red on `each` and on `every`. *Rejected.* A named set
   does not make a claim checkable. `This holds for every tea leaf in china` names its set, and a reader can settle
   nothing. The rewrite that says WHICH ONES is the fix. The entry
   `A universal may be written once its check has been run` closed the same subject.

1. **"`check_prose` drops `claim_faults` and leaves the claim rules to the write-time hooks."** The reviewer argued that
   a reworded sentence dodges the regex and keeps the claim. *Rejected.* The rewrite is what the rule is for. A
   write-time hook stops a faulty shape as the writer types it. `check_prose` holds the tree to the same bar. A writer
   who rewords a sentence to say WHICH ONES has fixed that sentence rather than disguised it.

1. **"`prose_words` refuses a bare `nothing`, `none` and `no more` wherever they appear. It refuses a bare `only`
   too."** Readers of the linguistic pass proposed it. *Rejected.* A negation in subject position claims about a class.
   A negation in object position denies a property of a single thing. The subject checker in `prose_rules` takes the
   first form. A sweep of the tree found the second form reading `the grammar has no say in it` and
   `it names no allocate callback`. The sweep also found `an error consumes nothing` and `blocking nothing`. A checker
   refusing those phrases refuses plain English.

1. **"`prose_shape` refuses a clause after `, and` that holds no finite verb."** The reviewer cited
   `The block scalar's opening empties are the unbounded case, and the fold's consume the bounded one.` *The author
   built that checker and rejected the proposal.* The author widened the checker of a hung tail. It stripped a leading
   conjunction and then asked for a verb. The checker still passed the cited sentence. The cited sentence has a tail
   past the length a hung phrase may take. The checker refused `A copy is made, and the caller frees it` instead.
   `_A_FINITE_VERB` is a list of verb forms rather than a checker. The list lacks `frees`. A clause holding `frees`
   reads as verbless. The subject may return once a checker tells a verb from a noun.

1. **"`prose_shape` refuses a form of `be` followed by a past participle. It also refuses a sentence opening on an `-ed`
   word and a preposition."** A pair of readers proposed it. Both were aiming at the passive voice. *Rejected in this
   wide form, and later accepted narrowed.* A passive naming its agent reads well, as in
   `counted by the way rather than by the item`. The wide form refuses that too. The narrowed form asks whether a `by`
   follows the participle, and it refuses the passive where none does. `prose_rules` holds that form as
   `a-sentence-names-its-actor`. The author ruled it in after watching the checker catch a run of agentless passives in
   a single fragment. The narrowed form costs a sweep of the tree, and the author took that cost knowingly.

1. **"A rewrite may not take a long run of words over from the prose it replaces."** The reviewer raised it after a
   writer kept patching faulty prose until a shape rule stopped matching. A hook would compare the sentences an edit
   takes out against the sentences it puts in. It would refuse a shared run past the cap, where the old prose had a
   fault. *Rejected after building the hook and running its own oracle.* Rewrites the author had ruled on went through
   the hook. The hook refused a rewrite the author had accepted. It passed a rewrite the author had refused. The pass on
   the refused rewrite is fatal. That rewrite got through by reordering a list. The reorder kept the noun pile and broke
   the word run. Permuting is not writing. The proposal aimed to stop that move. A later form asks whether a sentence of
   the old prose survived the edit. That form fails the same way over a single sentence. It refuses a rewrite that keeps
   a sentence the old prose already wrote well. A writer routes around a rule comparing new prose to old prose.
   `faulty-prose-is-re-said-not-patched` replaced the proposal. That rule judges the new prose without the old, and
   refuses opaquely.

1. **"A part-of-speech tagger. The shape rules could then ask about a verb rather than about a word."** The reviewer
   proposed it to unblock a run of rules at once. `_A_FINITE_VERB` is a list of words, and `frees` then reads as a noun.
   `_AN_ABSOLUTE_CLAUSE` matches an `-ing` suffix, and `a binding` then reads as a participle. *Rejected.* A tagger is a
   better trap than a word list. A writer routes around a trap. The author ruled that checkers pile up without end. The
   subject may return once a rule exists that a tagger would decide. A writer finds no route around such a rule.

1. **"The pronoun count in `prose_rules` covers `this`, `that` and `these` beside the personal pronouns. It covers
   `those` too."** The reviewer proposed it to catch `over the prose this hands it`, where `this` points at a module no
   sentence names. *Rejected after measuring it.* `that` is a relative pronoun far more often than a demonstrative here.
   The checker would refuse `a claim that may be false, and a grep settles it` and
   `a number that depends on the tree is refused as it is written`. Both read well. `this` before a noun is a determiner
   and reads well too, as in `This hook names the word it refuses`. The count dropped to a single personal pronoun
   instead. That reaches the same class without the false refusals.

1. **"`prose_shape` refuses a bare demonstrative as the subject anywhere in a fragment."** The reviewer proposed it
   alongside the fragment-opener rule. *Rejected.* The fault lives in the opener, and `prose_faults` takes the opener. A
   demonstrative later in a fragment points at the sentence in front of it.
   `That is what lets a rejection be tested at all` names its referent a line up.

1. **"`everywhere` and `zero-width` are terms of art. `prose_rules` exempts both words."** The reviewer proposed it
   after `spaces.EVERYWHERE` and the grammar's zero-width guards kept faulting. *The author split it and rejected the
   `everywhere` half.* `zero-width` names a guard that takes no width. The word went into
   `check_documents._NOT_A_NUMBER_OF_THE_TREE` with that reason. `everywhere` stays a refused universal.
   `every-claim-is-checked-before-it-is-written` catches a claim about the whole tree. The constant the proposal cited
   goes by `spaces.COMPLETE` in the tree. Prose that wanted `everywhere` says `admits throughout` instead.

1. **"The critic drops its vague-quantifier item."** The proposal rests on the grounds that `prose_rules` already
   refuses that class. *Rejected on measurement.* `_VAGUE` lists words rather than reading the class. `many`, `various`
   and `often` pass it. `CHANGELOG.md` holds "Many pairs of ways" under a green gate. The item stays. The item names the
   words `_VAGUE` refuses and the words the critic judges.

1. **"`prose_rules._VAGUE` widens to `many`, `various` and `often`. It widens to `much of` as well."** A reviewer
   proposed it after "Many pairs of ways" passed the gate. *Rejected on measurement.* The tree writes `many` inside
   `how many lines it runs`. It writes `often` inside `as often as the input allows`. `how many` is an interrogative
   rather than a quantifier. `as often as` is a comparative rather than a quantifier. The tree writes `various`,
   `numerous` and `plenty of` in no fragment at all. A list of words would refuse prose that reads well and catch words
   nobody writes. A list of words cannot tell `Many pairs` from `how many`. The critic judges that difference.

1. **`A clause with no finite verb is refused past the tail the checker takes.`** The critic put it in the forms below.
   A form drops the word cap on a tail opening on `with`, `over` or `for`. Another asks about any comma rather than the
   last one. Another asks about the whole sentence. Another refuses a sentence ending on `not`. *Rejected.* A form of
   the proposal asks `_A_FINITE_VERB` whether a clause holds a verb. That name holds a list of verb forms. The entry
   above closed a checker built on that list once already. A clause holding `frees` still reads as verbless. The subject
   returns with a way to tell a verb from a noun.

1. **`A word list refuses a decorative sentence.`** The critic put it in the forms below. The list would hold
   `is where`, `is when` and `is why`. It would hold `is the point`, `and so on` and `There is`. It would hold
   `notoriously`, a word repeated across `is a`, and a comparative with no `than`. *Rejected.* The comment over
   `_UNIVERSAL` in `prose_rules` argues against this shape. A list of words is a game of whack-a-mole. The list refuses
   `is where`, and a writer then writes `is the place`. The count rules survive the same objection. A count rule matches
   a class of sentence rather than a list of words.

1. **`A sentence ending on a demonstrative or a pronoun is refused.`** The critic put it in the forms below. The words
   cited were `this`, `that` and `these`. `those` came up too. So did `either`, `it` and `them`. *Rejected on
   measurement.* The tree ends sentences on those words, and on `it` more than on the other words. The critic points at
   a referent outside the sentence. A rule over characters does not see such a referent. The checker would refuse prose
   that reads well.

1. **`A pair of fragments of one file may not share a run of four, of six or of eight words.`** *The author accepted the
   widest run, built the checker, and took it back out.* A shorter run reports more pairs, and the shortest form reports
   far more than the widest. The widest form found pairs where both fragments say something the other leaves out. A
   checker cannot separate a repeated claim from a shared premise. Sibling docstrings rest on a shared premise and draw
   a different conclusion from it. That shape is what the checker kept finding. A report sometimes named a group of
   fragments repeating a run across a batch. Such a group was worth fixing by hand. The other reports named pairs. The
   checker also wanted a line-keyed exemption list, and a line key goes stale when an edit above it moves a line.

1. **"`prose_rules` refuses a semicolon."** *Rejected.* `prose_rules` refuses a semicolon at `_over`, and `check_prose`
   holds the tree to that refusal.

1. **`A colon is refused where its left side runs to fewer than three words.`** The reviewer raised it against the
   `Makefile` comment writing `1` and then a colon and then `it rewrote a file`. *Rejected.* A short left side comes in
   shapes the checker cannot tell apart. `README.md` writes a bold term leading its line, and that form passes. `ir.py`
   writes docstrings naming a grammar operator in a code span, and those pass. The `Makefile` comment holds an
   appositive. The proposed checker refuses the bold term, the code span and the appositive together. That comment is an
   ordinary finding instead.

1. **`word_faults refuses most where the word behind it is not of.`** *Rejected.* `at most once` states a bound and
   `the most rounds` names a top. The proposed checker refuses both. `most of what governs a step` is a quantifier, and
   the proposed checker spares it. The author accepted a form that reads the word in front of `most` instead.

1. **`shape_faults refuses a sentence whose first word is Its, Their, His, Her or Theirs.`** *Rejected.* The opener
   points at the sentence in front of it, and that is what a possessive pronoun is for. `include/yeast.h` writes
   `the byte source. Its read callback must be non-NULL`. `DESIGN.md`, `CHANGELOG.md` and the C tests write the same
   shape. An opener in `counting_allocator.c` drew the fault. That opener's referent sits further back than the sentence
   before. A checker over words cannot tell such a distant referent from a referent a sentence back. A narrowing that
   spares a fragment's first sentence came next, and the author turned it down on the same grounds. The `yeast.h` opener
   is a second sentence with a referent behind it. The narrowing refuses that opener too.

1. **`A sentence begins with a capital letter, unless it begins with a code span or an identifier.`** The reviewer
   raised it after the em-dash sweep left `normalize.py` writing `handed the grammar. it is a claim rather than a step`.
   *Rejected.* The files below open sentences on a bare identifier in lower case. `Makefile` writes
   `ctest runs the tests for correctness` and `clang-tidy is clang-based`. `README.md`, `DESIGN.md` and `SECURITY.md`
   open sentences on `libyeast`. `annotated2ir.py` documents a field with `the values t may take`. Telling a bare
   identifier from a word needs a roster of the names the tree and its tools use. The entry above turned that roster
   down under `The prose checker covers a string literal`. The author fixed the `normalize.py` messages as ordinary
   findings instead.

1. **`The word rules spare a quantifier the printing code has verified.`** The proposal covered a gate verdict. It
   covered an `ir.Question` description and an `ir.rounds` name. It covered a fault message. The words `each` and
   `every` there run over the set the code walks. *Rejected.* The author reworded the texts holding those words instead.
   A reader of the message cannot see the walk. The message has to state the claim in full. The tree already states such
   a claim without `each` or `every`. `check_conventions` reports `what a gate can decide holds`. `check_prose` reports
   `the tree says nothing a write-time hook would refuse`.

1. **`fragment_faults refuses a fragment whose first word ends in ing.`** The proposal cited `src/decoder.c`. That file
   opens on `Decoding a UTF-8 character of 0x80 or above.` *Rejected.* The suffix names the wrong feature. A sweep of
   the fragments found that opener on sound sentences with a subject and a finite verb.
   `Building the C library needs a C99 compiler` is one. So is `Reading is the work rather than a restriction on it`.
   `Existing YAML parsers sit on a horn of a dilemma` opens on an adjective rather than a gerund, and a suffix test
   cannot tell the two apart. The fault in the cited line is a missing verb. `short-plain-sentences` leaves a verbless
   clause with the reviewer. Deciding such a clause wants a part-of-speech tagger this tree does not have. The form that
   would decide is a first sentence holding no finite verb, and that wants the same tagger.

1. **`word_faults refuses the run some of the wherever prose appears.`** The proposal cited `src/decoder.h`. That file
   writes `Some of the character sets the grammar consumes admit non-ASCII characters.` *Rejected.* A sweep found the
   run in prose that can name no members. `include/yeast.h` writes
   `Setting some of the callbacks mixes custom and standard behavior.` That claim covers any strict subset of the
   callbacks, and a list would narrow a true claim into a false one. `.claude/hooks/tense-check.sh` writes
   `Some of these words are legitimate in prose about the parse itself.` The words sit in a list in that same file. A
   sentence naming them goes stale the day somebody edits that list. The word rules reach `CHANGELOG.md`, where the run
   appears in entries nobody should rewrite. The sentence after the cited line names the sets anyway. A list in the
   cited line would name the sets a second time. `a-piece-of-prose-is-written-once` covers that fault.

1. **`shape_faults refuses Also as the first word of a sentence.`** The proposal cited `src/decoder.h`. That file writes
   `Also the text of a document that failed to parse (nb-unparsed).` *Rejected.* The proposal's own rewrite sank the
   proposal. The rewrite offered reads `So does the text of a document that failed to parse (nb-unparsed)`. The sentence
   above supplies the subject and the verb of that line. The old line borrowed the same pair. A checker refusing `Also`
   gets the swap and no plainer sentence. The passage in `src/decoder.h` holds a run of verbless fragments, and
   `Directive names and parameters (ns-char), and anchor names (ns-anchor-char)` is one with no opener to flag. The fix
   is a single sentence naming the sets, and `Also` then goes with the fragments. The word is a poor proxy for a missing
   finite verb. A sweep found `So` opening full clauses across the tree.
   `So do not rule on accuracy, on a cited name, or on a number` is one. `short-plain-sentences` already rules that
   deciding a verbless clause wants a part-of-speech tagger this tree does not have.

1. **`fragment_faults refuses second, latter, former or next in front of a word the fragment has not written earlier.`**
   The proposal cited `src/decoder.c`, and the line `That second range rejects an overlong encoding`. *Rejected.* The
   rule names a pair of words that point back. It names a pair of counting words too. A sweep found `second` and `next`
   written as ordinary words across the tree. `.claude/conventions.md` writes
   `a second makes the reader match a second referent` and `An em-dash comes out as a full stop and a second sentence`.
   It writes `The author asks that of an accepted rule before the next review runs`. Such a `second` or `next` points at
   nothing behind it. A checker cannot tell a counting `second` from a pointing `second` without a part-of-speech
   tagger. `latter` and `former` do point back. The sweep found a live use of `latter` in `check_decoder.py`. The other
   matches of `latter` came from the proposal's own text. A checker for that pair earns nothing. The cited line is a
   real fault of another kind. `src/decoder.c` called that range the bytes that may follow. `That second range` gives
   the range a new name. `a-name-keeps-its-meaning` covers that fault.

1. **`shape_faults refuses a lone word between a pair of commas where that word ends in ed or ing.`** The proposal cited
   `src/memory.c`, and the line
   `Build an object of size bytes, zeroed, and say in memory what it may allocate from here on.` *Rejected.* A sweep of
   the pattern found that sentence and no further match in the tree. The match there is a list item rather than a
   participle holding the clause open. The proposal's reasoning describes a shape that sentence does not have. The
   rewrite offered splits a sentence doing a pair of jobs. `short-plain-sentences` already decides that split. The
   suffix test also takes a list item ending in those letters. `indeed` is such a word. The proposal spares `however`
   and `instead` by name. The author had already seen the wrong catches, and patched around the pair in front of them.

1. **`shape_faults refuses a sentence opening on NULL.`** The proposal cited `src/memory.c`, and the line
   `NULL if the cap or the allocator refuses.` *Rejected.* The rule names a word rather than a fault. A sweep looked for
   a bare term followed by `where`, `if` or `at`. It looked for `until` behind such a term too. The sentences it found
   read well. `Entries in this section came to credit a mechanism that had moved on without them.` is such a sentence.
   So is `Findings by themselves cannot tell a reader that looked hard from a reader that looked at little.` The word
   after the term opens a phrase modifying the subject, and the verb follows that phrase. A rule on the shape would
   refuse such a sentence too. The sweep also found a verbless sentence this project writes on purpose. A hook heads its
   description with a line reading `PreToolUse on Edit and Write`. A missing verb is therefore no fault. The `NULL`
   lines read as a register the C sources follow, and the sentence in front of such a line names the function that
   returns `NULL`.

1. **`fragment_faults refuses a fragment whose first word is These or Those.`** The proposal cited `include/yeast.h`.
   The line there put a pair of functions in the allocator option. *Rejected.* A sweep found no fragment in the tree
   opening on either word. The quoted sentence appears in no file of the tree. The proposal also credits `prose_faults`
   with catching a fragment opening on `This` or `That`. The rule has no instance and no fixture a reader can check. A
   critic that finds a fragment opening on `These` or `Those` will cite a real line. The ruling can rest on that
   citation.

1. **`shape_faults refuses a clause after , and that runs past the length shape_faults allows a hung tail.`** The
   proposal cited `src/decoder.h`, and a sentence about the padded-input contract. *Rejected.* A sweep found the shape
   across the tree in numbers no fault class reaches. The borrowed limit measures a hung tail. A hung tail is a fragment
   the reader must attach to something. A clause after `, and` holds a subject and a finite verb, and the reader
   attaches nothing. The proposal says as much and reads the checker's pass as a miss. The tree writes the shape in the
   rules that govern it. `.claude/conventions.md` opens on
   `Nobody can violate a rule nobody wrote down, and a reviewer can only propose it.` `.claude/agents/reader.md` writes
   `A later reader would face the same hunt, and that hunt is the defect.` Such a sentence holds a pair of short clauses
   in balance, and `short-plain-sentences` asks for that shape. Splitting them at the `and` leaves a pair of stubs and
   drops the contrast. A long sentence is the real fault underneath, and that rule counts the words and the commas
   already.

1. **`word_faults refuses any wherever prose appears.`** The proposal cited `CHANGELOG.md`, and the line it quoted is in
   no file of the tree. *Rejected.* `check_grammar_coverage._is_total` documents itself as
   `Whether node matches at any position and under any parameter value.` The word `any` states the definition there.
   `check_grammar_coverage` proves that universal from the shape of the node. The docstring could state that definition
   with `every` or `each` instead. `_UNIVERSAL` refuses both. The sentence then has no word left to use. `ir.rebuilt`
   writes `A caller recurses without special-casing any node` on the same ground. `normalize._split_way` writes
   `A way reads where any of its parts does`, where the word means some of them. A negative earlier in a sentence turns
   the word into a polarity item. `spaces.SubSpace` writes `The filled subspace admits no character under any answers`.
   The run `any other` is a comparison, and `.claude/conventions.md` writes `a checker holds its claims like any other`.
   `every-claim-is-checked-before-it-is-written` permits a claim a gate decides. The cited sentences state such claims.

1. **`word_faults refuses half the and half of the wherever prose appears.`** *Rejected.* `grammar2decoder` writes
   `This is the data half of the decoder`, and the decoder has a data half and a code half. `interpreter._popped` writes
   `Neither half of the grammar wrote that entry`. `review_input._THE_DOCUMENTS` and `test_c.c` name a half of a pair
   the same way. Such a use is exact. A checker over the word cannot tell a named part from an unmeasured share.
   `CHANGELOG.md` held a use that measured a share. The rewritten entry reads
   `The meter measures disjointness and says nothing about safety`.

1. **`an-elided-passive-names-its-agent refuses as in front of a past participle where no by follows.`** *Rejected.* A
   checker cannot tell a participle from an adjective without a part-of-speech tagger. A pattern over `as` plus an `-ed`
   word and a list of irregular participles refuses sentences that hide nobody. `pre-commit-review.js` writes
   `The readers take that as given`. `CHANGELOG.md` writes
   `Such a way would have read as documented while saying nothing`. The hidden actor stays with the review.

1. **`a-pointer-possesses-nothing refuses That or This in front of a possessive.`** *Rejected.* The referent of such a
   pointer sits in the sentence in front wherever this tree writes one. `.claude/conventions.md` writes
   `That module's docstring names the convention back`. `normalize.py` writes
   `That character's range is a set, and the answer is the union over the paths`. The rewrite the proposal asks for
   reads worse. `a-pointer-names-its-referent` covers a pointer reaching further back.

1. **`a-doubled-article-is-refused refuses an article followed directly by an article.`** *Rejected.* A settling round
   read the whole tree, and the doubled article the proposal cited is gone from `CHANGELOG.md`. The tree holds no such
   pair. A critic reading a fragment sees `a A` and rewrites it. A gate buys nothing here.

1. **`a-sentence-opens-after-a-stop refuses a capitalised article behind a word ending on a letter.`** *Rejected.* The
   pattern reads a full stop inside a code span as no stop. It reads a Doxygen directive as a word. `yeast.h` writes
   `@note The grammar-derived core`, and the pattern refuses that line. The tree holds no run-on the pattern would
   catch.

1. **`an-elided-verb-after-until-is-refused refuses a sentence ending on until, a noun phrase and a bare does.`**
   *Rejected.* The tree holds no such sentence after a settling round. `an-elided-verb-is-refused` already covers the
   shape. Widening that rule costs a pattern rather than a convention.

1. **`a-negation-names-its-pair refuses a form of be followed by neither and a full stop.`** *Rejected.* The shape
   survives in `normalize._admits` alone. The pair sits in the sentences in front as `The first` and `The second`, and a
   reader has the referent there. `a-run-of-pronouns-reaches-back-to-a-noun` covers a pointer with no visible referent.

1. **`a-possible-is-a-superlative refuses the run as possible.`** *Rejected.* The tree holds no such run. A settling
   round rewrote the `DESIGN.md` sentence the proposal cited.

1. **`a-pair-pointer-names-its-pair refuses a sentence opening on Both of those.`** *Rejected.* The tree holds no such
   opener. `a-run-of-pronouns-reaches-back-to-a-noun` states the same fault over a wider set of pointers.
