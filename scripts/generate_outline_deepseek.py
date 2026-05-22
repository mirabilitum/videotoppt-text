from __future__ import annotations

import argparse
from bisect import bisect_left
from dataclasses import dataclass
import json
import os
import re
from pathlib import Path

from openai import OpenAI

from common import load_config, output_dir
from text_filter import (
    adjust_span_to_alias_boundary,
    assert_no_alias_fragments,
    decrypt_text,
    encrypt_text,
    load_sensitive_word_map,
)


DEFAULT_PROMPT_PATH = Path("D:/video/prompt/prompt.md")
MODEL_DEFAULT = "deepseek-chat"
SYSTEM_PROMPT = "你是一名专业的课程内容分析师，擅长从课程转写文本中提取结构化大纲。"
TRUNCATED_FINISH_REASONS = {"length", "max_tokens"}
HEADING_RE = re.compile(r"^(#{1,})\s+(.+?)\s*$")
ROOT_HEADING_RE = re.compile(r"^#\s+.+$", re.MULTILINE)


@dataclass(frozen=True)
class ChatResult:
    content: str
    finish_reason: str
    continuations: int


@dataclass(frozen=True)
class ChapterLocation:
    chapter_id: int
    heading: str
    start_quote: str
    start: int


def outline_prompt_path() -> Path:
    configured = os.getenv("OUTLINE_PROMPT_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_PROMPT_PATH


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:[A-Za-z0-9_-]+)?\s*(.*?)\s*```", stripped, re.DOTALL)
    return match.group(1).strip() if match else stripped


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def normalize_skeleton(skeleton_md: str) -> str:
    headings: list[str] = []
    for raw_line in strip_markdown_fence(skeleton_md).splitlines():
        line = raw_line.strip()
        match = HEADING_RE.match(line)
        if not match:
            continue

        depth = min(len(match.group(1)), 4)
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


def apply_course_title(markdown: str, title: str | None) -> str:
    clean_title = (title or "").strip()
    if not clean_title:
        return markdown
    if ROOT_HEADING_RE.search(markdown):
        return ROOT_HEADING_RE.sub(f"# {clean_title}", markdown, count=1)
    return f"# {clean_title}\n\n{markdown.strip()}".strip()


def read_course_title(out: Path) -> str | None:
    course_info_path = out / "course_info.json"
    if not course_info_path.exists():
        return None
    try:
        payload = json.loads(course_info_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid course_info.json: {course_info_path}") from exc
    title = payload.get("title") if isinstance(payload, dict) else None
    return str(title).strip() if title else None


def strip_heading_number(title: str) -> str:
    return re.sub(r"^[一二三四五六七八九十百]+[、.．]\s*", "", title).strip()


def chapter_heading(chapter_subtree: str) -> str:
    first_line = chapter_subtree.splitlines()[0] if chapter_subtree.splitlines() else ""
    match = HEADING_RE.match(first_line.strip())
    return match.group(2).strip() if match else first_line.strip().lstrip("#").strip()


def find_first(text: str, needles: tuple[str, ...], start: int = 0) -> int:
    matches = [text.find(needle, start) for needle in needles]
    matches = [idx for idx in matches if idx >= 0]
    return min(matches) if matches else -1


def normalize_with_index(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    index_map: list[int] = []
    for index, char in enumerate(text):
        if char.isalnum():
            chars.append(char.lower())
            index_map.append(index)
    return "".join(chars), index_map


def find_quote_start(text: str, quote: str, start: int = 0) -> int:
    exact = text.find(quote, start)
    if exact >= 0:
        return exact

    normalized_text, index_map = normalize_with_index(text)
    normalized_quote, _ = normalize_with_index(quote)
    if not normalized_quote or not index_map:
        return -1

    normalized_start = bisect_left(index_map, start)
    normalized_exact = normalized_text.find(normalized_quote, normalized_start)
    if normalized_exact >= 0:
        return index_map[normalized_exact]

    min_length = min(12, len(normalized_quote))
    max_length = min(50, len(normalized_quote))
    for length in range(max_length, min_length - 1, -1):
        for offset in range(0, len(normalized_quote) - length + 1):
            fragment = normalized_quote[offset : offset + length]
            found = normalized_text.find(fragment, normalized_start)
            if found >= 0:
                estimated_start = max(normalized_start, found - offset)
                return index_map[estimated_start]

    return -1


def heading_search_terms(heading: str) -> list[str]:
    clean = strip_heading_number(heading)
    clean = re.sub(r"^\d+(?:\.\d+)*\s*", "", clean).strip()
    candidates = [clean]
    for delimiter in ("：", ":", "、"):
        if delimiter in clean:
            candidates.append(clean.split(delimiter, 1)[1].strip())
    candidates.append(re.sub(r"^第[一二三四五六七八九十百0-9]+[章节单元课时部分]*", "", clean).strip())

    seen: set[str] = set()
    terms: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip(" ：:、，,。.;；")
        if len(compact_text(candidate)) < 4 or candidate in seen:
            continue
        terms.append(candidate)
        seen.add(candidate)
    return terms


def find_heading_start(text: str, heading: str, start: int = 0) -> int:
    starts = [
        find_quote_start(text, term, start)
        for term in heading_search_terms(heading)
    ]
    starts = [item for item in starts if item >= 0]
    return min(starts) if starts else -1


def slice_chapter_transcripts(
    transcript: str,
    chapters: list[tuple[int, str]],
    locations: list[ChapterLocation],
) -> dict[int, str]:
    slices: dict[int, str] = {}
    starts = {location.chapter_id: location.start for location in locations}
    aliases = set(load_sensitive_word_map().values())

    for index, (chapter_id, _) in enumerate(chapters):
        start = starts[chapter_id]
        next_id = chapters[index + 1][0] if index + 1 < len(chapters) else None
        end = starts[next_id] if next_id is not None else len(transcript)
        if end <= start:
            end = len(transcript)
        start, end = adjust_span_to_alias_boundary(transcript, start, end, aliases)
        slices[chapter_id] = transcript[start:end].strip()

    return slices


def clip_intro_to_first_chapter(intro: str, transcript: str, first_chapter_start: int) -> str:
    if first_chapter_start <= 0:
        return intro.strip()

    expected_intro = transcript[:first_chapter_start].strip()
    if not expected_intro:
        return ""

    if not intro.strip() or len(compact_text(intro)) > len(compact_text(expected_intro)):
        return expected_intro

    return intro.strip()


def cap_heading_depths(markdown: str, max_depth: int = 4) -> str:
    capped: list[str] = []
    for raw_line in markdown.splitlines():
        match = HEADING_RE.match(raw_line.strip())
        if match and len(match.group(1)) > max_depth:
            capped.append(f"{'#' * max_depth} {match.group(2).strip()}")
        else:
            capped.append(raw_line)
    return "\n".join(capped).strip()


def call_chat(
    client: OpenAI,
    *,
    user_prompt: str,
    max_tokens: int,
    max_continuations: int = 3,
) -> ChatResult:
    word_map = load_sensitive_word_map()
    filtered_user_prompt = encrypt_text(user_prompt, word_map)
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": filtered_user_prompt,
        },
    ]
    chunks: list[str] = []

    for continuation_count in range(max_continuations + 1):
        response = client.chat.completions.create(
            model=os.getenv("DEEPSEEK_MODEL", MODEL_DEFAULT),
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        finish_reason = choice.finish_reason or ""
        chunks.append(content)

        if finish_reason not in TRUNCATED_FINISH_REASONS:
            filtered_content = strip_markdown_fence("\n".join(chunks))
            assert_no_alias_fragments(filtered_content, word_map)
            return ChatResult(
                content=decrypt_text(filtered_content, word_map),
                finish_reason=finish_reason,
                continuations=continuation_count,
            )

        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": (
                    "继续从上一条输出中断的位置写，不要重复已经输出的内容，"
                    "不要添加说明。保持相同 Markdown 结构。"
                ),
            }
        )

    raise RuntimeError(
        "Model output was still truncated after "
        f"{max_continuations} continuation request(s)."
    )


def call_skeleton_pass(client: OpenAI, prompt_template: str, transcript: str) -> str:
    user_prompt = build_skeleton_prompt(prompt_template, transcript)
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_SKELETON_MAX_TOKENS", 8192),
        max_continuations=env_int("OUTLINE_SKELETON_MAX_CONTINUATIONS", 3),
    )
    skeleton = normalize_skeleton(result.content)

    if result.continuations:
        print(f"Pass 1 continuation requests={result.continuations}")
    return skeleton


def split_transcript_chunks(
    transcript: str,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("SKELETON_CHUNK_CHARS must be a positive integer.")
    if overlap < 0:
        raise ValueError("SKELETON_CHUNK_OVERLAP must be 0 or a positive integer.")
    if overlap >= chunk_size:
        raise ValueError("SKELETON_CHUNK_OVERLAP must be smaller than chunk size.")

    chunks: list[str] = []
    start = 0
    while start < len(transcript):
        end = min(len(transcript), start + chunk_size)
        chunks.append(transcript[start:end])
        if end >= len(transcript):
            break
        start = end - overlap
    return chunks


def call_skeleton_merge_pass(client: OpenAI, sub_skeletons: list[str]) -> str:
    combined = "\n\n---\n\n".join(
        f"子骨架 {index + 1}：\n\n{skeleton.strip()}"
        for index, skeleton in enumerate(sub_skeletons)
    )
    user_prompt = f"""这是 Pass 1 合并：把多个按原文顺序生成的课程大纲子骨架合并为一个最终骨架。

硬性要求：
- 只输出 Markdown 大纲结构，不填充任何逐字稿原文。
- 使用 `#` 输出唯一课程主题。
- 使用 `##` 输出顶级章节；后续层级只使用 `###`、`####`，最多四级。
- 保持子骨架出现的原始顺序。
- 只允许删除或合并重复章节、统一层级和清理重复标题。
- 不允许新增未出现在子骨架中的章节。
- 不要输出说明、分析或代码围栏。

