# Outline Location Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make chapter start detection stable enough for skeleton granularity planning while keeping final transcript slicing strict and auditable.

**Architecture:** Keep one shared windowed location mechanism, but separate its prompt construction, deterministic fallback, and failure policy. Granularity planning can preserve located prefix chapters and estimate only the unresolved suffix; final outline filling must never slice transcript text from estimated starts.

**Tech Stack:** Python 3.13, unittest, DeepSeek-compatible OpenAI client, existing script-oriented pipeline in `scripts/generate_outline_deepseek.py`.

---

## File Structure

- Modify `scripts/generate_outline_deepseek.py`: add prompt builder, candidate fallback helpers, partial-location error, `location_source` metadata, prefix-preserving granularity fallback, and final-location validation.
- Modify `tests/test_generate_outline_source.py`: add focused unit tests for prompt shape, candidate fallback, partial failures, granularity fallback, metadata serialization, and strict final slicing.
- Generated only during verification: `D:\video\output\policy_skeleton_experiment_20260526\002_policy1_skeleton2_granularity_t02_rerun` or another new output directory. Do not overwrite the previous experiment directory.

## Implementation Notes

- `policy` remains only a theme/order guard. Do not use policy text or policy top-level items for transcript locating.
- `prompt_template` stays in `call_location_pass_windowed(...)` for compatibility, but the windowed location prompt must not include it.
- `call_location_pass(...)` is legacy non-windowed code. Do not add new callers. Leave it unchanged except for a short comment if touched.
- Location source values are fixed strings: `"llm"`, `"candidate"`, `"reused"`, and `"estimated_after_failure"`.
- A location with source `"estimated_after_failure"` is valid for granularity metadata only and invalid for final transcript slicing.
- `find_location_candidate(...)` is a best-effort fallback: malformed headings, absent terms, or unexpected matching edge cases must return `None`, not raise. This preserves the original LLM parse error for strict-mode diagnostics.
- When `allow_partial=True` and chapter 1 cannot be located, `call_location_pass_windowed(...)` raises the original `RuntimeError`; `build_granularity_plan_from_skeleton(...)` catches it with the generic fallback and estimates the whole plan. `PartialChapterLocationsError` is only for non-empty, successfully located prefixes.
- Old `outline_locations.json` files have no `source`; parsing them as `"reused"` is intentional because they were previously accepted final locations and must remain reusable unless normal position validation fails.

### Task 1: Add Location Metadata And Prompt Builder

**Files:**
- Modify: `scripts/generate_outline_deepseek.py`
- Modify: `tests/test_generate_outline_source.py`

- [ ] **Step 1: Write failing tests for metadata and prompt shape**

Add imports in `tests/test_generate_outline_source.py`:

```python
from scripts.generate_outline_deepseek import (
    ChapterLocation,
    ChatResult,
    build_granularity_plan_from_skeleton,
    build_location_prompt,
    build_skeleton_prompt,
    call_outline_policy_pass,
    call_skeleton_pass_chunked,
    format_chapter_locations,
    min_subsections_for_chars,
    normalize_skeleton,
    outline_complete,
    parse_chapter_locations,
    parse_chapters,
    parse_args,
    parse_outline_policy,
    run_skeleton_only_experiment,
    select_transcript_source,
    validate_chapter_locations,
    validate_skeleton_matches_granularity,
    validate_skeleton_matches_policy,
    write_outline_source,
)
```

Add tests:

