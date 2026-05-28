# Outline Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use inline execution for this scoped fix. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make repeated DeepSeek outline generation from the same transcript substantially more consistent by reducing fine-grained title freedom and rejecting invalid chapter locations.

**Architecture:** Keep the existing multi-pass outline generator. Tighten the repo prompt and inline pass prompts to prefer sparse headings and default to at most `###`; add deterministic validation so repeated or non-increasing chapter starts fail fast.

**Tech Stack:** Python 3.13, unittest, DeepSeek-compatible OpenAI client.

---

### Task 1: Add Deterministic Location Validation

**Files:**
- Modify: `scripts/generate_outline_deepseek.py`
- Modify: `tests/test_generate_outline_source.py`

- [ ] Add a unit test that constructs two `ChapterLocation` entries with identical `start` values and asserts validation raises `RuntimeError`.
- [ ] Implement `validate_chapter_locations(locations, transcript_len)` checking non-empty, IDs, strictly increasing starts, and bounds.
- [ ] Call validation after both fresh and reused location parsing.
- [ ] Run focused tests.

### Task 2: Reduce Fine-Grained Heading Freedom

**Files:**
- Modify: `prompt/prompt.md`
- Modify: `scripts/generate_outline_deepseek.py`

- [ ] Update the repo base outline prompt: default maximum heading depth is `###`; `####` only when the transcript explicitly names examples/steps/items.
- [ ] During real runs, set `OUTLINE_PROMPT_PATH=D:\tmp\video_wt\wt-clean-transcript\prompt\prompt.md` so the worktree prompt is the live prompt. Do not edit `D:\video\prompt\prompt.md` for this experiment.
- [ ] Update inline Pass 1 rules in `build_skeleton_prompt()` and `call_skeleton_merge_pass()` because those are the dominant heading-depth instructions.
- [ ] Update inline Pass 2 rules in `call_fill_chapter()`: do not reorganize prose around headings; headings are sparse road signs.
- [ ] Run focused tests.

### Task 3: Verify Stability With Two Real Calls

**Files:**
- Generated only under `D:\video\output\test1` and backups.

- [ ] Generate outline run A from raw transcript into a separate copy directory.
- [ ] Generate outline run B from the same raw transcript into another separate copy directory.
- [ ] Before each run, start from a fresh copy containing only the stable raw transcript inputs and course metadata; do not reuse stale `outline_*` files.
- [ ] Compare A/B with the same normalized body-text metric used by `scripts/evaluate_outline_quality.py`: remove Markdown headings/fences, NFKC normalize, remove whitespace, and use `difflib.SequenceMatcher(..., autojunk=False).ratio()`. Target self-similarity >= 0.95.
- [ ] If self-similarity passes, generate one final sample under `D:\video\output\test1` and run quality gate against the manual baseline.