待合并子骨架：

{combined}"""
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_SKELETON_MERGE_MAX_TOKENS", 8192),
        max_continuations=env_int("OUTLINE_SKELETON_MERGE_MAX_CONTINUATIONS", 3),
    )
    if result.continuations:
        print(f"Pass 1 merge continuation requests={result.continuations}")
    return normalize_skeleton(result.content)


def call_skeleton_pass_chunked(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    chunk_size: int,
    overlap: int,
) -> str:
    chunks = split_transcript_chunks(transcript, chunk_size, overlap)
    sub_skeletons: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        print(f"Pass 1: generating chunk skeleton {index}/{len(chunks)} chars={len(chunk)}")
        sub_skeletons.append(call_skeleton_pass(client, prompt_template, chunk))
    print(f"Pass 1: merging {len(sub_skeletons)} chunk skeletons...")
    return call_skeleton_merge_pass(client, sub_skeletons)


def build_skeleton_prompt(
    prompt_template: str,
    transcript: str,
) -> str:
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
- 不要生成“主要内容讲解”“课程主体部分”“各模块分析”这类只起包裹作用、会包含大量内容的过大 `##` 章节。
- 如果课程中按年级、学科、单元、课时、专题、任务、案例或活动切换，优先把这些真实切换点拆成独立 `##`。
- 如果某个 `##` 下包含三个以上可以独立讲解的单元、专题、案例或活动，通常说明该 `##` 过大，应拆成多个连续的 `##`；不要为了领域归类而牺牲实际讲授模块。
- 总览、背景或领域说明可以作为独立短 `##`，但不能包住后面多个具体模块。
- 后续层级只使用 `###`、`####`，最多四级；不得使用 `#####` 或更深层级。
- 每个 `##` 如果包含两段以上不同功能的内容，应继续拆成 `###`；如果 `###` 内包含列举、对比、例题分析、教学提示等不同要点，应拆到 `####`。
- 各章节不能只有一个粗略大标题；如果包含背景说明、目标要求、内容结构、方法策略、案例分析、教学建议、总结过渡等不同功能，应继续拆成 `###` 或 `####`。
- 末级节点只保留标题，不要写正文、解释或引用块。
- 忠实于逐字稿讲授顺序，覆盖全文主要内容。
- 如果逐字稿末尾包含课程总结、实践期待、致谢或结束语，必须在最后设置对应章节或节点承载这些内容。

结构细度参考示例：

```markdown
## 一、课程背景与整体说明
### 1.1 课程定位
#### 1.1.1 讲授对象与内容范围
#### 1.1.2 本节课的核心问题
### 1.2 内容结构
#### 1.2.1 主要模块安排
#### 1.2.2 模块之间的关系

## 二、第一项核心内容
### 2.1 概念或任务引入
#### 2.1.1 关键概念说明
#### 2.1.2 学习或教学目标
### 2.2 案例、活动或方法展开
#### 2.2.1 示例分析
#### 2.2.2 实施建议

## 三、课程总结
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


def call_location_pass(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    chapters: list[tuple[int, str]],
) -> list[ChapterLocation]:
    headings = "\n".join(
        f"{chapter_id}. {chapter_heading(chapter_subtree)}"
        for chapter_id, chapter_subtree in chapters
    )
    user_prompt = f"""{prompt_template.strip()}

