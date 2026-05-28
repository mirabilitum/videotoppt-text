from __future__ import annotations

import json
import os

from openai import OpenAI

from outline_models import (
    ChatResult,
    OutlinePolicy,
    PolicyMergeResult,
)
from outline_policy import (
    find_direct_policy_cover,
    format_outline_policy,
    parse_outline_policy,
)
from outline_text import env_float, env_int, strip_markdown_fence
from text_filter import assert_no_alias_fragments, decrypt_text, encrypt_text, load_sensitive_word_map


MODEL_DEFAULT = "deepseek-chat"
SYSTEM_PROMPT = "你是一名专业的课程内容分析师，擅长从课程转写文本中提取结构化大纲。"
TRUNCATED_FINISH_REASONS = {"length", "max_tokens"}


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
            temperature=env_float("DEEPSEEK_TEMPERATURE", 0.2),
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


def call_outline_policy_pass(client: OpenAI, prompt_template: str, transcript: str) -> OutlinePolicy:
    user_prompt = f"""{prompt_template.strip()}

---

这是 Pass 0：先分析全文结构，再确定候选顶级章节策略，只输出 JSON。

硬性要求：
- 先用 `course_structure_summary` 用一段话说明这份逐字稿整体讲了什么、按什么顺序展开。
- 再用 `ordered_blocks` 按逐字稿讲授顺序列出主要内容块；每个 block 必须说明覆盖范围、作用和开头原文脚注。
- `start_quote` 必须逐字复制自该内容块开头附近的逐字稿，帮助 merge 判断先后和来源；不要改写、概括或使用标题文本。
- `candidate_top_level_items` 是你认为可作为 `##` 的候选章节；`top_level_items` 是后续章节生成必须使用的最终 `##` 章节。
- `top_level_items` 顺序必须遵循逐字稿讲授顺序，并能从 `ordered_blocks` 推导出来。
- 如果课程围绕教材单元逐一解读，`top_level_items` 优先使用教材单元和独立综合实践活动。
- 短暂总述、过渡、跨单元关联说明、整体结构说明，不要放入 `top_level_items`；在 `merge_policy` 中说明它们应并入哪个相邻单元。
- 对讲解结构相似的连续单元，在 `parallel_groups` 中列出这些单元名称，供后续生成平行子节结构。
- 章节名称要准确、简洁，优先使用逐字稿中的教材单元名、活动名或讲师明确说出的板块名。
- 必须包含课程开头的整体说明章节和结尾致谢/结束语章节（如果逐字稿中存在）。
- 只输出一个 JSON 对象，不要输出 Markdown，不要解释。

JSON 输出格式：

```json
{{
  "course_structure_summary": "一段话说明全文按什么顺序展开、主要分几块、哪些是总述或过渡。",
  "ordered_blocks": [
    {{
      "block_id": "B01",
      "title": "课程开头总述",
      "scope_summary": "说明本课要介绍教材总体修订思路和修订重点，属于开场总述，不单独作为顶级章节。",
      "role": "overview",
      "start_quote": "从逐字稿中复制的该块开头短句",
      "candidate_top_level": false
    }},
    {{
      "block_id": "B02",
      "title": "第一项真实内容块",
      "scope_summary": "说明该块覆盖的讲授内容和边界。",
      "role": "main_section",
      "start_quote": "从逐字稿中复制的该块开头短句",
      "candidate_top_level": true
    }}
  ],
  "top_level_basis": "教材单元和独立综合实践活动",
  "candidate_top_level_items": [
    "教材整体编排与变化",
    "第一单元：单元名称",
    "第二单元：单元名称"
  ],
  "top_level_items": [
    "教材整体编排与变化",
    "第一单元：单元名称",
    "第二单元：单元名称"
  ],
  "merge_policy": "跨多个单元的总述、过渡和关联说明并入后续相关单元，不单独作为顶级章节。",
  "parallel_groups": [
    ["第二单元：同类单元名称", "第三单元：同类单元名称"]
  ]
}}
```

完整逐字稿：

```text
{transcript.strip()}
```"""
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_POLICY_MAX_TOKENS", 8192),
        max_continuations=env_int("OUTLINE_POLICY_MAX_CONTINUATIONS", 1),
    )
    return parse_outline_policy(result.content)


