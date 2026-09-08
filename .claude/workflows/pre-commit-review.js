// The pre-commit review. It prepares the staged change into files a reader can judge. It then asks a reader per
// question over those files. A reader answers with findings, and with the conventions it would propose. A
// proposal goes to `.claude/proposals-pending.md`, and `check_proposals` refuses while that file holds anything.

export const meta = {
  name: 'pre-commit-review',
  description: 'The questions a review asks of a change, with a reviewer per question',
  whenToUse: 'After `make pc` passes and before asking to commit. Pass a sentence saying what the change is about.',
  phases: [
    { title: 'Prepare', detail: 'build the diff, the joined hunks and the surviving references' },
    { title: 'Review', detail: 'a reviewer per question, run over the prepared input' },
  ],
}

// The input the caller sent, and the shape it arrived in. `args` reaches a script as an object where the caller
// passed an object. It reaches the script as a string where the caller passed a sentence. A caller passing JSON
// *text* looks exactly like a caller passing a bare sentence until something parses it.
//
// Read here rather than trusted. Taking the text at face value makes `.facts` and `.diff` quietly undefined. That is
// not an error anywhere, just a review that does not receive what the caller sent. That has happened twice.
const SAID = (() => {
  if (args && typeof args === 'object') {
    return args
  }
  const text = typeof args === 'string' ? args.trim() : ''
  if (text.startsWith('{')) {
    try {
      return JSON.parse(text)
    } catch (error) {
      return { note: text } // not JSON after all. A bare sentence looks like this.
    }
  }
  return text ? { note: text } : {}
})()

// The subject of the change. A reviewer told nothing reads the diff cold. That is slower and no better.
const NOTE = SAID.note || 'nothing said. read the diff cold.'

// Whatever the caller measured and wants the readers to take as given. Checking a documented count against the code is
// the gate's job rather than a reader's job. `check_documents` refuses a number that nothing justifies. That covers
// `DESIGN.md`, `PLAN.md` and `CHANGELOG.md`. So this is for anything else a reader would otherwise go and run.
const FACTS = SAID.facts || null

// The agent type the reviewers run as. `.claude/agents/reader.md` gives them `Read`, `Grep` and `Glob`, and stops
// there. That is both the ban made structural and a smaller context to prefill a turn.
//
// The type gives those tools. A reviewer run as the default type gets whatever the session has. That set holds
// neither `Grep` nor `Glob`. Such a reviewer then reads a file whole where a search would have settled the question.
// It then reports as unestablished what the search could have found.
//
// That is the expensive failure, and it is silent. A review that could not look has the same shape as a review that
// looked and found nothing.
//
// `args.reader` overrides the name, for a session that registers its agent types under a different name.
const READER = { agentType: SAID.reader || 'reader' }

