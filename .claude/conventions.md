# The conventions a review holds code to

The list here is whole. A reviewer who finds a violation of something written here reports a **finding** and cites the
rule **by name**. A convention the reviewer thinks the code ought to follow, however reasonable, is a **proposal**. The
reviewer reports a proposal separately, and a proposal blocks nothing.

The split exists for a reason. A reviewer asked "does this read well?" has an answer ready. Nobody can violate a rule
nobody wrote down, and a reviewer can only propose it. A review that reports a proposal as a violation falls silent on
no round.

A rule has a name. A finding cites that name. An invariant and a step work the same way. A number would move the day
somebody adds a rule above it. A finding citing a number would then name the wrong rule.

**The author rules on a proposal in the same turn and writes the ruling down.** An accepted proposal joins this list. A
rejected proposal goes to `.claude/rejected.md` with its reason. A proposal does not wait between rounds. That file
stops a proposal returning round after round, and a reviewer reads it before proposing anything.

**A gate decides a rule wherever a gate can, and then the review skips that rule.** A gate that catches a fault class
stops that class recurring. A reviewer catching the same fault finds it once. A rule says whether a gate decides it. The
author asks that of an accepted rule before the next review runs.

## Naming

- **a-boolean-name-is-a-question** - the name holds `is_`/`has_`/`did_`/`does_`/`are_`, and the word appears anywhere in
  it, as `ys_queue_is_ready` asks about the queue. A bare verb (`consumes`, `decides`, `holds`) does not satisfy it. The
  rule covers a variable and a function alike. A field and a parameter fall under it too. A name that reads as a
  yes-or-no answer holds one. `found_nothing` holding the status a search answers with reads true at the call site and
  means something else. A call that runs, may fail, and hands back whether it worked is a `try_`. Its `bool` answers no
  question, and a question's name would read false at the call site. A variable takes no `try_`. *Mechanised by
  `check_conventions`. That gate reads the `bool` off the C declaration and off the Python annotation.*
- **a-boolean-is-a-pure-question** - a `bool` answers a question and computes no more. A command reporting whether it
  worked hands back a `ys_status`. A parse hands back the position reached. `ys_next_line` answers NULL where the bytes
  fell short. Holding those cases apart is what lets the type decide the name. *Mechanised for C in part. A `bool` under
  a name that is not a question is a fault. Whether a question is pure stays with the reader.*
- **a-name-keeps-its-meaning** - new semantics take a new name, and nobody redefines an existing one. `pair` means a
  frame-scoped Push/Pop pair and no more. *Not mechanised.*
- **long-names-over-short-ones** - no invented shorthand where the project has a word for the thing. Grep for the
  existing term before coining one. *Not mechanised.*
- **a-literal-list-spells-each-word-once** - a word written twice in a literal list is the trace of somebody appending
  without reading. *Mechanised by `check_conventions`.*
- **an-unread-name-carries-an-underscore** - a bound name nothing reads is `_pair`, `_value` or `_round`. This tree
  writes an unpacked or declared name that way where nothing uses it. *Mechanised by `pylint`, through `W0612` for an
  unread local and `W0613` for an unused argument. The leading underscore quiets both.*
- **a-private-name-carries-an-underscore** - the underscore goes on a module-level function or constant that only its
  own module reads. `main` is the entry point and keeps its bare name. A name another module writes in a signature loses
  the underscore. That module reads the name. `spec_tests.Fixture` lost the underscore when `check_interpreter` came to
  name that class. *Mechanised by `check_conventions`. That gate walks the whole tree. Visibility is a cross-file
  question, and a linter answers nothing here. The gate reads a call rather than an annotation, and the signature half
  stays with the reader.*
- **a-name-is-not-shadowed** - a local does not take the name of something in the enclosing scope. *Mechanised by
  `pylint` `W0621`.*

## Structure

- **one-answer-per-question** - a pair of walks answering the same question drift. Grep for a walk that answers the
  question before writing one. Call the walk that exists. *Not mechanised.*
- **a-write-time-hook-reads-the-edit-in-place** - a hook decides about the file the edit would leave. The text the tool
  hands over is a slice. A reworded comment arrives with the `#` left behind in the part of the line the edit did not
  replace. A hook reading only the slice finds no prose there and passes. `collect_fragments` rebuilds the file from
  `old_string` and `new_string`. That module says which fragments the file would hold. *Mechanised by `check_hooks`.
  That gate fires a pair whose `old_string` is a mid-comment slice with no marker. A hook going blind to that shape
  reports as blind.*
