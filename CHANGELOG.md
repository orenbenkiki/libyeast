# Changelog

`CHANGELOG.md` lists the notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The ABI is unstable through `0.x`.

## [Unreleased]

### Added

- Project framework. CMake builds a shared library and a static library. The build hardens both, and controls symbol
  visibility. There is an incremental pre-commit `make` gate and tests under acutest. There is coverage with a
  `// UNTESTED` contract. There are Doxygen docs with a completeness gate, and a package-consumption test.

- Continuous integration. There is a GitHub Actions workflow per sub-gate. They cover static quality and C tests, and
  the generator pipeline and the docs. A workflow has its own status badge, and CodeQL analysis runs beside the
  workflows. GitHub Pages publishes the API docs and an HTML coverage report. A workflow runs on a pull request as well
  as on `main`. A change `make pc` refuses locally therefore cannot land remotely. The docs workflow is where that
  matters, and it holds the `// UNTESTED` coverage contract and the docs completeness check. A public symbol left
  undocumented, or an uncovered line left unannotated, would otherwise fail only after merging. The docs workflow builds
  on a pull request. Its deploy waits for `main`.

- Version API. It is `ys_version`, `ys_major`, `ys_minor` and `ys_patch`.

- Token-source API surface. `ys_new_yaml_memory_parser`, `ys_new_yaml_stream_parser` and `ys_new_yeast_stream_reader`
  make a `ys_token_source`. `ys_read_token` pulls a token from that source. `ys_delete_token_source` releases the
  source. Beside them sit the `ys_fd_reader` and `ys_fp_reader` adapters, a pluggable allocator, and the
  `ys_counting_allocator` leak counter. A caller reads tokens parsed from YAML and tokens replayed from a yeast wire
  through the same source. Code over tokens therefore does not know or care which made them. The two are a tagged union
  whose arms hold genuinely different state, with only the kind above them. `ys_read_token` fills the caller's token and
  returns a `ys_status`. A halt token does not repeat. The status is `YS_OK` with a token, or a negative status with
  `errno` set. A delegate failing is `YS_FAILED_STREAM` for the reader, or `YS_FAILED_MEMORY` for the allocator. Reading
  past the end gives `YS_FAILED_ACTION`. The call fails on its own terms rather than on a delegate's terms. A host
  failure ends the source there, with no `end-stream` to close the `begin-stream`. That missing close is the sign it did
  not finish. The codes a document cannot cause therefore leave the token model. A parser out of memory gives such a
  code. So does a failing reader, and so does the wire reader's own trouble. `YS_CODE_ERROR` is the `!` the wire writes.
  `YS_CODE_ERROR` names a malformed document or a malformed wire. Both are bad data. Nobody has written the parser core
  yet. A parser's `ys_read_token` returns a "not implemented" error.

- Token-sink API surface. It mirrors the token source. `ys_new_yeast_stream_writer` makes a `ys_token_sink` over a
  `ys_bytes_writer`. `ys_write_token` feeds it, and `ys_delete_token_sink` releases it. A token stream is therefore sent
  onward the same way whatever its destination. It has a pair of arms. The yeast writer serializes tokens to a wire.
  `ys_new_yaml_stream_emitter` writes the bytes a token spans. A wire replayed through the emitter therefore
  reconstructs the YAML it came from. That is the round-trip the wire exists for, tested over the whole fixture corpus.
  `ys_write_token` returns a `ys_status`. It is `YS_OK`, or `YS_FAILED_STREAM` if the byte transport failed.
  `YS_FAILED_ACTION` names a token the sink cannot write. That is a code the wire writes nothing for, text that lies
  about its code, or a `YS_CODE_ERROR` handed to the emitter. The emitter renders rather than judges, and refuses such a
  token for a caller to filter above. It moved from a `ys_bytes_writer` to the sink. The byte transport stays
  underneath, as a `ys_bytes_reader` does for a source. `ys_delete_token_sink` replaces `ys_close_writer`, and its flush
  is where a buffered write finally fails. The `ys_status` failure names dropped their direction. `YS_FAILED_STREAM` is
  for a reader or a writer, and `YS_FAILED_MEMORY` for the allocator. A source reads and a sink writes, but both close a
  transport the same way.

- Character decoder. The decoder validates UTF-8 input and classifies it against the grammar. It assembles no Unicode
  codepoint. A character becomes a 32-bit key. The key holds the id of the character where the grammar names it. The key
  also holds a bit per character set the grammar tests, and the count of bytes consumed. A test in the parser is
  therefore a single comparison or a single AND. The generator writes the tables from the grammar, and a gate refuses
  drift.

- Annotated grammar. `grammar/yeast-spec-1.2.yaml` is libyeast's own grammar, and the source the generated files come
  from. The file holds the YAML 1.2 rules together with the yeast tokens those rules emit. The official grammar cannot
  express those tokens. That grammar inlines the indicator characters, losing the structure the token layer hangs on,
  and names no token at all. The same file writes down the yeast token format. It holds the notation and the codes. Rule
  by rule, it says what a rule emits and why. The gates keep it honest. Erasing libyeast's additions recovers the
  official grammar exactly. Somebody therefore writes the grammar by hand where a gate cannot reach, and a gate proves
  the rest of it. A character the parser consumes must lie within a token action. A forgotten action then fails the
  build. It does not emit an `unparsed` token years later. An `end-` marker must close its own `begin-` marker. That
  holds on any path and under any context. The chomping and the resume policy make no difference. A rule that emits
  tokens must say which, checked against the grammar itself. A note that is wrong then fails as surely as one that is
  missing.

- A byte order mark takes no column where a consume asks for it. There it is no character of the line. It neither ends
  the line's start nor advances the column. A token that follows a mark on the same line therefore reports the column
  that token would have had with no mark there. The `#` of `l-document-prefix.bom-comment` is at column 0. The mark
  would have put it at column 1. A column reads the indentation of a construct, and a mark before that indentation is no
  part of it. A mark inside a scalar is not that. The content classes hold the mark in their upper range. There the mark
  takes a column like any other character. The set the consume names is what tells the two apart.

- `<column>`, a special rule beside `<empty>` and `<start-of-line>`. It is the column the parse has reached. The count
  starts at `0`. A construct is indented by that column. The parse reads that column where it has just taken the
  indentation. A lookahead does not work it out.

  A compact collection is the first written that way. `s-l+block-indented` peeked at the spaces after its `-` or `?`
  through `<auto-detect-in-line-indent>`. That peek wanted at least a single space. It then consumed exactly that many,
  and entered the collection at `n+1+m`. The rule takes the run as its own indent token and enters at `<column>`. That
  column is the same value. The run begins a character past an indicator at column `n`. The collection is therefore
  indented by the column that run leaves the parse at. A run over a character class is possessive and gives nothing
  back. Peeking a length and then consuming that length is therefore the same as consuming it outright. The value needs
  no name. The parse tries both compact alternatives at the same position. Naming it `n` would have been wrong. A
  parameter passed as itself is passed by reference. The write would have escaped into `c-l-block-seq-entry`, and from
  there into the enclosing sequence's own loop. `l-yeast-stream.seq-dedent-multilevel` catches that.

  `<auto-detect-in-line-indent>` goes with the peek it served. That rule was its single site.

  The block collections follow. `l+block-sequence` and `l+block-mapping` looked ahead for the first line holding
  something other than a space. That search ran past the current line where the parse was mid-line. The empty lines it
  then ran past had no bound. Those rules measured an entry against the line the search found. The parse is already at
  the start of the line the first entry is on. That line's own run of spaces is therefore taken as the indent token.
  `<column>` is the `n+m` the entries measure against. That value must be deeper than the indentation in force. The
  entries then run at it. `l-block-seq-entries` and `l-block-map-entries` are libyeast's own rules, and take that
  indentation as their `n`. The first entry has had its own spaces consumed already. A later entry begins with
  `s-indent(n)`. That indent ends the collection where a line holds less indentation. A call passes that indentation as
  an argument rather than writing it. A parameter passed as itself is passed by reference. `s-l+block-collection` calls
  the mapping with a bare `n` where the sequence goes through `seq-spaces`, and a write would have escaped one of the
  two.

  With the collections answering for themselves, the pipeline step that had been doing it has nothing left to find. It
  hoisted a loop into a production entered at the value the loop measured. A step that finds nothing is a fault by the
  pipeline's own rule. It is gone, and the phase is `clear-m` and `read-global-m`.

  The block scalar is the last of them. It resisted longest. Its indicator decides. Its first content line is calls
  away. That line is what the decision is about. The decision therefore travels as `i`. `i` is `given` where the
  indicator named the indentation. `i` is `detected` where the indicator named none. The content rules pass it to
  `s-indent-floor`, where the pair differ by a single node. That node is exactly `n` spaces, or a span with the guards
  on what it measured. That node is also where the parse establishes `n`. `i` is a finite parameter. `monomorphize`
  therefore specializes `i` into the names as it does the context. The generated parser tests no mode. `lift-chomping`
  becomes `lift-setters` and inverts both setters. Phase 0 settles `no-i-t-parameters`.

  A scalar with no content line at all establishes no indentation. The trailing empty lines are what would have said it.
  libyeast therefore takes a trailing empty line whole. The widest of those lines is the floor. The trailing comment
  measures against that floor. That matters for `keep`. There those lines are the scalar's own content, and the
  indentation decides which lines belong to the scalar. The YAML Test Suite's `JEF9/01` is what says so, with a `- |+`
  and a line of spaces.

  libyeast runs nothing that reads ahead of what it has consumed. `<auto-detect-indent>` has no site and the interpreter
  evaluates none. The IR node stays. The vendored grammar writes it, and a single reader loads the pair of grammars.

- `gate.run_deep` puts a gate's walk of a grammar on a thread of its own, written once rather than copied per gate. It
  gives the work a stack deep enough for the recursion a transformed grammar needs. It ends the process with whatever
  the work raised. That last part matters. An exception inside a thread is printed by the thread's own excepthook while
  the main thread exits `0`. That reads as a green `make pc` over a check that did not finish. One of the copies caught
  only the `SystemExit` the gate reports through, and it took a step going idle to notice.

- `check_documents` and `check_dead_code` are gates of their own. Somebody cut them out of `check_normalize`. A gate's
  prerequisites have to list what it reads, and `check_normalize`'s did not list the documents or `scripts/`. Editing
  `DESIGN.md` therefore re-ran the formatter and skipped the check that holds DESIGN's counts. Those counts passed on a
  stamp written before the edit. Splitting is what makes a list answerable. `verify-documents` reads the documents and
  the grammar. It also reads the sources a citation can name. `verify-dead-code` reads `generator/` and `scripts/`.
  `verify-normalize` reads the corpus and the grammar. Counts in a document and counts in the generator's own prose stay
  a single gate and a single checker. A count is a count wherever somebody writes it.

- A question the generator answered more than once has a single answer. `interpreter.run` walked the whole grammar to
  ask whether it pushes indentations. It did so per fixture and per stage. The answer belongs to the grammar rather than
  to a run. `invariant_faults` says in its own docstring that a table takes a count once. Its last law then re-derived
  those counts serially. It took a grammar at a time. `normalize._denoted_spans` was `chars.spans` written out again.
  `_relevant_finite` propagated a value up the call graph in rounds over the whole grammar. The callers of what moved
  are the callers with anything to reconsider. This buys no speed. The run-to-run spread of `make pc` is wider than any
  of it. The change buys a question a single place to answer it. These questions had a second place apiece, and no gate
  held a pair to agreeing.

- `check_dead_code` reads a call at a module's top level as running what it names. The walk's roots were the names a
  module reads outside its own definitions. The names the module defines came off that set. That is right for a mention
  beside a definition. It is wrong for a call. A check a module runs on itself was therefore reported as a function
  nothing reaches. A bare mention still roots nothing.

- A pair of families of kinds naming the same members draws a refusal at the place that defines them. `ir` says of
  itself that such a pair is a single family under a pair of names. `ir` said so in a comment nothing could act on.
  `ALWAYS_CONSUMES` and `CONSUMING` asked different questions over a single membership. That invites a distinction the
  members do not support, and lets a reader answer either through the other. They are one family. A second such pair
  raises where a module imports `ir`. It reads as a fault rather than as a pair of things the file knows about.

- The prose beside the C obeys the rules the prose beside the generator obeys. The list went unread, and it showed.
  `decoder.h` named `ys_new_string_parser` long after that constructor took another name. Beside that name, the header
  counted the character sets the grammar consumes, and no checker reproduces that figure. `parser.h` wrote out the
  `ys_status` values its own fields hold. Those values were wrong. `messages.c` described the message the table gives
  for running out of memory. The table does not hold one, and `messages.h` says outright it cannot. A host failure is a
  return value rather than a token with text. `check_documents` reads `src/` and `include/`. It takes a comment's own
  text and blanks the string literals first. A `//` inside a literal is then no prose.

- `normalize`'s own prose states no count the gate prints beside it. A comment gave the conflict count before and after
  a step the pipeline does not run. The first half duplicated a figure `check_normalize` reports on a run. The second
  measured a grammar nothing builds. Neither reached the count rule. A comma sat in the place of the noun the numeral
  counts. The narrow rule the generator's prose obeys names a numeral in front of one of the project's nouns.
  Punctuation between the pair puts the text out of the rule's reach.

- The count rule joins the generator's prose before reading it. A document goes through the same way. A formatter wraps
  a comment block and a docstring at the column limit. A count is therefore as likely to straddle a wrap as not, and a
  checker that took a line at a time saw neither half. Such counts were in the tree. A fixture total among them came
  from a corpus smaller than the corpus that replaced that one. A comment sharing a line with code keeps its marker. The
  reader takes that comment apart from the code. It is bounded by that line. Joining it with the comment below would
  read a pair of unrelated sentences as one.

- The `Makefile` obeys the column limit the other sources obey. The formatters skip it. That is why it drifted. A
  comment had reached half again the limit with nothing to say so. The limit counts characters rather than bytes. An
  em-dash is more of the second and one of the first.

- `review_input` clears what it writes into. The clearing happens whether the run prepares anything or refuses. A
  prepared thing is a single file where the output is short. A long output goes into numbered parts. A run that
  shortened the output therefore left the previous run's parts beside its own. Neither set said which run wrote it. A
  refused run left them whole, for a reader to take as this change.

- An answer kept against an object's identity is kept by `ir.kept_for`. An asker wrote out the store, and the check that
  the id still means the object the asker took it for. Beside those sat the object held with the answer to keep that
  true. This happened across a pair of modules. Both had a comment saying it kept the same discipline as the last. That
  is what a shared comment says once.

- `_NAMES_STILL_OWED` answers in both directions, as `_NAMES_THE_TREE_NO_LONGER_HOLDS` already did. A name there excuses
  a citation of something unbuilt. The day the pipeline builds it, the excuse stops any check of the citation. The tree
  said nothing about the day coming, though the list's own comment says a name comes off then.

- `EndMustConsumeAction` matches wherever a parse reaches it. The coverage gate reads it that way. The node sat among
  the kinds that may refuse, and a reason went with it. That reason gave the answer as whatever the way behind the node
  comes to. That is equally true of any action. `DidConsumeSinceOpenGuard` asks whether the turn matched, and the close
  says nothing about it.

- A staged path reaches a reviewer. `review_input` refuses a change holding a path that reaches none. `include/yeast.h`
  was in no slice at all. Neither were the files under `scripts/` and `grammar/`, the build files, `README.md` and
  `CONTRIBUTING.md`. A change to any of them therefore reached no reviewer. It went unseen. A review reads what the
  input hands it, and cannot see the rest of the change. The input names the fixtures rather than diffing them. A corpus
  change is a pile of files. Their bytes say less than a list of what arrived, went or got a new name.

- `check_dead_code` counts a kind built where something reaches the definition that builds it. It gathered the calls in
  a module before the walk had said what a run reaches. A kind constructed inside a function nothing calls therefore
  read as live. That is exactly the shape a transformation parked out of the pipeline leaves behind.

- The citation check sees the names the grammar uses. It wanted a pair of letters before the first hyphen. That told a
  name from the arithmetic a comment writes the same way. `c-printable`, `s-white`, `l-yaml-stream` and `b-break` have
  one. The rule therefore read past the productions the documents and the prose cite. The segments after the first tell
  the two apart. A name has a letter in those segments, and `n-1` has none.

- A comment sharing its line with code is prose like any other. `check_documents` read the lines that begin with a `#`
  and the docstrings. A trailing comment therefore reached neither the count rule nor the citation rule. Both were being
  broken there. Somebody wrote a fixpoint's depth down beside the loop that measures it. A step nobody ever built had a
  name beside the field it was to remove. The comments come from `tokenize`. That module finds a comment at any
  position, and does not mistake a `#` inside a string for a comment.

- A text file the generators open names its encoding. `ruff` holds them to that. Left unnamed, Python decodes by
  whatever `LANG` says. `DESIGN.md` is full of em-dashes. Reading it therefore works on a developer's UTF-8 machine and
  raises in a C-locale container. Reads and writes across `generator/` and `scripts/` alike were open to it. The rule
  that catches them also caught the `pathlib` reads a grep for `open(` misses.

- `CHANGELOG.md` holds its own citations to the tree. Naming what a change took away is what an entry is for. A
  changelog citation naming nothing in the tree was therefore exempt outright. An entry describing the mechanism
  correctly and naming it wrongly reads exactly the same. This file wrote `Step`'s fields under a name the type did not
  have. The invariant report was called by a name that named nothing. This file listed the claims the pipeline makes
  under invariant names that named nothing.

  `_NAMES_THE_TREE_NO_LONGER_HOLDS` declares what the entries legitimately name, and a citation outside it fails. A
  declaration the tree holds again is stale and fails too. Asking whether the tree *defines* a name is a different
  question from whether it writes one. The permissive checker counts a name written in a string. The list is itself
  strings, and under it a declaration in the list reads as stale.

- Comments and docstrings must cite real names, as the documents must. A comment may name a step, an invariant or a
  production that does not exist. That makes the thing look like it runs. The citation check read the documents and not
  the code. That is how `check_normalize`'s docstring described a pipeline stage called `speculate-folds` when no such
  step existed. A single read takes a file's comments and docstrings, and asks both the count rule and this rule of
  them.

  A pair of things also count as answering a citation. A hyphenated name resolves through its underscore form. The same
  transformation is a step called `lower-continuations-into-conflicts` and a function called
  `lower_continuations_into_conflicts`. A parameter's values are names too. `block-in` is a context.

- One count rule covers a document. A number is a fault unless something justifies it. The list of what a document
  allows is where the pair differ. `DESIGN` may also state a count outright. That count is a numeral in front of a name
  the code measures, and the gate checks it against the code. That was its rule. A count written any other way reached
  nothing.

  A pair of shapes escaped that way. The rule blanked a heading before reading any number in it. A heading counting the
  section beneath it therefore went unchecked. A sentence said how many of the rules the pipeline obeys the grammar
  satisfies. That named no measurable quantity. Both are refused. Neither count is measurable. Both are therefore
  rewritten as prose stating no number.

- `check_provisional.py` and `check_determinize.py` are gone. The `Makefile` targets went with them, and so did the
  mentions in `DESIGN.md`. Neither could execute a line. `check_determinize` imports `determinize.py`, deleted with the
  machinery no phase produces yet. `check_provisional` calls `normalize.provisional_faults`. That function went when
  somebody cut the pipeline back to the chomping. Those same changes took both out of the `verify:` list instead of
  removing the files. Both went unrun, and nobody reported a break. Either read as a parked gate rather than as one that
  cannot import.

  A balance net over the provisional actions belongs in their place. That net belongs to `PLAN.md`. That document
  describes the actions the net would answer for.

