# Anchored Skeleton Location Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace post-hoc skeleton title location with skeleton-emitted transcript anchors for granularity planning and final slicing.

**Architecture:** Skeleton generation emits audit anchors beside each `##`. Local code parses anchors into `ChapterLocation` records, computes granularity from real spans, writes clean and anchored skeleton artifacts, and uses the same anchor locations for fill slicing. Old granularity fallback heuristics are removed so only one skeleton-location path remains.

**Tech Stack:** Python 3.13, unittest, existing DeepSeek/OpenAI-compatible script flow in `scripts/generate_outline_deepseek.py`.

---

## File Structure

- Modify `scripts/generate_outline_deepseek.py`: add anchored skeleton normalization/parsing, derive granularity and final locations from anchors, write anchored skeleton artifacts, remove old title-location granularity fallback code.
- Modify `tests/test_generate_outline_source.py`: replace old location fallback tests with anchor contract tests and update skeleton-only experiment expectations.
- Generated during verification only: a new `D:\video\output\policy_skeleton_experiment_20260526\002_policy1_skeleton2_granularity_t02_rerun_anchor*` directory.

## Task 1: Add Anchor Parsing And Cleaning

**Files:**
- Modify `scripts/generate_outline_deepseek.py`
- Modify `tests/test_generate_outline_source.py`

- [ ] Add tests for `normalize_anchored_skeleton()`, `strip_skeleton_anchors()`, and `parse_skeleton_anchor_locations()`.
- [ ] Implement `ANCHOR_RE`, `normalize_anchored_skeleton()`, `strip_skeleton_anchors()`, and `parse_skeleton_anchor_locations()`.
- [ ] Ensure parsed anchor locations use `source="skeleton_anchor"` and strictly increasing transcript starts.
- [ ] Add a wrong-`chapter_id` test: a first `##` followed by `{"chapter_id": 2, ...}` must raise.
- [ ] Add a fuzzy-only quote test: an anchor quote that is not exact or normalized-exact must raise instead of using the older fragment fallback.
- [ ] Implement a strict anchor quote resolver that allows exact and normalized-exact matching but not the fragment-scanning fallback in `find_quote_start()`.
- [ ] Run `python -B -m unittest tests.test_generate_outline_source -v`.

## Task 2: Make Skeleton Generation Emit Anchors

**Files:**
- Modify `scripts/generate_outline_deepseek.py`
- Modify `tests/test_generate_outline_source.py`

- [ ] Update `build_skeleton_prompt()` and `call_skeleton_merge_pass()` to require exactly one `outline-anchor` comment after every `##`.
- [ ] Change `call_skeleton_pass()` and `call_skeleton_merge_pass()` to call `normalize_anchored_skeleton()` instead of `normalize_skeleton()`, so anchor comments survive the model response normalization.
- [ ] Keep `normalize_skeleton()` as the clean Markdown normalizer.
- [ ] Ensure every downstream `parse_chapters()` call receives `strip_skeleton_anchors(anchored_skeleton)` or an already clean skeleton; fill prompts must never include anchor comments.
- [ ] Update prompt tests to assert anchor requirements are present.
- [ ] Run focused tests.

## Task 3: Build Granularity From Skeleton Anchors

**Files:**
- Modify `scripts/generate_outline_deepseek.py`
- Modify `tests/test_generate_outline_source.py`

- [ ] Rewrite `build_granularity_plan_from_skeleton(transcript, anchored_skeleton)` so it parses skeleton anchors and calls `build_granularity_plan_from_locations()`.
- [ ] Add `start`, `start_quote`, and `location_source` fields to granularity items for auditability.
- [ ] Remove partial/sparse/estimated granularity fallback code that existed only for title-location failures.
- [ ] Update every call site; `build_granularity_plan_from_skeleton()` must no longer accept `client` or `prompt_template`.
- [ ] Rewrite granularity tests around real anchor spans.
- [ ] Run focused tests.

## Task 4: Use Anchors For Final Slicing

**Files:**
- Modify `scripts/generate_outline_deepseek.py`
- Modify `tests/test_generate_outline_source.py`

- [ ] Introduce a small dataclass, for example `SkeletonGenerationResult`, with `skeleton`, `anchored_skeleton`, `granularity_plan`, and `locations`.
- [ ] Change `generate_skeleton_with_granularity()` to return `SkeletonGenerationResult`.
- [ ] Update both current callers, `main()` and `run_skeleton_only_experiment()`, in the same patch.
- [ ] In `main()`, write `outline_skeleton_anchored.md`, `outline_skeleton.md`, `outline_granularity.json`, and `outline_locations.json` from the same final anchors.
- [ ] Remove the final `call_location_pass_windowed()` path when anchors are available.
- [ ] Resume branch behavior:
  - if `outline_skeleton_anchored.md` exists, derive clean skeleton, granularity, and locations from it;
  - else if clean `outline_skeleton.md` and valid `outline_locations.json` exist, reuse those locations for legacy compatibility and compute granularity from them;
  - else fail with a clear "regenerate skeleton to create anchors" error;
  - never call `call_location_pass_windowed()` to create skeleton granularity.
- [ ] Update final validation so new runs accept only `source="skeleton_anchor"`, while the explicit legacy resume branch may still accept `"reused"` after parsing old `outline_locations.json`.
- [ ] Add a test proving final slicing uses `skeleton_anchor` locations and does not call the location LLM.
- [ ] Add a legacy resume test proving clean skeleton plus valid `outline_locations.json` still works, and a missing-anchor/missing-locations resume test proving it fails clearly.
- [ ] Run focused tests.

## Task 5: Update Skeleton-Only Experiment Outputs

**Files:**
- Modify `scripts/generate_outline_deepseek.py`
- Modify `tests/test_generate_outline_source.py`

- [ ] Write per-run anchored skeleton files named `outline_skeleton_anchored_policy_XX_run_YY.md`.
- [ ] Write clean skeleton files to the existing `outline_skeleton_policy_XX_run_YY.md` names.
- [ ] Add anchored skeleton path/hash fields to `outline_skeleton_experiment.json`.
- [ ] Update existing skeleton-only experiment tests to assert `anchored_skeleton_path` and `anchored_skeleton_sha256`, plus existence and content of both anchored and clean skeleton files.
- [ ] Run focused tests.

## Task 6: Full Verification And Rerun

**Files:**
- Generated output only.

- [ ] Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest discover -s tests -v
```

- [ ] Run `scripts\check_env.py`.
- [ ] Rerun 002 skeleton experiment into a fresh anchor directory.
- [ ] Inspect anchored skeletons, clean skeletons, granularity files, and manifest.
- [ ] Confirm granularity comes from `skeleton_anchor`, not `estimated_after_failure`, `candidate`, or sparse average fallback.

## Cleanup Checklist

- [ ] No `find_location_candidate` or location candidate tests remain unless still used outside skeleton granularity.
- [ ] No `PartialChapterLocationsError` or sparse/partial granularity builder remains unless still called.
- [ ] `build_granularity_plan_from_skeleton()` does not accept `client` or `prompt_template`.
- [ ] New-run final slicing cannot use any source except `skeleton_anchor`.
- [ ] Legacy resume is the only path allowed to accept `reused` locations.
- [ ] `outline_locations.json` records `source: "skeleton_anchor"` for new runs.
- [ ] `rg` confirms removed helper names have no remaining production callers.
