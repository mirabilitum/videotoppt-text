from __future__ import annotations

import json
import re

from outline_locations import find_unique_anchor_quote_start, validate_chapter_locations
from outline_models import ChapterLocation, GranularityPlan, OutlinePolicy
from outline_policy import format_outline_policy, normalize_policy_heading_key
from outline_text import HEADING_RE, ROOT_HEADING_RE, chapter_heading, strip_markdown_fence


ANCHOR_RE = re.compile(r"^<!--\s*outline-anchor:\s*(\{.*\})\s*-->\s*$")


def normalize_skeleton(skeleton_md: str) -> str:
    headings: list[str] = []
    for raw_line in strip_markdown_fence(skeleton_md).splitlines():
        line = raw_line.strip()
        match = HEADING_RE.match(line)
        if not match:
            continue

        depth = len(match.group(1))
        if depth > 4:
            continue
        title = match.group(2).strip()
        if not title:
            continue

        headings.append(f"{'#' * depth} {title}")

    if not headings:
        raise RuntimeError("Skeleton response did not contain Markdown headings.")

    if not any(line.startswith("# ") and not line.startswith("## ") for line in headings):
        headings.insert(0, "# 课程大纲")

    normalized: list[str] = []
    for line in headings:
        if line.startswith("# ") or line.startswith("## "):
            if normalized:
                normalized.append("")
        normalized.append(line)

    return "\n".join(normalized).strip()

def normalize_anchored_skeleton(skeleton_md: str) -> str:
    lines: list[str] = []
    saw_heading = False
    pending_anchor = False
    for raw_line in strip_markdown_fence(skeleton_md).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = HEADING_RE.match(line)
        if match:
            depth = len(match.group(1))
            if depth > 4:
                pending_anchor = False
                continue
            title = match.group(2).strip()
            if not title:
                pending_anchor = False
                continue
            normalized = f"{'#' * depth} {title}"
            if normalized.startswith("# ") or normalized.startswith("## "):
                if lines:
                    lines.append("")
            lines.append(normalized)
            saw_heading = True
            pending_anchor = depth == 2
            continue
        if pending_anchor and ANCHOR_RE.match(line):
            lines.append(line)
            pending_anchor = False

    if not saw_heading:
        raise RuntimeError("Skeleton response did not contain Markdown headings.")
    if not any(line.startswith("# ") and not line.startswith("## ") for line in lines):
        lines.insert(0, "# 课程大纲")
        lines.insert(1, "")
    return "\n".join(lines).strip()

def strip_skeleton_anchors(skeleton_md: str) -> str:
    lines = [
        line
        for line in skeleton_md.splitlines()
        if not ANCHOR_RE.match(line.strip())
    ]
    return normalize_skeleton("\n".join(lines))

def attach_skeleton_anchors(
    skeleton_md: str,
    locations: list[ChapterLocation],
) -> str:
    clean_skeleton = strip_skeleton_anchors(skeleton_md)
    _, chapters = parse_chapters(clean_skeleton)
    if len(chapters) != len(locations):
        raise RuntimeError(
            "Cannot attach skeleton anchors because chapter count does not match "
            f"locations: chapters={len(chapters)} locations={len(locations)}"
        )

    location_by_id = {location.chapter_id: location for location in locations}
    anchored_lines: list[str] = []
    chapter_id = 0
    for raw_line in clean_skeleton.splitlines():
        line = raw_line.rstrip()
        anchored_lines.append(line)
        if line.startswith("## ") and not line.startswith("### "):
            chapter_id += 1
            location = location_by_id.get(chapter_id)
            if location is None:
                raise RuntimeError(f"Missing location for chapter {chapter_id}.")
            payload = {
                "chapter_id": chapter_id,
                "start_quote": location.start_quote,
            }
            anchored_lines.append(
                f"<!-- outline-anchor: {json.dumps(payload, ensure_ascii=False)} -->"
            )
    return "\n".join(anchored_lines).strip()

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
    if len(candidate_blocks) < len(top_level_items):
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

def repair_skeleton_anchors_from_policy(
    anchored_skeleton: str,
    policy: OutlinePolicy,
) -> str:
    clean_skeleton = strip_skeleton_anchors(anchored_skeleton)
    _, chapters = parse_chapters(clean_skeleton)
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