- Dead means unreached rather than unmentioned. The word covers a module as well as a name. The module walk starts from
  what runs. That is the `Makefile`'s own `python3` invocations, plus what `RUN_FROM_ELSEWHERE` declares with a reason.
  Imports reach the other modules. The definition walk starts from what a module does at import. It also starts from
  `main` where something runs that module. A definition reaches whatever definition it reads.

  Counting mentions instead made a definition its own witness. A pair that call one another hold a mention apiece. Both
  therefore read as live. `PEEK_OUTPUT`, `WRITES_NOTHING` and the span algebra's `_EVERY_CHARACTER` sat unread behind
  exactly that. They are gone. A kept transformation's reason covers its helpers. A helper needs no reason of its own.

  The walk reads what runs off the `Makefile` rather than keeping a list beside it. A gate added there is therefore
  reached the day somebody adds it. A gate taken out of it is dead that same day. A list kept by hand catches the first
  of those and misses the second.

- A fixture may say how much input the parse takes before reaching the rule. It writes `p=N` in the name, beside the
  `n`, `c` and `t` and the `r` and `i`. The run takes those first `N` characters before beginning, and drops the token
  they built. The line and the column are then what those characters make them, and so is what a look-behind finds
  behind the parse. A name asserts something else. A parse reaches a rule mid-line *after something*. Real leading
  context names that something. A parse enters a compact collection past its `-`. `s-l+block-indented`'s fixtures
  therefore name `p=3` beside their `n=2`, and hold the two spaces and the dash that put a parse there. A fixture the
  parse reaches mid-line names it.

- Grammar normalization. It is an ordered pipeline of semantics-preserving grammar-to-grammar transformations. They take
  the hand-authored grammar toward the canonical form a state machine falls out of. In that form a terminal is a
  character set and a run is a repetition of a set. The pipeline grows a goal at a time. The pipeline is a sequence of
  phases. A phase owns a single invariant and adds steps until that count is none. The gate enforces the invariant from
  the end of a phase. The law "none stays none" then makes a later step keep it. A phase finished with a green corpus is
  a checkpoint that lands on its own.

  An invariant is a count and not a yes-or-no. Its test gives back the places the grammar breaks that invariant. The
  length is therefore the count, and the contents say where. A `Step` declares what it does to an invariant. A step
  takes the count to none. A step may instead lower the count, or establish the invariant where nothing could have asked
  before. `Step` documents the fields those declarations go in. `invariant_faults` holds a step to the law. A count does
  not rise. A settling step leaves none, and a count at none stays there. `lapses` is the licence to break an invariant.
  It is written `{invariant: reason}`. A lapse naming an invariant no step names is a stale declaration and a fault. So
  is one naming an invariant the step does not break. `untested_steps` counts the steps promising what nothing checks. A
  step must name an invariant. Failing that, the step names in `untestable` why it can have none. `unsettled_invariants`
  counts what the final grammar still breaks, whatever the steps settle between them.

  A `Step` with no transform is a **claim**. It changes nothing. The step says that its invariants read none at that
  point in the pipeline. A claim is how the pipeline writes down a property it inherits rather than makes. The law holds
  a step behind a claim to that property exactly as it holds a step to a settled invariant. Naming such a property on
  whichever step happens to run next reads as that step establishing it. It puts the blame for a later break on the
  wrong side of the line. A step naming an invariant **the grammar already held at none** is therefore itself a fault.
  It says to write a claim instead. The properties named that way become claims. Those are
  `every-conditional-way-is-gated` at the door, and `no-production-reaches-itself-unconsumed` once the optionals are
  ways. They are also `accepted-and-gated-charsets-are-equal` where the consumed sets agree, and
  `every-path-reaches-a-leaf-way` for the leaf paths.

  Phase 0 is the two parameters the grammar sets by matching. Those are the chomping `t` and the block scalar's
  indentation mode `i`. A parameter is set by an indicator and read productions later through the environment. A read of
  either therefore means nothing until a reader knows the caller. `lift-setters` inverts a setter into a `(case)` on its
  parameter. The case matches the condition for a given value. It turns a production holding a variable as a local
  out-parameter into an ordered choice over that variable's values. The block scalar becomes a choice over strip, keep
  and clip, and over given and detected alike. A branch fixes the parameter to a literal it hands the setter and the
  reader both.

  `monomorphize` then specializes the finite parameters away. Those are the context `c`, the lexical `t` and `i`, and
  the resume policy `r`. The step copies a production it reaches once per combination of those values. A copy's `(case)`
  and `(flip)` on them evaluate to that copy's values. The step fixes the values into the copy's name, as
  `ns-plain-char_c_flow-in`. A call passes none of them. It follows references from the root's copy under a resume
  policy. Those copies are the machine's start states. The combinations it makes are therefore the combinations that
  occur. A value at its default, the no-resume `r`, is not in the name. The root therefore stays `l-yeast-stream`, and
  the recovery re-enters the copy the resume policy names. The integers `n`, `m` and `f` are what stay parameters. It
  rests on a rule the grammar keeps. The grammar switches on a finite parameter. The step does no more. An implicit
  key's commit softens by context. The grammar therefore says so in a `(case) c`. A key that will not parse is simply
  not this key. Its key branches are the bare item. Its `else` is the commit. The parser's `(commit)` is the same hard
  cut throughout. `(case)` grew that `else` for it. Past that step the grammar holds no `t` and no `i` at all.

  Phase 2 is the character questions. A set of characters has many written forms, and a rule asks about it in many ways.
  A set is a character, a range, or a union of them. It is a base with exclusions, a reference, or the item a lookaround
  peeks. They come to the bit the parser tests. A difference is such a set where both its sides are sets. The sites
  where it was not are `ns-double-char`, `ns-single-char` and the `ns-tag-char` copies. A difference takes whitespace or
  an indicator out of something that is an escape *or* a character. That is a `\` and a character after it, or a `''`,
  or a `%` and a pair of hex digits. The subtraction therefore ranged over a language rather than a set, and no bit
  could say it. `distribute-differences` takes a difference into the ways it subtracts from. The ways keep their order.
  A run of single characters takes the subtraction once, over the union they already form. A way that takes a pair of
  characters or more keeps its whole language. A subtracted set takes a single character, and so reaches only a match of
  that length. From there a difference sits between a pair of sets. `every-difference-is-between-character-sets` says
  so. `lower-char-sets` then folds them away, leaving `no-diff-nodes` at none and the notation gone from the grammar the
  phase hands on. `lower-char-sets` says a set as the sorted disjoint codepoint intervals it denotes. From its end a
  question about a character is therefore a `CharSet` or a literal. The step takes a maximal set rather than the sets
  inside it. The intervals of a union belong to the union. This leaves a reference a match takes in place. That
  reference is the caller's hold on the production that says the set. Inside a lookaround the walk reads through the
  reference instead. A peek holds the question rather than the hold. The walk reads through an annotation on what a peek
  names. A peek counts on the question a peek asks rather than on the shape that names it. A probe emits nothing and
  gives back what it read, and the code around `c-comment`'s `#` is therefore dead inside one. That peek was the last
  character question the phase left in place. The peeks beside that one in the grammar the phase hands on are copies the
  later steps make. A peek there is a `CharSet`. The `(exclude)` guards are the last lookarounds. They ask about a line
  rather than about a character. The phase follows the specialization. A set the context picks denotes nothing until a
  reader knows the caller. Saying it once also makes the form canonical. That is what lets the sweep do its own work. A
  pair of productions denoting the same characters differently are structurally unequal and do not merge. The merge
  reads shape rather than extension.

  Phase 3 is the block scalar's leading-empty floor. `clear-f` gives the value an end. `s-indent-floor` reads the value.
  That production clears the value where it returns. The reader clears it and not the writer. The parse measures the
  floor deep inside the leading empties, and hands it up to whatever asks. `read-global-f` then takes the declaration
  off a production and the argument off a call. A read becomes a `GlobalValue`. The reads hide where the generic walker
  does not go. It takes a `ParamValue` as a value and does not visit a value a field holds directly. `s-indent-floor`'s
  `Le(f, n)` is therefore a read both the count and the rewrite had to walk the fields themselves to see. The value does
  not nest, and that licenses the drop. The interpreter says so rather than the argument. A `(set)` puts the value on a
  stack the global keeps. A `(clear)` takes it off. A slot sits beside it. The interpreter counts the reads where the
  two differ over the whole corpus. The gate holds that at none. Made to nest, the same net reports them.

  Phase 4 is the detected indent, and `clear-m` and `read-global-m` do what `f`'s pair did. A read of the value happens
  once over a region something else can write in. That is what made it possible. The block header measures it. The
  scalar that asked reads it, a construct at a time. A block collection did read the value on the turns of its loop. A
  single slot cannot hold a value read that way. A collection or block scalar the loop entered detected an indent of its
  own in between. The grammar answers for that. A collection enters its entries at the indentation the first
  established, and the count reads none. `BindTree` maintains the stack beside the slot too. A write is a write whatever
  form it takes. A block header's indicator sets the detected indent through one, and a global written that way had been
  invisible to the net.

  Phase 5 is the indentation, and it is not one of the parse's own values. A nested collection measures the entries
  against an indentation of its own. It therefore goes on the parse's stack rather than into a slot. `push-indents` puts
  a push before a call measured against an indentation other than the level in force, and a pop behind it. Both halves
  sit in a single way of a single production. The way names the level. `read-indents` then takes the parameter off the
  declarations, the calls and the reads. That leaves the stack to answer. The parameter stays beside the pushes while
  they go in. That says the pushes go where they should. The interpreter compares the two at a read of `n` over the
  whole corpus, and only then does the parameter go. Skew a push by a level and fixtures refuse. The agreement is
  therefore a check rather than a coincidence.

  `hold-established-indents` goes first. It is for the indentation a call hands back rather than enters under. A block
  scalar cannot know the content indentation until the parse reads the first content line. That line measures it, and
  the value travels out through the calls that passed the parameter itself. That is a write whose readers are a
  production away. Taking the parameter out would silently take that write apart, and no local check can see it. The
  step inlines the chain until the write and its reader are in a single way. The write is then the push that way ends by
  taking back. With it in front, `push-indents` has a single rule instead of a pair.

  Two of the pushes' levels were wrong in a way that wanted the corpus to say it. A pop that names the level
  re-evaluates that expression when it runs. `<column>` or `n+1` means something else by then. A pop therefore takes
  what is on top. The stack's own kinds refuse a bad pairing. Working out what to push is also not a read of the
  indentation in force. The two differ where the level is the parameter itself. That is the same exemption the arguments
  of a call already had.

  Phase 6 is the empties, and `lower-optionals` is its first step. `x?` becomes `x | <empty>`. The empty way then sits
  beside the way that reads. It hides inside no node. It is the same match and the interpreter says so. An `OptTree`
  tries its item with the continuation behind that item. The `OptTree` rewinds where that fails, and takes the
  continuation without the item. That is the alternation tried in that order. The parse therefore has to know nothing
  about what follows.

  A run over a character class is a consume and not a way, and the interpreter draws that line where the grammar does.
  Such a run is single-outcome by construction. A character off its own set follows the run. It is therefore taken whole
  and judged whole. `s-indent-le` needs exactly that. That rule wants the maximal run, and then its length against `n`.
  Falling back to a shorter run would let an over-indented line pass as if it had none. `span-consumes` writes a run as
  a consume. `x*` is a `ConsumeSpanAction`, and `x+` is the character with that span behind it. A counted consume goes
  the same way. `x{n}` over a character class is a run of up to `n` characters of the set. The guard asks whether the
  run reached `n`. A count the parse works out becomes the pair of ways it denotes. With those rewritten, `RepTree` is
  gone from the grammar.

  A repetition of a way becomes the ways it denotes, and `lower-runs` says both forms that way. Those are a turn, a
  recursion taking the turns behind it, and a settled region around the turns after the first. A turn takes a character.
  A turn taking none is a turn the run did not take. The region settles the turns it holds. A failure past its close
  therefore gives the whole run up. The parse does not take fewer turns. The `(***)` form offers the turn not taken as a
  way of its own. The `(+++)` form offers no such way. That is the difference between them. The interpreter had been
  saying so throughout, and its two arms differed by that single line. `no-star-or-plus-nodes` settles at none over the
  runs in the grammar. Past that step the grammar repeats nothing.

  The ways are an ordered choice. It is that settled region rather than anything about the choice that holds a parse to
  the longest run. The guard a turn holds ends the run, and not a gate on the choice. The grammar says outright that a
  turn taking no character is no turn. It does not leave that to a comparison of positions inside the interpreter's own
  loop. The grammar repeats a way at no site. A character class comes under a pair of kinds, the maximal run and the
  counted run.

  A function that dispatches on node kind raises on a kind it has not heard of. This covers more than the questions that
  answer yes or no. A reader had taken the rule as being about the booleans. A wrong `False` there turns a consume into
  a way. It is about any answer given by default. `validate_grammar.consumed` walked into the children of a kind it did
  not name. A new way of taking a character would therefore have yielded nothing of its own. The characters it took
  would have passed the token-coverage check unannotated. `check_grammar_docs.emitted` did the same for a new way of
  emitting. Such a way would have read as documented while saying nothing. `chars.denote` answered "no characters" where
  it meant "no answer". `grammar2decoder.defined` answered "defines no character". That would have left the character
  out of the decoder tables. The drift gate can see a table that changed, rather than a table that lacked the entry from
  the start. `check_decoder` and the character-run invariant kept a list apiece of what repeats. Such a list goes stale
  the moment somebody writes a run a new way. The kinds it names are gone, and the list then reads none.

  `ir.KINDS` is the net they share. `_NOT_ONE_CHAR` holds some of the kinds. `is_one_char` answers for a further group
  on its own terms. A kind in neither therefore raises at the first question anyone asks of it. `ir.repeated` says what
  a node takes again and again, and `ir.CONSUMING` says which kinds take characters themselves. A kind has a name once
  rather than a re-listing at a site. Sharpening the run checker found counted repetitions over a character class that
  were not spans. That is the whole argument for it.

  `mint-consuming-and-residue` gives a production that may match empty a name per way it offers. `<name>_reads` is for
  the ways that take a character, and `<name>_empty` for the ways that take none. The production becomes the choice
  between them. That splits many productions and leaves the grammar wider for it. A caller entering such a production
  was choosing blind. A gate cannot rest on a character while both answers live under a single name. The residue gets a
  name rather than an inlined tree. A reader then works nothing out bottom-up. A body's parts split by what their own
  names already say. `every-empty-match-is-a-way` goes to none.

  The split gives the same match in the same order. A sequence's parts already offer its ways. `a b` consuming is
  `a_reads b` and then `a_empty b_reads`. That enumerates exactly as `a b` does. An alternation in the grammar puts the
  consuming way ahead of the empty way. The split therefore reorders nothing. A shape that cannot say the two apart
  takes another form instead. A possessive consume takes nothing exactly where its set is not there. Its empty way is
  therefore that negative peek. That is a character apiece, and a gate already asks that question. A pair of ways
  peeking the same set are a single production once the sweep merges them. A counted repetition whose count the parse
  works out matches nothing where that count is not positive. Its ways are therefore told apart by the count. The
  consuming way says the turn it takes. The count that admitted the way does not decide it. That is what `s-indent`'s
  indentation of none needed. A run over an item that may take nothing ends on a turn that takes none. The interpreter
  keeps that turn once. Such a turn in this grammar leaves nothing behind, and a check says so rather than an
  assumption. The consuming way is therefore the consuming turns, and the empty way is the turn that took none. A commit
  is the error where its item cannot match. Splitting it in place would make the consuming way's failure that error,
  instead of a step on the way to the empty way. `A (commit m: X)` and `(commit m: A X)` are the same match where what
  comes before takes no character and cannot fail. It is therefore lifted over the choice, and a single message scope
  sits around both ways.

  Somebody added fixtures rather than crediting a new name to the base it was split off. The coverage gate would have
  held a name covered by its base, as that gate does for a monomorphic copy. A base's coverage cannot say which of the
  two ways an input took. The corpus was therefore made to take both. It takes an empty and a non-empty single-quoted
  scalar in a block key and in flow. It takes a double-quoted scalar that is empty, and a double-quoted scalar holding a
  space. It takes the chomped last line at end of stream under a chomping, and a block header at end of file. It takes a
  kept block scalar with and without trailing empty lines, and a folded line at the leading-empty floor. It takes an
  error recovered behind an indented line, and an error recovered at end of input, where the recovery has nothing to
  give up.

  `distribute-residues` writes that choice at the call site, rather than behind the production's own name. It writes
  `A ::= F (X_reads | X_empty)` at a call site that entered one. That takes `no-call-enters-both-ways` to none, and
  leaves the grammar narrower for the names it stops needing. Naming the pair of ways was half of it. The choice sits
  behind a single name. A caller still enters without knowing whether the callee takes anything, and a gate has no place
  to go. The way around it is not split to do that. `A ::= F X_reads | F` would run `F` twice, where an alternation
  inside the sequence duplicates nothing. It is a single pass and no iteration. Such a production's body holds the pair
  of calls. The root and the recovery stay whole, and the calls between them are no part of this. A parse enters both by
  name, and nobody therefore chooses between them. A way that was a pair of things would make the other a choice on
  nothing at all.

  The coverage gate reads `_reads` and `_empty` as minted-helper suffixes. A form therefore credits the base it was
  split off, as a monomorphic copy already does. Its own note had anticipated that as "the base held to the matches that
  consume". The credit is the floor rather than the ceiling. The corpus reaches both ways of a split production in its
  own right. The gate answers for a base like `l-recover-entry`. A resume policy declines there, and the base matches at
  no site. A fixture could reach that before it was split either.

  `dissolve-residues` writes a production taking no character into the call sites that enter it. `e-node` goes into a
  pile of them. Phase 6 is then finished. `only-root-empties` goes to none. The productions still matching empty are the
  root and the recovery under a resume policy. A parse enters both by name. The split tells the two ways apart. A
  production still matching empty took nothing throughout. That is the residue a split named, and those that were
  actions and no more. A name is worth having where it marks a decision. There is none in a way that consumes nothing
  and ends where it began. The step writes a residue out before writing it in, and a residue holds calls of other
  residues. A residue reaching itself would be a match of nothing at all rather than a match of nothing, and the grammar
  has none.

  The grammar ends the phase about as wide as it began. The grammar went considerably wider in between. A caller chooses
  nothing about entering something that may take nothing. An empty match becomes a way the caller holds. A character can
  decide that way.

  A scope holding what it covers becomes the pair of actions that bracket the scope. There is a step per kind. An
  alternative is `gate actions... [P1 actions...] [P2]`. It has a place for an action and none for a node enclosing a
  call. A `(token)` around a call is an action that must run where the call returns. That is the continuation. The
  wrappers therefore come off before a way is split into a call and a continuation, or they come off twice. They run in
  order, smallest and least stateful first. The last step then moves the rest of them. `lower-wraps` writes
  `Wrap(begin, end, x)` as `Emit(begin) x Emit(end)`. `lower-windows` writes a `(max)` as `OpenWindow ... CloseWindow`.
  Windows do not nest. The outermost window applies. The pair counts the opens in force, where the wrapper asked whether
  something had already set a ceiling. `lower-commits` writes a `(commit)` as `PushMessage ... PopMessage`. A commit is
  the error where the item under it does not reach the end of that item. A commit records no more than that. That is
  what the push records and the pop marks reached. `lower-tokens` writes a `(token)` as `PushCode ... PopCode`. Both
  halves cut the run. The push sets the code the characters between them take. The pop takes back what the push
  displaced.

  The lowerings change where the displaced thing waits. A Python local is the frame of the match that is running. It
  becomes the parse's own state, and that is the whole point. A frame is gone once the split makes a call and a
  continuation. A stack survives that. That is also why the balance has to hold while the lowering runs. A pop takes
  back whatever is on top, rather than what its own push put there.

  A wrapper is paired by construction. `ir.Wrap` is a node rather than the two markers, precisely so a `begin` cannot
  lose its `end`. Unwrapping trades that for a property a gate has to check. `every-scope-closes-on-its-own-way` is what
  takes it over. It is read at none over a stage before a wrapper came off, and named by a step that takes one. A scope
  opened on a way closes on that way. The ways of a choice agree on what they leave open. A run's turn leaves none open,
  and a second turn opens the scope again. A probe gives a lookaround back, and what is inside a lookaround therefore
  touches nothing. It covers the pairs a normalized grammar holds. A `PushIndentAction` closes on a `PopIndentAction`. A
  `PushCodeAction` closes on a `PopCodeAction`. A `PushMessageAction` closes on a `PopMessageAction`. An
  `OpenWindowAction` closes on a `CloseWindowAction`. A `PushBackTrackAction` closes on a `PopBackTrackAction`. A
  `StartMustConsumeAction` closes on an `EndMustConsumeAction`. The indent pair had gone unchecked. It is not vacuous.
  Dropping the pops from a production reports both the scope left open and the ways that disagree about it.

  The markers are not that. A pair of them crosses productions by design. `b-chomped-last` emits `end-scalar` for a
  `begin-scalar` opened elsewhere. A per-way rule is therefore the wrong rule for them. `check_markers` does prove their
  balance with a fixpoint over the callers, and reads the grammar as authored rather than following the pipeline.
  `lower-wraps` loses nothing on its own account. The pair of markers comes out adjacent in a single way of a single
  production, and the sweep has nothing to separate them. It gives up the guarantee for later. The first thing that can
  put a `begin` in a production and its `end` in another is the split into a call and a continuation. That phase needs
  the marker net.

  Moving the state out of the frames is what the two faults were about. Either was a scope an abandoned parse left in
  place, where the wrapper's frame had taken it away. An in-grammar `(recover)` put back the `(max)` ceiling of the rule
  it belongs to. It did not put back the count of opens beside that ceiling. The recovery therefore answered for a
  window something still counted open. A key past the window's bound then bounded no later key at all. A cut that
  unwound to the stream's own level left its committed regions on the emitter. A raise had skipped their closes. A parse
  that matches refuses to return with a window or a region open. That is the permanent net for both. It reads none on
  the grammar as authored. It named the regions the moment `lower-commits` landed. The fixture behind it is a stream
  whose first implicit key overruns and whose second must overrun again.

  The grammar hands on with no `(wrap)`, `(max)`, `(commit)` or `(token)` left in it. They were the code, the message
  and the window pairs. The indent pair went with them. The markers sit beside those pairs. A scope closes on the way it
  opens. The `(recover)` stay. A recovery is a handler and not a scope, with no close whose position means anything. Its
  home is the edge an alternative rides.

  A choice is where the parse decides, and a machine decides in a state. Phase 7 therefore takes the tree apart. An item
  in a way is what the machine does at that position. `no-item-holds-a-match` counts the tree the phase begins with.
  That count covers the choices and the runs. It also covers the recoveries and a single binding. A wrapper holds a
  match inside it, where the machine has no state to be in. `lift-choices` settles the first of those, and takes
  `every-choice-is-a-body` to none. A choice inside a way gets a production of its own, and the way holds the call. It
  mints rather than distributing. Distributing is the other way out of a sequence. `a (x | y) b` as `a x b | a y b` runs
  `a` twice wherever it takes a character or pushes anything, and copies whatever `b` calls. The call costs a push and
  duplicates nothing. The grammar widens, and the sweep merges what the mintings duplicate. The phase's own count falls
  by the choices this took out of it.

  Phase 5's shape is what it costs, and the counts that say so are one fact. The grammar wrote a choice between
  consuming and taking nothing at the call site. A gate had no place there. It becomes a production a call reaches.
  `no-call-enters-both-ways`, `only-root-empties` and `every-empty-match-is-a-way` rise again. A rise is a declared
  lapse with that reason. A gate answers for the rise on arrival. The canonical form has a place to put it.
  `F (X_reads | X_empty)` is a call and then a decision, and a decision after a call is a production.

  The lifting also turned up a cycle nobody had looked at. `no-production-reaches-itself-unconsumed` exempted the stream
  and the recovery by name. The meaning was that a recovery is a landing the driver picks rather than a call the grammar
  makes. That is false under a resuming policy. `l-recover` is `l-unparsed` and then the stream again. The two are
  mutually recursive by design, and a resumed document can then fail again without a second mechanism. A production
  minted out of the stream's own body lands on that cycle, and inherited no exemption. `l-unparsed` holds the pair
  apart. It takes nothing where the next line is a document boundary or the input has ended. There the stream consumes
  the `---` or `...` itself, or `<end-of-stream>` answers. A second recovery therefore costs a character. The exemption
  belongs to the cycle rather than to a name. The walk cuts a path through what a parse enters by name before asking
  about reachability. A production that still reaches itself is the grammar's own doing, and a fault.

  The other shares follow, and the phase's count reaches none. `lift-runs` gives a run inside a way a production of its
  own. That production is the machine's loop state, and the machine jumps back to the top of it. The step settles
  `every-run-is-a-body`, and costs the same counts again for its own reason. A run of none or more is that same choice
  under another name, take a turn or take none. Naming a run settles nothing about the run itself. The run stays the
  possessive consume the grammar wrote. Whether it takes another turn is a question the gates ask. `lift-recoveries`
  does the same for the recoveries. A handler waits there until an edge exists to ride. An alternative holds one, and
  until the alternatives exist a recovery is a production of its own. `lower-bind` takes the last binding. The block
  header holds it as `Bind(ns-dec-digit, m, atoi(match))`. The step writes that binding as the match and the write that
  follows it. That is an identity the interpreter states twice over. A binding whose condition has matched does exactly
  what a `SetVarAction` does. The binding undoes the write where what follows fails, and the condition can then try its
  next way.

  `no-item-holds-a-match` reads none from there over the whole grammar. An item in a way is a call or an action. An item
  is also a guard or a character taken. An item may be nothing at all. The count itself got something wrong, and that is
  worth keeping. It did not know a recovery could *be* a body. The productions `lift-recoveries` minted therefore read
  as faults of their own. A single list names the forms a body may take. It is a choice of ways or a run of a way. It is
  a way under a handler, or a way. The count and the lifting both read it. A binding is deliberately not among them. A
  body that is one hides a write behind a match.

  The exclusions are the phase's other half, and `every-exclusion-is-bounded` is what the phase begins broken on. It
  counts the exclusions that ask a production rather than a bounded question. An `(exclude)` is a guard the parse holds
  and tests at a start of line while it is in force. The question therefore has to be checkable at the position the
  parse asks it. Some ask by name, as `c-forbidden` does. A guard would have to run a parse to answer that.
  `bound-exclusions` writes the question as the characters it denotes. At a line start it is `---` or `...`. A break or
  a space follows, and so does a tab or the end. That is a pair of `LiteralPeekGuard`s with a follow class between them.
  The parser's own fill already guarantees that. The meaning is a peek's. The walk reads through a name, and an
  annotation is dead. The follow test distributes over the two runs. A probe gives a question back. The duplication is
  therefore a test rather than a match.

  The count stops short of none. The remainder is the phase's declared debt rather than its oversight. Those exclusions
  also ask whether the line is at this indentation with content. That is a run of spaces with no bound. It is a
  condition on a line start, rather than a question about what follows one. It lands where the block-structure work
  makes a line start a decision the grammar writes.

  Phase 9 is the call. An edge of the machine is a push and a jump. The push says where to come back to, and the jump
  goes. A way is therefore the actions before it hands control on, plus the call, plus the production that continues.
  `a P1 b P2 c` becomes `a` and the call `P1`. A production of its own then holds `b P2 c`. That splits the same way
  until nothing comes past a call. `a-way-is-actions-a-call-and-a-continuation` reads the ways that go on doing things
  past their call, together with those that hand control on more than twice. `mint-continuations` settles it, widening
  the grammar. Binarization is no step of its own. A way that calls more than twice falls out with them.

  The split breaks the scope net's checker, and not the pairs it proves. `every-scope-closes-on-its-own-way` was a
  per-way rule, and a way that hands control on is half of a path. The consuming half of `s-indent-le` keeps its
  `PushCodeAction`. The matching `PopCodeAction` rides into the continuation. That is exactly the cut phase 6 moved the
  pairs onto the parse's own stack for. The invariant is therefore re-derived rather than lapsed, as
  `every-scope-closes-on-the-path-that-opens-it`. The invariant reads none at a stage before the split, and at a stage
  after it.

  Working it out took a single idea, and the machine runs on that idea. A call is a **unit**. The unit answers for what
  a call does relative to its own entry. Both of a way's calls are on the path. The call a way comes back from counts as
  much as the call it continues at. A step contributes the scopes taken off and the scopes left open. A push, a call,
  and the pop that follows therefore balance whatever depth the call reaches. That lets `c-flow-sequence` open a message
  scope, recurse flow content inside that scope, and close the scope on the way out. A walk into that recursion instead
  reads it as a circle entered a scope deeper per turn. `[ [a] ]` then becomes a fault.

  The circles remain, and there the checker does the work. A production that reaches itself has an answer. A turn round
  the circle leaves the scopes untouched. The walk reads the answers a circle of calls at a time, a circle after the
  circles it calls. Those are Tarjan's components in that order. The walk rounds a circle until the answers stop moving.
  The growth says whether a circle comes back level. There are the scope actions the circle holds, and the scopes its
  outside calls leave. An answer longer than those has been round more times than there are pairs to have opened. A
  round budget in place of that reads "has not finished yet" as "is at fault". The order the walk took decides which
  productions it blames. The same grammar answered differently on a run and on the run after it. The growth had yet to
  decide it.

  The summary reads what a production does and not where the parse goes, and the second is its own question. The pair of
  calls a way makes are alike to a summary and not alike to a path. A way hands control on to a place the path continues
  at. The way pushes nothing to come back to. A loop of hand-offs therefore puts the parse genuinely back at its earlier
  position. Round the loop once and whatever the turn left is open. Round it again and a second scope is open, and no
  bound applies. A loop must therefore be level, and a walk of the hand-offs is what can say so. A summary is what a
  production does relative to its own entry. That answers nothing about what a turn round a loop leaves. There is one
  walk per circle of hand-offs. A loop lies in one. A walk from any production of that circle reaches a loop in it, as
  an edge back to a place already visited. The circles that hold a loop come back level.

  The rule says nothing about what the parse can be *reached* at. A pair of paths may arrive at a production having
  opened different things. Both paths may balance in their own right. Refusing that is a rule about where a call may
  reach a production, rather than a rule about scopes. It read faults where there were none.

  Neither walk covers a recovery. The pairing of its halves says nothing about that. The interpreter unwinds to the
  recovery, reads the stack it finds, and takes off whatever the abandoned parse left. The interpreter therefore puts
  back the scopes the recovery started under, and no grammar action balances that. Holding a recovery to the pairs on
  that path would hold it to a rule nothing obeys.

  Phase 10 says a body in the machine's own words, and `every-body-is-a-choice-a-run-or-a-set` counts what is not yet
  said that way. A terminal is a set of characters. A loop is not among them. The lowering already said a repetition as
  ways. The state the machine jumps back to the top of is therefore a production like any other. A guard like any other
  stops it. Anything else is an ordered list of alternatives. A way is a gate to enter on, plus the actions the way
  performs, plus the call it hands control to. A way also names where it continues when that call returns. The recovery
  riding the push goes with it. `build-alternatives` writes a remaining body that way over the whole grammar.

  The step changes the form and not the meaning, and a pair of things say so. The interpreter already ran the canonical
  form. An alternative was the sequence the tree wrote, the gate's peek a lookahead, and the recovery the `(recover)`
  scope over the call it protects. The interpreter therefore learned nothing about executing one. The step also leaves
  the gates empty. The parse enters a way on a question about the character in front of it. The hoist answers that
  question. Until then the parse tries the alternatives in order. The tree said that too.

  The checkers that walk a body had to be taught, and they refuse rather than guess. Those are the flatness count and
  the nullability. They are also the split saying which ways a production has, and the left corner the cycle check
  walks. A choice says its ways as `alternatives` in the machine form, and as `items` in the tree form. An alternative
  says its parts by name. A sequence says them in a row. A single checker therefore says both, and the walks ask it. The
  case that bit was an `AltTree` reaching the helper written for the machine's form. The tree got itself back as its own
  single way. That is a walk that does not end, rather than an answer that is wrong. The per-way scope walk went with
  the rewrite, and the path check replaced what it read.

  Phase 11 is the gate. A machine that does not backtrack takes a way by looking at the character in front of it.
  `every-way-gated` therefore counts the ways a parse would have to try and give back. Those are the alternatives that
  make a decision. The exempt alternatives come off that count. The last way of a choice is exempt as the unconditional
  fallthrough, and a body with a single way is no decision at all. `gate-hoist` is the first of the hoists that reduce
  the count, and no analysis sits behind it. The parse enters a way whose first action takes a character on that
  character. The set therefore rises into the gate. A `ConsumeCharAction` takes its place, and consumes the character
  the gate has already found. It is done in an alternative, rather than where a choice needs telling apart. A way whose
  first action is a set fails where the set is not there. A gate changes nothing about that. It takes the ungated ways
  that begin with a set.

  The remainder says what the phase still has to do. Some begin with a call, and what a call can start with is an entry
  set the grammar has not computed. Some begin with an action the gate has to look past. The gate may do that, and such
  an action touches no input. Some begin with a guard. The gate holds a guard beside the peek rather than in it.

  The entry sets are therefore computed. They are `{name: spans}`, the characters a parse of a production can start on,
  as a least fixed point over the calls. The fixpoint errs wide where it errs at all. A gate too wide costs a parse that
  fails where the gate could have refused it. A gate too narrow loses a parse that should have matched.
  `gate-hoist-call` then enters a call-leading way on what its callee can start with. The way cannot match unless the
  callee does. The set therefore refuses exactly what the way would have failed on a call deeper. `every-way-gated`
  falls by better than half between the pair of hoists.

  There are two side conditions, and the second is one the corpus found rather than the argument. The step refuses a
  callee that can take nothing. The way then passes through that callee to whatever sits behind. The step also refuses a
  callee that answers a character it cannot start on with an *error* rather than a refusal. A `(commit)` opened before
  anything has to take a character makes the failure the error that region names. Gating the way out of the parse
  therefore lets the choice go on to a way that matches, where the parse would otherwise stop. That is a different
  language and not a narrower one. The suite said so exactly, in `2G84/00`, a case libyeast began accepting where the
  suite rejects it. A pile of callees are of that shape.

