# Outline Location Stability Design

## Context

The outline pipeline now uses `policy` only as a theme and top-level structure guard. It should not be used to locate or cut transcript text.

The 2026-05-26 `002_policy1_skeleton2_granularity_t02` run exposed a shared location weakness:

- `build_granularity_plan_from_skeleton()` generated a draft skeleton and called `call_location_pass_windowed()`.
- `call_location_pass_windowed()` located chapters 1-5, then chapter 6 returned non-JSON text such as "抱歉，这个问题未找到相关结果。"
- `build_granularity_plan_from_skeleton()` caught the exception and discarded all successful locations, falling back to a whole-outline average plan.
- The resulting granularity plan assigned nearly every chapter `source_chars ~= 1299`, so the later skeleton was constrained by average estimates rather than real chapter lengths.

The same `call_location_pass_windowed()` function is also used before final body filling:

```text
main()
  -> call_location_pass_windowed()
  -> slice_chapter_transcripts()
  -> call_fill_chapter()
```

Therefore location robustness affects both:

- skeleton granularity planning
- final transcript slicing and body injection

## Root Cause Analysis

The chapter 6 title was abstract and composite:

```text
课程的系统性与均衡性调整
```

The transcript does not contain this exact phrase as a single local heading. Instead, it has separate spoken transitions:

```text
9523  第三个方面啊就是课程的系统性
10308 均衡性
```

The LLM was asked to map an abstract synthesized heading onto a long transcript window that still contained the previous chapter tail. This created ambiguity:

- "系统性" points near 9523.
- "均衡性" points near 10200-10308.
- the window starts at 8471 and includes the end of "课程的政治属性与习近平法治思想".

The location prompt also includes the full base outline prompt before the location-specific instruction. That is unnecessary for a pure quote-finding task and increases the chance the model treats the request as an open QA task instead of a strict JSON extraction task.

## Design Goals

1. Make the shared location pass more deterministic.
2. Allow granularity planning to degrade gracefully when a later chapter cannot be located.
3. Keep final transcript slicing strict enough to avoid silently corrupting `outline.md`.
4. Preserve the rule that `policy` constrains theme/order only and never becomes a transcript anchor.

## Non-Goals

- Do not redesign policy generation.
- Do not use `policy.top_level_items` to locate transcript spans.
- Do not estimate final body slicing boundaries if location confidence is low.
- Do not rewrite the main outline prompt except where the location pass currently inherits it unnecessarily.

## Proposed Approach

### 1. Shorten and Harden the Shared Location Prompt

Change `call_location_pass_windowed()` so it no longer prepends `prompt_template`.

The location prompt should contain only:

- current `chapter_id`
- total chapter count
- current top-level heading
- optionally previous and next top-level headings
- absolute search window offsets
- the transcript window
- the JSON contract

The prompt should explicitly say:

- the Markdown heading may not appear verbatim in the transcript
- if the exact heading is absent, choose the earliest transcript sentence where the same topic begins
- always output one JSON object
- never output apologies, explanations, Markdown, or "not found"
- `start_quote` must be copied verbatim from the search window

Example output contract:

```json
{"chapter_id": 6, "start_quote": "第三个方面啊就是课程的系统性"}
```

This change benefits both granularity and final body slicing because both call the same function.

### 2. Add Deterministic Candidate Fallback Inside Location Parsing

When the LLM response is not parseable JSON or the returned quote cannot be found, the caller should have a deterministic fallback before giving up.

Fallback inputs:

- current chapter heading
- chapter subtree headings
- transcript window
- `search_from`

Fallback strategy:

