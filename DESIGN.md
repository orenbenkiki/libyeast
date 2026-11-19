# libyeast Design

libyeast is an efficient streaming YAML 1.2 parser in C. It is a *token* parser. A generator writes its C source from
the formal grammar. The parser produces a yeast token stream. That stream represents the structure of a YAML document
without loss.

Higher level layers use the yeast token stream. They compose the tokens into a node graph and link anchors and aliases.
They resolve tags, deal with duplicate keys, and construct native data structures.
[The YAML specification](https://yaml.org/spec/1.2.2/#31-processes) describes those processes. The stream also preserves
the presentation details of the input document. Indentation and comments are among those details. The `ys_code` values
`YS_CODE_INDENT` and `YS_CODE_BEGIN_COMMENT` mark them. The stream thus serves as a basis for YAML re-formatters and
pretty printers.

## Main Parts

1. The C parser and its public API in `include/yeast.h` are the goal of the project. The `exports_sources` in
   `conanfile.py` leave out the generator. The shipped package is a self-contained C library. `CMakeLists.txt` builds
   the library, and `conanfile.py` lets Conan consume it. A user of the library can look at the generated
   [Doxygen documentation](https://orenbenkiki.github.io/libyeast/) and go no further.

1. Python code generates the C decoder tables from the annotated YAML specification BNF. `make regen` writes the
   generated tables into `src/decoder_tables.h`. The repository commits that file. Building and installing the C parser
   library does not re-run the Python code. A pipeline of transformations takes the specification's backtracking PEG
   syntax toward a deterministic LL(1) grammar. A transformation in the pipeline is mechanical. `check_normalize` runs a
   fixture on the grammar of the last step that can still run it. It runs the YAML Test Suite on the grammar the
   pipeline ends with. `normalize.OWED` names the invariants the pipeline has yet to settle.

1. `spec_tests` and `star` take their fixtures from the test cases of the
   [Haskell YamlReference](https://github.com/orenbenkiki/yamlreference) and
   [YAML Test Suite](https://github.com/yaml/yaml-test-suite) projects. `check_normalize` runs the grammar and the
   result of a transformation step against those fixtures. The C parser takes no step between states. `ys_parser_read`
   answers `YS_OK` with a `not implemented` error token instead. `tests/test_c.c` tests the library's interface.
   `tests/test_decoder.c` tests the decoder. `tests/test_parser.c` tests the parser's internals and the error a read
   answers with.

1. The attached [Makefile](Makefile) coordinates development. The Makefile runs the Python code that generates the
   tables, and runs the tests. The authors built this project from the ground up with Claude. The project tests how far
   Claude can take a project. The project therefore checks its documentation and comments automatically. A check refuses
   the Claude style. A check asks that the prose be complete and consistent with the code. Shell and Python scripts run
   the checks. Claude settings constrain the agent as it writes.

## C Parser Overview

The [Doxygen documentation](https://orenbenkiki.github.io/libyeast/) already includes an overview of the code from the
user's point of view. The documentation of the public API holds the details, and this document does not repeat those
details.

At the bottom, the *decoder* in `decoder.c` turns input bytes into keys. The decoder assembles no Unicode codepoint. A
key holds a bit per character set the grammar tests. The generator evaluates the grammar's unions and subtractions ahead
of time. The generator writes its tables into `decoder_tables.h`, and that file sits in the tree. A test for a character
the grammar names is a single comparison of keys. The `text` of a leaf `ys_token` is a span of the original bytes.

The *parser* holds the runtime state a generated automaton needs. That state holds a window over the input and a queue
of tokens already built. `ys_queue` can hold a run back. The state also holds a stack of the productions the parse is
inside. A frame holds the return point and the indentation in force. The C call stack holds no part of that state.

The parser takes no step between states. `ys_read_token` finds no transition to take. `ys_read_token` hands back an
error token reading "not implemented" instead. The next call hands back that error token again.

Above the parser, a *token source* (`ys_token_source`) is a single handle over tokens. The tokens come from YAML parsed
from memory, from YAML parsed from a stream, or from a replayed yeast wire. A token source replays a yeast wire on
demand. A token stream written earlier reads back through `ys_read_token`, the same call a parse would use. A *token
sink* (`ys_token_sink`) writes tokens out as a yeast wire or back as YAML. The `ys_sink_kind` of the sink picks which.

The plumbing sits beside the parser and the token handles. The plumbing reads buffered bytes from a byte source through
`ys_source`. It allocates through the pluggable allocator `ys_allocator` and holds `YS_MESSAGES`, the table of static
message strings that `ys_message` reads.

## Grammar Transformations Overview

The [YAML specification](https://yaml.org/spec/1.2.2/) grammar is a backtracking PEG (Parsing Expression Grammar). A
parser can run such a grammar as written (e.g. [Haskell YamlReference](https://github.com/orenbenkiki/yamlreference) and
the [YAML Reference Parser](https://github.com/yaml/yaml-reference-parser)). This project holds such a
[parser](generator/interpreter.py). The parser's `run` runs the grammar as written. `check_normalize` runs the grammar
through its transformations with that parser. A backtracking parse can take
[exponential time](https://en.wikipedia.org/wiki/Recursive_descent_parser).

In contrast an ideal [deterministic LL(1) grammar](https://en.wikipedia.org/wiki/LL_parser) only requires looking at the
next character. A parser for such a grammar streams the input a character at a time. Such a parser is a finite state
machine with a stack for pushing and popping state data. Such a machine runs efficiently.

The [YAML specification](https://yaml.org/spec/1.2.2/) permits converting its grammar to a deterministic LL(1) grammar.
A [plain key](https://yaml.org/spec/1.2.2/#742-flow-mappings) and the
[leading empty lines](https://yaml.org/spec/1.2.2/#8111-block-indentation-indicator) of a block scalar wait on a later
character. `ys_queue` holds their tokens back as an open run until that character arrives.

The [YAML spec](https://yaml.org/spec/1.2.2/#742-flow-mappings) specifically restricts the amount of data in such
provisional tokens to `1024` characters for plain keys. A block scalar that
[detects its indentation](https://yaml.org/spec/1.2.2/#8111-block-indentation-indicator) is a harder case. A true parser
could make do with counting the initial empty lines and the maximal number of spaces in a line. We emit tokens that
cover the input. We must therefore keep such leading empty lines as provisional tokens. Here the YAML spec does not
restrict the amount of data we need to keep. To compensate, a caller can cap the total memory the parser uses through
`ys_options::max_bytes`. The parser rejects the input once it reaches that cap. We assume real documents do not open a
block scalar on enough empty lines to reach that limit.

We start with a slightly tweaked version of the [YAML specification BNF rules](grammar/yeast-spec-1.2.yaml).
`check_vendor_spec` checks that version against [the original](third_party/yaml-grammar/yaml-spec-1.2.yaml). `stages`
runs a series of semantics-preserving steps to transform the backtracking PEG grammar toward a deterministic LL(1)
grammar ([`generator/normalize.py`](generator/normalize.py)). `check_normalize` shows that a step preserves the
semantics. From the grammar we generate the C character tables
([`generator/grammar2decoder.py`](generator/grammar2decoder.py)).

## Ensuring correctness

`check_normalize` checks the correctness of the result. It runs test cases against the stages of the pipeline. A fixture
runs against a single stage. The suite cases run against the final grammar. A pass shows that the steps kept the
semantics. The C parser has no automaton for the suite to run against. `ys_parser_read` says so.

1. The fixtures in [`tests/spec/`](tests/spec) come from
   [Haskell YamlReference](https://github.com/orenbenkiki/yamlreference). The fixtures are low level tests.
   `check_interpreter` checks that the grammar produces the token stream a fixture's `.output` file holds. The tokens
   keep the presentation details, under `ys_code` values such as `YS_CODE_WHITE` and `YS_CODE_INDICATOR`. A fixture is a
   pair of files named `<production>[.n=N][.p=N][.c=C][.t=T][.r=R][.i=I].<case>`, and `spec_tests` reads that name. The
   first holds an `.input` YAML fragment. The second holds the `.output` token stream the named production must emit for
   that input. The name says which rule to run and with which parameters. A fixture therefore tests a rule rather than a
   document.

   An input the grammar must reject is a fixture like any other. Its name states the `invalid` case `spec_tests` reads.
   Its `.output` pins the `!` error token and the position, in the format `wire.py` writes. The `.output` then lists the
   markers `interpreter.run` closes behind that token. `check_interpreter` can then decide a rejection rather than
   merely observe it.

   If you need to generate the output of a new fixture, [`generator/regen_fixture.py`](generator/regen_fixture.py) runs
   the interpreter over a named input and freezes what it emits.

   We don't reuse the Haskell YamlReference test suite unchanged, as `third_party/yamlreference/README.md` says. The
   decoder in `src/decoder.h` reads UTF-8. Our `bom` token holds the mark itself (`U+FEFF`). The `bom` token of the
   Haskell YamlReference names the encoding. That token reads `UTF-8` for a
   [UTF-8 stream](third_party/yamlreference/tests/c-byte-order-mark.utf8.output).

1. We copy the test cases in [`third_party/yaml-test-suite/`](third_party/yaml-test-suite) as-is from the
   [YAML Test Suite](https://github.com/yaml/yaml-test-suite). `third_party/yaml-test-suite/SOURCE.md` names the source
   and the commit. The suite holds high level tests. Such a test checks that the grammar produces the essential YAML
   events as the YAML specification says. The events leave out presentation details, and `star` folds those details
   away. We pin the commit that [YAMLStar](https://github.com/yaml/yamlstar) itself
   [tests against](https://github.com/yaml/yamlstar/blob/8d33149eb7687c7c93022f2d79a991a1564e2a9c/core/Makefile#L14).

   We treat the YAML Test Suite as "golden". We attempt to produce exactly the results the test suite states. We call a
   disagreement out explicitly in [`check_star.DIVERGENCES`](generator/check_star.py). An entry there names the case and
   justifies the difference. An example is `JEF9/02`, an empty kept block scalar whose input ends in no line break. The
   test suite adds the line break automatically. Our implementation correctly parses the input without an implicit final
   line break.

## Vetting the project

We vet the project along the axes below.

1. **Functional correctness:** the test suites described above cover this. `generator/check_normalize.py` runs them.

1. **Memory safety of the C parser:** The Debug build compiles with AddressSanitizer and UndefinedBehaviorSanitizer. The
   tests run under both. Leak detection differs by platform. On Linux LeakSanitizer flags a leak as a test exits. Apple
   clang has no LeakSanitizer. On MacOS the `leaks` tool reads the Release binary instead. A test that runs on Linux and
   on MacOS uses `ys_counting_allocator`. That allocator verifies that allocations and deallocations match.

1. **Code style:** standard tools do this. `clang-format` formats the C, and `clang-tidy` and `cppcheck` lint it.
   `black`, `ruff` and `pylint` do the same for the Python. `mdformat` handles the markdown. `gersemi` handles the
   CMake, and `shfmt` handles the shell. `scripts/check_comments.py` checks the C comment style.
   `scripts/wrap_long_comments.py` reflows a `#` comment block to the column limit in the Python, the shell scripts and
   the `Makefile`.

1. **Prose style:** Claude generates the prose, and Claude is bad at it. We run mechanised tests for known Claude anti
   patterns directly from the `Makefile`. Its `verify-prose` target runs `generator/check_prose.py` over the tree. We
   provide [Claude settings](.claude/settings.json) to keep those patterns out of new prose. That `settings.json` runs
   write-time hooks such as `prose_gate` that refuse an edit rather than reporting it afterwards. A settling pass then
   reads the prose back and rewrites the prose the conventions refuse. `generator/converge_prose.py` runs that pass.
   [Settling the prose](#settling-the-prose) describes it.

1. **Prose correctness:** We run the [`pre-commit-review.js`](.claude/workflows/pre-commit-review.js) workflow of
   focused Claude agents. A claim-check agent checks the owed work against the tree and `CHANGELOG.md` against the
   staged change. A conventions agent checks the new code against the conventions. Both judge properties a pattern match
   cannot decide.

`make pc` in the `Makefile` runs the checks a machine decides. The `pre-commit-review` workflow in
`pre-commit-review.js` checks the claims and the conventions a gate leaves to a reader.

A push makes GitHub run the [workflows](.github/workflows). The workflows repeat the checks of `make pc` and leave out
the Claude workflow. The workflows add a CodeQL scan. The workflows also build the GitHub pages holding the
[Doxygen documentation of the C parser](https://orenbenkiki.github.io/libyeast/).

## Settling the prose

### A fragment

`collect_fragments.fragments` splits the tree into a `collect_fragments.Fragment` per piece of prose, plus the code that
prose describes. A fragment's key is `language:file:name`. The key of a file fragment leaves out the name. A fragment
with no declared name uses the digest of its prose as the name. A paragraph of a document under a heading puts that
heading in front of that digest.

A fragment holds its prose in sites. A site is a run of lines in a file that holds prose. A fragment starts at a line of
the file holding a site. A site gives its lines as offsets from that line. `collect_fragments.site_lines` turns the
offsets into line numbers.

A `collect_fragments.Fragment` holds its prose as the text `collect_fragments.flattened` gives. A site of the fragment
is a paragraph of its own. `apply_prose.does_hold` reads the lines of a site as the file wraps them.

`collect_fragments.prose_digest` computes a prose digest. The function digests the text `collect_fragments.flattened`
gives. That text keeps the words and the blank lines between paragraphs. A fenced block keeps its line breaks in the
digest. Line wrapping elsewhere does not change the digest. The digests `settling_state.ledger` reads are prose digests.
`settling_state.prose_queue` keys its entries by the digest. The driver computes the digest of its own writes with the
same function.

A rewrite changes the prose. The digest changes with it, and a key that ends in a digest changes too.
`collect_fragments.key_for` gives the new key. The old key disappears from the tree. A key contains no line number and
no position. `collect_fragments` builds a key out of the language, the file and a name for the fragment. The key of a
file fragment holds no name.

### The ledger

`settling_state` names the files of this section and the next. `.claude/prose-ledger.jsonl` holds a line per approved
prose digest. `prose_digest` digests the prose of a fragment. A change to the code leaves the ledger unchanged.
`generator/update_ledger_and_queue.py` keeps a ledger digest the tree still holds.

`settling_state.SUGGESTED` names the file that queues the critics' convention proposals.

### The queues

The queues sit in the directory the `Makefile` variable `PROSE_DIR` names. That directory defaults to `.git/critic`.

`prose-queue.jsonl` holds the state of the fragments which are neither approved nor given up, keyed by the prose digest.
The driver appends a line per state change. `update_ledger_and_queue` compacts the file as a run starts. The state is
`settling_state.UNEXAMINED` for a fragment no critic has read. A fragment a critic has read has a
`settling_state.ProseState`. That state holds the current draft of the fragment and the count of its critic rounds. The
state also holds the changes the critic suggested, as `settling_state.Change`. A change the comparator judged holds its
verdict.

`unsettled-prose.jsonl` holds the digest of a fragment the prose pass gave up on. A fragment that reaches the cap on
critic rounds lands there. So does a fragment where a claims reader changed the critic's draft.
`generator/settling_state.py` names that file. A writer may delete the file. `update_ledger_and_queue` then puts the
fragments of the deleted file back in the prose queue at the next run.

`writes.jsonl` holds a record per write of `converge_prose` or `converge_claims` into the tree. A record holds the prose
before and after the write. `settling_state.last_write` reads the record behind a digest.

`review.jsonl` holds a record where a claims reader corrected a critic's draft. `generator/settling_state.py` writes
that record. A record names the fragment's key. A record also holds the prose of the tree, the draft and the corrected
draft. A person reads the file. `check_prose` points at the file.

`completed-tasks.jsonl` holds the results of the finished tasks, as `settling_state.TaskResult`. The last step of a task
appends its result to this file with `settling_state.completed`. The append holds a file lock, as
`generator/settling_state.py` shows.

### The checkers

`checkers` holds the list of mechanical checkers. Prose passes through the checkers before a file, a queue or a list of
pending proposals takes it. `draft_refusals` runs the checkers over a draft bound for the tree or the queue.

A caller reaches the checkers through `checkers.refusals_for_edit`. The function reads a write to a file.
`apply_prose.text_refusals` asks `refusals_for_edit` about a file with new text. `apply_prose.draft_refusals` asks
`text_refusals` about the files a fragment's draft moves in. `record_run.proposal_refusals` asks `text_refusals` about
the pending proposals file with a proposal written in. `check_prose` asks `refusals_for_edit` about a file of the tree.
`collect_fragments.whole` makes the edit that writes such a file from nothing.

The checkers pass a WHY clause in the `why` bullet of a pending proposal. `settling_state.WHY` names that bullet.

A convention with no mechanical checker stays with the reviewers. `a-piece-of-prose-is-written-once` is one.

1. The main session edits a file. `.claude/settings.json` registers `prose_gate` as a write-time hook. `prose_gate`
   passes the edit through the checkers. `check_prose` passes the tracked files of the tree through the same checkers.

1. A critic answers with rewrites and proposals. `prose_answer` passes the answer through the checkers inside the agent
   call. A refusal there sends the critic back to rewrite the prose. The driver `converge_prose.py` passes the answer
   through the checkers again as it reads the answer.

1. The condense answers with proposals. `converge_prose` passes those proposals through the checkers with
   `record_run.proposal_refusals` as it reads the answer.

1. The driver writes a draft into the tree. `apply_prose.draft_refusals` passes the draft through the checkers before
   the write.

1. `.claude/workflows/pre-commit-review.js` runs the readers. A reader answers with findings and confirmed claims. The
   `conventions` reader also answers with proposals. `prose_answer` passes the proposals through the checkers inside the
   agent call. A claim-check finding quotes the faulty sentence. A finding goes to the author. `generator/record_run.py`
   writes the proposals into `.claude/proposals-pending.md`. It writes the findings into a separate file.

1. `record_run` writes the answer of a workflow. The proposals pass through the checkers first, and a refusal there
   writes nothing.

The comparator `prose-compare.md` generates no prose. A comparator answers with verdicts.

An agent answers through the StructuredOutput tool, and `prose_answer` reads that call. `check_hooks` refuses an agent
definition whose tools leave that tool out. `check_hooks` also refuses a module that runs a checker outside `checkers`.

Neither `.claude/agents/reader.md` nor `.claude/agents/prose-critic.md` lists a tool that writes a file. The answer of
`pre-commit-review.js` reaches the tree through `record_run`. A settling run writes its accepted changes through
`converge_prose`. That module checks the critic's answer.

`.claude/hooks/no-shell-file-writes.sh` declares the scripts that write a file. `check_hooks` holds such a script to the
checkers. A script whose writes hold no new prose goes in a declaration inside `check_hooks`. The declaration names what
the script writes.

The driver `generator/converge_prose.py` stops the run as a fault where it gets a refusal. `converge_prose.main` then
exits with `agent_calls.FAILED`, and a person investigates the stop.

### The process

The ledger and the queues hold the state of the settling, and `settling_state` names their files. An interruption kills
the agent calls in flight. `converge_prose` records no result for a killed call. A finished task already sits in the
completed tasks queue. A restart therefore repeats little work. `update_ledger_and_queue` keeps a started prose queue
entry while the tree holds the entry's digest. The ledger and the unsettled prose queue must also lack that digest.
`run_locked` holds a lock file in the directory of the queues, and that lock keeps another run from starting.

`collect_fragments` first reads the tree. `update_ledger_and_queue` then brings the ledger and the prose queue up to
date.

`update_ledger_and_queue` copies the tree's prose into the draft of a kept entry. The digests of the tree and the draft
match. The update drops a queued change that rewrites nothing in that draft. The update drops a queued change the
checkers refuse. The update drops a suggested proposal the checkers refuse. `rewritten` hands its text to `replaced`.
`replaced` writes a file under a second name, then renames that file into place.

`converge_prose.main` is the driver. It takes the run lock with `settling_state.run_locked`, and calls
`update_ledger_and_queue.updated`. A pass first closes a round that has a verdict per change. The driver runs passes in
a loop. A pass reads a result through `settling_state.completed_taken`, and the driver updates the prose queue from that
result. A pass with no result left to read creates a task where the state allows one. That pass then launches pending
tasks while a slot is free. The driver starts the next pass at once after a pass that did something. After an idle pass,
the driver waits a short while for a task to finish. An idle pass with no task in flight ends the loop.

The driver condenses the suggested proposals after the loop. The condense agent merges them into a shorter list without
duplicates. `record_run.proposals_appended` writes that list into the pending proposals file. The driver then clears the
suggested proposals queue. A person rules on the proposals in the main session.

#### Updating the prose queue

The driver reads a result in a single atomic step. `generator/settling_state.py` holds the lock behind that step. A task
running in parallel waits on the same lock before it appends a result.

A critic may pass a fragment unchanged. `converge_prose.py` then takes the fragment out of the prose queue, and adds its
prose digest to the ledger.

A critic may suggest changes to a fragment. The driver raises an error on a change whose old text the fragment lacks.
The run then stops. The driver discards a change that overlaps a change it kept. The run stops when the driver discards
a change. The driver may keep no change of a fragment the critic did not pass. The driver then marks that fragment
dirty. The driver records the kept changes in the prose queue. A comparator then judges those changes.

A critic may suggest conventions to `generator/converge_prose.py`. The driver appends the suggested conventions to the
file `settling_state.SUGGESTED` names.

A comparator answers `generator/converge_prose.py` with a verdict per change in its task. A verdict picks a side, or
says neither side reads better. The driver accepts a change whose new side wins. The driver rejects a change whose old
side wins or whose verdict says neither side. A comparator can skip a change in a task holding other changes too. The
driver then asks about that change in a task of its own. The driver rejects a change the comparator skips in a task of
its own. The driver records the verdicts in the prose queue. `record_run.rule_use_counted` counts the verdicts by rule.
A fragment waits while a change of it lacks a verdict. `apply_prose.applied` then puts the accepted changes into the
draft. A clean fragment with no accepted change leaves the prose queue, and its prose digest goes into the ledger.

`.claude/agents/prose-critic.md` gives the critic no tool that reads code. An accepted change may alter a draft. That
draft then waits in the `proposed` member of `ProseState` for a claims task. `converge_prose` builds the message of that
task with `converge_claims.claims_message`. `collect_fragments.CLAIM_DRAFT` names the environment variable that holds
the path of the draft file. `prose_answer` reads the reader's answer against that draft.

`converge_prose` reads the answer of the claims task for the fragment. A `changed` run over text the critic rewrote says
the draft contradicts the code. The fragment then keeps the prose of the tree and goes to the unsettled prose queue.
`settling_state.review_logged` writes the prose of the tree, the draft and the corrected draft to the file
`settling_state.REVIEW` names. A `changed` run over text the critic kept leaves that text to the claims pass.
`claim_state.stale_claims` names the written fragment to that pass. With no `changed` run over the rewrite,
`apply_prose.fragment_written` writes the draft into the tree. A file holding a changed site takes a single write. The
prose queue follows the new prose key after the write. The fragment is dirty, and goes to a new critic task.

The driver in `generator/converge_prose.py` ignores a result the prose queue does not wait for. A comparator result for
a change which has a verdict is stale. A critic result is stale where no fragment of its batch waits for a critic. A
critic task skips a digest the queue lacks too.

#### Generating new tasks

`converge_prose` first tries to create a claims task. A claims task takes a single fragment holding a draft that waits
for its claims check.

`converge_prose` then tries to create a comparator task. A comparator task batches changes, and the changes may come
from different fragments. The driver creates a comparator task once the changes fill a batch. Near the end of the run, a
comparator task takes a shorter batch. A change the comparator left unanswered in a larger batch gets a comparator task
of its own at any point in the run. The end is near when no critic task is pending or in flight. The prose queue must
also hold no unexamined fragment, or the run must have taken as many unexamined fragments as its count allows.

`converge_prose` next tries to create a critic task. A critic task batches fragments. The driver first takes the dirty
fragments with no change left to judge. The driver fills the rest of the batch with unexamined fragments. The driver
sends a batch short of the full size only where no comparator task is pending or in flight.

`generator/converge_prose.py` hands a fragment at the round limit to `given_up` rather than to a critic. `given_up`
appends that fragment to the unsettled prose queue. A critic still reads a fragment whose prose the driver cannot write
back. `generator/converge_prose.py` checks the write back only once a claims reader has read the draft of a round the
comparator decided.

`converge_prose` adds a new task to the pending tasks queue.

#### Launching a task

`converge_prose` caps the tasks running at once.

#### Failures

1. A rate-limited or overloaded API stops the run, and a later run resumes.
   https://platform.claude.com/docs/en/api/errors names those statuses. `agent_calls.does_the_reply_stop_the_run` reads
   the status off a reply.

1. A dead call exits with an error, runs past its turns, or makes no StructuredOutput call.
   `generator/converge_prose.py` counts a critic call that speaks for no fragment of its batch as dead too. The driver
   kills a call past its time limit, and that call counts as dead. A dead call stops the run as a fault. A reply that
   `does_the_reply_stop_the_run` accepts stops the run as resumable instead. A condense call of
   `generator/converge_prose.py` stops the run as a fault even on such a reply. A claims call of `converge_claims` that
   ran out of answers gives up its fragment and stops nothing. A call dying in `agent_calls` leaves a transcript under
   `dead/` in the run's directory. A stop as a fault names that transcript. A stop over an agent lacking a tool or
   writing no init line names none. A dead seed call names none. A stop over a critic speaking for no fragment names its
   transcript under `kept/`. The work of a settling call stays in the queues `generator/settling_state.py` keeps, and a
   later run takes it up. `claim_state.stale_claims` still names the fragment of a claims call, and a later run of
   `converge_claims` reads it again.

1. In `converge_prose`, a critic answer that passes or rewrites no fragment of its batch is a dead call. A critic answer
   that gives a fragment of its batch no verdict stops the run as a fault. So does an answer naming a key outside the
   batch.

1. `converge_prose` gives a change with no verdict a comparator task of its own. A missing verdict in a task of its own
   rejects the change.

1. Before the tree write, `apply_prose.does_hold` checks that shaping the prose of the fragment's sites gives back the
   lines the tree holds. A mismatch stops a run of `converge_prose`. A run of `converge_claims` moves the fragment to
   the unsettled map. A checker refusing the rewrite stops a run of either command.

1. The tree write passes through the checkers, and `apply_prose.draft_refusals` gives their answer. The agents' prose
   obeys the same checkers. A refusal therefore stops the run, and a person investigates.

1. A critic answer the checkers refuse stops the run of `converge_prose` as a fault. A condense answer the checkers
   refuse stops the run as a fault too.

1. `converge_prose.main` stops the run on an error inside the driver. The driver gives a pass a time budget. A pass past
   its budget stops the run instead of hanging.

1. A kill can leave a torn last line in a journal. `settling_state.lines_read` drops that line.

1. `agent_calls` starts the agents in the driver's
   [process group](https://docs.python.org/3/library/subprocess.html#subprocess.Popen). `agent_calls.stops_on_signals`
   stops the run on a signal, and the driver then kills its agents. `settling_state.run_locked` holds an operating
   system file lock over a run. The operating system
   [frees that lock](https://man7.org/linux/man-pages/man2/flock.2.html) when it kills the run.

1. `converge_prose` leaves the suggested proposals in place after a failed condense. The next run condenses them.

A run may stop at a point where a later run can continue. `converge_prose.main` then exits with the status `RESUMABLE`.
A person investigates a stop with the status `FAILED`.

### The agents

The critic and the comparator read prose. The condense reads the proposals the critics raised. The claims agent reads
prose too. The rest of the pass is mechanism. `converge_prose` starts an agent through `agent_calls` as
`claude -p --agent`, writes a message to the agent's standard input, and reads a JSON answer. An agent holds the tools
`write_agent_prompts.TOOLS` names for it. The message contains the prose the agent reads.

`.claude/agents/prose-critic.md` is the critic's system prompt, as https://code.claude.com/docs/en/sub-agents says of an
agent definition. `write_agent_prompts` writes the answer schema, the conventions and the rejected proposals into that
file. The message `converge_prose` sends the critic adds the ask, the rules `rules_by_use` lists, and a batch of
fragments. The batch gives a key and a file. The batch then gives the kind and the prose. `agent_answers.sited` lays out
the prose with a heading per site where the fragment has more than a single site. The batch gives no code.
`collect_fragments.CRITIC_BATCH` names the keys of the batch to the call.

`.claude/agents/prose-compare.md` is the comparator's system prompt. `write_agent_prompts` copies the conventions into
that file. `converge_prose` sends the comparator a batch of changes and asks which version of a change reads better.
`write_agent_prompts` puts the answer schema in the system prompt. A change gives an id, then the old text and the new
text under `A` and `B`. A coin decides whether the old text goes under `A` or under `B`. The comparator judges blind.

`.claude/agents/prose-condense.md` is the condense's system prompt, as https://code.claude.com/docs/en/sub-agents says
of an agent definition. `write_agent_prompts` writes into that file the conventions no checker decides. It also writes
in the rejected proposals. `converge_prose` adds to its message the proposals awaiting a ruling in `PENDING`.
`converge_prose` also adds the proposals in `SUGGESTED` that no condense has taken. The condense runs at most once per
run.

An agent's definition states the shape of the agent's answer. `write_agent_prompts` writes that shape from the schema
the driver sends. The definition and the schema therefore say the same thing.

A request [caches](https://docs.claude.com/en/docs/build-with-claude/prompt-caching) the system prompt and the ask ahead
of the batch. A seed call to the critic and another to the comparator write that cached prefix before the first batch
goes out. `converge_prose` sends a fresh seed after a stall past the idle gap, where a task is pending and more than a
single slot is free. Neither `EXAMINED` nor `JUDGED` names a key of a batch. A schema naming the keys of a batch would
differ per call. The [cache](https://docs.claude.com/en/docs/build-with-claude/prompt-caching) would then hold no prefix
to reuse.

## Checking the claims

The settling pass in `generator/converge_prose.py` judges how prose reads. `generator/converge_claims.py` judges whether
prose is true, through the agent `AN_AGENT` names. A comment that went false when the code moved reads the same as a
true one. The settling pass keys prose on its `prose_digest`. `update_ledger_and_queue.updated` queues no digest the
ledger holds. The settling pass sees no such comment.

`converge_claims` is the driver. It hands a reader a fragment, and the reader answers with a verdict per run of that
fragment's prose. The reader rewrites a run the code contradicts. The fetch server `fetch_fragments` records what the
reader fetched. `relied_on` keeps the fetches a run cites and the questions `is_a_question` finds.

A citation of a file names a range of its lines. `claim_state.refined` narrows the read of the file to the declarations
holding those lines. An edit elsewhere in the file then restales no reader of those declarations in `stale_claims`. A
cited line no declaration holds keeps the read of the whole file. `generator/converge_claims.py` has `unpointed_reads`
check the reads before the narrowing.

### The store

`claim_state` names `.claude/prose-dependencies.json`. `gate.NOT_OURS` holds the file. `is_prose_of_the_project` turns
the file down. `refusals_for_edit` then passes it to no prose checker.

`generator/claim_state.py` writes a `verified` section, and that section maps a fragment key to a
`claim_state.Verified`. That record holds the digests of the fragment, the count of calls, and the reads. A flag marks a
claim a person attested. `collect_fragments.code_digest` gives the code digests the store compares.
`generator/claim_state.py` also writes an `unsettled` section. An entry there holds a fragment's digests, a reason, and
the open claims. An author then edits that fragment or lists its key in the file `CLAIM_AGAIN` names.

`claim_state.claims_written` writes the store. The call takes the keys whose entries the store keeps. The call drops an
entry under another key.

`update_ledger_and_queue.updated` runs after `collect_fragments`, and it updates the claim store beside the ledger and
the queues. `claim_state.claims_purged` drops a store entry the tree moved past. `converge_claims` purges again after a
write.

### Staleness

`stale_claims` names the fragments the pass reads again. A fragment the store lacks reads again, unless the claim pass
skips its document. `claims_purged` drops the entry of a fragment that reads again. An edit restales the fragment it
touches. A code edit restales a settled fragment that read that code. An unsettled fragment waits for a person until its
own prose or code moves.

`generator/collect_fragments.py` leaves the prose out of a file fragment's code. An edit to a comment then restales the
fragment of that comment. `generator/claim_state.py` compares a reader's reads by `code_digest`, and restales no reader
of the file around that comment.

A read key may ask a question of the tree rather than name a fragment. `collect_fragments.CALLERS` and
`collect_fragments.MENTIONS` head such a question. `collect_fragments.is_a_question` tells such a key apart, and
`collect_fragments.asked_in` says the question in words. `claim_state.answer_sha` asks the question again and digests
the answer.

The `verify-claims` gate runs `generator/claim_state.py`. That run fails where a fragment falls stale, and it fails
where the store calls a fragment unsettled. The pre-commit target `pc` runs `verify`, and `verify` runs `verify-claims`.

### The tools

`.claude/mcp/fetch_fragments.py` is an MCP server. A reader holds its own fragment and asks the server for another
fragment by name.

`fetch_fragments` answers a `fetch` with a fragment's prose and its code, or with an outline for a markdown document or
a long file. `collect_fragments.said_by` writes the prose and the code. A name reaching more than a single fragment
comes back as the keys, and the reader asks again under the key it meant. `collect_fragments.code_only` adds the
generated tables and the specification's BNF to the fragments `fetch` serves. `gate.READ_ONLY` names the files holding
the tables and the BNF. A reader then settles a claim resting on those tables or on that BNF.

The `callers` tool of `fetch_fragments` gives the prose and the code of the fragments that call a name.
`collect_fragments.callers_of` computes that list. A file fragment holds the code of its functions, and the list leaves
out a file fragment whose functions hold the calls. The list leaves out the tests too.

`mentions` lists the fragments that mention a name. `collect_fragments.mentions_of` answers it. A struct has no callers.
Nor does a typedef, an enum or a macro. The mentions of such a name are the question to ask about it. The match rests on
the bare name. The `mentions` tool of the server `fetch_fragments` therefore takes only a name the tree declares.
`collect_fragments.c_members` names the fields the structs declare, and `fetch_fragments` refuses a field.

The server `fetch_fragments` appends the keys a call served, and the questions `callers` and `mentions` asked, to the
file the driver names.

`prose_answer` reads a claim record as an answer arrives. `claim_state.relied_on` keeps the reads an answer cites.
`relied_on` also keeps a question the answer read and did not cite. `claim_state.unpointed_reads` decides what a
fragment points at. `prose_answer` turns an answer back where the fragment of that answer leaves a kept read unpointed.
`prose_answer` reads that fragment with the rewrite of the answer applied. A reader then names the unpointed fragment in
a rewrite, or drops a citation the claim does not rest on. An answer may change nothing and leave no run unverifiable.
`converge_claims` stores the kept reads of such an answer. `claim_state.refined` narrows a kept read of a file to the
declarations its citations reach.

### The answer

The reader cuts the prose into runs, and the runs join back into that prose exactly. `agent_answers.tiled_faults` reads
the join. A sound run keeps its text. A run under the verdict `changed` holds the text that replaces it. A
`agent_answers.UNVERIFIABLE` run keeps its text, and says what the fetches left open. `prose_answer` turns back such a
run where it names a fragment the reader did not read. A run under the verdict `external` keeps its text. That text
links a source outside the tree, and the run cites the code that relies on the source. A person attests that the source
verifies the claim. The tools `.claude/agents/prose-claims.md` lists open no page.

`fragments` fills the `references` of a `Fragment` with the fragments its prose cites. `converge_claims` writes the
declarations among them into the reader's message. The record of the answer takes the keys of those declarations. A file
the prose cites stays out of the message. `.claude/agents/prose-claims.md` tells a reader to fetch that file where a
claim rests on it.

`agent_answers.CLAIMED` is the answer schema. `write_agent_prompts` writes that schema into
`.claude/agents/prose-claims.md`. `prose_answer` reads the answer inside the agent call. A faulty tiling goes back to
the reader. `converge_claims` reads the tiling again as it takes the answer.

### The process

`converge_claims` takes the run lock with `settling_state.run_locked`. The run reads the stale fragments in parallel
calls. `apply_prose` writes the changed runs into the tree. A fragment the run wrote goes unsettled where its answer
held an `unverifiable` run. `claims_purged` asks `stale_claims` after a write, and `stale_claims` marks stale again any
other fragment the run wrote. A later call reads such a fragment. The run caps the calls a key gets. A key at the cap
goes unsettled. A rewrite gives a fragment named by its digest a new key through `key_for`. A later call reads the new
key and collects the read set of the new text. A run under `--only` leaves the new key unread. So does a run whose count
has run out.

`generator/converge_claims.py` sends a fragment at the call cap to the unsettled section with the rewrites its calls
made. `claims_written` writes that section.

`make examine-claims` runs the pass. `CLAIM_COUNT` caps the reads a run makes over fragments. `CLAIM_KEYS` narrows a run
to the keys a file names. The pass spends agent calls. `make pc` runs none of it.

### The agent

`.claude/agents/prose-claims.md` is the reader's system prompt. The reader holds the fetch tools and the
StructuredOutput tool that `TOOLS` names. `agent_calls` starts a call, counts what the call spent, and decides where a
reply stops the run. `converge_prose` calls `agent_calls` too.

A call of `generator/converge_claims.py` takes a single fragment. The record that `.claude/mcp/fetch_fragments.py`
writes holds a fetch against the call. A batch would lose which fragment asked.
