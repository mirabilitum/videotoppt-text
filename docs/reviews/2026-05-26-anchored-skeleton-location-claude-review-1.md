# 2026-05-26 Anchored Skeleton Location Claude Review 1

## Critical

1. `normalize_skeleton()` currently strips anchor comments. The implementation plan must explicitly change `call_skeleton_pass()` and `call_skeleton_merge_pass()` to call a new anchored normalizer before anchors are parsed.
2. The resume path is incomplete. `main()` currently rebuilds granularity from clean `outline_skeleton.md`; after the signature change it needs a clear anchored-skeleton or legacy-location fallback.
3. Anchor `chapter_id` must be cross-checked against chapter position. `parse_chapters()` assigns positional IDs, so anchor IDs can otherwise be silently mis-correlated.
4. `validate_final_chapter_locations()` still permits `"candidate"` and `"reused"`. New anchored runs must require `"skeleton_anchor"` for final slicing.

## Important

1. `generate_skeleton_with_granularity()` return type and both call sites must change atomically.
2. Resume must not leave an old `call_location_pass_windowed()` granularity path alive.
3. Skeleton-only manifest tests must assert new anchored path/hash fields.
4. `parse_chapters()` must receive clean skeletons so fill prompts do not include raw anchor comments.

## Minor

1. Keep clean skeleton filenames unchanged and anchored skeleton filenames additive.
2. Treat added `start` and `start_quote` granularity fields as intentional audit fields.
3. Anchor quote matching should avoid fuzzy quote fallback or otherwise test it.
4. Cross-reference callers before deleting partial/sparse fallback helpers and tests.

## Conclusion

Not ready for implementation until the plan/spec explicitly handle anchored normalization, resume behavior, anchor ID validation, final source strictness, and clean-vs-anchored parse ordering.
