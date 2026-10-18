# EOP10 rebase conflict review

Date: 2026-10-18. Review only; the trial rebase was aborted and EOP10 restored.
The owner requests conflict review and a choice before selecting the rebase
strategy. Existing NGP ancestry alone is not a substitute for that review.

## First replayed commit

`3196f8285ea91fa0e2ce7bb4db81b8b55d840507`:
"docs: present branch features in FrogPilot-style tables and highlights".
It changes README.md and adds BRANCH_FEATURE_COMPARISON.md. This conflict is
documentation, not an observed modeld, stereo or JetLink code conflict.

Reconstructed the three-way README merge without changing the branch, using
the NGP tip as current, 3196f8285's parent as base, and that commit as incoming.
The reconstruction contains two conflict blocks:

| Block | NGP baseline side | Replayed commit side | Resolution aligned with current project |
| --- | --- | --- | --- |
| Baseline description | Exact v0.10.0 source commit and explanation that inference changes do not upgrade the entire driving stack | Short base/version/date line | Retain precise baseline provenance; concise feature formatting is optional |
| Model/feature explanation | Split vision/policy contract, runner/artifact limitations, platform adapter boundary, monitoring/dependency links and unvalidated-feature qualifications | Compact UI/feature highlights and tables | Preserve technical ownership and limitations, retain accurate presentation rows, update intended JetLink v3/PLP naming while marking migration pending |

The incoming commit also has automatically merged content that requires review:
it claims Tinygrad/Chestnut across all branches and marks some hardware features
as available more broadly than the current design establishes. Conflict markers
are not the complete review boundary. EOP must keep Rockchip adapters and GPU
stereo; NGP owns the shared model/control contract; Chestnut/eGPU remains removed
under the current project direction. Do not resurrect old runtime claims merely
because an old patch applies cleanly.

Neither side implements the newly chosen `nagaspilot/jetlink` upstream-v3
integration. Both branches still have NPBM code. The README resolution should
state the chosen target and its implementation status, not claim migration done.

## Strategy choices

1. **Reviewed linear rebase — recommended for a clean NGP → EOP patch stack.**
   Prepare a candidate from the saved EOP tip. Replay EOP changes on the reviewed
   NGP parent, resolve conflicts against the ownership ledger, and explicitly
   account for merge-only resolutions that a plain rebase drops. Fold or omit
   obsolete docs only with a recorded reason. Compare final candidate tree and
   behavioral tests to the saved tip before replacing the published branch.
2. **Preserve merge structure.** Prepare a candidate with rebase-merges. Retain
   useful topology and review recreated merge resolutions, including the older
   EOP side branch. The final history remains more complex and old conflicts
   can recur. NGP already being an ancestor does not guarantee a plain rebase
   is a no-op, as the aborted trial demonstrated.
3. **Keep current merged ancestry.** NGP inheritance already exists. Resolve
   the guard/adapter policy and proceed with new baseline-first commits. This
   avoids rewriting published history but does not deliver a clean patch stack.

For either rewrite option: preserve a recoverable original ref, work in an
isolated candidate, keep an explicit resolution log, compare source inventories
and final trees, and run lineage plus relevant tests before publication. A
rewritten remote branch requires a checked lease against its observed tip;
never use an unrestricted force push. No rewrite candidate has been selected
or published yet.

## Relationship to the implementation work

The selected strategy must preserve these decisions: JetLink v3 and local
model fallback start on NGP10; PLP is parallel; EOP adds GPU stereo point-cloud
and drivable-area geometry. The open lineage mismatch in bigmodel_platform.py
must be resolved as an explicit adapter boundary on the shared baseline.
After lineage work, resume the stereo plan with measured calibration and metric
depth before ground/obstacle fusion. Reviewing this first README conflict does
not establish that later replayed commits are conflict-free or behaviorally sound.