```python
    def test_build_location_prompt_excludes_base_prompt_and_requires_json_object(self) -> None:
        prompt = build_location_prompt(
            "alpha transcript",
            chapter_id=2,
            chapter_count=3,
            heading="课程的系统性与均衡性调整",
            previous_heading="政治属性",
            next_heading="降低难度",
            window_start=100,
            window_end=500,
        )

        self.assertNotIn("outline prompt", prompt)
        self.assertIn("chapter_id", prompt)
        self.assertIn("start_quote", prompt)
        self.assertIn("JSON object", prompt)
        self.assertIn("never output apologies", prompt)
        self.assertIn("100", prompt)
        self.assertIn("500", prompt)
        self.assertIn("alpha transcript", prompt)

    def test_format_and_parse_chapter_locations_preserve_source(self) -> None:
        transcript = "alpha start beta start"
        locations = [
            ChapterLocation(1, "Alpha", "alpha start", 0, source="candidate"),
            ChapterLocation(2, "Beta", "beta start", 12, source="llm"),
        ]

        content = format_chapter_locations(locations)
        parsed = parse_chapter_locations(
            content,
            transcript,
            [(1, "## Alpha"), (2, "## Beta")],
        )

        self.assertEqual([item.source for item in parsed], ["candidate", "llm"])
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected: fail because `build_location_prompt` is not imported/defined and `ChapterLocation` has no `source` field.

- [ ] **Step 3: Add source metadata and prompt builder**

In `scripts/generate_outline_deepseek.py`, update the dataclass:

```python
@dataclass(frozen=True)
class ChapterLocation:
    chapter_id: int
    heading: str
    start_quote: str
    start: int
    source: str = "llm"
```

Add the prompt builder near `parse_single_chapter_location()`:

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
    previous_block = previous_heading or "(none)"
    next_block = next_heading or "(none)"
    return f"""Pass 1.2: locate the transcript start for one top-level outline chapter.

Rules:
- Work only from the chapter heading and transcript search window below.
- The Markdown heading may not appear verbatim in the transcript.
- If the exact heading is absent, choose the earliest sentence where the same topic begins.
- start_quote must be copied verbatim from the transcript search window.
- Output exactly one JSON object.
- Do not output Markdown fences.
- Do not explain the answer.
- never output apologies, "not found", or any non-JSON text.

Current chapter: {chapter_id}/{chapter_count}
Previous top-level heading: {previous_block}
Current top-level heading: {heading}
Next top-level heading: {next_block}
Absolute search window offsets: {window_start} to {window_end}

JSON object shape:
{{"chapter_id": {chapter_id}, "start_quote": "copy a short opening quote from the transcript window"}}

Transcript search window:
```text
{transcript_window.strip()}
```"""
```

Update `format_chapter_locations(...)`:

```python
def format_chapter_locations(locations: list[ChapterLocation]) -> str:
    return json.dumps(
        [
            {
                "chapter_id": location.chapter_id,
                "heading": location.heading,
                "start_quote": location.start_quote,
                "start": location.start,
                "source": location.source,
            }
            for location in locations
        ],
        ensure_ascii=False,
        indent=2,
    )
```

Update `parse_chapter_locations(...)` to read optional source metadata while defaulting old files to `"reused"`:

```python
    by_id: dict[int, dict[str, str]] = {}
    for item in raw_locations:
        if not isinstance(item, dict):
            raise RuntimeError("Each chapter location item must be a JSON object.")
        chapter_id = int(item.get("chapter_id", 0))
        start_quote = str(item.get("start_quote", "")).strip()
        source = str(item.get("source", "reused")).strip() or "reused"
        if chapter_id in by_id:
            raise RuntimeError(f"Duplicate chapter location: {chapter_id}")
        by_id[chapter_id] = {"start_quote": start_quote, "source": source}
```

Then inside the loop:

```python
        start_quote = by_id[chapter_id]["start_quote"]
        source = by_id[chapter_id]["source"]
```

And when appending:

```python
        locations.append(
            ChapterLocation(
                chapter_id=chapter_id,
                heading=chapter_heading(chapter_subtree),
                start_quote=start_quote,
                start=start,
                source=source,
            )
        )
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected: new prompt and metadata tests pass; later tasks may still be unimplemented.

### Task 2: Add Deterministic Candidate Fallback

**Files:**
- Modify: `scripts/generate_outline_deepseek.py`
- Modify: `tests/test_generate_outline_source.py`

- [ ] **Step 1: Write failing candidate fallback tests**

Add imports:

```python
    find_location_candidate,
    location_candidate_terms,
