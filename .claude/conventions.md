# The conventions a review holds code to

The list here is whole. A reviewer reports a violation of a rule written here as a **finding**. The finding cites the
rule **by name**. A reviewer may think the code ought to follow a convention the list leaves out. That convention is a
**proposal** even where it reads as reasonable. The reviewer reports a proposal separately, and a proposal blocks
nothing.

A reviewer asking "does this read well?" finds a complaint on any round. Nobody can violate a rule nobody wrote down,
and a reviewer can only propose it. A review that reports a proposal as a violation does not converge.

A rule has a name. A finding cites that name. An invariant and a step work the same way. A number would move the day
somebody adds a rule above it. A finding citing a number would then name the wrong rule.

**The author rules on a proposal in the same turn and writes the ruling down.** An accepted proposal joins this list. A
rejected proposal goes to `.claude/rejected.md` with its reason. `.claude/rejected.md` stops a proposal returning round
after round. A reviewer reads that file before proposing anything.

**A gate decides a rule wherever a gate can, and then the review skips that rule.** A gate that catches a fault class
stops that class recurring. A reviewer catches a single instance of the fault, and the class comes back. A rule says
whether a gate decides it. The author asks that of an accepted rule before the next review runs.

## Naming

- **a-boolean-name-is-a-question** - the name holds a question word such as `is_`/`has_`/`did_`/`does_`/`are_`. That
  word may sit anywhere in the name. `ys_queue_is_ready` asks about the queue. A bare verb (`consumes`, `decides`,
  `holds`) does not satisfy the rule. The rule covers a variable and a function alike. A field and a parameter fall
  under it too. A name that reads as a yes-or-no answer holds such a word. `found_nothing` holds the status a search
  answers with. The name reads true at the call site and means something else. A call may run, fail, and hand back
  whether it worked. Such a call takes a name opening on `try_`. Its `bool` answers no question, and a question's name
  would read false at the call site. A variable takes no `try_`. *Mechanised by `check_conventions`. That gate reads the
  `bool` off the C declaration and off the Python annotation.*
- **a-boolean-is-a-pure-question** - a `bool` answers a question and computes no more. A command reporting whether it
  worked hands back a `ys_status`. A parse hands back the position reached. `ys_next_line` answers NULL where the bytes
  fell short. A command, a parse and a question return distinct types. The type decides the name. *Mechanised in part by
  `check_conventions`. A `bool` under a name that is not a question is a fault, in C and in Python alike. Whether a
  question is pure stays with the reader.*
- **a-name-keeps-its-meaning** - new semantics take a new name, and nobody redefines an existing one. `pair` means a
  frame-scoped `Push`/`Pop` pair and no more. *Not mechanised.*
- **long-names-over-short-ones** - a writer invents no shorthand where the project has a word for the thing. Grep for
  the existing term before coining one. *Not mechanised.*
- **a-literal-list-spells-each-word-once** - a word written twice in a literal list is the trace of somebody appending
  without reading. *Mechanised by `check_conventions`.*
- **an-unread-name-carries-an-underscore** - a bound name nothing reads is `_pair`, `_value` or `_round`. *Mechanised by
  `pylint`, through `W0612` for an unread local and `W0613` for an unused argument. The leading underscore quiets both.*
- **a-private-name-carries-an-underscore** - the underscore goes on a module-level function or constant that only its
  own module reads. `main` is the entry point and keeps its bare name. A name another module writes in a signature loses
  the underscore. That module reads the name. `spec_tests.Fixture` lost the underscore when `check_interpreter` came to
  name that class. *Mechanised by `check_conventions`. That gate walks the whole tree. Visibility is a cross-file
  question, and a linter answers nothing here. The gate reads a call rather than an annotation. A name another module
  writes only in a signature stays with the reader.*
- **a-name-is-not-shadowed** - a local does not take the name of something in the enclosing scope. *Mechanised by
  `pylint` `W0621`.*

## Structure

- **one-answer-per-question** - a pair of walks answering the same question drift. Grep for a walk that answers the
  question before writing one. Call the walk that exists. *Not mechanised.*
- **a-prose-checker-reads-flattened-prose** - a prose checker reads the text `collect_fragments.flattened` gives. A
  prose digest flattens through the same function. A checker reading a file as the file wraps would move its refusal
  when `make reformat` rewrapped that file. *Mechanised by `check_hooks`. That gate re-wraps the prose of the tree and
  compares the flattening. It also reads the write-time calls for a prose checker taking a text nothing flattened.*