---

这是 Pass 1.2：为顶级章节定位逐字稿起点。

硬性要求：
- 只根据下面的顶级章节列表和完整逐字稿工作。
- 为每个顶级章节找出它在逐字稿中开始的位置。
- `start_quote` 必须逐字复制自完整逐字稿，长度 12 到 60 个中文字符左右。
- `start_quote` 应该选在该章节实际内容开始处，不要选 Markdown 标题，因为标题不在逐字稿中。
- 各章节的 `start_quote` 必须按照逐字稿顺序递增。
- 第一章如果紧接课程开场之后开始，选择第一章实际讲解的第一句；不要选择课程总标题或泛泛问候。
- 只输出 JSON 数组，不要输出 Markdown，不要解释。

顶级章节列表：

{headings}

JSON 输出格式：

```json
[
  {{"chapter_id": 1, "start_quote": "从逐字稿中复制的该章开头短句"}},
  {{"chapter_id": 2, "start_quote": "从逐字稿中复制的该章开头短句"}}
]
```

完整逐字稿：

```text
{transcript.strip()}
```"""
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_LOCATION_MAX_TOKENS", 4096),
        max_continuations=env_int("OUTLINE_LOCATION_MAX_CONTINUATIONS", 1),
    )
    return parse_chapter_locations(result.content, transcript, chapters)


def parse_single_chapter_location(
    content: str,
    transcript: str,
    chapter_id: int,
    heading: str,
    search_from: int,
) -> ChapterLocation:
    json_text = strip_markdown_fence(content)
    try:
        raw_location = json.loads(json_text)
    except json.JSONDecodeError as exc:
        preview = json_text[:500].replace("\n", "\\n")
        raise RuntimeError(f"Invalid chapter location JSON: {preview}") from exc

    if isinstance(raw_location, list):
        if len(raw_location) != 1:
            raise RuntimeError("Windowed location response must contain one item.")
        raw_location = raw_location[0]
    if not isinstance(raw_location, dict):
        raise RuntimeError("Windowed location response must be a JSON object.")

    response_id = int(raw_location.get("chapter_id", 0))
    if response_id != chapter_id:
        raise RuntimeError(
            f"Windowed location response returned chapter_id={response_id}, "
            f"expected {chapter_id}."
        )

    start_quote = str(raw_location.get("start_quote", "")).strip()
    if not start_quote:
        raise RuntimeError(f"Empty start_quote for chapter {chapter_id}")

    start = find_quote_start(transcript, start_quote, search_from)
    if start < 0:
        start = find_heading_start(transcript, heading, search_from)
    if start < 0:
        raise RuntimeError(
            f"start_quote for chapter {chapter_id} was not found after the previous chapter: "
            f"{start_quote!r}"
        )

    return ChapterLocation(
        chapter_id=chapter_id,
        heading=heading,
        start_quote=start_quote,
        start=start,
    )


def call_location_pass_windowed(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    chapters: list[tuple[int, str]],
) -> list[ChapterLocation]:
    window_chars = env_int("OUTLINE_LOCATION_WINDOW_CHARS", 8000)
    if window_chars <= 0:
        raise ValueError("OUTLINE_LOCATION_WINDOW_CHARS must be a positive integer.")

    locations: list[ChapterLocation] = []
    prev_start = 0
    for index, (chapter_id, chapter_subtree) in enumerate(chapters, start=1):
        heading = chapter_heading(chapter_subtree)
        last_error: Exception | None = None
        for multiplier in (1, 2):
            window_end = min(len(transcript), prev_start + (window_chars * multiplier))
            window = transcript[prev_start:window_end]
            user_prompt = f"""{prompt_template.strip()}

