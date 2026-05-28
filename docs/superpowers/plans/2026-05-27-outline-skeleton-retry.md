# Outline Skeleton Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow retry layer so skeleton anchors are validated against canonical policy before granularity, and long skeleton chapters can be repaired without changing policy merge.

**Architecture:** Keep policy merge unchanged. Add anchor-policy validation before granularity. Prefer deterministic anchor replacement when the skeleton has the right `##` titles and canonical policy has one `ordered_blocks.start_quote` per top-level item; fall back to a bounded LLM anchor-repair pass only when the skeleton is structurally missing or misordering `##` headings. Use an LLM retry for granularity repair because it must add meaningful `###` headings. Record retry/failure status in experiment manifests instead of silently passing or guessing.

**Tech Stack:** Python 3.13, unittest, existing DeepSeek/OpenAI client wrapper, existing outline modules under `scripts/`.

---

## File Structure

- Modify `scripts/outline_models.py`: add optional retry metadata to `SkeletonGenerationResult` and add a structured `SkeletonRepairError`.
- Modify `scripts/outline_skeleton.py`: add anchor record parsing, policy anchor expectation extraction, anchor-policy validation, deterministic anchor attachment/replacement, and granularity failure collection.
- Modify `scripts/outline_llm.py`: add granularity repair prompt/call and wire the validated sequence into `generate_skeleton_with_granularity()`.
- Modify `scripts/outline_experiment.py`: write retry metadata and terminal statuses into skeleton experiment manifests.
- Modify `tests/test_generate_outline_source.py`: add focused fixtures for shifted anchors, deterministic anchor repair, granularity repair, retry ordering, and manifest fields.

No policy merge changes are planned.

## Checkpoint Sequence

The final skeleton flow must run in this order:

1. Generate initial anchored skeleton from canonical policy.
2. Validate `##` count/title order against `policy.top_level_items`.
3. Validate every `##` anchor against canonical policy `ordered_blocks.start_quote`.
4. If anchors are shifted or missing but the `##` skeleton matches policy, replace anchors deterministically from policy expectations.
5. If the `##` skeleton does not match policy, run at most two LLM anchor-repair calls; after each call, try deterministic anchor replacement again.
6. Parse repaired anchors and compute granularity.
7. If `###` counts fail granularity, run at most two granularity repair calls.
8. Re-run anchor validation and granularity validation after each repair.
9. Return valid result or raise structured `SkeletonRepairError` with terminal `ANCHOR_FAIL` / `GRANULARITY_FAIL`.

`OUTLINE_ANCHOR_REPAIR_MAX_ATTEMPTS` counts only LLM anchor fallback calls. The deterministic anchor replacement is a local normalization step and does not consume an LLM attempt.

## Task 1: Add Anchor Diagnostics And Deterministic Repair

**Files:**
- Modify: `scripts/outline_skeleton.py`
- Test: `tests/test_generate_outline_source.py`

- [ ] **Step 1: Add failing test for shifted anchors**

Add this test near existing skeleton anchor tests:

```python
def test_validate_skeleton_anchors_against_policy_rejects_shifted_quotes(self) -> None:
    transcript = "alpha start details beta start details gamma start tail"
    policy = {
        "top_level_items": ["Alpha", "Beta", "Gamma"],
        "ordered_blocks": [
            {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
            {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
            {"title": "Gamma", "start_quote": "gamma start", "candidate_top_level": True},
        ],
        "merge_policy": "",
        "parallel_groups": [],
    }
    shifted = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "beta start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "gamma start"} -->

## Gamma
"""

    with self.assertRaisesRegex(RuntimeError, "policy anchor mismatch"):
        validate_skeleton_anchors_against_policy(shifted, transcript, policy)
```

Update imports in the test file:

```python
from outline_skeleton import repair_skeleton_anchors_from_policy
from outline_skeleton import validate_skeleton_anchors_against_policy
```

- [ ] **Step 2: Add deterministic anchor repair test**

```python
def test_repair_skeleton_anchors_from_policy_replaces_shifted_and_missing_anchors(self) -> None:
    transcript = "alpha start details beta start details gamma start tail"
    policy = {
        "top_level_items": ["Alpha", "Beta", "Gamma"],
        "ordered_blocks": [
            {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
            {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
            {"title": "Gamma", "start_quote": "gamma start", "candidate_top_level": True},
        ],
        "merge_policy": "",
        "parallel_groups": [],
    }
    shifted = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "beta start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "gamma start"} -->

## Gamma
"""

    repaired = repair_skeleton_anchors_from_policy(shifted, policy)

    self.assertIn('## Alpha\n<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->', repaired)
    self.assertIn('## Beta\n<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->', repaired)
    self.assertIn('## Gamma\n<!-- outline-anchor: {"chapter_id": 3, "start_quote": "gamma start"} -->', repaired)
    validate_skeleton_anchors_against_policy(repaired, transcript, policy)
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source.GenerateOutlineSourceTests.test_validate_skeleton_anchors_against_policy_rejects_shifted_quotes tests.test_generate_outline_source.GenerateOutlineSourceTests.test_repair_skeleton_anchors_from_policy_replaces_shifted_and_missing_anchors -v
```