- **a-hook-answers-through-the-runner** - a registered command opens with `run.sh`. A hook that dies otherwise writes
  nothing and leaves with a status the tool ignores. A dead checker then reads as a checker that passed a clean call.
  `run.sh` turns the death into a refusal the model reads. *Mechanised by `check_hooks`. That gate reads
  `.claude/settings.json` and fails a command that opens with anything else.*
- **a-write-time-hook-reads-the-edit-in-place** - a hook decides about the file the edit would leave. The text the tool
  hands over is a slice. An edit rewording a comment can leave the `#` in place. The slice then holds the words and no
  `#`. A hook reading only the slice finds no prose there and passes. `collect_fragments` rebuilds the file from
  `old_string` and `new_string`. That module says which fragments the file would hold. *Mechanised by `check_hooks`.
  That gate fires a pair whose `old_string` is a mid-comment slice with no marker. A hook that finds no prose in that
  slice fails the gate.*
- **a-repository-question-with-two-callers-is-asked-once** - a pair of callers asking the same question of the
  repository can get differing answers, and the difference goes unreported. Cache the question at its definition. A
  question with a single caller wants no cache. *Mechanised by `check_conventions`.*
- **an-enumeration-of-the-tree-lives-in-gate** - a roster names the files a question covers. A pair of rosters of the
  same thing drift apart. The gate that missed a category reads exactly like a gate that passed. `gate` mints a roster.
  Adding a category then touches a single file. A module outside `gate` leaves the filesystem to `gate`. *Mechanised by
  `check_conventions`. That gate holds the calls it refuses.*
- **a-kind-dispatch-raises** - a question about the grammar is a total `ir.Question`. A default arm reports the
  question's blindness as a fact about the grammar. *Mechanised by `ir`. A `Question` raises on a kind its table does
  not name, and `check_normalize` reports a handler that a corpus run left unreached.*
- **a-declared-exception-carries-its-reason** - the gate checks the declaration in both directions. A declaration the
  tree has outgrown fails as loudly as a missing one. `KEPT_THOUGH_DEAD` and `NAMES_THE_TREE_NO_LONGER_HOLDS` are such
  declarations. `NAMES_STILL_OWED`, `DEVIATIONS` and `NAMES_A_FILE_MAY_CITE_UNANSWERED` are such declarations as well. A
  declaration keys on something durable. A name, a path glob and a production are durable. A make target is durable. A
  line number moves with the next edit. A declaration takes no line number. A declaration with no such key sits at the
  site as a comment marker. `not-prose:` is one. That marker says why the literal beside it holds a layout rather than a
  sentence. *Mechanised by `check_dead_code`, `check_documents` and `check_vendor_spec`. `check_prose` mechanises it
  too.*
- **a-gates-prerequisites-list-what-it-reads** - a gate reads a file, and that gate's stamp rule names the file as a
  prerequisite. A stamp rule missing an input turns a skipped gate into a green one. *Not mechanised. The `Makefile`
  cannot check itself.*
- **nothing-is-dead-below-the-top-level** - `check_dead_code` walks top-level names. That walk cannot see a parameter no
  caller sets, and it cannot see an attribute nothing reads. A function computes a value and passes it on. That function
  stays live, and the gate passes. A feature then runs end to end and does nothing. Docstrings along the call chain say
  the feature works. Such a parameter comes out, or somebody declares it beside `KEPT_THOUGH_DEAD` with a reason. An
  attribute goes the same way. `Emitter` held such a parameter and such an attribute. *Not mechanised.*
- **a-gate-walks-the-hooks-or-says-why-not** - the hooks are Python this project maintains, and the same faults land in
  them. A gate taking only `gate.modules()` says beside the walk why the hooks fall outside. A reader cannot otherwise
  tell which roster a gate used. `check_dead_code` says why. *Mechanised by `check_conventions`. That gate reads which
  roster a module calls for. A module calling for the narrower roster names the hooks in its docstring.*
- **a-parsed-source-is-split-on-the-newline** - a parse numbers a line by the newline. `splitlines` cuts on more than
  that. U+2028 and U+0085 are among the cuts, and `star.py` writes both in string literals. `splitlines` returns a list.
  A caller indexes that list with a line number the parse counted. The list and the parse then disagree about that line.
  A source rebuilt from that list stops the parse. *Mechanised by `check_conventions`.*
- **a-matched-name-is-spelled-somewhere-else** - a check matches source against a literal list of identifier names. Such
  a list holds names the tree writes elsewhere. A name whose single occurrence is the list itself is a guess about code
  that does not exist. The check covering that name reads exactly like a check that fires. Such a name comes out, or
  somebody declares it with a reason. *Mechanised by `check_conventions`. That gate reads a module-level list of
  identifier names and looks for the names across the tree. A single-letter name is a grammar parameter and falls
  outside.*
