# Changelog

`CHANGELOG.md` lists the notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The ABI is unstable through `0.x`.

## [Unreleased]

### Added

- Project framework. CMake builds a shared library and a static library. The build hardens both, and controls symbol
  visibility. The framework holds an incremental pre-commit `make` gate and tests under acutest. It holds coverage with
  a `// UNTESTED` contract. It holds Doxygen docs with a completeness gate. It also holds a package-consumption test.

- Continuous integration. There is a GitHub Actions workflow per sub-gate. The workflows cover static quality and C
  tests, the generator pipeline, and the docs. A workflow has its own status badge, and CodeQL analysis runs beside the
  workflows. GitHub Pages publishes the API docs and an HTML coverage report. A workflow runs on a pull request as well
  as on `main`. `make pc` refuses a change locally, and the workflows therefore refuse that change remotely. The docs
  workflow holds the `// UNTESTED` coverage contract and the docs completeness check. The docs workflow builds on a pull
  request. Its deploy waits for `main`.

- Version API. It is `ys_version`, `ys_major` and `ys_minor`. `ys_patch` goes with them.

- Token-source API surface. `ys_new_yaml_memory_parser`, `ys_new_yaml_stream_parser` and `ys_new_yeast_stream_reader`
  make a `ys_token_source`. `ys_read_token` pulls a token from that source. `ys_delete_token_source` releases the
  source. The surface also holds the `ys_fd_reader` and `ys_fp_reader` adapters, a pluggable allocator, and the
  `ys_counting_allocator` leak counter. A caller reads tokens parsed from YAML and tokens replayed from a yeast wire
  through the same source. Code reading tokens therefore cannot tell a parser from a wire reader. The parser and the
  wire reader are the arms of a tagged union that holds a kind field above the arms. The arms hold different state.
  `ys_read_token` fills the caller's token and returns a `ys_status`. A halt token does not repeat. A read that yields a
  token returns `YS_OK`. A failed read returns a negative status and sets `errno`. A failed reader gives
  `YS_FAILED_STREAM`, and a failed allocator gives `YS_FAILED_MEMORY`. Reading past the end gives `YS_FAILED_ACTION`.
  That failure comes from the call rather than from a delegate. A host failure ends the source there, and emits no
  `end-stream` to close the `begin-stream`. That missing close shows that the source did not finish. The token model
  leaves out a code a document cannot cause. A parser out of memory gives such a code. A failing reader and a fault
  inside the wire reader give such a code too. `YS_CODE_ERROR` is the `!` the wire writes. `YS_CODE_ERROR` names a
  malformed document or a malformed wire. The tree holds no parser core. A parser's `ys_read_token` returns a "not
  implemented" error.

- Token-sink API surface. The sink mirrors the token source. `ys_new_yeast_stream_writer` makes a `ys_token_sink` over a
  `ys_bytes_writer`. `ys_write_token` feeds the sink, and `ys_delete_token_sink` releases the sink. A caller therefore
  sends a token stream onward the same way to any destination. The sink has a pair of arms. The yeast writer serializes
  tokens to a wire. `ys_new_yaml_stream_emitter` writes the bytes a token spans. A wire replayed through the emitter
  therefore reconstructs the YAML it came from. A test runs that round-trip over the fixture corpus. `ys_write_token`
  returns a `ys_status`. It is `YS_OK`, or `YS_FAILED_STREAM` if the byte transport failed. `YS_FAILED_ACTION` names a
  token the sink cannot write. Such a token has a code the wire writes nothing for, or text that lies about its code. A
  `YS_CODE_ERROR` the emitter receives is such a token too. The emitter renders tokens and does not judge them. The
  emitter refuses a token it cannot write. A caller filters out such a token above the emitter. The emitter moved from a
  `ys_bytes_writer` to the sink. The byte transport stays underneath, as a `ys_bytes_reader` does for a source.
  `ys_delete_token_sink` replaces `ys_close_writer`. A buffered write fails at the flush `ys_delete_token_sink` runs.
  The failure names in `ys_status` say no direction. `YS_FAILED_STREAM` names a failed reader or writer.
  `YS_FAILED_MEMORY` names a failed allocator. A source reads and a sink writes, but both close a transport the same
  way.

- Character decoder. The decoder validates UTF-8 input and classifies it against the grammar. It assembles no Unicode
  codepoint. A character becomes a 32-bit key. The key holds the id of the character where the grammar names it. The key
  also holds a bit per character set the grammar tests, and the count of bytes consumed. A test in the parser is
  therefore a single comparison or a single AND. The generator writes the tables from the grammar, and a gate refuses
  drift.

- Annotated grammar. `grammar/yeast-spec-1.2.yaml` is libyeast's own grammar. libyeast generates its parser from that
  file. The file holds the YAML 1.2 rules together with the yeast tokens those rules emit. The official grammar cannot
  express those tokens. That grammar inlines the indicator characters. The inlining loses the structure that the yeast
  tokens mark. That grammar also names no token. The same file writes down the yeast token format. A separate file for
  that format holds the notation and the codes. A note beside a rule says why the rule emits its tokens. A gate erases
  libyeast's additions and recovers the official grammar exactly. The author writes by hand the parts of the grammar a
  gate cannot reach. A gate proves the rest of it. A character the parser consumes must lie within a token action. A
  forgotten action then fails the build. The fault does not surface later as an `unparsed` token. An `end-` marker must
  close its own `begin-` marker. The pairing holds on any path and under any context. It also holds under any chomping
  and any resume policy. A rule that emits tokens must name those tokens. A gate checks the names against the grammar. A
  note that is wrong then fails as surely as one that is missing.

- A byte order mark takes no column where a consume asks for it. Such a mark is no character of the line. It neither
  ends the line's start nor advances the column. A token that follows a mark on the same line therefore reports the
  column that token would have had with no mark there. The `#` of `l-document-prefix.bom-comment` is at column 0. The
  mark would have put it at column 1. A column reads the indentation of a construct, and a mark before that indentation
  is no part of it. A mark inside a scalar is content. The content classes hold the mark in their upper range. There the
  mark takes a column like any other character. The set the consume names decides which of those cases holds.

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
  something other than a space. That search ran past the current line where the parse was mid-line. The search then ran
  past empty lines with no bound. Those rules measured an entry against the line the search found. The parse is already
  at the start of the line the first entry is on. libyeast therefore takes that line's own run of spaces as the indent
  token. `<column>` is the `n+m` the entries measure against. That value must be deeper than the indentation in force.
  The entries then run at it. `l-block-seq-entries` and `l-block-map-entries` are libyeast's own rules, and take
  `<column>` as their `n`. The parse has consumed the first entry's spaces already. A later entry begins with
  `s-indent(n)`. That indent ends the collection where a line holds less indentation. A call passes that indentation as
  an argument rather than writing the indentation. A call that names a parameter as its own argument passes that
  parameter by reference. `s-l+block-collection` calls the mapping with a bare `n` where the sequence goes through
  `seq-spaces`. A write would have escaped through the call with the bare `n`.

  The collections measure their own entries. The pipeline step that measured for them finds nothing. It hoisted a loop
  into a production. A caller entered that production at the value the loop measured. A step that finds nothing is a
  fault by the pipeline's own rule. The change removes that step. The step's phase keeps `clear-m` and `read-global-m`.

  The block scalar's indicator decides the indentation of its content. That decision is about the first content line,
  and that line sits calls away from the indicator. The decision therefore travels as `i`. `i` is `given` where the
  indicator named the indentation. `i` is `detected` where the indicator named none. The content rules pass it to
  `s-indent-floor`, where the `given` and `detected` forms differ by a single node. That node is exactly `n` spaces, or
  a span with the guards on what it measured. That node is also where the parse establishes `n`. `i` is a finite
  parameter. `monomorphize` therefore specializes `i` into the names as it does the context. The generated parser tests
  no mode. `lift-chomping` becomes `lift-setters` and inverts both setters. `specialize` settles `no-i-t-parameters`.

  A scalar with no content line at all establishes no indentation. The empty lines after the scalar would have to state
  the indentation instead. libyeast therefore takes a trailing empty line whole. The widest trailing empty line sets the
  indentation floor. A trailing comment measures against that floor. Under `keep`, the trailing empty lines are the
  scalar's own content. The indentation decides which lines belong to the scalar. `JEF9/01` in the YAML Test Suite
  writes a `- |+` and a line of spaces.

  libyeast runs nothing that reads ahead of the input it has consumed. `<auto-detect-indent>` has no site and the
  interpreter evaluates no such node. The IR node stays. The vendored grammar writes it, and a single reader loads the
  pair of grammars.

- `gate.run_deep` puts a gate's walk of a grammar on a thread of its own. The gates call this helper rather than a copy
  of it. It gives the work a stack deep enough for the recursion a transformed grammar needs. It ends the process with
  the exception the work raised. An exception inside a thread is printed by the thread's own excepthook while the main
  thread exits `0`. `make pc` then reads green over a check that did not finish. A copy caught only the `SystemExit` its
  gate reports through. A step going idle exposed that fault.

- `check_documents` and `check_dead_code` are gates of their own. Somebody cut them out of `check_normalize`. A gate's
  prerequisites list the files the gate reads. The prerequisites of `check_normalize` left out the documents and
  `scripts/`. Editing `DESIGN.md` therefore re-ran the formatter and skipped the check that holds DESIGN's counts. Those
  counts passed on a stamp written before the edit. A gate with a narrower job has a prerequisite list a reader can
  check. `verify-documents` reads the documents and the grammar. It also reads the sources a citation can name.
  `verify-dead-code` reads `generator/` and `scripts/`. `verify-normalize` reads the corpus and the grammar. A single
  gate and a single checker read the counts in a document and the counts in the generator's prose.

- A question the generator answered more than once has a single answer. `interpreter.run` walked the whole grammar to
  ask whether the grammar pushes indentations. That walk ran per fixture and per stage. The answer belongs to the
  grammar rather than to a run. `invariant_faults` says in its own docstring that a table takes a count once. Its last
  law then re-derived those counts a grammar at a time. `normalize._denoted_spans` wrote `chars.spans` out again.
  `_relevant_finite` propagated a value up the call graph in rounds over the whole grammar. The callers of a changed
  production are the callers with anything to reconsider. The change buys no measurable speed. The run-to-run spread of
  `make pc` is wider than any gain the change makes. These questions had a second place apiece, and no gate held a pair
  to agreeing.

- `check_dead_code` reads a call at a module's top level as running what it names. The walk's roots were the names a
  module reads outside its own definitions. The walk then dropped the names the module defines. That is right for a
  mention beside a definition. It is wrong for a call. The gate therefore reported a check a module runs on itself as a
  function nothing reaches. A bare mention still roots nothing.

- A family in `ir` is a set of kinds. `ir` refuses a pair of families that name the same members. A comment in `ir`
  called such a pair a single family under a pair of names. The module did not act on that comment. `ALWAYS_CONSUMES`
  and `CONSUMING` asked different questions over a single membership. The pair of names invites a distinction the
  members do not support. A reader can answer either question through the other. A second such pair makes `ir` raise at
  import. The raise reports the pair as a fault rather than as a pair of things the module knows about.

- The prose of the tree says nothing a write-time hook would refuse, and the settling loop sets nothing aside.
  `check_prose` reported faults across the tree, and a checker answered on text that holds no fault. `_A_BARE_PATH` read
  a bare `//` as a path, and read the `/` of a closing markup tag the same way. `_A_SO_CLAUSE` read the pro-verb in
  `does so` and in `did so`, and `do so` was already spared. `_SENTENCE` cut a sentence at the `..` of a range and at an
  ellipsis, and the piece behind the cut opened on a dot that read as a dotted name. `_quoted_runs` ended a JS template
  literal at a backtick inside an interpolation, and the expression behind that backtick reached the checkers as prose.
  `_past_interpolation` counts the braces of an interpolation and takes the scan over both.

- The settling loop writes a fragment of any language the tree holds. `apply_prose.does_hold` refused a JSON note
  outright, and `converge_prose` set such a note aside before a critic read it. The prose of that note reached no
  reader. A note writes a string per line under `_`, and an empty string there ends a paragraph. The reader joined the
  strings with a space, and the paragraphs of a note came out as a single line. `_said_by_note` keeps the paragraphs,
  and `_json_note` writes the strings back. A JSON note takes the round trip `does_hold` asks of a comment and of a
  docstring. `_MARKERS` names `Conf`, and a `.editorconfig` comment keeps its marker through a rewrite.

- A fragment the loop cannot settle is a fault of the gate rather than a line in a queue. `check_prose` reads the
  unsettled prose queue and reports a fragment sitting there. `converge_prose._stored` raises where an entry holds no
  work a pass can take up. Such an entry sat in the queue and no pass selected it, and the run ended with work
  outstanding and nothing to do.

- The prose beside the C obeys the rules the prose beside the generator obeys. The checkers skipped the prose beside the
  C, and faults gathered there. `decoder.h` named a constructor long after that constructor took another name. Beside
  that name, the header counted the character sets the grammar consumes, and no checker reproduces that figure.
  `parser.h` wrote out the `ys_status` values its own fields hold. Those values were wrong. `messages.c` described a
  message table entry for running out of memory. The message table holds no such entry. `messages.h` says the table
  cannot hold one. A host failure is a return value rather than a token with text. `check_documents` reads `src/` and
  `include/`. It takes a comment's own text and blanks the string literals first. A `//` inside a literal is then no
  prose.

- `normalize`'s own prose states no count the gate prints beside it. A comment gave the conflict count before and after
  a step the pipeline does not run. The first half duplicated a figure `check_normalize` reports on a run. The second
  measured a grammar nothing builds. Neither reached the count rule. A comma sat in the place of the noun the numeral
  counts. The narrow rule the generator's prose obeys names a numeral in front of one of the project's nouns.
  Punctuation between the pair puts the text out of the rule's reach.

- The count rule joins the generator's prose before reading it. A document goes through the same way. A formatter wraps
  a comment block and a docstring at the column limit. A count can therefore straddle a wrap. A checker reading a line
  at a time saw no count there. Such counts were in the tree. A fixture total among them counted an older corpus, and
  that corpus was smaller than its replacement. A comment sharing a line with code keeps its marker. The checker reads
  such a comment as a text that ends with its line. The checker does not join that comment to the comment below.

- The `Makefile` obeys the column limit the other sources obey. The formatters skip the `Makefile`. A comment there had
  run past the limit. A gate reported nothing. The limit counts characters rather than bytes. An em-dash is a single
  character and takes more than a single byte.

- `review_input` clears the files it writes. A run clears those files whether it prepares a review or refuses. A short
  output goes into a single file. A long output goes into numbered parts. A run that shortened the output therefore left
  the previous run's parts beside its own. Neither set said which run wrote it. A refused run left the previous run's
  parts whole. A reader could take those parts for the change under review.

- An answer kept against an object's identity is kept by `ir.kept_for`. A pair of modules wrote out the store. Both
  modules checked that the id still named the object the asker meant. Both modules stored the object beside its answer,
  and that stored object keeps the check true. A comment in the second module said the module kept the discipline of the
  first. The docstring of `ir.kept_for` states that discipline.

- `_NAMES_STILL_OWED` answers in both directions, as `_NAMES_THE_TREE_NO_LONGER_HOLDS` already did. A name there excuses
  a citation of something unbuilt. After the pipeline builds that thing, the excuse still stops a check of the citation.
  The list's own comment says a name comes off on that day. A gate reported nothing when that day came.

- `EndMustConsumeAction` matches wherever a parse reaches it. The coverage gate reads the node that way. The node sat
  among the kinds that may refuse, and a reason sat beside the node. That reason said the node answers with the state
  that the way behind the node reaches. Any action answers the same way. `DidConsumeSinceOpenGuard` asks whether a turn
  matched. The node that closes a turn leaves that question open.

- A staged path reaches a reviewer. `review_input` refuses a change holding a path that reaches none. `include/yeast.h`
  was in no slice at all. Neither were the files under `scripts/` and `grammar/`. The build files were out too.
  `README.md` and `CONTRIBUTING.md` were out as well. A change to one of those files therefore reached no reviewer. A
  review reads the text the input hands over. A reviewer cannot see the rest of the change. The input names the fixtures
  rather than diffing them. A corpus change is a pile of files. The bytes of those files say less than a list of the
  files that arrived, went or got a new name.

- `check_dead_code` counts a kind as built where its walk reaches the definition that builds that kind. It gathered the
  calls in a module before the walk had said which definitions a run reaches. A function nothing calls constructs a
  kind. The gate therefore read that kind as live. A transformation parked out of the pipeline leaves such a function
  behind.

- The citation check sees the names the grammar uses. The check wanted a pair of letters before the first hyphen. A
  comment writes arithmetic in the same form as a name. The pair of letters told a name from arithmetic. `c-printable`,
  `s-white` and `l-yaml-stream` have a single letter there. So does `b-break`. The check therefore read past the
  productions the documents and the prose cite. The segments after the first hyphen tell a name from arithmetic. A name
  has a letter in those segments, and `n-1` has none.

- A comment sharing its line with code is prose like any other. `check_documents` read the docstrings and the lines that
  begin with a `#`. A trailing comment therefore reached neither the count rule nor the citation rule. Trailing comments
  broke both rules. Somebody wrote a fixpoint's depth down beside the loop that measures it. A comment beside a field
  named the step that would remove that field. The tree holds no such step. `check_documents` takes a file's comments
  from `tokenize`. That module finds a comment at any position, and does not mistake a `#` inside a string for a
  comment.

- A text file the generators open names its encoding. `ruff` refuses a file open that names no encoding. Python decodes
  such a file by the encoding `LANG` names. `DESIGN.md` holds em-dashes. A generator reading `DESIGN.md` therefore works
  on a developer's UTF-8 machine and raises in a C-locale container. Reads and writes across `generator/` and `scripts/`
  had the same fault. The `ruff` rule also caught the `pathlib` reads that a grep for `open(` misses.

- `CHANGELOG.md` holds its own citations to the tree. An entry names the code a change took away. The citation check
  therefore exempted a changelog citation that named nothing in the tree. An entry can describe a mechanism correctly
  and name it wrongly. Such an entry reads like a correct one. This file wrote `Step`'s fields under a name the type did
  not have. This file called the invariant report by a name that named nothing. This file listed the claims the pipeline
  makes under invariant names that named nothing.

  `_NAMES_THE_TREE_NO_LONGER_HOLDS` declares the names a changelog entry may cite. A citation outside that list fails. A
  declaration the tree holds again is stale and fails too. The citation check asks whether the tree *defines* a name,
  rather than whether the tree writes it. A looser checker counts a name inside a string. The list holds its names as
  strings. That checker reads a declaration in the list as stale.

- Comments and docstrings must cite real names, as the documents must. A comment may name a step, an invariant or a
  production that does not exist. Such a comment reads as though the named thing runs. The citation check read the
  documents and not the code. `check_normalize`'s docstring therefore described a pipeline stage called
  `speculate-folds`. The tree held no such step. The citation check reads a file's comments and docstrings once. The
  count rule reads the same text.

  The citation check accepts a citation in a pair of further cases. A hyphenated name resolves through its underscore
  form. `lower-continuations-into-conflicts` names a step, and `lower_continuations_into_conflicts` names the function
  behind that step. A parameter's values are names too. `block-in` is a context.

- One count rule covers a document. A number is a fault unless the document's list of allowed numbers names it. The
  documents differ in those lists. `DESIGN` may also state a count outright. That count is a numeral in front of a name
  the code measures, and the gate checks it against the code. The gate checked no count written any other way.

  A pair of shapes escaped the count rule. The rule blanked a heading before reading any number in it. A heading
  counting the section beneath it therefore went unchecked. A sentence counted the pipeline rules the grammar satisfies.
  Neither count is measurable. The gate refuses both, and the prose states neither number.

- `check_provisional.py` and `check_determinize.py` are gone. The `Makefile` targets went with them. The mentions in
  `DESIGN.md` went too. Neither could execute a line. `check_determinize` imports `determinize.py`. A change deleted
  that module along with the machinery no phase produces. `check_provisional` calls `normalize.provisional_faults`. That
  function went when somebody cut the pipeline back to the chomping. The deletion and the cut took both gates out of the
  `verify:` list. Neither change removed the files. Both went unrun, and nobody reported a break. Either read as a
  parked gate rather than as one that cannot import.

  A balance net over the provisional actions would replace the deleted gates. `PLAN.md` names that net beside the
  actions it would cover.