- Boolean names audited across the generator. That covers a function, a variable and a parameter alike. A fixpoint's
  `changed` and `moved` are `did_change` and `did_move`. A match's `matched` is `did_match`. The law renames `licensed`,
  `broken` and `settled` to `is_licensed`, `is_broken` and `is_settled`. `refuses_softly` and `establishes` are
  `does_refuse_softly` and `does_establish`. The flags `bisect`, `check` and `apply` are `does_bisect`, `is_checking`
  and `is_applying`. A CPS continuation whose bool means "the rest of the parse matched" keeps its name. It is protocol
  rather than a predicate. The name says the act it performs at that position.

  **The meter, unprinted since somebody reordered the pipeline.** `every-decision-goes-on-a-character` counts a
  multi-way choice whose gates do not tell its ways apart. That is a way the gate says nothing about with another behind
  it. A pair of ways admitting the same character is another such choice. The last way is no fault. An empty gate there
  is the fallthrough, taken where the ways in front of it did not fire. That is a character deciding by firing nothing.
  It reads a deciding choice where the alternatives are first made and no gate says anything. The hoists then take out
  the choices they can reach.

  The rest of the gating goes with it. `hoist-past-actions` enters a way on the character the first question asks,
  whatever actions come in front. The parse tests a gate before entering the way, and an action touches no input. The
  same character therefore chooses either way, and a way that fails the test rewinds whatever its actions did. The walk
  goes through a call that can take nothing as well. The parse then enters the way on what that call can start on *and*
  what sits behind it. The walk stops at a commit. That is `gate-hoist-call`'s refusal a call deeper. A `(cut)`, an
  `(error)` or a region opened before the question makes failing there an error rather than a refusal. A gate that keeps
  the parse out of the way turns that error into a way not taken. `hoist-guards` puts a leading `EndOfStreamGuard` or
  look-behind in the gate beside the peek. Both are questions the machine can put at that position. `every-way-gated`
  says so too. A way entered at the end of the input has no character to ask about at all. Together the hoists take the
  ungated ways they can reach. **The text below accounts for what survives.**

  The text accounts for those rather than leaving them. They call a production that answers a wrong character with an
  error, or a production that can start on nothing. They sit behind a commit. Such a way also passes through what it
  holds, and no character then has to be in front at all. A policy is a question about *when a failure is an error*.
  Determinizing answers that. A hoist answers something else.

  **And the meter is half of what determinizing owes.** A character picking a way is not the same as committing to it
  being safe. A gate can be perfectly disjoint and still be wrong. The way it admits fails further on. A backtracking
  parse would have taken the next way there. The meters measure that at no point. A case the pair of modes read
  differently is a gate that is disjoint and not safe. Holding the pair to a shared corpus would measure it, and
  `PLAN.md` owes the committed mode.

  Its first meaning is honest rather than a failure. The entry sets behind a gate err wide on purpose. A wide gate is
  exactly a gate that admits a character its way cannot go on to match. That is harmless while the parse backtracks. It
  turns fatal once the parse stops backtracking. A run prints the count, and it judges the transforms ahead beside the
  meter. The count becomes a gate where it reads none. The two modes then agree by law, the way the corpus already
  agrees.

  **What the meter counts is not a single thing, and the walk says which.** `determinize.py` walks a conflict's live
  ways in lockstep to their first divergence. The difference there classifies the site. The characters may differ, and
  then a character decides the site. The codes over the same spans may differ. Holding the tokens and retyping them can
  then decide the site. The configuration may repeat, under no bound at all. Run as a measurement over what the meter
  flagged, the walk sorted the sites by kind. There is **a character decides** and **the codes differ**. There is also
  **it cannot even root** and **a guard already separates**. The last is the meter reading peeks and no more, as the
  meter says it does.

  The sites the walk cannot root are worth a step, and those are no verdicts. `_caller_continuation` refuses a conflict
  reached from more than a single place. The follow is then more than a single way. The walk then has no place to begin,
  and the meter is claiming a fault it cannot describe. `splice-conflicts` answers it from the other end. The step
  splices a call to such a conflict into the site the call had. A copy is therefore the conflict in the context that
  reaches it. The follow is the remainder of the way the copy sits in. The copies differ by position rather than by
  content. That is what keeps the sweep from folding them back into one.

  There are side conditions, and the corpus or a count found them. A way that has taken a character does not splice. The
  parse then enters the callee somewhere other than the way. A way that has **committed** does not splice. The callee's
  ways backtrack *inside* that region, and spliced out a way would open its own. That would make the first way's failure
  the error, rather than the next way's turn. The flow collections' unterminated-bracket commits are the case, and they
  broke fixtures before the condition existed. A callee that continues at something not level does not splice. That call
  would become a call the way comes back from, with the caller's continuation pushed behind it. The scope net caught it.

  The step runs to a fixpoint. Splicing makes sites. A pass moves the count up as often as down. A pass leaves a caller
  that has become the conflict. `every-conflict-can-be-asked` is the walk's own question. It is also the count that says
  how much of the meter anybody can work on at all. It falls, and the two modes read fewer cases differently. The meter
  itself rises, and the rise is bookkeeping rather than work. The walk says a character decides more of them than
  before. The pile that is genuinely hard has not moved.

  The remainder has a name. A commit before the call blocks some of them, and those want the commit lifted. That is the
  step the remaining ungated ways want too. A way wants the tail call it continues at made level.

  **A prefix can hide behind a call, and a gate cannot see a prefix that does.** A pair of `b-break` ways both begin by
  handing control to `b-carriage-return`. They therefore do the same thing until that call returns. That is a shared
  beginning like any other, and invisible to what compares gates. `no-conflict-shares-a-called-head` counts them, and at
  conflicts only. A choice whose gates already tell its ways apart has nothing to move. Inlining there would copy a
  production for no decision at all.

  `inline-shared-heads` splices that callee into the ways that share it. That is the splice's own rewrite pointed the
  other way, down into a conflict rather than up into its callers. The same grounds refuse it. The pair of steps
  therefore share a pass, and differ in which calls they name. A terminal splices too. It is a single way, and that way
  is a character. The call becomes a gate on its set and a consume. It then hides no prefix. The count falls and the
  grammar with it. The sweep can drop whatever a shared head came out of. It is a single pass, and the rounds are a
  question left open rather than settled. A second run over the grammar it leaves keeps the count falling. The grammar
  narrows with it. A second run over the grammar it starts from makes the count climb instead. A conflict left after a
  pass and a hoist is a truer conflict than a conflict before either.

  **A choice goes undecided where the gates overlap partly, rather than where the gates overlap at all.** A pair of ways
  admitting exactly the same characters are a shared prefix the factoring can take. A pair whose gates cross are neither
  told apart nor shared. A character firing both says take the earlier way. Order decides there rather than the input.
  `no-partial-overlap` counts those, and `split-gates` settles it. The step cuts the ways along the groups their gates
  treat alike. That is the coarsest cut that leaves no gate straddling a group. Cutting at an edge instead splits a way
  along boundaries that have nothing to do with it. That costs an order of magnitude more copies for the same answer.
  The match is the same. The copies spread it over the site the way had. The step keeps the order. A copy shares a gate
  with exactly the ways it overlapped.

  The meter does not move for it. That is right. An overlap made whole is still an overlap. The split is for the
  factoring behind it. That factoring reads exactly the gates the split leaves equal.

  **A way no input refuses is the last way the machine takes.** `no-unreachable-option` counts a way with another behind
  it that nothing can hand back. A choice goes on to its next way exactly where the way in front fails and hands back. A
  way that matches throughout therefore leaves the ways behind it nothing to enter on. So does a way whose failure is
  the error a commit names. Both stop the choice at that position. The invariant reads **none** from the point a step
  claims it, and no lapse appears anywhere.

  The step asks a single question rather than a pair. That is what took the work. The parse refuses a way where a
  character it needs is not there. A guard the way asks may decline, or the gate may turn the way away. The parse
  refuses no way past a `(cut)`, and none inside a committed region. `interpreter.match` raises through a region that
  has not closed, and hands back through a region that has. The parse refuses a counted consume. Such a consume takes
  its whole count or none. A consume of none or more takes another route. Asking instead whether a way refuses *the
  character it cannot start with* answers a narrower question with a pessimistic bound. It names the first place a raise
  is possible and stops. A way that raises on an input hands back on another. That checker called ways unreachable.
  Deleting them broke fixtures. Those were `c-l+literal.n=2.empty` and `header-eof`, plus `keep-empties`, `keep-none`
  and `JEF9/00`. That is the test a claim of unreachability has to survive. `gate_hoist_call` asks the narrower
  question, and it keeps that question.

  **A way of a choice has a test the machine can make before entering it.** `every-way-gated` is
  `every-way-carries-a-test`. That is what the invariant counted throughout, and what its name says. A machine takes a
  way by testing something first, and what the test is comes second. A character set is the usual one. A guard is one
  too. A guard asks where the parse is in its line. It asks whether the input holds a further character, or how the
  indentation compares. Whether the tests of a choice are exclusive is the next question and a different count.

  `hoist-askable-guards` settles the guards' half of it. A guard among a way's actions is a question asked a moment too
  late. A guard among the actions is reached only by entering the way. Entering the way is what that guard could have
  decided. The guard therefore moves to the gate. The machine asks it there. The step decides which guards move. A guard
  reading the input passes any action. An action writes neither the position nor what surrounds it. A comparison passes
  the actions that do not write what it reads. An indentation test behind a `PushIndentAction` would read what the parse
  has not done yet. A commit lets nothing past. A `CutAction` is a commit, and so are an `ErrorAction` and a
  `PushMessageAction`. Past such an action, a failure is an error rather than a refusal. A gate that keeps the parse out
  of the way would therefore turn a parse that stopped into a parse that took another way.
  `no-guard-left-among-the-actions` counts the remainder and settles at none.

  A test written as a call is also a test the parse cannot enter the way on, and the same step lifts those. A production
  of a single way that holds no action, makes no call and continues to nothing *is* its gate. Entering it is therefore
  asking that gate. Ways handed control to such a production. The sweep had merged the zero-width end-of-stream helpers
  into a single `EndOfStreamGuard`. That merge kept the block header's name. The call becomes the guard, written into
  the site the call had. Lifting and hoisting feed one another. A callee left holding its gate and no body is a callee
  the callers can lift. Both therefore run until neither finds anything. A round drops a call or moves a guard, and
  there are finitely many of both.

  The sweep takes an empty match among a way's actions away with them. That is what let them through. A sequence drops
  `<empty>`, and a way's actions are a tuple on an alternative rather than a sequence. The litter therefore sat there. A
  walk looking for what a way begins with stopped at it. It takes no character and does nothing.

  The step does not take the ways left. A way hands control to a production that answers a character it cannot start on
  with an error rather than a refusal. Gating the way out would therefore turn a parse that stops into a parse that
  takes another way. That is a different language. They want the commit lifted off the callee.

  **What answers for a failed cut is a scope, and the IR names its pair.** A `(recover)` was the scope phase 6 left as a
  node. The meaning was that it is "a handler rather than a scope, with no close whose position means anything". The
  interpreter says otherwise. The interpreter takes what the parse holds where the region opens, and puts that back
  where a cut unwinds in. That is exactly what the window, the message and the code pairs do. A recovery also *resumes*.
  The window, the message and the code pairs resume nothing. Its close holds information. It is the last written rather
  than the fourth.

  The IR names the pair for it. `PushRecovery(recovery, resume)` sits before the call it covers, and `PopRecovery` sits
  where that call returns. Both operands are named outright. It belongs after `build-alternatives`. The place a way
  holds on at gets a name only once the way is a call and a continuation. It wants a pair of productions per site. The
  first holds the pop and whatever the way continued to. The call returns there. The second holds that continuation and
  no more. The unwind resumes there. The second must not pop. The unwind has already taken the region off. A step writes
  the pair at no point. `KEPT_THOUGH_DEAD` says why the pair stays unwritten.

  The interpreter keeps the two lists this needs. The first is the return stack, where an entered production holds on
  when it matches. The interpreter pushes the stack and takes it back with the production trace, an entry at a time. The
  second is the open recovery regions. A place on the list says a region is open. Neither is checkpointed. A region's
  own pushes and pops balance the region, as they do for the committed regions.

  **This is what a way with a scope cost.** `splice-conflicts` builds a spliced way out of a callee's parts. It took the
  callee's recovery and dropped the caller's recovery. It did so silently. A dropped handler shows only as a parse that
  stops where a recovery should have caught it. A shape had reached it at no point before. A flattening that wrote out a
  called run of items did, and the recovery fixtures said so. The pair would leave nothing to drop. A rewrite that moves
  a way takes the actions along. Until a step writes the pair, what holds the handler is a side condition on the splice
  rather than the shape.

  **The step writes out a run of items a way calls, and a gate that decides nothing moves up.** `a P c` where `P` is
  `d e` does more things in a row than it shows. A checker that walks a way to find what it does first therefore stops
  at the call. `flatten-called-sequences` writes it out. A call last in the way stays. The parts past a call are a
  production of their own by design. `no-sequence-of-sequences` falls to a residue. The recursion guard holds that
  residue.

  Beside it sits `no-gate-decides-nothing`. A gate on a body that offers a single way selects nothing. There is nothing
  to select between. It reads **none at the door**. There are no gates until the ways are re-encoded. The count
  therefore names whichever step strands one. The hoists do, and a hoist declares it. The hoists gate a way where they
  can, and that covers a body's single way. There the gate is an assertion the callers can discharge.

  `lift-gates-to-callers` moves the stranded gates it can. It moves by **splitting rather than rewriting**.
  `A = Gate A'` with `A' = Stuff` leaves `A` meaning exactly what it meant. A caller that can take the gate calls `A'`
  instead. Rewriting `A` in place needed a side condition for anything else that entered it. A caller had to take it,
  save productions entered by name, and save the productions a fixture runs directly. Those conditions vanished with the
  split. A way that acts before the call reaches the gates left, and so does a way that reaches them in tail position.
  There the caller's own entry says nothing about the character.

  The split exposed something worth having. The callers gate a production. A parse then enters that production on a
  character the production can start with, and a refusal is out of reach. The coverage gate already credits a gate
  refusing to the production it guards. The comment there reads "otherwise gating a rule correctly would make it look
  untested". The gate says the same of a production whose refusals its callers have taken up.

  The corpus cases where a committed run and a backtracking run read differently **fall again**. The meter rises and
  `every-conflict-can-be-asked` with it. That is what the copies cost. The step mints ungated forms, and a written-out
  run adds ways the meter counts as undecided.

  **The step writes out a choice a way of a choice calls, into the site the call had.** `a | P | c` where `P` is `d | e`
  makes more decisions than it shows. The hidden decision sits behind a call that nothing about the outer choice can
  see. The written-out `a | d | e | c` is the same ways in the same order. A way then sits where a gate can go on it.
  `no-choice-of-choices` counts the decisions still hidden where the step runs. `flatten-called-alternations` settles
  it. It runs until nothing moves. It does not run into a choice that can reach back. Such a choice would write itself
  out for ever. It runs before the split makes a call and a continuation. A phase behind it therefore sees the choice
  whole.

  The step is the first thing to move the counts that measure the goal. `every-way-carries-a-test` falls, and so does
  the meter. So do the corpus cases where a committed run and a backtracking run read differently. The grammar got
  *smaller* for it.

  The conflicts left are a single shape. A conflict is a way handing control to a production that answers a character it
  cannot start on with an error rather than a refusal. A `NegLookGuard` guards it. They are the auto-detected-indent
  copies of the block scalar's content.

  **A question about a node goes through a table that cannot answer for a kind nobody named.** `ir.Question` maps a node
  kind to what to do about that kind, and obeys its own rules. A kind nobody put in the table **raises**, and the
  message names the checker. A handler nothing reaches is **reported** by `unexercised` after a whole-corpus run. A kind
  named twice will not build. The first pair pin a table to exactly the kinds that occur. A missing kind raises, and
  `unexercised` reports a spare one. A checker therefore says nothing about what it cannot see. A kind added to the IR
  touches the checkers that reach that kind, on the day somebody adds it. `NEVER` is how a checker keeps a wide group
  and takes back the part that cannot arrive. A check says so rather than a belief. A checker is a table, and the suite
  exercises it to the last handler.

  The table replaces a chain of `isinstance` tests ending in a fallthrough. Such a chain answers permissively for
  whatever form its author did not think of. The chain reports its own blindness as a property of the grammar. That is
  not a hypothetical. The gate read an invariant a different way on run after run over the *same grammar*. The checker
  was a walk silently defaulting on a form it did not recognise. Those were a character wrapped in a `(token)`, and a
  way's gate rather than its items. They were also a repetition, and a count the parse works out.

  The landing found things worth naming. `is_one_char` had a `(case)` branch nothing has ever reached.
  `_is_actions_alone` claimed a kind while the kinds a way can begin with are what reach it. A pair of overlaps say
  which way they go. The order of a test chain had been settling them silently. `ErrorAction` and `PushMessageAction`
  are actions and commits both. A walk looking for the places a way can refuse must stop at such an action rather than
  step over it. Stripping "the scopes written around a match" turns out to strip a `(token)` and no more. A `(max)`, a
  `(recover)` and a `(wrap)` reach it at no point.

  A checker may answer with a named `Verdict` rather than a value. That is what lets a walk over a way's items be a
  table as well. It takes the item. It may step over that item, or stop. It follows the call the item makes, or follows
  what the item holds. It may also treat the item as the commit past which a failure is an error rather than a refusal.

  **What tells a conflict's ways apart, and how far in.** `determinize.verdict` walks the live ways to their first
  divergence. The characters may differ. Factoring the shared prefix down to that point puts the decision where the
  input makes it. The codes over the same span may differ, and no depth of factoring separates those. The step holds the
  run and retypes it instead. There may be no verdict at all. That is where the walk cannot find a root, fails to
  converge, or runs past its limit. Of what the meter holds, **a character decides the larger pile**. The codes decide a
  smaller pile. The remaining sites have no verdict. `no-lookahead-left-to-factor` sums the depths of the first kind. A
  loop of factoring runs on that measure rather than on the meter. Factoring trades an undecided choice at a depth for
  an undecided choice a character shallower. The meter can therefore sit flat while a round makes real progress, where
  this cannot. A round takes exactly a character off a conflict it targets, and no rewrite pushes a discriminator
  deeper. It therefore falls by the number of targets and does not rise. A step names it at no point yet. The round that
  does is the round the loop exists for.

  A step's grammar is swept of what the step leaves behind. The passes run to a fixpoint, and they feed one another. The
  sweep flattens a body to the shape it denotes. A sequence or a choice of a single item is that item. A nested body of
  the same kind is its items in place. An `<empty>` in a sequence goes. The match stays at that position and moves
  nothing. A choice of *nothing* stays. It is the path no input takes. It comes first. The merge reads shape rather than
  meaning. A pair of productions may say the same thing with a singleton alternation in different places. They merge
  only once the flattening has run. A production whose whole body is one ungated, action-free call is what it calls. A
  reference to it therefore becomes a reference to that callee. The sweep writes productions that behave alike once.
  That is the same parameters, and the same body. The read replaces a reference with the group of what it names, rather
  than with the name itself. That is what tells a pair of loops apart from a loop written twice. The sweep purges a
  production no parse can enter, and that comes last. It then sees what the earlier passes strand. They leave what the
  grammar matches or emits untouched.

  The sweep drops no fixture it strands. A stranded fixture pins to the last stage whose grammar can run it. It guards
  that grammar token for token, and credits coverage from that stage. The coverage gate credits a minted helper to the
  base the helper came from, as it does for a monomorphic copy. A helper is a piece of the base's own body moved. Asking
  more of a helper than of the body it came from would ask the corpus for what the untransformed grammar did not need.
  `check_normalize` holds a step token-and-event identical over the whole corpus. That is the conformance fixtures and
  the YAML Test Suite cases. Some of them pin the document-marker boundary the spec's `c-forbidden` writes and the
  Clojure reference agrees on. `---foo`, `---#foo` and `----` are content, and so are their `...` kin. The `--- foo`
  form is a boundary. The `... foo` form breaks the rule. Others pin the sequence dedent hand-off any committed block
  structure must reproduce. At a dedent an exiting level puts its end markers before the dedent line's indent token. The
  owner level continues past it. A dedent at the line's start has no indent token at all. A pair pin the sequence
  entry's committed dash, where the Clojure reference agrees both are errors. `- @` is an entry whose body fails inside
  the entry's own committed region. The gate refuses `-b`, and the sequence closes before a stream-level error. A step
  must change the grammar, and a step that does not is a fault the report names. A step goes idle where the shape it
  looks for stops arriving. That is a regression in the step before it, rather than a step to keep. The check reads a
  transform's own output before the sweep runs. The check therefore judges a step on the step's own work, rather than on
  what the sweep did after it.

