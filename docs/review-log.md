
## 2026-05-25 Outline Stability Plan CC Review

- Tool: Claude Code CLI
- Materials: `docs/superpowers/plans/2026-05-25-outline-stability.md`
- Purpose: Minimal-scope plan consistency review before outline-stability implementation.
- Initial status: Not ready. Critical blockers were live prompt path mismatch and missing inline prompt targets; important blocker was undefined self-similarity metric.
- Fixes applied: Plan now sets `OUTLINE_PROMPT_PATH` for real runs, targets `build_skeleton_prompt()`, `call_skeleton_merge_pass()`, and `call_fill_chapter()`, and defines normalized body-text `SequenceMatcher(..., autojunk=False)` self-similarity.
- Confirmation status: Ready to execute as written.

## 2026-05-26 Outline Location Stability Design CC Review

- Tool: Claude Code CLI
- Materials: `docs/superpowers/specs/2026-05-26-outline-location-stability-design.md`, `scripts/generate_outline_deepseek.py`, `tests/test_generate_outline_source.py`
- Purpose: Pre-implementation spec review for shared chapter-location fixes affecting skeleton granularity and final transcript injection.
- Raw review: `docs/reviews/2026-05-26-outline-location-stability-claude-review-1.md`
- Critical: none against the design itself; Claude identified that the current implementation still lacks all proposed contracts: short location prompt, deterministic candidate fallback, partial-location API, prefix-preserving granularity fallback, and tests.
- Important: add `location_source` metadata, preserve strict final slicing, and either deprecate or align the older non-windowed location pass.
- Conclusion: Design is ready to implement. Recommended order is prompt helper, candidate fallback, partial location support, prefix-preserving granularity fallback, metadata, and tests.

## 2026-05-26 Outline Location Stability Plan CC Review 1

- Tool: Claude Code CLI
- Materials: `docs/superpowers/plans/2026-05-26-outline-location-stability.md`, `docs/superpowers/specs/2026-05-26-outline-location-stability-design.md`, prior design review, review log, `scripts/generate_outline_deepseek.py`, `tests/test_generate_outline_source.py`
- Purpose: Plan consistency review before implementation.
- Raw review: `docs/reviews/2026-05-26-outline-location-stability-plan-claude-review-1.md`
- Status: not ready before fixes; findings applied to the plan.

Critical/important findings:

- Clarify previous/next heading index expressions to avoid 1-based/0-based mistakes.
- Keep test coverage for bare `RuntimeError` location failure producing a fully estimated granularity plan.
- Explicitly validate partial prefix locations before using them for granularity.
- Document candidate fallback as best-effort/no-raise.
- Clarify final-location validation placement and DeepSeek env injection for the 002 rerun.

Conclusion: findings applied; reran confirmation review.

## 2026-05-26 Outline Location Stability Plan CC Review 2

- Tool: Claude Code CLI
- Materials: updated `docs/superpowers/plans/2026-05-26-outline-location-stability.md`, `docs/reviews/2026-05-26-outline-location-stability-plan-claude-review-1.md`
- Purpose: Final confirmation after applying plan-review findings.
- Raw review: `docs/reviews/2026-05-26-outline-location-stability-plan-claude-review-2.md`
- Status: passed.

Critical: none

Important: none

Conclusion: Ready for subagent-driven execution.

## 2026-05-26 Anchored Skeleton Location CC Review 1

- Tool: Claude Code CLI
- Materials: `docs/superpowers/specs/2026-05-26-anchored-skeleton-location-design.md`, `docs/superpowers/plans/2026-05-26-anchored-skeleton-location.md`, current `scripts/generate_outline_deepseek.py`, current `tests/test_generate_outline_source.py`
- Purpose: Lightweight design/plan review before replacing post-hoc skeleton title location with skeleton-emitted transcript anchors.
- Raw review: `docs/reviews/2026-05-26-anchored-skeleton-location-claude-review-1.md`
- Status: not ready before fixes; findings are being applied to the spec and plan.

Critical findings:

- Existing `normalize_skeleton()` strips HTML anchor comments unless anchored generation call sites are explicitly changed.
- Resume path for clean skeletons without anchored skeletons is underspecified.
- Anchor `chapter_id` must be checked against parsed chapter position.
- New final slicing should require `source: "skeleton_anchor"` instead of merely rejecting `estimated_after_failure`.

Important findings:

- Update `generate_skeleton_with_granularity()` return type and all call sites atomically.
- Ensure `parse_chapters()` receives clean skeletons so fill prompts never include raw anchor JSON comments.
- Manifest tests must assert the new anchored skeleton path/hash fields.

Conclusion: not ready; update plan/spec and request confirmation review.

## 2026-05-26 Anchored Skeleton Location CC Review 2

- Tool: Claude Code CLI
- Materials: updated `docs/superpowers/specs/2026-05-26-anchored-skeleton-location-design.md`, updated `docs/superpowers/plans/2026-05-26-anchored-skeleton-location.md`, prior review summary.
- Purpose: Confirmation review after applying blocking findings.
- Raw review: `docs/reviews/2026-05-26-anchored-skeleton-location-claude-review-2.md`
- Status: passed.

Critical: none

Important: none

Conclusion: Ready for implementation.

## 2026-05-27 Outline Skeleton Retry CC Review 1

- Tool: Claude Code CLI
- Materials: focused prompt summarizing `D:\video\output\policy_only_20260527_144643_schema\002_policy2_merge2`, current outline flow, and failures in round 01 / round 02.
- Purpose: Diagnose failed 002 skeleton/granularity continuation and request minimal solution boundaries before further code changes.
- Raw review: `docs/reviews/2026-05-27-outline-skeleton-retry-claude-review-1.md`
- Status: solution guidance received; not yet implemented.

Critical findings:

- Round 01 is a prompt feedback gap: skeleton was generated before it knew the granularity minimum, so validation correctly failed when a 7834-char chapter had 0 `###`.
- Round 02 is an anchor omission/boundary-collapse failure: the final `##` lacked its own anchor and the ending quote was attached to the previous `##`.
- The two failures need separate retry triggers, not a generic rerun.

Important findings:

- Add Pass A anchor repair before granularity computation.
- Add Pass B granularity repair after granularity computation.
- Keep policy merge unchanged; these failures are downstream of canonical policy.

Conclusion: implement a narrow skeleton retry layer with explicit checkpoints, manifest fields, and terminal `ANCHOR_FAIL` / `GRANULARITY_FAIL` statuses.

## 2026-05-27 Outline Skeleton Retry Plan CC Review 1

- Tool: Claude Code CLI
- Materials: `docs/superpowers/plans/2026-05-27-outline-skeleton-retry.md`
- Purpose: Final confirmation after revising the plan to include deterministic anchor repair, LLM anchor fallback, structured retry errors, explicit granularity repair bounds, and a concrete 002 rerun command.
- Raw review: `docs/reviews/2026-05-27-outline-skeleton-retry-plan-claude-review-1.md`
- Status: passed.

Critical: none

Important:

- Clarify that `OUTLINE_ANCHOR_REPAIR_MAX_ATTEMPTS` counts only LLM fallback calls, not deterministic local anchor normalization.
- Keep `validate_skeleton_matches_granularity()` and `collect_granularity_failures()` consistent.
- Preserve/import `build_granularity_plan_from_locations` when replacing `generate_skeleton_with_granularity()`.

Conclusion: Ready.