```

Add tests:

```python
    def test_location_candidate_terms_split_composite_heading_and_drop_generic_terms(self) -> None:
        terms = location_candidate_terms(
            """## 课程的系统性与均衡性调整
### 降低课程难度
### 综合实践活动""",
        )

        compact_terms = [item.replace(" ", "") for item in terms]
        self.assertIn("系统性", compact_terms)
        self.assertIn("均衡性", compact_terms)
        self.assertIn("降低课程难度", compact_terms)
        self.assertNotIn("课程", compact_terms)
        self.assertNotIn("调整", compact_terms)

    def test_find_location_candidate_handles_composite_abstract_heading(self) -> None:
        transcript = (
            "上一章结尾内容。"
            "第三个方面啊就是课程的系统性，这一点大家要注意。"
            "后面还会讲到均衡性。"
        )
        location = find_location_candidate(
            transcript,
            transcript,
            """## 课程的系统性与均衡性调整
### 降低课程难度""",
            chapter_id=6,
            search_from=0,
            window_start=0,
        )

        self.assertIsNotNone(location)
        assert location is not None
        self.assertEqual(location.chapter_id, 6)
        self.assertEqual(location.source, "candidate")
        self.assertEqual(location.start, transcript.index("第三个方面"))
        self.assertIn("课程的系统性", location.start_quote)
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected: fail because `location_candidate_terms` and `find_location_candidate` are undefined.

- [ ] **Step 3: Implement candidate helpers**

Add near `heading_search_terms(...)`:

```python
LOCATION_GENERIC_TERMS = {
    "课程",
    "内容",
    "设计",
    "调整",
    "理念",
    "方面",
    "介绍",
    "整体",
    "说明",
    "章节",
    "单元",
}

LOCATION_JOINER_RE = re.compile(r"[与和及、：:,/]+")
LOCATION_TRANSITION_MARKERS = (
    "第一个方面",
    "第二个方面",
    "第三个方面",
    "第四个方面",
    "第五个方面",
    "接下来",
    "下面",
    "再来看",
)


def location_candidate_terms(chapter_subtree: str) -> list[str]:
    raw_terms: list[str] = []
    for raw_line in chapter_subtree.splitlines():
        match = HEADING_RE.match(raw_line.strip())
        if not match:
            continue
        depth = len(match.group(1))
        if depth not in (2, 3):
            continue
        title = strip_heading_number(match.group(2).strip())
        title = re.sub(r"^\d+(?:\.\d+)*\s*", "", title).strip()
        raw_terms.append(title)
        raw_terms.extend(part.strip() for part in LOCATION_JOINER_RE.split(title))

    seen: set[str] = set()
    terms: list[str] = []
    for term in raw_terms:
        term = term.strip(" \t\r\n，。；;（）()《》“”\"'")
        compact = compact_text(term)
        if len(compact) < 3:
            continue
        if compact in LOCATION_GENERIC_TERMS:
            continue
        if compact in seen:
            continue
        seen.add(compact)
        terms.append(term)
    return terms
```

Add near `find_heading_start(...)`:

```python
def quote_around(text: str, start: int, length: int = 48) -> str:
    end = min(len(text), start + length)
    quote = text[start:end].strip()
    return quote or text[start:end]


def find_location_candidate(
    transcript: str,
    window: str,
    chapter_subtree: str,
    *,
    chapter_id: int,
    search_from: int,
    window_start: int,
) -> ChapterLocation | None:
    try:
        heading = chapter_heading(chapter_subtree)
        terms = location_candidate_terms(chapter_subtree)
        matches: list[tuple[int, int, str]] = []
        for term in terms:
            start = find_quote_start(transcript, term, search_from)
            if start < window_start or start >= window_start + len(window):
                continue
            marker_start = max(search_from, start - 80)
            prefix = transcript[marker_start:start]
            transition_bonus = 0 if any(marker in prefix for marker in LOCATION_TRANSITION_MARKERS) else 1
            candidate_start = start
            marker_positions = [
                transcript.rfind(marker, marker_start, start)
                for marker in LOCATION_TRANSITION_MARKERS
            ]
            marker_positions = [item for item in marker_positions if item >= search_from]
            if marker_positions:
                candidate_start = min(marker_positions)
            matches.append((transition_bonus, candidate_start, term))

        if not matches:
            return None

        _, start, _ = min(matches, key=lambda item: (item[0], item[1]))
        return ChapterLocation(
            chapter_id=chapter_id,
            heading=heading,
            start_quote=quote_around(transcript, start),
            start=start,
            source="candidate",
        )
    except Exception:
        return None
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected: prompt/metadata/candidate tests pass.

### Task 3: Add Partial Location Semantics To Windowed Location

**Files:**
- Modify: `scripts/generate_outline_deepseek.py`
- Modify: `tests/test_generate_outline_source.py`

- [ ] **Step 1: Write failing tests for strict and partial modes**

Add imports:

```python
    PartialChapterLocationsError,
    call_location_pass_windowed,