- `(match)` is the text of the open token the rule is building. The `(<<<)` origin that measured it goes with the
  operator. `OpenMatch` and `CloseMatch` go too. So do the `match_start` parameter and the `lower-bounds` step. An
  indentation is the length of the indent token the rule builds. That is what `s-indent-lt` and `s-indent-le` read. The
  block header's indicator is `(atoi)` of the digit it just consumed. `(ord)` had been a single-character operator,
  where this is a whole string. A pair of grammars justify the meaning differently, and a grammar obeys its own
  justification. Here a `(match)` must sit inside a `(token)` it fills, and must not read a run a nested `(token)` has
  cut. The official grammar has no token annotations. There a `(match)` it reads must be one libyeast reads in the same
  production. Those are `s-indent-lt`, `s-indent-le` and `c-indentation-indicator`. The reader takes the official forms
  into this vocabulary where the comparison needs them. `(<<<)` wraps a repetition that matches possessively here, and
  so hands back nothing already. `(ord)` reads a single digit.

- Decoder ABI. The decoder consumes a run of characters, and the tree writes it that way throughout. `ys_consume_set`
  advances while the character is in a set. `ys_consume_trim_sets` does that over a pair of sets. A `ys_consumed` says
  what either of them took. That is bytes and characters. The two differ wherever the run holds a character outside
  ASCII. Scanning was the other word for it. A single act under a pair of names is a pair of vocabularies for a reader
  to hold.

- Decoder ABI. `ys_consume_trim_sets` consumes over a pair of character sets in a single forward pass. It takes the
  whole run under `full`, and says how far the last character not in `trim` reached. It returns a `ys_trim`. That holds
  the kept `span` and the `trim` run handed back after it. It is what a plain or a quoted scalar's line compiles to. The
  call keeps the inner spaces. The trailing spaces go to the caller as an `s-white*`. The call reads the input once. The
  generated parser does not call it yet.

- Indentation detection. The official grammar calls the rule a "special rule" and gives no definition. Elsewhere the
  official grammar leaves it as an integer added to the string `"auto-detect"`. libyeast defines it. libyeast declares
  its departures from the official grammar. A reason goes with a departure. `m` is an indentation. `s-l+block-indented`
  sets the `m` it had read without setting.