// The questions this project asks of a change. The list does not depend on what the change touched. These are the
// project's fixed checks. This file writes the questions once rather than composing them per commit.
const RULES = `
DESIGN.md gives context. It gives perspective and architecture. That says what makes the code and its comments
easier to read. DESIGN.md repeats no code comment. CHANGELOG.md is history, in the present tense, about what the
change did. PLAN.md holds what the tree still owes. A document keeps to its own domain. Text anywhere narrates no
plan step. It writes no comparison of an earlier state with the present, such as \`now has\`, \`no longer\` or
\`used to\`. Text says what IS.

**\`.claude/conventions.md\` is the whole list of conventions. Read it before you judge anything about how the code
reads.** A violation of something on that list is a finding. A convention you think the code ought to follow, and
that the list does not hold, is a **proposal**. Report it under \`proposals\` rather than under \`findings\`. Do not
let it colour a finding you report for another reason.

**Read \`.claude/rejected.md\` too. Raise nothing in it. That holds for a finding and for a proposal.** The author has
seen those and turned them down, and a reason stands beside the entry. Proposing one again keeps a review from
falling silent.

The split exists for a reason. A reviewer asked "does this read well?" has an answer ready. A review that reports
taste as violation falls silent on no round. Nobody can violate a rule nobody wrote. Proposing a good one is useful
work, and the author rules on it. Asserting it as a defect helps nobody.

The tree as it stood before this change is correct. A review passed that tree at the time. Your job is what this
change disturbs, rather than a re-audit of what it left standing. That cuts both ways. A fault the change did not
touch is out of scope. A fault outside the diff that the change created is in scope.

**The gate has proved the following. Nobody need spend a reader on it.** \`make pc\` is green. A checker decides the
points below before anybody asks you anything.

  - a number in DESIGN, PLAN or CHANGELOG is a number something justifies. In DESIGN that is a count beside a name
    the code measures, and the count agrees with the code. In the three documents that is whatever the document's
    own allowed list names. The rule reads digits and words alike. A heading reads as a sentence does.
  - the three documents say no \`now\`, \`no longer\`, \`used to\` or \`previously\` of themselves.
  - a name cited in backticks names something in the tree. a step, an invariant and a production named exist. That
    holds in DESIGN and PLAN. It holds in a comment or docstring in \`generator/\` and \`scripts/\`. It holds in
    CHANGELOG, bar the names \`NAMES_THE_TREE_NO_LONGER_HOLDS\` declares gone.
  - the \`Makefile\` runs a module of \`generator/\` and \`scripts/\`. Something the \`Makefile\` runs may import that
    module instead. A run of the \`Makefile\` reaches a top-level function, class and constant in those modules. A
    mention is no reach. Something builds an \`ir\` kind. \`KEPT_THOUGH_DEAD\` and \`RUN_FROM_ELSEWHERE\` declare the
    rest with a reason.

Do not re-check that. A rule of that kind may have a gap, and the question that owns the rule names the gap. Report
a gap. The gate closing a gap is worth more than a reviewer finding that gap once.

**You have \`Read\`, \`Grep\` and \`Glob\`. Do not use \`Bash\`.** Do not run anything with \`Bash\`, and do not read
anything with it. That rules out \`git\`, \`sed\`, \`cat\` and a shell \`grep\`. The three tools you have answer the
questions this review asks.

Cost is not the reason. **Reaching for a shell is the signal that you have started fishing.** Fishing is casting
around the repository for something that might be relevant. The alternative is reading what this prompt gave you and
judging that. This review exists to prevent fishing. So the ban sits on the tool rather than on your intent. A
reviewer talks himself past an intent easily. A tool you do not have stays gone.

Running code is doubly out. A run tells you what the code does in one execution. The question here is whether the
text matches what the code SAYS. Reading both from first principles settles that. The author already has the
executions. The gate is those executions, and it is green.

Something you cannot settle by reading is itself the finding. Say what you could not establish, and say why.

**Ask for what you can in one message.** Independent lookups go together and cost one round trip. Six greps and four
files are such lookups. So are a diff and a definition. Sent one at a time they cost six round trips. A lookup
rarely needs the answer to another before you can ask it. This decides whether the review takes four minutes or
forty.

**Budget about 15 tool calls where the work below stands prepared for you. Budget 40 tool calls where the work below
stands unprepared.** Past that you are exploring rather than reviewing, and you should report what you have. Two runs
of this review spent 197-428 turns per question, and then 51-138, to answer questions worth minutes. Those runs made
a lookup at a time. The work prepared below is those same lookups.
${FACTS ? `\nMeasured for you - take these as given and do not go looking for them:\n${FACTS}\n` : ''}`

const FINDINGS = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['summary', 'evidence'],
        properties: {
          file: { type: 'string', description: 'the repo-relative path the finding names' },
          line: { type: 'integer', description: 'omit where the finding is something absent' },
          summary: { type: 'string', description: 'one sentence naming what is wrong or what is missing' },
          evidence: { type: 'string', description: 'the reading or the run that settles it' },
          rule: { type: 'string', description: 'the exact NAME of the rule in .claude/conventions.md this breaks' },
        },
      },
    },
    // The report of what a reader checked and held. Findings by themselves cannot tell a reader that looked hard
    // from a reader that looked at little. Both report nothing when nothing is wrong.
    confirmed: {
      type: 'array',
      description: 'checkable claims read against the code and found true, written a line per claim',
      items: { type: 'string' },
    },
    // Kept apart from the findings and blocking nothing. A convention that nobody wrote down is not a thing the code
    // can violate. Proposing such a convention is the honest way to raise it. The author's ruling retires the whole
    // class rather than the place a reader noticed it.
    proposals: {
      type: 'array',
      description: 'conventions you would add to `.claude/conventions.md`, and no convention already on it',
      items: {
        type: 'object',
        required: ['rule', 'why', 'seen'],
        properties: {
          rule: { type: 'string', description: 'the convention, stated tightly enough to decide a violation' },
          why: { type: 'string', description: 'the gain for a reader, and what goes wrong without the convention' },
          seen: { type: 'string', description: 'the file and line in this change the convention would apply to' },
        },
      },
    },
  },
}