- **a-repository-question-with-two-callers-is-asked-once** - a pair of callers asking the same question of the
  repository can get differing answers, and the difference goes unreported. Cache the question at its definition. A
  question with a single caller wants no cache. *Mechanised by `check_conventions`.*
- **an-enumeration-of-the-tree-lives-in-gate** - a roster names the files a question covers. A pair of rosters of the
  same thing drift apart. The gate that missed a category reads exactly like a gate that passed. `gate` mints a roster.
  Adding a category then touches a single file. A module outside `gate` leaves the filesystem to `gate`. *Mechanised by
  `check_conventions`. That gate holds the calls it refuses.*
- **a-kind-dispatch-raises** - a question about the grammar is a total `ir.Question`. A default arm reports the
  question's blindness as a fact about the grammar. *Enforced by a `PostToolUse` hook on an edit, and by
  `check_dead_code`'s unexercised-handler report.*
- **a-declared-exception-carries-its-reason** - the gate checks the declaration in both directions. A declaration the
  tree has outgrown fails as loudly as a missing one. `KEPT_THOUGH_DEAD` and `NAMES_THE_TREE_NO_LONGER_HOLDS` are such
  declarations. So are `NAMES_STILL_OWED`, `DEVIATIONS` and `NAMES_A_FILE_MAY_CITE_UNANSWERED`. A declaration keys on
  something durable. A name or a path glob survives an edit above it, and so does a production or a make target. A line
  number moves with the next edit. A declaration takes no line number. A declaration with no such key sits at the site
  as a comment marker. `not-prose:` is one. That marker says why the literal beside it holds a layout rather than a
  sentence. *Mechanised by `check_dead_code`, `check_documents`, `check_vendor_spec` and `check_prose`.*
- **a-gates-prerequisites-list-what-it-reads** - a stamp rule missing an input turns a skipped gate into a green one.
  *Not mechanised. The `Makefile` cannot check itself.*
- **nothing-is-dead-below-the-top-level** - `check_dead_code` walks top-level names. That walk cannot see a parameter no
  caller sets, and it cannot see an attribute nothing reads. A function computing the value and passing it on stays
  live, and the gate passes. A feature then runs end to end and does nothing, with the docstrings along the chain saying
  it does. Such a parameter comes out, or somebody declares it beside `KEPT_THOUGH_DEAD` with a reason. An attribute
  goes the same way. `Emitter` held both. *Not mechanised.*
- **a-gate-walks-the-hooks-or-says-why-not** - the hooks are Python this project maintains, and the same faults land in
  them. A gate taking only `gate.modules()` says beside the walk why the hooks fall outside. A reader cannot otherwise
  tell which roster a gate used. `check_dead_code` says why. *Not mechanised.*
- **a-parsed-source-is-split-on-the-newline** - a parse numbers a line by the newline. `splitlines` cuts on more than
  that. U+2028 and U+0085 are among the cuts, and `star.py` writes both in string literals. A list cut that way is
  indexed by a number that counted differently. Rebuilding it into a source stops the parse. *Mechanised by
  `check_conventions`.*
- **a-matched-name-is-spelled-somewhere-else** - a literal list of identifier names a check matches source against holds
  names the tree writes elsewhere. A name whose single occurrence is the list itself is a guess about code that does not
  exist. The check covering that name reads exactly like a check that fires. Such a name comes out, or somebody declares
  it with a reason. *Not mechanised.*
- **a-hook-refuses-through-one-helper** - a `PreToolUse` hook answers a refused edit through a shared helper rather than
  building the `{"decision": "block", "reason": ...}` payload itself. That payload's shape is a contract with the tool.
  A copy with a wrong key prints nothing and reads as an edit that passed. *Not mechanised.*
- **failures-are-not-ignored** - an ignored failure produces output that looks like success, and nobody goes back to
  check it. A shell script stops on a failed command. A recipe does not swallow one. A workflow asks whether an agent
  answered, and whether the answer holds anything. An agent that refuses answers under its schema anyway, and that
  answer is present and empty. Python raises, or reads the status before the output. A handler doing neither states a
  reason on a line of its own. `failure-is-reported:` says the failure comes out elsewhere. `not-a-failure:` says the
  exception is an ordinary answer. *Mechanised by `check_failures`. That gate reads the shell scripts and the
  `Makefile`. It reads the workflows and the Python too.*
- **a-reader-is-handed-its-prose** - a batch goes in the reader's prompt. A reader holding no fragment is a dispatch
  fault. An empty answer from such a reader reads exactly like a batch that passed. *Not mechanised.*