- The yeast wire format. `ys_write_token` writes a token stream, a character and its escaped text per token.
  `ys_read_token` reads a token back. A tool can therefore pipe a stream onward. A tool can store a stream, or compare
  it against another parser's stream. `ys_bytes_writer` mirrors `ys_bytes_reader`, with the same file-descriptor and
  `FILE *` adapters. An escape writes a codepoint under any code but `YS_CODE_UNPARSED_INVALID`, and a byte under that
  one. The reader and the writer hold the other's text to being what it claims. Writing `\x80` for a raw `0x80` under a
  code that means codepoints says U+0080. It reads back as a pair of bytes nobody gave. `YS_CODE_UNPARSED_INVALID`
  exists to hold exactly the bytes that encode no character. The text under that code must therefore encode no
  character, and a byte of it opens none. The text under another code must encode them. `ys_write_token` holds both
  halves and answers `EINVAL`. The errno policy already promised that answer for a bad argument, and the check had not
  run. `decoder.c` owns the validation, and had it throughout. `ys_codepoint` assembled continuation bits without asking
  whether they were continuation bytes, and silently turned `"\xE0ab"` into different bytes. `YS_CODE_UNPARSED` becomes
  `YS_CODE_UNPARSED_TEXT`. The pair then say what they are together. The reader's search for a line's break resumes
  where the last search gave up. It does not start over. A line arriving in pieces therefore costs the length of the
  line rather than its square. 16MB on a single line took 2.17s and takes 0.09s. A wire read from a pipe is what the
  format is for, and is exactly what arrives in pieces. `max_bytes` is unlimited by default. The cost was therefore a
  denial of service against the format's own purpose. The reader also refuses a position a token cannot start at. That
  is a position the reader can read, whose own text puts the end past where counting stops and back around. A caller
  comparing or slicing the pair of marks would get a span running backwards. It is the same fault as a position too
  large to read at all, found a step later, and the message says so. A code the wire writes nothing for is the last of
  the bad arguments it took. It wrote the code character out unchecked. A code with no wire character therefore wrote a
  line no reader could read back, and `EINVAL` covers it. `ys_code_char` answers `'\0'` there rather than `'?'`. `'?'`
  was safe for as long as nothing claimed it as a code. A code can claim `'\0'` at no point. A line is NUL-terminated,
  and a code written as one would read back as an empty line. `check_wire.py` holds a character in the table to being
  printable. That is what a wire being text meant throughout. A code the enum names has a character. This therefore
  answers an out-of-range code. That is a caller's mistake rather than a code the library produces.

- The grammar settles ill-formed UTF-8 against fixtures, before any C parser exists to get it wrong. The reference
  interpreter reads its input a character at a time out of the bytes. A byte that begins no character is a value of its
  own, `<invalid>`, rather than an exception thrown before the parse starts. A token's text is therefore the input bytes
  untouched. A fixture can at last cover ill-formed input. Recovery's `l-unparsed` interleaves runs of such bytes as
  `YS_CODE_UNPARSED_INVALID` tokens among the `unparsed-text` and the breaks. A run is maximal. It ends where valid
  UTF-8 resumes, or at the end of the input. On the wire that token's `\xXX` is a raw byte, and not the codepoint it
  would be under any other code. The reader holds a `~` token to being ill-formed throughout. A valid character anywhere
  among its bytes is a malformed wire. That mirrors the writer, and the writer already refused to write one.

- Errors tell the caller what to do about them. A malformed document gives `YS_CODE_ERROR`. The text holds the message,
  and the wire character is a `!`. A malformed wire gives the same code. It is bad data like a bad document. A host
  failure that is not the data's fault is `ys_read_token`'s return value, a `ys_status`, and not a token. That covers
  running out of memory, and it covers a reader failing. It ends the source for good, and a new source with a larger cap
  reads the input again. `ys_options.resume` says what the parser does with the input after a malformed document. By
  default the error ends the parse, and the rest of the input comes back as `YS_CODE_UNPARSED` tokens. Haskell
  YamlReference does the same, and the pair of token streams therefore stay comparable on any input.
  `YS_RESUME_DOCUMENT` instead continues at the next document. A malformed document in a stream then does not cost the
  caller the remaining documents. `YS_RESUME_INDENT` continues inside the document, at the next line no more indented
  than the entry that failed. A malformed entry then does not cost the caller the rest of its container either. A policy
  gives up less of the input than the policy before it. A policy with nothing to resume at falls back to the policy
  before it. The price is that the input before the first error is what stays comparable with Haskell YamlReference. A
  skipped line is two tokens. The content is a `YS_CODE_UNPARSED` and the break is a `YS_CODE_UNPARSED_BREAK`. The break
  gets its own code. The parser found no structural break there.

- Parser state. The state holds the window over the input. It holds the stack of productions the parser is inside. The
  state also holds the queue of tokens built and not yet handed back, and the machine state. It sits in a single struct.
  The C call stack holds none of it. That is what lets `ys_read_token` hand back a token from the middle of a production
  and resume there. The queue holds a run of undecided tokens. Once the parser learns what the run held, it rewrites the
  codes and injects a marker ahead of the run. The stack holds the grammar's runtime parameter `n`. The generator does
  not build the automaton that drives them yet. `ys_read_token` still returns a "not implemented" error.

- A conformance suite, `tests/spec/`. Somebody built it once from Haskell YamlReference's vendored `tests/`. It took the
  fixtures that align with libyeast's grammar. The build turned an expected output into what libyeast emits, rather than
  what Haskell YamlReference does. A production libyeast flattens to a character class becomes plain unparsed. A token
  spans a line at no point. A byte-order mark is the character it matched, and not Haskell YamlReference's encoding
  name. An error keeps the position and drops the wording. The build drops Haskell YamlReference's isolated-run commit
  artifacts. Haskell YamlReference itself departs from the spec in the plain-scalar `:` and `#` factoring. libyeast
  follows the spec. The build leaves out fixtures in encodings libyeast does not read, and fixtures for Haskell
  YamlReference's own internal productions. From there libyeast owns the fixtures, and nobody keeps the build that made
  them. `generator/check_spec_tests.py` keeps them intact. An input is paired. A name is a production the grammar still
  has. An output is a token stream whose marks chain and whose markers balance. A fixture of the root is a whole parse
  and must balance exactly. A fixture of a rule run outside the root may close what its caller would have opened. It may
  still not leave a marker open. That last is what `check_markers` cannot reach. It settles the grammar's clean paths.
  It says nothing about what an error leaves behind. Both imbalances the gate has found have been there. A fixture whose
  name calls its input invalid must have one. The production refuses that input, or stops short of its end. A clean
  match is out of the question. The name is a claim, and an unchecked claim is how `c-printable.invalid` came to hold a
  character `c-printable` accepts. The suite holds the bytes verbatim, and that covers CR and CRLF. A line ending goes
  through unnormalized.

- A reference interpreter of the grammar sits in `generator/interpreter.py`. That module is a slow backtracking matcher,
  and obviously correct. The interpreter runs a production against an input and emits its yeast tokens. It is checked
  fixture by fixture against the conformance suite. libyeast's grammar is therefore proved to produce Haskell
  YamlReference's tokens before any C runs. It matches a node family. Those are the character-level nodes and the
  repetitions. They are also the parameter machinery that threads the parameters and detects indentation. They are also
  the assertions and lookahead. The ongoing `(exclude)` guard that stops a plain scalar at a document boundary is among
  them. It produces tokens from the annotation nodes. It gives a run a code, and brackets a match in `begin` and `end`
  markers. It emits a bare marker, and writes an error token naming what the parse wanted. It backtracks in the
  success-continuation style, re-entering an alternation when a later element fails as the reference does. It reproduces
  a fixture token for token, `l-yeast-stream` and the malformed inputs included. That rests on a promise the emitter
  makes and nothing checked. The promise is that a checkpoint captures the state, and an alternative that fails can
  therefore be undone. `make verify-emitter` checks it. A field comes back, and comes back the same way twice. An
  alternation rewinds to a single checkpoint once per branch. It was not true. A checkpoint handed out the parameters
  rather than a copy of them. A discarded branch's `(set)` therefore reached into what the branch after it rewound to. A
  fixture could see it at no point. The grammar's sites of that kind set the same parameter in a branch of the
  alternation, and whatever leaked was overwritten by the branch that matched. A malformed input is where the grammar's
  `(cut)` earns its keep. A cut commits. If the parse then fails, the interpreter emits an error token naming what the
  cut expected. It closes the markers the abandoned parse left open. It hands the rest of the input to `l-recover`, the
  grammar's own recovery rule. That rule brings the input back as unparsed. A failure that passed no cut is not an
  error. The production simply rejects its input, and the report names where the match ended.

- Error reporting lives in the grammar. The `(cut)` points mark where a parse commits. An `(error)` is an error token
  the grammar writes where it already knows the parse cannot go on. An error names a message in `grammar/messages.yaml`.
  That is the source the interpreter reads and the C message table generates from, gated so the two cannot drift.
  `l-unparsed` is the recovery rule they hand the rest of the input to. It brings the input back a line at a time, as
  `YS_CODE_UNPARSED` content and `YS_CODE_UNPARSED_BREAK` breaks. It consumes anything, and earns a character set in the
  decoder. Moving the key's length field up into spare bits freed that set. The block header gained a lookahead, and its
  two orderings then need no backtracking for a cut to block. That is a declared deviation, and the official header is
  ambiguous there. An anchor commits after its `&`. An alias commits after a `*` the same way. Both are indicators, and
  neither can therefore begin anything else where a node's properties may start. `&` with no name is a mistake, rather
  than a rule declining to match. A message says what its own cut expects and no more. A `...` marker requires a comment
  or a line break. That is what its cut guards. It had claimed a new document had to follow. A stream holding `...` and
  no more is valid.

- A block scalar's leading empty lines obey the spec's prose in section 8.1.1.1. An empty may out-indent the first
  content line at no point. The reference and the BNF read such a line as content. Its extra spaces fall through the cap
  of `l-empty(n)` into `s-indent(n) nb-char+`. A space is an `nb-char`. libyeast makes it an error instead, and
  `check_vendor_spec` declares the divergence. A forward parser with no lookahead learns of a broken floor only once the
  content line arrives. That is therefore where it speaks. `l-leading-empties` emits a leading empty whatever its
  indentation. It raises `f` to the widest of those lines with the `(increase)` action. `f` is a new runtime floor
  parameter. That is `f = max(f, column)`, made explicit rather than magic. The structural transformation then has less
  to infer. `s-indent-floor` then takes the content line's own indentation, and only after it an under-indent error
  keyed `BLOCK_SCALAR_UNDER_INDENT`. Reporting it there is also what tells an empty scalar from a violating one. An
  empty scalar has no content line to come. It reads as this line being under-indented, rather than a past empty line
  being over-indented. There the indentation match fails before the cut, and the scalar ends with no error. The literal
  and folded styles share the mechanism. The step draws the folded fork into folded-versus-spaced lines after taking the
  shared indentation. The floor is therefore checked once, and fixtures enforce both.

- An implicit mapping key obeys the spec's bound in section 7.4.2. A parser resolving whether a `:` makes the entry a
  key must see it within 1024 characters. The official grammar writes this as `(max): 1024` before the key production, a
  length note it does not enforce. libyeast makes `(max)` a wrapping window, `(max): [1024, IMPLICIT_KEY_TOO_LONG, key]`
  around the production, that the interpreter runs. The window is the deterministic parser's bounded lookahead. It
  matches the key, but no further than 1024 characters. It takes the interpreter's own unbounded lookahead to get there,
  and then keeps what fit. A key that runs past the limit is an error, `IMPLICIT_KEY_TOO_LONG`, and unparsed from there.
  The tokens up to exactly the 1024th character come out first. The window cuts the run where the limit falls. A token
  split across the limit therefore comes back under its own code. Then comes the error, as a failed cut leaves things.
  Recovering the official grammar undoes the wrapping back to the preceding `(max): 1024`. `check_vendor_spec` therefore
  still reads it rule for rule, and declares no divergence. The grammar also holds the key to a single line, and that
  needs no window. The flow-key context already binds a key's separation to `s-separate-in-line` and its scalars to a
  single line. A break is therefore consumed inside a key at no point. Fixtures pin the limit falling inside a token and
  on the boundary between a pair of them. They also pin a flow-collection key that a break would run onto a second line.
  That fails as an unterminated flow collection.

- `l-yeast-stream` is the root the parser runs. It is a YAML stream, and then the end of the input. A part of the spec's
  `l-yaml-stream` is optional. A `]` is no stream at all, and the rule matches nothing there. That would leave the input
  unaccounted for. The parse would say nothing about it. The root makes that an error and the input comes back unparsed.
  A byte therefore reaches the caller whatever it holds. Its second alternative matches throughout. The first way
  `l-yaml-stream` finds is therefore the way taken, and no parse backtracks into it. The root is libyeast's own rule.
  `l-unparsed` is another. The spec's rule 211 stays untouched, and the official grammar still comes back from
  libyeast's rule for rule.

- The resume policy is the grammar's fifth parameter. `ys_options.resume` chooses what the parser does with input it
  cannot parse, and the grammar says what a choice means. `l-unparsed(n,r)` guards its own run. Under
  `YS_RESUME_DOCUMENT` the run therefore stops at the next `---` or `...`. It does not eat the rest of the input.
  `l-recover(n,r)` brings that run back, and then parses the stream again from the marker. The two are mutually
  recursive. A second error inside a resumed document therefore needs no mechanism of its own. A failed cut arrives at
  the same rule the root does. A cut says where the unwind lands, and it says no more. `r` is finite, and takes `c`'s
  and `t`'s fate rather than `n`'s. It specializes away at generation time. The emitted C holds an automaton per policy,
  and `ys_options.resume` chooses the start state. A fixture names the policy it runs under. It writes `.r=d` beside the
  `.n`, `.c` and `.t` the filename already has. A fixture that names none runs under the default a zeroed `ys_options`
  selects.

  The resume policies are one hierarchy, and a policy adds a place the run stops. A policy therefore gives up less of
  the input than the policy before it. They are nothing, then `c-forbidden`. The last is `c-forbidden` or
  `s-indent-le-line(n)`. That last is `YS_RESUME_INDENT`. It continues *inside* the document rather than at the next
  document. A malformed entry costs its container that entry and no further entries. The recovery reaches a sibling of
  what failed, rather than a child of it. `c-forbidden` is not redundant there. `s-indent-le-line` cannot hold below an
  indentation of 0, and a run bounded by nothing would go straight past a document marker. A failure nothing encloses
  leaves the indent guard dead, and the policy is exactly `YS_RESUME_DOCUMENT`. It is `le` and not `lt`. A sibling entry
  sits at exactly the container's indentation, and `lt` would skip the entries the policy exists to keep. It is not `eq`
  either. A container with a malformed last entry would then eat the rest of the document. It would hunt a sibling that
  does not come. `s-indent-le-line` is pinned by a lookahead for content. That forces a reader to measure the line's
  whole indentation, and keeps a blank line and a comment line from being boundaries. Neither has an indentation of its
  own to speak of. It is two lookaheads rather than the character class `ns-char - c-comment`. A difference is a
  character set, and the decoder's key has room for the sets it holds and no more.

- `(recover)` says where a failed cut stops unwinding. A cut unwinds past any call between it and whatever answers for
  the cut. This is a rule saying "that is me". A block collection wraps the entry in a recovery, and names the `n+m` it
  has already computed. An indentation is therefore recovered from the runtime at no point. The recovery reads the
  parameters of the rule that declares it. It does not read the parameters of whatever failed below. The error is
  emitted. The parse closes the markers the entry opened, down to that depth and no further. The parse gives the run up,
  and continues as though the entry had matched. The collection's own repetition therefore takes its next turn.
  `s-indent(n+m)` matches where the next entry begins, and fails where the collection ends. The parse has to know
  nothing about which of the two the run stopped at. The node holds no policy. A rule reached under a policy that
  recovers elsewhere has no branch to take. It therefore does not match, and the cut goes on unwinding. That is why the
  remaining policies come out byte-identical. Flow collections get none. Recovery is by indentation, and a flow node is
  a level of it. A flow node holds no place to resume at.

- An error closes the markers it opened. A raise skipped the returns that would have emitted them. A malformed document
  would otherwise end with its `begin-` markers hanging. That was harmless while an error was followed by unparsed
  input. It is wrong the moment the parse resumes. The next document then parses as a child of the failed document. It
  should have been a sibling. A caller reaching for the documents an error did not cost would find them nested inside
  the wreck. The parse closes them at the error, after the error token and before the first `unparsed`. They are
  zero-width at the byte that failed. Their order therefore says that the error is inside what failed, and that the
  unparsed run is inside nothing. A `begin-` gets its `end-` on a path. That is what lets the fold that rebuilds the
  production tree work on an errored stream at all.

- A grammar-coverage gate runs `generator/check_grammar_coverage.py` through `make verify-grammar-base-coverage`. The
  fixtures must exercise a production both ways. Coverage is dynamic, and not by name. A production counts when running
  a reproducible fixture reaches it. The fixtures that reach a production therefore cover a production with no fixture
  of its own. A production nothing reaches is a gap. It takes the grammar as an argument, and re-runs on a
  structurally-transformed grammar as those arrive. The suite gained an empty stripped literal `|-`. The scalar-closing
  `end-block-scalar` is therefore exercised by a clean fixture rather than an error one.

  Reaching a rule is half of exercising it. A rule is a decision. A fixture that watches it say yes and no more leaves
  the other answer untested. A fixture must therefore also watch a production reject an input by failing to match. A
  `(cut)` inside it does the same, and the failure takes the message the cut named. The exception is a rule that
  *cannot* say no. The gate computes those rather than listing them. The body's shape proves totality. The gate
  therefore asks for no fixture where `l-yaml-stream` fails. A part of that rule is optional, and such a fixture cannot
  exist. That a rule cannot say no is worth knowing anyway. That is exactly what let `l-yaml-stream` swallow a whole
  input. `l-yeast-stream` was written to say so. A `(cut)` is a decision too. A cut that does not fire is a commit point
  nothing shows to be reachable. The message behind it goes unchecked. A token must therefore appear in a fixture's
  expected output. The gate checks it against the fixtures rather than by watching the interpreter, and that is
  stricter. It proves the error survived the trip back to the caller. A cut that raised inside a lookahead would prove
  that it can raise, and no more. New fixtures close what this found. Those are cuts that had not fired. They are also
  `c-reserved`, `ns-tag-prefix` and `ns-global-tag-prefix`, and no input had made those refuse.

  The interpreter enters the top production as a reference, the way any other rule arrives. It does not match the body
  directly. A rule run at the top is still a rule, and the gate that watches references could not see it otherwise. That
  is what had hidden them. Their fixtures existed and rejected throughout.

- The YAML Test Suite folds to events. `generator/star.py` is the independent net, and `make verify-star` gates it. It
  is vendored under `third_party/yaml-test-suite/` and written by other hands from the same spec. It therefore catches a
  grammar bug libyeast's own fixtures would share. Those fixtures came from Haskell YamlReference. libyeast parses
  tokens. Tokens are a level below events. The check is therefore a deterministic fold. The yeast stream's `begin-` and
  `end-` markers rebuild the production tree, and the leaf tokens fill it. The fold projects that tree to the events the
  suite states. Those are the stream and the document events. The mapping and the sequence events go with them. They are
  also the scalar and the alias. Node and pair brackets go, and so does the presentation. A scalar's value comes off the
  codes the parser settled. A `line-fold` is a space and a `line-feed` is a newline. The fold resolves an escape. The
  gate strips nothing, and the tokens already separate content from whitespace. A tag resolves through the document's
  `%TAG` directives over the default `!` and `!!`, and the `%XX` URI escapes decode. A valid case must fold to its
  `test.event`. An error case must come back a rejection. The fold reports even the two resolution errors a token stream
  cannot show. Those are a named tag handle no `%TAG` defines, and a repeated `%YAML`. A case holds green-or-declared
  against the HTML spec, and that spec is the source of truth. libyeast declines to match `JEF9/02`. It is an empty kept
  block scalar whose input ends in no line break. YAMLStar loads it by first appending the break, and the YAML Test
  Suite therefore expects the line feed that break yields. The spec appends nothing. End-of-input is a line break in
  `b-chomped-last`, and a scalar with no content does not reach it. libyeast therefore folds it to the empty scalar, and
  declares the divergence from the suite.

  The net earned its keep, correcting the grammar and the interpreter that libyeast's own fixtures had agreed with. A
  quoted scalar or flow collection at a document's top, or as a block-sequence entry, is first tried as a block-mapping
  key. Its `UNTERMINATED_*` cut committed at the opening. A scalar that closed cleanly but found no `:` therefore fired
  the cut. The parse did not backtrack to the scalar. A whole `"hello"` document became an error. A flow cut covers its
  own item, and the error therefore comes where the item does not close. `:` is an `ns-anchor-char`, and `*a:` is
  therefore the alias `a:`. The backtracking interpreter shortened the name to `a` to open a mapping. A
  `<not_followed_by_an_ns-anchor-char>` guard holds the alias to its greedy match. Haskell YamlReference and YAMLStar do
  the same. A block scalar's last content line at end-of-input keeps its break. That is the zero-width line feed
  `b-chomped-last` emits, where the spec reads end-of-input as a line break. A block scalar with no content takes the
  content indentation from the widest empty line. That is the fallback the spec gives in section 8.1.1.1. The root the
  parser runs, given no resume policy, takes the zeroed one. Trailing content it cannot parse therefore recovers, rather
  than the interpreter asserting the root is total.