// The shape a claim-checker answers in. A refuted claim quotes the code that refutes it, and that is the
// contract. A contract asked for in a prompt is a contract somebody drops. The schema requires the code instead. A
// finding without the span cannot come back at all.
const CLAIMS = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['summary', 'sentence', 'file', 'line', 'code', 'code_at'],
        properties: {
          file: { type: 'string', description: 'repo-relative path holding the sentence' },
          line: { type: 'integer', description: 'the line the sentence is on' },
          sentence: { type: 'string', description: 'the sentence quoted exactly as written' },
          code: { type: 'string', description: 'the verbatim code that refutes it' },
          code_at: { type: 'string', description: 'file:line of that code' },
          summary: { type: 'string', description: 'one sentence naming what the code does instead' },
        },
      },
    },
    confirmed: {
      type: 'array',
      description: 'checkable claims read against the code and found true, written a line per claim',
      items: { type: 'string' },
    },
  },
}

// The phase that builds the prepared input, and the manifest it yields. The building is a phase of this workflow
// rather than something a caller remembers to do first. Files prepared by hand go stale the moment somebody fixes
// anything. A reviewer handed a stale file reports on a change that has moved on, and the run still looks well.
//
// The phase is a single short agent, and this script cannot run the preparer from here. This file touches no
// filesystem. The agent runs the script and reads back the manifest the script wrote. The agent hands over what is on
// disk rather than what it read off a printout.
//
// A question does not get the manifest whole. `wants` says which parts a question needs. A question about naming has
// no use for a changelog, and a question about counts has no use for a decoder.
const PREPARED_INTO = "$(git rev-parse --git-dir)/review-input"

// The shape `review_input.py` writes its manifest in. The preparer hands that file back rather than the printout.
// These are the manifest's keys, and the manifest holds no others.
const MANIFEST = {
  type: 'object',
  required: ['code', 'docs', 'fixtures', 'hunks', 'references'],
  properties: {
    code: { type: 'array', items: { type: 'string' }, description: 'the paths to the parts of the code diff' },
    docs: { type: 'array', items: { type: 'string' }, description: 'the paths to the parts of the documents diff' },
    fixtures: { type: 'array', items: { type: 'string' }, description: 'the paths to the parts of the fixtures file' },
    hunks: { type: 'array', items: { type: 'string' }, description: 'the paths to the parts of the hunks file' },
    references: { type: 'array', items: { type: 'string' }, description: 'the paths to the parts of the references file' },
  },
}

// The contents of a prepared file. A question asking for a part learns what it is getting rather than a bare path.
const PREPARED = {
  code: 'the staged diff of the code. generator, scripts and src are in it. include and grammar are in it. so are the C tests and the build files.',
  docs: 'the staged diff of the documents. DESIGN.md, PLAN.md and CHANGELOG.md are in it. so are README.md and CONTRIBUTING.md.',
  fixtures:
    'the staged change to the conformance fixtures under tests/spec. a status and a path take the place of a diff. they say what arrived, what went and what a rename touched.',
  hunks:
    'the changed runs of Python. a run comes with the function it falls in. that function\'s docstring comes too, in the form this change leaves and the form HEAD holds. three lines either side come with the run, and `>` marks what moved. the text and the code sit side by side, and so do the before and the after. `git show HEAD:` has nothing to add.',
  references:
    'the identifiers and swept terms the change took out of a file. a term comes with its surviving mentions in the tree, found by a repo-wide grep. the search for what still refers to the change is done.',
}

