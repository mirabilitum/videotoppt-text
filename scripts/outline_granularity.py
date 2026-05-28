from __future__ import annotations

import json

from outline_locations import validate_chapter_locations
from outline_models import ChapterLocation, GranularityPlan, OutlinePolicy
from outline_skeleton import parse_skeleton_anchor_locations
from outline_text import chapter_heading, env_int


def min_subsections_for_chars(char_count: int) -> int:
    if char_count < env_int("OUTLINE_GRANULARITY_SOFT_MIN_CHARS", 1000):
        return 0
    if char_count < env_int("OUTLINE_GRANULARITY_STRONG_MIN_CHARS", 2000):
        return 2
    return 3

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
                "start": location.start,
                "start_quote": location.start_quote,
            }
        )
    return plan

def build_policy_granularity_plan(transcript: str, policy: OutlinePolicy) -> GranularityPlan:
    # Backward-compatible shim; the live outline flow now derives granularity from skeletons.
    titles = [str(item).strip() for item in policy.get("top_level_items", []) if str(item).strip()]
    pseudo_chapters = [(index + 1, f"## {title}") for index, title in enumerate(titles)]
    if not pseudo_chapters:
        return []
    segment = max(1, len(transcript) // len(pseudo_chapters))
    plan: GranularityPlan = []
    start = 0
    for index, (_, chapter_subtree) in enumerate(pseudo_chapters):
        end = len(transcript) if index + 1 == len(pseudo_chapters) else min(len(transcript), start + segment)
        source_chars = max(0, end - start)
        plan.append(
            {
                "top_level_item": chapter_heading(chapter_subtree),
                "source_chars": source_chars,
                "min_subsections": min_subsections_for_chars(source_chars),
                "max_depth": 4,
                "location_source": "policy_estimate",
            }
        )
        start = end
    return plan

def build_granularity_plan_from_skeleton(
    transcript: str,
    anchored_skeleton: str,
) -> tuple[GranularityPlan, list[tuple[int, str]]]:
    _, chapters, locations = parse_skeleton_anchor_locations(anchored_skeleton, transcript)
    return build_granularity_plan_from_locations(transcript, chapters, locations), chapters

def format_granularity_plan(plan: GranularityPlan) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2)