## Comments and documents

The prose checkers read a comment beside code and a document alike.

- **text-says-what-is** - the text avoids `now`, `no longer`, `used to` and `previously` about itself. The text narrates
  no plan step. *Mechanised by `check_documents` for the documents. `prose_rules` refuses the tense word wherever prose
  appears, through the `prose_rewrite` hook and the `check_prose` gate.*
- **each-document-keeps-to-its-domain** - `DESIGN.md` is context, perspective and architecture, and it repeats no code
  comment. `CHANGELOG.md` is what the change did. `PLAN.md` is what is still owed. *Not mechanised.*
- **a-step-carries-its-argument** - a new step of the normalization pipeline comes with the argument that answers the
  rules above `_Step` in `generator/normalize.py`. The argument says which invariant the step establishes, or why it
  names none. It says that the transform is local and mechanical. It says that a value the step produces is a global or
  a stack entry. *Mechanised in part. The `step-rules` hook refuses a step whose prose does not open that argument.
  Whether the argument is true stays with the review.*
- **a-reference-parser-has-one-name** - a pair of upstream projects both go by "the reference parser". A name of its own
  goes to either. **Haskell YamlReference** is the Haskell parser vendored under `third_party/yamlreference/`, and it is
  the token-level oracle. **YAML Reference Parser** is the spec-generated parser at `yaml/yaml-reference-parser`, and
  YAMLStar runs its Clojure build. A bare `YamlReference` names neither. "The YAML reference parser" names neither. A
  path, a URL and a vendored upstream file keep their own form. *Not mechanised.*
- **an-oracle-answers-and-a-checker-decides** - an oracle produces the answer. Haskell YamlReference produces a parse.
  The fixtures under `tests/spec/` hold the token stream a production must emit. `check_spaces._check_standings` writes
  out the comparison values. A checker produces pass or fail, and the hooks under `.claude/hooks/` call themselves
  checkers. An oracle comes from somewhere other than the thing it answers for. An oracle answering for itself answers
  nothing. *Not mechanised. Whether a thing answers or decides stays with the review.*
- **short-plain-sentences** - a sentence says a single thing, with subject and verb near the front and close together. A
  pair of plain sentences beat a compressed clause. Density is not the goal. Compression is where writing goes wrong. A
  compressed sentence keeps the rhythm of the style and holds no readable claim. Read a sentence once and at speed. A
  clause you have to hunt for wants the sentence split rather than tightened. *Mechanised by `prose_rules`. That module
  holds the checker. The `prose_rewrite` hook refuses a fragment as the writer writes it, and the refusal names no
  pattern. `faulty-prose-is-re-said-not-patched` says why. `check_prose` asks the same of the prose in the tree. The
  checker counts a sentence's commas and words. A single third-person pronoun passes, and a second makes the reader
  match a second referent. The checker refuses a possessive with its noun dropped. It refuses a bare participle where
  the subject belongs. It refuses a short tail hung off the last comma with no verb. That takes the appositive and the
  trailing absolute. It refuses a relative clause hung off the last comma at any length. A list runs to the length
  `prose_rules` allows. The checker refuses a verb elided after a negation. A verbless clause elsewhere in a sentence
  stays with the review. Deciding such a clause wants a part-of-speech tagger this tree does not have.*
- **a-sentence-names-its-actor** - the subject names a thing that acts. A caller acts, and so does an edit or a gate. A
  nominalized clause acts on nothing. `What libyeast adds cannot quietly become what libyeast changes` states a relation
  between a pair of abstractions and names nobody.
  `An edit meant to add something must not turn out to have changed an official production` says the same thing and
  names the actor. Prefer the active voice. `One is opened under the current code` hides who opens it. A fragment's
  prose may drop the subject where that subject is the thing the fragment documents. `Break the project into fragments`
  reads as `this module breaks the project into fragments`. The reader supplies the module from the fragment in front of
  them. That elision is active voice under a shorthand, and it passes. The rule refuses the elision that recovers
  nobody. *Mechanised in part. `prose_rules` refuses a sentence opening on `What`, `How`, `Which` or `Where` in either
  case. A leading `So`, `And` or `But` does not spare it. `Whether` opens the docstring of a predicate and passes.
  `fragment_faults` refuses a fragment opening on a word that points outside it. The checker refuses a form of `be`
  followed by a past participle where no `by` follows the participle to name the agent. A passive that names its agent
  passes. The checker refuses a sentence opening on a past participle as an elided subject.*