Expected: both tests fail because the helper functions do not exist.

- [ ] **Step 4: Implement policy anchor expectation extraction**

Add to `scripts/outline_skeleton.py`:

```python
def policy_anchor_expectations(policy: OutlinePolicy) -> list[dict[str, object]]:
    top_level_items = [
        str(item).strip()
        for item in policy.get("top_level_items", [])
        if str(item).strip()
    ]
    candidate_blocks = [
        block
        for block in policy.get("ordered_blocks", [])
        if isinstance(block, dict) and bool(block.get("candidate_top_level"))
    ]
    if len(candidate_blocks) != len(top_level_items):
        raise RuntimeError(
            "Cannot validate skeleton anchors against policy because "
            "candidate ordered_blocks do not match top_level_items: "
            f"top_level_items={len(top_level_items)} candidate_blocks={len(candidate_blocks)}"
        )

    expectations: list[dict[str, object]] = []
    for index, (title, block) in enumerate(zip(top_level_items, candidate_blocks), start=1):
        start_quote = str(block.get("start_quote") or "").strip()
        if not start_quote:
            raise RuntimeError(f"Missing policy start_quote for top-level item {index}: {title!r}")
        expectations.append(
            {
                "chapter_id": index,
                "title": title,
                "start_quote": start_quote,
            }
        )
    return expectations
```

- [ ] **Step 5: Implement deterministic anchor replacement**

Add to `scripts/outline_skeleton.py`:

```python
def repair_skeleton_anchors_from_policy(
    anchored_skeleton: str,
    policy: OutlinePolicy,
) -> str:
    clean_skeleton = strip_skeleton_anchors(anchored_skeleton)
    title, chapters = parse_chapters(clean_skeleton)
    validate_skeleton_matches_policy(chapters, policy)
    expectations = policy_anchor_expectations(policy)
    if len(chapters) != len(expectations):
        raise RuntimeError(
            "Cannot repair skeleton anchors because chapter count does not match policy "
            f"expectations: chapters={len(chapters)} expectations={len(expectations)}"
        )

    repaired_lines: list[str] = []
    chapter_id = 0
    for raw_line in clean_skeleton.splitlines():
        line = raw_line.rstrip()
        repaired_lines.append(line)
        if line.startswith("## ") and not line.startswith("### "):
            chapter_id += 1
            start_quote = str(expectations[chapter_id - 1]["start_quote"])
            payload = {"chapter_id": chapter_id, "start_quote": start_quote}
            repaired_lines.append(
                f"<!-- outline-anchor: {json.dumps(payload, ensure_ascii=False)} -->"
            )
    return "\n".join(repaired_lines).strip()
```

- [ ] **Step 6: Implement anchor-policy validation**

Add to `scripts/outline_skeleton.py`:

```python
def validate_skeleton_anchors_against_policy(
    anchored_skeleton: str,
    transcript: str,
    policy: OutlinePolicy,
) -> None:
    expectations = policy_anchor_expectations(policy)
    _, chapters, locations = parse_skeleton_anchor_locations(anchored_skeleton, transcript)
    validate_skeleton_matches_policy(chapters, policy)

    if len(locations) != len(expectations):
        raise RuntimeError(
            "Skeleton anchor count does not match policy expectations: "
            f"anchors={len(locations)} expectations={len(expectations)}"
        )

    for location, expectation in zip(locations, expectations):
        expected_quote = str(expectation["start_quote"])
        if location.start_quote == expected_quote:
            continue
        raise RuntimeError(
            "Skeleton policy anchor mismatch at "
            f"{location.chapter_id}: expected {expected_quote!r}, got {location.start_quote!r}"
        )
```

- [ ] **Step 7: Run focused tests**

Run the command from Step 3 again.

Expected: both tests pass.

## Task 2: Add LLM Anchor Repair Fallback

**Files:**
- Modify: `scripts/outline_llm.py`
- Test: `tests/test_generate_outline_source.py`

- [ ] **Step 1: Add test for LLM anchor repair fallback when a `##` is missing**

```python
def test_call_anchor_repair_pass_sends_policy_titles_and_quotes(self) -> None:
    broken = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->
"""
    policy = {
        "top_level_items": ["Alpha", "Beta"],
        "ordered_blocks": [
            {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
            {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
        ],
        "merge_policy": "",
        "parallel_groups": [],
    }
    repaired = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""
    with patch("outline_llm.call_chat") as call_chat:
        call_chat.return_value = ChatResult(
            content=repaired,
            finish_reason="stop",
            continuations=0,
        )

        result = call_anchor_repair_pass(
            object(),
            "prompt",
            broken,
            policy,
            "chapter count mismatch",
        )

    self.assertIn("## Beta", result)
    prompt = call_chat.call_args.kwargs["user_prompt"]
    self.assertIn("chapter count mismatch", prompt)
    self.assertIn("Beta", prompt)
    self.assertIn("beta start", prompt)
    self.assertIn("outline-anchor", prompt)
```