def call_outline_policy_merge_pass(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    policies: list[OutlinePolicy],
) -> OutlinePolicy:
    policy_blocks = "\n\n".join(
        f"Policy run {index + 1}:\n```json\n{format_outline_policy(policy)}\n```"
        for index, policy in enumerate(policies)
    )
    user_prompt = f"""{prompt_template.strip()}

---

这是 Pass 0 merge：把多次 outline policy 合并为一个 canonical policy，只输出 JSON。

硬性要求：
- 先阅读每个 policy 的 `course_structure_summary` 和 `ordered_blocks`，不要只合并标题列表。
- 以 `ordered_blocks.start_quote` 和逐字稿顺序作为排序依据；如果标题顺序和 start_quote 指向的顺序冲突，以逐字稿顺序为准。
- 合并的是内容块和覆盖范围，不是标题字符串；语义相同、覆盖范围相同或一方明显包含另一方时，必须合并为一个 canonical item。
- `top_level_items` 必须覆盖所有 policy run 中出现的实质性顶级要点，但语义重复、同义改写、粗细粒度重叠的条目只能保留一次。
- 如果一个 policy 的粗粒度章节包含另一个 policy 的多个细粒度章节，必须在 `merge_trace` 中说明拆分/包含关系；不要机械取标题并集。
- `top_level_items` 的顺序必须按逐字稿讲授顺序排列，不要按某个 policy 的原始顺序机械拼接。
- 不要根据字数阈值或填充便利性决定是否增删顶级章节；这里只处理 policy 并集。
- `merge_policy` 必须合并所有 policy run 中关于总述、过渡、跨单元说明应如何并入相邻章节的规则。
- `parallel_groups` 必须覆盖所有 policy run 中有价值的平行结构提示，语义重复的分组只保留一次。
- 必须输出 `merge_trace`，说明每个 canonical 顶级章节来自哪些 policy run / block / candidate item。
- 必须输出 `dropped_or_merged_items`，说明被合并、丢弃或降级为非顶级章节的项，以及原因。
- 必须输出 `ordering_basis`，说明最终顺序依据哪些 start_quote 或内容块顺序。
- 只输出一个 JSON 对象，不要输出 Markdown，不要解释。

JSON 输出格式：

```json
{{
  "course_structure_summary": "合并后的全文结构说明。",
  "ordered_blocks": [
    {{
      "block_id": "C01",
      "title": "合并后的内容块标题",
      "scope_summary": "这个内容块覆盖的范围，以及来自哪些 run 的哪些 block。",
      "role": "main_section",
      "start_quote": "能代表该内容块开头的逐字稿短句",
      "candidate_top_level": true
    }}
  ],
  "top_level_basis": "合并后的顶级章节依据",
  "candidate_top_level_items": ["合并后的候选章节"],
  "top_level_items": ["按逐字稿顺序排列的并集章节"],
  "merge_policy": "合并后的并入规则",
  "parallel_groups": [["同类章节A", "同类章节B"]],
  "ordering_basis": "最终顺序依据逐字稿中 C01/C02/C03 的 start_quote 出现顺序。",
  "merge_trace": [
    {{
      "canonical_item": "合并后的顶级章节",
      "sources": ["policy_run_01:B02", "policy_run_02:B01"],
      "decision": "merged",
      "reason": "两个来源覆盖同一段讲授内容，只是标题不同。"
    }}
  ],
  "dropped_or_merged_items": [
    {{
      "source": "policy_run_02:某标题",
      "decision": "merged_into",
      "target": "合并后的顶级章节",
      "reason": "该标题是 target 的同义改写或子范围。"
    }}
  ]
}}
```

待合并 policy：

{policy_blocks}

完整逐字稿：

```text
{transcript.strip()}
```"""
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_POLICY_MERGE_MAX_TOKENS", 8192),
        max_continuations=env_int("OUTLINE_POLICY_MERGE_MAX_CONTINUATIONS", 1),
    )
    return parse_outline_policy(result.content)