1. Build candidate terms from the top-level heading and its `###` headings.
2. Strip numbering and punctuation.
3. Split composite titles on common joiners such as `与`, `和`, `及`, `、`, `：`, `:`.
4. Drop generic terms such as `课程`, `内容`, `设计`, `调整`, `理念`, `方面`, `介绍`, `整体`.
5. Search terms in transcript order after `search_from`.
6. Prefer candidate positions that appear after transition markers like `第一个方面`, `第二个方面`, `第三个方面`, `第四个方面`, or `接下来`.
7. Return a `ChapterLocation` only when a candidate quote is found inside the search window and after the previous chapter start.

For the observed chapter 6 case, acceptable fallback starts include:

```text
第三个方面啊就是课程的系统性
```

or, if that is missed:

```text
无论是从课程的系统性上
```

The fallback should not use `policy`.

### 3. Split Location Strictness by Caller

Use one shared location mechanism but different failure policies.

Granularity planning:

- `build_granularity_plan_from_skeleton()` should call a tolerant location helper.
- If chapters 1-5 are located and chapter 6 fails, preserve the 1-5 real starts.
- Estimate only the remaining unresolved span from the last known start to the end of transcript.
- Mark estimated plan items with metadata, for example:

```json
{
  "top_level_item": "六、课程的系统性与均衡性调整",
  "source_chars": 1187,
  "min_subsections": 2,
  "max_depth": 4,
  "location_source": "estimated_after_failure"
}
```

Final transcript slicing:

- `main()` should keep using strict location.
- It can benefit from the shorter prompt and deterministic candidate fallback.
- If a chapter still cannot be located, it should raise and stop.
- It must not use average estimates to slice transcript text.

This avoids corrupting `outline.md` while still making skeleton granularity resilient.

## API Shape

Suggested internal functions:

```python
def build_location_prompt(
    transcript_window: str,
    *,
    chapter_id: int,
    chapter_count: int,
    heading: str,
    previous_heading: str | None,
    next_heading: str | None,
    window_start: int,
    window_end: int,
) -> str:
    ...


def find_location_candidate(
    transcript: str,
    window: str,
    chapter_subtree: str,
    *,
    search_from: int,
    window_start: int,
) -> ChapterLocation | None:
    ...


def call_location_pass_windowed(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    chapters: list[tuple[int, str]],
    *,
    allow_partial: bool = False,
) -> list[ChapterLocation]:
    ...
```

`prompt_template` can remain in the signature for compatibility, but the windowed location prompt should not include it.

If `allow_partial=False`, any unresolved chapter raises after retries and deterministic fallback fail.

If `allow_partial=True`, the function may return successfully located leading chapters, or a separate helper may catch a new `PartialChapterLocationsError` that carries `locations`.

## Granularity Fallback Contract

If only a prefix of chapter starts is known:

1. Build exact source lengths for known adjacent starts.
2. For the last known chapter and unresolved chapters, divide the remaining transcript span across the remaining chapters.
3. Preserve chapter order and count.
4. Add `location_source`:
   - `llm`
   - `candidate`
   - `estimated_after_failure`

This makes the output inspectable and prevents silent whole-plan averaging.

## Testing Plan

Unit tests should cover:

- `build_location_prompt()` does not include the base outline prompt and contains a strict JSON-only contract.
- `find_location_candidate()` can locate a composite heading like `课程的系统性与均衡性调整` from transcript text containing `第三个方面啊就是课程的系统性` and later `均衡性`.
- strict location raises when LLM returns non-JSON and no candidate exists.
- tolerant granularity preserves successful prefix locations and estimates only unresolved suffix chapters.
- final slicing path does not accept estimated locations.

Focused command:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Full command:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest discover -s tests -v
```

## Open Decisions

1. Whether partial locations should be represented by a custom exception carrying successful prefix locations, or by a separate tolerant helper used only by granularity.
2. Whether candidate fallback should search only the top-level heading or also `###` headings. Recommendation: include `###` headings because composite `##` titles often summarize lower-level concrete topics.
3. Whether to record `location_source` in final `outline_locations.json`. Recommendation: yes, for auditability, while still rejecting estimated locations for final slicing.