Update imports:

```python
from outline_llm import call_anchor_repair_pass
```

- [ ] **Step 2: Implement LLM anchor repair call**

Add to `scripts/outline_llm.py`:

```python
def call_anchor_repair_pass(
    client: OpenAI,
    prompt_template: str,
    anchored_skeleton: str,
    policy: OutlinePolicy,
    validation_error: str,
) -> str:
    expected_items = [
        {
            "chapter_id": item["chapter_id"],
            "title": item["title"],
            "start_quote": item["start_quote"],
        }
        for item in policy_anchor_expectations(policy)
    ]
    user_prompt = f"""{prompt_template.strip()}

---

这是 Pass 1 anchor repair：只修复大纲骨架的顶级章节和 anchor。

上一轮错误：
{validation_error}

硬性要求：
- 只输出完整 anchored Markdown skeleton，不输出解释。
- 最终 `##` 数量、顺序和含义必须与 expected_policy_items 完全一致。
- 每个 `##` 下方必须紧跟一个 `outline-anchor` 注释。
- 每个 anchor 的 `start_quote` 必须使用 expected_policy_items 中对应条目的 exact value。
- 可以补回缺失的 `##`，但不要新增 expected_policy_items 之外的 `##`。
- 不要填充逐字稿正文。

expected_policy_items：
```json
{json.dumps(expected_items, ensure_ascii=False, indent=2)}
```

当前 anchored skeleton：
```markdown
{anchored_skeleton.strip()}
```"""
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_ANCHOR_REPAIR_MAX_TOKENS", 8192),
        max_continuations=env_int("OUTLINE_ANCHOR_REPAIR_MAX_CONTINUATIONS", 2),
    )
    return normalize_anchored_skeleton(result.content)
```

Also add this import in `scripts/outline_llm.py`:

```python
from outline_skeleton import policy_anchor_expectations
```

- [ ] **Step 3: Run focused anchor fallback test**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source.GenerateOutlineSourceTests.test_call_anchor_repair_pass_sends_policy_titles_and_quotes -v
```

Expected: test passes.

## Task 3: Add Granularity Failure Collection And Repair Prompt

**Files:**
- Modify: `scripts/outline_skeleton.py`
- Modify: `scripts/outline_llm.py`
- Test: `tests/test_generate_outline_source.py`

- [ ] **Step 1: Add test for collecting failing granularity chapters**

```python
def test_collect_granularity_failures_reports_short_subsection_counts(self) -> None:
    _, chapters = parse_chapters("# T\n\n## Alpha\n\n## Beta\n### One\n")
    plan = [
        {"top_level_item": "Alpha", "source_chars": 2000, "min_subsections": 3, "max_depth": 4},
        {"top_level_item": "Beta", "source_chars": 1200, "min_subsections": 2, "max_depth": 4},
    ]

    failures = collect_granularity_failures(chapters, plan)

    self.assertEqual(
        failures,
        [
            {
                "chapter_id": 1,
                "top_level_item": "Alpha",
                "source_chars": 2000,
                "min_subsections": 3,
                "actual_subsections": 0,
            },
            {
                "chapter_id": 2,
                "top_level_item": "Beta",
                "source_chars": 1200,
                "min_subsections": 2,
                "actual_subsections": 1,
            },
        ],
    )
```

Update imports:

```python
from outline_skeleton import collect_granularity_failures
```

- [ ] **Step 2: Implement `collect_granularity_failures()`**

Add to `scripts/outline_skeleton.py`:

```python
def count_direct_subsections(chapter_subtree: str) -> int:
    return sum(
        1
        for line in chapter_subtree.splitlines()
        if line.startswith("### ") and not line.startswith("#### ")
    )


def collect_granularity_failures(
    chapters: list[tuple[int, str]],
    granularity_plan: GranularityPlan,
) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for index, ((chapter_id, chapter_subtree), item) in enumerate(
        zip(chapters, granularity_plan),
        start=1,
    ):
        min_subsections = int(item.get("min_subsections", 0))
        if min_subsections <= 0:
            continue
        actual_subsections = count_direct_subsections(chapter_subtree)
        if actual_subsections >= min_subsections:
            continue
        failures.append(
            {
                "chapter_id": chapter_id,
                "top_level_item": item.get("top_level_item"),
                "source_chars": int(item.get("source_chars", 0)),
                "min_subsections": min_subsections,
                "actual_subsections": actual_subsections,
            }
        )
    return failures
```

Then update `validate_skeleton_matches_granularity()` to call `collect_granularity_failures()` and raise from the first failure. Preserve the existing error message shape so current tests keep passing.

- [ ] **Step 3: Add test for granularity repair prompt contents**

```python
def test_call_granularity_repair_pass_sends_limited_repair_prompt(self) -> None:
    anchored = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""
    plan = [
        {"top_level_item": "Alpha", "source_chars": 2000, "min_subsections": 3, "max_depth": 4},
        {"top_level_item": "Beta", "source_chars": 100, "min_subsections": 0, "max_depth": 4},
    ]
    failures = [
        {
            "chapter_id": 1,
            "top_level_item": "Alpha",
            "source_chars": 2000,
            "min_subsections": 3,
            "actual_subsections": 0,
        }
    ]
    with patch("outline_llm.call_chat") as call_chat:
        call_chat.return_value = ChatResult(
            content=anchored.replace("## Beta", "### One\n### Two\n### Three\n\n## Beta"),
            finish_reason="stop",
            continuations=0,
        )

        repaired = call_granularity_repair_pass(object(), "prompt", anchored, plan, failures)

    self.assertIn("### One", repaired)
    prompt = call_chat.call_args.kwargs["user_prompt"]
    self.assertIn("chapter_id", prompt)
    self.assertIn("min_subsections", prompt)
    self.assertIn("actual_subsections", prompt)
    self.assertIn("不要修改 `##`", prompt)
    self.assertIn("outline-anchor", prompt)
```

Update imports:

```python
from outline_llm import call_granularity_repair_pass
```

- [ ] **Step 4: Implement granularity repair call**

Add to `scripts/outline_llm.py`:

```python
def call_granularity_repair_pass(
    client: OpenAI,
    prompt_template: str,
    anchored_skeleton: str,
    granularity_plan: GranularityPlan,
    failures: list[dict[str, object]],
) -> str:
    user_prompt = f"""{prompt_template.strip()}

