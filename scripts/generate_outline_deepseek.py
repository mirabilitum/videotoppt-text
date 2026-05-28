from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from openai import OpenAI

from common import load_config, output_dir
from outline_io import (
    DEFAULT_PROMPT_PATH,
    file_sha256,
    outline_complete,
    outline_inputs_match,
    outline_policy_path,
    outline_prompt_path,
    outline_source_path,
    outline_source_payload,
    outline_source_policy_matches,
    read_course_title,
    select_transcript_source,
    sha256_text,
    write_outline_source,
)
from outline_granularity import (
    build_granularity_plan_from_locations,
    build_granularity_plan_from_skeleton,
    build_policy_granularity_plan,
    format_granularity_plan,
    min_subsections_for_chars,
)
from outline_experiment import (
    run_skeleton_only_experiment,
    write_skeleton_experiment_manifest,
)
from outline_locations import (
    find_anchor_quote_start,
    find_heading_start,
    find_quote_start,
    format_chapter_locations,
    heading_search_terms,
    parse_chapter_locations,
    read_legacy_chapter_locations,
    slice_chapter_transcripts,
    validate_anchor_chapter_locations,
    validate_chapter_locations,
    validate_final_chapter_locations,
)
from outline_models import (
    ChatResult,
    ChapterLocation,
    GranularityPlan,
    OutlinePolicy,
    SkeletonGenerationResult,
    TranscriptSource,
)
from outline_llm import (
    call_chat,
    call_fill_chapter,
    call_intro_pass,
    call_outline_policy_pass,
    call_skeleton_merge_pass,
    call_skeleton_pass,
    call_skeleton_pass_chunked,
    generate_skeleton_from_policy,
    generate_skeleton_with_granularity,
    merge_outline_policy_runs,
    split_transcript_chunks,
)
from outline_text import (
    HEADING_RE,
    ROOT_HEADING_RE,
    chapter_heading,
    compact_text,
    env_float,
    env_int,
    find_first,
    normalize_with_index,
    strip_heading_number,
    strip_markdown_fence,
)
from outline_policy import (
    format_outline_policy,
    normalize_outline_policy,
    normalize_parallel_groups,
    normalize_policy_heading_key,
    normalize_policy_items,
    parse_json_object,
    parse_outline_policy,
    read_outline_policy,
    write_outline_policy,
)
from outline_skeleton import (
    ANCHOR_RE,
    apply_course_title,
    attach_skeleton_anchors,
    build_skeleton_prompt,
    cap_heading_depths,
    normalize_anchored_skeleton,
    normalize_skeleton,
    parse_chapters,
    parse_skeleton_anchor_locations,
    strip_skeleton_anchors,
    validate_skeleton_matches_granularity,
    validate_skeleton_matches_policy,
)
from text_filter import (
    decrypt_text,
    encrypt_text,
    load_sensitive_word_map,
)

MODEL_DEFAULT = "deepseek-chat"
SYSTEM_PROMPT = "你是一名专业的课程内容分析师，擅长从课程转写文本中提取结构化大纲。"
TRUNCATED_FINISH_REASONS = {"length", "max_tokens"}