- Dead means unreached rather than unmentioned. The word covers a module as well as a name. The module walk starts from
  the modules the `Makefile` runs through `python3`. It also starts from the modules `RUN_FROM_ELSEWHERE` declares with
  a reason. Imports reach the other modules. The definition walk starts from the code a module runs at import. It also
  starts from `main` where something runs that module. A definition reaches the definitions it reads.

  A walk counting mentions made a definition its own witness. A pair of definitions that call one another hold a mention
  apiece. Both therefore read as live. `PEEK_OUTPUT`, `WRITES_NOTHING` and the span algebra's `_EVERY_CHARACTER` sat
  unread behind that mutual mention. The change deleted those names. A transformation kept for a reason keeps the
  helpers it calls.

  The walk starts from the gates the `Makefile` runs and keeps no list beside the `Makefile`. The walk therefore reaches
  a gate the day somebody adds it to the `Makefile`. A gate somebody takes out of the `Makefile` is dead that same day.
  A list kept by hand catches the added gate and misses the removed gate.

- A fixture may say how much input the parse takes before reaching the rule. It writes `p=N` in the name. The `n`, `c`
  and `t` sit beside `p=N`. The `r` and `i` sit there too. A test run takes those first `N` characters before it enters
  the rule, and drops the token they built. Those characters then set the line and the column. Those characters also set
  the text a look-behind finds behind the parse. A parse reaches a rule mid-line after some leading text. The fixture
  holds that leading text rather than a position its name asserts. A parse enters a compact collection past its `-`.
  `s-l+block-indented`'s fixtures therefore name `p=3` beside their `n=2`, and hold the spaces and the dash that put a
  parse there. A fixture for a rule the parse reaches mid-line names its `p=N`.

- Grammar normalization is an ordered pipeline of transformations. A transformation rewrites a grammar and preserves its
  semantics. The pipeline takes the hand-authored grammar toward the canonical form a state machine falls out of. In
  that form a terminal is a character set and a run is a repetition of a set. The pipeline is a sequence of phases. A
  phase owns a single invariant and adds steps until the invariant counts no violation. The gate enforces the invariant
  from the end of a phase. The law "none stays none" then makes a later step keep the invariant at none. A phase lands
  as a checkpoint once the corpus runs green on it.

  An invariant is a count and not a yes-or-no. Its test gives back a list of the places the grammar breaks that
  invariant. The count is the length of that list. A `Step` declares what it does to an invariant. A step takes the
  count to none. A step may instead lower the count, or establish the invariant where the test could not ask before.
  `Step` documents the fields those declarations go in. `invariant_faults` refuses a step that breaks the rules below. A
  count does not rise. A settling step leaves none, and a count at none stays there. `lapses` is the licence to break an
  invariant. A step writes `lapses` as `{invariant: reason}`. A lapse naming an invariant no step names is a stale
  declaration and a fault. A lapse naming an invariant its step does not break is a fault too. `untested_steps` counts
  the steps whose promise no invariant checks. A step must name an invariant. A step naming none says in `untestable`
  why it can have none. `unsettled_invariants` counts the invariants the final grammar still breaks. The count holds
  under any settlement the steps reach.

  A `Step` with no transform is a **claim**. It changes nothing. The step says that its invariants read none at that
  point in the pipeline. A claim records a property the pipeline inherits rather than makes. The law checks that
  property on a step behind a claim. The law checks a settled invariant on a step the same way. A writer may name such a
  property on the step that runs next. A reader then takes that step to establish the property. That reader then blames
  the step for a later break. A step naming an invariant **the grammar already held at none** is therefore itself a
  fault. The fix for such a step is a claim. `every-conditional-way-is-gated` became a claim at the door.
  `no-production-reaches-itself-unconsumed` became a claim once the optionals are ways.
  `accepted-and-gated-charsets-are-equal` became a claim where the consumed sets agree. `every-path-reaches-a-leaf-way`
  became a claim for the leaf paths.

  `specialize` covers the parameters the grammar sets by matching. Those are the chomping `t` and the block scalar's
  indentation mode `i`. An indicator sets such a parameter. A production further on reads the parameter through the
  environment. A read of `t` or `i` therefore means nothing until a reader knows the caller. `lift-setters` inverts a
  setter into a `(case)` on its parameter. The case matches the condition for a given value. A production may hold such
  a parameter as a local out-parameter. `lift-setters` turns that production into an ordered choice over the parameter's
  values. The block scalar becomes a choice over strip, keep and clip, and over given and detected alike. A branch fixes
  the parameter to a literal. The branch hands that literal to the setter and the reader both.

  `monomorphize` then specializes the finite parameters away. Those are the context `c`, the lexical `t` and `i`, and
  the resume policy `r`. The step copies a production it reaches once per combination of those values. A copy's `(case)`
  and `(flip)` on them evaluate to that copy's values. The step fixes the values into the copy's name, as
  `ns-plain-char_c_flow-in`. A call passes none of them. The root has a copy per resume policy, and those copies are the
  machine's start states. The step follows references from the root's copies. The combinations the step makes are
  therefore the combinations that occur. A value at its default, the no-resume `r`, is not in the name. The root
  therefore stays `l-yeast-stream`, and the recovery re-enters the copy the resume policy names. The integers `n`, `m`
  and `f` stay parameters. The step rests on a rule the grammar keeps. The grammar only switches on a finite parameter,
  and computes no value from it. An implicit key's commit softens by context. The grammar therefore says so in a
  `(case) c`. A key that will not parse is simply not this key. The key branches of that `(case)` are the bare item. The
  `else` of that `(case)` is the commit. The parser's `(commit)` is the same hard cut throughout. `(case)` grew that
  `else` for the commit. Past `monomorphize` the grammar holds no `t` and no `i`.

  `character-sets` covers the character questions. A rule may write a set of characters in the forms below. A set is a
  character, a range, or a union of such sets. A set may also be a base with exclusions, a reference, or the item a
  lookaround peeks. Such a form reduces to the bit the parser tests. A difference is such a set where both its sides are
  sets. A difference in `ns-double-char`, in `ns-single-char` or in a `ns-tag-char` copy had a side that was not a set.
  A difference takes whitespace or an indicator out of something that is an escape *or* a character. Such an escape is a
  `\` and a character after it, or a `''`, or a `%` and a pair of hex digits. The subtraction therefore ranged over a
  language rather than a set, and a bit could say no such subtraction. `distribute-differences` takes a difference into
  the ways it subtracts from. The ways keep their order. A run of single characters takes the subtraction once, over the
  union they already form. A way that takes a pair of characters or more keeps its whole language. A subtracted set
  takes a single character. That set therefore reaches only a match of that length. After `distribute-differences`, a
  difference sits between a pair of sets. `every-difference-is-between-character-sets` says so. `lower-char-sets` then
  folds the differences away. `no-diff-nodes` then reads none. `lower-char-sets` says a set as the sorted disjoint
  codepoint intervals it denotes. After `lower-char-sets`, a question about a character is therefore a `CharSet` or a
  literal. `lower-char-sets` takes a maximal set rather than the sets inside it. The intervals of a union belong to the
  union. The step leaves in place a reference that a match takes. That reference is the caller's hold on the production
  that says the set. Inside a lookaround `lower-char-sets` reads through the reference instead. A peek holds the
  question a peek asks rather than the shape that names it. `lower-char-sets` reads through an annotation on the
  production a peek names. A probe emits nothing and gives back what it read. The code around `c-comment`'s `#` is
  therefore dead inside a probe. The peek at that `#` was the last character question the phase left in place. The
  grammar the phase hands on holds further peeks. The later steps make those peeks as copies. A peek there is a
  `CharSet`. The `(exclude)` guards are the last lookarounds. They ask about a line rather than about a character. The
  phase follows the specialization. A set the context picks denotes nothing until a reader knows the caller. The phase
  gives a set a single canonical form. A merge of productions depends on that canonical form. A pair of productions
  denoting the same characters differently are structurally unequal and do not merge.

  `drop-f` covers the block scalar's leading-empty floor. `clear-f` gives the value an end. `s-indent-floor` reads the
  value. That production clears the value where it returns. The production that writes the value clears nothing. The
  parse measures the floor deep inside the leading empties, and hands it up to the caller that asks. `read-global-f`
  then takes the declaration off a production and the argument off a call. A read becomes a `GlobalValue`. The generic
  walker takes a `ParamValue` as a value. The walker does not visit a value a field holds directly. A read in such a
  field stays hidden from the walker. `s-indent-floor`'s `Le(f, n)` is such a read. `read-global-f` walks the fields
  when it counts the reads and when it rewrites them. The value does not nest. The step may therefore drop the argument.
  The interpreter checks the nesting. A `(set)` puts the value on a stack the global keeps. A `(clear)` takes it off. A
  slot sits beside the stack. The interpreter counts the reads where the stack and the slot differ. The count runs over
  the whole corpus. The gate fails when the interpreter counts such a read. Where a value does nest, the interpreter
  reports the reads the same way.

  `drop-m` covers the detected indent. `clear-m` and `read-global-m` do for `m` the job `clear-f` and `read-global-f` do
  for `f`. A step of such a pair reads its global once over a region something else can write in. The pair of steps
  rests on that single read. The block header measures the detected indent. A block scalar reads the detected indent a
  construct at a time. A block collection did read the value on the turns of its loop. A single slot cannot hold a value
  read that way. The loop entered a collection or a block scalar. Such a construct detected an indent of its own between
  the turns. The grammar allows that. A collection enters its entries at the indentation the first entry established,
  and the interpreter's count finds no read there. `BindTree` maintains the stack beside the slot too. `BindTree` counts
  a write in any form the write takes. A block header's indicator sets the detected indent through such a form. The
  count had missed a global written that way.

  `drop-n` covers the indentation. The indentation is not among the parse's own values. A nested collection measures the
  entries against an indentation of its own. That indentation therefore goes on the parse's stack rather than into a
  slot. `push-indents` puts a push before a call and a pop behind it. The step does that where the call measures against
  an indentation other than the level in force. Both halves sit in a single way of a single production. The way names
  the level. `read-indents` then takes the parameter off the declarations, the calls and the reads. That leaves the
  stack to answer. The parameter stays beside the pushes while they go in. The interpreter compares the parameter
  against the stack at a read of `n` over the whole corpus, and only then does the parameter go. Skew a push by a level
  and fixtures refuse. The agreement of parameter and stack is therefore a check rather than a coincidence.

  `hold-established-indents` goes first. The step handles the indentation a call hands back, rather than the indentation
  a call enters under. A block scalar cannot know the content indentation until the parse reads the first content line.
  That line measures the content indentation. The measured value travels back out through the calls that passed the
  indentation parameter along unchanged. The measurement is a write whose readers are a production away. A step that
  removed the indentation parameter would silently break that write. A local check cannot see the break. The step
  inlines the chain until the write and its reader are in a single way. The write is then the push that the same way
  ends by popping. With `hold-established-indents` in front, `push-indents` has a single rule instead of a pair.

  The corpus found pushes at a wrong level. A pop that names the level re-evaluates the level's expression when the pop
  runs. `<column>` or `n+1` means something else by then. A pop therefore takes the entry on top. The stack's own kinds
  refuse a bad pairing. A push computing its level also does not read the indentation in force. The level and the
  indentation in force differ where the level is the parameter itself. That is the same exemption the arguments of a
  call already had.

  `lower-repetitions` covers the empties, and `lower-optionals` opens `lower-repetitions`. `x?` becomes `x | <empty>`.
  The empty way then sits beside the way that reads `x`. The empty way hides inside no node. The interpreter checks that
  the rewrite matches the input `x?` matched. An `OptTree` tries its item with the continuation behind that item. The
  `OptTree` rewinds where that fails, and takes the continuation without the item. The alternation tries the same ways
  in the same order. The parse therefore has to know nothing about the continuation.

  A run over a character class is a consume and not a way, and the interpreter draws that line where the grammar does.
  Such a run is single-outcome by construction. A character off its own set follows the run. The parse therefore takes
  the run whole and judges it whole. `s-indent-le` needs a run taken whole. That rule takes the maximal run and compares
  its length against `n`. A parse falling back to a shorter run would pass an over-indented line as unindented.
  `span-consumes` writes a run as a consume. `x*` is a `ConsumeSpanAction`, and `x+` is the character with that span
  behind it. A counted consume goes the same way. `x{n}` over a character class is a run of up to `n` characters of the
  set. The guard asks whether the run reached `n`. The parse works out a count. That count becomes the ways it denotes.
  After `span-consumes`, `RepTree` is gone from the grammar.

  A repetition of a way becomes the ways it denotes. `lower-runs` writes the `(***)` form and the `(+++)` form as such
  ways. The ways are a turn, a recursion taking the turns behind it, and a settled region around the turns after the
  first. A turn takes a character. A turn taking none is a turn the run did not take. The region settles the turns it
  holds. A failure past the close of the region therefore gives the whole run up. The parse does not take fewer turns.
  The `(***)` form offers the turn not taken as a way of its own. The `(+++)` form offers no such way. The interpreter's
  `(***)` arm and `(+++)` arm differed in that offer. Once `lower-runs` has run, `no-star-or-plus-nodes` finds no run
  left in the grammar.

  The ways are an ordered choice. A settled region, rather than the choice, holds a parse to the longest run. The guard
  a turn holds ends the run. A gate on the choice does not end the run. The grammar says outright that a turn taking no
  character is no turn. The grammar leaves no such test to a comparison of positions inside the interpreter's loop. The
  grammar repeats a way at no site. A character class comes under the maximal run or under the counted run.

  A function that dispatches on node kind raises on a kind it has not heard of. The rule covers more than the questions
  that answer yes or no. A reader had taken the rule as being about the booleans. A wrong `False` from such a question
  turns a consume into a way. The rule covers any answer a default arm gives. `validate_grammar.consumed` walked into
  the children of a kind it did not name. A new way of taking a character would therefore have yielded nothing of its
  own. The characters that way took would have passed the token-coverage check unannotated. `check_grammar_docs.emitted`
  did the same for a new way of emitting. Such a way would have read as documented while saying nothing. `chars.denote`
  answered "no characters" where it meant "no answer". `grammar2decoder.defined` answered "defines no character". The
  answer would have left a new character out of the decoder tables. The drift gate sees a table change. That gate misses
  a character a table lacked from the start. `check_decoder` and the character-run invariant kept a list apiece of the
  kinds that repeat. Such a list goes stale when somebody writes a run in a new form. The kinds on such a list are gone,
  and the list then names none.

  The functions that dispatch on node kind share `ir.KINDS`. `_NOT_ONE_CHAR` holds some of the kinds. `is_one_char`
  covers a further group by its own standard. A kind outside `_NOT_ONE_CHAR` and outside `is_one_char` raises at the
  first question a caller asks of it. `ir.repeated` names the part a node takes again and again, and `ir.CONSUMING` says
  which kinds take characters themselves. A kind has a name once in `ir`, and a site does not list the kinds again. The
  change sharpened the run checker. That checker found counted repetitions over a character class. The grammar did not
  write those repetitions as spans.

  `mint-consuming-and-residue` names the ways of a production that may match empty. `<name>_reads` is for the ways that
  take a character. `<name>_empty` is for the ways that take none. The production becomes the choice between them. The
  split leaves the grammar wider. A caller entering such a production was choosing blind. A gate cannot rest on a
  character while both answers live under a single name. The residue gets a name rather than an inlined tree. A reader
  then works nothing out bottom-up. The step splits the parts of a body along the ways their names already give.
  `every-empty-match-is-a-way` then reports no fault.

  The split gives the same match in the same order. A sequence's parts already offer its ways. `a b` consuming is
  `a_reads b` and then `a_empty b_reads`. The pair of ways enumerates in the same order as `a b`. An alternation in the
  grammar puts the consuming way ahead of the empty way. A shape that cannot tell its consuming way from its empty way
  takes another form instead. A possessive consume takes nothing exactly where its set is not there. Its empty way is
  therefore a negative peek at that set. The peek looks at a single character. A gate already asks whether that
  character is in the set. A pair of ways peeking the same set are a single production once the sweep merges them. A
  counted repetition may have a count the parse works out. Such a repetition matches nothing where that count is not
  positive. Its ways are therefore told apart by the count. The consuming way says the turn it takes. The count that
  admitted the way does not decide that turn. `s-indent` takes that form where its indentation count is not positive. A
  run over an item that may take nothing ends on a turn that takes none. The interpreter keeps that turn once. A check
  confirms that such a turn leaves nothing behind in this grammar. The consuming way is therefore the consuming turns,
  and the empty way is the turn that took none. A commit is the error where its item cannot match. A split of the commit
  in place would turn a failure of the consuming way into that error. That failure is instead a step on the way to the
  empty way. `A (commit m: X)` and `(commit m: A X)` are the same match where `A` takes no character and cannot fail.
  The split therefore lifts the commit over the choice. A single message scope sits around both ways.

  Somebody added fixtures rather than crediting a split name to its base. The coverage gate would have held a name
  covered by its base, as that gate does for a monomorphic copy. A base's coverage cannot say which way an input took.
  The added fixtures therefore make the corpus take both ways of a split name. The corpus takes an empty and a non-empty
  single-quoted scalar in a block key and in flow. It takes a double-quoted scalar that is empty, and a double-quoted
  scalar holding a space. It takes the chomped last line at end of stream under a chomping, and a block header at end of
  file. It takes a kept block scalar with and without trailing empty lines, and a folded line at the leading-empty
  floor. It takes an error recovered behind an indented line, and an error recovered at end of input. There the recovery
  has nothing to give up.

  `distribute-residues` writes the choice between a callee's consuming way and its empty way at the call site. It writes
  `A ::= F (X_reads | X_empty)` at a call site of `X`. After the step, `no-call-enters-both-ways` reports no call. The
  grammar also drops the names the step leaves unused. The choice sits behind a single name. A caller still enters
  without knowing whether the callee takes anything, and a gate has no place to go. The step does not split the way
  around the call. `A ::= F X_reads | F` would run `F` twice. An alternation inside the sequence runs `F` once. The step
  runs a single pass. A production the step rewrites holds a call to the consuming way and a call to the empty way. The
  root and the recovery stay whole, and the calls between them fall outside the step. A parse enters the root and the
  recovery by name rather than through a choice. A split of either production would leave its empty way a choice on
  nothing at all.

  The coverage gate reads `_reads` and `_empty` as minted-helper suffixes. A split form therefore credits the base it
  came from. A monomorphic copy credits its base the same way. The gate's note describes such a form as "the base
  restricted to the matches that consume". The credit is the floor rather than the ceiling. The corpus reaches both ways
  of a split production in its own right. The gate covers a base like `l-recover-entry`. A resume policy declines there,
  and the base matches at no site. A fixture could not reach that base before the split either.

  `dissolve-residues` writes a production taking no character into the call sites that enter it. `e-node` goes into a
  call site that enters it. `only-root-empties` goes to none. The productions still matching empty are the root and the
  recovery under a resume policy. The split tells a consuming way from an empty way. A production still matching empty
  took nothing throughout. Such a production is a residue a split named, or a production holding actions and no more. A
  name is worth having where it marks a decision. A way that consumes nothing and ends where it began marks no decision.
  A residue holds calls of other residues. The step writes out the calls inside a residue before writing that residue
  into its call sites. A residue reaching itself would recurse without end. The grammar holds no such residue.

  The grammar ends the phase about as wide as it began. The grammar went wider in between. A caller chooses nothing
  about entering something that may take nothing. An empty match becomes a way the caller holds. A character can decide
  that way.

  A wrapper around a node becomes a pair of actions around that node. A kind takes a step of its own. An alternative is
  `gate actions... [P1 actions...] [P2]`. It has a place for an action. It has no place for a node enclosing a call. A
  `(token)` around a call is an action that must run where the call returns. That place is the continuation. The steps
  therefore remove the wrappers before a later step splits a way into a call and a continuation. The steps run in order.
  A step with less state runs ahead of a step with more. The last step moves the remaining wrappers. `lower-wraps`
  writes `Wrap(begin, end, x)` as `Emit(begin) x Emit(end)`. `lower-windows` writes a `(max)` as
  `OpenWindow ... CloseWindow`. Windows do not nest. The outermost window applies. The pair counts the opens in force.
  The wrapper asked whether something had already set a ceiling. `lower-commits` writes a `(commit)` as
  `PushMessage ... PopMessage`. A commit is the error the parse reports where the item under the commit stops short of
  its own end. The text of that error is the error message. The push records that message, and the pop marks the end of
  the item reached. `lower-tokens` writes a `(token)` as `PushCode ... PopCode`. Both halves cut the run. The push sets
  the code the characters between them take. The pop restores the code the push displaced.

  A wrapper keeps the displaced value in a Python local, in the frame of the running match. A lowering puts that value
  on a stack. The stack becomes the parse's own state. A frame is gone once the split makes a call and a continuation. A
  stack survives the split. The pushes and pops of a lowering therefore balance while the lowering runs. A pop takes
  back the entry on top, rather than the entry its own push put there.

  A wrapper pairs its markers by construction. `ir.Wrap` is a single node holding a `begin` and its `end`. A `begin`
  inside that node cannot lose its `end`. Unwrapping gives up that pairing, and a gate then checks it.
  `every-scope-closes-on-its-own-way` is that gate. The gate reads no fault over a stage before a wrapper comes off. A
  step that takes a wrapper off names the gate. A scope opened on a way closes on that way. The ways of a choice agree
  on the scopes they leave open. A run's turn leaves none open, and a second turn opens the scope again. A probe gives a
  lookaround back. The items inside a lookaround therefore touch no scope. The gate covers the pairs a normalized
  grammar holds. A `PushIndentAction` closes on a `PopIndentAction`. A `PushCodeAction` closes on a `PopCodeAction`. A
  `PushMessageAction` closes on a `PopMessageAction`. An `OpenWindowAction` closes on a `CloseWindowAction`. A
  `PushBackTrackAction` closes on a `PopBackTrackAction`. A `StartMustConsumeAction` closes on an
  `EndMustConsumeAction`. The indent pair had gone unchecked. The gate is not vacuous. Drop the pops from a production,
  and the gate reports the scope left open. The gate also reports the ways that disagree about that scope.

  A pair of scalar markers may cross productions by design. `b-chomped-last` emits `end-scalar` for a `begin-scalar`
  opened elsewhere. A per-way rule is therefore the wrong rule for them. `check_markers` proves the markers' balance
  with a fixpoint over the callers. `check_markers` reads the grammar as authored and does not follow the pipeline.
  `lower-wraps` loses nothing. The pair of markers comes out adjacent in a single way of a single production, and the
  sweep has nothing to separate them. `lower-wraps` leaves the balance unproven for the later phases. The split into a
  call and a continuation can put a `begin` in a production and its `end` in another. That split needs a check over the
  markers.

  A pair of faults came from moving the state out of the frames. In both faults an abandoned parse left a scope in
  place. The wrapper's frame had taken that scope away. An in-grammar `(recover)` put back the `(max)` ceiling of the
  rule it belongs to. It did not put back the count of opens beside that ceiling. The recovery therefore restored a
  window while the count still held that window open. A key past the window's bound then bounded no later key. A cut
  could unwind to the stream's own level. A raise then skipped the closes of the cut's committed regions, and those
  regions stayed on the emitter. A parse that matches refuses to return with a window or a region open. This refusal
  catches either fault where the parse returns. The refusal finds no open window or region in the grammar as authored.
  The refusal named the open regions as soon as `lower-commits` landed. The fixture behind the refusal is a stream whose
  first implicit key overruns and whose second implicit key must overrun again.

  The phase hands on a grammar without `(wrap)`, `(max)` or `(commit)`. `(token)` is gone too. They were the code, the
  message and the window pairs. The indent pair went with them. The markers sit beside those pairs. A scope closes on
  the way it opens. The `(recover)` stay. A recovery is a handler and not a scope. A handler has no close whose position
  means anything. Its home is the edge an alternative rides.

  The parse decides at a choice. A machine decides in a state. `one-step-per-item` therefore takes the tree apart. An
  item in a way names the action the machine takes at the item's position. `no-item-holds-a-match` counts the items
  holding a match in the tree the phase begins with. That count covers the choices and the runs. It also covers the
  recoveries and a single binding. A wrapper holds a match inside it. The machine has no state for a position inside a
  wrapper. `lift-choices` settles the choices. That step takes the count under `every-choice-is-a-body` down to none. A
  choice inside a way gets a production of its own, and the way holds the call. `lift-choices` mints a production rather
  than distributing the sequence. Distributing is the other way out of a sequence. Distributing `a (x | y) b` as
  `a x b | a y b` runs `a` twice wherever `a` takes a character or pushes an entry. It also copies the productions `b`
  calls. The call costs a push and duplicates nothing. The grammar widens, and a later sweep merges the duplicate
  productions `lift-choices` mints. The phase's own count falls by the choices `lift-choices` lifted.

  The grammar wrote a choice between consuming and taking nothing at the call site. A gate had no place there. A phase
  turns that choice into a production a call reaches. `no-call-enters-both-ways`, `only-root-empties` and
  `every-empty-match-is-a-way` rise again. A rise is a declared lapse. The declaration says the canonical form has a
  place to put the gate. A gate accepts such a rise on arrival. `F (X_reads | X_empty)` is a call and then a decision,
  and a decision after a call is a production.

  The lifting also turned up a cycle. `no-production-reaches-itself-unconsumed` exempted the stream and the recovery by
  name. The exemption read a recovery as a landing the driver picks rather than a call the grammar makes. That is false
  under a resuming policy. `l-recover` is `l-unparsed` and then the stream again. `l-recover` and the stream are
  mutually recursive by design, and a resumed document can then fail again without a second mechanism. A production
  minted out of the stream's own body lands on that cycle and inherits no exemption. `l-unparsed` holds the pair apart.
  It takes nothing where the next line is a document boundary or the input has ended. There the stream consumes the
  `---` or `...` itself, or `<end-of-stream>` answers. A second recovery therefore costs a character. The exemption
  belongs to the cycle rather than to a name. The invariant cuts a path at a production a parse enters by name. The
  invariant then asks about reachability. A production that still reaches itself is a fault of the grammar.

  The other shares follow, and the phase's count reaches none. `lift-runs` gives a run inside a way a production of its
  own. That production is the machine's loop state, and the machine jumps back to the top of it. The step settles
  `every-run-is-a-body` and raises the same counts again. A run of none or more is a choice between taking a turn and
  taking none. Naming a run settles nothing about the run itself. The run stays the possessive consume the grammar
  wrote. The gates ask whether the run takes another turn. `lift-recoveries` does the same for the recoveries. A handler
  waits in the recovery's production until an edge turns up to ride. An alternative holds such an edge. A recovery stays
  a production of its own until the grammar holds the alternatives. `lower-bind` takes the last binding. The block
  header holds it as `Bind(ns-dec-digit, m, atoi(match))`. The step writes that binding as the match and the write that
  follows it. The interpreter treats the binding and that pair alike. A binding whose condition has matched does the
  work of a `SetVarAction`. The binding undoes the write when the next item fails. The condition can then try its next
  way.

  `no-item-holds-a-match` reads no fault over the whole grammar. An item in a way is a call or an action. An item is
  also a guard or a character taken. An item may be nothing at all. The count itself had a fault. The count did not know
  that a recovery could *be* a body. The productions `lift-recoveries` minted therefore read as faults of their own. A
  single list names the forms a body may take. A body is a choice of ways or a run of a way. A body is a way under a
  handler, or a way. The count and the lifting both read the list. The list leaves out a binding. A body holding a
  binding hides a write behind a match.

  The exclusions are the phase's other half. `every-exclusion-is-bounded` fails at the start of the phase. It counts the
  exclusions that ask a production rather than a bounded question. An `(exclude)` is a guard the parse holds and tests
  at a start of line while the guard is in force. The question therefore has to be checkable at the position the parse
  asks it. An exclusion may ask by name, as `c-forbidden` does. A guard would have to run a parse to answer such a
  question. `bound-exclusions` writes the question as the characters it denotes. At a line start those characters are
  `---` or `...`. A break or a space or a tab or the end of the input follows. The step writes that question as a pair
  of `LiteralPeekGuard`s with a follow class between them. The parser's own fill already guarantees the input that pair
  peeks at. The question means what a peek means. The walk reads through a name, and an annotation is dead. The follow
  test distributes over the `---` run and the `...` run. A probe gives a question back. A duplicated question is
  therefore a test rather than a match.

  The count stops short of none. The remainder is the phase's declared debt rather than its oversight. Those exclusions
  also ask whether the line is at this indentation with content. Such an indentation is a run of spaces with no bound.
  The exclusion is a condition on a line start, rather than a question about the characters behind it. The exclusion
  lands where the block-structure work makes a line start a decision the grammar writes.

  `mint-continuations` covers the call. An edge of the machine is a push and a jump. The push records where to come back
  to. The jump enters the callee. A way therefore holds its actions, then a call, then the production that continues it.
  `a P1 b P2 c` becomes `a` and the call `P1`. A production of its own then holds `b P2 c`. The new production splits
  the same way until nothing comes past a call. `a-way-is-actions-a-call-and-a-continuation` reads the ways that act
  past their call. The invariant also reads the ways that hand control on more than twice. `mint-continuations` splits
  those ways. The split widens the grammar. Binarization takes no step of its own, and a binary grammar falls out with
  the continuations.

  The split breaks the checker of the scope net. The pairs that checker proves still hold.
  `every-scope-closes-on-its-own-way` was a per-way rule, and a way that hands control on is half of a path. The
  consuming half of `s-indent-le` keeps its `PushCodeAction`. The matching `PopCodeAction` rides into the continuation.
  An earlier change moved the pairs onto the parse's own stack for exactly that cut. The phase therefore re-derives the
  invariant rather than letting the invariant lapse. The new name is `every-scope-closes-on-the-path-that-opens-it`. The
  invariant finds no fault at a stage before the split, or at a stage after it.

  The scope checker treats a call as a **unit**. The unit describes what a call does relative to its own entry. Both of
  a way's calls are on the path. The call a way comes back from counts as much as the call it continues at. A step
  contributes the scopes taken off and the scopes left open. A push, a call, and the pop that follows therefore balance
  the depth the call reaches. The balance lets `c-flow-sequence` open a message scope, recurse flow content inside that
  scope, and close the scope on the way out. A walk into that recursion instead reads the recursion as a circle. A turn
  of that circle enters a scope deeper than the turn before. `[ [a] ]` then becomes a fault.

  The checker does its work on the circles of calls. A production that reaches itself has an answer. A turn round that
  production's circle leaves the scopes untouched. The walk reads the answers a circle of calls at a time. The circles
  are Tarjan's components. The walk reads a circle after the circles it calls. The walk rounds a circle until the
  answers stop moving. The growth of an answer says whether a circle comes back level. The bound on that growth counts
  the scope actions the circle holds, and the scopes its outside calls leave. An answer past that bound has gone round
  the circle more times than the circle has pairs to open. A round budget in place of the bound reads "has not finished
  yet" as "is at fault". Such a budget blames the productions the walk happened to reach first. The same grammar then
  gets differing answers on successive runs.

  A scope summary reads the work a production does. The path the parse takes is a separate question. A summary treats
  the pair of calls a way makes alike. A path tells the pair apart. A way hands control on to a place the path continues
  at. The way pushes nothing to come back to. A loop of hand-offs therefore puts the parse genuinely back at its earlier
  position. Round the loop once and the scope the turn left is open. Round it again and a second scope is open, and no
  bound applies. A loop must therefore be level. A walk of the hand-offs decides whether a loop is level. A summary says
  nothing about the scopes a turn round a loop leaves open. A walk covers a circle of hand-offs. A loop lies inside such
  a circle. A walk from any production of that circle reaches a loop in it, as an edge back to a place already visited.
  The circles that hold a loop come back level.

  The rule says nothing about the point where a call *reaches* a production. A pair of paths may arrive at a production
  having opened different things. Both paths may balance in their own right. A rule refusing such a pair of paths
  governs where a call may reach a production. That rule says nothing about scopes. The refusing rule reported faults
  where there were none.

  Neither walk covers a recovery. The interpreter unwinds to the recovery, reads the stack it finds, and takes off the
  entries the abandoned parse left. The interpreter therefore puts back the scopes the recovery started under, and no
  grammar action balances those scopes. A check could require a recovery to balance the pairs a walk reads. The
  interpreter breaks that requirement at a recovery.

  `build-alternatives` writes a body in the terms the machine runs. `every-body-is-a-choice-a-run-or-a-set` checks the
  bodies it writes. A terminal is a set of characters. A loop is no such body. The lowering step writes a repetition as
  ways. A loop jumps back to the top of a production. That production is an ordinary one, and an ordinary guard stops
  the loop. A remaining body is an ordered list of alternatives. A way holds a gate to enter on and the actions the way
  performs. The way then hands control to a call, and names where it continues when that call returns. The way also
  names the recovery that goes with its push. `build-alternatives` writes the remaining bodies of the grammar in that
  form.

  `build-alternatives` changes the form and not the meaning. The interpreter already ran the canonical form. An
  alternative was the sequence the tree wrote. The gate's peek was a lookahead. The recovery was the `(recover)` scope
  over the call it protects. The interpreter therefore learned nothing about executing an alternative. The step also
  leaves the gates empty. The parse enters a way on a question about the character in front of it. The hoist answers
  that question. Before the hoist, the parse tries the alternatives in order. The tree said that too.

  The change taught the checkers that walk a body to refuse rather than guess. The flatness count and the nullability
  walk both walk a body. The split of a production into its ways walks a body. The left-corner walk of the cycle check
  walks a body. A choice says its ways as `alternatives` in the machine form, and as `items` in the tree form. An
  alternative says its parts by name. A sequence says its parts in a row. A single checker therefore reads both forms,
  and the walks ask that checker. An `AltTree` reached the helper written for the machine's form. That helper handed the
  tree back as its single way. That is a walk that does not end, rather than an answer that is wrong. The rewrite
  removed the per-way scope walk. The path check reads the same facts.

  `gate-the-ways` gates the ways of a choice. A machine that does not backtrack takes a way by looking at the character
  in front of it. `every-way-gated` therefore counts the ways a parse would have to try and give back. Such ways are the
  alternatives that make a decision. The count leaves out an exempt alternative. The last way of a choice is exempt as
  the unconditional fallthrough. A body with a single way makes no decision. `gate-hoist` reduces the count, and the
  step reads no analysis. A way may open on an action that takes a character from a set. The parse enters such a way on
  a character of that set. `gate-hoist` therefore moves the set into the gate. A `ConsumeCharAction` takes its place,
  and consumes the character the gate has already found. The step rewrites an alternative without asking whether a
  choice needs its ways told apart. A way whose first action is a set fails where the set is not there. The gate leaves
  that failure unchanged. `gate-hoist` takes the ungated ways that begin with a set.

  Lowering leaves the bodies of the kinds below unlowered. A body may begin with a call. The grammar computes no entry
  set for such a call. A body may begin with an action that touches no input. A way's gate may look past such an action.
  A body may begin with a guard. A way's gate holds such a guard beside its peek rather than inside the peek.

  The change therefore computes the entry sets. An entry set is `{name: spans}`. The spans hold the characters a parse
  of a production can start on. A least fixed point over the calls computes the sets. The fixpoint errs wide where it
  errs at all. A gate too wide costs a parse that fails where the gate could have refused it. A gate too narrow loses a
  parse that should have matched. `gate-hoist-call` then enters a call-leading way on the entry set of its callee. The
  way cannot match unless the callee does. The set therefore refuses a character the way would have failed on inside the
  callee. `every-way-gated` falls between `gate-hoist` and `gate-hoist-call`.

  The step holds a pair of side conditions. The corpus found the second of them, and the argument did not. The step
  refuses a callee that can take nothing. The way then passes through that callee to the actions behind. The step also
  refuses a callee that answers a character it cannot start on with an *error* rather than a refusal. Such a callee
  opens a `(commit)` before it has to take a character. The failure then becomes the error that region names. A gate
  takes that way out of the parse. The choice then goes on to a way that matches. Without the gate, the parse stops
  there. That is a different language and not a narrower one. The suite showed the difference in `2G84/00`. The suite
  rejects that case, and libyeast began accepting it.