function handed(wants) {
  // A named and empty part is not a prepared part. A part with no path would announce itself to a reviewer as
  // something handed over.
  const parts = (wants || []).filter((name) => [].concat(GIVEN[name] || []).length)
  if (!parts.length) {
    return ''
  }
  // Paths rather than the text. A prompt cannot hold the text, and the script cannot read a file to inline a part.
  // Reading a prepared file is a single lookup. Rebuilding what a file holds is the lookups the turn counts came
  // from.
  return (
    `\nThis is prepared for you. Read it rather than search:\n` +
    parts
      .map((name) => {
        // A prepared thing may split into files of a single read apiece. The naming groups those files under what
        // they hold. A reader wanting a file wants the whole group.
        const paths = [].concat(GIVEN[name])
        return `${paths.map((path) => `  ${path}`).join('\n')}\n      ${PREPARED[name]}`
      })
      .join('\n') +
    `\nRead ${parts.flatMap((name) => [].concat(GIVEN[name])).length > 1 ? 'them all' : 'it'} first, in one message, ` +
    `with \`Read\` - each is sized to one call and hands you the whole of it.\n\n` +
    `**Each of these is COMPLETE for what it covers.** It was built by a script for this review, from the change you ` +
    `would otherwise have gone looking for. If your question is answered by what is in there, it is answered, and ` +
    `checking it against the repository is the fishing this exists to make unnecessary.\n\n` +
    `**Where something that should be in there is not, that is a FINDING, not an errand.** A docstring shown as ` +
    `\`(none)\` where the code has one, a name whose surviving mentions are plainly incomplete, a changed function ` +
    `whose record stops short of the lines around it - report it and move on. Two reasons, and the second is the one that ` +
    `matters: the preparation may be wrong, which is worth knowing; and if the text itself does not say, then what ` +
    `you would have to do to find out is exactly what every future reader would have to do, which is the defect.\n\n` +
    `Go outside the prepared files only for what they plainly never covered, and say in the finding what sent you.\n`
  )
}

// A question per slice, and a question is about the change as a whole. Verifying that a particular edit does what it
// meant to is the author's job with a command. These are what a change has to answer forever.
const QUESTIONS = [
  {
    key: 'claim-check',
    wants: ['hunks', 'code', 'docs'],
    schema: CLAIMS,
    ask: `You are a CLAIM CHECKER rather than a critic. Go through the prose this change adds or rewrites. Take it a
sentence at a time. That prose lives in the comments, the docstrings and the documents. Ask a single question of a
sentence.

**Does this sentence assert a checkable fact about the code?**

Checkable means reading the code settles it. These are checkable. \`X is the only caller of Y\` is such a claim. So
are \`this returns a set\` and \`every path is covered\`. So are \`the floor is the widest empty line\` and
\`normalize holds a move per phase\`. These are not checkable. \`this buys a reader time\` is such a sentence. So is
\`detail written twice goes stale\`.

**\`PLAN.md\` is in scope. Reviewers skip it.** An item it lists asserts that the tree still owes something, and that
is a claim about the code like any other. The change may deliver an item in whole or in part. The code then refutes
the item. The finding quotes the PLAN sentence and the code that delivers it. A plan claiming work already done is
the failure this catches. A word list cannot catch it. A stale item goes on saying \`will do\` while the code already
does it.

- Asserts nothing checkable. Skip that sentence in silence and report nothing about it.
- Asserts something checkable. Find the code that settles it. Read the callee. Expand the glob. Count the thing.
  - The code CONFIRMS the sentence. Skip it and report nothing.
  - The code REFUTES the sentence. Report it.

**Output contract, binding.** A finding MUST quote a span of code with its \`file:line\`. A finding needs the
contradicting code quoted. Without that quote you do not have a finding.

**Forbidden here, even where you are confident and right.** That a sentence is unclear or long. That a sentence is
dense or ambiguous. That a pronoun has no referent. That a claim appears twice. That prose is redundant. That
something should go. That a comment gives WHY where it need not. That a name is poor. Anything you cannot settle by
quoting code. Anything too vague for you to say what would make it false. Those belong to a hook or to another
reader. Reporting such a finding here is a failure of this task.

**The bar.** A checkable claim confirmed is a complete and successful result, and a fixed change should produce it.
Do not manufacture a finding to have something to report. An empty finding list is a good outcome.

**Report your confirmed claims too. Put a line per claim under \`confirmed\`.** That makes your coverage visible. A
pair of runs of this question over a single file found the same defect and read different amounts besides. Their
findings could not tell the pair apart. A confirmed list says what you actually checked. A claim nobody looked at is
then no claim that held.`,
  },
  {
    key: 'conventions',
    wants: ['hunks'],
    ask: `Where does the new code break a convention in \`.claude/conventions.md\`? Read that file first - it is the
whole list, and a finding here cites the rule it breaks by its name. Naming, structure, a rename applied to some
occurrences and not others, an unused parameter, duplicated logic an existing helper answers.

Something that reads wrong to you and breaks no rule on the list is a **proposal**, not a finding. State it as a rule
whose violation is decidable, say what it buys a reader, and name where in this change it would apply. It blocks
nothing and the author rules on it. Read \`.claude/rejected.md\` before you propose: what is there was proposed once
already and turned down, and is closed.

\`hunks\` is complete for this and is all you are given: every changed function with the lines around it, which is
where a name that does not fit its neighbours shows. A name is judged against what stands beside it, and that is what
a record holds.

Two things are outside them and you should not chase either. Whether a helper already exists that the new code
duplicates - say what the new code appears to duplicate and let the author check, since finding out means reading a
6800-line module. And whether a rename reached every site - that is the claim-check reader's, and it has the grep.

Where a hunk does not show enough to judge a name - the record does not reach the neighbours it should read like -
report that: a name whose fit cannot be seen from the function it is in is one nobody will keep consistent.`,
  },
]