---

这是 Pass 1.2：为单个顶级章节定位逐字稿起点。

硬性要求：
- 只根据下面的单个顶级章节和逐字稿搜索窗口工作。
- 为该章节找出它在完整逐字稿中开始的位置。
- `start_quote` 必须逐字复制自搜索窗口，长度 12 到 60 个中文字符左右。
- `start_quote` 应该选在该章节实际内容开始处，不要选 Markdown 标题，因为标题不在逐字稿中。
- 只输出一个 JSON 对象，不要输出 Markdown，不要解释。

当前章节：{chapter_id}/{len(chapters)}

顶级章节标题：
{heading}

JSON 输出格式：

```json
{{"chapter_id": {chapter_id}, "start_quote": "从逐字稿中复制的该章开头短句"}}
```

逐字稿搜索窗口（完整逐字稿偏移 {prev_start} 到 {window_end}）：

```text
{window.strip()}
```"""
            result = call_chat(
                client,
                user_prompt=user_prompt,
                max_tokens=env_int("OUTLINE_LOCATION_MAX_TOKENS", 4096),
                max_continuations=env_int("OUTLINE_LOCATION_MAX_CONTINUATIONS", 1),
            )
            try:
                location = parse_single_chapter_location(
                    result.content,
                    transcript,
                    chapter_id,
                    heading,
                    prev_start,
                )
            except Exception as exc:
                last_error = exc
                if multiplier == 1 and window_end < len(transcript):
                    print(
                        "Pass 1.2: extending location window "
                        f"chapter={chapter_id} chars={window_chars * 2}"
                    )
                    continue
                raise

            locations.append(location)
            prev_start = location.start
            print(
                f"Pass 1.2: located chapter {index}/{len(chapters)} "
                f"start={location.start}"
            )
            break
        else:
            if last_error:
                raise last_error

    return locations


def parse_chapter_locations(
    content: str,
    transcript: str,
    chapters: list[tuple[int, str]],
) -> list[ChapterLocation]:
    json_text = strip_markdown_fence(content)
    try:
        raw_locations = json.loads(json_text)
    except json.JSONDecodeError as exc:
        preview = json_text[:500].replace("\n", "\\n")
        raise RuntimeError(f"Invalid chapter location JSON: {preview}") from exc
    if not isinstance(raw_locations, list):
        raise RuntimeError("Chapter location response must be a JSON array.")

    by_id: dict[int, str] = {}
    for item in raw_locations:
        if not isinstance(item, dict):
            raise RuntimeError("Each chapter location item must be a JSON object.")
        chapter_id = int(item.get("chapter_id", 0))
        start_quote = str(item.get("start_quote", "")).strip()
        if chapter_id in by_id:
            raise RuntimeError(f"Duplicate chapter location: {chapter_id}")
        by_id[chapter_id] = start_quote

    expected_ids = [chapter_id for chapter_id, _ in chapters]
    missing = [chapter_id for chapter_id in expected_ids if chapter_id not in by_id]
    if missing:
        raise RuntimeError(f"Chapter location response missing IDs: {missing}")

    locations: list[ChapterLocation] = []
    search_from = 0
    for chapter_id, chapter_subtree in chapters:
        start_quote = by_id[chapter_id]
        if not start_quote:
            raise RuntimeError(f"Empty start_quote for chapter {chapter_id}")
        start = find_quote_start(transcript, start_quote, search_from)
        if start < 0:
            start = find_heading_start(
                transcript,
                chapter_heading(chapter_subtree),
                search_from,
            )
        if start < 0:
            raise RuntimeError(
                f"start_quote for chapter {chapter_id} was not found after the previous chapter: "
                f"{start_quote!r}"
            )
        locations.append(
            ChapterLocation(
                chapter_id=chapter_id,
                heading=chapter_heading(chapter_subtree),
                start_quote=start_quote,
                start=start,
            )
        )
        search_from = start + len(start_quote)

    return locations


def format_chapter_locations(locations: list[ChapterLocation]) -> str:
    return json.dumps(
        [
            {
                "chapter_id": location.chapter_id,
                "heading": location.heading,
                "start_quote": location.start_quote,
                "start": location.start,
            }
            for location in locations
        ],
        ensure_ascii=False,
        indent=2,
    )


def call_intro_pass(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    first_chapter_heading: str,
) -> str:
    user_prompt = f"""{prompt_template.strip()}