- **a-hook-refuses-through-one-helper** - a `PreToolUse` hook answers a refused edit through a shared helper rather than
  building the `{"decision": "block", "reason": ...}` payload itself. That payload's shape is a contract with the tool.
  A copy with a wrong key prints nothing and reads as an edit that passed. `refusal.py` holds the payload for a hook
  written in Python. `.claude/hooks/refusal.sh` holds it for a hook written in shell. *Mechanised by
  `check_conventions`. That gate reads the payload keys off a hook. A helper names those keys by design and falls
  outside.*
- **failures-are-not-ignored** - an ignored failure produces output that looks like success. Nobody goes back to check
  that output. The rule covers the code of the tree in whatever shape the failure takes. A gate deciding that shape is
  no part of the rule. A shape no gate reads breaks the rule the same way. A swallowed exception is this fault. So is a
  dropped item. So is a skipped call. So is a status nobody reads. A shell script stops on a failed command. A recipe
  does not swallow one. A workflow asks whether an agent answered, and whether the answer holds anything. An agent that
  refuses still answers under its schema. That answer is present and empty. Python raises, or reads the status before
  the output. A handler doing neither states a reason on a line of its own. `failure-is-reported:` says the failure
  comes out elsewhere. `not-a-failure:` says the exception is an ordinary answer. *Mechanised by `check_failures` over
  the shapes that gate reads. That gate reads the shell scripts and the `Makefile`. It reads the workflows and the
  Python too.*
- **a-reader-is-handed-its-prose** - a batch goes in the reader's prompt. A reader holding no fragment is a dispatch
  fault. An empty answer from such a reader reads exactly like a batch that passed. *Not mechanised.*

## Comments and documents

The prose checkers read a comment beside code and a document alike.

- **text-says-what-is** - the text avoids `now`, `no longer` and `used to` about itself. It avoids `previously` too. The
  text narrates no plan step. *Mechanised by `check_documents` for the documents, and the `document_rules` checker
  refuses the same wording as the writer writes it. `prose_rules` refuses the tense word wherever prose appears, through
  the `prose_rewrite` hook and the `check_prose` gate.*
- **a-sentence-states-no-plan** - the checker refuses `yet` where a full stop or a comma follows it. A clause ending on
  that word points at work the project still owes, and the prose goes stale the day that work lands. `PLAN.md` holds the
  work the project owes. `The parser core does not exist yet.` points. `The tree holds no parser core.` says what is.
  *Mechanised by `prose_rules`, through the `prose_words` hook and the `check_prose` gate.*
- **each-document-keeps-to-its-domain** - `DESIGN.md` is context, perspective and architecture, and it repeats no code
  comment. `CHANGELOG.md` records the work a change did. `PLAN.md` names the work the project still owes. *Not
  mechanised. Whether a passage keeps to its document's domain stays with the reader.*
- **a-design-citation-keeps-its-altitude** - a citation of a private name in `DESIGN.md` repeats the docstring beside
  that name. `check_documents.private_citation_errors` decides which citations are private. *Mechanised by
  `check_documents`, and the `altitude` hook refuses such a citation as the writer writes it.*
- **a-step-carries-its-argument** - a new step of the normalization pipeline comes with the argument that answers the
  rules above `_Step` in `generator/normalize.py`. The argument says which invariant the step establishes, or why the
  step establishes none. It says that the transform is local and mechanical. It says that a value the step produces is a
  global or a stack entry. *Not mechanised. Whether the argument is true stays with the review.*
- **a-step-opens-its-argument** - a step's prose opens the argument the rules above `_Step` in `generator/normalize.py`
  ask for. *Mechanised by the `step-rules` hook.*
- **a-reference-parser-has-one-name** - a pair of upstream projects both go by "the reference parser". This project
  gives the pair distinct names. **Haskell YamlReference** is the Haskell parser vendored under
  `third_party/yamlreference/`, and it is the token-level oracle. **YAML Reference Parser** is the spec-generated parser
  at `yaml/yaml-reference-parser`, and YAMLStar runs the Clojure build of that parser. A bare `YamlReference` names
  neither. `the YAML reference parser` names neither. A path, a URL and a vendored upstream file keep their own form.
  *Mechanised by `prose_rules`, through the `prose_words` hook and the `check_prose` gate. The checker refuses
  `the YAML reference parser` in lower case. It passes the capitalised project name.*
