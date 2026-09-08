# Making the review converge

Round after round of review stayed flat. The reviewers were not the cause. A fix rewrote the cited sentence with new
unverified prose, and that prose gave the next round its findings.

The order here is mechanism, then proof, then application. Building a mechanism while fixing project prose re-judges the
prose already fixed. That re-judging is the churn.

## The concerns of this review

The review has the concerns below and no more.

- **(a)** `PLAN.md` is future. `DESIGN.md` is present. `CHANGELOG.md` is past.
- **(b)** Prose in simple sentences.
- **(c)** Prose consistent with the code.
- **(d)** Omissions. Something landed, and no document says so.

A mechanical rule sits under those concerns. A document and a comment alike state no number that depends on the tree.

Both languages are in scope. The Python is build-time tooling. The public C API is the user-facing surface.

## Phase 1: build the mechanisms

The tree stays red through this phase. A red tree here is the expected state rather than a signal.

### Edit-time checkers, under `.claude/hooks/`

| Step | Check                                                                                                                | State |
| ---- | -------------------------------------------------------------------------------------------------------------------- | ----- |
| 1.1  | Numbers of the tree, over markdown and Python and C                                                                  | Built |
| 1.2  | The em-dash refused. A ceiling on commas and a ceiling on words.                                                     | Built |
| 1.3  | A clause giving WHY                                                                                                  | Built |
| 1.4  | Vague quantifiers and universals                                                                                     | Built |
| 1.5  | Hook scope matches gate scope. The hook holds a markdown file to the number rule. The gate holds the root documents. | Owed  |

### Gate checks, under `generator/`

| Step | Check                                                                                                   | State             |
| ---- | ------------------------------------------------------------------------------------------------------- | ----------------- |
| 1.6  | One number rule, for the documents and for the prose beside the code                                    | Built             |
| 1.7  | A document narrating its own history                                                                    | Already built     |
| 1.8  | A new top-level definition has a docstring                                                              | Owed              |
| 1.9  | `CHANGELOG.md` names a new step and a new invariant                                                     | Owed              |
| 1.10 | `CHANGELOG.md` names a top-level name the change adds or removes. A declared exemption list backs that. | Owed, last, noisy |

A pair of checks came out. A word list for completed tense in `PLAN.md` is defeated by tense. A stale item keeps saying
"will do" while the code does it. A shared run of words between a pair of documents is defeated by re-wording. And
`CHANGELOG.md` overlapping `DESIGN.md` is correct when something lands the first time.

### `DESIGN.md`

| Step | Check                                                                               | State |
| ---- | ----------------------------------------------------------------------------------- | ----- |
| 1.11 | `DESIGN.md` holds two chapters. The C library, and the generator                    | Owed  |
| 1.12 | Altitude. `DESIGN.md` cites no private name. Known violations exist                 | Owed  |
| 1.13 | The generator chapter covers `ir.TREES`. It names a kind of that table.             | Owed  |
| 1.14 | The C chapter covers the object families the `ys_new_` and `ys_delete_` pairs name. | Owed  |

Whether `DESIGN.md` argues its architecture well is not mechanisable. That one stays with the author.

### C

| Step | Check                                                                                                      | State |
| ---- | ---------------------------------------------------------------------------------------------------------- | ----- |
| 1.15 | A declaration in `include/yeast.h` has a comment block. The contract lives on the header                   | Owed  |
| 1.16 | `review_input.hunks` emits C function records. That is the biggest lever this plan has for the reader on C | Owed  |
| 1.17 | `check_dead_code` reaches C. Something exports or calls a non-static function under `src/`.                | Owed  |

### The workflow

The agents below remain, for the judgements no script can make.

- **claim-check.** Does a sentence assert a checkable fact, and does the code refute it.
- **conventions.** Does this break a rule named on the written list.

The plan drafted a **dead-code** agent, and the workflow asks it nothing. That agent would say what the change leaves
that nothing reaches below the top level.

| Step | Task                                                                                                 | State                  |
| ---- | ---------------------------------------------------------------------------------------------------- | ---------------------- |
| 1.19 | Fan claim-check out, an agent per prepared part. Recall is the measured weak axis                    | Owed                   |
| 1.20 | Name `PLAN.md` in the claim-check contract. The code delivering an item claimed as owed is refutable | Owed                   |
| 1.22 | Settle a cap on the fan-out width                                                                    | Owed, needs the author |
| 1.24 | Record in `.claude/conventions.md` which rules became mechanical                                     | Owed                   |

## Phase 2: prove the mechanism

The work here touches no project prose, outside the module named below.

### Determinism

| Step | Task                                                                                |
| ---- | ----------------------------------------------------------------------------------- |
| 2.1  | Run a checker on synthetic passing and failing cases. Run it in both directions.    |
| 2.2  | `review_input.py` runs and writes its files                                         |
| 2.3  | The workflow runs end to end                                                        |
| 2.4  | The workflow runs twice unchanged, and an agent answers the same both times         |
| 2.5  | Nobody has run `dead-code` twice. Its stability is a guess. Nobody has measured it. |
| 2.6  | The union of the fanned-out parts covers the prose, and the slices leave no gap     |
| 2.7  | A checker reads the source of the checkers and refuses nothing                      |

### Convergence

Determinism is not convergence. Determinism says the answer is stable. Convergence says fixing the answer ends.

`generator/check_dead_code.py` is the scope. That module is moderate in size, and this round of fixing has not touched
it.

| Step | Task                                                                                    |
| ---- | --------------------------------------------------------------------------------------- |
| 2.8  | Round one. Run the workflow scoped to the module. Record a finding.                     |
| 2.9  | Fix the module. Prefer deletion where deletion is available. Record which prose is new. |
| 2.10 | Round two. The same run                                                                 |
| 2.11 | Classify a round-two finding as pre-existing, or as prose the fix wrote.                |

Criteria, registered before the run.

- **Primary.** A round-two review reports no finding against prose written during the fix.
- **Secondary.** The total falls by more than half.
- **Failure.** A round-two finding in a forbidden category. Such a finding means an agent broke its contract.

Phase 3 does not begin unless the primary criterion holds. Applying a loop that does not converge to the whole tree
repeats the week.

## Phase 3: apply to the project

| Step | Task                                                                                |
| ---- | ----------------------------------------------------------------------------------- |
| 3.1  | Measure the backlog per class. Phase 1 names the classes.                           |
| 3.2  | Read the counts, then decide per class. A class drains at once, or it drains later. |
| 3.3  | Drain by deletion                                                                   |
| 3.4  | `make pc` green                                                                     |

## Phase 4: prove it again on the fixed tree

| Step | Task                                                                                                |
| ---- | --------------------------------------------------------------------------------------------------- |
| 4.1  | The workflow again. A decidable finding surviving here is a fault.                                  |
| 4.2  | Classify anything found as a real miss, or as the mechanism drifting. Classify it before fixing it. |