- **no-em-dash-and-no-colon** - an em-dash appositive replaces a sentence. A sentence that wants such an appositive
  wants a full stop instead. A writer refused `a - b` who writes `a: b` moves the appositive to another mark. That is a
  cheap trick rather than a fix. The term of a definition list keeps its colon. That term reads `**term:**` and leads
  its line. A sentence introducing a list keeps its colon. The claim is whole in front of that colon, and the reader
  takes the items a step at a time. A message in a string literal keeps its colon. The label naming the subject leads
  the message, and what the gate says follows the colon. *Mechanised by `prose_rules`, through the `prose_rewrite` hook
  and the `check_prose` gate. The checker refuses a semicolon and a raised dot alongside the em-dash. Either replaces a
  full stop.*
- **faulty-prose-is-re-said-not-patched** - a shape rule refuses a fragment and tells the writer no more. The writer
  says the whole prose of that fragment again, from the code and from the claims the old text made. A refusal that named
  the shape read as a to-do list. I kept the word order and swapped words around it until the pattern stopped matching,
  and the fault lives in the word order. So the refusal names no pattern and quotes no sentence. The refusal states the
  writing guidelines instead, and those reach the writer at the keyboard. A word rule runs the other way round, and
  `prose_words` names the word. A claim wants a grep rather than a rewrite. *Mechanised by `prose_rewrite`.
  `check_prose` prints the faults in full, on a run I ask for.*
- **use-the-words-this-project-uses** - the checker refuses a word the project turned down wherever prose appears.
  `meet` was the first. A reader opens a fragment. A pair of subspaces are `intersected with` one another. A thing
  satisfies a requirement. The checker refuses `answers to` as a verb. A rule covers a text. A name names something in
  the tree. A script follows a rule. The noun passes, and `an answer to a question` keeps its form. The checker refuses
  `carry` in any form. Say the verb the thing actually does. A file states its description. A list names its items. A
  parse continues at its tail call. The checker refuses `spell` in any form. A grammar writes a production. A source
  holds a name. Say which form a thing takes. The checker refuses `by itself` wherever it appears. Say `only` where the
  point is that one thing takes part. Name the thing that is missing where the point is an absence. The ban is a ruling.
  Its reason sits beside the pattern. *Mechanised by `prose_rules`, through the `prose_words` hook and the `check_prose`
  gate.*
- **a-name-uses-the-words-this-project-uses** - a bound name says no word the project turned down. A name splits on the
  underscores, and the words that split gives go to the same list the prose goes to. A name saying `spelling` sat beside
  a docstring saying `form`, and a reader had to work out that the two were the same thing. The next writer greps for
  the refused word, finds it in a live name, and writes that word back into prose. A rule name, an invariant name and a
  step name are strings rather than bound names. Such a name keeps its own words. *Mechanised by `check_conventions`.
  That gate reads the names a module binds against the same word list.*
- **a-piece-of-prose-is-written-once** - a file does not say the same thing twice. A C header and its source count as a
  single file here. A fragment does not write the same sentence twice either. A writer edits detail written twice in a
  single place, and the copy left behind goes quietly false. A pair of places wanting the same thing said give it to the
  first, and the second cites that one. *Mechanised in part by `check_conventions`, over the prose `collect_fragments`
  gives it. The gate compares a pair of proses by the runs of words they share. A copy with a word changed here and
  there is caught. A pair of paragraphs stating the same detail in different words score nothing alike, and those stay
  with the reviewer.*
- **a-file-says-what-it-is** - a file the project writes has a description of itself. A Python module says that in a
  docstring. Another file says it in a comment block. A file a script generates says so, and names the script.
  *Mechanised by `pylint` `C0114` for Python, and by `collect_fragments`. That module faults a file fragment holding no
  prose.*
- **a-file-description-comes-first** - a file's description is the first thing in it. The licence tag and the
  interpreter line go above it. An include guard, an import and a declaration come under it. A reader who opens the file
  cold learns what they are looking at before anything else. A description found lower down documents whatever it sits
  over instead. *Mechanised by `collect_fragments`. That module reads the block at the top of a file as that file's
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
- **a-nested-docstring-is-a-note** - a function nested inside another runs its docstring to the same length. Docstring
  syntax works there. `collect_fragments` folds such a docstring into the enclosing function's fragment. A reader who
  opens only the nested function sees nothing of it. A passage worth more than a note belongs to a function at the top
  level. *Mechanised by `check_conventions`. That gate reads the syntax tree.*
