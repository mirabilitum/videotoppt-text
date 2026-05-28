# Anchored Skeleton Location Design

## Context

The previous location-stability work tried to locate transcript spans after skeleton generation by matching abstract `##` titles back into the transcript. The reruns showed this is structurally fragile: a title such as `法治专册修订对比` can be mentioned early in an overview and then discussed much later. A post-hoc title locator can find a valid quote that is still the wrong chapter boundary.

The replacement design moves location evidence into skeleton generation. The model must emit a transcript quote anchor for every top-level chapter at the same time it creates that chapter. Local code then uses those quotes deterministically for granularity planning and final transcript slicing.

## Goals

- Make chapter starts auditable directly from the skeleton output.
- Compute granularity from real transcript spans, not average lengths and not title-based reverse lookup.
- Use the same anchors for final fill slicing.
- Preserve a clean Markdown skeleton for the fill prompt and final outline files.
- Remove old heuristic code that only existed for the discarded post-hoc title location strategy.

## Non-Goals

- Do not redesign outline policy generation.
- Do not use policy text as transcript location evidence.
- Do not silently estimate final transcript slicing boundaries.
- Do not keep two competing granularity-location paths.

## Anchor Contract

Skeleton generation outputs Markdown headings plus one HTML comment immediately after every `##` heading:

```markdown
## 九、法治专册修订对比
<!-- outline-anchor: {"chapter_id": 9, "start_quote": "在这个法制专策当中啊，我给大家再具体的说一下"} -->
### 9.1 六年级上册修订要点
### 9.2 八年级下册修订要点
```

Rules:

- Anchors are required only for `##` chapters.
- `chapter_id` is 1-based and must match the chapter order after parsing.
- `start_quote` must be copied verbatim from the transcript near that chapter's actual beginning.
- Local parsing resolves `start_quote` to a transcript offset. Anchor resolution first requires exact or normalized quote matching; the older fuzzy fragment fallback is not acceptable for anchors because anchors are supposed to be verbatim evidence.
- Chapter starts must be strictly increasing.
- Missing, malformed, unmatched, duplicate, wrong-`chapter_id`, or non-increasing anchors fail the skeleton run.

`normalize_skeleton()` keeps its existing behavior as the clean Markdown normalizer and strips non-heading lines. Anchored generation must call a separate `normalize_anchored_skeleton()` in `call_skeleton_pass()` and `call_skeleton_merge_pass()` so anchor comments survive until local parsing. `parse_chapters()` must receive `strip_skeleton_anchors(anchored_skeleton)` output; fill prompts must never receive raw anchor comments.

## Files

- `outline_skeleton_anchored.md`: audit artifact with anchor comments.
- `outline_skeleton.md`: clean Markdown with anchor comments removed.
- `outline_locations.json`: deterministic locations derived from skeleton anchors.
- `outline_granularity.json`: span-derived granularity with `location_source: "skeleton_anchor"`.

Skeleton-only experiments write the same pair per run:

- `outline_skeleton_anchored_policy_XX_run_YY.md`
- `outline_skeleton_policy_XX_run_YY.md`
- `outline_granularity_policy_XX_run_YY.json`

The experiment manifest records both skeleton paths and hashes.

Granularity items intentionally include the existing fields plus audit fields:

```json
{
  "top_level_item": "九、法治专册修订对比",
  "source_chars": 520,
  "min_subsections": 0,
  "max_depth": 4,
  "location_source": "skeleton_anchor",
  "start": 14982,
  "start_quote": "在这个法制专策当中啊，我给大家再具体的说一下"
}
```

## Flow

1. Generate an anchored draft skeleton.
2. Parse anchors and compute a granularity plan from actual spans.
3. Generate the final anchored skeleton with that granularity plan.
4. Parse final anchors and compute the final granularity plan.
5. Strip anchors before fill prompts and final clean skeleton output.
6. Use the final anchor-derived locations for transcript slicing.

If the draft already satisfies policy and granularity, a later optimization can skip step 3. This implementation keeps the existing two-pass shape to minimize pipeline churn.

`generate_skeleton_with_granularity()` returns a single structured value containing:

- clean skeleton Markdown;
- anchored skeleton Markdown;
- granularity plan from final anchors;
- final `ChapterLocation` list from final anchors.

Both `main()` and `run_skeleton_only_experiment()` must consume that structured result. This is a breaking internal signature change and all call sites must be updated together.

## Resume Behavior

New runs always write both clean and anchored skeleton files. Resume behavior is:

1. If `outline_skeleton_anchored.md` exists, read it, parse anchors, derive clean skeleton, granularity, and locations from it.
2. If only clean `outline_skeleton.md` exists but `outline_locations.json` also exists and passes final validation, reuse those locations for slicing and compute granularity from them. This is legacy compatibility and the reused locations keep source `"reused"`.
3. If only clean `outline_skeleton.md` exists and no valid `outline_locations.json` exists, fail with a clear message instructing the user to regenerate the skeleton. Do not call the old location LLM to recreate skeleton granularity.

For new non-resume runs, final slicing accepts only `source: "skeleton_anchor"` locations. Legacy `"reused"` is accepted only on the explicit resume compatibility branch above.

## Cleanup Scope

Remove code and tests that only support the old skeleton-granularity locator:

- deterministic title candidate fallback helpers;
- partial/sparse location fallback for granularity;
- skeleton granularity calls to `call_location_pass_windowed()`;
- tests asserting estimated suffix behavior after location failure.

Keep only strict location parsing helpers still needed for compatibility with existing `outline_locations.json` reuse. Before deleting helpers, check all callers with `rg`; no removed helper may remain referenced.

## Validation

Unit tests cover:

- anchored skeleton normalization preserves anchor comments;
- clean skeleton stripping removes anchor comments;
- anchor parsing rejects missing or malformed anchors;
- anchor parsing rejects wrong `chapter_id` values;
- anchor parsing rejects fuzzy-only quote matches;
- granularity is computed from anchor starts;
- final transcript slicing uses skeleton anchors rather than an extra LLM location pass on new runs;
- legacy resume can reuse a valid clean skeleton plus `outline_locations.json`;
- skeleton-only experiment writes both anchored and clean skeleton files;
- old estimated/partial granularity tests are removed or rewritten around anchors.

Verification still requires:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest discover -s tests -v
```

Then rerun the 002 skeleton experiment into a new output directory and inspect:

- both anchored skeleton files;
- both clean skeleton files;
- both granularity files;
- `outline_skeleton_experiment.json`.