---

这是 Pass 1 repair：只修复大纲骨架的章节细分粒度。

硬性要求：
- 只输出完整 Markdown 骨架，不输出解释。
- 不要修改 `#`、任何 `##` 标题、`##` 顺序或任何 `outline-anchor` 注释。
- 只为下面列出的失败章节补充必要的 `###`。
- 每个失败章节下的直接 `###` 数量必须不少于 `min_subsections`。
- 不要新增、删除、合并或拆分 `##`。
- 不要填充逐字稿正文。

失败章节：
```json
{json.dumps(failures, ensure_ascii=False, indent=2)}
```

完整 granularity plan：
```json
{json.dumps(granularity_plan, ensure_ascii=False, indent=2)}
```

当前 anchored skeleton：
```markdown
{anchored_skeleton.strip()}
```"""
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_SKELETON_REPAIR_MAX_TOKENS", 8192),
        max_continuations=env_int("OUTLINE_SKELETON_REPAIR_MAX_CONTINUATIONS", 2),
    )
    return normalize_anchored_skeleton(result.content)
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source.GenerateOutlineSourceTests.test_collect_granularity_failures_reports_short_subsection_counts tests.test_generate_outline_source.GenerateOutlineSourceTests.test_call_granularity_repair_pass_sends_limited_repair_prompt -v
```

Expected: both tests pass.

## Task 4: Wire Validated Skeleton Generation

**Files:**
- Modify: `scripts/outline_models.py`
- Modify: `scripts/outline_llm.py`
- Test: `tests/test_generate_outline_source.py`

- [ ] **Step 1: Add retry metadata and structured repair exception**

Change `scripts/outline_models.py`:

```python
@dataclass(frozen=True)
class SkeletonGenerationResult:
    skeleton: str
    anchored_skeleton: str
    granularity_plan: GranularityPlan
    locations: list[ChapterLocation]
    retry_report: dict[str, object] | None = None


class SkeletonRepairError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: str,
        retry_report: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_report = retry_report
```

- [ ] **Step 2: Add test that anchor repair runs before granularity**

```python
def test_generate_skeleton_with_granularity_repairs_shifted_policy_anchors_before_granularity(self) -> None:
    transcript = "alpha start " + ("a" * 1200) + " beta start tail"
    policy = {
        "top_level_items": ["Alpha", "Beta"],
        "ordered_blocks": [
            {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
            {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
        ],
        "merge_policy": "",
        "parallel_groups": [],
    }
    shifted = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "beta start"} -->

## Beta
"""

    with patch("outline_llm.generate_skeleton_from_policy", return_value=shifted):
        result = generate_skeleton_with_granularity(
            object(),
            "prompt",
            transcript,
            None,
            policy,
            len(transcript),
        )

    self.assertEqual([location.start_quote for location in result.locations], ["alpha start", "beta start"])
    self.assertEqual(result.retry_report["anchor_repair_count"], 1)
    self.assertEqual(result.retry_report["status"], "valid")