- **a-comment-describes-what** - and HOW only where it matters. Density matches the surrounding code. **The default is
  no WHY at all.** A sentence explaining why something is where it is goes in where a reader would otherwise undo the
  decision. That sentence runs to a single sentence, and a checker holds its claims like any other. This is a limit
  rather than a permission. The explanatory sentence is where the false claims in this tree turn up. A writer writes
  such a sentence from intent rather than reading it off the code. *Mechanised in part by `prose_rules`. That module
  refuses a clause giving WHY. Whether a comment describes the code stays with the review.*
- **every-claim-is-checked-before-it-is-written** - a superlative and an enumeration want checking without fail.
  `the one place` and `nothing else` are superlatives. `which declares A, B and C` is an enumeration. A comment asserts
  things about the tree exactly as code does, and it gets the same evidence. *Mechanised in part by `prose_rules`. That
  module refuses a universal and a superlative, through the `prose_words` hook and the `check_prose` gate. The checker
  names the determiner classes rather than a list of words. It refuses `the sole door` beside `the only door`. It
  refuses a negation in subject position, and a negation passes in object position. `check_documents` verifies the cited
  names. A claim no word marks stays with the review.*
- **a-refusal-may-write-the-words-it-bans** - a hook refusal names the rule it enforces, and ends on that name. Such a
  text quotes the wording it turns down. The prose rules go past it. A refusal naming no rule goes with ordinary prose.
  *Mechanised by `prose_rules` and `collect_fragments`. Those modules read a refusal as a text of its own rather than as
  prose of this project.*
- **a-count-is-answered-for** - `DESIGN.md` may state a count in front of a name the code measures. `PLAN.md` and
  `CHANGELOG.md` may state none. `check_documents._NOT_A_NUMBER_OF_THE_TREE` lists what may pass, and the checker
  refuses a numeral outside that list. A determiner reads as a numeral. The checker refuses `zero`, `one` and `two`
  where it refuses another numeral. `zero-width` names a guard that takes no width, and that name appears in the list
  under both its forms. The text replacing a number says **which ones** rather than **how much**. `a handful` and `most`
  and `nearly all` are unfalsifiable. *Mechanised by `check_documents` for the numbers. `prose_rules` refuses the vague
  quantifier that replaces a number.*
- **every-cited-name-exists** - a backticked name names something in the tree. *Mechanised by `check_documents`.*
- **an-invariant-docstring-states-its-claim** - an invariant's docstring is prose, and the prose rules cover it. The
  invariant's name string keeps its own form. `every-difference-is-between-character-sets` takes that name and reads
  `Check that a difference is between character sets`. The name is an identifier rather than prose. `check_prose` skips
  that name. *Mechanised by `check_prose` for the docstring. The name stays with the author.*
- **a-mechanised-rule-names-its-convention** - a convention marked mechanised above names the module that mechanises it.
  That module's docstring names the convention back. A rule otherwise lands in the code while the list here still
  describes the tree before it. Entries in this section came to credit a mechanism that had moved on without them.
  *Mechanised by `check_conventions`.*

## Layout

- **wrapped-at-120-columns** - the limit holds over code and comments. It holds over markdown too, and over the
  `Makefile` and CMake. *Mechanised by `vet-format-*`.*
- **a-tracked-file-holds-ascii** - the rule covers code and comments. It covers markdown and the grammar too, and the
  `Makefile` and CMake. A character past ASCII looks like the ASCII character it replaces. An em-dash comes out as a
  full stop and a second sentence. A curly quote comes out straight. An arrow comes out as a word. A text naming such a
  character writes the escape that names the codepoint. `check_ascii._MAY_HOLD_ANY_BYTE` declares the paths holding such
  a character as content. *Mechanised by `check_ascii`, and the `ascii_only` hook refuses such a character as an edit
  writes it.*
- **a-file-of-the-tree-has-a-reader** - a tracked file holds prose, and `collect_fragments.language_of` names the reader
  for it. A file holding no prose of ours goes in `gate.UNREAD`, and the comment above that list says why. Without a
  reader the checkers find no fragment, and the prose in the file lands unread. *Mechanised by `collect_fragments`, and
  the `unread_files` hook refuses such a path as an edit names it.*
- **formatting-is-make-reformat** - a writer runs no formatter by hand and reflows no text by hand. *Mechanised by
  `vet-format-*`.*

`.claude/rejected.md` holds what a review proposed and the author turned down, with a reason beside the entry.
