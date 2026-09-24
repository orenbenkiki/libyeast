# libyeast Design

libyeast is an efficient streaming YAML 1.2 parser in C. It is a *token* parser. A generator writes its C source from
the formal grammar. The parser produces a yeast token stream. That stream represents the structure of a YAML document
without loss.

Higher level layers use the yeast token stream. They compose the tokens into a node graph and link anchors and aliases.
They resolve tags, deal with duplicate keys, and construct native data structures. The stream also preserves the
presentation details of the input document. Indentation and comments are among those details. The stream thus serves as
a basis for YAML re-formatters and pretty printers.

`DESIGN.md` describes the architecture of libyeast as a whole. The source files document the details, and this document
does not repeat them.

## Main Parts

1. The C parser and its public API in `include/yeast.h` are the goal of the project. The library ships without the
   generator. The repository is a self-contained C library. CMake builds the library, and Conan can consume it. A user
   of the library can look at the generated [Doxygen documentation](https://orenbenkiki.github.io/libyeast/) and go no
   further.

1. Python code generates the C parser tables from the YAML specification BNF. The build commits the generated tables
   into the repository. Building and installing the C parser library does not re-run the Python code. A pipeline of
   transformations takes the specification's backtracking PEG syntax toward a deterministic LL(1) grammar. A
   transformation in the pipeline is mechanical, and it keeps the result true to the YAML specification.
   `normalize.OWED` names the invariants the pipeline has yet to settle.

1. A test framework takes its fixtures from the test cases of the
   [Haskell YamlReference](https://github.com/orenbenkiki/yamlreference) and
   [YAML Test Suite](https://github.com/yaml/yaml-test-suite) projects. The framework runs the grammar and the result of
   a transformation step against the canonical interpretation of YAML documents. The C parser has no automaton. The C
   tests cover the library's interface and the error a read answers with.

1. The attached [Makefile](Makefile) coordinates development. The Makefile runs the Python code that generates the
   tables, and runs the tests. The authors built this project from the ground up with Claude. The project tests how far
   Claude can take a project. The project therefore checks its documentation and comments automatically. A check refuses
   the Claude style. A check asks that the prose be complete and consistent with the code. Shell and Python scripts run
   the checks. Claude settings constrain the agent as it writes.

## C Parser Overview

The [Doxygen documentation](https://orenbenkiki.github.io/libyeast/) already includes an overview of the code from the
user's point of view. The documentation of the public API holds the details, and this document does not repeat those
details.

`DESIGN.md` sketches the architecture of the C parser. Comments in the source code give the details, and `DESIGN.md`
does not repeat them either.

The parts around the automaton work. The generator does not build the automaton.

At the bottom, the *decoder* turns input bytes into something a parse can branch on. The decoder does not assemble
Unicode codepoints. It classifies a character straight into a key. The key holds a bit per character set the grammar
tests. The generator evaluates the grammar's unions and subtractions into those bits ahead of time. A decision is then a
single comparison, and a token is a span of the original bytes. The generator writes the tables the decoder reads, and
those tables sit in the tree.

The *parser* holds the runtime state a generated automaton needs. That state holds a window over the input and a queue
of tokens already built. That queue can hold a run back until a later character settles the codes of that run. The state
also holds a stack of the productions the parse is inside. A frame holds the return point and the indentation in force.
The C call stack holds no part of that state. A caller can therefore pull a single token. The parser resumes
mid-production on the next call.

The parser takes no step between states. `ys_read_token` finds no transition to take. `ys_read_token` hands back a
single error token reading "not implemented" instead.

Above the parser, a *token source* is a single handle over tokens. The tokens come from YAML parsed from memory, from
YAML parsed from a stream, or from a replayed yeast wire. A token source replays a yeast wire in full. A token stream
written earlier reads back through the same call a parse would use. A *token sink* writes tokens out as a yeast wire or
back as YAML.

The plumbing sits beside the parser and the token handles. The plumbing reads buffered bytes from a byte source. It
allocates through the pluggable allocator and holds the table of static message strings.

## Grammar Transformations Overview

The YAML specification grammar is a backtracking PEG (Parsing Expression Grammar). A backtracking PEG lets the grammar
state complex notions directly. A parser can run such a grammar as written (e.g.
[Haskell YamlReference](https://github.com/orenbenkiki/yamlreference) and the
[YAML Reference Parser](https://github.com/yaml/yaml-reference-parser)). This project holds such a
[parser](generator/interpreter.py). The tests run the grammar through its transformations with that parser. Such parsers
are correct by construction, but are inefficient.

In contrast an ideal deterministic LL(1) grammar only requires looking at the next character. A parser for such a
grammar streams the input a character at a time. Such a parser is a finite state machine with a stack for pushing and
popping state data. Such a machine runs efficiently.

The generator converts the PEG grammar to a deterministic LL(1) grammar. The YAML specification permits that conversion.
A plain key and the leading empty lines of a block scalar wait on a later character. The parser keeps their tokens as
provisional tokens until that character arrives.

The YAML spec specifically restricts the amount of data in such provisional tokens to `1024` characters for plain keys.
A block scalar that detects its indentation is a harder case. A true parser could make do with counting the initial
empty lines and the maximal number of spaces in a line. We emit tokens that cover the input. We must therefore keep such
leading empty lines as provisional tokens. Here the YAML spec does not restrict the amount of data we need to keep. To
compensate, we restrict the total memory the parser uses before it rejects the input. Real documents do not open a block
scalar on enough empty lines to reach that limit.

We start with a slightly tweaked version of the [YAML specification BNF rules](grammar/yeast-spec-1.2.yaml). We check
that version against [the original](third_party/yaml-grammar/yaml-spec-1.2.yaml). We then use a series of
semantics-preserving steps to transform the backtracking PEG grammar to a deterministic LL(1) grammar
([`generator/normalize.py`](generator/normalize.py)). From the result we generate the C character tables
([`generator/grammar2decoder.py`](generator/grammar2decoder.py)). `PLAN.md` owes the parser's state tables.

## Ensuring correctness

We try to ensure the correctness of the result in a pair of ways. First, we try and keep the grammar transformation
steps small and plain. A reviewer can then read a step directly and see that it preserves the semantics. Second, we run
a suite of test cases against the original grammar and against the result of a step. A pass shows that the step kept the
semantics. The C parser has no automaton for the suite to run against.

Our test suites come in the groups below.

1. The fixtures in [`tests/spec/`](tests/spec) come from
   [Haskell YamlReference](https://github.com/orenbenkiki/yamlreference). The fixtures are low level tests. They check
   that the parser produces tokens as the YAML specification says, and that the tokens keep the presentation details. A
   fixture is a pair of files named `<production>[.n=N][.p=N][.c=C][.t=T][.r=R][.i=I].<case>`. The first holds an
   `.input` YAML fragment. The second holds the `.output` token stream the named production must emit for that input.
   The name says which rule to run and with which parameters. A fixture therefore tests a rule rather than a document.

   An input the parser must reject is a fixture like any other. Its name states the `invalid` case. Its `.output` pins
   the `!` error token and the position. The `.output` then lists the markers that close behind that token. A test can
   then decide a rejection rather than merely observe it.

   If you need to generate the output of a new fixture, [`generator/regen_fixture.py`](generator/regen_fixture.py) runs
   the interpreter over a named input and freezes what it emits.

   We don't reuse the Haskell YamlReference test suite unchanged. We have added tests. Our parser reads UTF-8. We leave
   out the UTF-16 and UTF-32 cases of that suite. Our `bom` token holds the mark itself as opposed to the little-endian
   and big-endian output from the Haskell YamlReference. We also ensure that no token contains a line break, other than
   the line break tokens themselves. The `unparsed` tokens we emit after an error fall under the same rule.

1. We copy the test cases in [`third_party/yaml-test-suite/`](third_party/yaml-test-suite) as-is from the
   [YAML Test Suite](https://github.com/yaml/yaml-test-suite). The suite holds high level tests. Such a test checks that
   the parser produces the essential YAML events as the YAML specification says. Such a test discards some presentation
   details. We pin the commit that [YAMLStar](https://github.com/yaml/yamlstar) itself tests against.

   We treat the YAML Test Suite as "golden". We attempt to produce exactly the results the test suite states. We call a
   disagreement out explicitly in [`check_star.DIVERGENCES`](generator/check_star.py). An entry there names the case and
   justifies the difference. An example is `JEF9/02`, an empty kept block scalar whose input ends in no line break. The
   test suite adds the line break automatically. Our implementation correctly parses the input without an implicit final
   line break.

A bug we find comes with a test case that demonstrates it. That test must trigger before the fix goes in.

## Vetting the project

We vet the project along the axes below.

1. **Functional correctness:** the test suites described above cover this.

1. **Memory safety of the C parser:** The Debug build compiles with AddressSanitizer and UndefinedBehaviorSanitizer. The
   tests run under both. Leak detection differs by platform. On Linux LeakSanitizer flags a leak as a test exits. Apple
   clang has no LeakSanitizer. On MacOS the `leaks` tool reads the Release binary instead. A test that runs on Linux and
   on MacOS uses `ys_counting_allocator`. That allocator verifies that allocations and deallocations match.

1. **Code style:** standard tools do this. `clang-format` formats the C, and `clang-tidy` and `cppcheck` lint it.
   `black`, `ruff` and `pylint` do the same for the Python. `mdformat` handles the markdown. `gersemi` handles the
   CMake, and `shfmt` handles the shell. A pair of scripts of our own verify additional style issues.
   `scripts/check_comments.py` checks the comment style, and `scripts/wrap_long_comments.py` reflows a comment block to
   the column limit.

1. **Prose style:** Claude writes the prose, and Claude is bad at it. We run mechanised tests for known Claude anti
   patterns directly from the Makefile. We provide [Claude settings](.claude/settings.json) to keep those patterns out
   of new prose. Those settings run write-time hooks that refuse an edit rather than reporting it afterwards. A settling
   pass then reads the prose back and rewrites the prose the conventions refuse.
   [Settling the prose](#settling-the-prose) describes that pass.

1. **Prose correctness:** We run a [workflow](.claude/workflows/pre-commit-review.js) of focused Claude agents. The
   agents review the finished prose for properties a pattern match cannot decide.

`make pc` runs the checks a machine decides. The `pre-commit-review` workflow in Claude checks the prose properties a
gate leaves to a reader.

A push makes GitHub run the [workflows](.github/workflows). The workflows repeat the checks of `make pc` and leave out
the Claude workflow. The workflows add a CodeQL scan. The workflows also build the GitHub pages holding the
[Doxygen documentation of the C parser](https://orenbenkiki.github.io/libyeast/).

## Settling the prose

### A fragment

`collect_fragments.fragments` splits the tree into a `collect_fragments.Fragment` per piece of prose, plus the code that
prose describes. A fragment's key is `language:file:name`. A fragment with no declared name uses the digest of its prose
as the name.

A fragment holds its prose in sites. A site is a run of comment lines in a file. A fragment starts at a line of its
file. A site gives its lines as offsets from that line. `collect_fragments.site_lines` turns the offsets into line
numbers.

`collect_fragments.prose_digest` computes a prose digest. The function reads the words and the blank lines between
paragraphs. The wrapping of a line plays no part in a digest. `make reformat` therefore leaves a digest unchanged. The
ledger holds this digest. A queue keys its entries by this digest. The driver computes the digest of its own writes with
the same function.

A rewrite changes the prose. The digest changes with it, and the key changes too. `collect_fragments.key_for` gives the
new key. The old key disappears from the tree. A key contains no line number and no position.

### The ledger

`settling_state` names the files of this section and the next. `.claude/prose-ledger.jsonl` holds a line per approved
prose digest. Git tracks this file. A clone without the ledger would need to re-settle the whole tree. A change to the
code of a fragment does not affect the ledger.

`.claude/suggested-proposals.jsonl` holds the convention proposals the critics make. Git tracks the file too.

### The queues

`.git/critic/prose-queue.jsonl` holds the state of the fragments which are not approved, keyed by the prose digest. The
driver appends a line per state change. `update_ledger_and_queue` compacts the file as a run starts. The state is
`settling_state.UNEXAMINED` for a fragment no critic has read. A fragment a critic has read has a
`settling_state.ProseState`. That state holds the current draft of the fragment and the count of its critic rounds. The
state also holds the changes the critic suggested, as `settling_state.Change`. A change the comparator judged holds its
verdict.

`.git/critic/unsettled-prose.jsonl` holds the digest of a fragment that reached the cap on critic rounds without
settling. Deleting the file puts those fragments back in the queue.

`.git/critic/pending-tasks.json` holds the tasks waiting to run, as `settling_state.Task`. A task invokes an agent, a
critic or a comparator.

`.git/critic/completed-tasks.jsonl` holds the results of the finished tasks, as `settling_state.TaskResult`. The last
step of a task appends its result to this file with `settling_state.completed`. Tasks run in parallel. The append is
therefore atomic.

### The checkers

`checkers` holds the list of mechanical checkers. Prose passes through the checkers before a file, a queue or a list of
pending proposals takes it.

A caller reaches the checkers through `checkers.refusals_for_edit`. The function reads a write to a file, as the
write-time hook reads a Write call. `apply_prose.text_refusals` asks it about a file with new text.
`apply_prose.draft_refusals` asks it about the files a fragment's draft moves in. `record_run.proposal_refusals` asks it
about the pending proposals file with a proposal written in. `check_prose` asks it about a file of the tree.
`collect_fragments.whole` makes the edit that writes such a file from nothing.

The checkers pass a WHY clause in the `why` bullet of a pending proposal. `settling_state.WHY` names that bullet. The
checkers read the other prose of the tree in full.

A convention with no mechanical checker stays with the reviewers. `a-piece-of-prose-is-written-once` is one.

The list below names the writers of prose.

1. A person or the main session edits a file. The write-time hooks pass the edit through the checkers.

1. A critic answers with rewrites and proposals. `prose_answer` passes the answer through the checkers inside the agent
   call. A refusal there sends the critic back to say the prose again. The driver passes the answer through the checkers
   again as it reads the answer.

1. The condense answers with proposals. The driver passes those proposals through the checkers as it reads the answer.

1. The driver writes a draft into the tree. `apply_prose.draft_refusals` passes the draft through the checkers before
   the write.

1. A reader of the pre-commit review answers with findings and proposals. `prose_answer` passes the proposals through
   the checkers inside the agent call. A finding quotes the faulty text, and goes to the author rather than into a file.

1. `record_run` writes the answer of a workflow. The proposals pass through the checkers first, and a refusal there
   writes nothing. `apply_prose` passes a rewrite through the checkers, and holds back a refused rewrite.

The comparator writes no prose. A comparator answers with verdicts.

An agent answers through the StructuredOutput tool, and `prose_answer` reads that call. `check_hooks` refuses an agent
definition whose tools leave that tool out. `check_hooks` also refuses a module that runs a checker outside `checkers`.

A workflow writes no file. The answer of a workflow's agents reaches the tree through `record_run`. That module checks
the answer.

`.claude/hooks/no-shell-file-writes.sh` declares the scripts that write a file. `check_hooks` holds such a script to the
checkers. A script whose writes hold no new prose goes in a declaration beside `check_hooks`. The declaration names what
the script writes.

A refusal the driver gets stops the run as a fault. A person investigates such a stop.

### The process

The ledger and the queues hold the state of the settling. An interruption loses the work of the agents in flight. A
restart therefore repeats little work. A lock file under `.git/critic` keeps another run from starting.

`collect_fragments` first reads the tree. `update_ledger_and_queue` then brings the ledger and the prose queue up to
date. The update drops a ledger digest the tree lacks. The update drops a prose queue entry the tree lacks. The update
adds an `unexamined` entry for a digest the ledger lacks, unless the unsettled prose queue holds that digest. The update
clears the pending tasks queue, and keeps the completed tasks queue.

`update_ledger_and_queue` copies the tree's prose into the draft of a kept entry. The digests of the tree and the draft
match. The update drops a queued change that rewrites nothing in that draft. The update drops a queued change the
checkers refuse. The update drops a suggested proposal the checkers refuse. The update writes a file under a second
name, then renames it into place.

`converge_prose` is the driver. It takes the run lock with `settling_state.run_locked`, and calls
`update_ledger_and_queue.updated`. The driver first closes a round that has a verdict per change. A run that stopped
before the tree write leaves such a round. The driver then runs passes until the prose queue and the pending tasks queue
are both empty. A pass takes a result off the completed tasks queue with `settling_state.completed_taken`, and the
driver updates the prose queue from that result. A pass with no result left to read creates a task where the state
allows one. That pass then launches a pending task where a slot is free. The driver starts the next pass at once after a
pass that did something. After an idle pass, the driver waits for a task to finish. The loop ends when no task is in
flight.

The driver condenses the suggested proposals after the loop. The condense agent merges them into a shorter list without
duplicates. `record_run.proposals_appended` writes that list into the pending proposals file. The driver then clears the
suggested proposals queue. A person rules on the proposals in the main session.

#### Updating the prose queue

The driver takes a result off the completed tasks queue in a single atomic step. A task running in parallel may append a
result during that step.

A critic may pass a fragment unchanged. The driver then takes the fragment out of the prose queue, and adds its key to
the ledger.

A critic may suggest changes to a fragment. The driver discards a change whose old text the fragment lacks. The driver
discards a change that overlaps a change it kept. The driver marks the fragment dirty when it discards a change. A
comparator judges the changes the driver kept. The driver records the kept changes in the prose queue.

A critic may suggest conventions. The driver appends the suggested conventions to the suggested proposals queue.

A comparator answers with a verdict per change in its task. A verdict picks a side, or says neither side reads better.
The driver accepts a change whose new side wins. The driver rejects a change in the other cases. The driver records the
verdicts in the prose queue. `record_run.rule_use_counted` counts the verdicts by rule. A fragment waits while a change
of it lacks a verdict. `apply_prose.applied` then puts the accepted changes into the draft.
`apply_prose.fragment_written` writes the draft into the tree in a single write. The prose queue follows the new prose
key after the write. A fragment with an accepted change is dirty. A clean fragment leaves the prose queue, and its key
goes into the ledger. A dirty fragment goes to a new critic task.

The driver ignores a result the prose queue does not wait for. A comparator result for a change which has a verdict is
stale. A critic result for a fragment which does not wait for a critic is stale. A result for a prose key the queue
lacks is stale too. A stale result comes from a run that stopped.

#### Generating new tasks

The driver first tries to create a comparator task. A comparator task batches changes, and the changes may come from
different fragments. The driver creates a comparator task once the changes fill a batch. Near the end of the run, a
comparator task takes a shorter batch. The end is near when the prose queue holds no unexamined fragment and no critic
task is pending or in flight.

The driver next tries to create a critic task. A critic task batches fragments. The driver first takes the dirty
fragments with no change left to judge. The driver fills the rest of the batch with unexamined fragments. The driver
shortens the batch once no comparator task is pending or in flight.

A fragment past the round limit goes to the unsettled prose queue rather than to a critic. A fragment whose prose the
driver cannot write back goes to the unsettled prose queue before a critic reads it.

The driver adds a new task to the pending tasks queue.

#### Launching a task

The driver caps the tasks running at once. The driver launches a pending task while a slot is free.

#### Failures

1. An exhausted usage window stops the run.

1. A dead call exits with an error, answers no JSON, or answers a shape the driver cannot read. A lost connection to the
   API kills a call this way. So does a message the model refuses to answer. The driver kills a call past its time
   limit, and that call counts as dead. The driver splits the batch of a dead call into halves, and queues the halves. A
   dead critic call over a single fragment counts as a round of that fragment. A dead comparator call over a single
   change rejects that change. The driver stops the run where dead calls keep coming with no call answering between
   them.

1. A critic answer naming no fragment of its batch is a dead call. The driver counts a round against a fragment the
   critic says nothing about. The driver ignores a key outside the batch.

1. A change the comparator gives no verdict goes to a comparator task of its own. A missing verdict in a task of its own
   rejects the change.

1. The tree write first checks with `apply_prose.does_hold` that the tree still holds the prose. A mismatch stops the
   run. An error from the write stops the run.

1. The tree write passes through the checkers, and `apply_prose.draft_refusals` gives their answer. The agents' prose
   obeys the same checkers. A refusal therefore stops the run, and a person investigates.

1. A critic answer the checkers refuse stops the run as a fault. A condense answer the checkers refuse stops the run as
   a fault too.

1. An error inside the driver stops the run. The driver gives a pass a time budget. A pass past its budget stops the run
   instead of hanging.

1. A kill can leave a torn last line in a journal. The code reading the journal drops that line.

1. The agents run in the driver's process group. Stopping the driver stops its agents. A run holds an operating system
   file lock. The operating system frees that lock when it kills the run.

1. A failed condense leaves the suggested proposals in place. The next run condenses them.

A run may stop at a point where a later run can continue. The driver then exits with a status of its own. A person
investigates a stop with another status.

### The agents

The critic, the comparator and the condense read prose. The rest of the pass is mechanism. `converge_prose` starts an
agent as `claude -p --agent`, writes a message to the agent's standard input, and reads a JSON answer. An agent has no
tools. The message therefore contains the prose the agent reads.

`.claude/agents/prose-critic.md` is the critic's system prompt. `write_agent_prompts` writes the conventions and the
rejected proposals into that file. The message the driver sends the critic adds the ask, the answer schema, and a batch
of fragments. The batch gives a key, a file and a line, and the prose. `batch_pending_fragments.sited` writes the prose
with a heading per site. The batch gives no code.

`.claude/agents/prose-compare.md` is the comparator's system prompt, and that file gives the conventions too. The
message to the comparator adds the ask, the answer schema, and a batch of changes. A change gives an id, then the old
text and the new text under `A` and `B`. A coin decides whether the old text goes under `A` or under `B`. The comparator
judges blind.

`.claude/agents/prose-condense.md` is the condense's system prompt. That file gives the conventions. It also gives the
rejected proposals and the proposals awaiting a ruling. The message adds the proposals of the run. The condense runs
once per run.

An agent's definition states the shape of the agent's answer. `write_agent_prompts` writes that shape from the schema
the driver sends. The definition and the schema therefore say the same thing.

A request caches the system prompt and the ask ahead of the batch. A seed call per agent writes that cached prefix
before the first batch goes out. `converge_prose` sends a fresh seed after a stall past the idle gap. The schema names
no key of a batch. A schema naming the keys of a batch would differ per call. The cache would then hold no prefix to
reuse.
