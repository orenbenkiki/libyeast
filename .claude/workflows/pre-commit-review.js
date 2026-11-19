// The pre-commit review asks a reader per question over the files a caller prepared. A reader answers with findings and
// with the claims it confirmed. The `conventions` reader also answers with the conventions it would propose. A proposal
// goes to `.claude/proposals-pending.md`, and `check_proposals` refuses while that file holds a proposal.
//
// `review_input.py` writes a manifest. A caller runs that script first and hands the manifest in as `args.manifest`.
// The review does not run `review_input.py` itself, and it throws where `args.manifest` is missing. `record_run` takes
// what comes back and writes the proposals.

export const meta = {
  name: 'pre-commit-review',
  description: 'The questions a review asks of a change, with a reviewer per question',
  whenToUse: 'After `make pc` passes and before asking to commit. Run `review_input.py`, then pass the manifest it wrote beside a sentence saying what the change is about.',
  phases: [
    { title: 'Review', detail: 'a reviewer per question, run over the prepared input' },
  ],
}

// The input the caller sent. `args` may hold an object, JSON text or a bare sentence. Text opening with `{` parses as
// JSON into an object. Such text that fails to parse becomes the note. A bare sentence becomes the note.
const SAID = (() => {
  if (args && typeof args === 'object') {
    return args
  }
  const text = typeof args === 'string' ? args.trim() : ''
  if (text.startsWith('{')) {
    try {
      return JSON.parse(text)
    } catch (error) {
      return { note: text } // A string that opens with `{` and fails to parse as JSON becomes the note. So does a bare sentence.
    }
  }
  return text ? { note: text } : {}
})()

// The subject of the change. A reviewer who gets no subject reads the diff cold.
const NOTE = SAID.note || 'nothing said. read the diff cold.'

// The caller states here what it measured. The readers take that as given. `no_tree_numbers` refuses a number that
// `check_documents.numbers_in_prose` finds in a document or in a comment. A reader checks no documented count against
// the code.
const FACTS = SAID.facts || null

// The agent type the reviewers run as. `.claude/agents/reader.md` defines the type and its tools.
//
// `args.reader` overrides the agent type name.
const READER = { agentType: SAID.reader || 'reader' }

// A reviewer gets a question from `QUESTIONS` and a set of instructions. The instructions do not depend on the files a
// change touches. This file writes the instructions once. A run fills in the caller's `facts`.
const RULES = `
**The change since the last review.** The diff runs from the tree the last review read, or from HEAD where no review of
this commit ran. \`previous\` holds the findings of that last review, a JSON line per finding. Check each previous
finding of your question against the tree. Report a previous finding the tree still bears out, and quote it. Report
nothing about a previous finding the change settled.

DESIGN.md gives context. It gives perspective and architecture. That says what makes the code and its comments
easier to read. DESIGN.md repeats no code comment. CHANGELOG.md is history, in the present tense, about the work a
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
change disturbs, rather than a re-audit of the code it left alone. That cuts both ways. A fault the change did not
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

**Ask the lookups you can in one message.** Independent lookups go together and cost one round trip. Six greps and four
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
          summary: { type: 'string', description: 'a sentence naming what is wrong or what is missing' },
          evidence: { type: 'string', description: 'the reading or the run that settles it' },
          rule: { type: 'string', description: 'the exact NAME of the rule in `.claude/conventions.md` this breaks' },
        },
      },
    },
    // A reader reports here the claims it checked and found true. A reader that looked hard and a reader that looked at
    // little both file no finding when nothing is wrong.
    confirmed: {
      type: 'array',
      description: 'checkable claims read against the code and found true, written a line per claim',
      items: { type: 'string' },
    },
    // A proposal sits apart from the findings. `check_proposals` holds the gate red until the author rules on a
    // proposal. Code cannot violate a convention nobody wrote down. A reviewer raises such a convention as a proposal.
    // A ruling covers a class of code rather than a single line.
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

// The shape a claim-checker answers in. A refuted claim quotes the code that refutes it, and the schema requires that
// code.
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
          summary: { type: 'string', description: 'a sentence naming what the code does instead' },
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

// `review_input.py` writes a manifest per run from the staged change. The manifest is `{name: [path, ...]}`. A question
// does not get the manifest whole. `wants` says which parts a question needs.
const GIVEN = SAID.manifest || null

// `review_input.py` exits through `report` on a path it will not show, and writes no manifest. The review throws where
// `args.manifest` names no path, and asks no reviewer anything. The review also throws where `args.manifest` is not an
// object, or is an array.
if (GIVEN !== null && (typeof GIVEN !== 'object' || Array.isArray(GIVEN))) {
  throw new Error('`args.manifest` is not an object, or is an array. The review takes the manifest object.')
}
const prepared = GIVEN ? Object.values(GIVEN).flat().filter(Boolean) : []
if (!prepared.length) {
  throw new Error('the review takes the manifest `review_input.py` wrote, as `args.manifest`.')
}

// A description of the contents of a prepared part, keyed by the part's name. A reviewer asking for a part gets that
// description beside the part's paths rather than a bare path.
const PREPARED = {
  code: 'the staged diff of the code. generator, scripts and src are in it. include and grammar are in it. so are the C tests and the build files.',
  docs: 'the staged diff of the documents. `DESIGN.md`, `PLAN.md` and `CHANGELOG.md` are in it. `README.md` and `CONTRIBUTING.md` are in it too.',
  fixtures:
    'the staged change to the conformance fixtures under `tests/spec`. a status and a path take the place of a diff. they say what arrived, what went and what a rename touched.',
  hunks:
    'the changed runs of Python. a run shows the function holding it. the docstring of that function shows in the form this change leaves and in the form the base of the diff holds. a run shows the lines around it, and `>` marks the lines that moved. the text sits beside the code, and the before sits beside the after.',
  previous: 'the findings of the last review of this commit, a JSON line per finding. the file is empty where no review of this commit ran.',
  references:
    'the identifiers and swept terms the change took out of a file. a term comes with its surviving mentions in the tree, found by a repo-wide grep. the search for the sites still naming those terms is done.',
}

function handed(wants) {
  // A named and empty part is not a prepared part. A part with no path would announce itself to a reviewer as a part
  // the run handed over.
  const parts = (wants || []).filter((name) => [].concat(GIVEN[name] || []).length)
  if (!parts.length) {
    return ''
  }
  // The prompt names a path rather than holding the prose. The script inlines no part of a file.
  return (
    `\nThis is prepared for you. Read it rather than search:\n` +
    parts
      .map((name) => {
        // `generator/review_input.py` may split a prepared thing into numbered parts. The numbered parts share a name,
        // and that name says what they hold. A reader wanting a file wants the whole group.
        const paths = [].concat(GIVEN[name])
        return `${paths.map((path) => `  ${path}`).join('\n')}\n      ${PREPARED[name]}`
      })
      .join('\n') +
    `\nRead ${parts.flatMap((name) => [].concat(GIVEN[name])).length > 1 ? 'them' : 'it'} first with \`Read\`. ` +
    `A call of \`Read\` takes a prepared file whole.\n\n` +
    `**A prepared file is COMPLETE over the ground it covers.** A script built it for this review from the change. ` +
    `Trust its answer. Checking that answer against the repository is the fishing this file saves.\n\n` +
    `**Report a gap in a prepared file as a FINDING.** A docstring may show as \`(none)\` where the code has ` +
    `one. The mentions of a name may be plainly incomplete. The record of a changed function may stop short of the ` +
    `lines around it. Report the gap and move on. The preparation may be wrong, and the author wants to know. A gap ` +
    `the text leaves costs a later reader the same search, and that cost is the defect.\n\n` +
    `Go outside the prepared files for ground they did not cover. Say in the finding what sent you.\n`
  )
}