- The change audited the boolean names across the generator. That covers a function, a variable and a parameter alike. A
  fixpoint's `changed` and `moved` are `did_change` and `did_move`. A match's `matched` is `did_match`. The law renames
  `licensed` and `broken` to `is_licensed` and `is_broken`. `settled` becomes `is_settled`. `refuses_softly` and
  `establishes` are `does_refuse_softly` and `does_establish`. The flags `bisect` and `check` are `does_bisect` and
  `is_checking`. `apply` is `is_applying`. A CPS continuation whose bool means "the rest of the parse matched" keeps its
  name. It is protocol rather than a predicate.

  **The pipeline prints the meter again.** `every-decision-goes-on-a-character` counts a multi-way choice whose gates do
  not tell its ways apart. A choice with an ungated way in front of another way is such a choice. A choice with a pair
  of ways admitting the same character is another. The last way is no fault. An empty gate there is the fallthrough. The
  parse takes that way where the ways in front of it did not fire. The meter reads a choice at the step that first makes
  its alternatives. The gates there are still empty. The hoists then take out the choices they can reach.

  The rest of the gating goes with it. `hoist-past-actions` enters a way on the character the first question asks, under
  any actions in front. The parse tests a gate before entering the way, and an action touches no input. The same
  character therefore chooses either way, and a way that fails the test rewinds the actions it did. The walk goes
  through a call that can take nothing as well. The parse then enters the way on the characters that call can start on
  *and* the characters behind it. The walk stops at a commit. `gate-hoist-call` refuses to go a call deeper. A `(cut)`,
  an `(error)` or a region opened before the question makes failing there an error rather than a refusal. A gate that
  keeps the parse out of the way turns that error into a way not taken. `hoist-guards` puts a leading `EndOfStreamGuard`
  or look-behind in the gate beside the peek. Both are questions the machine can put at that position. `every-way-gated`
  says so too. The parse can enter a way at the end of the input. Such a way has no character to ask about. Together the
  hoists take the ungated ways they can reach. **The text below accounts for the ways that survive.**

  The text accounts for the ungated ways rather than leaving them. They call a production that answers a wrong character
  with an error, or a production that can start on nothing. They sit behind a commit. Such a way also passes through the
  ways it holds, and no character then has to be in front at all. A policy is a question about *when a failure is an
  error*. Determinizing answers that. A hoist answers something else.

  **The meter measures disjointness and says nothing about safety.** A character picks a way. That choice does not make
  the way safe. A gate can be perfectly disjoint and still be wrong. The way it admits fails further on. A backtracking
  parse would have taken the next way there. The meters do not measure such a failure. The backtracking mode and the
  committed mode read such a gate differently. A run of both modes over a shared corpus would measure such a case.
  `PLAN.md` owes the committed mode.

  The count's first meaning is honest rather than a failure. The entry sets that sit behind a gate err wide on purpose.
  A wide gate is exactly a gate that admits a character its way cannot go on to match. Such a gate is harmless while the
  parse backtracks. Such a gate is fatal to a parse that does not backtrack. A run prints the count. A reader judges a
  transform by the count and the meter together. The count gates nothing. A gate on the count would make the pair of
  modes agree, as the corpus runs agree.

  **The meter counts more than a single thing, and the walk says which kinds.** `determinize.py` walks a conflict's live
  ways in lockstep to their first divergence. The walk classifies the conflict's site by the difference there. The
  characters may differ, and then a character decides the site. The codes over the same spans may differ. A parse that
  holds the tokens and retypes them can then decide the site. A configuration of the walk may repeat without a bound.
  The walk ran over the sites the meter flagged. The walk found **a character decides** and **the codes differ**. The
  walk also found **it cannot even root** and **a guard already separates**. The last kind is the meter reading a peek
  and no more. The meter's own description says as much.

  A site the walk cannot root gets a step. The meter's flag on such a site is no verdict. `_caller_continuation` refuses
  a conflict reached from more than a single place. The follow is then more than a single way. The walk then has no
  place to begin, and the meter is claiming a fault it cannot describe. `splice-conflicts` works on such a site from the
  other end. The step replaces a call to such a conflict with a copy of the conflict. The copy sits in the context the
  call had. The follow is the remainder of the way the copy sits in. The copies differ by position rather than by
  content. The sweep therefore keeps the copies apart.

  The corpus or a count found the side conditions below. A way that has taken a character does not splice. The parse
  then enters the callee somewhere other than the way. A way that has **committed** does not splice. The callee's ways
  backtrack *inside* the region the commit opens. A way spliced out would open a region of its own. The first way's
  failure would then be the error. The next way would get no turn. The flow collections commit on an unterminated
  bracket, and they broke fixtures before the commit condition existed. A callee that continues at something not level
  does not splice. A call to such a callee would become a call the way comes back from. The parse pushes the caller's
  continuation behind that call. The scope net caught such a call.

  The step runs to a fixpoint. Splicing makes sites. A pass moves the count of conflicts up as often as down. A pass
  leaves a caller that has become the conflict. `every-conflict-can-be-asked` is the walk's own question. It is also the
  count that says how much of the meter anybody can work on at all. The count falls, and the committed mode and the
  backtracking mode read fewer cases differently. The meter itself rises, and the rise is bookkeeping rather than work.
  The walk says a character decides more cases than before. The cases that no character decides have not moved.

  The remainder has a name. A commit in front of the call blocks a way of the remainder. Such a way wants the commit
  lifted. The remaining ungated ways want the same step. A way wants the call it continues at to be level.

  **A prefix can hide behind a call, and a gate cannot see a prefix that does.** A pair of `b-break` ways both begin by
  handing control to `b-carriage-return`. They therefore do the same thing until that call returns. That is a shared
  beginning like any other. A comparison of gates does not find it. `no-conflict-shares-a-called-head` counts such a
  beginning at conflicts only. A choice whose gates already tell its ways apart has nothing to move. Inlining there
  would copy a production for no decision at all.

  `inline-shared-heads` splices a shared callee into the ways that call it. The splice rewrites upward into the callers
  of a production. `inline-shared-heads` makes the same rewrite downward into a conflict. The grounds that refuse the
  splice refuse `inline-shared-heads` too. The pair of steps therefore share a pass, and differ in which calls they
  name. A terminal splices too. A terminal has a single way, and that way is a character. A call to a terminal becomes a
  gate on the terminal's set and a consume. Such a call hides no prefix. The count of conflicts falls, and the grammar
  shrinks. The sweep can drop the production a shared head came out of. The sweep makes a single pass. A second pass is
  an open question. A second run over the grammar the sweep produces keeps the count falling, and the grammar narrows. A
  second run over the grammar the sweep started from makes the count climb instead.

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
  it. The parse cannot hand back to that way. A choice goes on to its next way exactly where the way in front fails and
  hands back. A way that matches throughout therefore leaves the ways behind it nothing to enter on. So does a way whose
  failure is the error a commit names. Both stop the choice at that position. The invariant reads **none** from the
  point a step claims it.

  The step asks a single question rather than a pair. The parse refuses a way where a character it needs is not there. A
  guard the way asks may decline, or the gate may turn the way away. The parse refuses no way past a `(cut)`. The parse
  refuses no way inside a committed region. `interpreter.match` raises through a region that has not closed, and hands
  back through a region that has closed. The parse refuses a counted consume. Such a consume takes its whole count or
  none. A consume of none or more takes another route. A narrower question asks whether a way refuses *the character it
  cannot start with*. That question gives a pessimistic bound. It names the first place a raise is possible and stops. A
  way that raises on an input hands back on another. A checker asking the narrower question called ways unreachable.
  Deleting those ways broke the fixtures `c-l+literal.n=2.empty` and `header-eof`. `keep-empties`, `keep-none` and
  `JEF9/00` broke with them. `gate_hoist_call` asks the narrower question, and it keeps that question.

  **A way of a choice has a test the machine can make before entering it.** `every-way-gated` is
  `every-way-carries-a-test`. The invariant counted the tests throughout, and its name says so. A machine takes a way by
  testing something first, and the kind of test comes second. A character set is the usual one. A guard is one too. A
  guard asks where the parse is in its line. It asks whether the input holds a further character, or how the indentation
  compares. Whether the tests of a choice are exclusive is the next question and a different count.

  `hoist-askable-guards` settles the guards' half of the work. A guard among a way's actions asks its question too late.
  The parse reaches a guard among the actions only by entering the way. That guard could have decided whether the parse
  enters the way. The guard therefore moves to the gate. The parse asks the guard at the gate. The step decides which
  guards move. A guard reading the input moves past an action that writes neither the position nor the input around it.
  A comparison passes the actions that do not write what it reads. An indentation test behind a `PushIndentAction` would
  read an indentation the parse has left unpushed. A commit lets nothing past. A `CutAction`, an `ErrorAction` and a
  `PushMessageAction` are commits. Past such an action, a failure is an error rather than a refusal. A gate that keeps
  the parse out of the way would therefore change the outcome. A parse that stopped would take another way.
  `no-guard-left-among-the-actions` counts the remainder and settles at none.

  A test written as a call is also a test the parse cannot use to enter a way. The same step lifts such a test. A
  production of a single way *is* its gate. That way holds no action, makes no call and continues to nothing. A parse
  entering that production therefore asks that gate. A way hands control to such a production. A sweep merged the
  zero-width end-of-stream helpers into a single `EndOfStreamGuard`. That merge kept the block header's name. The call
  becomes the guard. The guard goes into the site the call had. Lifting and hoisting feed one another. A callee may hold
  its gate and no body. Callers can lift such a callee. Lifting and hoisting run until neither finds anything. A round
  drops a call or moves a guard. The grammar holds finitely many calls and guards. The rounds therefore end.

  The sweep takes out an empty match among a way's actions. A sequence drops `<empty>`, and a way's actions are a tuple
  on an alternative rather than a sequence. The empty match therefore stayed in the tuple. A walk looking for the first
  action of a way stopped at the empty match. That match takes no character and does nothing.

  The step leaves a way ungated where that way calls a particular kind of production. Such a production answers a
  character it cannot start on with an error rather than a refusal. A gate on such a way would therefore turn a parse
  that stops into a parse that takes another way. The parser would then accept a different language. Such ways want the
  commit lifted off the callee.

  **A scope catches a failed cut, and the IR names the scope's pair.** A `(recover)` was a scope left as a node. The
  node read as "a handler rather than a scope, with no close whose position means anything". The interpreter says
  otherwise. The interpreter saves the parse state where a region opens. The interpreter puts that state back where a
  cut unwinds in. The window, the message and the code pairs do the same. A recovery also *resumes*. The window, the
  message and the code pairs resume nothing. A recovery's close holds information. That close is the last written rather
  than the fourth.

  The IR names a pair for a recovery. `PushRecovery(recovery, resume)` sits before the call it covers, and `PopRecovery`
  sits where that call returns. `PushRecovery` names both operands outright. The pair belongs after
  `build-alternatives`. The place a way holds on at gets a name only once the way is a call and a continuation. A site
  of the pair wants a pair of productions. The first holds the pop and the continuation the way reached. The call
  returns there. The second holds that continuation and no more. The unwind resumes there. The second must not pop. The
  unwind has already taken the region off. `KEPT_THOUGH_DEAD` says why a step writes the pair at no point.

  The interpreter keeps a pair of lists for recovery. The first is the return stack. An entered production that matches
  holds an entry on that stack. The interpreter pushes and pops the stack an entry at a time. The stack moves with the
  production trace. The second list holds the open recovery regions. The interpreter checkpoints neither list. A
  region's own pushes and pops balance the region, as they do for the committed regions.

  **A way with a scope cost the caller its recovery.** `splice-conflicts` builds a spliced way out of a callee's parts.
  It took the callee's recovery and dropped the caller's recovery. It did so silently. A dropped handler shows only as a
  parse that stops where a recovery should have caught it. The recovery fixtures failed once a flattening wrote out a
  called run of items and handed the result to `splice-conflicts`. A frame-scoped pair around those items would leave
  nothing to drop. A rewrite that moves a way takes the actions along. A side condition on the splice keeps the handler
  until a step writes such a pair. The shape of the way does not keep the handler.

  **`flatten-called-sequences` writes a called sequence in place of its call. A gate that decides nothing moves up.**
  Take `a P c`, where `P` is `d e`. The way `a P c` hides the run `d e` behind the call to `P`. A checker walks a way to
  find the first action of that way. The checker stops at the call to `P`. `flatten-called-sequences` writes the body of
  `P` in place of the call. A call last in the way stays. The parts past a call form a separate production.
  `no-sequence-of-sequences` falls to a residue. The recursion guard holds that residue.

  `no-gate-decides-nothing` sits beside `flatten-called-sequences`. A gate on a body that offers a single way selects
  nothing. The invariant reads **none at the door**. A way holds no gate until a step re-encodes the way. A count under
  the invariant therefore names the step that strands such a gate. The hoists strand such a gate, and a hoist declares
  the gate it strands. The hoists gate a way where they can. A body's single way is such a place. There the gate is an
  assertion the callers can discharge.

  `lift-gates-to-callers` moves the stranded gates it can. It moves by **splitting rather than rewriting**.
  `A = Gate A'` with `A' = Stuff` leaves `A` meaning exactly what it meant. A caller that can take the gate calls `A'`
  instead. Rewriting `A` in place needed a side condition on the other entries into `A`. A caller of `A` had to take the
  gate. The side condition made an exception for a production entered by name. It made another for a production a
  fixture runs directly. Those conditions vanished with the split. The step leaves a gate in place where a way acts
  before the call. It also leaves a gate in place where a way reaches that gate in tail position. At such a site, the
  caller's own entry says nothing about the next character.

  After the split, the callers of a production gate it. A parse then enters that production on a character the
  production can start with, and a refusal is out of reach. The coverage gate already credits a gate refusing to the
  production it guards. The comment there reads "otherwise gating a rule correctly would make it look untested". The
  coverage gate also counts a refusal that the callers of a production have taken over.

  The corpus holds cases where a committed run and a backtracking run read differently. Those cases **fall again**. The
  meter rises, and `every-conflict-can-be-asked` rises too. The written-out copies cause that rise. The step mints
  ungated forms, and a written-out run adds ways the meter counts as undecided.

  **A way of a choice may call another choice. The step writes that called choice out at the site of the call.**
  `a | P | c` where `P` is `d | e` makes more decisions than it shows. The hidden decision sits behind the call to `P`.
  The outer choice cannot see that decision. The written-out `a | d | e | c` is the same ways in the same order. A way
  then sits where a gate can go on it. `no-choice-of-choices` counts the decisions still hidden where the step runs.
  `flatten-called-alternations` settles that count. It runs until nothing moves. It does not run into a choice that can
  reach back. Such a choice would write itself out for ever. The step runs before the step that splits a production into
  a call and a continuation. A later phase therefore sees the choice whole.

  `every-way-carries-a-test` falls, and the meter falls with it. Fewer corpus cases read differently under a committed
  run and under a backtracking run. The grammar also got *smaller*.

  The conflicts left are a single shape. Such a conflict is a way that hands control to a production. That production
  may see a character it cannot start on. The production then answers with an error rather than a refusal. A
  `NegLookGuard` guards the way. Such a conflict sits in a copy that detects the indent. That copy holds the block
  scalar's content.

  **A question about a node goes through a table that cannot cover a kind nobody named.** `ir.Question` maps a node kind
  to the handler for that kind. A kind nobody put in the table **raises**, and the message names the checker. A handler
  nothing reaches is **reported** by `unexercised` after a whole-corpus run. A table naming a kind twice will not build.
  A table therefore names exactly the kinds that occur. A checker makes no claim about a kind outside its table. A kind
  added to the IR touches the checkers that reach that kind. A checker names a wide group and lists under `NEVER` the
  kinds that cannot arrive. A check tests the `NEVER` list rather than trusting it.

  The table replaces a chain of `isinstance` tests ending in a fallthrough. Such a chain answers permissively for a form
  its author did not think of. The chain reports its own blindness as a property of the grammar. The gate read an
  invariant a different way on run after run over the *same grammar*. The checker was a walk that defaulted silently on
  a form the walk did not recognise. The walk missed a character wrapped in a `(token)`. The walk read a way's items and
  missed a character in the way's gate. The walk also missed a repetition and a count the parse works out.

  `is_one_char` had an unreachable `(case)` branch. `_is_actions_alone` claimed a single kind. The kinds that reach it
  are the kinds a way can begin with. The `ir.Question` tables state the handler for a pair of overlapping kinds. The
  order of a test chain had settled those handlers silently. `ErrorAction` and `PushMessageAction` are actions and
  commits both. A walk finds the places a way can refuse. That walk must stop at such an action rather than step over
  it. A step that strips "the scopes written around a match" strips a `(token)` and no more. A `(max)`, a `(recover)`
  and a `(wrap)` reach that strip at no point.

  A checker may answer with a named `Verdict` rather than a value. A walk over a way's items can then be a table as
  well. The table takes the item. It may step over that item, or stop. It follows the call the item makes, or follows
  what the item holds. It may also treat the item as the commit past which a failure is an error rather than a refusal.

  **A verdict names the difference between a conflict's ways, and the depth of that difference.** `determinize.verdict`
  walks the live ways to their first divergence. The characters may differ. Factoring the shared prefix down to that
  point puts the decision where the input makes it. The codes over the same span may differ, and no depth of factoring
  separates those. The step holds the run and retypes it instead. The walk may find no root, fail to converge, or run
  past its limit. Such a site gets no verdict. Among the sites the meter counts, **a character decides the larger
  pile**. The codes decide a smaller pile. The remaining sites have no verdict. `no-lookahead-left-to-factor` sums the
  depths of the first kind. A loop of factoring runs until a round leaves that sum unchanged. The meter plays no part in
  that stop. Factoring trades an undecided choice at a depth for an undecided choice a character shallower. The meter
  can therefore sit flat while a round makes real progress. The summed depth cannot sit flat that way. A round takes
  exactly a character off a conflict it targets, and no rewrite pushes a discriminator deeper. The summed depth
  therefore falls by the number of targets and does not rise. The pipeline holds no step that takes the summed depth as
  its measure.

  A sweep clears the leftovers of a step out of the step's grammar. The sweep runs its passes to a fixpoint, and the
  passes feed one another. The sweep flattens a body to the shape it denotes. A sequence or a choice of a single item is
  that item. A nested body of the same kind is its items in place. An `<empty>` in a sequence goes. The match stays at
  that position and moves nothing. A choice of *nothing* stays. It is the path no input takes. It comes first. The sweep
  merges productions by shape rather than by meaning. A pair of productions may say the same thing with a singleton
  alternation in different places. They merge only once the flattening has run. A production may hold a single call with
  no gate and no action. Such a production is its callee. A reference to such a production therefore becomes a reference
  to that callee. The sweep keeps a single copy of a pair of productions that behave alike. Such a pair has the same
  parameters and the same body. The merge replaces a reference with the group the reference names, rather than with the
  name itself. The merge thereby tells a pair of loops apart from a loop written twice. The sweep purges a production no
  parse can enter. That pass runs last, and it sees the productions the earlier passes strand. The passes change neither
  the text the grammar matches nor the events the grammar emits.

  The sweep drops no fixture it strands. A stranded fixture pins to the last stage whose grammar can run it. The fixture
  guards that grammar token for token, and credits coverage from that stage. The coverage gate credits a minted helper
  to the base the helper came from. The gate credits a monomorphic copy the same way. A helper holds a piece of the
  base's own body.

  `check_normalize` asks a step to leave the tokens and the events identical over the corpus. The corpus holds the
  conformance fixtures and the YAML Test Suite cases. Some of these cases pin the document-marker boundary. The spec's
  `c-forbidden` writes that boundary, and the Clojure build of YAML Reference Parser agrees on it. `---foo`, `---#foo`
  and `----` are content, and their `...` kin are content as well. The `--- foo` form is a boundary. The `... foo` form
  breaks the `c-forbidden` rule. Other cases pin the hand-off a sequence makes at a dedent. A committed block structure
  must reproduce that hand-off. At a dedent an exiting level puts its end markers before the dedent line's indent token.
  The owner level continues past that token. A dedent at the line's start has no indent token at all. A pair of cases
  pin the sequence entry's committed dash. The Clojure build of YAML Reference Parser agrees that both cases are errors.
  `- @` is an entry whose body fails inside the entry's own committed region. The entry's gate refuses `-b`, and the
  sequence closes before a stream-level error. A step must change the grammar. `check_normalize` reports a step that
  changes nothing as a fault. A step goes idle where the shape it looks for stops arriving. Such an idle step marks a
  regression in the preceding step. The idle step comes out. `check_normalize` reads a step's output before a later
  sweep runs. The check therefore judges a step on the step's own work, rather than on the sweep's work.

