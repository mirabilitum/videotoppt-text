# Outline Program Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `scripts/generate_outline_deepseek.py` into focused outline modules while preserving CLI behavior, tests, and the anchored skeleton location contract.

**Architecture:** Keep `scripts/generate_outline_deepseek.py` as the thin command entry point and compatibility export surface during the first split. Move pure helpers first, then LLM prompt/call code, then experiment orchestration. Use top-level imports from sibling `scripts/` modules because existing scripts are executed directly and currently import siblings as `from common import ...`.

**Tech Stack:** Python 3.13, unittest, existing OpenAI client wrapper, PowerShell verification commands.

---

## Refactor Boundary

Current file:

```text
scripts/generate_outline_deepseek.py = 70,598 bytes
```

Target modules:

- `scripts/outline_models.py`
  - Dataclasses and type aliases: `ChatResult`, `ChapterLocation`, `TranscriptSource`, `SkeletonGenerationResult`, `OutlinePolicy`, `GranularityPlan`.
- `scripts/outline_io.py`
  - Prompt path, SHA helpers, transcript source selection, outline source metadata, completion checks.
- `scripts/outline_text.py`
  - Markdown fence stripping, env helpers, compact/normalized text helpers, heading normalization.
- `scripts/outline_skeleton.py`
  - Skeleton normalization, anchor parsing/stripping/reattaching, `parse_chapters()`, policy/granularity skeleton validation.
- `scripts/outline_locations.py`
  - Quote matching, chapter location validation, transcript slicing, legacy location parse/format.
- `scripts/outline_policy.py`
  - Policy JSON parsing, normalization, formatting, policy heading key logic.
- `scripts/outline_granularity.py`
  - `min_subsections_for_chars()`, granularity plan builders, granularity plan formatting.
- `scripts/outline_llm.py`
  - `call_chat()`, policy pass, skeleton pass, chunked skeleton pass, intro pass, fill chapter pass.
- `scripts/outline_experiment.py`
  - Skeleton-only experiment manifest and loop.
- `scripts/generate_outline_deepseek.py`
  - CLI args and `main()` orchestration.
  - Temporary re-exports for tests and callers until imports are migrated.

## Task 1: Create Shared Models

**Files:**

- Create: `scripts/outline_models.py`
- Modify: `scripts/generate_outline_deepseek.py`
- Test: `tests/test_generate_outline_source.py`

- [ ] Move the dataclasses and aliases into `scripts/outline_models.py`.
- [ ] Import them back in `scripts/generate_outline_deepseek.py`.
- [ ] Keep the public names available from `scripts.generate_outline_deepseek` so current tests continue to import without churn.
- [ ] Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected:

```text
OK
```

## Task 2: Move Pure Text And IO Helpers

**Files:**

- Create: `scripts/outline_text.py`
- Create: `scripts/outline_io.py`
- Modify: `scripts/generate_outline_deepseek.py`
- Test: `tests/test_generate_outline_source.py`

- [ ] Move pure text helpers:
  - `strip_markdown_fence`
  - `env_int`
  - `env_float`
  - `compact_text`
  - `normalize_with_index`
  - `strip_heading_number`
  - `chapter_heading`
  - `find_first`
- [ ] Move IO/source helpers:
  - `outline_prompt_path`
  - `sha256_text`
  - `file_sha256`
  - `select_transcript_source`
  - `outline_source_path`
  - `outline_policy_path`
  - `outline_source_payload`
  - `write_outline_source`
  - `outline_inputs_match`
  - `outline_source_policy_matches`
  - `outline_complete`
  - `read_course_title`
- [ ] Import helpers back into `generate_outline_deepseek.py`.
- [ ] Run focused tests as in Task 1.

## Task 3: Move Policy And Skeleton Helpers

**Files:**

- Create: `scripts/outline_policy.py`
- Create: `scripts/outline_skeleton.py`
- Modify: `scripts/generate_outline_deepseek.py`
- Test: `tests/test_generate_outline_source.py`

- [ ] Move policy helpers:
  - `parse_json_object`
  - `normalize_policy_heading_key`
  - `normalize_policy_items`
  - `normalize_parallel_groups`
  - `normalize_outline_policy`
  - `parse_outline_policy`
  - `read_outline_policy`
  - `write_outline_policy`
  - `format_outline_policy`