def _parse_skeleton_anchor_quotes_by_chapter(anchored_skeleton: str) -> dict[int, str]:
    anchor_by_chapter: dict[int, str] = {}
    current_chapter_id: int | None = None
    for raw_line in anchored_skeleton.splitlines():
        line = raw_line.strip()
        if line.startswith("## ") and not line.startswith("### "):
            current_chapter_id = (current_chapter_id or 0) + 1
            continue
        match = ANCHOR_RE.match(line)
        if not match:
            continue
        if current_chapter_id is None:
            raise RuntimeError("Skeleton anchor appeared before any top-level chapter.")
        if current_chapter_id in anchor_by_chapter:
            raise RuntimeError(f"Duplicate skeleton anchor for chapter {current_chapter_id}.")
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid skeleton anchor JSON for chapter {current_chapter_id}."
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Skeleton anchor for chapter {current_chapter_id} must be a JSON object.")
        start_quote = str(payload.get("start_quote", "")).strip()
        if not start_quote:
            raise RuntimeError(f"Empty skeleton anchor start_quote for chapter {current_chapter_id}.")
        anchor_by_chapter[current_chapter_id] = start_quote
    return anchor_by_chapter

def validate_skeleton_anchors_against_policy(
    anchored_skeleton: str,
    transcript: str,
    policy: OutlinePolicy,
) -> None:
    expectations = policy_anchor_expectations(policy)
    clean_skeleton = strip_skeleton_anchors(anchored_skeleton)
    _, chapters = parse_chapters(clean_skeleton)
    validate_skeleton_matches_policy(chapters, policy)

    anchor_by_chapter = _parse_skeleton_anchor_quotes_by_chapter(anchored_skeleton)
    for expectation in expectations:
        chapter_id = int(expectation["chapter_id"])
        expected_quote = str(expectation["start_quote"])
        actual_quote = anchor_by_chapter.get(chapter_id)
        if actual_quote is None:
            continue
        if actual_quote == expected_quote:
            continue
        raise RuntimeError(
            "Skeleton policy anchor mismatch at "
            f"{chapter_id}: expected {expected_quote!r}, got {actual_quote!r}"
        )

    expected_ids = {int(item["chapter_id"]) for item in expectations}
    actual_ids = set(anchor_by_chapter)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        raise RuntimeError(
            "Skeleton anchor count does not match policy expectations: "
            f"anchors={len(anchor_by_chapter)} expectations={len(expectations)} "
            f"missing={missing} extra={extra}"
        )

    _, _, locations = parse_skeleton_anchor_locations(anchored_skeleton, transcript)
    if len(locations) != len(expectations):
        raise RuntimeError(
            "Skeleton anchor count does not match policy expectations: "
            f"anchors={len(locations)} expectations={len(expectations)}"
        )

def apply_course_title(markdown: str, title: str | None) -> str:
    clean_title = (title or "").strip()
    if not clean_title:
        return markdown
    if ROOT_HEADING_RE.search(markdown):
        return ROOT_HEADING_RE.sub(f"# {clean_title}", markdown, count=1)
    return f"# {clean_title}\n\n{markdown.strip()}".strip()

def cap_heading_depths(markdown: str, max_depth: int = 4) -> str:
    capped: list[str] = []
    for raw_line in markdown.splitlines():
        match = HEADING_RE.match(raw_line.strip())
        if match and len(match.group(1)) > max_depth:
            capped.append(f"{'#' * max_depth} {match.group(2).strip()}")
        else:
            capped.append(raw_line)
    return "\n".join(capped).strip()

