# 2026-05-27 Outline Skeleton Retry Claude Review 1

## Critical

Round 01 root cause: granularity validation is correct, but the fix location is wrong.

The skeleton LLM was never told the granularity requirement before it generated `###`. The pipeline derives granularity after skeleton, then validates, but the skeleton prompt contains no instruction about minimum `###` density per chapter. The LLM produced a legal-looking skeleton with zero `###` and passed its own internal check. This is a prompt information gap, not a threshold problem. The granularity plan must be computed from the anchored skeleton and then fed back into a skeleton-retry prompt that says explicitly: chapter 1 spans 7834 chars, requires at least 3 `###`, you currently have 0, regenerate only the `###` subdivisions for affected chapters. Do not re-run full skeleton; re-run only a `###` repair pass for failing chapters.

Round 02 root cause: the anchor for the final `##` was never emitted by the skeleton LLM, not merely misplaced.

The evidence is that the quote that should anchor `课程结束语` was attached to the previous `##`. This is a skeleton generation omission, likely caused by the model treating a short closing section as a continuation rather than a new top-level item. The canonical policy has 12 items; the skeleton LLM silently collapsed the last boundary. This is structurally different from round 01. The fix is: after skeleton parse, count emitted `##` anchors against canonical policy top-level count; if they differ, return the discrepancy as structured error feedback to the skeleton LLM with the exact missing item titles and their `start_quote` from the canonical policy, and demand a targeted correction, not a full regeneration.

These are two distinct failure modes requiring two distinct retry triggers, not one generic retry.

## Important

Retry architecture: two narrow retry passes, not one broad skeleton retry.

Pass A, anchor-repair retry, should trigger when emitted `##` count differs from canonical policy top-level count, or any chapter ID is missing from the anchor map.

- Input: original skeleton text, list of missing items with their `start_quote` from canonical policy, and explicit instruction to insert missing `##` anchors at correct text boundaries without altering existing anchors.
- Max retries: 2.
- Manifest fields: `skeleton_anchor_retry_count`, `anchor_repair_reason` with structured missing IDs and titles.
- Stop condition: after 2 retries, if anchor count still mismatches, mark `ANCHOR_FAIL`, halt the pipeline for that unit, and do not proceed to granularity.

Pass B, granularity-repair retry, should trigger when validation finds chapters where span exceeds threshold and `###` count is below minimum.

- Input: anchored skeleton plus granularity plan output and instruction to add `###` subdivisions only for listed chapters without modifying `##` structure or anchors.
- Max retries: 2.
- Manifest fields: `granularity_retry_count`, `granularity_repair_chapters`.
- Stop condition: after 2 retries, if any chapter still fails, mark `GRANULARITY_FAIL`, halt before continuation.

Pass A must run before Pass B. Granularity cannot be meaningfully computed until the anchor map is complete.

Recommended hard sequence:

1. Skeleton generation.
2. Anchor parse.
3. Anchor count validation against canonical policy, triggering Pass A if needed.
4. Granularity computation from confirmed anchor spans.
5. Granularity validation, triggering Pass B if needed.
6. Continue only after validation passes.

Policy merge should not be changed for these failures. Both failures occur after canonical policy is already fixed, so changing merge to compensate would couple unrelated layers.

## Minor

Suggested tests:

- `test_skeleton_anchor_count_mismatch`: canonical policy has N top-level items and skeleton emits N-1 anchors. Assert that the pipeline detects mismatch, constructs a repair prompt containing the missing title and `start_quote`, does not proceed to granularity, and increments `skeleton_anchor_retry_count`.
- `test_granularity_repair_prompt_contents`: anchored skeleton has a long chapter with 0 `###`. Assert the repair prompt contains chapter ID, span size, minimum `###`, and chapter-limited instruction scope.
- `test_anchor_repair_exhaust_retries`: mock LLM to keep missing the same anchor after 2 retries. Assert `ANCHOR_FAIL` in manifest and no granularity computation.
- `test_granularity_repair_exhaust_retries`: assert `GRANULARITY_FAIL` in manifest and halt before continuation.
- `test_pass_a_before_pass_b_ordering`: when a skeleton has both missing anchor and insufficient `###`, assert anchor repair fires first and granularity repair waits until anchors pass.
- Extend existing outline source tests to enforce the retry manifest fields.

## Conclusion

Root-cause judgment is correct and the two failures are distinct. No deeper policy-flow issue is hidden; the pipeline is missing feedback loops between skeleton generation, anchor validation, and granularity validation. The narrow fix is to enforce the checkpoint sequence and add separate anchor-repair and granularity-repair retry passes with separate triggers, error payloads, manifest fields, and terminal failure statuses. Policy merge is not implicated and should not be changed. Do not lower thresholds, whitelist, or guess missing anchors.