- [ ] Move skeleton helpers:
  - `normalize_skeleton`
  - `normalize_anchored_skeleton`
  - `strip_skeleton_anchors`
  - `attach_skeleton_anchors`
  - `apply_course_title`
  - `cap_heading_depths`
  - `build_skeleton_prompt`
  - `parse_chapters`
  - `parse_skeleton_anchor_locations`
  - `validate_skeleton_matches_policy`
  - `validate_skeleton_matches_granularity`
- [ ] Keep anchor parsing exact-only: no fuzzy quote fallback.
- [ ] Run focused tests as in Task 1.

## Task 4: Move Location And Granularity Helpers

**Files:**

- Create: `scripts/outline_locations.py`
- Create: `scripts/outline_granularity.py`
- Modify: `scripts/generate_outline_deepseek.py`
- Test: `tests/test_generate_outline_source.py`

- [ ] Move location helpers:
  - `find_quote_start`
  - `find_anchor_quote_start`
  - `heading_search_terms`
  - `find_heading_start`
  - `validate_chapter_locations`
  - `validate_final_chapter_locations`
  - `validate_anchor_chapter_locations`
  - `slice_chapter_transcripts`
  - `parse_chapter_locations`
  - `format_chapter_locations`
  - `read_legacy_chapter_locations`
- [ ] Move granularity helpers:
  - `min_subsections_for_chars`
  - `build_granularity_plan_from_locations`
  - `build_policy_granularity_plan`
  - `build_granularity_plan_from_skeleton`
  - `format_granularity_plan`
- [ ] Confirm `build_granularity_plan_from_skeleton()` still requires anchors and never estimates by average.
- [ ] Run focused tests as in Task 1.

## Task 5: Move LLM Calls And Experiment Orchestration

**Files:**

- Create: `scripts/outline_llm.py`
- Create: `scripts/outline_experiment.py`
- Modify: `scripts/generate_outline_deepseek.py`
- Test: `tests/test_generate_outline_source.py`

- [ ] Move LLM call helpers:
  - `call_chat`
  - `call_outline_policy_pass`
  - `call_skeleton_pass`
  - `split_transcript_chunks`
  - `call_skeleton_merge_pass`
  - `call_skeleton_pass_chunked`
  - `generate_skeleton_from_policy`
  - `generate_skeleton_with_granularity`
  - `call_intro_pass`
  - `call_fill_chapter`
- [ ] Move skeleton-only experiment helpers:
  - `write_skeleton_experiment_manifest`
  - `run_skeleton_only_experiment`
- [ ] Keep `generate_outline_deepseek.py` responsible for:
  - `parse_args()`
  - `main()`
  - orchestration of policy, skeleton, locations, fill, and metadata writes
  - temporary re-exports
- [ ] Run focused tests as in Task 1.

## Task 6: Migrate Tests To Module Imports

**Files:**

- Modify: `tests/test_generate_outline_source.py`

- [ ] Update tests to import helpers from their owning modules instead of `scripts.generate_outline_deepseek`.
- [ ] Keep only CLI-level tests importing `parse_args`, `outline_complete`, or `main`-adjacent behavior from `scripts.generate_outline_deepseek`.
- [ ] Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected:

```text
OK
```

## Task 7: Full Verification And 002 Smoke Rerun

**Files:**

- No new source files unless tests reveal a real issue.

- [ ] Run full unittest:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest discover -s tests -v
```

Expected:

```text
OK
```

- [ ] Run environment check with `.env` loaded:

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B scripts\check_env.py
```

Expected:

```text
DEEPSEEK_API_KEY=set
```

- [ ] Rerun the existing anchored 002 skeleton-only smoke test in a fresh output directory.
- [ ] Inspect the rerun:
  - manifest has valid skeleton records or clear model validation errors
  - all generated granularity locations use `skeleton_anchor`
  - clean skeleton files do not contain `outline-anchor`
  - anchored skeleton files contain `outline-anchor`

## Guardrails

- Do not change CLI arguments or generated file names during this split.
- Do not change prompt behavior unless a test exposes a necessary import issue.
- Do not reintroduce removed candidate/sparse/estimated location generation.
- Do not add `scripts/__init__.py` as part of this split unless import behavior requires it and tests prove it is safe.
- Keep each task mechanically small: move code, import it, run focused tests.

## Before Starting Tomorrow

- Send this plan to CC review before implementation if we want the same review discipline as today's anchor work.
- Start from Task 1 and stop after the first unexpected behavior instead of combining multiple module moves.
- Use `git diff -- scripts/generate_outline_deepseek.py scripts/outline_*.py tests/test_generate_outline_source.py` after each task to ensure the split is mechanical.