- A pair of invariants say what they are about. `every-end-of-stream-gates-a-leaf-way` asked a pair of things at once.
  It asked that a gate holds an `EndOfStreamGuard`, and that the way that gate admits calls nothing. The second is
  untrue. A way that reaches the end still has the wrapping up to do, and may hand it on. A continuation is where a
  callee left off. A call made with nothing left to give it is another thing. It fired repeatedly the moment a callee's
  ways went into a caller that had one. `every-end-of-stream-sits-in-a-gate` remains. It has the same shape as
  `every-consume-is-protected-by-a-gate`. Whether a character is there is a question. The question therefore belongs
  where a way asks its questions.

  `every-way-has-actions-or-a-call` becomes `every-ungated-way-has-actions-or-a-call`, and reads none. A way that holds
  a gate, does its actions and then calls is an edge the machine runs. The grammar forbids it at no point. The rule has
  content for a way with **no** gate. Such a way needs a gate. A pair of ways supply it. The first hoists a guard up out
  of its callee. The other writes the callee's ways in. Both need those guards to reach the position the parse enters
  the way at. `GUARD_CROSSES_ACTION` says they cannot reach past what the way performs. It is therefore scaffolding that
  empties itself. `every-conditional-way-is-gated` at none leaves no ways for it to be about. A single checker says what
  "ungated" means. `_ungated_ways` is that checker, and both invariants ask it.

- An invariant must change no grammar, and a gate must finish. A test is a question and not a change. That lets the
  counting pass hand the same grammars to the checkers at once, in whatever order the cores take them.
  `Invariant.__call__` therefore reads the grammar before and after, and refuses a grammar that differs. It does so in a
  `finally`. The next invariant gets a grammar a checker could not answer about, the same as any other. `gate.spread`
  puts a watchdog over an item, and over its own wait for an answer. It names the item, prints where a thread sits, and
  takes the run down. A check that spins printed its last line and then nothing. That line said which worker happened to
  print last, and no more.

  A walk was doing quadratically what it could do once. The question about where a parse enters a production scanned the
  grammar for a name on a round, and an invariant re-ran it. That question becomes an index of the call sites. A grammar
  has an index of its own. `no-production-reaches-itself-unconsumed` grew a name's whole reach, a relation the square of
  the grammar. That invariant becomes a walk of Tarjan's components. The walk answers who reaches themselves, and what a
  reader may take before what. The scope signature ran the whole grammar to a standstill, pinned whatever still moved,
  and began again. Over a grammar of the width the pipeline reaches, that is a walk per production per round per name,
  and it does not finish. The pipeline's own check runs in seconds.

- A step says which thing it does to an invariant, and `establishes` is the third. The step reads none of the grammar it
  hands on. The question made no sense before the step ran. The shape it is about is what that step builds. `settles` is
  then what it says and no more, a count handed over broken and taken to none. A step naming a count already none is the
  fault it was from the start. A **claim** is a step that transforms nothing and establishes what it names. That is what
  `holds-at-the-door` and `holds-once-optionals-are-ways` were saying with the wrong word.

  The change buys a hard fault where there was a silence. Asking an invariant of a stage meant that a checker reaching a
  shape the pipeline had not built yet raised. The raise was caught and read as "not a question here". A checker broken
  outright and a checker asked too early were therefore the same answer. The gate asks an invariant from its own step
  onward. A checker that cannot answer there is a fault.

- A gate reads the character in front of it at most once. A pair of peeks in a gate are a question said twice. A pair of
  `LookGuard`s are the set they both admit. A `LookGuard` beside a `NegLookGuard` is the set the first admits and the
  second refuses. The hoists bring them together, and a hoist moves a question to a position another already holds. The
  build of ways with gates therefore establishes `every-gate-looks-ahead-at-most-once`. A hoist declares a lapse of that
  invariant. A `merge-gate-peeks` behind it says the sets as one. A gate mixing kinds that cannot fold into a set
  raises, and no pass steps over it. An `EndOfStreamGuard` holds no set, and a `LiteralPeekGuard` holds a run.

- A call goes into the way that calls it where doing so pays, and the site itself says so. The site decides on what it
  would leave. The input to the site decides nothing. A way nothing has gated goes. The callee's ways go into the site
  the call had. A site whose ways come out ungated keeps its call. A site taken therefore lowers the count by one. The
  walk has a floor, and it runs until nothing moves. A pass judging a site on its input writes a way out. The promise is
  that a later pass will gate that way. That promise is what has no floor. A round composes the state such a way
  continues at afresh. A pass therefore finds a new site to write out, and descends. A guard against that is a guard
  against a rule that was simply wrong. That is a bound on the passes, a circle to refuse, or states named by what they
  hold. `every-conditional-way-is-gated` still reads a count where the phase ends. The phase is the expansion, the hoist
  to the callers with its merge, and the expansion again.

- The sweep's duplicate-merge reads a body once instead of once a round. A pair of productions behave alike where their
  bodies match. A reference reads as the group of what it names. That is a partition refined until a round splits
  nothing. A round had written a body out afresh with the group numbers substituted in. A round moves a name between
  groups, and leaves its position untouched. The shape with the names taken out is therefore built once, and a round
  compares a tuple of group numbers beside it. `check_normalize` runs in a third of the time.

- The gate reads the crossing table's values. That is what they were for. `GUARD_CROSSES_ACTION` answers `True` or
  `False`. It may answer nothing at all. An unnamed pair refuses the move, and the table records that the pair came up.
  "No" and "not yet" cannot then be mistaken for one another. The tree called `unnamed()` and `unconsulted()` at no
  site. The difference existed and went unreported. A walk took an unnamed pair for a worked-out refusal, and looked
  settled while it was only ignorant. The gate then named `LookGuard` in front of `OpenWindow` at once. That is the pair
  that was holding a flow mapping's implicit key ungated. The table already answers it a line above. A window bounds
  what a committed consume may take, and no lookaround at all. It named cells nothing asks. Those cells are gone.

- **A conflict that what follows decides has to hold what follows.** `every-conflict-is-a-tail-call` counts the call
  sites. At such a site a way enters a production the machine cannot steer through. Something sits behind that call. A
  decision the position cannot settle is settled by what comes after it. A parse can look at what comes after only from
  inside the call. A way calling a production and continuing holds that continuation in its own frame, out of reach of
  what the call does. The choice is therefore made in front of the thing that decides it. That is the backtracking
  `every-choice-way-is-different` counts, and it happens a frame up.

  Being a conflict spreads to whatever ends in one. A way ending in a call hands its own end to the callee. A call the
  machine cannot steer through is therefore a way it cannot steer through. The production holding that way is untellable
  too. Reaching that production anywhere else does not spread it. A call with something behind it comes back. The return
  lands among the way's own parts.

  A single checker, `_conflicted_ways`, says which ways conflict. A pair of questions ask it. This counts the call sites
  of the productions holding them, where `every-choice-way-is-different` counts the ways themselves. A phase has taken
  it on at no point. The invariant therefore sits in `OWED` beside that one, and the gate reads its count at the end.

- **A pair of ways that do the same thing are not a pair of ways to tell apart.** Both counts over the ways of a choice
  compared the states a parse enters a way in. Neither asked whether the pair *do* anything different. A machine takes
  the first way whose gate holds and does not come back. A pair of ways alike but for their gates are therefore a single
  way reached a pair of ways. Whichever gate answers first, the same actions run and the same calls go out. The same
  input is read. `s-separate-lines` reaches its flow prefix both at a line start and at the end of the stream, by a pair
  of such ways. There is nothing for the input to decide. The pair of gates cannot become a single gate. A gate is its
  guards holding at once, and this is either holding. `_does_the_same` says it. `no-choice-ways-partially-overlap` and
  `every-choice-way-is-different` both ask it before counting a pair or cutting one.

  The split also fixes a bug in `split-overlapping-ways`. The step made pairs of ways alike in a part, where the grammar
  handed it no such pair. *Other* ways a pair half overlaps cut that pair, even where the pair is alike but for its
  gates. The atom where their own gates overlap then gives either a piece narrowed to the same gate. Those pieces agree
  in a part, and the gate is among them. A piece is the way. The other piece is that way said twice. The second is
  therefore not written out.

  `every-choice-way-is-different` **falls**. A way among them was no conflict, and the rest of the drop was the step's
  own duplicates.

- **A count measures the backtracking that remains. A parse enters a way in the states of a way behind it.** With a way
  gated and no pair half overlapping, a pair of ways of a choice can overlap a single way. A pair overlaps by entering
  in exactly the same states. There the input says nothing about which to take. A machine reading a character and asking
  a question of it has the same answer for both. The machine takes the way that comes first, and gives it back where
  that was wrong. `every-choice-way-is-different` counts the ways that happens to. The count holds an entry per way a
  parse enters where it also enters a way behind. The gating count counts ways rather than the pairs they make. It is
  worst in the block-indented flattening and the stream's document loop. A parse enters a way of a choice somewhere. The
  count therefore measures ambiguity. It does not measure ways nothing reaches.

  The count reads the split working rather than the split costing. Cutting a way by the atoms of its choice turns a pair
  that half overlapped into pieces that are apart or the same. A shape wanted a state in an overlap told from a state
  just outside it. It becomes a pair of ways the input cannot tell apart at all. The work did not grow. It became the
  kind the determinizing knows how to answer.

  `OWED` comes back with it. A change ago it had gone out empty. An invariant with a test but no phase counts at the end
  beside what the steps name. This is such an invariant.

- **A parse enters the ways of a choice in the same states, or in none of the same.** `no-choice-ways-partially-overlap`
  is **settled**. `split-overlapping-ways` refines those states into atoms. Those pieces do not overlap. The pieces
  together cover what the ways cover. A way becomes a way per atom inside it. The piece keeps the way's own gate and
  gains the guards that say the atom. A state that admitted a way admits a piece of that way, and the split loses
  nothing. A pair of pieces is apart or the same. An atom is exactly that. The pieces go into the site the way had, and
  the parse falls where it fell. The choice's else takes no part. The else has no gate, and the parse enters it wherever
  the ways in front did not fire. That is not a state an atom holds.

  The guards minted are ones the grammar could already write. `_COMPARES` gains a pair of rows. `0 < <column>` is for a
  position off a line start. `n <= len(match)` is for a consumed length no shorter than the indentation. Both use
  comparison kinds that already exist. The interpreter therefore runs them unchanged. The step synthesises a gate and
  then holds the gate to what it came out as. `_admits` reads the guards back, and their overlap with what the way
  already admits must be the atom. An atom the step cannot say raises. It does not come back approximated. A piece is
  said exactly.

  The design did not foresee what follows. A subspace coalesces the byte that begins no character with codepoint `0`,
  where a `CharSet` keeps them apart. An atom turned back into a set therefore dropped a run of characters. A later pass
  had to tell the pair apart again. An atom pins an axis its answers agree on. That includes an axis another pinned axis
  already forces. `n <= 0` says `n <= len(match)`, and a length is not negative. The gate is therefore built an axis at
  a time, and keeps what narrows. The split would otherwise hold questions the answers already settle. A way narrowed to
  fewer characters than the take holds gets a narrowed take too. A take of a character names the atom's set. A run has
  its first character peeled off into the gate. A state of its own holds the remainder of the run. That is the same
  maximal run said in a pair of places. Such a run takes at least a character, and stops where its set stops.

  Splitting also exposes a way a parse enters and then fails on any input. The gate asks for an indentation of none, and
  the way calls a rule that wants the consumed length shorter than that. The way behaved the same before, and the parse
  reached the way behind it. The path into that rule said nothing about the indentation, and no checker could therefore
  see the fault. `_does_reach_nothing` says which piece to drop. The step drops that piece rather than writing it out. A
  piece takes nothing, and its callee offers no way those states can reach. That is a call the machine would make
  knowing it fails.

  The grammar **widens slightly**, and `merge-gate-peeks` runs again behind the split. A piece's gate gains the
  characters its atom holds, beside whatever the way already asked. A pair of questions about a character are a single
  question. A set saying them together makes that plain.

- **A gate asks about a character. A literal is therefore the characters it holds.** `fold-literals-into-gates` said a
  run of single characters as a `LiteralPeekGuard`. `---` became a comparison rather than a state per character, and the
  parse decided it before consuming anything. The step bought states and cost truth. A literal's subspace is its first
  character, where the guard asks for several. A single state is no place to ask about a later position. It was
  therefore the last guard the space could not put exactly. That is what would have stopped the step settling
  `no-choice-ways-partially-overlap`. Pairs sat half apart with a literal on a side. Splitting them by subspace would
  make pieces that *read* identical, while a piece really wanted `---` and the other `-`. Settling those wanted a
  negated literal peek. That is a new kind through the interpreter and the crossing table. A total question over guards
  goes with it. It is also a literal coordinate the space cannot hold, in both the invariant and the step.

  Said per character the question does not arise. The step is gone, and with it `LiteralPeekGuard` and
  `ConsumePeekedAction`. The fold was what built them. Their `then` and `barrier` fields were already dead. The fold had
  passed `None` throughout. `mint-consume-states` runs to a fixpoint for it. The step cuts a way at its second take, and
  mints a state for what follows. That state is where a third take goes, and a longer run therefore takes as many
  rounds.

  The change costs productions, distinct character sets in gates, and lookaheads. The decoder is untouched. It builds
  from the authored grammar rather than the normalized one. The step gives up deciding `---` before consuming. Per
  character the way takes `-`, finds no second dash, and hands it back. That is a backtrack the literal hid rather than
  removed, and a backtrack the determinizing has to answer for either way. The purchase is that a gate refuses a guard
  the grammar holds at a given answer. A count says so rather than an assumption. A pair of ways is therefore read apart
  or together, and not guessed at. `_admits` names the two kinds that could be, and neither occurs. Those are a peek
  that pins no set, and a look-behind at any set but `ns-char`. The count itself does not move, and the invariant reads
  the same either way. The change moves whether a reader can believe it.

- **A break is a line feed, or a carriage return with a line feed after it where a line feed follows.** The official
  grammar's `b-break` is a way apiece for carriage return with line feed, carriage return, and line feed. The first pair
  begin the same way. A parse at a carriage return can tell the pair apart only by taking the longer way and handing it
  back. A rule that consumes a break inherits that. Many pairs of ways sat on `CRLF`, where a way wanted both characters
  and the other wanted the first. The first sat nested inside the other. To the subspace the pair read identical, and a
  subspace sees a literal as its first character. Said as `LF | CR LF?` the same matches come out with nothing to back
  out of, and no input tells the two meanings apart. `check_vendor_spec` declares it beside the other deviations from
  the official BNF. `c-b-block-header`'s is the same shape of change.

- **A pair of ways can overlap half apart, and that overlap is no use. The count runs per way.** A parse enters a pair
  of ways apart, together, or half apart. The first pair are of use. Apart, the input says which to take. Together, the
  input says nothing. Something else must decide. Half apart is the shape that is no use. A machine has to tell a state
  in the overlap from a state just outside it. That is what makes a machine ask a pair of questions where it should ask
  a single one. `no-choice-ways-partially-overlap` counts the ways it happens to. There is a fault per way that half
  overlaps a way behind it. `every-conditional-way-is-gated` counts ways rather than the pairs they make. The count
  reads them the way `accepted-and-gated-charsets-are-equal` reads a gate. That is the way's own gate crossed with what
  the paths into its production ask, taken over the paths together. Said as pairs, the choices that decide between more
  than a single way hold pairs apart. They also hold pairs half apart and pairs together.

  The invariant belongs to no step yet, and `OWED` holds it. The gate counts it at the end beside what the steps name.
  Nobody writes it into a document by hand. `spaces.SubSpace` gains the difference the refinement will want. A
  difference answers on the states it holds. The union and the intersection already answer that way. `_decided_ways`
  says what a choice decides between, and the last way is its else. It says that once, for this invariant and for
  `every-conditional-way-is-gated` alike.

- **A comparison of a pair of the parse's own quantities is an axis. The quantities are not.** The indentation and the
  column are integers of no fixed range. So are the length of the run just measured and the block scalar's floor. The
  space therefore held no coordinate for any of them. The space read a comparison between a pair of them as admitting
  under any answer at all. A guard admitting that much says nothing. The space could not say the sentence it exists to
  say. A guard reads a quantity at no point. A guard asks how a pair of them compare, and the grammar asks a fixed set
  of such questions. Those are therefore the axes, and the magnitudes do not appear. **A gate refuses a guard at some
  state**, where the space had admitted a whole class of guards throughout.

  Free booleans would admit states no parse is in. An ordering is transitive, and `f <= n` with `n < column` settles
  `f < column`. The enumeration therefore comes from the quantities rather than an assignment. They are the orderings
  integers make, under facts true of any parse. A line start is column `0`, and anywhere else is at least `1`. The
  interpreter says that outright, and sets `is_sol` to `column == 0`. The indentation reaches `-1`. A parse enters the
  root with an empty stack. A length is not negative. A consumed length falls within its line, and is therefore at most
  the column. A site that reads it measures `s-space*` under a `(token): indent`. A space is not a break. A break would
  reset the column under it. A space is not a byte-order mark either. A mark would take no column of its own. That last
  fact halves the orderings at a line start.

  A pair more bound the other axes. A character an `ns-char` names comes behind a line start at no point. Behind a line
  start comes a break or a byte-order mark. There may be nothing at all. A run that took its whole limit leaves the
  parse mid-line. A limited run in the grammar takes spaces or hex digits, and no consume consumes nothing. **Many more
  answers** result, and the pipeline runs no slower. The interning below made the count of answers something the algebra
  does not pay for. `check_spaces` judges them in both directions. The checker walks the quantities over a wider range
  than `spaces` does. An answer no parse reaches says the enumeration is not what it claims. A state no answer names is
  a hole a subspace would say nothing about.

- **A single `Characters` per distinct answer.** The algebra then costs what the sets number, rather than what the
  answers number. A `SubSpace` held a `Region` dataclass per answer it admitted anything under. The code sorted the
  regions into a canonical tuple. A dict built beside it gave the index. An intersection therefore built an object per
  answer, a dict and a sort. An answer added would have paid those costs again. It holds a set per answer, positionally,
  and the sets come out of a table. Equal ones are the same object, and equality is identity. The table remembers what a
  pair of them come to. It works nothing out afresh. The distinct sets are a small table. The pairs that overlap and the
  pairs that join are small tables too. The arithmetic had been done over and over.

  An intersection is **7.0us where it was 78.9us**. A union is 6.9us where it was 72.3us. The tables the two leaf
  invariants read take 0.4s where they took 1.1s. The second is not what it buys. The answers are free to grow with the
  questions the guards ask. That is what a space that can put a comparison of a pair of the parse's own values will
  need. `check_spaces` holds the table to its own promise. It asks for a set of states written a run of ways. They are
  out of order, cut in half where the halves close up, and with a span repeated. The check holds them to a single
  answer. A table keyed on the input stops identity from answering equality. The key has to be what the input comes to.
  A pair of forms of a set then become a pair of sets the algebra reads as different.