- **an-oracle-answers-and-a-checker-decides** - an oracle produces the answer. Haskell YamlReference produces a parse.
  The fixtures under `tests/spec/` hold the token stream a production must emit. `check_spaces._check_standings` writes
  out the comparison values. A checker produces pass or fail, and the hooks under `.claude/hooks/` call themselves
  checkers. An oracle comes from somewhere other than the thing it judges. An oracle judging itself proves nothing. *Not
  mechanised. Whether a thing answers or decides stays with the review.*
- **a-sentence-stays-within-the-limits** - the checker counts a sentence's commas and words. A single third-person
  pronoun passes, and a second makes the reader match a second referent. The checker refuses a possessive with its noun
  dropped. It refuses a bare participle where the subject belongs. It refuses a short tail hung off the last comma with
  no verb. Such a tail covers the appositive and the trailing absolute. It refuses a relative clause hung off the last
  comma at any length. A list runs to the length `prose_rules` allows. The checker refuses a verb elided after a
  negation. *Mechanised by `prose_rules`. That module holds the checker. The `prose_rewrite` hook refuses a fragment as
  the writer writes it, and the refusal names no pattern. `faulty-prose-is-re-said-not-patched` says why. `check_prose`
  asks the same of the prose in the tree.*
- **a-sentence-says-one-thing** - a sentence says a single thing. The subject and the verb sit near the front and close
  together. A pair of plain sentences beat a compressed clause. Density is not the goal. Compression is where writing
  goes wrong. A compressed sentence keeps the rhythm of the style and holds no readable claim. Read a sentence once and
  at speed. A clause you have to hunt for wants the sentence split rather than tightened. A verbless clause inside a
  sentence is such a clause. *Not mechanised. Deciding a verbless clause wants a part-of-speech tagger this tree does
  not have.*
- **a-sentence-hangs-a-single-tail** - a sentence hangs a trailing modifier off its last comma, and hangs no further
  modifier. A reader tracks such a modifier to the word it lands on. A stacked modifier sends the reader back through
  the clause.
  `A nibble-table lookup classifies 16 bytes at a time under SSSE3 or NEON, behind this same signature, without the generated parser changing a line.`
  stacks a pair of modifiers.
  `A nibble-table lookup classifies 16 bytes at a time under SSSE3 or NEON. The signature holds, and the generated parser changes no line.`
  stacks none. *Mechanised by `prose_rules`, through the `prose_rewrite` hook and the `check_prose` gate. `_hung_tail`
  reads the tail after the last comma and stops there. `_A_STACKED_TAIL` counts the commas in front of `with`, `behind`
  and `without`.*
- **a-whose-clause-is-a-hung-tail** - a relative clause opening on `whose` after the last comma makes the reader walk
  back to find the noun the clause is about. End the sentence and name the noun.
  `A tested set is a maximal character node, whose parent is no character node itself` says
  `A tested set is a maximal character node. Its parent is no character node itself.` *Mechanised by `prose_rules`,
  through the `prose_rewrite` hook and the `check_prose` gate.*
- **a-fragment-opens-on-its-own-referent** - the opening sentence of a fragment writes no `such a` or `such an`. A
  reader who opens the fragment cold finds no earlier sentence naming the thing the pointer reaches back to. Naming the
  noun is shorter than pointing at it. *Mechanised by `prose_rules`, through the `prose_rewrite` hook and the
  `check_prose` gate. `_fragment_faults` reads the opening sentence.*
- **a-possessive-owns-a-noun** - a sentence does not end on a possessive. A possessive with no noun behind it names no
  owner. `A lookahead's.` is a docstring an edit ate the noun out of. *Mechanised by `prose_rules`, through the
  `prose_rewrite` hook and the `check_prose` gate. The checker reads a stub that falls under `_FEWEST_WORDS`.*
- **a-sentence-does-not-define-by-itself** - a sentence writes no word on both sides of `what`. Such a sentence defines
  a word by repeating it and tells the reader nothing. `A recovery takes what its recovery takes` names no source.
  *Mechanised by `prose_rules`, through the `prose_rewrite` hook and the `check_prose` gate. `_A_FUNCTION_WORD` names
  the words a repeat says nothing about.*
- **a-condition-names-what-decides-it** - a clause saying when something happens names the thing that decides it.
  `where a cut fires` names the cut. `where the parse gets through` names nothing, and a reader cannot tell what makes
  it true. *Not mechanised.*
- **a-so-clause-is-a-why** - a comment writes no `so` clause. The clause gives the reader WHY an action happens rather
  than WHAT happens next. The checker spares `do so` and `doing so`. It spares `says so` and `so that` too.
  `The paths come back so a site can say which it wants` says `The paths come back. A site then says which it wants.`
  *Mechanised by `prose_rules`, through the `prose_words` hook and the `check_prose` gate.*