- `(match)` is the text of the open token the rule is building. The `(<<<)` operator comes out, and the origin that
  measured `(match)` comes out with it. `OpenMatch` and `CloseMatch` go too. The `match_start` parameter and the
  `lower-bounds` step go with them. An indentation is the length of the indent token the rule builds. `s-indent-lt` and
  `s-indent-le` read that length. The block header's indicator is `(atoi)` of the digit the header just consumed.
  `(atoi)` reads a whole string. The official grammar and libyeast's grammar justify `(match)` in differing ways. In
  libyeast's grammar, a `(match)` must sit inside a `(token)` it fills, and must not read a run a nested `(token)` has
  cut. The official grammar has no token annotations. The official grammar writes `(match)` in `s-indent-lt`,
  `s-indent-le` and `c-indentation-indicator`. In those productions, the `(match)` must read a run that libyeast reads
  in the same production. A comparison against the official grammar rewrites the official forms into libyeast's
  vocabulary. In libyeast's grammar, `(<<<)` wraps a possessive repetition. Such a repetition hands back no characters.
  `(ord)` reads a single digit.

- Decoder ABI. The decoder consumes a run of characters, and the ABI names that act `consume`. `ys_consume_set` advances
  while the character is in a set. `ys_consume_trim_sets` does that over a pair of sets. A `ys_consumed` says the bytes
  and the characters a call took. The byte count and the character count differ wherever the run holds a character
  outside ASCII. The change drops `scan` as a name for the act.

- Decoder ABI. `ys_consume_trim_sets` consumes over a pair of character sets in a single forward pass. It takes the
  whole run under `full`, and says how far the last character not in `trim` reached. It returns a `ys_trim`. A `ys_trim`
  holds the kept `span` and the `trim` run handed back after it. A plain or a quoted scalar's line compiles to this
  call. The call keeps the inner spaces. The trailing spaces go to the caller as an `s-white*`. The call reads the input
  once. The generated parser does not call `ys_consume_trim_sets`.

- Indentation detection. The official grammar calls the rule a "special rule" and gives no definition. Elsewhere the
  official grammar writes the detected indentation as an integer added to the string `"auto-detect"`. libyeast defines
  the rule. libyeast declares its departures from the official grammar. A reason goes with a departure. `m` is an
  indentation. In the official grammar `s-l+block-indented` reads `m` and does not set it. In libyeast
  `s-l+block-indented` sets that `m`.

- The yeast wire format. `ys_write_token` writes a token stream. A token becomes a line holding the code's character and
  the token's escaped text. `ys_read_token` reads a token back. A tool can therefore pipe a stream onward. A tool can
  store a stream, or compare it against another parser's stream. `ys_bytes_writer` mirrors `ys_bytes_reader`. The pair
  takes the same file-descriptor and `FILE *` adapters. An escape writes a codepoint under any code but
  `YS_CODE_UNPARSED_INVALID`. An escape writes a byte under that code. The reader and the writer both check that a
  token's text matches what its code claims. Take a code that means codepoints. A writer escaping a raw `0x80` as `\x80`
  under that code says U+0080. A reader then gets back a pair of bytes nobody wrote. `YS_CODE_UNPARSED_INVALID` holds
  exactly the bytes that encode no character. A byte of the text under that code opens no character. The text under
  another code must encode characters. `ys_write_token` checks both conditions. A token failing either gets `EINVAL`.
  The errno policy already promised that result on a bad argument, and the check had not run. `decoder.c` owns the
  validation. `ys_codepoint` assembled continuation bits and did not check for continuation bytes. That function
  silently turned `"\xE0ab"` into different bytes. `YS_CODE_UNPARSED` becomes `YS_CODE_UNPARSED_TEXT`. The new name
  pairs with `YS_CODE_UNPARSED_INVALID`. The reader's search for a line's break resumes where the last search gave up. A
  line arriving in pieces therefore costs the length of the line rather than its square. The format serves a wire read
  from a pipe. Such a wire arrives in pieces. `max_bytes` is unlimited by default. The cost was therefore a denial of
  service against the format's own purpose. The reader also refuses a position a token cannot start at. The reader
  parses such a position and adds the token's text to it. The sum overflows, and the end wraps around. A caller compares
  or slices the token's start and end marks. The span then runs backwards. The reader's check matches a position too
  large to read. The reader finds that fault a step later, and the refusal message says so. `ys_write_token` also
  accepted a code with no wire character, and wrote the code's character out unchecked. Such a code produced a line no
  reader could read back. `ys_write_token` answers `EINVAL` for such a code. `ys_code_char` answers `'\0'` there rather
  than `'?'`. `'?'` was safe while the codes left it unclaimed. A code cannot claim `'\0'`. A line is NUL-terminated. A
  code written as `'\0'` would read back as an empty line. `check_wire.py` refuses a character in the table that is not
  printable. A text wire requires a printable character. A code the enum names has a character. `ys_code_char` therefore
  answers `'\0'` for an out-of-range code. Such a code is a caller's mistake rather than a code the library produces.

