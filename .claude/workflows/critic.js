// The critic pass. This enumerates the project's fragments, reduces the ledger against them, and queues the
// remainder. This file implements the linguistic pass. The pass reads a fragment's prose and leaves its code unread.
// The pass moves what passes to `unverified`.
//
// A run takes what `_MOST_BATCHES` readers can hold. A run then names the fragments no reader has examined. A commit
// touching a single batch of fragments is done in a single run.
//
// The settling pass reads a fragment's prose against its code. That pass is not here yet.

export const meta = {
  name: 'critic',
  description: 'The linguistic pass over the queue of unexamined prose fragments',
  whenToUse: 'To drain the prose queue. Run it until the queue holds nothing unexamined. Apply the rewrites between runs.',
  phases: [
    { title: 'Prepare', detail: 'enumerate the fragments, reduce the ledger, then batch the unexamined ones' },
    { title: 'Examine', detail: 'one reader per batch, given prose and no code' },
    { title: 'Record', detail: 'move what passed to unverified' },
  ],
}

// The paths the enumeration, the ledger and the queue live at. Held under the git directory. Git commits none of this,
// and a clean checkout starts from an empty ledger.
//
// The ledger's real home stays open. The ledger records verdicts reached against the tree, and it belongs in the tree
// once the drain begins. Until then it is scratch. Losing it costs a re-examination rather than a wrong answer.
const HELD_IN = '$(git rev-parse --git-dir)/critic'
const BATCHES = args && args.batches ? ` ${Number(args.batches)}` : ''
const FRAGMENTS = `${HELD_IN}/fragments.json`
const LEDGER = `${HELD_IN}/ledger.json`
const QUEUE = `${HELD_IN}/queue.json`

// The shape the preparer hands back. These are `batch_pending_fragments.py`'s own manifest keys.
const MANIFEST = {
  type: 'object',
  required: ['batches', 'examine', 'batched', 'unexamined'],
  properties: {
    batches: { type: 'array', items: { type: 'string' }, description: 'the path of a batch file written' },
    examine: { type: 'string', description: 'the absolute path of the generated examine workflow' },
    batched: { type: 'integer', description: 'the count of fragments those batches hold' },
    unexamined: { type: 'integer', description: 'the count of fragments the queue holds unexamined' },
  },
}

phase('Prepare')

const GIVEN = await agent(
  `Prepare the prose queue for a linguistic review of this repository. Run these commands in order.\n\n` +
    `  \`mkdir -p "${HELD_IN}"\`\n` +
    `  \`python3 generator/collect_fragments.py --json > "${FRAGMENTS}"\`\n` +
    `  \`python3 generator/update_ledger_and_queue.py "${FRAGMENTS}" "${LEDGER}" "${QUEUE}"\`\n` +
    `  \`python3 generator/batch_pending_fragments.py "${FRAGMENTS}" "${QUEUE}" "${HELD_IN}"${BATCHES}\`\n\n` +
    `Then \`cat "${HELD_IN}/manifest.json"\`. That file is your answer.\n\n` +
    `The first script enumerates the project's prose fragments. The second reduces the ledger to what the tree still ` +
    `bears out and writes the queue. The third writes the unexamined fragments into batch files, and writes the ` +
    `examine workflow that holds their prose.\n\n` +
    `Hand back the manifest word for word. Do not read the batch files. Do not review anything. Run none of the ` +
    `other commands. You are preparing, not reviewing.`,
  { label: 'prepare', phase: 'Prepare', schema: MANIFEST },
)

if (!GIVEN || !GIVEN.batches || !GIVEN.batches.length) {
  log('nothing is left unexamined')
  return { examined: 0, passed: 0, rewritten: [], proposals: [] }
}
log(`queue: ${GIVEN.unexamined} unexamined; taking ${GIVEN.batched} in ${GIVEN.batches.length} batch(es)`)

phase('Examine')

// `batch_pending_fragments.py` writes this script with the prose of the batches inside it. A reader calls no
// tool and opens no file.
const answered = await workflow({ scriptPath: GIVEN.examine })