```

Add a tiny fake client helper inside `GenerateOutlineSourceTests`:

```python
    def make_chat_client(self, responses: list[str]) -> object:
        class FakeChoice:
            def __init__(self, content: str) -> None:
                self.message = type("Message", (), {"content": content})()
                self.finish_reason = "stop"

        class FakeCompletions:
            def __init__(self, items: list[str]) -> None:
                self.items = list(items)

            def create(self, **kwargs: object) -> object:
                if not self.items:
                    raise RuntimeError("no fake responses left")
                return type("Response", (), {"choices": [FakeChoice(self.items.pop(0))]})()

        class FakeChat:
            def __init__(self, items: list[str]) -> None:
                self.completions = FakeCompletions(items)

        return type("FakeClient", (), {"chat": FakeChat(responses)})()
```

Add tests:

```python
    def test_call_location_pass_windowed_uses_candidate_when_llm_returns_non_json(self) -> None:
        transcript = "intro。第三个方面啊就是课程的系统性，继续讲。"
        chapters = [(1, "## 课程的系统性与均衡性调整")]
        client = self.make_chat_client(["抱歉，这个问题未找到相关结果。"])

        locations = call_location_pass_windowed(client, "base prompt", transcript, chapters)

        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0].source, "candidate")
        self.assertEqual(locations[0].start, transcript.index("第三个方面"))

    def test_call_location_pass_windowed_strict_raises_when_no_json_and_no_candidate(self) -> None:
        transcript = "intro only"
        chapters = [(1, "## 完全不存在的主题")]
        client = self.make_chat_client(["抱歉，这个问题未找到相关结果。"])

        with self.assertRaisesRegex(RuntimeError, "Invalid chapter location JSON"):
            call_location_pass_windowed(client, "base prompt", transcript, chapters)

    def test_call_location_pass_windowed_partial_error_carries_located_prefix(self) -> None:
        transcript = "alpha start " + ("x" * 50) + " unrelated tail"
        chapters = [(1, "## Alpha"), (2, "## Missing")]
        client = self.make_chat_client(
            [
                json.dumps({"chapter_id": 1, "start_quote": "alpha start"}),
                "not json",
            ]
        )

        with self.assertRaises(PartialChapterLocationsError) as caught:
            call_location_pass_windowed(client, "base prompt", transcript, chapters, allow_partial=True)

        self.assertEqual([item.chapter_id for item in caught.exception.locations], [1])

    def test_call_location_pass_windowed_partial_mode_raises_plain_error_when_first_chapter_fails(self) -> None:
        transcript = "intro only"
        chapters = [(1, "## Missing"), (2, "## Also Missing")]
        client = self.make_chat_client(["not json"])

        with self.assertRaisesRegex(RuntimeError, "Invalid chapter location JSON"):
            call_location_pass_windowed(client, "base prompt", transcript, chapters, allow_partial=True)
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected: fail because partial error and new call signature do not exist.

- [ ] **Step 3: Implement partial error, shorter prompt use, and fallback**

Add near `ChapterLocation`:

```python
class PartialChapterLocationsError(RuntimeError):
    def __init__(self, message: str, locations: list[ChapterLocation]) -> None:
        super().__init__(message)
        self.locations = list(locations)
```

Update `parse_single_chapter_location(...)` so LLM JSON locations are explicitly marked:

```python
    return ChapterLocation(
        chapter_id=chapter_id,
        heading=heading,
        start_quote=start_quote,
        start=start,
        source="llm",
    )
```