def clip_intro_to_first_chapter(intro: str, transcript: str, first_chapter_start: int) -> str:
    if first_chapter_start <= 0:
        return intro.strip()

    expected_intro = transcript[:first_chapter_start].strip()
    if not expected_intro:
        return ""

    if not intro.strip() or len(compact_text(intro)) > len(compact_text(expected_intro)):
        return expected_intro

    return intro.strip()


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
    parser.add_argument(
        "--transcript-source",
        choices=("auto", "clean", "raw"),
        default="auto",
        help="Transcript input source. auto prefers transcript_clean.txt when present.",
    )
    parser.add_argument(
        "--skeleton-only",
        action="store_true",
        help="Stop after Pass 0 and Pass 1 for policy/skeleton stability experiments.",
    )
    parser.add_argument(
        "--policy-runs",
        type=int,
        default=2,
        help="Generate this many independent policies before canonical policy merge.",
    )
    parser.add_argument(
        "--skeleton-runs",
        type=int,
        default=1,
        help="With --skeleton-only, generate this many skeletons per policy.",
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
    transcript_source = select_transcript_source(out, preferred=args.transcript_source)
    reuse_existing = args.resume and outline_inputs_match(out, transcript_source, prompt_path)
    if args.resume and not reuse_existing:
        print("resume_inputs_changed=true")

    prompt_template = prompt_path.read_text(encoding="utf-8")
    transcript = strip_part_markers(transcript_source.text)
    course_title = read_course_title(out)
    char_count = len(transcript)
    print(f"transcript_source={transcript_source.name}")
    print(f"transcript_path={transcript_source.path}")
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

    if args.skeleton_only:
        run_skeleton_only_experiment(
            client=client,
            prompt_template=prompt_template,
            transcript=transcript,
            course_title=course_title,
            out=out,
            policy_runs=args.policy_runs,
            skeleton_runs=args.skeleton_runs,
        )
        return

    policy_path = outline_policy_path(out)
    if reuse_existing and outline_source_policy_matches(out, policy_path):
        print(f"Pass 0: reusing outline policy {policy_path}")
        policy = read_outline_policy(policy_path)
    else:
        if args.resume and reuse_existing:
            print("resume_policy_changed=true")
        if args.policy_runs < 1:
            raise ValueError("--policy-runs must be at least 1.")
        policy_runs: list[OutlinePolicy] = []
        for policy_index in range(1, args.policy_runs + 1):
            print(f"Pass 0: generating outline policy {policy_index}/{args.policy_runs}...")
            policy_run = call_outline_policy_pass(client, prompt_template, transcript)
            policy_runs.append(policy_run)
            if args.policy_runs > 1:
                (out / f"outline_policy_run_{policy_index:02d}.json").write_text(
                    json.dumps(policy_run, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        print("Pass 0: merging outline policies...")
        policy_merge = merge_outline_policy_runs(client, prompt_template, transcript, policy_runs)
        policy = policy_merge.policy
        if args.policy_runs > 1:
            canonical_policy_path = out / "outline_policy_canonical.json"
            canonical_policy_path.write_text(
                format_outline_policy(policy) + "\n",
                encoding="utf-8",
            )
        print(f"Pass 0: canonical policy reason={policy_merge.reason}")
        write_outline_policy(out, policy)
        reuse_existing = False
    granularity_path = out / "outline_granularity.json"

    skeleton_path = out / "outline_skeleton.md"
    anchored_skeleton_path = out / "outline_skeleton_anchored.md"
    locations_path = out / "outline_locations.json"
    legacy_locations = False
    if reuse_existing and anchored_skeleton_path.exists():
        print(f"Pass 1: reusing anchored outline skeleton {anchored_skeleton_path}")
        anchored_skeleton = anchored_skeleton_path.read_text(encoding="utf-8").strip()
        titled_anchored_skeleton = apply_course_title(anchored_skeleton, course_title)
        if titled_anchored_skeleton != anchored_skeleton:
            anchored_skeleton = titled_anchored_skeleton
            anchored_skeleton_path.write_text(anchored_skeleton + "\n", encoding="utf-8")
        skeleton = strip_skeleton_anchors(anchored_skeleton)
        _, chapters, locations = parse_skeleton_anchor_locations(anchored_skeleton, transcript)
        granularity_plan = build_granularity_plan_from_locations(transcript, chapters, locations)
        skeleton_path.write_text(skeleton + "\n", encoding="utf-8")
    elif reuse_existing and skeleton_path.exists() and locations_path.exists():
        print(f"Pass 1: reusing legacy outline skeleton {skeleton_path}")
        skeleton = skeleton_path.read_text(encoding="utf-8").strip()
        titled_skeleton = apply_course_title(skeleton, course_title)
        if titled_skeleton != skeleton:
            skeleton = titled_skeleton
            skeleton_path.write_text(skeleton + "\n", encoding="utf-8")
        _, chapters = parse_chapters(skeleton)
        locations = read_legacy_chapter_locations(locations_path, transcript, chapters)
        validate_final_chapter_locations(locations, len(transcript))
        granularity_plan = build_granularity_plan_from_locations(transcript, chapters, locations)
        legacy_locations = True
    elif reuse_existing and skeleton_path.exists():
        raise RuntimeError(
            "Cannot resume from a clean outline_skeleton.md without "
            "outline_skeleton_anchored.md or valid outline_locations.json. "
            "Regenerate the skeleton to create anchors."
        )
    else:
        print("Pass 1: generating outline skeleton...")
        skeleton_result = generate_skeleton_with_granularity(
            client,
            prompt_template,
            transcript,
            course_title,
            policy,
            char_count,
        )
        skeleton = skeleton_result.skeleton
        anchored_skeleton = skeleton_result.anchored_skeleton
        granularity_plan = skeleton_result.granularity_plan
        locations = skeleton_result.locations
        anchored_skeleton_path.write_text(anchored_skeleton + "\n", encoding="utf-8")
        skeleton_path.write_text(skeleton + "\n", encoding="utf-8")
    granularity_path.write_text(format_granularity_plan(granularity_plan) + "\n", encoding="utf-8")

    title, chapters = parse_chapters(skeleton)
    if not chapters:
        raise RuntimeError(f"No top-level chapters found in skeleton: {skeleton_path}")
    validate_skeleton_matches_policy(chapters, policy)
    validate_skeleton_matches_granularity(chapters, granularity_plan)

    print(f"outline_skeleton.md={skeleton_path}")
    print(f"outline_granularity.json={granularity_path}")
    print(f"chapters={len(chapters)}")

    if legacy_locations:
        validate_final_chapter_locations(locations, len(transcript))
    else:
        validate_anchor_chapter_locations(locations, len(transcript))
    locations_path.write_text(format_chapter_locations(locations) + "\n", encoding="utf-8")
    chapter_transcripts = slice_chapter_transcripts(transcript, chapters, locations)

    first_heading = chapters[0][1].splitlines()[0] if chapters[0][1].splitlines() else ""
    intro_path = out / "outline_intro.md"
    if reuse_existing and intro_path.exists():
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
            reuse_existing
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
    write_outline_source(
        out,
        transcript_source,
        prompt_path,
        model=os.getenv("DEEPSEEK_MODEL", MODEL_DEFAULT),
        policy_path=policy_path,
    )

    print(f"prompt={prompt_path}")
    print(f"outline_policy.json={policy_path}")
    print(f"outline_source.json={outline_source_path(out)}")
    print(f"outline_locations.json={locations_path}")
    print(f"outline_intro.md={intro_path}")
    print(f"outline.md={outline_path}")
    print(f"chars={len(outline)}")


if __name__ == "__main__":
    main()
