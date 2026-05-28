from __future__ import annotations

import json
import re
from pathlib import Path

from outline_io import outline_policy_path
from outline_models import OutlinePolicy
from outline_text import strip_markdown_fence


def parse_json_object(text: str, *, label: str) -> dict[str, object]:
    json_text = strip_markdown_fence(text)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        preview = json_text[:500].replace("\n", "\\n")
        raise RuntimeError(f"Invalid {label} JSON: {preview}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} response must be a JSON object.")
    return payload

def normalize_policy_items(items: object) -> list[str]:
    if not isinstance(items, list):
        raise RuntimeError("outline_policy.top_level_items must be a list.")
    normalized = [str(item).strip().lstrip("#").strip() for item in items]
    normalized = [item for item in normalized if item]
    if not normalized:
        raise RuntimeError("outline_policy.top_level_items must not be empty.")
    keys = [normalize_policy_heading_key(item) for item in normalized]
    if len(set(keys)) != len(keys):
        raise RuntimeError("outline_policy.top_level_items must not contain duplicates.")
    return normalized

def normalize_parallel_groups(groups: object) -> list[list[str]]:
    if groups in (None, ""):
        return []
    if not isinstance(groups, list):
        raise RuntimeError("outline_policy.parallel_groups must be a list.")
    normalized: list[list[str]] = []
    for group in groups:
        if not isinstance(group, list):
            raise RuntimeError("Each outline_policy.parallel_groups item must be a list.")
        items = [str(item).strip() for item in group if str(item).strip()]
        if len(items) >= 2:
            normalized.append(items)
    return normalized

def normalize_candidate_items(items: object, default: list[str]) -> list[str]:
    if items in (None, ""):
        return list(default)
    return normalize_policy_items(items)

def normalize_ordered_blocks(blocks: object) -> list[dict[str, object]]:
    if blocks in (None, ""):
        return []
    if not isinstance(blocks, list):
        raise RuntimeError("outline_policy.ordered_blocks must be a list.")

    normalized: list[dict[str, object]] = []
    for index, block in enumerate(blocks, start=1):
        if not isinstance(block, dict):
            raise RuntimeError("Each outline_policy.ordered_blocks item must be a JSON object.")
        block_id = str(block.get("block_id") or f"B{index:02d}").strip()
        title = str(block.get("title") or "").strip()
        scope_summary = str(block.get("scope_summary") or "").strip()
        role = str(block.get("role") or "").strip()
        start_quote = str(block.get("start_quote") or "").strip()
        candidate_top_level = block.get("candidate_top_level", False)
        if isinstance(candidate_top_level, str):
            candidate_top_level = candidate_top_level.strip().lower() in {"1", "true", "yes", "是"}
        normalized.append(
            {
                "block_id": block_id,
                "title": title,
                "scope_summary": scope_summary,
                "role": role,
                "start_quote": start_quote,
                "candidate_top_level": bool(candidate_top_level),
            }
        )
    return normalized

def normalize_record_list(records: object, field_name: str) -> list[dict[str, object]]:
    if records in (None, ""):
        return []
    if not isinstance(records, list):
        raise RuntimeError(f"outline_policy.{field_name} must be a list.")
    normalized: list[dict[str, object]] = []
    for item in records:
        if not isinstance(item, dict):
            raise RuntimeError(f"Each outline_policy.{field_name} item must be a JSON object.")
        normalized.append(dict(item))
    return normalized

def normalize_outline_policy(payload: dict[str, object]) -> OutlinePolicy:
    top_level_items = normalize_policy_items(payload.get("top_level_items"))
    return {
        "course_structure_summary": str(payload.get("course_structure_summary") or "").strip(),
        "ordered_blocks": normalize_ordered_blocks(payload.get("ordered_blocks")),
        "top_level_basis": str(payload.get("top_level_basis") or "course_structure").strip(),
        "top_level_items": top_level_items,
        "candidate_top_level_items": normalize_candidate_items(
            payload.get("candidate_top_level_items"),
            top_level_items,
        ),
        "merge_policy": str(payload.get("merge_policy") or "").strip(),
        "parallel_groups": normalize_parallel_groups(payload.get("parallel_groups")),
        "ordering_basis": str(payload.get("ordering_basis") or "").strip(),
        "merge_trace": normalize_record_list(payload.get("merge_trace"), "merge_trace"),
        "dropped_or_merged_items": normalize_record_list(
            payload.get("dropped_or_merged_items"),
            "dropped_or_merged_items",
        ),
    }

def parse_outline_policy(text: str) -> OutlinePolicy:
    return normalize_outline_policy(parse_json_object(text, label="outline policy"))

def read_outline_policy(path: Path) -> OutlinePolicy:
    return parse_outline_policy(path.read_text(encoding="utf-8-sig"))

def write_outline_policy(out: Path, policy: OutlinePolicy) -> None:
    outline_policy_path(out).write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

def format_outline_policy(policy: OutlinePolicy) -> str:
    return json.dumps(policy, ensure_ascii=False, indent=2)

def policy_item_keys(policy: OutlinePolicy) -> list[str]:
    keys: list[str] = []
    for item in policy.get("top_level_items", []):
        key = normalize_policy_heading_key(str(item))
        if key:
            keys.append(key)
    return keys

def find_direct_policy_cover(policies: list[OutlinePolicy]) -> tuple[int, OutlinePolicy] | None:
    if len(policies) < 2:
        return None

    key_sets = [set(policy_item_keys(policy)) for policy in policies]
    for candidate_index, candidate_keys in enumerate(key_sets):
        if not candidate_keys:
            continue
        covers_all = all(other_keys <= candidate_keys for other_keys in key_sets)
        strictly_covers_one = any(other_keys < candidate_keys for other_keys in key_sets)
        if covers_all and strictly_covers_one:
            return candidate_index, policies[candidate_index]
    return None

def normalize_policy_heading_key(title: str) -> str:
    text = str(title).strip().lstrip("#").strip()
    text = re.sub(r"^[一二三四五六七八九十百]+[、.．]\s*", "", text)
    text = re.sub(r"^\d+(?:\.\d+)*[、.．]?\s*", "", text)
    return re.sub(r"[\s:：,，.．、;；()（）《》<>“”\"'`·\-—_]+", "", text).lower()