def build_skeleton_prompt(
    prompt_template: str,
    transcript: str,
    policy: OutlinePolicy | None = None,
    granularity_plan: GranularityPlan | None = None,
) -> str:
    policy_block = ""
    if policy:
        policy_block = f"""

顶级章节策略（必须遵守）：
```json
{format_outline_policy(policy)}
```

生成要求：
- 最终 `##` 必须与 `top_level_items` 数量、顺序和含义一致。
- 每个 `top_level_items` 条目必须生成且只生成一个对应 `##`。
- 不要新增、删除、重排、拆分或合并 `top_level_items` 中的 `##`。
- `merge_policy` 中要求并入相邻单元的总述、过渡和关联说明，只能作为对应 `##` 下的 `###`。
- `parallel_groups` 中的同类单元要尽量使用平行的 `###` 目录结构。
"""
    granularity_block = ""
    if granularity_plan:
        granularity_block = f"""

章节细分计划（必须遵守）：
```json
{json.dumps(granularity_plan, ensure_ascii=False, indent=2)}
```

生成要求：
- 每个 `##` 下的 `###` 数量必须不少于对应 `min_subsections`。
- `min_subsections=0` 表示该章节可以只保留 `##`，不要为了凑层级强行拆分。
- `source_chars` 较长的章节必须拆成稳定的 `###` 路牌，优先按逐字稿中的实际讲授顺序拆分。
- `max_depth=4` 表示最多允许到 `####`，默认仍优先使用 `###`。
"""
    return f"""{prompt_template.strip()}

---

这是 Pass 1：只输出大纲骨架。

硬性要求：
- 忽略通用模板中关于插入原文和输出正文的示例；本 Pass 绝对不要输出逐字稿正文。
- 只输出 Markdown 大纲结构，不填充任何逐字稿原文。
- 不输出“核心知识点”“时间节点”“说明”等额外总结区块。
- 每个章节/分章节/末级节点只使用简洁标题。
- 使用 `#` 输出唯一课程主题。
- 使用 `##` 输出可独立填充的顶级章节；每个 `##` 章节应对应讲师明确展开的一段课程模块、主题切换或内容板块。
- 每个 `##` 标题下一行必须紧跟一个 HTML 注释锚点：`<!-- outline-anchor: {{"chapter_id": 1, "start_quote": "从逐字稿逐字复制的该章开头短句"}} -->`。
- `chapter_id` 必须等于最终 `##` 的 1-based 顺序；`start_quote` 必须逐字复制自该章实际开始处的逐字稿，不要改写、概括或使用标题文本。
- 如果课程围绕教材单元逐一讲解，`##` 优先对应教材单元或独立综合实践活动，例如“第一单元：……”“第二单元：……”“综合与实践活动：……”。
- 不要把短暂总述、过渡、跨单元关联说明单独升成 `##`；这类内容应并入紧随其后的第一个真实教材单元下的 `###`，例如“单元整体编排结构”或“相关内容整体说明”。
- 连续相似单元要使用平行的 `###` 目录结构。若乘法、除法、乘除法单元都包含“运算意义/口诀或求商/解决问题/整理复习”，应尽量在各自 `##` 内保持一致层级。
- 不要把某个教材单元内部的“解决问题”“整理复习”“整体说明”抽成独立 `##`，除非讲师明确把它作为独立大板块反复展开。
- 不要生成“主要内容讲解”“课程主体部分”“各模块分析”这类只起包裹作用、会包含大量内容的过大 `##` 章节。
- 如果课程中按年级、学科、单元、课时、专题、任务、案例或活动切换，优先把这些真实切换点拆成独立 `##`。
- 如果某个 `##` 下包含三个以上可以独立讲解的单元、专题、案例或活动，通常说明该 `##` 过大，应拆成多个连续的 `##`；不要为了领域归类而牺牲实际讲授模块。
- 总览、背景或领域说明可以作为独立短 `##`，但不能包住后面多个具体模块。
- 后续层级优先使用 `###`；确有必要时可使用 `####`，最多四级，不得使用 `#####` 或更深层级。
- 例题、步骤、条目和连续要点优先作为 `###` 标题的一部分表达；只有长章节内部需要稳定路牌时才继续细拆成 `####`。
- 每个 `##` 如果包含两段以上不同功能的内容，可拆成 `###`；不要为了显得完整而继续细拆，`####` 只用于三级仍不足以承载讲授结构的长章节。
- 各章节不能只有一个粗略大标题；如果包含背景说明、目标要求、内容结构、方法策略、案例分析、教学建议、总结过渡等不同功能，优先拆成 `###`。
- 末级节点只保留标题，不要写正文、解释或引用块。
- 忠实于逐字稿讲授顺序，覆盖全文主要内容。
- 如果逐字稿末尾包含课程总结、实践期待、致谢或结束语，必须在最后设置对应章节或节点承载这些内容。
{policy_block}
{granularity_block}

结构细度参考示例：

```markdown
## 一、课程背景与整体说明
<!-- outline-anchor: {{"chapter_id": 1, "start_quote": "今天我们先看课程背景"}} -->
### 1.1 课程定位
### 1.2 内容结构

## 二、第一项核心内容
<!-- outline-anchor: {{"chapter_id": 2, "start_quote": "下面进入第一项核心内容"}} -->
### 2.1 概念或任务引入
### 2.2 案例、活动或方法展开

## 三、课程总结
<!-- outline-anchor: {{"chapter_id": 3, "start_quote": "最后我们做一个总结"}} -->
### 3.1 重点回顾
### 3.2 后续实践建议
```

以下是完整逐字稿：

```text
{transcript.strip()}
```"""

def parse_chapters(skeleton_md: str) -> tuple[str, list[tuple[int, str]]]:
    lines = skeleton_md.strip().splitlines()
    title_lines: list[str] = []
    chapters: list[tuple[int, str]] = []
    current: list[str] = []

    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            if current:
                chapters.append((len(chapters) + 1, "\n".join(current).strip()))
            current = [line]
            continue

        if current:
            current.append(line)
        else:
            title_lines.append(line)

    if current:
        chapters.append((len(chapters) + 1, "\n".join(current).strip()))

    title = "\n".join(line for line in title_lines if line.strip()).strip()
    if not title:
        title = "# 课程大纲"

    return title, chapters