phase('Prepare')

// Built here per run from the staged change. The alternative is a caller who builds it by hand and forgets
// some day. A review of a change somebody has since fixed reads exactly like a review of the change in front of the
// reader.
const GIVEN = await agent(
  `Prepare the input for a code review of the staged change in this repository. Run two commands.\n\n` +
    `  \`mkdir -p "${PREPARED_INTO}" && python3 generator/review_input.py "${PREPARED_INTO}"\`\n` +
    `  \`cat "${PREPARED_INTO}/manifest.json"\`\n\n` +
    `The first takes about twenty seconds. It writes \`code\` and \`docs\`. It writes \`fixtures\`, \`hunks\` and ` +
    `\`references\` too. A part comes whole or split into numbered parts. The second command prints the manifest ` +
    `that run wrote. The manifest is \`{name: [path, ...]}\` and it is your answer.\n\n` +
    `The script refuses a tree the writer has not staged in full, and names the loose paths. That refusal is not ` +
    `yours to work around. Report it and stop. The change then gets staged, and the review reads what will land. ` +
    `A refused run writes no manifest.\n\n` +
    `Hand back the manifest word for word. Do not transcribe the printout. Do not sort or renumber anything. Do not ` +
    `add a path the manifest does not name. Do not read the prepared files. Do not review anything. Run none of the ` +
    `other commands. You are preparing, not reviewing.`,
  { label: 'prepare', phase: 'Prepare', schema: MANIFEST },
)

// A refusal arrives as an answer naming no file. It does not arrive as silence. `review_input.py` exits on a path it
// will not show. The preparer reports that and stops. A manifest of empty lists comes back here. An empty list is
// truthy, and `handed` then tells a reviewer the run gave it something. The reviewer goes looking, and finds whatever
// the last run left on disk.
const prepared = GIVEN ? Object.values(GIVEN).flat().filter(Boolean) : []
if (!GIVEN || !prepared.length) {
  throw new Error('the preparation failed, and a review without its input is a review of nothing in particular')
}
log(`prepared: ${Object.entries(GIVEN).map(([name, paths]) => `${name} ${[].concat(paths).length}`).join(', ')}`)

