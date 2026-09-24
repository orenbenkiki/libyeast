// The critic pass. This reads the batches a caller prepared and hands back what the readers said. The pass reads a
// fragment's prose and leaves its code unread.
//
// A caller runs `collect_fragments`, `update_ledger_and_queue` and `batch_pending_fragments` first, then hands the
// manifest in as `args`. This pass calls no tool of its own. `record_run` takes what comes back and writes it.
//
// A run takes what `_MOST_BATCHES` readers can hold. A single run covers a commit touching a single batch of fragments.
//
// The settling pass reads a fragment's prose against its code. This workflow holds no such pass.

export const meta = {
  name: 'critic',
  description: 'The linguistic pass over the queue of unexamined prose fragments',
  whenToUse: 'To drain the prose queue. Run it until the queue holds nothing unexamined. Apply the rewrites between runs.',
  phases: [
    { title: 'Examine', detail: 'a reader per batch, given prose and no code' },
  ],
}

if (!args || !args.examine) {
  throw new Error('critic takes the manifest `batch_pending_fragments` wrote, as `args`.')
}
if (!args.batches || !args.batches.length) {
  log('nothing is left unexamined')
  return { examined: 0, passed: [], rewritten: [], proposals: [] }
}
log(`queue: ${args.unexamined} unexamined. taking ${args.batched} in ${args.batches.length} batch(es)`)

phase('Examine')

// `batch_pending_fragments.py` writes this script and puts the batched prose inside the script. A reader calls no
// tool and opens no file.
const answered = await workflow({ scriptPath: args.examine })

const passed = answered.flatMap((one) => (one && one.passed) || [])
const rewritten = answered.flatMap((one, at) =>
  ((one && one.rewritten) || []).map((said) => ({ batch: at + 1, ...said })),
)
const proposals = answered.flatMap((one, at) =>
  ((one && one.proposals) || []).map((found) => ({ batch: at + 1, ...found })),
)
answered.forEach((one, at) => {
  const proposed = ((one && one.proposals) || []).length
  log(
    `batch ${at + 1}: ${((one && one.passed) || []).length} passed, ` +
      `${((one && one.rewritten) || []).length} rewritten` +
      (proposed ? ` (+${proposed} proposed)` : ''),
  )
})

// A batch's fragments come back under `passed`, `rewritten` or `out_of_budget`. A reader that drops a fragment leaves
// the count short.
const budgeted = answered.flatMap((said) => (said && said.out_of_budget) || [])
const answers = passed.length + rewritten.length + budgeted.length
if (answers !== args.batched) {
  throw new Error(`the batches hold ${args.batched} fragment(s) and the readers answered ${answers} of them`)
}

// A fragment that passed moves out of `unexamined`. A fragment the reader said again stays there. A writer may take the reader's rewrite and change that fragment's prose. `update_ledger_and_queue` would then put the fragment back. `record_run` writes both
// the passed keys and the proposals.
return { examined: args.batched, passed, rewritten, proposals }