Update the `call_location_pass_windowed(...)` signature:

```python
def call_location_pass_windowed(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    chapters: list[tuple[int, str]],
    *,
    allow_partial: bool = False,
) -> list[ChapterLocation]:
```

Replace the inline `user_prompt = f"""..."""` block with:

```python
            current_index = index - 1
            previous_heading = (
                chapter_heading(chapters[current_index - 1][1])
                if current_index > 0
                else None
            )
            next_heading = (
                chapter_heading(chapters[current_index + 1][1])
                if current_index + 1 < len(chapters)
                else None
            )
            user_prompt = build_location_prompt(
                window,
                chapter_id=chapter_id,
                chapter_count=len(chapters),
                heading=heading,
                previous_heading=previous_heading,
                next_heading=next_heading,
                window_start=prev_start,
                window_end=window_end,
            )
```

Replace the `except Exception as exc:` body inside the multiplier loop with:

```python
            except Exception as exc:
                last_error = exc
                candidate = find_location_candidate(
                    transcript,
                    window,
                    chapter_subtree,
                    chapter_id=chapter_id,
                    search_from=prev_start,
                    window_start=prev_start,
                )
                if candidate is not None:
                    location = candidate
                elif multiplier == 1 and window_end < len(transcript):
                    print(
                        "Pass 1.2: extending location window "
                        f"chapter={chapter_id} chars={window_chars * 2}"
                    )
                    continue
                else:
                    if allow_partial and locations:
                        raise PartialChapterLocationsError(str(exc), locations) from exc
                    raise
```

Keep the append/print/break block common after parse or candidate fallback:

```python
            locations.append(location)
            prev_start = location.start
            print(
                f"Pass 1.2: located chapter {index}/{len(chapters)} "
                f"start={location.start} source={location.source}"
            )
            break
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected: windowed location strict/partial tests pass.

### Task 4: Preserve Located Prefix In Granularity Plans

**Files:**
- Modify: `scripts/generate_outline_deepseek.py`
- Modify: `tests/test_generate_outline_source.py`

- [ ] **Step 1: Rewrite the old whole-fallback test and add metadata assertions**

Replace `test_build_granularity_plan_from_skeleton_falls_back_to_estimated_plan` with:

```python
    def test_build_granularity_plan_from_skeleton_preserves_prefix_and_estimates_suffix(self) -> None:
        transcript = "Alpha start " + ("a" * 1200) + " Beta start " + ("b" * 500) + " Gamma tail"
        skeleton = """# T

## Alpha

## Beta

## Gamma
"""
        beta_start = transcript.index("Beta start")
        prefix = [
            ChapterLocation(1, "Alpha", "Alpha start", 0, source="llm"),
            ChapterLocation(2, "Beta", "Beta start", beta_start, source="candidate"),
        ]

        with patch(
            "scripts.generate_outline_deepseek.call_location_pass_windowed",
            side_effect=PartialChapterLocationsError("chapter 2 failed", prefix),
        ):
            plan, chapters = build_granularity_plan_from_skeleton(
                object(),
                "prompt",
                transcript,
                skeleton,
            )

        self.assertEqual([item["top_level_item"] for item in plan], ["Alpha", "Beta", "Gamma"])
        self.assertEqual(len(chapters), 3)
        self.assertEqual(plan[0]["location_source"], "llm")
        self.assertGreater(plan[0]["source_chars"], 1000)
        self.assertEqual(plan[1]["location_source"], "candidate")
        self.assertEqual(plan[2]["location_source"], "estimated_after_failure")

    def test_build_granularity_plan_from_skeleton_still_fully_estimates_when_no_prefix_exists(self) -> None:
        transcript = "intro " + ("x" * 400) + " Beta " + ("y" * 100)
        skeleton = """# T

## Alpha

## Beta
"""

        with patch(
            "scripts.generate_outline_deepseek.call_location_pass_windowed",
            side_effect=RuntimeError("location failed"),
        ):
            plan, chapters = build_granularity_plan_from_skeleton(
                object(),
                "prompt",
                transcript,
                skeleton,
            )

        self.assertEqual([item["top_level_item"] for item in plan], ["Alpha", "Beta"])
        self.assertEqual(len(chapters), 2)
        self.assertEqual(
            [item["location_source"] for item in plan],
            ["estimated_after_failure", "estimated_after_failure"],
        )
        self.assertEqual(plan[0]["min_subsections"], 0)
        self.assertEqual(plan[1]["min_subsections"], 0)
