from __future__ import annotations

import json
import os

from openai import OpenAI

from outline_granularity import (
    build_granularity_plan_from_locations,
    format_granularity_plan,
)
from outline_models import (
    ChatResult,
    GranularityPlan,
    OutlinePolicy,
    PolicyMergeResult,
    SkeletonGenerationResult,
    SkeletonRepairError,
)
from outline_policy import (
    find_direct_policy_cover,
    format_outline_policy,
    parse_outline_policy,
)
from outline_skeleton import (
    apply_course_title,
    build_skeleton_prompt,
    cap_heading_depths,
    collect_granularity_failures,
    normalize_anchored_skeleton,
    parse_chapters,
    parse_skeleton_anchor_locations,
    policy_anchor_expectations,
    repair_skeleton_anchors_from_policy,
    strip_skeleton_anchors,
    validate_skeleton_anchors_against_policy,
    validate_skeleton_matches_granularity,
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
- `candidate_top_level_items` 是你认为可作为 `##` 的候选章节；`top_level_items` 是后续 Pass 1 必须使用的最终 `##` 章节。
- `top_level_items` 顺序必须遵循逐字稿讲授顺序，并能从 `ordered_blocks` 推导出来。
- 如果课程围绕教材单元逐一解读，`top_level_items` 优先使用教材单元和独立综合实践活动。
- 短暂总述、过渡、跨单元关联说明、整体结构说明，不要放入 `top_level_items`；在 `merge_policy` 中说明它们应并入哪个相邻单元。
- 对讲解结构相似的连续单元，在 `parallel_groups` 中列出这些单元名称，供后续生成平行 `###` 目录。
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
- 不要根据后续 granularity、字数阈值或填充便利性决定是否增删顶级章节；这里只处理 policy 并集。
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

def call_skeleton_pass(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    policy: OutlinePolicy | None = None,
    granularity_plan: GranularityPlan | None = None,
) -> str:
    user_prompt = build_skeleton_prompt(prompt_template, transcript, policy, granularity_plan)
    result = call_chat(
        client,
        user_prompt=user_prompt,
        max_tokens=env_int("OUTLINE_SKELETON_MAX_TOKENS", 8192),
        max_continuations=env_int("OUTLINE_SKELETON_MAX_CONTINUATIONS", 3),
    )
    skeleton = normalize_anchored_skeleton(result.content)

    if result.continuations:
        print(f"Pass 1 continuation requests={result.continuations}")
    return skeleton

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

This is Pass 1 anchor repair. Only repair top-level chapters and anchors.

Previous validation error:
{validation_error}

Hard requirements:
- Output only a complete anchored Markdown skeleton. Do not add explanations.
- The final `##` count, order, and meaning must match expected_policy_items exactly.
- Each `##` must be followed immediately by one `outline-anchor` HTML comment.
- Each anchor `start_quote` must use the exact value from expected_policy_items.
- You may add missing `##` headings, but do not add any `##` outside expected_policy_items.
- Do not fill transcript body text.

expected_policy_items:
```json
{json.dumps(expected_items, ensure_ascii=False, indent=2)}
```

Current anchored skeleton:
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

def call_granularity_repair_pass(
    client: OpenAI,
    prompt_template: str,
    anchored_skeleton: str,
    granularity_plan: GranularityPlan,
    failures: list[dict[str, object]],
) -> str:
    user_prompt = f"""{prompt_template.strip()}

---

This is Pass 1 granularity repair. Only add necessary `###` headings inside failed chapters.

Hard requirements:
- Output only a complete anchored Markdown skeleton. Do not add explanations.
- Do not modify `#`, any `##` heading, `##` order, or any `outline-anchor` comment.
- Only add the necessary direct `###` headings for chapters listed in failed_chapters.
- Each failed chapter must have at least `min_subsections` direct `###` headings.
- Do not add, delete, merge, split, or reorder `##` headings.
- Do not fill transcript body text.

failed_chapters:
```json
{json.dumps(failures, ensure_ascii=False, indent=2)}
```

Full granularity plan:
```json
{json.dumps(granularity_plan, ensure_ascii=False, indent=2)}
```

Current anchored skeleton:
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

def call_skeleton_merge_pass(
    client: OpenAI,
    sub_skeletons: list[str],
    policy: OutlinePolicy | None = None,
    granularity_plan: GranularityPlan | None = None,
) -> str:
    combined = "\n\n---\n\n".join(
        f"子骨架 {index + 1}：\n\n{skeleton.strip()}"
        for index, skeleton in enumerate(sub_skeletons)
    )
    policy_block = ""
    if policy:
        policy_block = f"""

顶级章节策略（必须遵守）：
```json
{format_outline_policy(policy)}
```

合并后的最终 `##` 必须与 `top_level_items` 数量、顺序和含义一致，不要新增、删除、重排或拆分 `##`。
"""
    granularity_block = ""
    if granularity_plan:
        granularity_block = f"""

章节细分计划（必须遵守）：
```json
{format_granularity_plan(granularity_plan)}
```

每个 `##` 下的 `###` 数量必须不少于对应 `min_subsections`。`min_subsections=0` 的短章节可以不拆分。
"""
    user_prompt = f"""这是 Pass 1 合并：把多个按原文顺序生成的课程大纲子骨架合并为一个最终骨架。

硬性要求：
- 只输出 Markdown 大纲结构，不填充任何逐字稿原文。
- 使用 `#` 输出唯一课程主题。
- 使用 `##` 输出顶级章节；默认后续层级使用 `###`，确有必要时可用 `####`，最多四级。
- 每个 `##` 标题下一行必须紧跟一个 HTML 注释锚点：`<!-- outline-anchor: {{"chapter_id": 1, "start_quote": "从逐字稿逐字复制的该章开头短句"}} -->`。
- `chapter_id` 必须等于最终 `##` 的 1-based 顺序；`start_quote` 必须逐字复制自该章实际开始处的逐字稿。
- 不得输出 `#####` 或更深层级；例题、步骤或条目优先合并进相邻 `###` 标题，只有长章节内部需要稳定路牌时才使用 `####`。
- 如果课程围绕教材单元展开，最终 `##` 优先对应教材单元或独立综合实践活动。
- 不要把短暂总述、过渡、跨单元关联说明单独升成 `##`；把它并入紧随其后的第一个真实教材单元下的 `###`。
- 连续相似单元应使用平行的 `###` 目录结构，不要把某个单元的“解决问题”“整理复习”等局部内容抽成独立 `##`。
- 保持子骨架出现的原始顺序。
- 只允许删除或合并重复章节、统一层级和清理重复标题。
- 不允许新增未出现在子骨架中的章节。
- 不要输出说明、分析或代码围栏。
{policy_block}
{granularity_block}

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
    return normalize_anchored_skeleton(result.content)

def call_skeleton_pass_chunked(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    chunk_size: int,
    overlap: int,
    policy: OutlinePolicy | None = None,
    granularity_plan: GranularityPlan | None = None,
) -> str:
    chunks = split_transcript_chunks(transcript, chunk_size, overlap)
    sub_skeletons: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        print(f"Pass 1: generating chunk skeleton {index}/{len(chunks)} chars={len(chunk)}")
        sub_skeletons.append(
            call_skeleton_pass(client, prompt_template, chunk, policy, granularity_plan)
        )
    print(f"Pass 1: merging {len(sub_skeletons)} chunk skeletons...")
    return call_skeleton_merge_pass(client, sub_skeletons, policy, granularity_plan)

def generate_skeleton_from_policy(
    client: OpenAI,
    prompt_template: str,
    transcript: str,
    course_title: str | None,
    policy: OutlinePolicy,
    granularity_plan: GranularityPlan | None,
    char_count: int,
) -> str:
    chunk_threshold = env_int("OUTLINE_SKELETON_CHUNK_THRESHOLD", 30000)
    if char_count > chunk_threshold:
        skeleton = call_skeleton_pass_chunked(
            client,
            prompt_template,
            transcript,
            env_int("OUTLINE_SKELETON_CHUNK_CHARS", 15000),
            env_int("OUTLINE_SKELETON_CHUNK_OVERLAP", 500),
            policy,
            granularity_plan,
        )
    else:
        skeleton = call_skeleton_pass(client, prompt_template, transcript, policy, granularity_plan)
    return apply_course_title(skeleton, course_title)

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
        anchored_skeleton = repair_skeleton_anchors_from_policy(anchored_skeleton, policy)
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

        anchor_llm_attempts = 0
        max_anchor_repairs = max(0, env_int("OUTLINE_ANCHOR_REPAIR_MAX_ATTEMPTS", 2))
        while not anchor_ok and anchor_llm_attempts < max_anchor_repairs:
            anchored_skeleton = call_anchor_repair_pass(
                client,
                prompt_template,
                anchored_skeleton,
                policy,
                str(retry_report["anchor_error"]),
            )
            anchor_llm_attempts += 1
            retry_report["anchor_repair_count"] = int(retry_report["anchor_repair_count"]) + 1
            try:
                anchored_skeleton = repair_skeleton_anchors_from_policy(anchored_skeleton, policy)
                validate_skeleton_anchors_against_policy(anchored_skeleton, transcript, policy)
                anchor_ok = True
            except RuntimeError as anchor_retry_exc:
                retry_report["anchor_error"] = str(anchor_retry_exc)

        if not anchor_ok:
            fail("ANCHOR_FAIL", f"Skeleton anchor repair failed: {retry_report['anchor_error']}")

    _, _, locations = parse_skeleton_anchor_locations(anchored_skeleton, transcript)
    clean_skeleton = strip_skeleton_anchors(anchored_skeleton)
    granularity_plan = build_granularity_plan_from_locations(
        transcript,
        parse_chapters(clean_skeleton)[1],
        locations,
    )

    max_granularity_repairs = max(0, env_int("OUTLINE_GRANULARITY_REPAIR_MAX_ATTEMPTS", 2))
    while True:
        _, chapters = parse_chapters(strip_skeleton_anchors(anchored_skeleton))
        failures = collect_granularity_failures(chapters, granularity_plan)
        if not failures:
            break
        if not retry_report["granularity_repair_chapters"]:
            retry_report["granularity_repair_chapters"] = failures
        if int(retry_report["granularity_repair_count"]) >= max_granularity_repairs:
            fail(
                "GRANULARITY_FAIL",
                "Skeleton granularity repair failed after "
                f"{max_granularity_repairs} attempt(s): {failures}",
            )
        anchored_skeleton = call_granularity_repair_pass(
            client,
            prompt_template,
            anchored_skeleton,
            granularity_plan,
            failures,
        )
        retry_report["granularity_repair_count"] = (
            int(retry_report["granularity_repair_count"]) + 1
        )
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
- 保持当前章节大纲层级，不新增标题，不得使用 `#####` 或更深层级。
- 如果当前章节大纲子树没有 `####`，输出时也不要新增 `####`；标题只是稀疏路牌，不是细颗粒摘要。
- 逐字稿原文是主体，保持连续流动，不加 `>` 引用块。
- 将当前章节大纲子树的各级标题，作为路牌按讲授顺序插入原文对应位置。
- 标题插在对应内容开始之前，原文紧跟在标题后自然流动。
- 不要新增当前章节大纲子树中不存在的标题层级；如果子树没有 `####`，不得自行添加 `####`。
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