- **a-plan-step-is-not-named-by-a-numeral** - prose names no step by `Phase`, `Step` or `Stage` in front of a numeral.
  The number moves when somebody adds a step, and the prose then points at the wrong one. Name the step instead.
  `Phase 10 leaves the bodies unlowered` says `Lowering leaves the bodies unlowered`. *Mechanised by `prose_rules`,
  through the `prose_words` hook and the `check_prose` gate.*
- **a-dotted-name-sits-in-a-code-span** - a word holding `/`, `::`, or a dot in front of a letter sits in backticks. A
  bare path skips the gate that checks a cited name, and a rename then leaves the prose naming a file the tree dropped.
  A markdown link target keeps its own form. A URL keeps its own form too. `e.g` and `i.e` are prose. *Mechanised by
  `prose_rules`, through the `prose_words` hook and the `check_prose` gate. `said_by` blanks a URL and takes a link's
  target off before the checker reads the text.*
- **a-code-span-hides-no-passive** - the passive check reads a code span as a word. A form of `be` with a code span in
  front of a past participle is a passive, and the reader still cannot tell who acts. *Mechanised by `prose_rules`,
  through the `prose_rewrite` hook and the `check_prose` gate. `said_by` writes a code span as `name`, and the passive
  pattern reads that word.*
- **a-sentence-names-its-actor** - the subject names a thing that acts. A caller acts. An edit and a gate act. A
  nominalized clause acts on nothing. `What libyeast adds cannot quietly become what libyeast changes` states a relation
  between a pair of abstractions and names nobody.
  `An edit meant to add something must not turn out to have changed an official production` says the same thing and
  names the actor. Prefer the active voice. `One is opened under the current code` hides who opens it. A fragment's
  prose may drop the subject where that subject is the thing the fragment documents. `Break the project into fragments`
  reads as `this module breaks the project into fragments`. The reader supplies the module from the fragment in front of
  them. That elision is active voice under a shorthand, and it passes. The rule refuses an elision whose subject no
  reader can recover. *Not mechanised.*
- **a-sentence-opens-on-a-subject** - the checker refuses a sentence opening on `What`, `How` or `Which` in upper or
  lower case. It refuses `Where` there too. A leading `So`, `And` or `But` does not spare such a sentence. `Whether`
  opens the docstring of a predicate and passes. `prose_faults` refuses a fragment opening on a word that points outside
  it. The checker refuses a form of `be` followed by a past participle. It takes `got` and `gotten` as participles. It
  reads a code span as a word, and `a-code-span-hides-no-passive` covers a span between the form of `be` and the
  participle. A `by` after the participle names the agent, and the checker passes that passive. A sentence opening on a
  past participle drops its subject, and the checker refuses that sentence. *Mechanised by `prose_rules`, through the
  `prose_rewrite` hook and the `check_prose` gate.*
- **a-run-of-pronouns-reaches-back-to-a-noun** - a run of sentences opening on `It`, `Its` or `They` reads as a list
  under a subject the prose named. A run opening on `Their` reads the same way. The subject goes in the sentence in
  front of that run. A demonstrative there names nothing, and the reader walks back further to find the noun.
  `That is a call.` anchors no run. `A call is one such node.` anchors it. `Such a node` names the noun and passes.
  *Mechanised by `prose_rules`, through the `prose_rewrite` hook and the `check_prose` gate. `prose_faults` reads the
  run and the sentence in front of it.*
- **a-paragraph-opens-on-a-noun** - the checker refuses a paragraph whose opening sentence begins with `It`, `Its` or
  `They`. It refuses `Their` there too. A pronoun later in the paragraph passes. A paragraph break puts a blank line
  between the pronoun and its referent. The reader walks back over that break. *Mechanised by `prose_rules`, through the
  `prose_rewrite` hook and the `check_prose` gate. `prose_faults` reads the paragraphs, and the fragment's own opener
  stays with `a-run-of-pronouns-reaches-back-to-a-noun`.*
- **a-question-word-is-no-noun** - a free relative after a preposition points at a thing rather than naming it. Name the
  noun. `Reduce the ledger to what the tree still bears out.` points.
  `Reduce the ledger to the entries the tree still bears out.` names. A sentence with no noun to reach for wants
  restructuring rather than a swap. *Mechanised by `prose_rules`, through the `prose_words` hook and the `check_prose`
  gate. The checker refuses `what` where `to`, `of` or `for` comes directly in front. It refuses `what` behind `about`
  and behind `through` as well. It refuses `is what` and `are what`, where a copula joins a free relative and names
  nobody.*