---

这是 Pass 1.5：只提取课程开头引语。

硬性要求：
- 只输出课程开头总体介绍的逐字稿原文，不要输出任何 Markdown 标题。
- 输出范围是逐字稿开头到第一个顶级章节内容开始之前。
- 第一个顶级章节的大纲标题是：{first_chapter_heading}
- 不改写、不总结、不润色原文。
- 如果没有开头引语，输出空字符串。

完整逐字稿：

```text
{transcript.strip()}
```"""
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_INTRO_MAX_TOKENS", 2048),
        max_continuations=env_int("OUTLINE_INTRO_MAX_CONTINUATIONS", 1),
    )
    return strip_markdown_fence(result.content).strip()


def call_fill_chapter(
    client: OpenAI,
    fill_prompt: str,
    outline_subtree: str,
    chapter_transcript: str,
    chapter_id: int,
    chapter_count: int,
) -> str:
    user_prompt = f"""{fill_prompt.strip()}

---

这是 Pass 2：只填充指定顶级章节。

硬性要求：
- 只处理下面给出的”当前章节大纲子树”，不要输出其他章节。
- 不修改当前章节大纲结构，不新增、删除、合并、重排标题。
- 保持当前章节大纲层级，最多只允许 `####`，不得使用 `#####` 或更深层级。
- 逐字稿原文是主体，保持连续流动，不加 `>` 引用块。
- 将当前章节大纲子树的各级标题，作为路牌按讲授顺序插入原文对应位置。
- 标题插在对应内容开始之前，原文紧跟在标题后自然流动。
- 如果某段原文无法细化到末级节点，保留在最近的上级标题之后即可。
- 原文必须逐字保留，不改写、不总结、不润色。
- 原文不要跨章节重复；明显属于其他章节的内容不要填入当前章节。
- 输出纯 Markdown，不要包裹代码围栏。