- **A comparison takes its name from the comparing. A column has no part in that name.** `ColumnLtGuard` and
  `ColumnLeGuard` hold a pair of values and assert an order between them. The column appears in a minority of the guards
  the grammar holds, and it is not the left operand. The other guards mention no column. They relate `n` and the block
  scalar's floor. They also relate the length of a match and the literal `0`. The docstrings said "the first indentation
  is less than the second", and that is wrong the same way. Some of the shapes name neither an indentation nor a column.
  They are `IsLessThanGuard` and `IsLessEqualGuard`. The grammar writes them `(<)` and `(<=)`. The pair of `_admits`
  handlers are `_is_less_than_admits` and `_is_less_equal_admits`. A handler takes its name from a shape among the
  shapes the handler answers.

  The crossing table said a comparison crosses a `SetVarAction`. Its ground was that a comparison names the indentation,
  a literal, or the length of a match. It does not. `f <= column` and `f <= n` name the floor. The floor is one of the
  pair of slots such a write writes. An instance collides at no point, and the grammar was therefore right. The entry
  licensed an instance that would collide. It is the same shape that crashed `b-l-folded` when a turn's close was let
  past a code's pop. The gate asks it of the pair in hand, and `_does_read_nothing_the_write_writes` reads the operands
  for the slot. `_is_using` cannot answer it. It looks for a `ParamValue`, where a read of `f` is a `GlobalValue` by
  then.

- **A turn that closes where it opened takes a path no input takes, and the path goes.**
  `every-conditional-way-is-gated` is **settled**. It read well above none when the phase began. The ways left were one
  shape. A path opens a turn that must take a character, and reaches that same turn's close, with the same `pair` on
  both. The ways between them take none. The close asks whether the parse took anything since its own open. On such a
  path the close therefore refuses on any input. The way holding the path fails there and falls to the way behind it.
  The path dropped, that refusal comes sooner. The step drops the path rather than cancelling it. A turn that refuses
  the input is the turn's point. Annulling the open against the close would make the path *succeed*, and a run over a
  body matching empty spin. `l-yaml-stream` wraps `l-document-prefix`, whose pair of halves both offer an empty way.
  That is where the shape arises. `("EndMustConsumeGuard", "StartMustConsumeAction")` is gone from the crossing table,
  with the paths that asked it.

- **The pair in hand settles a crossing the kinds cannot answer.** A write says what may not match at a start of line.
  Whether a lookaround may come in front of that write depends on which lookaround and which set. The lookaround matches
  its item through that refusal, and reads the set in force at that position. The guard admits a state, and what is
  forbidden can begin taking a character in a state. A position where those do not overlap answers alike on either side.
  A `_Crossing` entry may name a question rather than an answer. `_does_read_none_of_the_forbidden` is such a question.
  It crosses `_admits` with the accepted space. An entry naming a question sends the walk to work the pair out, and the
  instance decides. It is therefore neither of the table's pair of faults. `l-explicit-document` forbids `---` and
  `...`, whose accepted space is `-` and `.`. Its ungated way reaches gates asking about a space and a tab. The other
  gates ask about a carriage return, a line feed and a `\r\n`. Those are disjoint from the accepted space. The write
  therefore cannot change one of their answers. `every-conditional-way-is-gated` **falls again**. The ways left are one
  shape, a turn's close behind its own open. In front of that open it has nothing to take off the stack at all.

- **A way nothing has gated becomes the paths it holds.** `flatten-ungated-call-trees` walks such a way through the
  calls *and* the continuations. A path reaches a gate. A path runs out at no point, circles at no point, and ends on a
  body that is a choice. A way holding no call is not the end of a path. The innermost thing still pending runs next,
  and the gate is in there. A walk that stopped at the missing call reported those as paths reaching nothing. A path
  becomes a way. That is the gate it found, whatever the path performed in front of that gate, and the call at that
  point. Behind them comes a chain of states running the remainder. The leaf's own continuation comes first. The pending
  continuations follow it from the innermost outward. A leaf that only continues gives the head of that chain the free
  slot. It does not sit a state deeper. A question about a limited run is asked by the first call a way makes, and a
  state further on the run has been taken away. `every-conditional-way-is-gated` **falls** over the whole grammar.

  The paths left are a refused crossing apiece. Some reach gates asking a lookaround behind a `SetForbiddenAction`. The
  other paths reach a turn's close behind its own open. A gate splits at no point, a path's gate holding a single guard.
  Asking the guards that may come up here and the remainder a state deeper would gate a way on part of its question.
  That has no case in this grammar, and nobody has written it.

- **`expand-called-ways` is gone. The pair of steps that served it go too.** Writing a callee's ways into the site the
  call had is one level of what the flattening does over the whole tree. The count lands the same either way. Its worth
  was size, and a narrower grammar. Its cost was a hidden defect. With the expansions out, `hoist-guards-to-callers`
  breaks the corpus without them. `merge-gate-peeks-2` had nothing left to merge. `hoist-guards-to-callers` stops
  lapsing `every-gate-looks-ahead-at-most-once`. `expand-called-ways` and `merge-gate-peeks-2` therefore come out with
  it, and no step replaces either.

- **A turn's close takes its own open off the stack. A code above that open refuses the question.**
  `GUARD_CROSSES_ACTION` let an `EndMustConsumeGuard` cross a `PopCodeAction`. It reasoned about where the parse is, and
  a code is neither the input nor a count. The guard *pops*. The parse opened a code in front of the guard and closed it
  behind. A guard asked early reaches for the open, and finds the code. `hoist-guards-to-callers` moved a guard into a
  gate in front of exactly such a pop, and `b-l-folded` crashed on it. The pair says `False`.

  The table names the pairs nothing asked it. The first is a comparison in front of a `SetVarAction`. A comparison reads
  the indentation, a literal or a match's length, and not the pair of variables the parse holds. The other is a
  `StartOfLineGuard` in front of a `PushIndentAction` or a `SetForbiddenAction`. Neither of those is where the parse is
  in its line. The pairs only the expansion asked are gone. The table stays pinned to what occurs.

- **A parse hands back no action. A guard is what refuses.** A refusal is where a choice goes on to its next way. A gate
  that admitted an action lets the action do its work. Failing that, the gate lied. A gate that lied is a crash and not
  a parse. `_can_be_refused` said exactly that in words, and its table said otherwise. The table listed the consumes
  among the kinds an input can hand back. It says a single thing. An action answers no. A consume answers no. A guard
  answers yes. A set where a match belongs answers yes. It is a match rather than either of the pair.

- **A consume holds the set it consumes, and consumes that set.** `ConsumeCharAction` meant "the character the gate
  found". A gate hoisted to a caller took the consume's meaning with it. A reader then had to work out what a way
  consumed from whichever guard happened to come in front. It holds its own `set`. The interpreter can then ask of a
  consume what it actually did. Re-probing the set answers a prediction instead of the event. At a stage and over the
  whole corpus, the answer is that **a consume consumed nothing at no point**. The grammar writes a run of a class as a
  gate that found the class beside a consume. The consume therefore consumes. `ConsumeSpanAction`,
  `ConsumeLimitedSpanAction` and `ConsumeTrimmedSpanAction` join `ALWAYS_CONSUMES`. `_does_scan_read` recovered that
  fact from the gate, and goes. With it go `_ahead_of_gate`, `_ahead_of_any` and `_ahead_of`. So do `_narrowed_ahead`,
  `_entering_guards` and `_spans_meeting`. Both consume splits go too.

- **`ConsumeLiteralAction` is gone. Nobody ever made such an action.** The generator constructed no such action. The
  whole history holds no call. `ConsumePeekedAction` consumes the literals the grammar writes, under the
  `LiteralPeekGuard` minted beside it. Those literals are `---` and `...`, and the `YAML` of a directive. It was a kind
  a question had to answer for, and an input could reach at no point.

- **The two subspaces come from different halves of a way.** `_accepted_way` intersected the way's own gate.
  `accept <= gate` then held by construction, and the two could not disagree. It reads consumes and calls only. The
  gated subspace reads guards only. Their agreement is worth something. `accepted-and-gated-charsets-are-equal` compares
  the union over the paths into a production, rather than a single path. `l-folded-content` decides whether a space sits
  here, and its pair of ways continue into the same tail. The branch that consumed a space reaches that tail knowing
  nothing about the next character. The branch that found none still knows there is no space. A path is narrower than
  what the tail consumes, and their union is exactly that.

- **A leaf way takes what the parse entered it on, and a path reaches such a way.** A way holding no call answers by its
  own actions. The parse enters a way in a space, and accepts another space. An invariant holds the pair to one another
  before the pipeline moves anything. `_asked_where_entered` already gave the guards asked along a path into a
  production, gathered from the last take onward. The intersection of those with a way's own gate is the states a parse
  may enter that way in. A pair of invariants read them. `accepted-and-gated-charsets-are-equal` compares answer by
  answer instead of whole subspaces. A caller may know the parse sits at a line start where the way asks about the
  character. The caller then knows more than the way asks. The pair does not disagree. The leaf ways that take a
  character agree with their paths at an answer. `every-path-reaches-a-leaf-way` asks for a way rather than for the
  whole set. `l-document-prefix` offers a way that takes a byte order mark and a way that takes nothing. The path
  reaching it at the end of the stream can take only the second. Read the stronger way, the invariant called that a
  fault. That fault is what showed the relaxation.

- **The subspace algebra stops being the cost of asking.** Reading the two invariants at a stage put `_accepted_spaces`
  on the counting pass. There it tripped the watchdog that refuses a slow invariant. The shape was right throughout.
  `under` scanned the regions for a point where a lookup answers. The code computed an intersection as the complement of
  a side subtracted from the other. `chars.intersected_spans` merges the pair directly. The fixpoint swept a production
  on a round, where a production's answer can change when a callee's has. A worklist holds the unvisited productions.
  Together they take the invariant back under the watchdog, with the same answer to the span.

- **A match nothing makes says so.** A `(case)` on a finite parameter listed values and said nothing about the values
  outside the list. The spec reads silence as declining those values. An absence then said what a production did under
  such a value. "It matches nothing" and "nobody asks" are two different things an absence cannot tell apart. The
  specialization made that silence an alternation of no ways. A walk past it then had to read an emptiness as a refusal.
  `_split_ways` reported `l-recover-entry` as having no empty way. That is true of a production with no ways at all, and
  means the opposite of what it reads as. `<fail>` is the twin of `<empty>`. `<empty>` is what any input makes taking
  nothing. The other is what no input makes at all. The cases that were silent name a value of their parameter.
  `validate_grammar._check_total_cases` holds a case to that. The specialization raises where a value has no branch. It
  mints no emptiness.

- **The ways nothing takes come out, and no later phase sees such a way.** `prune-failures` is a phase of its own behind
  the specialization. The phase can go no earlier. A `<fail>` is a branch until the specialization runs. A branch does
  not yet say what the production does. A choice drops the ways nothing enters, and is itself a `<fail>` where that
  leaves none. A run holding a `<fail>` matches nothing. A call of a production that matches nothing matches nothing. A
  recovery whose handler nothing enters is no recovery. The cut goes on unwinding to the handler above, as it did with a
  recovery that matched nothing. A commit keeps what sits under it. A refusal there is the error the commit names, and
  not the error of the choice. The declines go to a single decline at the specialization and to none at the phase. The
  grammar loses the productions nothing could reach, and `no-fails` reads none from there on. Dropping the dead recovery
  stopped the non-recovering policies' compact mappings merging with the indentation-bounded policy's mapping. That
  showed the corpus had no `r=i` fixture holding a compact mapping, a hole the merge had been hiding.
  `l-yeast-stream.recover-compact.r=i` closes it. `generator/regen_fixture.py` is what wrote it. A fixture's stream
  comes from the interpreter. An indent or a white token holds trailing spaces that a hand loses.

- **A guard says which states it lets a parse through in.** `normalize._admits` reads a guard as a `spaces.SubSpace`.
  `_accepted_spaces` says where a production can begin taking a character. The walk over a way takes the states a parse
  that took nothing can still sit in. A guard past a take asks about a later position. It narrows nothing. The checker
  is sound rather than decisive. A comparison between two of the parse's own values fixes no coordinate and admits
  throughout. That is true of the comparison. A literal's first character is a constraint, where the rest of the literal
  is a residual the axes do not speak for. A state a gate admits that the space refuses is therefore a hole the grammar
  really has. Apart from the indentation comparisons, the guards in the final grammar name a coordinate. Those relate
  `n`, the column and a match's length to the floor. `_split_ways` still answers whether a way can take nothing. The
  subspace does not fold that in. A production that succeeds taking nothing succeeds anywhere. That is true and says
  nothing. Holding the two apart is what stops it poisoning a caller through the fixpoint.

- **A subspace says which states a parse can decide in.** A guard asks about an axis of a small space. A pair of axes
  are the character in front and the character behind. A pair more say whether the parse sits at a line start and
  whether it sits under indentation. A pair of bits of the parse's own bookkeeping make up the remaining axes. A parse
  sits at a single point of that space when it decides. A gate admits a subset. A way takes a subset. A call site can
  reach a subset. `spaces.SubSpace` is that subset. It holds the characters admitted under an answer. An axis is finite,
  and the subset is therefore exact. `spaces` computes union, intersection and containment answer by answer. The end of
  the stream is the character axis's own value, and not the absence of a character. A way entered there is therefore a
  way entered somewhere. A comparison relating a pair of the parse's own values is no axis at all. A guard asking such a
  comparison constrains nothing. `check_spaces` judges the algebra by the states it holds rather than by the algebra. It
  compares the operations against set arithmetic. The arithmetic runs over an enumeration of the answers, and over an
  alphabet spanning a boundary the cases name.

- **The span algebra lives in a single place.** `normalize` had its own copy of merging and subtracting codepoint
  intervals. That copy matched the code in `chars` character for character. The character model behind the decoder
  already keeps that code.

- **Nothing a way performs can fail.** A counted consume took `n` characters of a class or none at all, and no gate
  could protect it. A gate speaks for the character in front of it. It does not speak for `n` characters. A consume
  asked for more than is there. Such a way failed on the doing, rather than on the decision. The last such ways in the
  grammar were the counted consumes. They were the failure edge in the machine that no question guarded. `x{n}` is a run
  of up to `n` of the class. The run takes what is there and says whether it reached the limit. The guard behind it
  asks. The taking matches. The refusing is a question like any other. `ConsumeCountedSpan` is gone from the IR. The
  pipeline already wrote that shape for `x+`. That is a consume that may take none, made to take a character by the
  `LookGuard` in front of it. The split on the count happens where the pipeline mints the run and its guard.
  `split-counted-spans-on-the-count` gated the consume afterwards, and is gone with the kind it gated.

- **A question names the kinds it leaves out, and says whether an answer exists for those kinds.** `ir.Question` took
  `NEVER` for a kind a wide group names and the checker does not reach. That claimed an impossibility nobody had proved.
  A pair of lists replace it. The lists differ in whether an answer exists. `untested` names a kind a family answers for
  that nothing has asked about. The family answers a kind that arrives. The checker records the kind, lists it, and
  fails the gate. The gate forces a decision. `unknown` names a kind nothing answers for at all. Reaching such a kind
  raises. The checker refuses `untested` on a kind no group of the checker names. There is no answer to call untested.
  Splitting the existing uses proved the distinction real. `untested` names the kinds their families answer for.
  `unknown` names `ChoiceState`. That kind has no answer at all.

- **What a run reaches is recorded by the run.** Coverage was collected by rebinding `interpreter.match` and
  `interpreter._evaluate`. The rebinding missed a handler reaching a production any other way. The report then read
  exactly like a covered one. A run fills an `interpreter.Coverage` where it enters and hands back productions. The gate
  reads that record. There is no outside to bypass. The same reasoning retires `interpreter.match`'s own dispatch chain.
  It is a table over the kinds, but not an `ir.Question`. A caller reaches a question through its type. The C stack
  bounds the nesting. This matcher recurses once per grammar step.

- **A single place names the families of node kinds.** A list of a pair of kinds or more lived beside whichever checker
  used it. The same idea then had a pile of memberships that nothing compared. They become a section of `ir.py`. A read
  of the lists together found a pair that did not match its own words. The consume family left out the trimmed consume,
  though its own words cover a consume of a character class that may take none at all. The zero-width family left out
  the gate's literal form, though it matches without consuming. `validate_grammar` had been patching around that inline.
  `TAKES_CHARACTERS` narrowed `CONSUMING` to the forms a phase uses. It made a way holding a `OneCharSet` count as
  taking nothing, and is gone. A family's name says which category its kinds come from. `ZERO_WIDTH` is
  `ASKED_NOT_TAKEN_NODES`. Taking nothing is what an action and a guard both do. The kinds in that family hold
  characters that a parse asks about and does not take. That is what tells them apart.

- `no-empty-nodes`, settled by `build-alternatives`. A way is a gate, a list of actions and the calls that way hands
  control to. An empty match is none of them. A way matching the empty input is the way with no gate, no action and no
  call. The machine's own words have no place for such a way. The lowerings mint them freely. The last of them go where
  `build-alternatives` builds the ways.

- **A kind's name says the category it is in.** The categories are an action, a guard and a call. A wrapper, a character
  set and a tree the lowerings remove are categories too. So are a state the machine has, a value the parse works out
  and a part a node holds. A kind's name ends in the category it belongs to. A use site then says which. Nobody reads it
  against the families in `ir.py`. `CharSet` and `Wrapper` needed nothing. Both names already end in a category. `Char`
  becomes `OneCharSet`. That set holds a single character. `CharSet` is taken by the kind holding spans.
  `GUARD_CROSSES_ACTION` keys its pairs by `type(node).__name__`. The rename covers its answers along with the classes.
  Left behind, the table would name no pair at all, and an unnamed pair refuses the move exactly as a worked-out no
  does. That is what the step-changed-nothing assertion caught.

- `every-span-question-follows-its-run`, established where the pipeline mints the run and the question about it. The
  guard reads what the action in front of it did. That action has to be the run. The run is the item before the guard
  among the things a way performs. Minting the guard states may make it the head of a state. It is then the last act of
  a way calling that state. The call is the first that way makes. A parse enters a call made past another call wherever
  the earlier call left off. Whatever the callee performed has then taken away what the run did. The interpreter already
  refused this where it happened. The grammar answers instead. A step that moves the two apart is a fault at the point
  of the move.

- **A question about the grammar is an `ir.Question`.** Some were still answered by an `isinstance` chain. The audit
  that found them is worth keeping. A membership test naming a family is what the families are *for*. Of those that
  answer differently per kind, the step transforms reshape named kinds and pass any other kind through. That is a
  rewrite and not a question. The list below holds the questions.

  - `validate_grammar.consumed`, what a node takes and whether an annotation covers it.
  - `check_grammar_docs.emitted`, the codes a node emits.
  - `normalize._peeked_question`, the question a peek asks.
  - `normalize._reached_forbidden`, what is forbidden where a match ends.
  - The walker inside `every-character-question-is-a-character-set`.

  Two of them answer by `yield from` and by appending rather than by returning. A search for a chain of returns does not
  find them.

  The peek's chain was the chain that mattered. The tail of that chain handed the node back as its own question. A shape
  nothing had looked at then said "ask about this", and the readers believed it. Those readers were `lower-runs`,
  `span-consumes` and the character-set invariant. The remaining chains ended in `elif isinstance(node, ir.KINDS)`.
  Their own docstrings said a kind the arm leaves out raises. The arm already names the whole set of kinds. That raise
  is unreachable. Both read the vendored grammar before any lowering. They therefore call the canonical forms untested.
  They do not walk into those forms by default.