- **no-em-dash-and-no-colon** - an em-dash appositive replaces a sentence. A sentence that wants such an appositive
  wants a full stop instead. A checker refuses `a - b`. A writer who then writes `a: b` moves the appositive to another
  mark. That is a cheap trick rather than a fix. The term of a definition list keeps its colon. That term reads
  `**term:**` and leads its line. A sentence introducing a list keeps its colon. The claim is whole in front of that
  colon, and the reader takes the items a step at a time. A message in a string literal keeps its colon. The label
  naming the subject leads the message, and the gate's verdict follows the colon. *Mechanised by `prose_rules`, through
  the `prose_rewrite` hook and the `check_prose` gate. The checker refuses a semicolon and a raised dot alongside the
  em-dash. Either replaces a full stop.*
- **faulty-prose-is-re-said-not-patched** - a shape rule refuses a fragment and tells the writer no more. The writer
  says the whole prose of that fragment again, from the code and from the claims the old text made. A refusal naming the
  shape reads as a to-do list. A writer keeps the word order and swaps words around it until the pattern stops matching.
  The fault lives in the word order. So the refusal names no pattern and quotes no sentence. The refusal states the
  writing guidelines instead, and those reach the writer at the keyboard. A word rule runs the other way round, and
  `prose_words` names the word. A claim wants a grep rather than a rewrite. *Mechanised by `prose_rewrite`.
  `check_prose` prints the faults in full when the author runs it.*
- **use-the-words-this-project-uses** - the checker refuses a word the project turned down wherever prose appears.
  `meet` was the first. The sentences after a refused word say the verb that replaces it. A reader opens a fragment. A
  pair of subspaces are `intersected with` one another. A thing satisfies a requirement. The checker refuses `answer to`
  and `answer for` in every form. A rule covers a text. A name names something in the tree. A script follows a rule. A
  default supplies the parameter a caller omits. The checker refuses `hold` followed straight by `to`. A text satisfies
  a convention. A header matches what the grammar produces. `holds this module to the roster` names its object and
  passes. The checker refuses `carry` in any form. Say the verb the thing actually does. A file states its description.
  A list names its items. A parse continues at its tail call. The checker refuses `spell` in any form. A grammar writes
  a production. A source holds a name. Say which form a thing takes. The checker refuses `by itself` wherever it
  appears. Say `only` where the point is that one thing takes part. Name the thing that is missing where the point is an
  absence. The same ruling covers `on its own`, `on their own` and `of its own accord`. A phrase holding a possessive
  takes another form. `on a line of its own` replaces `on its own line`. The ban is a ruling. Its reason sits beside the
  pattern. *Mechanised by `prose_rules`, through the `prose_words` hook and the `check_prose` gate.*
- **a-free-choice-word-is-a-universal** - the checker refuses `whatever` and `whichever` wherever prose appears. The
  word claims over a class, and the sentence names no member of that class. A free relative naming a thing takes the
  noun instead. `A pop takes off whatever is on top` says `A pop takes off the entry on top`. *Mechanised by
  `prose_rules`, through the `prose_words` hook and the `check_prose` gate.*
- **a-purpose-clause-says-no-action** - the checker refuses `exists for` and `exists to` wherever prose appears. Such a
  clause gives WHY where the question was WHAT. The text names the action instead. `a-comment-describes-what` allows a
  single sentence of WHY, and a relative clause rides inside a WHAT sentence past that limit. *Mechanised by
  `prose_rules`, through the `prose_words` hook and the `check_prose` gate.*
- **a-pointer-names-its-referent** - the word rules refuse the runs `That last` and `This last` wherever prose appears.
  The prose names the rule, the file or the option instead. An edit inserting a sentence leaves such a pointer aimed at
  something else, and the prose still reads as though the pointer holds. *Mechanised by `prose_rules`, through the
  `prose_words` hook and the `check_prose` gate.*
- **a-marked-up-word-is-read-as-a-word** - the word rules strip the emphasis markers off a word before matching it. A
  bolded sentence is the headline of a fragment, and a reader takes the claim in it hardest. The markers glue onto the
  opening word. Without the strip, a word rule reads `**No token spans a line.**` as `**No` and passes the sentence. The
  same rule refuses `No token spans a line.` *Mechanised by `prose_rules`, through the `prose_words` hook and the
  `check_prose` gate.*
- **a-hedge-names-its-condition** - a hedge reads as a condition and states none. A hedge names nobody who decides. A
  reader cannot tell when the sentence applies. `It is cheap enough to leave enabled in a release build if desired.`
  hedges. `It is cheap enough to leave enabled in a release build.` does not. Deleting the hedge costs the reader
  nothing. Say who chooses and on what, or say the claim without the hedge. *Not mechanised. A hedge the word list below
  does not name stays with the review.*