const passed = answered.flatMap((one) => (one && one.passed) || [])
const rewritten = answered.flatMap((one, at) =>
  ((one && one.rewritten) || []).map((said) => ({ batch: at + 1, ...said })),
)
const proposals = answered.flatMap((one, at) =>
  ((one && one.proposals) || []).map((found) => ({ batch: at + 1, ...found })),
)
answered.forEach((one, at) => {
  log(
    `batch ${at + 1}: ${((one && one.passed) || []).length} passed, ` +
      `${((one && one.rewritten) || []).length} rewritten` +
      `${((one && one.proposals) || []).length ? ` (+${one.proposals.length} proposed)` : ''}`,
  )
})

// A batch's fragments come back under `passed`, `rewritten` or `out_of_budget`. A reader that drops a fragment leaves
// the count short.
const budgeted = answered.flatMap((said) => (said && said.out_of_budget) || [])
const answers = passed.length + rewritten.length + budgeted.length
if (answers !== GIVEN.batched) {
  throw new Error(`the batches hold ${GIVEN.batched} fragment(s) and the readers answered ${answers} of them`)
}

phase('Record')

// A fragment that passed moves. A fragment the reader said again stays in `unexamined`. Applying the rewrite changes
// that fragment's prose, and `update_ledger_and_queue` would put the fragment back there.
if (passed.length) {
  const recorded = await agent(
    `Two steps, in order.\n\n` +
      `First, write this json array of fragment keys to \`${HELD_IN}/passed.json\` word for word. Change no key. ` +
      `Add none and drop none.\n\n` +
      '```json\n' +
      JSON.stringify(passed, null, 2) +
      '\n```\n\n' +
      `Second, run \`python3 generator/pass_examined_fragments.py "${FRAGMENTS}" "${QUEUE}" ` +
      `"${HELD_IN}/passed.json"\` and hand back what it printed.\n\n` +
      `The script refuses a key the queue does not hold as unexamined, and names that key. Report the refusal and ` +
      `stop. Do not edit the queue by hand, and do not drop the offending keys to get it through.`,
    { label: 'record-passed', phase: 'Record' },
  )
  if (!recorded) {
    throw new Error(
      `${passed.length} fragment(s) passed the linguistic pass and reached no record. Those fragments live in ` +
        `this run's result and no place else, and the next run will read them again.`,
    )
  }
}

// Written down rather than reported. `check_proposals` refuses while `.claude/proposals-pending.md` holds anything.
if (proposals.length) {
  const recorded = await agent(
    `Append the proposals below to \`.claude/proposals-pending.md\`. Create the file where it does not exist. ` +
      `Group them under a \`## From \\\`critic\\\`\` heading. Write a proposal as a numbered list item opening with ` +
      `the rule in bold, then an indented \`*Why:*\` paragraph and an indented \`*Seen:*\` paragraph. ` +
      `\`check_proposals\` counts the bold list items and skips any other line. Write no more than that. Do not rule on ` +
      `them, and do not edit \`.claude/conventions.md\` or \`.claude/rejected.md\`.\n\n` +
      proposals.map((one) => `- **${one.rule}**\n  - why ${one.why}\n  - seen ${one.seen}`).join('\n'),
    { label: 'record-proposals', phase: 'Record' },
  )
  if (!recorded) {
    throw new Error(
      `${proposals.length} proposal(s) came back and reached no place in .claude/proposals-pending.md.`,
    )
  }
  log(`${proposals.length} proposal(s) recorded - the gate stays red until you rule on them`)
}

// The work left for the caller. A fragment that passed leaves `unexamined`. A fragment the reader said again stays
// there. A fragment no reader named stays there too. Run this again until `unexamined` is empty.
const left = GIVEN.unexamined - passed.length
log(`${passed.length} passed, ${rewritten.length} rewritten, ${left} still unexamined`)
if (left) {
  log(`apply the rewrites, then run this again. It ends when the queue holds nothing unexamined`)
}

return { examined: GIVEN.batched, passed: passed.length, unexamined: left, rewritten, proposals }