```

- [ ] **Step 3: Add test that missing `##` uses LLM anchor fallback**

```python
def test_generate_skeleton_with_granularity_uses_anchor_llm_fallback_when_chapter_is_missing(self) -> None:
    transcript = "alpha start " + ("a" * 1200) + " beta start tail"
    policy = {
        "top_level_items": ["Alpha", "Beta"],
        "ordered_blocks": [
            {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
            {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
        ],
        "merge_policy": "",
        "parallel_groups": [],
    }
    missing_beta = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->
"""
    repaired = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""

    with (
        patch("outline_llm.generate_skeleton_from_policy", return_value=missing_beta),
        patch("outline_llm.call_anchor_repair_pass", return_value=repaired) as anchor_repair,
    ):
        result = generate_skeleton_with_granularity(
            object(),
            "prompt",
            transcript,
            None,
            policy,
            len(transcript),
        )

    anchor_repair.assert_called_once()
    self.assertEqual([location.start_quote for location in result.locations], ["alpha start", "beta start"])
    self.assertEqual(result.retry_report["anchor_repair_count"], 1)
    self.assertEqual(result.retry_report["status"], "valid")
```

- [ ] **Step 4: Add test that granularity repair is called after anchor success**

```python
def test_generate_skeleton_with_granularity_repairs_granularity_after_anchor_success(self) -> None:
    transcript = "alpha start " + ("a" * 2200) + " beta start tail"
    policy = {
        "top_level_items": ["Alpha", "Beta"],
        "ordered_blocks": [
            {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
            {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
        ],
        "merge_policy": "",
        "parallel_groups": [],
    }
    coarse = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""
    repaired = coarse.replace(
        "## Beta",
        "### One\n### Two\n### Three\n\n## Beta",
    )

    with (
        patch("outline_llm.generate_skeleton_from_policy", return_value=coarse),
        patch("outline_llm.call_granularity_repair_pass", return_value=repaired) as repair_pass,
    ):
        result = generate_skeleton_with_granularity(
            object(),
            "prompt",
            transcript,
            None,
            policy,
            len(transcript),
        )

    repair_pass.assert_called_once()
    self.assertIn("### Three", result.skeleton)
    self.assertEqual(result.retry_report["granularity_repair_count"], 1)
    self.assertEqual(result.retry_report["status"], "valid")
```

- [ ] **Step 5: Add test for granularity repair exhaustion**

```python
def test_generate_skeleton_with_granularity_reports_granularity_fail_after_repair_exhaustion(self) -> None:
    transcript = "alpha start " + ("a" * 2200) + " beta start tail"
    policy = {
        "top_level_items": ["Alpha", "Beta"],
        "ordered_blocks": [
            {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
            {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
        ],
        "merge_policy": "",
        "parallel_groups": [],
    }
    coarse = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""

    with (
        patch.dict(os.environ, {"OUTLINE_GRANULARITY_REPAIR_MAX_ATTEMPTS": "1"}),
        patch("outline_llm.generate_skeleton_from_policy", return_value=coarse),
        patch("outline_llm.call_granularity_repair_pass", return_value=coarse),
    ):
        with self.assertRaises(SkeletonRepairError) as raised:
            generate_skeleton_with_granularity(
                object(),
                "prompt",
                transcript,
                None,
                policy,
                len(transcript),
            )

    self.assertEqual(raised.exception.status, "GRANULARITY_FAIL")
    self.assertEqual(raised.exception.retry_report["granularity_repair_count"], 1)
```

Update imports:

```python
from outline_models import SkeletonRepairError
```

- [ ] **Step 6: Implement retry wiring in `generate_skeleton_with_granularity()`**

Update imports in `scripts/outline_llm.py`:

```python
from outline_granularity import build_granularity_plan_from_locations
from outline_models import SkeletonRepairError
from outline_skeleton import (
    collect_granularity_failures,
    parse_chapters,
    parse_skeleton_anchor_locations,
    repair_skeleton_anchors_from_policy,
    strip_skeleton_anchors,
    validate_skeleton_anchors_against_policy,
    validate_skeleton_matches_granularity,
)
```

Replace the body of `generate_skeleton_with_granularity()` with this sequence:

```python
def generate_skeleton_with_granularity(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    course_title: str | None,
    policy: OutlinePolicy,
    char_count: int,
) -> SkeletonGenerationResult:
    print("Pass 1: generating outline skeleton from canonical policy...")
    retry_report: dict[str, object] = {
        "status": "valid",
        "anchor_repair_count": 0,
        "granularity_repair_count": 0,
        "anchor_error": "",
        "granularity_repair_chapters": [],
    }

    def fail(status: str, message: str) -> None:
        retry_report["status"] = status
        raise SkeletonRepairError(message, status=status, retry_report=retry_report)

    anchored_skeleton = generate_skeleton_from_policy(
        client,
        prompt_template,
        transcript,
        course_title,
        policy,
        granularity_plan=None,
        char_count=char_count,
    )

    try:
        validate_skeleton_anchors_against_policy(anchored_skeleton, transcript, policy)
    except RuntimeError as exc:
        retry_report["anchor_error"] = str(exc)
        anchor_ok = False
        try:
            anchored_skeleton = repair_skeleton_anchors_from_policy(anchored_skeleton, policy)
            retry_report["anchor_repair_count"] = int(retry_report["anchor_repair_count"]) + 1
            validate_skeleton_anchors_against_policy(anchored_skeleton, transcript, policy)
            anchor_ok = True
        except RuntimeError as deterministic_exc:
            retry_report["anchor_error"] = str(deterministic_exc)

        max_anchor_repairs = max(0, env_int("OUTLINE_ANCHOR_REPAIR_MAX_ATTEMPTS", 2))
        while not anchor_ok and int(retry_report["anchor_repair_count"]) < max_anchor_repairs:
            anchored_skeleton = call_anchor_repair_pass(
                client,
                prompt_template,
                anchored_skeleton,
                policy,
                str(retry_report["anchor_error"]),
            )
            retry_report["anchor_repair_count"] = int(retry_report["anchor_repair_count"]) + 1
            try:
                anchored_skeleton = repair_skeleton_anchors_from_policy(anchored_skeleton, policy)
                validate_skeleton_anchors_against_policy(anchored_skeleton, transcript, policy)
                anchor_ok = True
            except RuntimeError as anchor_retry_exc:
                retry_report["anchor_error"] = str(anchor_retry_exc)

        if not anchor_ok:
            fail("ANCHOR_FAIL", f"Skeleton anchor repair failed: {retry_report['anchor_error']}")

    _, chapters, locations = parse_skeleton_anchor_locations(anchored_skeleton, transcript)
    clean_skeleton = strip_skeleton_anchors(anchored_skeleton)
    granularity_plan = build_granularity_plan_from_locations(
        transcript,
        parse_chapters(clean_skeleton)[1],
        locations,
    )

    max_repairs = max(0, env_int("OUTLINE_GRANULARITY_REPAIR_MAX_ATTEMPTS", 2))
    while True:
        _, chapters = parse_chapters(strip_skeleton_anchors(anchored_skeleton))
        failures = collect_granularity_failures(chapters, granularity_plan)
        if not failures:
            break
        if not retry_report["granularity_repair_chapters"]:
            retry_report["granularity_repair_chapters"] = failures
        if int(retry_report["granularity_repair_count"]) >= max_repairs:
            fail(
                "GRANULARITY_FAIL",
                f"Skeleton granularity repair failed after {max_repairs} attempt(s): {failures}",
            )
        anchored_skeleton = call_granularity_repair_pass(
            client,
            prompt_template,
            anchored_skeleton,
            granularity_plan,
            failures,
        )
        retry_report["granularity_repair_count"] = int(retry_report["granularity_repair_count"]) + 1
        try:
            validate_skeleton_anchors_against_policy(anchored_skeleton, transcript, policy)
        except RuntimeError as exc:
            retry_report["anchor_error"] = str(exc)
            fail(
                "ANCHOR_FAIL",
                f"Skeleton anchor validation failed during granularity repair: {exc}",
            )

    _, chapters, locations = parse_skeleton_anchor_locations(anchored_skeleton, transcript)
    clean_skeleton = strip_skeleton_anchors(anchored_skeleton)
    validate_skeleton_matches_granularity(chapters, granularity_plan)
    return SkeletonGenerationResult(
        skeleton=clean_skeleton,
        anchored_skeleton=anchored_skeleton,
        granularity_plan=granularity_plan,
        locations=locations,
        retry_report=retry_report,
    )
```

If deterministic anchor repair fails because the `##` structure itself is wrong, the function uses `call_anchor_repair_pass()` up to `OUTLINE_ANCHOR_REPAIR_MAX_ATTEMPTS`. If all anchor repair attempts fail, it raises `SkeletonRepairError(status="ANCHOR_FAIL")` with the current `retry_report`.