- `ir.py` names the categories as families. `TREES`, `STATES`, `PARTS` and `CALLS` join those that were already there.
  Together the families cover the kinds exactly. A question naming the other categories raises for a kind in no
  category. A question naming `KINDS` cannot. That is what made it worth doing.

- A `Prod` is no kind of node and is not among them. `KINDS` is the kinds of thing that sit inside a body. A production
  holds a name, a parameter list and a body. A walk of a body reaches such a wrapper at no point. It was the class that
  fell into no category. The checker refuses a question naming it. A table answering for it answers for what no walk
  asks.

- **A rule matched what the last consume took, and not the token the parser is building.** `(len): (match)` read the
  open token's length. The parser cuts a token wherever an annotation opens or closes. The answer was therefore the
  parser's own bookkeeping rather than the grammar's question. `(len): (match)` reads `consumed_length`. A consume
  leaves that slot behind under whichever form the pipeline gives it. Those forms are `StarTree`, `PlusTree` and
  `RepTree`. `TrimStarTree` and the `Consume...Action`s are forms too. A rule asking gets the same answer before and
  after the lowering. `(atoi): (match)` reads the token, and rightly. It wants the digits of a block scalar's
  indentation indicator, and `(token): indicator` wraps exactly those.

  A gate makes a consume take at least a character. A run of none or more that takes none therefore performs no consume
  at all. A length from an earlier run would read as the length of that run. `ConsumeNoCharAction` says it outright.
  `ConsumeNoCharAction` sits under the refusal of the same set that gates the taking way. `x*` is `[look x] consume x`
  or `[not x] consume-none`. It names no set. The way that took nothing makes no difference to what it leaves behind. A
  pair of consume-nones are a single action wherever they sit together.

- **A gate asks and does not write.** `EndMustConsumeGuard` took its own open off the parse's stack while answering. It
  therefore held refusals in `GUARD_CROSSES_ACTION` that another guard does not need. It is split into
  `DidConsumeSinceOpenGuard` and `EndMustConsumeAction`. `DidConsumeSinceOpenGuard` reads. The guard finds its own open
  by the pair, whatever the parse pushed over that open. The second closes the region. The guard crosses a code's close
  like any other question. The refusal it keeps is the refusal a guard shares with any other guard. A committed region's
  close is the line a refusal changes meaning across.

- **The indentation a parse sits at stops at the `-1` a parse enters the root at.** The grammar does compute lower. A
  parse enters `l+block-sequence` at `n - 1`, and enters a sequence at the root at `-2`. A reader answers alike for
  both. The reader of such a value is `(<): [n, <column>]`, against a column that cannot be negative.
  `interpreter._indent` holds what it hands back to `-1`. A state of a parse then falls inside the answers `spaces.py`
  enumerates. It also refuses a null. The grammar writes a null where no indentation applies.
  `ns-flow-yaml-node: [null, c]` is such a place. A reader may measure against it there at no point. That was true and
  unstated.

- `ConsumeTrimmedSpanAction` asserts it consumed something, as the span and the limited span already do. The consume of
  a single character cannot take nothing. It has nothing to assert. The assertion sits ahead of the step that would
  build such a consume. A step does at no point. `KEPT_THOUGH_DEAD` declares that. The tree has run the step at no
  point. The step cannot run.

- **The grammar says where a parse may sit, and real parses check that answer.** A production has an *entry space*. A
  parse may sit there when it enters that production. A production also has an *exit space*. A parse sits there when the
  production hands control back. `normalize._entry_and_exit_spaces` grows the pair together. They are one question and
  not two. A production's entry is the space the sites calling it sit in. That is what the ways making those calls
  leave. A way leaves a space that runs through the exits of what it calls. Entry grows downward from the productions a
  parse enters by name. Exit grows upward from the ways that call nothing. Both start from none, and they feed one
  another until neither moves. `_entry_and_exit_spaces` reads the pair for a parse that starts at the root. A seed
  naming somewhere a caller starts a rule of its own would answer a question about that caller. The question is about
  the machine that ships.

  Underneath it is a transition per action. `normalize.after_action` says where performing something leaves a parse.
  `spaces.reached_by` does that arithmetic once per action rather than once per site. `spaces._standing_at` saturates a
  quantity that runs past the enumeration. The walk therefore takes a space through a way action by action. Nobody
  guesses it at the ends. `normalize.spaces_of_ways` then intersects a production's entry with a way's own gate. That
  intersection is where a parse enters the way.

  **`check_normalize._spaces_held` is what stops that being a story about the grammar.** It runs the corpus through
  `interpreter.run(checking=...)`. It asserts that the parse really sits somewhere the computed space admits. The
  assertion runs at both ends of a way and at the far end of an action. A lookaround is no exception. The interpreter
  answers for where it sits through `_standings_now`. A space computed too narrow is exactly what this catches. A
  checker reading only the grammar cannot. The other direction is caught by nothing. A space admitting more than any
  parse reaches goes unremarked until something prunes by it. `PLAN.md` therefore owes the two cuts that would read
  these spaces. This entry does not claim them.

- **A look-behind reads the character behind the parse rather than searching for where a match could have begun.** The
  look-behind the grammar makes asks about a character. It asks whether an `ns-char` sits behind.
  `interpreter._try_look_behind` reads that character and matches the item against it. An item of more than a single
  character is **refused**. Finding where such a thing began would re-match input the parse has already read, at a cost
  in the length of it. It would answer a question nothing asks. That is a refusal on what the grammar may write, and the
  stages of the pipeline have to keep satisfying it.

- **A break character puts the parse at a line start, and the line count waits for the feed.** A carriage return that a
  line feed follows is half of a break. The feed counts the line. The carriage return does not count it. The carriage
  return is still no part of the line it ends. A column between the pair goes unread. Saying so at the consume lets the
  space checker answer for a break by the character the consume takes. That is what a checker of the grammar has to go
  on. The input says what follows a carriage return. The set does not.

- **A consume takes at least a character, and `_shortest_match` says so.** A span of none or more read as taking
  nothing. A run up to a limit read as taking its whole limit or nothing at all. Both mistook the action for the way
  around it. The taking cannot fail. It takes what is there and says how much. `DidMatchFullSpanGuard` asks whether the
  run reached the limit. The guard asks past the run. The limited run was the **unsound** direction. A lower bound above
  the truth lets a reader conclude a match cannot take fewer characters than it can.

- `generator/review_input.py` prepares a change for review. It makes the joins and searches a reviewer would otherwise
  make by hand. A review's cost sat there. The list below says what it prepares.

  - The staged diff, split by what a question asks about.
  - A changed run of Python with its function, the docstring of that function in the tree and in `HEAD`, and the lines
    around the change.
  - A name or swept word the change removed, with whatever the tree still says about it.
  - A number the documents state, beside the names the gate can measure.

  `.claude/workflows/pre-commit-review.js` asks the review questions of a change. `.claude/agents/reader.md` is what
  answers them. It has `Read`, `Grep` and `Glob`, and no shell. A reviewer reaching for a shell has started casting
  about rather than reading what the workflow gave it.

  `review_input` refuses a tree holding a loose path, and names that path. It refuses a staged path no prepared file
  would show. A vendored path is the exception. `review_input` reports line numbers from `git diff --cached`, and reads
  the file beside them. The pair describe the same file where the tree holds no unstaged change. A reviewer reading them
  over a dirty tree would approve what happens to be on disk instead of what lands.

  A refusal reaches the workflow as an answer naming no file. `check_failures` holds a workflow that binds an agent's
  answer under a schema to asking whether that answer holds anything. Asking whether a file is there at all lets a
  refusal through. The reviewers then read whatever an earlier run left on disk, and report on a change other than the
  change in front of them.

  `.claude/settings.json` holds what a hook can decide from a single tool call.

  - The shell writes no file.
  - A question about the grammar is a total dispatch.
  - The tense says what the tree holds.

  The review above and the gates `make pc` runs hold a change to the rest of what it must satisfy. A check that reads a
  whole diff belongs to a reader with the diff in front of it. It may belong to a gate that can build the tree. A hook
  fired per edit is neither.

- `lower-continuations-into-conflicts` is in `normalize.py` and in no step of the pipeline. Over the grammar the count
  takes `every-conflict-is-a-tail-call` **up rather than down**. A copy holds the call sites of the original. It
  therefore names no invariant. The pipeline refuses that. The comment sits where its `Step` would go. `PLAN.md` owes
  what a step would have to answer for those copies.

  The step lowers into the conflicts that something outside decides. `_productions_decided_from_outside` names those
  conflicts. It runs to a fixpoint. Lowering a conflict can make another conflict. Telling the copies apart is what it
  turns on. `_is_alike_up_to_where_it_continues` raises where a pair of ways perform the same actions in a different
  order. The pair is not alike.

### Changed

- `ys_options.max_token_bytes` becomes `max_bytes`, and caps the memory the parser allocates rather than the bytes it
  buffers for a token. The buffered input grows, and the tokens held back with it. So does the parser's stack. Deep
  nesting grows that stack, and no quantity of input bounds it. A single cap bounds them together.

- `src/yeast.c` is gone. The split goes by topic. The version query and the load-time sanity check have a file of their
  own. So do the counting allocator, the stream adapters, and the yeast wire format. Allocation and the `max_bytes`
  accounting live in `src/memory.c`. They were one copy in the parser and another in the wire-format reader. The library
  refuses outright a reader held under a cap too small to build it in. The parser already worked that way.
  `ys_resolved_options` says what a NULL `ys_options` means. That function names the defaults where it reads the struct,
  and not again at a field. The reader hand-over and the teardown of what an object owns are a place apiece.
  `ys_discard_reader` serves a constructor that is already failing, and `ys_teardown` a destructor. Either was copies of
  the same close and the same errno care. A caller hands over a reader whether or not the library can build the object
  that would read through it. A constructor that fails therefore closes the reader rather than leaking it.
  `ys_discard_reader` discards a close failure it has no channel to report. It holds on to the error the constructor is
  already returning `NULL` for.

- The reader of the yeast wire format tells a broken wire from the tokens a wire holds. The parser tells a malformed
  document from a valid document the same way. A wire that is not the wire format is a `YS_CODE_ERROR` token, bad data
  like a bad document. The token's text is a message per way the wire can break. Its marks are the line and column in
  the wire. The reader validates its input as the parser does. A byte that a conformant wire would have escaped is a
  located `YS_CODE_ERROR`. So is an escape naming no Unicode codepoint, and so is a position that is not a number. A
  read of that kind is a fault rather than a misread. Such a read spends the wire. A host failure reading the wire is
  not a token but `ys_read_token`'s return. Out of the memory to buffer it gives `YS_FAILED_MEMORY`, and a byte source
  that fails gives `YS_FAILED_STREAM`. A read past the end gives `YS_FAILED_ACTION`. A caller reading until a negative
  return therefore learns why it stopped.

- **`make vet-mypy` reads the Python types.** It checks what has an annotation and asks for none where there is none.
  `mypy.ini` says so. That file names a module held to having an annotation throughout. `PLAN.md` owes the modules.

  The naming rule is what it starts for. `check_conventions` decides `a-boolean-name-is-a-question` for C by reading the
  `bool` off the declaration. Python has no annotations, and the same rule is a reader's there. A written `-> bool` is
  what moves it to the gate. It reaches a parameter and a field, where a checker of the returns cannot.

  `mypy` found what a cache holds. The empty containers a fixpoint fills say the key and the value. `ir.Kept` is what a
  `kept_for` store is, the subject beside its answer, keyed by the subject's id.

  `mypy` also found a thing that was wrong. `ir.LitValue` holds an int or a string. It may also hold nothing.
  `_can_be_refused` read a repetition's count as a number without asking. A count written as a string would have raised
  there.

- **A `bool` answers a question, and a command hands back a `ys_status`.** The internals had a second failure
  convention. These reported a host failure as `false`.

  - `ys_memory_reserve` and `ys_queue_make_room`, where the cap or the allocator refuses.
  - `ys_queue_emit` and `ys_stack_push` reach the allocator through the pair above.
  - `ys_append` and `ys_append_byte` grow the wire reader's text.
  - `ys_parser_fill`, where the source's reader fails or the cap does.
  - `ys_put`, where the byte transport fails.

  A command hands back a `ys_status`. The value then says which delegate failed. `ys_put`'s callers stop writing
  `ys_put(...) ? YS_OK : YS_FAILED_STREAM`.

  A parse is neither a command nor a question. `ys_hex`, `ys_scan` and `ys_unescape` fail on a malformed wire, and a
  malformed wire is a token rather than an `errno`. A negative `ys_status` holds an `errno`. A status there would have
  made one of a thing the policy holds apart. A parse hands back the position it reached. NULL comes back where the
  bytes were not what the parse needed. `ys_next_line` in the same file already answered that way. `ys_scan` loses its
  cursor out-parameter. The caller reads the marks in a loop that keeps where a scan gave up. `ys_hex`'s callers advance
  by what the call hands back. They do not compute a width again.

  `ys_hex` refuses without touching the value. A refused reservation charges nothing in the same way.

  `check_conventions` holds the rule. A C function handing back a `bool` under a name which is not a question is a
  fault. `is_continuation`, `ys_queue_is_ready` and `ys_are_tokens_stable` are the functions left. A name among them
  asks something.

- An `errno` policy across the API. Malformed data is no `errno`. A syntax error or a broken wire is a `YS_CODE_ERROR`
  token in the stream. A host failure is an `errno`. `ys_read_token` returns a negative `ys_status`. The `errno` is
  `ENOMEM`, `ENODATA`, or whatever the reader set. A function that fails without a token sets `errno`. Those are a
  constructor, `ys_write_token`, and the closers. `EINVAL` names a bad argument, such as a stream source with no `read`
  callback, or a memory parser given a NULL buffer with a length. `ENOMEM` names insufficient memory. The API passes
  through the value a failing callback set. An allocator or reader callback must set `errno` when it fails. A debug
  build asserts a failing allocator did.

- Closing reports. A buffered close is where a write finally reaches its destination. It is therefore where a full disk
  or a broken pipe is first seen, long after the last `ys_write_token` returned `YS_OK`. The `close` of a
  `ys_bytes_reader`, a `ys_bytes_writer` and a `ys_allocator` answers the contract of `close(2)`. That is 0 or -1 with
  `errno` set. Their `read` and `write` already answer `read(2)`'s and `write(2)`'s. `ys_delete_token_sink` and
  `ys_delete_token_source` return that failure rather than swallow it. A delete closes the byte transport. It closes the
  allocator after the memory goes back. That order lets the allocator be what the memory lived in. It runs the sequence
  through whatever fails, and a close that fails leaks nothing. It answers `YS_OK`, or `YS_FAILED_STREAM` if the
  transport's close failed. `YS_FAILED_MEMORY` names a failure in the allocator. `YS_FAILED_BOTH` names a failure in
  both, and `errno` holds the first failure. That a single `errno` cannot name a pair of failures is the documented
  limit. A caller that needs both failures records the pair in its own callbacks. The `ys_allocator` gains a `close`. An
  arena or pool then goes down with whatever the caller built out of it. The `ys_counting_allocator` installs
  `ys_close_counting_allocator`. That close checks for a leak. It reports a leak as a close failure, `-1` with `errno`
  `ENOMEM` and the memory the counter still holds. A delete through it therefore surfaces the leak as `YS_FAILED_MEMORY`
  rather than asserting.

### Fixed

- The yeast wire format dropped an error's message. It took a token's text to be the input the token spans, and an error
  spans none. A malformed document therefore wrote `!` and no message, where Haskell YamlReference writes `!` and the
  message. The wire exists to compare token streams against Haskell YamlReference. An invalid document is exactly where
  the pair of parsers differ. That gap broke the comparison precisely where it was worth the most.

- The wire reader handed out a token text that was not NUL-terminated, and `ys_write_token` took an error's length with
  `strlen`. A round trip of an error token through the wire overread the heap whenever the text filled its buffer
  exactly. That read-then-write pipe is what the wire exists for. The reader leaves a text terminated. A bare error
  reads back with an empty text rather than a NULL one, as the header promised.

- A reader leaked where the library could not build the parser behind it. A caller hands over a reader, and an owned
  file descriptor stops belonging to that caller. A NULL return left the caller with nothing to close the descriptor
  with. Both constructors close what the caller gave them, and preserve the `errno` that named the failure across the
  close.

- `ys_hex` accumulated 8 hexadecimal digits into a signed `long`. That overflows wherever a `long` is 32 bits. MSVC is
  among them. A wire could name a codepoint Unicode does not have, or half of a surrogate pair. The reader then wrote
  that codepoint into its own text as bytes that are not UTF-8. `ys_scan` read a position with `strtoul`. That function
  takes a sign. `# B: -1` was therefore a position of `SIZE_MAX`.

- The marker gate could not see inside a `(<<<)`. It walked the other nodes. It passed over the `(<<<)` and discarded
  what that node held. An unclosed `begin-` marker inside an indentation bound therefore passed the grammar gates. The
  gate that proves a marker closed had a blind spot. A second gate was missing.

- The coverage gate passed on a report that covered nothing. The day gcovr's filters stopped matching, the `// UNTESTED`
  contract would have evaporated in silence while the badge still showed a percentage.

- The reader of the yeast wire format grew its line buffer on a refill. The lines it had handed back may already have
  left room. It grew to the size of the whole stream. Under a cap it stopped partway, and read as a stream that had
  simply ended. A stream of 2000 short lines under a 16 KB cap yielded 2 tokens. It grows where the remainder really
  does fill the buffer. The parser's window already did that. The two had the same shape, and only one of them had the
  check. That is the argument for their growing through the same code.

- Nothing in the build system keeps a list of files by hand. `CMakeLists.txt` globs the sources and the tests with
  `CONFIGURE_DEPENDS`, as the `Makefile` already globbed its inputs. A list was still hand-kept, the `Makefile`'s set of
  files to lint. It had already gone stale. It named neither `src/parser.c` nor `src/messages.c`. The linter had seen
  neither file.

- The tests cover the `FILE *` writer adapter on Windows. The test had not run there. Its test was portable but sat
  behind the guard that hides the file-descriptor ones. Behind that guard was a second copy of the guard.

- Haskell YamlReference does not resume after an error. libyeast matched a reading of Haskell YamlReference that said it
  did. Haskell YamlReference emits the error token, hands back the input behind that token as unparsed, and stops.
  Resuming at the next document was therefore not fidelity but a departure. libyeast adopted resuming to protect the
  token-for-token comparison against Haskell YamlReference. That is exactly what it broke. Not resuming is the default.
  Resuming is an option the caller asks for. The caller knows what it costs.

- An error's message is a static string. Its lifetime is not an exception to the rule another token's text follows. It
  cannot be. The message names the production the parser was inside and what it expected there. Both of those come from
  the grammar rather than the input. The message leaves out what the parser found. The message does not need it. The
  first `YS_CODE_UNPARSED` token behind an error begins at exactly the byte that failed.

- No token spans a line. A text token spans no line. A comment spans no line. The input skipped after a malformed
  document spans no line. That comes back as one `YS_CODE_UNPARSED` token for a line's content, and another for its
  break. A token that spanned a line would have made a stream parser's output depend on how much of the input its buffer
  happened to hold.

_Nobody has tagged a release. The YAML parser itself does not run yet._