- The grammar settles ill-formed UTF-8 against fixtures. The reference interpreter reads its input a character at a time
  out of the bytes. A byte that begins no character is the value `<invalid>`. The interpreter throws no exception for
  such a byte before the parse starts. A token's text is therefore the input bytes untouched. A fixture can cover
  ill-formed input. Recovery's `l-unparsed` interleaves runs of such bytes as `YS_CODE_UNPARSED_INVALID` tokens among
  the `unparsed-text` and the breaks. A run is maximal. It ends where valid UTF-8 resumes, or at the end of the input.
  On the wire that token's `\xXX` is a raw byte, and not the codepoint it would be under any other code. The reader
  refuses a `~` token that holds a valid character among its bytes. The writer refuses to write such a token.

- Errors tell the caller what to do about them. A malformed document gives `YS_CODE_ERROR`. The text holds the message,
  and the wire character is a `!`. A malformed wire gives the same code. It is bad data like a bad document. A host
  failure is not the data's fault. `ys_read_token` reports such a failure as a `ys_status` return value rather than as a
  token. Running out of memory is a host failure. A reader failing is another. A host failure ends the source for good.
  A caller reads the input again through a new source with a larger cap. `ys_options.resume` says what the parser does
  with the input after a malformed document. By default the error ends the parse, and the rest of the input comes back
  as `YS_CODE_UNPARSED` tokens. Haskell YamlReference ends the parse the same way. `YS_RESUME_DOCUMENT` instead
  continues at the next document. A malformed document in a stream then does not cost the caller the remaining
  documents. `YS_RESUME_INDENT` continues inside the document, at the next line no more indented than the entry that
  failed. A malformed entry then does not cost the caller the rest of its container either. A policy gives up less of
  the input than the policy before it. A policy with nothing to resume at falls back to the policy before it. Under
  `YS_RESUME_DOCUMENT` or `YS_RESUME_INDENT`, the output matches Haskell YamlReference up to the first error. A skipped
  line is two tokens. The content is a `YS_CODE_UNPARSED` and the break is a `YS_CODE_UNPARSED_BREAK`. The break gets a
  separate code because the parser found no structural break there.

- Parser state. The state holds the window over the input. It holds the stack of productions the parser is inside. The
  state also holds the machine state and a queue of tokens. The queue holds the tokens the parser built and has not held
  back. A single struct holds the state. The C call stack holds none of it. `ys_read_token` can therefore hand back a
  token from the middle of a production and resume there. The queue holds a run of undecided tokens. Once the parser
  learns what the run held, it rewrites the codes of the tokens in the run and injects a marker ahead of the run. The
  stack holds the grammar's runtime parameter `n`. The generator builds no automaton to drive the state. `ys_read_token`
  returns a "not implemented" error.

- `tests/spec/` holds a conformance suite. Somebody built it once from Haskell YamlReference's vendored `tests/`. The
  build took the fixtures that align with libyeast's grammar. The build turned an expected output into the tokens
  libyeast emits rather than the tokens Haskell YamlReference emits. Where libyeast flattens a production to a character
  class, the fixture of that production expects plain unparsed tokens. A fixture's token ends on the line it starts on.
  A byte-order mark token holds the matched character rather than Haskell YamlReference's encoding name. An error keeps
  the position and drops the wording. The build drops Haskell YamlReference's isolated-run commit artifacts. Haskell
  YamlReference itself departs from the spec in the plain-scalar `:` and `#` factoring. libyeast follows the spec. The
  build leaves out fixtures in encodings libyeast does not read, and fixtures for Haskell YamlReference's own internal
  productions. From there libyeast owns the fixtures, and nobody keeps the build that made them.
  `generator/check_spec_tests.py` keeps the fixtures intact. A fixture pairs its input with an output. A fixture's name
  is a production of the grammar. An output is a token stream whose marks chain and whose markers balance. A fixture of
  the root is a whole parse and must balance exactly. A fixture of a rule run outside the root may close a marker its
  caller would have opened. Such a fixture may still not leave a marker open. `check_markers` settles the grammar's
  clean paths and cannot reach that open marker. The imbalances this suite's gate has found came from error paths. A
  fixture whose name calls its input invalid must fail to match. The fixture's production refuses that input, or stops
  short of the input's end. The name makes a claim. An unchecked claim let `c-printable.invalid` hold a character
  `c-printable` accepts. The suite holds the input bytes verbatim. A CR or a CRLF line ending goes through unnormalized.

- A reference interpreter of the grammar sits in `generator/interpreter.py`. That module is a slow backtracking matcher.
  The interpreter runs a production against an input and emits the yeast tokens of the match. A gate checks the
  interpreter fixture by fixture against the conformance suite. That check proves that libyeast's grammar produces
  Haskell YamlReference's tokens before any C runs. The interpreter matches the character-level nodes and the
  repetitions. It matches the parameter machinery that threads the parameters and detects indentation. It matches the
  assertions and the lookahead. It matches the ongoing `(exclude)` guard that stops a plain scalar at a document
  boundary. It produces tokens from the annotation nodes. It gives a run a code, and brackets a match in `begin` and
  `end` markers. It emits a bare marker, and writes an error token. That token names the production the parse wanted.
  The interpreter backtracks in the success-continuation style. A later element that fails sends the interpreter back
  into the alternation, as Haskell YamlReference does. The interpreter reproduces a fixture token for token. The
  reproduction covers `l-yeast-stream` and the malformed inputs. The reproduction rests on a promise the emitter makes.
  The promise is that a checkpoint captures the state. The interpreter can then undo an alternative that fails.
  `make verify-emitter` checks the promise. A restored field comes back the same way on a second restore. An alternation
  rewinds to a single checkpoint once per branch. The promise did not hold. A checkpoint handed out the parameters
  rather than a copy of them. A discarded branch's `(set)` therefore reached into the parameters of the checkpoint the
  next branch rewound to. A fixture could see the leak at no point. Where a grammar alternation holds such a `(set)`,
  the branch that matched sets the same parameter again. That branch overwrote the leaked value. The grammar's `(cut)`
  acts on a malformed input. A cut commits. If the parse then fails, the interpreter emits an error token naming what
  the cut expected. It closes the markers the abandoned parse left open. It hands the rest of the input to the grammar's
  recovery rule `l-recover`. That rule brings the input back as unparsed. A failure that passed no cut is not an error.
  The production that failed rejects its input, and the report names where the match ended.

- Error reporting lives in the grammar. A `(cut)` marks the point where a parse commits. An `(error)` is an error token
  the grammar writes where it already knows the parse cannot go on. An error names a message in `grammar/messages.yaml`.
  The interpreter reads that file. The build generates the C message table from the same file, and a gate keeps the pair
  in step. A cut or an error hands the rest of the input to the recovery rule `l-unparsed`. That rule brings the input
  back a line at a time, as `YS_CODE_UNPARSED` content and `YS_CODE_UNPARSED_BREAK` breaks. The rule consumes any
  character and earns a character set in the decoder. The decoder freed that set by moving the length field of a key
  into spare bits. The block header gained a lookahead. The lookahead lets the header choose its ordering without
  backtracking. A cut in the header therefore blocks no ordering the header needs. The lookahead is a declared
  deviation, and the official header is ambiguous there. An anchor commits after its `&`. An alias commits after a `*`
  the same way. Both `&` and `*` are indicators. Where a node's properties may start, neither character begins another
  construct. The parse reports an `&` with no name as a mistake rather than as a rule declining to match. A message says
  what its own cut expects and no more. A `...` marker requires a comment or a line break. The marker's cut guards the
  comment or the break. The message of that cut had claimed that a new document had to follow. A stream holding `...`
  and no more is valid.

- A block scalar's leading empty lines obey the spec's prose in section 8.1.1.1. An empty may out-indent the first
  content line at no point. The reference and the BNF read such a line as content. Its extra spaces fall through the cap
  of `l-empty(n)` into `s-indent(n) nb-char+`. A space is an `nb-char`. libyeast makes such a line an error instead, and
  `check_vendor_spec` declares the divergence. A forward parser with no lookahead learns of a broken floor only once the
  content line arrives. The parser therefore reports the error at the content line. `l-leading-empties` emits a leading
  empty under any indentation. It raises `f` to the widest of those lines with the `(increase)` action. `f` is a new
  runtime floor parameter. The action computes `f = max(f, column)`. `s-indent-floor` then takes the content line's own
  indentation, and then emits an under-indent error keyed `BLOCK_SCALAR_UNDER_INDENT`. The error site also tells an
  empty scalar from a violating one. An empty scalar has no content line to come. The next line reads as an
  under-indented line rather than as an over-indented empty. The indentation match fails before the cut at that line,
  and the scalar ends with no error. The literal and folded styles share the mechanism. The folded style splits into
  folded and spaced lines only after it takes the shared indentation. The parser therefore checks the floor once, and
  fixtures pin the literal and the folded style.

- An implicit mapping key obeys the spec's bound in section 7.4.2. A `:` decides whether the entry is a key. A parser
  must see that `:` within 1024 characters. The official grammar writes this as `(max): 1024` before the key production.
  The official grammar does not enforce that bound. libyeast makes `(max)` a window that wraps the production, as
  `(max): [1024, IMPLICIT_KEY_TOO_LONG, key]`. The interpreter runs that window. The window is the deterministic
  parser's bounded lookahead. The window matches the key up to 1024 characters. The window runs the interpreter's own
  unbounded lookahead, and keeps the tokens inside the limit. A key that runs past the limit fails with
  `IMPLICIT_KEY_TOO_LONG`. The parse reports the input from that point as unparsed. The tokens up to exactly the 1024th
  character come out first. The window cuts the run where the limit falls. A token split across the limit therefore
  comes back under its own code. The error token follows those tokens. A reader who recovers the official grammar turns
  the window back into the preceding `(max): 1024`. `check_vendor_spec` therefore reads the official grammar rule for
  rule, and declares no divergence. The grammar also holds the key to a single line. That bound needs no window. The
  flow-key context binds a key's separation to `s-separate-in-line`. That context also binds a key's scalars to a single
  line. A key therefore consumes no break. A fixture pins a limit that falls inside a token. Another fixture pins a
  limit on the boundary between a pair of tokens. Both fixtures also pin a flow-collection key that a break would run
  onto a second line. Such a key fails as an unterminated flow collection.

- `l-yeast-stream` is the root the parser runs. It matches a YAML stream and then the end of the input. A part of the
  spec's `l-yaml-stream` is optional. `l-yaml-stream` matches nothing on an input such as `]`. The parse would then
  report nothing about that input. The root reports an error there instead, and the parse hands the input back as
  unparsed. A byte therefore reaches the caller under any content. The root's second alternative matches throughout. The
  first way `l-yaml-stream` finds is therefore the way the parse takes, and the parse does not backtrack into
  `l-yaml-stream`. The root is libyeast's own rule. `l-unparsed` is another. The spec's rule 211 stays untouched. A
  reader recovers the official grammar from libyeast's grammar, rule for rule.

- The resume policy is the grammar's fifth parameter. `ys_options.resume` chooses what the parser does with input it
  cannot parse, and the grammar says what a choice means. `l-unparsed(n,r)` guards its own run. Under
  `YS_RESUME_DOCUMENT` the run therefore stops at the next `---` or `...`. It does not eat the rest of the input.
  `l-recover(n,r)` brings that run back, and then parses the stream again from the marker. `l-recover` and the stream
  rule are mutually recursive. A second error inside a resumed document therefore needs no mechanism of its own. A
  failed cut arrives at the same rule the root does. A cut says where the unwind lands, and it says no more. `r` is
  finite, and takes the fate of `c` and `t` rather than the fate of `n`. It specializes away at generation time. The
  emitted C holds an automaton per policy, and `ys_options.resume` chooses the start state. A fixture names the policy
  it runs under. It writes `.r=d` beside the `.n`, `.c` and `.t` the filename already has. A fixture that names none
  runs under the default a zeroed `ys_options` selects.

  The resume policies are one hierarchy, and a policy adds a place the run stops. A policy therefore gives up less of
  the input than the policy before it. The first policy adds no stop. The next policy stops at `c-forbidden`. The last
  policy stops at `c-forbidden` or `s-indent-le-line(n)`. The `s-indent-le-line(n)` policy is `YS_RESUME_INDENT`. It
  continues *inside* the document rather than at the next document. A malformed entry costs its container that entry and
  no further entries. The recovery reaches a sibling of the failed entry, rather than a child of it. `c-forbidden` is
  not redundant there. `s-indent-le-line` cannot hold below an indentation of 0, and a run bounded by nothing would go
  straight past a document marker. A failure nothing encloses leaves the indent guard dead, and `YS_RESUME_INDENT` then
  acts exactly as `YS_RESUME_DOCUMENT`. The guard compares with `le` rather than `lt`. A sibling entry sits at exactly
  the container's indentation, and `lt` would skip the entries the policy keeps. The guard does not compare with `eq`
  either. A container with a malformed last entry would then eat the rest of the document. It would hunt a sibling that
  does not come. `s-indent-le-line` is pinned by a lookahead for content. The lookahead forces a reader to measure the
  line's whole indentation, and keeps a blank line and a comment line from being boundaries. Neither line has an
  indentation. The pin is a pair of lookaheads rather than the character class `ns-char - c-comment`. A difference is a
  character set, and the decoder's key has room for the sets it holds and no more.

- `(recover)` says where a failed cut stops unwinding. A cut unwinds past any call between it and the rule that catches
  the cut. A rule holding `(recover)` catches the cut. A block collection wraps the entry in a recovery, and names the
  `n+m` it has already computed. The parse therefore reads no indentation from the runtime. The recovery reads the
  parameters of the rule that declares it. It does not read the parameters of the rule that failed below. The parse
  emits the error. The parse closes the markers the entry opened, down to that depth and no further. The parse gives the
  run up, and continues as though the entry had matched. The collection's own repetition therefore takes its next turn.
  `s-indent(n+m)` matches where the next entry begins, and fails where the collection ends. The parse does not need to
  know where the run stopped. The node holds no policy. A rule reached under a policy that recovers elsewhere has no
  branch to take. It therefore does not match, and the cut goes on unwinding. The remaining policies therefore come out
  byte-identical. Flow collections get no recovery. Recovery works by indentation, and a flow node sits inside a single
  level of indentation. A flow node holds no place to resume at.

- An error closes the markers its document opened. A raise skips the returns that would emit those markers. Without the
  closing, a malformed document would leave its `begin-` markers open. Hanging markers break a parse that resumes after
  an error. The next document then parses as a child of the failed document rather than as a sibling. The closing
  markers come after the error token and in front of the first `unparsed`. The closing markers are zero-width at the
  byte that failed. Their order therefore says that the error is inside the failed document, and that the unparsed run
  is inside nothing. A `begin-` gets its `end-` on a path. The fold that rebuilds the production tree relies on that
  pairing over an errored stream.

- A grammar-coverage gate runs `generator/check_grammar_coverage.py` through `make verify-grammar-base-coverage`. The
  fixtures must watch a production say yes and watch it say no. A run decides coverage, and a name does not. A
  production counts when running a reproducible fixture reaches it. The fixtures that reach a production therefore cover
  a production with no fixture of its own. A production nothing reaches is a gap. The gate takes the grammar as an
  argument. The gate re-runs on a structurally-transformed grammar as the pipeline produces one. The fixture suite
  gained an empty stripped literal `|-`. The scalar-closing `end-block-scalar` is therefore exercised by a clean fixture
  rather than an error one.

  Reaching a rule is half of exercising it. A rule is a decision. A fixture that watches a rule say yes and no more
  leaves the other answer untested. A fixture must therefore also watch a production reject an input by failing to
  match. A fixture may instead watch a `(cut)` inside that production reject the input. The failure then takes the
  message the cut named. The exception is a rule that *cannot* say no. The gate computes such rules rather than listing
  them. The shape of a rule's body proves that the rule is total. The gate therefore asks for no fixture where
  `l-yaml-stream` fails. A part of that rule is optional, and such a fixture cannot exist. A rule that cannot say no is
  still worth knowing about. `l-yaml-stream` cannot say no, and it swallowed a whole input. `l-yeast-stream` says so
  outright. A `(cut)` is a decision too. A cut that does not fire is a commit point, and the fixtures do not show the
  parse reaching it. The message of such a cut goes unchecked. The error token of a cut must therefore appear in the
  expected output of a fixture. The gate reads the fixtures for that token rather than watching the interpreter. A
  fixture is the stricter test. It proves the error survived the trip back to the caller. A cut that raised inside a
  lookahead would prove that it can raise, and no more. New fixtures close the gaps the gate found. The new fixtures
  fire the cuts that had not fired. They also make `c-reserved`, `ns-tag-prefix` and `ns-global-tag-prefix` refuse an
  input, and no earlier input had done so.

  The interpreter enters the top production as a reference, the way any other rule arrives. It does not match the body
  directly. A rule run at the top is still a rule. The gate watches references, and it would otherwise miss that rule.
  The gate had therefore missed the fixtures of the top rules. Those fixtures existed and already rejected their inputs.

- `generator/star.py` folds the tokens to events and checks them against the YAML Test Suite. `make verify-star` gates
  that check. The suite sits under `third_party/yaml-test-suite/`, and other authors wrote it from the same spec. The
  suite therefore catches a grammar bug libyeast's own fixtures would share. Those fixtures came from Haskell
  YamlReference. libyeast parses tokens. Tokens are a level below events. The check is therefore a deterministic fold.
  The yeast stream's `begin-` and `end-` markers rebuild the production tree, and the leaf tokens fill it. The fold
  projects that tree to the stream, document, mapping, sequence, scalar and alias events the suite states. The fold
  drops the node and pair brackets and the presentation. A scalar's value comes off the codes the parser settled. A
  `line-fold` is a space and a `line-feed` is a newline. The fold resolves an escape. The gate strips nothing. The
  tokens separate content from whitespace. The fold resolves a tag through the document's `%TAG` directives over the
  default `!` and `!!`. It decodes the `%XX` URI escapes of the tag. A valid case must fold to its `test.event`. An
  error case must come back a rejection. The fold reports a named tag handle that no `%TAG` defines. It also reports a
  repeated `%YAML`. A token stream shows neither resolution error. The HTML spec is the source of truth. A case passes,
  or libyeast declares where the case departs from that spec. libyeast declines to match `JEF9/02`. `JEF9/02` is an
  empty kept block scalar whose input ends in no line break. YAMLStar loads it by first appending the break, and the
  YAML Test Suite therefore expects the line feed that break yields. The spec appends nothing. End-of-input is a line
  break in `b-chomped-last`, and a scalar with no content does not reach it. libyeast therefore folds `JEF9/02` to the
  empty scalar and declares the divergence from the suite.

  `make verify-star` found faults in the grammar and in the interpreter. libyeast's own fixtures had agreed with both.
  The parse first tries a quoted scalar or a flow collection as a block-mapping key. The parse tries that at a
  document's top and at a block-sequence entry. The `UNTERMINATED_*` cut of the scalar or collection committed at the
  opening. A scalar that closed cleanly but found no `:` therefore fired the cut. The parse did not backtrack to the
  scalar. A whole `"hello"` document became an error. A flow cut covers its own item, and the error therefore comes
  where the item does not close. `:` is an `ns-anchor-char`, and `*a:` is therefore the alias `a:`. The backtracking
  interpreter shortened the name to `a` to open a mapping. A `<not_followed_by_an_ns-anchor-char>` guard holds the alias
  to its greedy match. Haskell YamlReference and YAMLStar do the same. A block scalar's last content line at
  end-of-input keeps its break. That break is the zero-width line feed `b-chomped-last` emits. The spec reads
  end-of-input as a line break. A block scalar with no content takes the content indentation from the widest empty line.
  That is the fallback the spec gives in section 8.1.1.1. A caller that sets no resume policy gets a root with the
  zeroed policy. The parse therefore recovers from trailing content that root cannot parse. The interpreter does not
  assert that the root is total.