- **no-discovery-narration** - `prose_rules._NOT_OUR_WORD` refuses `turns out` and `turned out` wherever prose appears.
  The phrase tells the story of a discovery. The text states the fact instead. A reader otherwise strips the story off
  to reach the fact. *Mechanised by `prose_rules`, through the `prose_words` hook and the `check_prose` gate.*
- **a-named-hedge-is-refused** - `prose_rules._NOT_OUR_WORD` refuses `if desired`, `as needed` and `as appropriate`
  wherever prose appears. It refuses `where appropriate` and `in general` there too. *Mechanised by `prose_rules`,
  through the `prose_words` hook and the `check_prose` gate.*
- **a-name-uses-the-words-this-project-uses** - a bound name says no word the project turned down. `check_conventions`
  splits a name on its underscores. The gate matches the words against the list the prose rules read. A name saying
  `spelling` sat beside a docstring saying `form`, and a reader had to work out that `spelling` and `form` were the same
  thing. The next writer greps for the refused word, finds it in a live name, and writes that word back into prose. A
  rule name, an invariant name and a step name are strings rather than bound names. Such a name keeps its own words.
  *Mechanised by `check_conventions`. That gate reads the names a module binds.*
- **a-piece-of-prose-is-written-once** - a file does not say the same thing twice. A C header and its source count as a
  single file here. A fragment does not write the same sentence twice either. A writer edits a copy of a detail written
  twice. The copy left behind goes quietly false. A pair of places may want the same detail. The first place of such a
  pair says the detail, and the second cites the first. Parallel wording for parallel things is no repeat. *Not
  mechanised.*
- **a-file-says-what-it-is** - a file the project writes has a description of itself. A Python module says that in a
  docstring. Another file says it in a comment block. A file a script generates says so, and names the script.
  *Mechanised by `pylint` `C0114` for Python, and by `collect_fragments`. That module faults a file fragment holding no
  prose.*
- **a-file-description-comes-first** - a file's description is the first thing in it. The licence tag and the
  interpreter line go above it. An include guard, an import and a declaration come under it. A reader who opens the file
  cold learns what they are looking at before anything else. A description found lower down documents the declaration it
  sits over instead. *Mechanised by `collect_fragments`. That module reads the block at the top of a file as that file's
  prose, and reads a block lower down as something else.*
- **a-make-assignment-carries-no-trailing-comment** - a comment above a `Makefile` variable documents it. A comment
  sharing the variable's line documents nothing. `make` keeps the whitespace before the `#` inside the value.
  `PKG_PREFIX := .../prefix  # a note` becomes a path with trailing spaces, and `$(PKG_PREFIX)/lib` then names a
  directory nothing holds. The failure is silent wherever a command word spends the value. It is loud where something
  concatenates the value. *Mechanised by `check_conventions`.*
- **a-comment-inside-a-body-is-a-note** - a comment inside a function body runs to the length `short_comments` allows. A
  passage there restates the code in prose, or defends a decision nobody questioned. A field's trailing comment and the
  run wrapped below it count as a single comment. A blank line ends a wrap. A block documenting the next field sits
  below that blank line. A field wanting more than a note says the detail in the class docstring, where the prose rules
  reach. *Mechanised by `check_conventions`. That gate reads the syntax tree, and the `short_comments` hook refuses the
  shape as the writer writes it.*
- **a-nested-docstring-is-a-note** - a function nested inside another keeps its docstring to the length of a note.
  Docstring syntax works there. `collect_fragments` folds such a docstring into the enclosing function's fragment. A
  reader who opens only the nested function sees nothing of that docstring. A passage worth more than a note belongs to
  a function at the top level. *Mechanised by `check_conventions`. That gate reads the syntax tree, and the
  `nested_docstrings` checker refuses the shape as the writer writes it.*
- **a-comment-describes-what** - a comment says HOW only where HOW matters. Density matches the surrounding code. **The
  default is no WHY at all.** A comment may say why code sits where it sits. The comment says so only where a reader
  would otherwise move the code. The explanation runs to a single sentence, and a checker holds its claims like any
  other. This is a limit rather than a permission. False claims in this tree turn up in such a sentence. A writer writes
  such a sentence from intent rather than reading it off the code. *Not mechanised. Whether a comment describes the code
  stays with the review.*
- **a-comment-writes-no-why-clause** - the checker refuses a clause giving WHY inside a comment or a docstring. An
  explanation a comment may give takes a sentence. *Mechanised by `prose_rules`, through the `prose_rewrite` hook and
  the `check_prose` gate.*