当前章节：{chapter_id}/{chapter_count}

当前章节大纲子树：

```markdown
{outline_subtree.strip()}
```

当前章节候选逐字稿：

```text
{chapter_transcript.strip()}
```"""
    dynamic_max = min(16384, max(8192, int(len(chapter_transcript) * 0.8) + 2048))
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_FILL_MAX_TOKENS", dynamic_max),
        max_continuations=env_int("OUTLINE_FILL_MAX_CONTINUATIONS", 3),
    )
    if result.continuations:
        print(f"Pass 2 chapter {chapter_id} continuation requests={result.continuations}")
    return cap_heading_depths(strip_markdown_fence(result.content))


def merge_outline(title: str, intro: str, filled_chapters: list[str]) -> str:
    clean_title = title.strip()
    clean_intro = intro.strip()
    clean_chapters = [chapter.strip() for chapter in filled_chapters if chapter.strip()]
    parts = [clean_title]
    if clean_intro:
        parts.append(clean_intro)
    parts.extend(clean_chapters)
    return "\n\n".join(parts) + "\n"


def strip_part_markers(transcript: str) -> str:
    return re.sub(r"\[Part \d+\]\s*", "", transcript)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a structured course outline from transcript.txt."
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing outline_skeleton.md, outline_intro.md, and chapter files.",
    )
    parser.add_argument(
        "--rerun-from",
        type=int,
        default=0,
        help="With --resume, regenerate chapters whose 1-based index is at least this value.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_config()

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is missing. Fill it in .env first.")

    prompt_path = outline_prompt_path()
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing outline prompt: {prompt_path}")

    out = output_dir()
    transcript_path = out / "transcript.txt"
    if not transcript_path.exists():
        raise FileNotFoundError(f"Missing transcript: {transcript_path}")

    prompt_template = prompt_path.read_text(encoding="utf-8")
    transcript = strip_part_markers(transcript_path.read_text(encoding="utf-8"))
    course_title = read_course_title(out)
    char_count = len(transcript)
    print(f"transcript_chars={char_count}")
    if char_count > 30000:
        print(f"WARNING: transcript is long ({char_count} chars); chunked skeleton will be used")
    elif char_count > 20000:
        print(f"INFO: transcript is medium length ({char_count} chars)")

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=env_float("DEEPSEEK_TIMEOUT", 180.0),
        max_retries=env_int("DEEPSEEK_MAX_RETRIES", 2),
    )

    skeleton_path = out / "outline_skeleton.md"
    if args.resume and skeleton_path.exists():
        print(f"Pass 1: reusing outline skeleton {skeleton_path}")
        skeleton = skeleton_path.read_text(encoding="utf-8").strip()
        titled_skeleton = apply_course_title(skeleton, course_title)
        if titled_skeleton != skeleton:
            skeleton = titled_skeleton
            skeleton_path.write_text(skeleton + "\n", encoding="utf-8")
    else:
        print("Pass 1: generating outline skeleton...")
        chunk_threshold = env_int("OUTLINE_SKELETON_CHUNK_THRESHOLD", 30000)
        if char_count > chunk_threshold:
            skeleton = call_skeleton_pass_chunked(
                client,
                prompt_template,
                transcript,
                env_int("OUTLINE_SKELETON_CHUNK_CHARS", 15000),
                env_int("OUTLINE_SKELETON_CHUNK_OVERLAP", 500),
            )
        else:
            skeleton = call_skeleton_pass(client, prompt_template, transcript)
        skeleton = apply_course_title(skeleton, course_title)
        skeleton_path.write_text(skeleton + "\n", encoding="utf-8")

    title, chapters = parse_chapters(skeleton)
    if not chapters:
        raise RuntimeError(f"No top-level chapters found in skeleton: {skeleton_path}")

    print(f"outline_skeleton.md={skeleton_path}")
    print(f"chapters={len(chapters)}")

    locations_path = out / "outline_locations.json"
    if args.resume and locations_path.exists():
        print(f"Pass 1.2: reusing chapter locations {locations_path}")
        try:
            locations = parse_chapter_locations(
                locations_path.read_text(encoding="utf-8"),
                transcript,
                chapters,
            )
        except Exception as exc:
            print(
                "Pass 1.2: existing chapter locations were invalid, regenerating "
                f"({exc})"
            )
            locations = call_location_pass_windowed(
                client,
                prompt_template,
                transcript,
                chapters,
            )
    else:
        print("Pass 1.2: locating chapter starts...")
        locations = call_location_pass_windowed(
            client,
            prompt_template,
            transcript,
            chapters,
        )
    locations_path.write_text(format_chapter_locations(locations) + "\n", encoding="utf-8")
    chapter_transcripts = slice_chapter_transcripts(transcript, chapters, locations)

    first_heading = chapters[0][1].splitlines()[0] if chapters[0][1].splitlines() else ""
    intro_path = out / "outline_intro.md"
    if args.resume and intro_path.exists():
        print(f"Pass 1.5: reusing opening intro {intro_path}")
        intro = intro_path.read_text(encoding="utf-8").strip()
    else:
        print("Pass 1.5: extracting opening intro...")
        intro_transcript = transcript[: locations[0].start + 500]
        intro = call_intro_pass(client, prompt_template, intro_transcript, first_heading)
    intro = clip_intro_to_first_chapter(intro, transcript, locations[0].start)
    intro_path.write_text(intro + "\n", encoding="utf-8")

    chapters_dir = out / "outline_chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    filled_chapters: list[str] = []
    for chapter_id, chapter_subtree in chapters:
        heading = chapter_subtree.splitlines()[0] if chapter_subtree.splitlines() else ""
        chapter_path = chapters_dir / f"outline_chapter_{chapter_id:03d}.md"
        should_reuse = (
            args.resume
            and chapter_path.exists()
            and chapter_path.stat().st_size > 0
            and (args.rerun_from <= 0 or chapter_id < args.rerun_from)
        )
        if should_reuse:
            print(f"Pass 2: reusing chapter {chapter_id}/{len(chapters)} {heading}")
            filled_chapters.append(chapter_path.read_text(encoding="utf-8").strip())
            continue

        print(f"Pass 2: filling chapter {chapter_id}/{len(chapters)} {heading}")
        filled = call_fill_chapter(
            client,
            prompt_template,
            chapter_subtree,
            chapter_transcripts[chapter_id],
            chapter_id,
            len(chapters),
        )
        chapter_path.write_text(filled + "\n", encoding="utf-8")
        filled_chapters.append(filled)

    outline = merge_outline(title, intro, filled_chapters)
    outline_path = out / "outline.md"
    outline_path.write_text(outline, encoding="utf-8")

    print(f"prompt={prompt_path}")
    print(f"outline_locations.json={locations_path}")
    print(f"outline_intro.md={intro_path}")
    print(f"outline.md={outline_path}")
    print(f"chars={len(outline)}")


if __name__ == "__main__":
    main()
