# Claude Review 1: Outline Location Stability Implementation Plan

## Critical

**1. Strict non-JSON/no-candidate test depends on candidate fallback never raising.**

The test asserting `RuntimeError` with `"Invalid chapter location JSON"` works only if `find_location_candidate(...)` returns `None` rather than raising. The plan should make that helper's contract explicit.

**2. Empty-prefix partial mode behavior is implicit.**

When `allow_partial=True` and chapter 1 fails, `call_location_pass_windowed(...)` raises the original `RuntimeError`; `build_granularity_plan_from_skeleton(...)` then falls back to a fully estimated plan. This is intended but not documented clearly enough.

**3. Previous/next heading index expressions are easy to misread.**

The plan's `previous_heading = chapter_heading(chapters[index - 2][1]) if index > 1 else None` is technically correct because `index` is 1-based and `chapters` is 0-based, but it should add a clarifying comment so implementation does not introduce an off-by-one error. Same concern applies to `next_heading = chapters[index][1]`.

## Important

**4. Replacing the old granularity fallback test removes bare `RuntimeError` coverage.**

The new partial-prefix test covers `PartialChapterLocationsError`, but the plan should keep or add coverage that a plain location failure still returns a fully estimated plan.

**5. Prefix locations from `PartialChapterLocationsError` need explicit validation.**

The plan should either state the exception only carries valid appended locations or add `validate_chapter_locations(...)` inside `build_granularity_plan_from_partial_locations(...)`. The latter is safer.

**6. Final-location validation should be explicitly described as one common call after reuse/fresh branches.**

The suggested placement before `locations_path.write_text(...)` covers both branches, but the plan should state this clearly.

**7. 002 rerun command lacks explicit DeepSeek env injection example.**

Task 6 says to continue with explicit environment injection if `.env` is absent, but does not show how to inject `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL`.

## Minor

**8. Defaulting old `outline_locations.json` source to `"reused"` is safe but should be noted.**

**9. `LOCATION_GENERIC_TERMS` contains entries already removed by the length filter.**

Harmless.

**10. The plan assumes `chapter_subtree` remains available in the loop.**

This is correct but implicit.

**11. Next-heading index expression has the same 1-based/0-based clarity concern as previous-heading.**

## Conclusion

Not yet ready for subagent-driven execution as written. Fix the index comments, keep bare `RuntimeError` fallback coverage, and validate partial prefix locations before using them.