- An invariant asks a single question. `every-end-of-stream-gates-a-leaf-way` asked a pair of things at once. It asked
  that a gate holds an `EndOfStreamGuard`. It also asked that the way behind that gate calls nothing. The second claim
  is untrue. A way at the end of the stream may still hand its remaining work to a call. A continuation resumes a callee
  where the callee left off. A call made with no input left is a different edge. `every-end-of-stream-gates-a-leaf-way`
  fired as soon as a callee's ways went into a caller holding a continuation. `every-end-of-stream-sits-in-a-gate`
  remains. That invariant has the same shape as `every-consume-is-protected-by-a-gate`. A gate asks whether a character
  is there. An `EndOfStreamGuard` asks the same question. That guard therefore sits in a gate.

  `every-way-has-actions-or-a-call` becomes `every-ungated-way-has-actions-or-a-call`, and the renamed invariant reads
  none. A way may hold a gate, perform its actions and then call. The C state machine runs such a way. The grammar
  forbids such a way at no point. The renamed invariant has content for a way with **no** gate. Such a way needs a gate.
  A pair of rewrites supply that gate. The first hoists a guard up out of its callee. The other writes the callee's ways
  in. Both rewrites need the callee's guards to reach the position where the parse enters the way.
  `GUARD_CROSSES_ACTION` says a guard cannot reach past an action the way performs. The renamed invariant is therefore
  scaffolding that empties itself. Once `every-conditional-way-is-gated` reads none, no ungated way remains for the
  renamed invariant to check. A single checker decides which ways are ungated. `_ungated_ways` is that checker, and both
  invariants ask it.

- An invariant must change no grammar, and a gate must finish. An invariant that changes no grammar lets the counting
  pass hand the same grammars to checkers on separate cores at once. `Invariant.__call__` therefore reads the grammar
  before and after, and refuses a grammar that differs. The comparison sits in a `finally`. The next invariant still
  gets the grammar a checker could not answer about. `gate.spread` puts a watchdog over an item, and over its own wait
  for an answer. The watchdog names the item and prints where a thread sits. The watchdog then takes the run down. A
  check that spins printed its last line and then printed nothing. That line said which worker happened to print last,
  and no more.

  A question asks where a parse enters a production. A walk answered that question. The walk scanned the grammar for a
  name on a round. An invariant re-ran the scan. An index of the call sites answers that question. A grammar builds that
  index once. `no-production-reaches-itself-unconsumed` grew the whole reach of a name. That relation grows with the
  square of the grammar. A walk of Tarjan's components replaces that growth. The walk names the productions that reach
  themselves. The walk also gives the order in which a reader may take the productions. The scope signature ran the
  whole grammar to a standstill, pinned the parts that still moved, and began again. The pipeline reaches a wide
  grammar. On such a grammar the scope signature walks per production, per round and per name. The scope signature's
  walk does not finish. The pipeline's own check runs in seconds.

- A step names its relation to an invariant. `establishes` is a new relation. A step that establishes an invariant reads
  none of the grammar it hands on. The invariant made no sense before the step ran. The step builds the shape the
  invariant is about. `settles` means that a step receives a broken count and takes that count to none. A step that
  settles a count already at none is a fault. A **claim** is a step that transforms nothing and establishes what it
  names. `holds-at-the-door` and `holds-once-optionals-are-ways` are claims that named themselves with the wrong word.

  The change buys a hard fault where there was a silence. The gate asked an invariant of a stage. A checker could reach
  a shape the pipeline had not built, and that checker raised. The gate caught the raise and read it as "not a question
  here". A checker broken outright and a checker asked too early therefore gave the same answer. The gate asks an
  invariant from its own step onward. A checker that cannot answer there is a fault.

- A gate reads the character in front of it at most once. A pair of peeks in a gate are a question said twice. A pair of
  `LookGuard`s are the set they both admit. A `LookGuard` beside a `NegLookGuard` is the set the first admits and the
  second refuses. A hoist brings such guards together. A hoist moves a question to a position that already holds another
  question. The step that builds gated ways therefore establishes `every-gate-looks-ahead-at-most-once`. A hoist
  declares a lapse of that invariant. A `merge-gate-peeks` after the hoist folds the sets into a single set. A gate may
  mix guard kinds that cannot fold into a set. Such a gate raises, and no pass steps over it. An `EndOfStreamGuard`
  holds no set, and a `LiteralPeekGuard` holds a run.

- The expansion inlines a call into the way that makes the call. The expansion inlines where the call site gains from
  the inlining. The call site decides by the ways the inlining would leave. The input to the site decides nothing. The
  expansion drops a way that no guard gates. The expansion puts the callee's ways at the call site. A site whose ways
  come out ungated keeps its call. An inlined site therefore lowers the count of ungated ways. That count gives the
  expansion's walk a floor. The walk runs until a pass inlines no call. A pass judging a site on its input writes a way
  out. The pass counts on a later pass to gate that way. A walk resting on that expectation has no floor. A round
  composes a fresh state for such a way to continue at. A pass therefore finds a new site to write out, and descends. A
  bound on the passes would guard against that descent. A refused circle is another such guard. So is a state named by
  its contents. Such a guard patches a rule that was wrong. `every-conditional-way-is-gated` reads the count where a
  phase ends. That phase runs the expansion, then hoists to the callers and merges, then runs the expansion again.

- The sweep's duplicate-merge reads a body once instead of once a round. A pair of productions behave alike where their
  bodies match. A reference reads as the group of the production it names. The merge refines a partition of the
  productions until a round splits nothing. A round had written a body out afresh with the group numbers substituted in.
  A round moves a name between groups, and the name keeps its position in the body. The sweep therefore builds the
  body's shape once and takes the names out. A round compares a tuple of group numbers against that shape.
  `check_normalize` runs faster.

- The gate reads the crossing table's values. `GUARD_CROSSES_ACTION` answers `True` or `False`. It may answer nothing at
  all. An unnamed pair refuses the move, and the table records that the pair came up. A walk then cannot mistake "No"
  for "not yet". The tree called `unnamed()` and `unconsulted()` at no site. The table recorded the difference, and no
  site reported it. A walk took an unnamed pair for a worked-out refusal. The gate then named `LookGuard` in front of
  `OpenWindow` at once. That pair held a flow mapping's implicit key ungated. The table answers that pair a line above.
  A window bounds the characters a committed consume may take. A window bounds no lookaround. The gate also named cells
  nothing asks. Those cells are gone.

- **A conflict that a continuation decides has to hold that continuation.** `every-conflict-is-a-tail-call` counts the
  call sites. At such a site a way enters a production the machine cannot steer through. A continuation sits behind that
  call. A decision the position cannot settle falls to the input after the call. A parse can look at the input after the
  call only from inside the call. A way that calls a production and then continues holds the continuation in its own
  frame. The call cannot reach that frame. The machine therefore makes the choice in front of the input that decides it.
  That early choice is the backtracking `every-choice-way-is-different` counts. The backtracking happens a frame up.

  A way ending in a conflict is a conflict too. A way ending in a call hands its own end to the callee. A call the
  machine cannot steer through is therefore a way it cannot steer through. The production holding that way is untellable
  too. A call reaching that production at another site does not spread the conflict. A call with something behind it
  comes back. The return lands among the way's own parts.

  A single checker, `_conflicted_ways`, says which ways conflict. A pair of questions ask it.
  `every-conflict-is-a-tail-call` counts the call sites of the productions holding those ways.
  `every-choice-way-is-different` counts the ways themselves. `every-conflict-is-a-tail-call` belongs to no phase. The
  invariant therefore sits in `OWED` beside `every-choice-way-is-different`, and the gate reads its count at the end.

- **A pair of ways that do the same thing are not a pair of ways to tell apart.** A pair of invariants counted the ways
  of a choice. Both compared the states a parse enters a way in. Neither asked whether the pair *do* anything different.
  A machine takes the first way whose gate holds and does not come back. A pair of ways that differ only in their gates
  is therefore a single way. The parse reaches that way by a pair of routes. The same actions run and the same calls go
  out under either gate. `s-separate-lines` reaches its flow prefix by a pair of such ways. The first way runs at a line
  start. The other way runs at the end of the stream. The input decides nothing between the pair. The pair of gates
  cannot become a single gate. A gate holds where its guards hold together. The pair of ways wants a gate that holds
  where either gate holds. `_does_the_same` asks whether a pair of ways does the same thing.
  `no-choice-ways-partially-overlap` and `every-choice-way-is-different` both ask that question before counting a pair
  or cutting one.

  The split also fixes a bug in `split-overlapping-ways`. The step made pairs of ways that are alike in a part, and the
  grammar holds no such pair. A pair of ways can differ only in their gates. Another way that half overlaps such a pair
  cut both ways of the pair. Both ways then gave a piece at the atom where the gates of the pair overlap. The pair of
  pieces had the same gate and agreed in a part. A single piece is the way, and the other piece repeats that way. The
  step writes out a single piece.

  `every-choice-way-is-different` **falls**. A way among them was no conflict, and the rest of the drop was the step's
  own duplicates.

- **A count measures the backtracking that remains. A parse enters a way in the states of a way behind it.** The steps
  run in front of the count. Those steps gate the ways and leave no pair that half overlaps. A pair of ways of a choice
  can still overlap in full. A pair overlaps by entering in exactly the same states. The input does not decide between
  such a pair. A machine reads a character and asks a question of that character. The machine gets the same answer on
  either way. The machine takes the way that comes first. The machine backtracks where that way was wrong.
  `every-choice-way-is-different` counts the ways the machine may backtrack out of. A parse may enter a way and a way
  behind that one together. The count holds an entry for the way in front. The count holds no entry per pair. The count
  is high in the block-indented flattening and in the stream's document loop. A parse enters a way of a choice
  somewhere. The count therefore measures ambiguity. It does not measure ways nothing reaches.

  A rise in the count shows the split doing its work rather than adding work. Cutting a way by the atoms of its choice
  turns a pair that half overlapped into pieces that are apart or the same. Such a pair asked the machine to tell a
  state inside the overlap from a state just outside it. After the split the pair becomes a pair of ways the input
  cannot tell apart. The work did not grow. The work became a kind the determinizing step knows how to answer.

  `OWED` comes back with this entry. `OWED` had gone out empty a change ago. An invariant with a test but no phase
  counts at the end beside the invariants the steps name. `every-choice-way-is-different` is such an invariant.

- **A parse enters the ways of a choice in the same states, or in none of the same.** `no-choice-ways-partially-overlap`
  is **settled**. `split-overlapping-ways` refines the states of a choice into atoms. The atoms do not overlap. The
  atoms together cover what the ways cover. A way becomes a way per atom inside it. A piece keeps the gate of its way
  and gains the guards naming its atom. A state that admitted a way admits a piece of that way, and the split loses
  nothing. A pair of atoms is apart or the same, and a pair of pieces is apart or the same. The pieces take the site of
  their way. The parse falls where it fell before the split. The choice's else takes no part. The else has no gate, and
  the parse enters it wherever the ways in front did not fire. An atom holds no state of the else.

  The step mints guards the grammar could already write. `_COMPARES` gains a pair of rows. `0 < <column>` is for a
  position off a line start. `n <= len(match)` is for a consumed length no shorter than the indentation. Both use
  comparison kinds that already exist. The interpreter therefore runs those guards unchanged. The step synthesises a
  gate and then checks the gate it built. `_admits` reads the guards back. The guards and the way together must admit
  exactly the atom. The step raises on an atom its guards cannot state. The step approximates no piece.

  The design did not foresee what follows. A subspace coalesces the byte that begins no character with codepoint `0`,
  where a `CharSet` keeps them apart. An atom turned back into a set therefore dropped a run of characters. A later pass
  had to tell the pair apart again. An atom pins an axis its answers agree on. The atom also pins an axis that another
  pinned axis already forces. `n <= 0` says `n <= len(match)`, and a length is not negative. The step therefore builds
  the gate an axis at a time. The step keeps an axis that narrows the gate. Without that test, the split would ask
  questions that the answers already force. The step may narrow a way to fewer characters than its take holds. The step
  then narrows the take too. A take of a character names the atom's set. The step peels the first character of a run off
  into the gate. A separate state holds the remainder of the run. The gate and that state say a single maximal run. Such
  a run takes at least a character, and stops where its set stops.

  Splitting also exposes a way a parse enters and then fails on any input. The gate asks for an indentation of none, and
  the way calls a rule that wants the consumed length shorter than that. The parse entered the same way before the
  split. The parse then went on to the next way. The path into that rule said nothing about the indentation, and no
  checker could therefore see the fault. `_does_reach_nothing` says which piece to drop. The step drops that piece
  rather than writing it out. A piece like that takes nothing. Its callee offers no way the piece's states can reach.
  The machine would make that call, and the call would fail.

  The grammar **widens slightly**, and `merge-gate-peeks` runs again behind the split. A piece's gate gains the
  characters its atom holds, beside the characters the way already asked. A pair of questions about a character are a
  single question. A set saying them together makes that plain.

- **A gate asks about a character. A literal is therefore the characters it holds.** `fold-literals-into-gates` said a
  run of single characters as a `LiteralPeekGuard`. `---` became a comparison rather than a state per character, and the
  parse decided it before consuming anything. A literal's subspace is its first character, where the guard asks for
  several. A single state is no place to ask about a later position. The space therefore could not put
  `LiteralPeekGuard` exactly. That gap would have kept `split-overlapping-ways` from settling
  `no-choice-ways-partially-overlap`. Such pairs partially overlapped, and a way in such a pair began with a literal.
  Splitting such a pair by subspace would make a pair of pieces that *read* identical. A piece really wanted `---`, and
  the other wanted `-`. Settling such a pair wanted a negated literal peek. Such a peek needs a new kind in the
  interpreter and in the crossing table. It needs a total question over guards too. The space cannot hold the literal of
  such a peek, in the invariant or in the step.

  The grammar says a literal a character at a time. A state of such a literal asks about its own position.
  `fold-literals-into-gates` comes out. `LiteralPeekGuard` and `ConsumePeekedAction` come out with it. The fold built
  both. Their `then` and `barrier` fields were already dead. The fold had passed `None` throughout.
  `mint-consume-states` runs to a fixpoint over a literal. `mint-consume-states` cuts a way at its second take, and
  mints a state for the rest of the way. A third take goes to that state. A longer run takes a round per take.

  The change costs productions, distinct character sets in gates, and lookaheads. The change does not touch the decoder.
  The decoder builds from the authored grammar rather than the normalized one. The change gives up deciding `---` before
  the way consumes a character. The way takes `-` per character, finds no second dash, and hands the character back. The
  literal `---` hid that backtrack rather than removing it. A determinizing step has to settle that backtrack either
  way. In return, a gate refuses a guard the grammar holds at a given answer. A count shows that refusal. An assumption
  shows nothing. A reader therefore reads a pair of ways apart or together, and guesses at none. `_admits` names the
  kinds of guard that could break the count. A peek that pins no set is one kind. A look-behind at a set other than
  `ns-char` is the other. The tree holds neither. The count itself does not move.

- **A break is a line feed, or a carriage return that a line feed may follow.** The official grammar's `b-break` is a
  way apiece for carriage return with line feed, carriage return, and line feed. A carriage return opens the way for
  carriage return with line feed. A carriage return also opens the way for a lone carriage return. A parse at a carriage
  return can tell the pair apart only by taking the longer way and handing it back. A rule that consumes a break
  inherits that hand-back. In such rules, a pair of ways sat on `CRLF`. A way of the pair wanted both characters, and
  the other way wanted the carriage return. The shorter way sat nested inside the longer way. A subspace sees a literal
  as its first character. The pair therefore read identical to a subspace. The rule `LF | CR LF?` gives the same matches
  with nothing to back out of. The forms match the same inputs. `check_vendor_spec` declares the change beside the other
  deviations from the official BNF. The change to `c-b-block-header` has the same shape.

- **A pair of ways can overlap half apart, and that overlap is no use. The count runs per way.** A parse enters a pair
  of ways apart, together, or half apart. The first pair are of use. Apart, the input says which to take. Together, the
  input says nothing. Something else must decide. Half apart is the shape that is no use. A machine has to tell a state
  in the overlap from a state just outside it. The machine then asks a pair of questions where a single question would
  do. `no-choice-ways-partially-overlap` reports a fault per way that half overlaps a way behind it.
  `every-conditional-way-is-gated` counts ways rather than the pairs they make. The new count reads a way as
  `accepted-and-gated-charsets-are-equal` reads a gate. It crosses the way's own gate with the questions the paths into
  its production ask. It takes those paths together. A choice between more than a single way holds pairs apart, pairs
  half apart and pairs together.

  The invariant belongs to no step, and `OWED` holds it. The gate counts that invariant at the end beside the invariants
  the steps name. Nobody writes that invariant into a document by hand. `spaces.SubSpace` gains a difference. A
  difference answers on the states it holds. The union and the intersection already answer that way. `_decided_ways`
  names the ways a choice decides between. The last way is the choice's else. `_decided_ways` states that rule once.
  This invariant and `every-conditional-way-is-gated` both call `_decided_ways`.

- **A comparison of a pair of the parse's own quantities is an axis. A quantity of the parse is not an axis.** The
  indentation and the column are integers of no fixed range. So are the length of the run just measured and the block
  scalar's floor. The space therefore held no coordinate for such a quantity. The space read a comparison between a pair
  of those quantities as admitting under any answer at all. A guard admitting that much says nothing. A guard reads no
  quantity. A guard asks how a pair of quantities compare, and the grammar asks a fixed set of such questions. The axes
  are therefore those questions. The magnitudes stay out of the axes. **A gate refuses a guard at some state.** The
  space had read such a comparison guard as admitting at any state.

  Free booleans would admit states no parse is in. An ordering is transitive, and `f <= n` with `n < column` settles
  `f < column`. The enumeration therefore comes from the quantities rather than an assignment. The enumeration lists the
  orderings integers make. The facts below restrict those orderings. A line start is column `0`, and anywhere else is at
  least `1`. The interpreter says that outright, and sets `is_sol` to `column == 0`. The indentation reaches `-1`. A
  parse enters the root with an empty stack. A length is not negative. A consumed length falls within its line, and is
  therefore at most the column. A site that reads the consumed length measures `s-space*` under a `(token): indent`. A
  space is not a break. A break would reset the column under the site. A space is not a byte-order mark either. A mark
  would take no column of its own. The byte-order mark halves the orderings at a line start.

  A pair of further facts bound the other axes. An `ns-char` character sits directly behind a line start at no point in
  a parse. Behind a line start comes a break or a byte-order mark. A line start may have nothing behind it. A run that
  took its whole limit leaves the parse mid-line. A limited run in the grammar takes spaces or hex digits, and a consume
  takes at least a character. **The enumeration yields an answer per ordering the facts allow.** The pipeline runs no
  slower. Interning the `Characters` sets spares the algebra a cost per answer. `check_spaces` judges the answers in
  both directions. The checker walks the quantities over a wider range than `spaces` does. An answer no parse reaches
  shows a fault in the enumeration. A state no answer names is a hole. A subspace says nothing about such a state.

- **A `SubSpace` holds a single `Characters` per distinct answer.** The cost of the algebra then follows the count of
  distinct sets rather than the count of answers. A `SubSpace` held a `Region` dataclass per answer it admitted anything
  under. The code sorted the regions into a canonical tuple. A dict built beside it gave the index. An intersection
  therefore built an object per answer, a dict and a sort. An answer added would have paid those costs again. The sets
  sit by position and come out of a table. Equal sets are the same object, and equality is identity. The table remembers
  the result for a pair of sets. The distinct sets are a small table. The pairs that overlap and the pairs that join are
  small tables too. The code had done the arithmetic over and over.

  An intersection is **7.0us where it was 78.9us**. A union is 6.9us where it was 72.3us. The leaf invariants read their
  tables in 0.4s where they took 1.1s. The speed is not the gain. The answers are free to grow with the questions the
  guards ask. A space may state a comparison between a pair of the parse's own values. Such a space needs that growth.
  `check_spaces` tests the table. The check writes a set of states in differing forms. A form lists the spans out of
  order. A form cuts a span in half where the halves close up. A form repeats a span. The check asks that the forms
  yield a single answer. A table keyed on the input compares forms rather than sets. The key has to be the set the input
  denotes. A table keyed on the input would read a pair of forms of a set as a pair of different sets.