// An entry of `QUESTIONS` asks a question about the change as a whole. Such a question holds across changes.
const QUESTIONS = [
  {
    key: 'claim-check',
    wants: ['hunks', 'code', 'docs', 'previous'],
    schema: CLAIMS,
    ask: `You are a CLAIM CHECKER rather than a critic. The claim pass of \`converge_claims\` has already read the
comments, the docstrings and \`DESIGN.md\` against the code. Do not check that prose. The claim pass skips the files
\`claim_state._UNCLAIMED\` names. You check two of them. Read \`PLAN.md\` and \`CHANGELOG.md\` from the tree.
\`docs\` shows what changed in them since the last review.

**\`PLAN.md\` against the tree.** An item the plan lists says the tree still owes something. The change may deliver
an item in whole or in part. The code then refutes the item. The finding quotes the PLAN sentence and the code that
delivers it. A stale item goes on saying \`will do\` while the code already does it.

**The \`[Unreleased]\` section of \`CHANGELOG.md\` against the staged change.** Read the section a bullet at a time.

- A bullet says the change does something the staged diff does not show. Report it. The finding quotes the bullet and
  the diff that refutes it.
- The staged diff holds a change a user of the project would notice, and no bullet covers it. Report it. The finding
  names the change and quotes the diff that shows it.

An entry under an older version says what an earlier change did. Do not check it.

**Output contract, binding.** A finding MUST quote a span of code or of the diff with its \`file:line\`. Without that
quote you do not have a finding.

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
    wants: ['hunks', 'code', 'references', 'previous'],
    ask: `Find the places the new code breaks a convention of \`.claude/conventions.md\`. Read that file first. A
finding cites by name the rule it breaks. A finding may be about naming or structure. A rename may reach a site and
miss another. A parameter may go unused. An existing helper may answer the new logic.

A fault that breaks no listed rule is a **proposal**. State it as a rule whose violation is decidable. Say what the
rule buys a reader. Name the sites in this change where it applies. A proposal blocks nothing, and the author rules on
it. \`.claude/rejected.md\` holds the proposals the author turned down. Read it before you propose.

\`hunks\` holds the changed functions with the lines around them. A name that does not fit its neighbours shows there.
\`hunks\` holds the Python files. \`code\` holds the staged diff of the C, shell and JavaScript files.

Do not chase whether a helper already exists that the new code duplicates. Say what the new code appears to
duplicate, and let the author check. \`references\` holds the mentions a term the change took out still has in the
tree. A mention there is a site a rename missed. Report it.

A record may stop short of the neighbours a name should read like. Report that.`,
  },
]

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
        `only for the code the diff cannot show. That is the code around a changed line, and the text your question ` +
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
// The workflow keeps a proposal apart from a finding the whole way out. `generator/record_run.py` writes the findings
// to `review_input.REVIEWED`, and `_reviewed` reads them for the next review. A proposal that arrives mixed in with the
// findings reads there as a finding.
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

// The workflow returns a proposal in its result. `record_run` writes the proposal into `.claude/proposals-pending.md`.
// `check_proposals` refuses while that file holds a proposal. A ruling clears the gate. The proposal moves into
// `.claude/conventions.md` or `.claude/rejected.md` and comes out of the pending file.
if (proposals.length) {
  log(`${proposals.length} proposal(s) came back. \`record_run\` writes them down. The gate stays red until you rule on them.`)
}

return { note: NOTE, findings, proposals, confirmed }