def parse_skeleton_anchor_locations(
    anchored_skeleton: str,
    transcript: str,
) -> tuple[str, list[tuple[int, str]], list[ChapterLocation]]:
    clean_skeleton = strip_skeleton_anchors(anchored_skeleton)
    title, chapters = parse_chapters(clean_skeleton)
    if not chapters:
        raise RuntimeError("Anchored skeleton did not contain top-level chapters.")

    anchor_by_chapter: dict[int, str] = {}
    current_chapter_id: int | None = None
    for raw_line in anchored_skeleton.splitlines():
        line = raw_line.strip()
        if line.startswith("## ") and not line.startswith("### "):
            current_chapter_id = (current_chapter_id or 0) + 1
            continue
        match = ANCHOR_RE.match(line)
        if not match:
            continue
        if current_chapter_id is None:
            raise RuntimeError("Skeleton anchor appeared before any top-level chapter.")
        if current_chapter_id in anchor_by_chapter:
            raise RuntimeError(f"Duplicate skeleton anchor for chapter {current_chapter_id}.")
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid skeleton anchor JSON for chapter {current_chapter_id}."
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Skeleton anchor for chapter {current_chapter_id} must be a JSON object.")
        start_quote = str(payload.get("start_quote", "")).strip()
        if not start_quote:
            raise RuntimeError(f"Empty skeleton anchor start_quote for chapter {current_chapter_id}.")
        anchor_by_chapter[current_chapter_id] = start_quote

    missing = [chapter_id for chapter_id, _ in chapters if chapter_id not in anchor_by_chapter]
    if missing:
        raise RuntimeError(f"Skeleton anchors missing chapter IDs: {missing}")

    locations: list[ChapterLocation] = []
    search_from = 0
    for chapter_id, chapter_subtree in chapters:
        start_quote = anchor_by_chapter[chapter_id]
        start = find_unique_anchor_quote_start(transcript, start_quote, search_from)
        if start == -2:
            raise RuntimeError(
                f"Skeleton anchor quote for chapter {chapter_id} is ambiguous "
                f"after the previous chapter: {start_quote!r}"
            )
        if start < 0:
            raise RuntimeError(
                f"Skeleton anchor quote for chapter {chapter_id} was not found exactly "
                f"after the previous chapter: {start_quote!r}"
            )
        locations.append(
            ChapterLocation(
                chapter_id=chapter_id,
                heading=chapter_heading(chapter_subtree),
                start_quote=start_quote,
                start=start,
                source="skeleton_anchor",
            )
        )
        search_from = start + len(start_quote)

    validate_chapter_locations(locations, len(transcript))
    return title, chapters, locations

def validate_skeleton_matches_policy(
    chapters: list[tuple[int, str]],
    policy: OutlinePolicy,
) -> None:
    expected = [str(item).strip() for item in policy.get("top_level_items", []) if str(item).strip()]
    actual = [chapter_heading(chapter_subtree) for _, chapter_subtree in chapters]

    if len(actual) != len(expected):
        raise RuntimeError(
            "Skeleton top-level chapter count does not match outline_policy.top_level_items: "
            f"expected {len(expected)} {expected}, got {len(actual)} {actual}"
        )

    for index, (expected_title, actual_title) in enumerate(zip(expected, actual), start=1):
        expected_key = normalize_policy_heading_key(expected_title)
        actual_key = normalize_policy_heading_key(actual_title)
        if expected_key == actual_key:
            continue
        if expected_key and actual_key and (expected_key in actual_key or actual_key in expected_key):
            continue
        raise RuntimeError(
            "Skeleton top-level chapter mismatch at "
            f"{index}: expected outline_policy.top_level_items item "
            f"{expected_title!r}, got {actual_title!r}"
        )

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
    for chapter_id, chapter_subtree in chapters:
        if chapter_id > len(granularity_plan):
            continue
        item = granularity_plan[chapter_id - 1]
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

def validate_skeleton_matches_granularity(
    chapters: list[tuple[int, str]],
    granularity_plan: GranularityPlan,
) -> None:
    failures = collect_granularity_failures(chapters, granularity_plan)
    if not failures:
        return
    failure = failures[0]
    raise RuntimeError(
        "Skeleton chapter does not meet granularity plan at "
        f"{failure['chapter_id']}: expected at least {failure['min_subsections']} "
        f"subsection(s) for {failure.get('top_level_item')!r} "
        f"({failure.get('source_chars')} chars), got {failure['actual_subsections']}."
    )