- **A comparison takes its name from the comparing. A column has no part in that name.** `ColumnLtGuard` and
  `ColumnLeGuard` hold a pair of values and assert an order between them. Where a guard mentions the column, the column
  is not the left operand. The other guards mention no column. They relate `n` and the block scalar's floor. They also
  relate the length of a match and the literal `0`. The docstrings said "the first indentation is less than the second",
  and that wording ties the comparison to an indentation. Some of the shapes name neither an indentation nor a column.
  The shapes take the names `IsLessThanGuard` and `IsLessEqualGuard`. The grammar writes the shapes `(<)` and `(<=)`.
  The pair of `_admits` handlers are `_is_less_than_admits` and `_is_less_equal_admits`. A handler takes its name from a
  shape the handler answers.

  The crossing table said a comparison crosses a `SetVarAction`. The table reasoned that a comparison names the
  indentation, a literal, or the length of a match. That reasoning misses a case. `f <= column` and `f <= n` name the
  floor. A `SetVarAction` writes a pair of slots, and the floor is one of them. The grammar holds no instance that
  collides, and the grammar was therefore right. The crossing table still licensed an instance that would collide. The
  same shape crashed `b-l-folded` when a step moved a turn's close past a code's pop. The gate asks whether the
  comparison and the write in hand collide. `_does_read_nothing_the_write_writes` reads the operands for the slot.
  `_is_using` cannot answer that question. `_is_using` looks for a `ParamValue`, and a read of `f` is a `GlobalValue` by
  that point.

- **A turn that closes where it opened takes a path no input takes, and the path goes.**
  `every-conditional-way-is-gated` is **settled**. The invariant reported faults. The ways still faulting shared a
  single shape. A path opens a turn that must take a character. The path then reaches the close of that turn. The open
  and the close share a `pair`. The ways between the open and the close take no character. The close asks whether the
  parse took anything since its own open. On such a path the close therefore refuses on any input. The way holding the
  path fails there and falls to the way behind it. Once the step drops the path, the close refuses sooner. The step
  drops the path rather than cancelling it. The grammar wants the turn to refuse on that path. A step that annulled the
  open against the close would make the path *succeed*. A run over a body matching empty would then spin.
  `l-yaml-stream` wraps `l-document-prefix`. Both halves of `l-document-prefix` offer an empty way. `l-document-prefix`
  holds the shape. `("EndMustConsumeGuard", "StartMustConsumeAction")` is gone from the crossing table. The paths that
  asked it are gone as well.

- **The pair in hand settles a crossing the kinds cannot answer.** A write names the text that may not match at a start
  of line. Whether a lookaround may come in front of that write depends on the lookaround and on the set. The lookaround
  matches its item through that refusal, and reads the set in force at that position. A lookaround's guard admits a set
  of states. The forbidden text can begin taking a character in another set of states. Where the pair of sets do not
  overlap, the guard answers alike on either side of the write. A `_Crossing` entry may name a question rather than an
  answer. `_does_read_none_of_the_forbidden` is such a question. It crosses `_admits` with the accepted space. An entry
  naming a question sends the walk to work the pair out, and the instance decides. Such an entry therefore commits
  neither fault of the table. `l-explicit-document` forbids `---` and `...`. The accepted space of that forbidden text
  is `-` and `.`. The ungated way of `l-explicit-document` reaches gates asking about a space and a tab. The other gates
  ask about a carriage return, a line feed and a `\r\n`. Those are disjoint from the accepted space. The write therefore
  cannot change an answer of those gates. `every-conditional-way-is-gated` **falls again**. The ways left share a shape.
  In that shape, a turn's close sits behind its own open. In front of that open, the close finds nothing to take off the
  stack.

- **A way nothing has gated becomes the paths it holds.** `flatten-ungated-call-trees` walks such a way through the
  calls *and* the continuations. A path reaches a gate. A path runs out at no point, circles at no point, and ends on a
  body that is a choice. A way holding no call is not the end of a path. The innermost pending continuation runs next,
  and that continuation holds the gate. A walk that stopped at the missing call reported such ways as paths reaching
  nothing. A path becomes a way. The way holds the gate the path found. The way also holds the actions the path
  performed in front of that gate, and the call at that gate. A chain of states follows the call and runs the remainder
  of the path. A path ends on a leaf, and the chain runs that leaf's own continuation first. The pending continuations
  run after the leaf's continuation. The innermost pending continuation runs first. A leaf that only continues gives the
  free slot to the head of that chain rather than to a state deeper. The first call a way makes asks a question about a
  limited run. By a state further on, earlier calls have taken part of that run. `every-conditional-way-is-gated`
  **falls** over the whole grammar.

  A path left over reaches a refused crossing in a pair of ways. A path may reach a gate asking a lookaround behind a
  `SetForbiddenAction`. A path may instead reach a turn's close behind that turn's open. A path's gate holds a single
  guard, and that gate does not split. A split gate would ask the guards that may come up at the gate's own state. A
  deeper state would ask the remaining guards. A split gate decides a way on part of its question. The split has no case
  in this grammar and no code in the tree.

- **`expand-called-ways` is gone. The pair of steps that served it go too.** `expand-called-ways` wrote a callee's ways
  into the call site. The flattening writes a callee's ways into its caller too. The count lands the same either way.
  `expand-called-ways` made the grammar smaller and narrower. It also hid a defect. `hoist-guards-to-callers` breaks the
  corpus without the expansions. `merge-gate-peeks-2` had nothing left to merge. `every-gate-looks-ahead-at-most-once`
  holds after `hoist-guards-to-callers` runs. The pipeline adds no step in place of the steps that came out.

- **A turn's close takes its own open off the stack. A code above that open refuses the question.**
  `GUARD_CROSSES_ACTION` let an `EndMustConsumeGuard` cross a `PopCodeAction`. It reasoned about the parse position, and
  a code is neither the input nor a count. The guard *pops*. The parse opened a code in front of the guard and closed it
  behind. A guard asked early reaches for the open, and finds the code. `hoist-guards-to-callers` moved a guard into a
  gate in front of such a pop. `b-l-folded` then crashed. `GUARD_CROSSES_ACTION` says `False` for an
  `EndMustConsumeGuard` in front of a `PopCodeAction`.

  The table also names pairs that no question reached. The first is a comparison in front of a `SetVarAction`. A
  comparison reads the indentation, a literal or a match's length, and not the pair of variables the parse holds. The
  other is a `StartOfLineGuard` in front of a `PushIndentAction` or a `SetForbiddenAction`. Neither action changes where
  the parse is in its line. The table drops the pairs that only the expansion asked. The table lists the pairs the
  grammar holds.

- **A parse hands back no action. A guard refuses.** At a refusal, a choice goes on to its next way. A gate that
  admitted an action lets the action do its work. An action that fails after its gate admitted it shows the gate lied. A
  gate that lied is a crash and not a parse. `_can_be_refused` said exactly that in words, and its table said otherwise.
  The table listed the consumes among the kinds an input can hand back. The rewritten table answers by kind. An action
  answers no. A consume answers no. A guard answers yes. A set where a match belongs answers yes. Such a set is a match
  rather than a consume or a guard.

- **A consume holds the set it consumes, and consumes that set.** `ConsumeCharAction` meant "the character the gate
  found". A gate hoisted to a caller took the consume's meaning with it. A reader then had to work out the set a way
  consumed from the guard in front of the consume. A consume holds its own `set`. The interpreter can then ask of a
  consume what it actually did. An interpreter that probes the set again gets a prediction rather than the event. The
  interpreter asked that question at a stage and over the whole corpus. It found **no consume that consumed nothing**.
  The grammar writes a run of a class as a pair. A gate finds the class, and a consume sits beside that gate. The
  consume therefore consumes. `ConsumeSpanAction`, `ConsumeLimitedSpanAction` and `ConsumeTrimmedSpanAction` join
  `ALWAYS_CONSUMES`. `_does_scan_read` recovered that fact from the gate. The change deletes `_does_scan_read`,
  `_ahead_of_gate`, `_ahead_of_any` and `_ahead_of`. The change deletes `_narrowed_ahead`, `_entering_guards` and
  `_spans_meeting` too. Both consume splits go too.

- **`ConsumeLiteralAction` is gone.** The generator constructed no such action. The history of the repository holds no
  call to `ConsumeLiteralAction`. `ConsumePeekedAction` consumes the literals the grammar writes. The grammar writes
  `---` and `...`, and the `YAML` of a directive. A `LiteralPeekGuard` guards such a consume. `ConsumeLiteralAction` was
  a kind a question had to cover. An input could reach that kind at no point.

- **The accepted subspace and the gated subspace come from different halves of a way.** `_accepted_way` intersected the
  way's own gate. `accept <= gate` then held by construction, and the accepted subspace could not disagree with the
  gate. The accepted subspace reads consumes and calls only. The gated subspace reads guards only.
  `accepted-and-gated-charsets-are-equal` compares the union over the paths into a production, rather than a single
  path. `l-folded-content` decides whether a space sits at the parse position. Its pair of ways continue into the same
  tail. The branch that consumed a space knows nothing about the next character when it reaches that tail. The branch
  that found none still knows there is no space. A path is narrower than the set the tail consumes. The union of the
  paths admits exactly that set.

- **A leaf way takes what the parse entered it on, and a path reaches such a way.** A way holding no call answers by its
  own actions. The parse enters such a way in a space. The way accepts another space. An invariant checks that the pair
  agree before the pipeline moves anything. `_asked_where_entered` already gave the guards asked along a path into a
  production. `_asked_where_entered` gathers those guards from the last take onward. A parse may enter a way in the
  states that both those guards and the way's gate admit. A pair of invariants read those states.
  `accepted-and-gated-charsets-are-equal` compares answer by answer instead of whole subspaces. The way may ask about
  the character. A caller may also know that the parse sits at a line start. The caller then knows more than the way
  asks. The caller and the way do not disagree. The leaf ways that take a character agree with their paths at an answer.
  `every-path-reaches-a-leaf-way` asks for a single leaf way rather than for the whole set of leaf ways.
  `l-document-prefix` offers a way that takes a byte order mark and a way that takes nothing. The path reaching
  `l-document-prefix` at the end of the stream can take only the way that takes nothing. An invariant asking for the
  whole set would call that path a fault.

- **The subspace algebra stops being the cost of asking.** Reading both invariants at a stage put `_accepted_spaces` on
  the counting pass. There it tripped the watchdog that refuses a slow invariant. The shape was right throughout.
  `under` scanned the regions for a point where a lookup answers. The code computed an intersection as the complement of
  a side subtracted from the other. `chars.intersected_spans` merges the pair directly. The fixpoint swept a production
  on a round. A production's answer can change when a callee's answer has changed. A worklist holds the unvisited
  productions. The merge and the worklist take the invariant back under the watchdog. The invariant gives an unchanged
  answer.

- **`<fail>` names a match no input makes.** A `(case)` on a finite parameter listed values and said nothing about the
  values outside the list. The spec reads silence as declining those values. An absence then said what a production did
  under such a value. An absence cannot tell "It matches nothing" from "nobody asks". The specialization made that
  silence an alternation of no ways. A walk past that alternation then had to read an emptiness as a refusal.
  `_split_ways` reported `l-recover-entry` as having no empty way. Such a report holds for a production with no ways at
  all. A reader of the report takes the opposite meaning. `<fail>` is the twin of `<empty>`. Any input makes `<empty>`
  by taking nothing. `<fail>` matches no input at all. The cases that were silent name a value of their parameter.
  `validate_grammar._check_total_cases` refuses a case that names no such value. The specialization raises where a value
  has no branch. It mints no emptiness.

- **The ways nothing takes come out, and no later phase sees such a way.** `prune-failures` is a phase of its own behind
  the specialization. The phase can go no earlier. A `<fail>` is a branch until the specialization runs. A branch does
  not yet say what the production does. A choice drops the ways nothing enters. A choice left with no way becomes a
  `<fail>`. A run holding a `<fail>` matches nothing. A call of a production that matches nothing matches nothing. A
  recovery whose handler nothing enters is no recovery. The cut goes on unwinding to the handler above, as it did with a
  recovery that matched nothing. A commit keeps what sits under it. A refusal there is the error the commit names, and
  not the error of the choice. The specialization writes a single decline, and the phase writes none. The grammar loses
  the productions nothing could reach, and `no-fails` reads none from there on. The phase dropped the dead recovery.
  Without the dead recovery, a non-recovering policy keeps its compact mapping. That mapping stays apart from the
  mapping of the indentation-bounded policy. The separate mappings showed that the corpus held no `r=i` fixture with a
  compact mapping. The merge had hidden that hole. `l-yeast-stream.recover-compact.r=i` closes it.
  `generator/regen_fixture.py` wrote that fixture. A fixture's stream comes from the interpreter. An indent or a white
  token holds trailing spaces that a writer typing the fixture by hand loses.

- **A guard names the states in which it lets a parse through.** `normalize._admits` reads a guard as a
  `spaces.SubSpace`. `_accepted_spaces` says where a production can begin taking a character. The walk over a way
  collects states until the way takes a character. A guard past a take asks about a later position. Such a guard narrows
  nothing. `normalize._admits` is sound rather than decisive. A comparison between a pair of the parse's own values
  fixes no coordinate and admits throughout. A literal's first character is a constraint. The rest of the literal is a
  residual, and the axes of the subspace do not speak for that residual. A gate may admit a state the space refuses.
  Such a state is a hole in the grammar. Apart from the indentation comparisons, the guards in the final grammar name a
  coordinate. The indentation comparisons relate `n`, the column and a match's length to the floor. `_split_ways` still
  answers whether a way can take nothing. A production that succeeds taking nothing succeeds anywhere. `_split_ways`
  keeps that answer out of the subspace. The answer then poisons no caller through the fixpoint.

- **A subspace says which states a parse can decide in.** A guard asks about an axis of a small space. A pair of axes
  are the character in front and the character behind. A further pair says whether the parse sits at a line start and
  whether it sits under indentation. A pair of bits of the parse's own bookkeeping make up the remaining axes. A parse
  sits at a single point of that space when it decides. A gate admits a subset. A way takes a subset. A call site can
  reach a subset. `spaces.SubSpace` is that subset. It holds the characters admitted under an answer. An axis is finite,
  and the subset is therefore exact. `spaces` computes union, intersection and containment answer by answer. The end of
  the stream is the character axis's own value, and not the absence of a character. A way entered there is therefore a
  way entered somewhere. A comparison relating a pair of the parse's own values is no axis at all. A guard asking such a
  comparison constrains nothing. `check_spaces` judges the algebra by the states a subspace holds. It compares the
  operations against set arithmetic. The arithmetic runs over an enumeration of the answers. It also runs over an
  alphabet. That alphabet holds the characters on both sides of a boundary a case names.

- **The span algebra lives in a single place.** `normalize` had its own copy of merging and subtracting codepoint
  intervals. That copy matched the code in `chars` character for character. The character model behind the decoder
  already keeps that code.

- **Nothing a way performs can fail.** A counted consume took `n` characters of a class or none at all, and no gate
  could protect it. A gate speaks for the character in front of it. It does not speak for `n` characters. A consume
  could ask for more characters than the input held. Such a way failed as it consumed, rather than at a guard. The
  counted consumes were such ways. `x{n}` is a run of up to `n` of the class. The run takes the characters the input
  holds. The guard behind the run asks whether the run reached the limit. That guard refuses a parse as any other
  question does. `ConsumeCountedSpan` is gone from the IR. The pipeline already writes a run and a guard for `x+`. The
  run there is a consume that may take none. The `LookGuard` in front of such a consume makes the consume take a
  character. The split on the count happens where the pipeline mints the run and its guard.
  `split-counted-spans-on-the-count` gated the consume afterwards, and is gone with the kind it gated.

- **A question names the kinds it leaves out, and says whether those kinds have an answer.** `ir.Question` took `NEVER`
  for a kind the checker does not reach. A wide group named such a kind. That claimed an impossibility nobody had
  proved. A pair of lists replace `NEVER`. The lists differ in whether an answer exists. `untested` names a kind a
  family covers. A run has yet to ask about such a kind. The family answers a kind that arrives. The checker records the
  kind, lists it, and fails the gate. The gate forces a decision. `unknown` names a kind no family covers at all. A
  question raises on such a kind. The checker refuses `untested` on a kind no family covers. Splitting the existing uses
  sent the covered kinds to `untested`. `unknown` names `ChoiceState`. That kind has no answer at all.

- **A run records the productions it reaches.** The gate collected coverage by rebinding `interpreter.match` and
  `interpreter._evaluate`. The rebinding missed a handler reaching a production any other way. The report then read
  exactly like a covered one. A run fills an `interpreter.Coverage`. The run writes an entry where it enters a
  production and where it hands a production back. The gate reads that record. A handler cannot route around that
  record. The same reasoning retires `interpreter.match`'s own dispatch chain. That chain is a table over the kinds
  rather than an `ir.Question`. A caller reaches a question through the question's type. The matcher recurses once per
  grammar step. The C stack bounds that recursion.

- **A single place names the families of node kinds.** A list of kinds lived beside the checker that used the list. A
  family then had a list per checker, and the checkers left the lists uncompared. `ir.py` holds the lists in a single
  section. An author read the lists together and found a pair of families that did not match their own words. The
  consume family left out the trimmed consume. The family's words cover a consume of a character class that may take
  none at all. The zero-width family left out the gate's literal form. That form matches without consuming.
  `validate_grammar` patched around the missing form inline. `TAKES_CHARACTERS` narrowed `CONSUMING` to the forms a
  phase uses. That narrowing made a way holding a `OneCharSet` count as taking nothing. `ir.py` holds no
  `TAKES_CHARACTERS`. A family's name says which category its kinds come from. `ZERO_WIDTH` becomes
  `ASKED_NOT_TAKEN_NODES`. An action and a guard both take nothing. The kinds in that family hold characters that a
  parse asks about and does not take. Those characters set that family apart from an action.

- `no-empty-nodes`, settled by `build-alternatives`. A way is a gate, a list of actions and the calls that way hands
  control to. An empty match is none of them. A way matching the empty input is the way with no gate, no action and no
  call. The machine's own words have no place for such a way. The lowerings mint such ways freely. The last such ways
  come out where `build-alternatives` builds the ways.

- **A kind's name says the category it is in.** The categories are an action, a guard and a call. A wrapper, a character
  set and a tree the lowerings remove are categories too. So are a state the machine has, a value the parse works out
  and a part a node holds. A kind's name ends in the category it belongs to. A reader learns the category at the use
  site. The reader does not look it up in the families in `ir.py`. `CharSet` and `Wrapper` keep their names. Both names
  already end in a category. `Char` becomes `OneCharSet`. That set holds a single character. The kind holding spans
  already has the name `CharSet`. `GUARD_CROSSES_ACTION` keys its pairs by `type(node).__name__`. The rename covers the
  names in that table along with the classes. A table left with the old names would name no pair. `GUARD_CROSSES_ACTION`
  refuses to move a guard across an action for a pair the table leaves out. The table also refuses that move for a pair
  it rules out. The step-changed-nothing assertion caught the refused move.

- The pipeline establishes `every-span-question-follows-its-run` where it mints the run and the question about the run.
  A guard asks that question. The guard reads the result of the action in front of it. That action has to be the run.
  The run is the item before the guard among the things a way performs. The pipeline may put the guard at the head of a
  state as it mints the guard states. The run is then the last act of a way that calls that state. That call is the
  first call the way makes. A way may make a call after another call. The parse enters the later call where the earlier
  call left off. The callee has by then taken away what the run did. The interpreter already refused a guard cut off
  from its run at the point of the cut. The invariant asks the same question of the grammar. A step that moves the run
  and the guard apart is a fault at the point of the move.

