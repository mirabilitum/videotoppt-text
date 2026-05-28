# Claude Review 1: Outline Location Stability Design

## Critical

**1. `call_location_pass_windowed` still prepends `prompt_template` verbatim — the spec's primary fix is not implemented.**
`generate_outline_deepseek.py` lines 1295-1323 show the windowed prompt is still built as `f"""{prompt_template.strip()}\n\n---\n\n这是 Pass 1.2…`. The spec's §1 ("Shorten and Harden the Shared Location Prompt") requires removing `prompt_template` from the location prompt entirely. This is the root-cause mitigation. Nothing in the implementation or the proposed `build_location_prompt()` function signature exists yet.

**2. `build_granularity_plan_from_skeleton` silently discards all successful prefix locations on any failure — the spec's §3 partial-result contract is not implemented.**
Lines 965-969:
```python
except Exception as exc:
    print(f"Pass 0.5: draft location pass failed, using estimated granularity plan: {exc}")
    return build_estimated_granularity_plan(transcript, chapters), chapters
```
The spec (§3) requires preserving successfully located chapters 1-N and only estimating the remaining unresolved tail. The current code throws away chapters 1-5's real positions and replaces everything with a uniform average.

**3. `call_location_pass_windowed` has no `allow_partial` parameter and no `PartialChapterLocationsError` — the spec's strictness-split contract is entirely absent.**
The spec's API shape specifies `allow_partial: bool = False` with distinct failure semantics. Without this, `build_granularity_plan_from_skeleton` cannot receive a partial result to fall back from.

**4. `find_location_candidate` does not exist — the deterministic fallback is not implemented.**
`parse_single_chapter_location` falls back to `find_heading_start`, but that does not split composite `与`/`和`/`及`/`、` joiners, drop generic filler terms, or prefer transition markers. For the observed `课程的系统性与均衡性调整` failure, existing `heading_search_terms` cannot find the relevant transcript text.

**5. No test covers the spec's primary new behaviours.**
Existing tests still cover the old whole-plan fallback behavior and do not cover the new prompt, candidate fallback, strict final slicing, or tolerant prefix-preserve behavior.

## Important

**6. `location_source` metadata field is not added to granularity plan items.**
The spec requires `"location_source": "llm" | "candidate" | "estimated_after_failure"` for auditability.

**7. `validate_skeleton_matches_granularity` cannot enforce the estimated-location guard.**
It only checks `min_subsections`; no pipeline point detects estimated locations.

**8. The `prompt_template` parameter of `call_location_pass_windowed` is currently load-bearing even though the spec says it must stop being used.**
The implementation should keep the parameter for compatibility but stop inserting it into the prompt.

**9. The existing `call_location_pass` non-windowed function still uses the full `prompt_template` and has the same root-cause exposure.**
It is not on the live main path, but should be deprecated or handled consistently if retained.

## Minor

**10. `heading_search_terms` does not split on `与`, `和`, `及` joiners.**
This narrow change would improve composite heading fallback generally.

**11. The current estimated fallback test will become incorrect once the spec is implemented.**
It should be updated to assert prefix-preserve plus suffix-estimate behavior.

**12. `outline_locations.json` format does not include `location_source`.**
Adding it is low-risk and improves auditability.

## Conclusion

The spec is well-reasoned and correctly identifies the shared-location failure mode. The design is ready to implement, with no blocking spec ambiguity. Recommended implementation order:

1. add `build_location_prompt` excluding `prompt_template`
2. add `find_location_candidate` with composite-heading splitting
3. add `allow_partial` to `call_location_pass_windowed`
4. fix `build_granularity_plan_from_skeleton` to preserve prefix locations
5. add `location_source` to plan items and `outline_locations.json`
6. update and add tests per the spec's Testing Plan