```

Add:

```python
    def test_build_granularity_plan_from_skeleton_marks_candidate_locations(self) -> None:
        transcript = "Alpha start " + ("a" * 100) + " Beta start"
        skeleton = "# T\n\n## Alpha\n\n## Beta\n"
        locations = [
            ChapterLocation(1, "Alpha", "Alpha start", 0, source="candidate"),
            ChapterLocation(2, "Beta", "Beta start", transcript.index("Beta start"), source="llm"),
        ]

        with patch("scripts.generate_outline_deepseek.call_location_pass_windowed", return_value=locations):
            plan, _ = build_granularity_plan_from_skeleton(object(), "prompt", transcript, skeleton)

        self.assertEqual([item["location_source"] for item in plan], ["candidate", "llm"])
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected: fail because granularity plan items do not include `location_source` and fallback still estimates the whole plan.

- [ ] **Step 3: Add exact and prefix-preserving plan builders**

Replace `build_granularity_plan_from_locations(...)` with a version that records source:

```python
def build_granularity_plan_from_locations(
    transcript: str,
    chapters: list[tuple[int, str]],
    locations: list[ChapterLocation],
) -> GranularityPlan:
    if not chapters:
        return []
    validate_chapter_locations(locations, len(transcript))
    if len(chapters) != len(locations):
        raise RuntimeError(
            "Granularity location count does not match chapter count: "
            f"chapters={len(chapters)} locations={len(locations)}"
        )

    plan: GranularityPlan = []
    for index, ((_, chapter_subtree), location) in enumerate(zip(chapters, locations)):
        end = locations[index + 1].start if index + 1 < len(locations) else len(transcript)
        source_chars = max(0, end - location.start)
        plan.append(
            {
                "top_level_item": chapter_heading(chapter_subtree),
                "source_chars": source_chars,
                "min_subsections": min_subsections_for_chars(source_chars),
                "max_depth": 4,
                "location_source": location.source,
            }
        )
    return plan
```

Update `build_estimated_granularity_plan(...)` item dicts:

```python
                "location_source": "estimated_after_failure",
```

Add:

```python
def build_granularity_plan_from_partial_locations(
    transcript: str,
    chapters: list[tuple[int, str]],
    locations: list[ChapterLocation],
) -> GranularityPlan:
    if not locations:
        return build_estimated_granularity_plan(transcript, chapters)

    validate_chapter_locations(locations, len(transcript))
    expected_prefix_ids = [chapter_id for chapter_id, _ in chapters[: len(locations)]]
    actual_prefix_ids = [location.chapter_id for location in locations]
    if actual_prefix_ids != expected_prefix_ids:
        raise RuntimeError(
            "Partial chapter locations must be a leading prefix: "
            f"expected={expected_prefix_ids}, actual={actual_prefix_ids}"
        )
    if len(locations) >= len(chapters):
        return build_granularity_plan_from_locations(transcript, chapters, locations)

    plan: GranularityPlan = []
    for index, ((_, chapter_subtree), location) in enumerate(zip(chapters, locations)):
        if index + 1 < len(locations):
            end = locations[index + 1].start
        else:
            remaining_chapters = len(chapters) - index
            remaining_chars = max(0, len(transcript) - location.start)
            end = min(len(transcript), location.start + max(1, remaining_chars // remaining_chapters))
        source_chars = max(0, end - location.start)
        plan.append(
            {
                "top_level_item": chapter_heading(chapter_subtree),
                "source_chars": source_chars,
                "min_subsections": min_subsections_for_chars(source_chars),
                "max_depth": 4,
                "location_source": location.source,
            }
        )

    last_known_start = locations[-1].start
    last_known_chars = int(plan[-1]["source_chars"]) if plan else 0
    consumed_end = last_known_start + last_known_chars
    unresolved = chapters[len(locations) :]
    if unresolved:
        segment = max(1, max(0, len(transcript) - consumed_end) // len(unresolved))
        cursor = consumed_end
        for index, (_, chapter_subtree) in enumerate(unresolved):
            end = len(transcript) if index + 1 == len(unresolved) else min(len(transcript), cursor + segment)
            source_chars = max(0, end - cursor)
            plan.append(
                {
                    "top_level_item": chapter_heading(chapter_subtree),
                    "source_chars": source_chars,
                    "min_subsections": min_subsections_for_chars(source_chars),
                    "max_depth": 4,
                    "location_source": "estimated_after_failure",
                }
            )
            cursor = end
    return plan
```