def merge_outline_policy_runs(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    policies: list[OutlinePolicy],
) -> PolicyMergeResult:
    if not policies:
        raise ValueError("At least one outline policy is required.")
    if len(policies) == 1:
        return PolicyMergeResult(policy=policies[0], reason="single_policy", source_run=1)

    direct_cover = find_direct_policy_cover(policies)
    if direct_cover is not None:
        index, policy = direct_cover
        return PolicyMergeResult(
            policy=policy,
            reason=f"policy_run_{index + 1:02d}_strict_superset",
            source_run=index + 1,
        )

    return PolicyMergeResult(
        policy=call_outline_policy_merge_pass(client, prompt_template, transcript, policies),
        reason="llm_union",
        source_run=None,
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


_CHINESE_NUMERALS = ["一","二","三","四","五","六","七","八","九","十",
                     "十一","十二","十三","十四","十五","十六","十七","十八","十九","二十"]

def _chapter_numeral(chapter_id: int) -> str:
    if 1 <= chapter_id <= len(_CHINESE_NUMERALS):
        return _CHINESE_NUMERALS[chapter_id - 1]
    return str(chapter_id)


def call_fill_chapter_draft(
    client: OpenAI,
    prompt_template: str,
    chapter_id: int,
    chapter_count: int,
    chapter_title: str,
    chapter_transcript: str,
    course_structure_summary: str,
    chapter_map: list[dict[str, str]],
) -> str:
    numeral = _chapter_numeral(chapter_id)
    chapter_map_text = "\n".join(
        f"- [{item['block_id']}] {item['title']}：{item['scope_summary']}"
        for item in chapter_map
    )
    user_prompt = f"""{prompt_template.strip()}

---

这是 Pass 2a：对当前章节逐字稿进行话题分段，输出分段结果供后续节点生成使用。

全课结构摘要：
{course_structure_summary.strip()}

全课章节地图（仅供定位）：
{chapter_map_text}

分段规则：
- 识别讲师的话题切换信号，包括但不限于："那么接下来"、"好，下面"、"第X个话题"、"那第X点"、"然后我们看"、"关于X"等口语转折词，以及明显的内容主题跳转。
- 每个段落必须包含至少 100 个汉字的实质性内容；不足 100 字的碎片（过渡语、致谢、停顿填充词）并入前一个段落。
- 不要因为讲师说了"话题一"、"话题二"就机械分段，要判断实际内容是否构成独立的讲授话题。
- 输出格式为 JSON 数组，每个元素包含：
  - `seg_id`：从 1 开始的段落编号
  - `topic`：一句话说明本段的核心讲授内容（10-20 字）
  - `text`：本段逐字稿原文，逐字保留不改写

只输出 JSON 数组，不要输出其他内容。

当前章节：{chapter_id}/{chapter_count}
当前章节标题：{chapter_title}

当前章节逐字稿：

```text
{chapter_transcript.strip()}
```"""
    dynamic_max = min(16384, max(8192, int(len(chapter_transcript) * 1.2) + 2048))
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_FILL_MAX_TOKENS", dynamic_max),
        max_continuations=env_int("OUTLINE_FILL_MAX_CONTINUATIONS", 3),
    )
    if result.continuations:
        print(f"Pass 2a chapter {chapter_id} continuation requests={result.continuations}")
    return strip_markdown_fence(result.content)


def call_fill_chapter_merge(
    client: OpenAI,
    prompt_template: str,
    chapter_id: int,
    chapter_count: int,
    draft: str,
) -> str:
    numeral = _chapter_numeral(chapter_id)
    user_prompt = f"""{prompt_template.strip()}

---

这是 Pass 2b：根据分段结果生成章节 Markdown，包含结构节点和逐字稿原文。

输入是一个 JSON 数组，每个元素包含 seg_id、topic（段落主题）、text（逐字稿原文）。

节点生成规则：
- 只输出当前章节，从 `##` 标题开始。
- `##` 标题格式固定为：`## {numeral}、章节标题文字`，不得修改标题文字。
- 根据各段的 topic 和内容判断节点结构：
  - 内容独立、主题明显不同的段落各自生成 `###` 节点。
  - 语义相近、内容连贯的相邻段落合并为同一 `###` 节点，节点标题概括合并后的完整内容。
  - 单个 `###` 节点内容丰富、可明显细分为多个子话题时，拆成 `####` 子节点。
- `###` 编号格式：`{chapter_id}.1`、`{chapter_id}.2`……依序递增，不得跳号。
- `####` 编号格式：在各自父节点下从 `{chapter_id}.X.1` 开始依序递增，不得跳号。
- 标题层级最深到 `####`，不得使用 `#####` 或更深层级。
- 逐字稿原文必须逐字保留，不改写、不总结、不润色，保持连续流动，不加 `>` 引用块。
- 输出纯 Markdown，不要包裹代码围栏。

当前章节：{chapter_id}/{chapter_count}

分段结果：

```json
{draft.strip()}
```"""
    dynamic_max = min(16384, max(8192, int(len(draft) * 1.2) + 2048))
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_MERGE_MAX_TOKENS", dynamic_max),
        max_continuations=env_int("OUTLINE_MERGE_MAX_CONTINUATIONS", 3),
    )
    if result.continuations:
        print(f"Pass 2b chapter {chapter_id} continuation requests={result.continuations}")
    return strip_markdown_fence(result.content)