- [ ] **Step 7: Run focused generation tests**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source.GenerateOutlineSourceTests.test_generate_skeleton_with_granularity_repairs_shifted_policy_anchors_before_granularity tests.test_generate_outline_source.GenerateOutlineSourceTests.test_generate_skeleton_with_granularity_uses_anchor_llm_fallback_when_chapter_is_missing tests.test_generate_outline_source.GenerateOutlineSourceTests.test_generate_skeleton_with_granularity_repairs_granularity_after_anchor_success tests.test_generate_outline_source.GenerateOutlineSourceTests.test_generate_skeleton_with_granularity_reports_granularity_fail_after_repair_exhaustion -v
```

Expected: all four tests pass.

## Task 5: Record Retry Metadata In Experiment Manifest

**Files:**
- Modify: `scripts/outline_experiment.py`
- Test: `tests/test_generate_outline_source.py`

- [ ] **Step 1: Extend existing experiment manifest test**

In `test_skeleton_only_experiment_generates_each_skeleton_from_canonical_policy`, set `retry_report` on the fixture:

```python
skeleton_result = SkeletonGenerationResult(
    skeleton="# T\n\n## A\n\n## B\n",
    anchored_skeleton=(
        '# T\n\n## A\n<!-- outline-anchor: {"chapter_id": 1, "start_quote": "A start"} -->'
        '\n\n## B\n<!-- outline-anchor: {"chapter_id": 2, "start_quote": "B start"} -->'
    ),
    granularity_plan=[
        {
            "top_level_item": "A",
            "source_chars": 100,
            "min_subsections": 0,
            "max_depth": 4,
            "location_source": "skeleton_anchor",
        },
        {
            "top_level_item": "B",
            "source_chars": 100,
            "min_subsections": 0,
            "max_depth": 4,
            "location_source": "skeleton_anchor",
        },
    ],
    locations=[
        ChapterLocation(1, "A", "A start", 0, source="skeleton_anchor"),
        ChapterLocation(2, "B", "B start", 100, source="skeleton_anchor"),
    ],
    retry_report={
        "status": "valid",
        "anchor_repair_count": 1,
        "granularity_repair_count": 1,
        "anchor_error": "shifted",
        "granularity_repair_chapters": [{"chapter_id": 1}],
    },
)
```

Then assert:

```python
self.assertEqual(manifest["runs"][0]["skeleton_retry_status"], "valid")
self.assertEqual(manifest["runs"][0]["anchor_repair_count"], 1)
self.assertEqual(manifest["runs"][0]["granularity_repair_count"], 1)
self.assertIn("granularity_repair_chapters", manifest["runs"][0])
```

- [ ] **Step 2: Add retry fields to manifest records**

In `scripts/outline_experiment.py`, before appending a successful record:

```python
retry_report = skeleton_result.retry_report or {}
```

Add these fields to the record:

```python
"skeleton_retry_status": retry_report.get("status", "valid"),
"anchor_repair_count": retry_report.get("anchor_repair_count", 0),
"granularity_repair_count": retry_report.get("granularity_repair_count", 0),
"anchor_error": retry_report.get("anchor_error", ""),
"granularity_repair_chapters": retry_report.get("granularity_repair_chapters", []),
```

Import the structured exception:

```python
from outline_models import SkeletonRepairError
```

For the `except RuntimeError` record, derive status from `SkeletonRepairError` instead of string matching:

```python
retry_report = exc.retry_report if isinstance(exc, SkeletonRepairError) else {}
record_status = exc.status if isinstance(exc, SkeletonRepairError) else "INVALID"
```

Add these fields to the invalid record:

```python
"skeleton_retry_status": record_status,
"anchor_repair_count": retry_report.get("anchor_repair_count", 0),
"granularity_repair_count": retry_report.get("granularity_repair_count", 0),
"anchor_error": retry_report.get("anchor_error", validation_error if record_status == "ANCHOR_FAIL" else ""),
"granularity_repair_chapters": retry_report.get("granularity_repair_chapters", []),
```

- [ ] **Step 3: Run experiment test**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest tests.test_generate_outline_source.GenerateOutlineSourceTests.test_skeleton_only_experiment_generates_each_skeleton_from_canonical_policy -v
```

Expected: test passes and manifest fields are present.

## Task 6: Full Verification And 002 Re-run

**Files:**
- No source edits unless verification exposes a bug.
- Generated output under `D:\video\output\policy_only_20260527_144643_schema\002_policy2_merge2`.

- [ ] **Step 1: Run full unit test suite**

Run:

```powershell
$env:CODEX_TEST_ROOT='D:\video\.codex_tmp\tests'
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Re-run 002 from existing canonical policies**

Run this verification command after loading `D:\video\.env`; it calls the updated `generate_skeleton_with_granularity()` and does not regenerate policy:

```powershell
$env:PYTHONPATH='D:\tmp\video_wt\wt-clean-transcript\scripts'
@'
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from generate_outline_deepseek import strip_part_markers
from outline_granularity import format_granularity_plan
from outline_io import file_sha256, read_course_title
from outline_llm import generate_skeleton_with_granularity
from outline_policy import read_outline_policy
from outline_text import env_float, env_int