Then update `build_granularity_plan_from_skeleton(...)`:

```python
    try:
        locations = call_location_pass_windowed(
            client,
            prompt_template,
            transcript,
            chapters,
            allow_partial=True,
        )
    except PartialChapterLocationsError as exc:
        print(
            "Pass 0.5: draft location pass partially failed, "
            f"using prefix locations and estimating suffix: {exc}"
        )
        return build_granularity_plan_from_partial_locations(transcript, chapters, exc.locations), chapters
    except Exception as exc:
        print(f"Pass 0.5: draft location pass failed, using estimated granularity plan: {exc}")
        return build_estimated_granularity_plan(transcript, chapters), chapters
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected: granularity tests pass. If the first estimated suffix item is too small in the new test, adjust only the synthetic transcript lengths, not the production threshold logic.

### Task 5: Keep Final Transcript Slicing Strict

**Files:**
- Modify: `scripts/generate_outline_deepseek.py`
- Modify: `tests/test_generate_outline_source.py`

- [ ] **Step 1: Write failing tests for estimated-location rejection**

Add import:

```python
    validate_final_chapter_locations,
```

Add tests:

```python
    def test_validate_final_chapter_locations_rejects_estimated_sources(self) -> None:
        locations = [
            ChapterLocation(1, "Alpha", "alpha", 0, source="llm"),
            ChapterLocation(2, "Beta", "beta", 10, source="estimated_after_failure"),
        ]

        with self.assertRaisesRegex(RuntimeError, "estimated"):
            validate_final_chapter_locations(locations, transcript_len=100)

    def test_validate_final_chapter_locations_accepts_llm_candidate_and_reused_sources(self) -> None:
        locations = [
            ChapterLocation(1, "Alpha", "alpha", 0, source="candidate"),
            ChapterLocation(2, "Beta", "beta", 10, source="reused"),
        ]

        validate_final_chapter_locations(locations, transcript_len=100)
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected: fail because `validate_final_chapter_locations` is undefined.

- [ ] **Step 3: Implement final validation and wire it before slicing**

Add after `validate_chapter_locations(...)`:

```python
def validate_final_chapter_locations(locations: list[ChapterLocation], transcript_len: int) -> None:
    validate_chapter_locations(locations, transcript_len)
    estimated = [
        location.chapter_id
        for location in locations
        if location.source == "estimated_after_failure"
    ]
    if estimated:
        raise RuntimeError(
            "Final outline generation cannot use estimated chapter locations: "
            f"{estimated}"
        )
```

In `main()`, add one common validation call after the reuse/fresh branches have assigned `locations` and before `locations_path.write_text(...)`. This single placement covers both existing-location reuse and newly generated strict locations:

```python
    validate_final_chapter_locations(locations, len(transcript))
```

Keep `slice_chapter_transcripts(...)` unchanged except for existing validation. The final caller now rejects estimated starts before slicing.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source -v
```

Expected: final strictness tests pass.

### Task 6: Verify Full Suite And Rerun 002 Skeleton Experiment

**Files:**
- Generated: new directory under `D:\video\output\policy_skeleton_experiment_20260526\`

- [ ] **Step 1: Run all unit tests**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run environment check from this worktree**

Run:

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe scripts\check_env.py
```