- **every-claim-is-checked-before-it-is-written** - a superlative and an enumeration want checking without fail.
  `the one place` and `nothing else` are superlatives. `which declares A, B and C` is an enumeration. A comment asserts
  things about the tree exactly as code does. A comment wants the same evidence code wants. A claim no word marks wants
  checking too. *Not mechanised.*
- **a-claim-writes-no-universal** - the checker refuses a universal and a superlative. It names the determiner classes
  rather than a list of words. It refuses `the sole door` beside `the only door`. It refuses a negation in subject
  position, and a negation passes in object position. *Mechanised by `prose_rules`, through the `prose_words` hook and
  the `check_prose` gate.*
- **a-refusal-may-write-the-words-it-bans** - a hook refusal names the rule it enforces, and ends on that name. Such a
  text quotes the wording it turns down. The prose rules skip such a text. The prose rules read a refusal naming no rule
  as ordinary prose. *Mechanised by `prose_rules` and `collect_fragments`. Those modules read a refusal as a text of its
  own rather than as prose of this project.*
- **a-count-is-answered-for** - `DESIGN.md` may state a count in front of a name the code measures. `PLAN.md` and
  `CHANGELOG.md` may state none. `check_documents._NOT_A_NUMBER_OF_THE_TREE` lists what may pass, and the checker
  refuses a numeral outside that list. A number word counts as a numeral. The checker refuses `zero`, `one` and `two`
  where it refuses another numeral. A written-out count gets past the gate that reads a numeral. The word rules
  therefore refuse `the two`, `the three` and `the four` wherever prose appears. `zero-width` names a guard that takes
  no width, and that name appears in the list under both its forms. The text replacing a number says **which ones**
  rather than **how much**. `a handful` and `most` and `nearly all` are unfalsifiable. *Mechanised by `check_documents`
  for the numbers. `prose_rules` refuses the vague quantifier that replaces a number. The `no_tree_numbers` hook refuses
  a number as the writer writes it, and the `prose_words` hook refuses the quantifier there.*
- **every-cited-name-exists** - a backticked name names something in the tree. *Mechanised by `check_documents`.*
- **a-name-of-the-tree-sits-in-a-code-span** - a name the tree binds appears inside backticks. The checker strips the
  code spans off a fragment and refuses a word left holding an underscore between letters. C prose writes a public name
  bare in places, and the rule covers that prose too. `check_documents` verifies a backticked name against the tree, and
  an unmarked name gets past that gate. A rename then leaves the prose citing a name the tree dropped. *Mechanised by
  `prose_rules`, through the `prose_words` hook and the `check_prose` gate.*
- **an-invariant-docstring-states-its-claim** - an invariant's docstring is prose, and the prose rules cover it. The
  invariant's name string keeps its own form. The invariant named `every-difference-is-between-character-sets` has a
  docstring reading `Check that a difference is between character sets`. The name is an identifier rather than prose.
  `check_prose` skips that name. *Mechanised by `check_prose` for the docstring. The name stays with the author.*
- **a-mechanised-rule-names-its-convention** - a convention marked mechanised above names the module that mechanises it.
  That module's docstring names the convention back. Otherwise a rule lands in the code, and the list here goes stale.
  *Mechanised by `check_conventions`, and the `document_rules` checker refuses an edit of this file that breaks it.*

## Layout

- **wrapped-at-120-columns** - the limit holds over code and comments. It holds over markdown too, and over the
  `Makefile` and CMake. *Mechanised by `vet-format-*`.*
- **a-tracked-file-holds-ascii** - the rule covers code and comments. It covers markdown and the grammar too, and the
  `Makefile` and CMake. A character past ASCII looks like the ASCII character it replaces. An em-dash comes out as a
  full stop and a second sentence. A curly quote comes out straight. An arrow comes out as a word. A text naming such a
  character writes the escape that names the codepoint. `check_ascii._MAY_HOLD_ANY_BYTE` declares the paths whose
  content holds such a character. *Mechanised by `check_ascii`, and the `ascii_only` hook refuses such a character as an
  edit writes it.*
- **a-file-of-the-tree-has-a-reader** - a tracked file holds prose. `collect_fragments.language_of` names the reader of
  that file. A file holding no prose of ours goes in `gate.UNREAD`, and the comment above that list says why. A checker
  finds no fragment in a file without a reader. The prose in such a file goes unread. *Mechanised by
  `collect_fragments`, and the `unread_files` hook refuses such a path as an edit names it.*
- **formatting-is-make-reformat** - a writer runs no formatter by hand and reflows no text by hand. *Mechanised by
  `vet-format-*`.*

`.claude/rejected.md` holds the proposals the author turned down. A reason sits beside a proposal there.