- **A question about the grammar is an `ir.Question`.** An audit found questions that an `isinstance` chain answered.
  The audit passed over a membership test that names a family. A step transform answers differently per kind. A step
  transform reshapes the kinds it names and passes any other kind through. Such a transform is a rewrite rather than a
  question. The list below holds the questions.

  - `validate_grammar.consumed`, what a node takes and whether an annotation covers it.
  - `check_grammar_docs.emitted`, the codes a node emits.
  - `normalize._peeked_question`, the question a peek asks.
  - `normalize._reached_forbidden`, what is forbidden where a match ends.
  - The walker inside `every-character-question-is-a-character-set`.

  A walker in the list above may answer by `yield from` or by appending rather than by returning. A search for a chain
  of returns does not find such a walker.

  The peek's chain was the chain that mattered. The tail of that chain handed the node back as its own question. A shape
  nothing had looked at then said "ask about this", and the readers believed it. Those readers were `lower-runs`,
  `span-consumes` and the character-set invariant. The remaining chains ended in `elif isinstance(node, ir.KINDS)`.
  Their own docstrings said a kind the arm leaves out raises. The arm already names the whole set of kinds. That raise
  is unreachable. Both read the vendored grammar before any lowering. They therefore call the canonical forms untested.
  They do not walk into those forms by default.

- `ir.py` names the categories as families. This change adds the families `TREES`, `STATES`, `PARTS` and `CALLS`.
  Together the families cover the kinds exactly. A question naming the other categories raises for a kind in no
  category. A question naming `KINDS` raises for no kind.

- A `Prod` is no kind of node, and `KINDS` leaves it out. `KINDS` is the kinds of thing that sit inside a body. A
  production holds a name, a parameter list and a body. A walk of a body reaches no production. `Prod` was the class
  that fell into no category. The checker refuses a question naming `Prod`. A table covering `Prod` covers a class no
  walk reaches.

- **A rule matched what the last consume took, and not the token the parser is building.** `(len): (match)` read the
  open token's length. The parser cuts a token wherever an annotation opens or closes. That length therefore measured
  the parser's own bookkeeping rather than the grammar's question. `(len): (match)` reads `consumed_length`. `StarTree`,
  `PlusTree` and `RepTree` set that slot on a consume. So do `TrimStarTree` and the `Consume...Action`s. A rule asking
  for the length gets the same answer before and after the lowering. `(atoi): (match)` reads the token, and that is
  correct. That rule wants the digits of a block scalar's indentation indicator, and `(token): indicator` wraps exactly
  those.

  A gate makes a consume take at least a character. A run of none or more can take no character. Such a run performs no
  consume. An earlier run leaves a length behind. Without a mark, a reader takes that length for the length of the empty
  run. `ConsumeNoCharAction` marks such an empty run. A way that takes a character sits under a gate on a set.
  `ConsumeNoCharAction` sits under a refusal of that set. `x*` is `[look x] consume x` or `[not x] consume-none`. A
  consume-none names no set. A consume-none leaves the same state behind however the way took nothing. A pair of
  consume-nones are a single action wherever they sit together.

- **A gate asks and does not write.** `EndMustConsumeGuard` took its own open off the parse's stack while answering. It
  therefore held refusals in `GUARD_CROSSES_ACTION` that another guard does not need. This change splits
  `EndMustConsumeGuard` into `DidConsumeSinceOpenGuard` and `EndMustConsumeAction`. `DidConsumeSinceOpenGuard` reads.
  `DidConsumeSinceOpenGuard` finds its own open by its pair. The parse may have pushed over that open.
  `EndMustConsumeAction` closes the region. `DidConsumeSinceOpenGuard` crosses a code's close.
  `DidConsumeSinceOpenGuard` keeps the refusal of a committed region. A guard that crosses a committed region holds that
  refusal too.

- **A parse enters the root at indentation `-1`, and its indentation goes no lower.** The grammar does compute lower. A
  parse enters `l+block-sequence` at `n - 1`, and enters a sequence at the root at `-2`. A reader answers alike for `-1`
  and for `-2`. `(<): [n, <column>]` reads such a value. It compares the value against a column, and a column cannot be
  negative. `interpreter._indent` answers no value below `-1`. A state of a parse then falls inside the answers
  `spaces.py` enumerates. `interpreter._indent` also refuses a null. The grammar writes a null where no indentation
  applies. `ns-flow-yaml-node: [null, c]` is such a place. A reader measures against a column there rather than against
  the null. The grammar states no such measurement.

- `ConsumeTrimmedSpanAction` asserts it consumed something, as the span and the limited span already do. The consume of
  a single character cannot take nothing, so that consume has nothing to assert. The assertion sits ahead of the step
  that would build such a consume. `KEPT_THOUGH_DEAD` declares that step dead.

- **The grammar says where a parse may sit, and real parses check that answer.** A production has an *entry space*. A
  parse may sit there when it enters that production. A production also has an *exit space*. A parse sits there when the
  production hands control back. `normalize._entry_and_exit_spaces` grows the pair together. The entry space and the
  exit space answer a single question. A production's entry is the space its call sites sit in. The ways making those
  calls leave that space. A way's exit space follows from the exit spaces of the productions the way calls. Entry grows
  downward from the productions a parse enters by name. Exit grows upward from the ways that call nothing. Both start
  from none, and they feed one another until neither moves. `_entry_and_exit_spaces` reads the pair for a parse that
  starts at the root. A caller may start a rule of its own. A seed naming that rule would answer a question about that
  caller. This change asks about the machine that ships.

  The computation rests on a transition per action. `normalize.after_action` says where an action leaves a parse.
  `spaces.reached_by` does that arithmetic once per action rather than once per site. `spaces._standing_at` saturates a
  quantity that runs past the enumeration. `_entry_and_exit_spaces` takes a space through a way action by action. That
  function does not guess the space at the ends of a way. `normalize.spaces_of_ways` then intersects a production's
  entry with a way's own gate. That intersection is where a parse enters the way.

  **`check_normalize._spaces_held` tests a computed space against real parses.** It runs the corpus through
  `interpreter.run(checking=...)`. It asserts that the parse really sits somewhere the computed space admits. The
  assertion runs at both ends of a way and at the far end of an action. A lookaround is no exception. The interpreter
  reports where it sits through `_standings_now`. `_spaces_held` catches a space computed too narrow. A checker reading
  only the grammar misses such a space. A space computed too wide passes `_spaces_held`. A step pruning by that space
  would show the fault. `PLAN.md` owes the cuts that would read these spaces. This entry does not claim them.

- **A look-behind reads the character behind the parse rather than searching for where a match could have begun.** The
  grammar's look-behind asks whether an `ns-char` sits behind the parse. `interpreter._try_look_behind` reads that
  character and matches the item against it. An item of more than a single character is **refused** by
  `_try_look_behind`. A search for the start of such an item would re-match input the parse has already read. The cost
  grows with the length of the item. The grammar asks no such question. The refusal limits the look-behinds the grammar
  may write. A stage of the pipeline keeps a look-behind within that limit.

- **A break character puts the parse at a line start, and the line count waits for the feed.** A carriage return that a
  line feed follows is half of a break. The feed counts the line. The carriage return does not count it. The carriage
  return is still no part of the line it ends. A column between the pair goes unread. A consume of a break character
  marks the line start. The space checker reads a break off the character a consume takes. The input decides which
  character follows a carriage return. The consumed character set does not decide it.

- **A consume takes at least a character, and `_shortest_match` says so.** `_shortest_match` read a span of none or more
  as taking nothing. The function read a run up to a limit as taking its whole limit or nothing at all. Both mistook the
  action for the way around it. A consume cannot fail. It takes what is there and says how much. `DidMatchFullSpanGuard`
  asks whether the run reached the limit. The guard asks past the run. The limited run was the **unsound** direction. A
  lower bound above the truth lets a reader conclude a match cannot take fewer characters than it can.

- `generator/review_input.py` prepares a change for review. It makes the joins and searches a reviewer would otherwise
  make by hand. The script prepares the items below:

  - The staged diff, split by the subject a question asks about.
  - A changed run of Python with its function, the docstring of that function in the tree and in `HEAD`, and the lines
    around the change.
  - A name or swept word the change removed, with the text the tree still says about it.
  - A number the documents state, beside the names the gate can measure.

  `.claude/workflows/pre-commit-review.js` asks the review questions of a change. `.claude/agents/reader.md` answers
  those questions. That reader has `Read`, `Grep` and `Glob`, and no shell. A reviewer that reaches for a shell has
  stopped reading the files the workflow prepared.

  `review_input` refuses a tree holding a loose path, and names that path. It refuses a staged path no prepared file
  would show. A vendored path is the exception. `review_input` reports line numbers from `git diff --cached`, and reads
  the file beside them. The line numbers and the file agree where the tree holds no unstaged change. Over a dirty tree,
  a reviewer would approve the bytes on disk rather than the staged change.

  An agent that refuses hands a workflow an answer naming no file. An agent may answer a workflow under a schema.
  `check_failures` requires such a workflow to ask whether the answer holds anything. A workflow that asks only whether
  a file exists lets a refusal through. Reviewers then read the files an earlier run left on disk. Their report covers
  that earlier change rather than the staged one.

  `.claude/settings.json` holds what a hook can decide from a single tool call.

  - The shell writes no file.
  - A question about the grammar is a total dispatch.
  - The tense says what the tree holds.

  The pre-commit review and the gates of `make pc` check the parts of a change that a per-edit hook does not see. A
  check that reads a whole diff belongs to a reader that holds the diff. Such a check may also belong to a gate that
  builds the tree. A hook that fires per edit sees neither the whole diff nor the built tree.

- `lower-continuations-into-conflicts` is in `normalize.py` and in no step of the pipeline. Over the grammar, that
  transform raises the fault count of `every-conflict-is-a-tail-call` **rather than lowering it**. A copy of a
  production holds the call sites of the original production. The transform therefore names no invariant, and the
  pipeline refuses a step naming none. A comment in `normalize.py` marks the place the transform's `Step` would go.
  `PLAN.md` names the work a step owes such copies.

  `lower-continuations-into-conflicts` lowers into a conflict decided from outside that conflict.
  `_productions_decided_from_outside` names such conflicts. That function runs to a fixpoint. A lowered conflict can
  make another conflict. The transform has to tell a copy from its original. `_is_alike_up_to_where_it_continues` raises
  where a pair of ways perform the same actions in a different order. The pair is not alike.

### Changed

- `ys_options.max_token_bytes` becomes `max_bytes`, and caps the memory the parser allocates rather than the bytes it
  buffers for a token. The buffered input grows, and the tokens the parser holds back grow with it. So does the parser's
  stack. Deep nesting grows that stack, and no quantity of input bounds it. A single cap bounds the input, the tokens
  and the stack together.

- `src/yeast.c` is gone. The library splits that file's code into files by topic. The version query and the load-time
  sanity check have a file of their own. So do the counting allocator, the stream adapters, and the yeast wire format.
  Allocation and the `max_bytes` accounting live in `src/memory.c`. The parser held a copy of that code, and the
  wire-format reader held another. The library refuses to build a wire-format reader whose cap is too small for that
  reader. The parser refuses the same way. `ys_resolved_options` says what a NULL `ys_options` means. That function
  names the defaults where it reads the struct, and a field repeats no default. A single function hands a reader over,
  and a single function tears down the resources an object owns. A failing constructor calls `ys_discard_reader`. A
  destructor calls `ys_teardown`. Both functions replace copies of the same close and the same errno handling. A caller
  hands over a reader whether or not the library can build the object that would read through it. A constructor that
  fails therefore closes the reader rather than leaking it. `ys_discard_reader` discards a close failure it has no
  channel to report. It holds on to the error the constructor is already returning `NULL` for.

- The reader of the yeast wire format tells a broken wire from the tokens a wire holds. The parser tells a malformed
  document from a valid document the same way. A wire that is not the wire format is a `YS_CODE_ERROR` token. The
  token's text names the fault in the wire. The token's marks are the line and column in the wire. The reader validates
  its input as the parser does. A byte that a conformant wire would have escaped is a located `YS_CODE_ERROR`. An escape
  naming no Unicode codepoint is one. A position that is not a number is one. The reader reports such input as a fault
  rather than misreading it. The fault spends the wire. A host failure reading the wire is not a token. `ys_read_token`
  returns such a failure. A reader out of memory for its buffer returns `YS_FAILED_MEMORY`. The reader returns
  `YS_FAILED_STREAM` where the byte source fails. The reader returns `YS_FAILED_ACTION` for a read past the end. A
  caller reading until a negative return therefore learns why the reads stopped.

- **`make vet-mypy` reads the Python types.** It checks the code that has an annotation. It asks for no annotation where
  the code has none. `mypy.ini` names the modules that must have an annotation throughout. `PLAN.md` owes the
  annotations the other modules lack.

  The Python annotations serve `a-boolean-name-is-a-question`. `check_conventions` decides that rule for C by reading
  the `bool` off the declaration. Where Python has no annotation, a reader decides the same rule. A written `-> bool`
  moves the rule to the gate. The gate then reaches a parameter and a field. A checker of return values reaches neither.

  `mypy` found the types a cache holds. A fixpoint fills an empty container, and the annotation on that container names
  the key type and the value type. A `kept_for` store holds `ir.Kept` values. Such a value pairs a subject with its
  answer. The store keys that value by the subject's id.

  `mypy` also found a bug. `ir.LitValue` holds an int, a string or nothing. `_can_be_refused` read a repetition's count
  as a number and did not check the count's type. A count written as a string would have made that read raise.

- **A `bool` answers a question, and a command hands back a `ys_status`.** The internals had a second failure
  convention. The functions below reported a host failure as `false`.

  - `ys_memory_reserve` and `ys_queue_make_room` fail where the cap or the allocator refuses.
  - `ys_queue_emit` and `ys_stack_push` reach the allocator through the pair above.
  - `ys_append` and `ys_append_byte` grow the wire reader's text.
  - `ys_parser_fill` fails where the source's reader fails or the cap refuses.
  - `ys_put` fails where the byte transport fails.

  A command hands back a `ys_status`. The value then says which delegate failed. `ys_put`'s callers stop writing
  `ys_put(...) ? YS_OK : YS_FAILED_STREAM`.

  A parse is neither a command nor a question. `ys_hex`, `ys_scan` and `ys_unescape` fail on a malformed wire, and a
  malformed wire is a token rather than an `errno`. A negative `ys_status` holds an `errno`. A `ys_status` return would
  have reported a malformed wire as a host failure. A parse hands back the position it reached. A parse hands back NULL
  where the bytes fell short. `ys_next_line` in the same file already answered that way. `ys_scan` loses its cursor
  out-parameter. A caller of `ys_scan` reads the marks in a loop. The loop keeps the position where a scan gave up. A
  caller of `ys_hex` advances by the position the call hands back and computes no width again.

  `ys_hex` refuses without touching the value. A refused reservation charges nothing in the same way.

  `check_conventions` refuses a C function that hands back a `bool` under a name that asks no question. The gate passes
  `is_continuation`, `ys_queue_is_ready` and `ys_are_tokens_stable`. Those names ask a question.

- The API follows an `errno` policy. Malformed data is no `errno`. A syntax error or a broken wire is a `YS_CODE_ERROR`
  token in the stream. A host failure is an `errno`. `ys_read_token` returns a negative `ys_status`. The `errno` is
  `ENOMEM`, `ENODATA`, or the value the reader set. A function that fails without a token sets `errno`. Those are a
  constructor, `ys_write_token`, and the closers. `EINVAL` names a bad argument. A stream source with no `read` callback
  is such an argument. So is a NULL buffer with a length given to a memory parser. `ENOMEM` names insufficient memory.
  The API passes through the value a failing callback set. An allocator or reader callback must set `errno` when it
  fails. A debug build asserts that a failing allocator set `errno`.

- A close reports its failure. A buffered transport writes to its destination at the close. A full disk or a broken pipe
  therefore shows up at the close. The `close` of a `ys_bytes_reader`, a `ys_bytes_writer` and a `ys_allocator` answers
  the contract of `close(2)`. That contract answers 0 or -1 with `errno` set. A reader's `read` and a writer's `write`
  already answer the contracts of `read(2)` and `write(2)`. `ys_delete_token_sink` and `ys_delete_token_source` return
  that failure rather than swallow it. A delete closes the byte transport. It closes the allocator after the memory goes
  back. The allocator may therefore hold the memory the delete gives back. A failing close does not stop the delete, and
  the delete leaks nothing. The delete answers `YS_OK`, or `YS_FAILED_STREAM` if the transport's close failed.
  `YS_FAILED_MEMORY` names a failure in the allocator. `YS_FAILED_BOTH` names a failure in both, and `errno` holds the
  first failure. A single `errno` cannot name a pair of failures, and the header documents that limit. A caller that
  needs both failures records the pair in its own callbacks. The `ys_allocator` gains a `close`. An arena or pool then
  goes down with the objects a caller built out of that arena or pool. The `ys_counting_allocator` installs
  `ys_close_counting_allocator`. That close reports a leak as a failure. It answers `-1`, sets `errno` to `ENOMEM`, and
  reports the memory the counter still holds. A delete through that allocator therefore surfaces the leak as
  `YS_FAILED_MEMORY` rather than asserting.

### Fixed

- The yeast wire format dropped an error's message. It took a token's text to be the input the token spans, and an error
  spans none. For a malformed document, the format therefore wrote `!` and no message. Haskell YamlReference writes `!`
  and the message. libyeast compares its token streams against Haskell YamlReference in the yeast wire format. libyeast
  and Haskell YamlReference differ on an invalid document. The dropped message broke the comparison on that input.

- The wire reader handed out a token text that was not NUL-terminated, and `ys_write_token` took an error's length with
  `strlen`. A round trip of an error token through the wire overread the heap whenever the text filled its buffer
  exactly. The wire runs that read-then-write pipe. The reader leaves a text terminated. A bare error reads back with an
  empty text rather than a NULL one, as the header promised.

- A reader leaked where the library could not build the parser behind it. A caller hands over a reader, and an owned
  file descriptor stops belonging to that caller. A NULL return left the caller with nothing to close the descriptor
  with. A reader constructor closes the descriptor the caller gave it. The constructor keeps the `errno` that named the
  failure intact across that close.

- `ys_hex` accumulated 8 hexadecimal digits into a signed `long`. That overflows on a platform whose `long` is 32 bits.
  MSVC is such a platform. A wire could name a codepoint Unicode does not have, or half of a surrogate pair. The reader
  then wrote that codepoint into its own text as bytes that are not UTF-8. `ys_scan` read a position with `strtoul`.
  That function takes a sign. `# B: -1` was therefore a position of `SIZE_MAX`.

- The marker gate could not see inside a `(<<<)`. It walked the other nodes. It passed over the `(<<<)` and discarded
  what that node held. An unclosed `begin-` marker inside an indentation bound therefore passed the grammar gates. A
  second gate was missing.

- The coverage gate passed on a report that covered nothing. If gcovr's filters had stopped matching, the gate would
  have kept passing. The `// UNTESTED` contract would then have checked nothing, and the badge would still have shown a
  percentage.

- The reader of the yeast wire format grew its line buffer on a refill. The lines the reader had handed back may already
  have left room in that buffer. The buffer grew to the size of the whole stream. A cap stopped that growth partway. The
  reader then reported the end of the stream. A stream of 2000 short lines under a 16 KB cap yielded 2 tokens. The
  reader grows the buffer where the unread remainder fills that buffer. The parser's window already applied that check.

- The build system keeps no hand-written list of files. `CMakeLists.txt` globs the sources and the tests with
  `CONFIGURE_DEPENDS`, as the `Makefile` already globbed its inputs. The `Makefile` had kept a hand-written list of the
  files to lint. That list had gone stale. It named neither `src/parser.c` nor `src/messages.c`. The linter had seen
  neither file.

- The tests cover the `FILE *` writer adapter on Windows. The adapter's test is portable. That test had sat behind the
  guard over the file-descriptor tests, and Windows had not run it. That guard also held a copy of itself.

- Haskell YamlReference does not resume after an error. Haskell YamlReference emits the error token, hands back the
  input behind that token as unparsed, and stops. libyeast had misread Haskell YamlReference and resumed at the next
  document. That resumption broke the token-for-token comparison against Haskell YamlReference. libyeast stops after an
  error by default. A caller may ask libyeast to resume.

- An error's message is a static string. Its lifetime follows the same rule as the text of another token. The message
  names the production the parser was inside and the input the parser expected there. The grammar supplies that
  production and that expectation. The document under parse supplies neither. The message leaves out the bytes the
  parser found. The first `YS_CODE_UNPARSED` token behind an error begins at exactly the byte that failed.

- A text token spans no line, and a comment spans no line. The parser skips the input after a malformed document. The
  parser returns the content of a line in that input as a `YS_CODE_UNPARSED` token. The parser returns the break of that
  line as a separate `YS_CODE_UNPARSED` token. A stream parser's output then does not depend on the amount of input in
  its buffer.

_Nobody has tagged a release. The YAML parser itself runs nothing._