Expected: passes when `.env` is present or when the process environment injects the same key/url/model values from `D:\video\.env`. If it fails only because this worktree has no `.env`, record that and continue to the API-backed experiment with explicit environment injection.

- [ ] **Step 3: Rerun 002 skeleton-only experiment into a new directory**

Use a new output folder so the previous run remains inspectable:

```powershell
$env:OUTPUT_DIR='D:\video\output\policy_skeleton_experiment_20260526\002_policy1_skeleton2_granularity_t02_rerun'
$env:OUTLINE_PROMPT_PATH='D:\video\prompt\prompt.md'
$env:SENSITIVE_WORD_LIST='D:\tmp\video_wt\wt-clean-transcript\.codex_tmp\empty_sensitive_words.json'
$env:DEEPSEEK_TEMPERATURE='0.2'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe scripts\generate_outline_deepseek.py --skeleton-only --policy-runs 1 --skeleton-runs 2
```

If the worktree has no `.env`, load the values from `D:\video\.env` before the same command. Use the repo's existing key names; do not print secret values:

```powershell
Get-Content D:\video\.env | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim().Trim('"'''), 'Process')
    }
}
$env:OUTPUT_DIR='D:\video\output\policy_skeleton_experiment_20260526\002_policy1_skeleton2_granularity_t02_rerun'
$env:OUTLINE_PROMPT_PATH='D:\video\prompt\prompt.md'
$env:SENSITIVE_WORD_LIST='D:\tmp\video_wt\wt-clean-transcript\.codex_tmp\empty_sensitive_words.json'
$env:DEEPSEEK_TEMPERATURE='0.2'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe scripts\generate_outline_deepseek.py --skeleton-only --policy-runs 1 --skeleton-runs 2
```

Expected:
- the command may require network approval outside the sandbox;
- both skeleton runs complete or record validation errors in `outline_skeleton_experiment.json`;
- granularity files no longer show every chapter with a near-uniform whole-outline average when only one later location fails;
- any `estimated_after_failure` entries appear only after the last successfully located prefix chapter.

- [ ] **Step 4: Inspect the rerun manifest and granularity files**

Check:

```powershell
Get-ChildItem D:\video\output\policy_skeleton_experiment_20260526\002_policy1_skeleton2_granularity_t02_rerun
```

And inspect:

```powershell
Get-Content D:\video\output\policy_skeleton_experiment_20260526\002_policy1_skeleton2_granularity_t02_rerun\outline_skeleton_experiment.json
Get-Content D:\video\output\policy_skeleton_experiment_20260526\002_policy1_skeleton2_granularity_t02_rerun\outline_granularity_policy_01_run_01.json
Get-Content D:\video\output\policy_skeleton_experiment_20260526\002_policy1_skeleton2_granularity_t02_rerun\outline_granularity_policy_01_run_02.json
```

Expected: `location_source` is visible in granularity files, and chapters before a failure use `"llm"` or `"candidate"` rather than `"estimated_after_failure"`.

### Task 7: Final Review Before Reporting

**Files:**
- Inspect only unless fixes are required.

- [ ] **Step 1: Check no policy-to-location dependency was introduced**

Run:

```powershell
rg -n "policy|top_level_items|merge_policy|parallel_groups" scripts\generate_outline_deepseek.py
```

Expected: no new use of policy inside `build_location_prompt`, `find_location_candidate`, `call_location_pass_windowed`, or granularity fallback helpers.

- [ ] **Step 2: Check final slicing cannot consume estimated locations**

Run:

```powershell
rg -n "estimated_after_failure|validate_final_chapter_locations|slice_chapter_transcripts" scripts\generate_outline_deepseek.py tests\test_generate_outline_source.py
```

Expected: `estimated_after_failure` appears in granularity fallback and tests; `validate_final_chapter_locations(...)` is called before `slice_chapter_transcripts(...)` in `main()`.

- [ ] **Step 3: Prepare concise implementation summary**

Record:
- focused test result;
- full test result;
- whether `scripts\check_env.py` passed or failed due to local `.env`;
- 002 rerun output directory;
- whether the rerun converged or still has skeleton drift.

Do not claim final outline injection is fixed unless a non-skeleton full outline run was actually completed.