phase('Review')
log(`facts ${FACTS ? 'given' : 'NOT given'}. asking ${QUESTIONS.length} question(s) - ${NOTE.slice(0, 90)}`)

const answered = await parallel(
  QUESTIONS.map((question) => () =>
    agent(
      `Review a change in this repository. The change is about ${NOTE}\n\n` +
        `Your question is below, and only yours. ${QUESTIONS.length - 1} other reviewers have the other questions. ` +
        `Do not widen.\n\n` +
        `${question.ask}\n\n${RULES}\n${handed(question.wants)}\n` +
        `Read what your question needs and no more. The change is above. Do not fetch it again. Go to the files ` +
        `only for what the diff cannot show. That is the code around a changed line, and whatever your question ` +
        `sends you looking for. Report concrete findings. Give the file and the line where the finding has one. Say ` +
        `in the evidence what you read that settles the finding. Be harsh. The user wants flaws rather than balance, ` +
        `and wants no positives listed. Be harsh about the text the author wrote down. A finding is a broken rule or ` +
        `a false claim. A preference is no finding. A preference stated as a finding costs the author a rewrite of ` +
        `working text. Finding nothing is a fine answer. Finding nothing quickly beats finding nothing slowly.`,
      { label: question.key, phase: 'Review', schema: question.schema || FINDINGS, ...READER },
    ),
  ),
)

const findings = QUESTIONS.flatMap((question, at) =>
  ((answered[at] && answered[at].findings) || []).map((one) => ({ question: question.key, ...one })),
)
// Kept apart the whole way out. A proposal that arrives mixed in with the findings reads as a finding. This
// separation exists to stop that.
const proposals = QUESTIONS.flatMap((question, at) =>
  ((answered[at] && answered[at].proposals) || []).map((one) => ({ question: question.key, ...one })),
)
// The things a reader checked and found true. These go beside the findings, and they tell a quiet reader from a
// thorough reader.
const confirmed = QUESTIONS.flatMap((question, at) =>
  ((answered[at] && answered[at].confirmed) || []).map((one) => ({ question: question.key, claim: one })),
)
for (const question of QUESTIONS) {
  const mine = findings.filter((one) => one.question === question.key)
  const asked = proposals.filter((one) => one.question === question.key)
  const held = confirmed.filter((one) => one.question === question.key)
  log(
    `${question.key}: ${mine.length || 'nothing'}${asked.length ? ` (+${asked.length} proposed)` : ''}` +
      `${held.length ? ` [${held.length} confirmed]` : ''}`,
  )
}

// Written down rather than reported. A proposal that arrives in a result and gets no ruling comes back next round,
// and the round after. `verify-proposals` refuses while this file holds anything. A ruling clears the gate. The
// proposal moves into `.claude/conventions.md` or `.claude/rejected.md` and comes out of here.
if (proposals.length) {
  const recorded = await agent(
    `Append the proposals below to \`.claude/proposals-pending.md\`. Create the file where it does not exist. ` +
      `Group the proposals under a \`## From \\\`reviewer\\\`\` heading. That heading names the reviewer who raised ` +
      `the proposal. Write a proposal as a numbered list item opening with the rule in bold, then an indented ` +
      `\`*Why:*\` paragraph and an indented \`*Seen:*\` paragraph. \`check_proposals\` counts the bold list items ` +
      `and skips any other line. Write no more than that. Do not rule on a proposal. Do not argue with one. Do not edit ` +
      `\`.claude/conventions.md\` or \`.claude/rejected.md\`.\n\n` +
      proposals
        .map((one) => `- from \`${one.question}\`: **${one.rule}**\n  - why ${one.why}\n  - seen ${one.seen}`)
        .join('\n'),
    { label: 'record-proposals', phase: 'Review' },
  )
  if (!recorded) {
    throw new Error(
      `${proposals.length} proposal(s) came back and reached no place in .claude/proposals-pending.md. ` +
        `They live in this run's result and no place else. Record them by hand before you trust the gate.`,
    )
  }
  log(`${proposals.length} proposal(s) recorded - the gate stays red until you rule on them`)
}

return { note: NOTE, findings, proposals, confirmed }