load_dotenv(Path(r"D:\video\.env"), override=False)
base = Path(r"D:\video\output\policy_only_20260527_144643_schema\002_policy2_merge2")
prompt_template = Path(r"D:\tmp\video_wt\wt-clean-transcript\prompt\prompt.md").read_text(encoding="utf-8")
transcript = strip_part_markers((base / "transcript.txt").read_text(encoding="utf-8-sig"))
course_title = read_course_title(base)
client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    timeout=env_float("DEEPSEEK_TIMEOUT", 180.0),
    max_retries=env_int("DEEPSEEK_MAX_RETRIES", 2),
)
records = []
for round_index in (1, 2):
    round_dir = base / f"round_{round_index:02d}"
    policy_path = round_dir / "outline_policy_canonical.json"
    policy = read_outline_policy(policy_path)
    try:
        result = generate_skeleton_with_granularity(
            client,
            prompt_template,
            transcript,
            course_title,
            policy,
            len(transcript),
        )
        (round_dir / "outline_skeleton.md").write_text(result.skeleton + "\n", encoding="utf-8")
        (round_dir / "outline_skeleton_anchored.md").write_text(result.anchored_skeleton + "\n", encoding="utf-8")
        (round_dir / "outline_granularity.json").write_text(format_granularity_plan(result.granularity_plan) + "\n", encoding="utf-8")
        retry_report = result.retry_report or {}
        record = {
            "round": round_index,
            "canonical_policy_path": str(policy_path.relative_to(base)).replace("\\", "/"),
            "canonical_policy_sha256": file_sha256(policy_path),
            "top_level_count": len(policy.get("top_level_items", [])),
            "chapter_count": len(result.locations),
            "valid": retry_report.get("status", "valid") == "valid",
            "validation_error": "",
            "skeleton_retry_status": retry_report.get("status", "valid"),
            "anchor_repair_count": retry_report.get("anchor_repair_count", 0),
            "granularity_repair_count": retry_report.get("granularity_repair_count", 0),
            "anchor_error": retry_report.get("anchor_error", ""),
            "granularity_repair_chapters": retry_report.get("granularity_repair_chapters", []),
            "granularity_path": str((round_dir / "outline_granularity.json").relative_to(base)).replace("\\", "/"),
            "skeleton_path": str((round_dir / "outline_skeleton.md").relative_to(base)).replace("\\", "/"),
            "anchored_skeleton_path": str((round_dir / "outline_skeleton_anchored.md").relative_to(base)).replace("\\", "/"),
        }
    except RuntimeError as exc:
        retry_report = getattr(exc, "retry_report", {})
        record = {
            "round": round_index,
            "canonical_policy_path": str(policy_path.relative_to(base)).replace("\\", "/"),
            "canonical_policy_sha256": file_sha256(policy_path),
            "top_level_count": len(policy.get("top_level_items", [])),
            "chapter_count": 0,
            "valid": False,
            "validation_error": str(exc),
            "skeleton_retry_status": getattr(exc, "status", "INVALID"),
            "anchor_repair_count": retry_report.get("anchor_repair_count", 0),
            "granularity_repair_count": retry_report.get("granularity_repair_count", 0),
            "anchor_error": retry_report.get("anchor_error", ""),
            "granularity_repair_chapters": retry_report.get("granularity_repair_chapters", []),
        }
    records.append(record)
(base / "skeleton_from_policy_manifest.json").write_text(
    json.dumps({"runs": records}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
'@ | C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe -B -
```

Expected writes:

- `round_01/outline_skeleton.md`
- `round_01/outline_skeleton_anchored.md`
- `round_01/outline_granularity.json`
- `round_02/outline_skeleton.md`
- `round_02/outline_skeleton_anchored.md`
- `round_02/outline_granularity.json`
- root `skeleton_from_policy_manifest.json`

The script must not regenerate any `outline_policy_run_*.json` or `outline_policy_canonical.json`.

- [ ] **Step 3: Inspect run outcome**

Check:

```powershell
Get-Content -Raw D:\video\output\policy_only_20260527_144643_schema\002_policy2_merge2\skeleton_from_policy_manifest.json
Select-String -Path D:\video\output\policy_only_20260527_144643_schema\002_policy2_merge2\round_02\outline_skeleton_anchored.md -Pattern '^## |outline-anchor'
```

Expected:

- `round_01` should either be valid after granularity repair or explicitly `GRANULARITY_FAIL` with repair attempts recorded.
- `round_02` should have 12 `##` and 12 anchors aligned to policy start quotes, or explicitly `ANCHOR_FAIL`.
- No run should produce granularity from shifted or missing anchors.

## Self-Review

- Spec coverage: covers the corrected diagnosis: `round_01` needs granularity feedback; `round_02` needs anchor-policy alignment, not just missing-anchor count.
- Placeholder scan: no placeholder markers or unspecified implementation steps remain.
- Type consistency: new helpers use existing `OutlinePolicy`, `GranularityPlan`, and `SkeletonGenerationResult` types.
- Scope check: policy merge is intentionally unchanged; all changes stay in skeleton validation/repair and experiment reporting.
