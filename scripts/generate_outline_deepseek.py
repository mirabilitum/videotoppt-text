from __future__ import annotations

import json
import os
import re
from bisect import bisect_left
from pathlib import Path

from openai import OpenAI

from common import load_config, output_dir
from outline_io import (
    DEFAULT_PROMPT_PATH,
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
from outline_models import (
    ChapterLocation,
    OutlinePolicy,
)
from outline_llm import (
    call_chat,
    call_fill_chapter_draft,
    call_fill_chapter_merge,
    call_intro_pass,
    call_outline_policy_pass,
    merge_outline_policy_runs,
)
from outline_text import (
    env_float,
    env_int,
    normalize_with_index,
    strip_markdown_fence,
)
from outline_policy import (
    format_outline_policy,
    read_outline_policy,
    write_outline_policy,
)
from text_filter import (
    decrypt_text,
    encrypt_text,
    load_sensitive_word_map,
)

MODEL_DEFAULT = "deepseek-chat"


def strip_part_markers(transcript: str) -> str:
    return re.sub(r"\[Part \d+\]\s*", "", transcript)


def clip_intro_to_first_chapter(intro: str, transcript: str, first_chapter_start: int) -> str:
    if first_chapter_start <= 0:
        return intro.strip()
    expected_intro = transcript[:first_chapter_start].strip()
    if not expected_intro:
        return ""
    if not intro.strip():
        return expected_intro
    return intro.strip()


def merge_outline(title: str, intro: str, filled_chapters: list[str]) -> str:
    clean_title = title.strip()
    clean_intro = intro.strip()
    clean_chapters = [c.strip() for c in filled_chapters if c.strip()]
    parts = [clean_title]
    if clean_intro:
        parts.append(clean_intro)
    parts.extend(clean_chapters)
    return "\n\n".join(parts) + "\n"


def find_quote_start(text: str, quote: str, start: int = 0) -> int:
    exact = text.find(quote, start)
    if exact >= 0:
        return exact
    normalized_text, index_map = normalize_with_index(text)
    normalized_quote, _ = normalize_with_index(quote)
    if not normalized_quote or not index_map:
        return -1
    normalized_start = bisect_left(index_map, start)
    pos = normalized_text.find(normalized_quote, normalized_start)
    if pos < 0:
        return -1
    return index_map[pos]


def locate_chapters(
    transcript: str,
    policy: OutlinePolicy,
) -> list[ChapterLocation]:
    candidate_blocks = [
        b for b in policy.get("ordered_blocks", [])
        if isinstance(b, dict) and bool(b.get("candidate_top_level"))
    ]
    top_level_items = [
        str(item).strip()
        for item in policy.get("top_level_items", [])
        if str(item).strip()
    ]
    n = len(top_level_items)
    blocks = candidate_blocks[:n]

    locations: list[ChapterLocation] = []
    search_from = 0
    for i, (title, block) in enumerate(zip(top_level_items, blocks)):
        chapter_id = i + 1
        start_quote = str(block.get("start_quote") or "").strip()
        if not start_quote:
            raise RuntimeError(
                f"Missing start_quote for chapter {chapter_id}: {title!r}"
            )
        pos = find_quote_start(transcript, start_quote, search_from)
        if pos < 0:
            raise RuntimeError(
                f"start_quote for chapter {chapter_id} not found after previous chapter: "
                f"{start_quote!r}"
            )
        locations.append(ChapterLocation(
            chapter_id=chapter_id,
            heading=title,
            start_quote=start_quote,
            start=pos,
            source="policy",
        ))
        search_from = pos + len(start_quote)
    return locations


def slice_chapter_transcripts(
    transcript: str,
    locations: list[ChapterLocation],
) -> dict[int, str]:
    result: dict[int, str] = {}
    for i, loc in enumerate(locations):
        end = locations[i + 1].start if i + 1 < len(locations) else len(transcript)
        result[loc.chapter_id] = transcript[loc.start:end]
    return result


def format_chapter_locations(locations: list[ChapterLocation]) -> str:
    return json.dumps(
        [
            {
                "chapter_id": loc.chapter_id,
                "heading": loc.heading,
                "start_quote": loc.start_quote,
                "start": loc.start,
                "source": loc.source,
            }
            for loc in locations
        ],
        ensure_ascii=False,
        indent=2,
    )


def parse_args():
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate a structured course outline from transcript.txt."
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing chapter files and intro where inputs match.",
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
        "--policy-runs",
        type=int,
        default=2,
        help="Generate this many independent policies before canonical policy merge.",
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

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=env_float("DEEPSEEK_TIMEOUT", 180.0),
        max_retries=env_int("DEEPSEEK_MAX_RETRIES", 2),
    )

    # Pass 0: policy
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
            (out / "outline_policy_canonical.json").write_text(
                format_outline_policy(policy) + "\n",
                encoding="utf-8",
            )
        print(f"Pass 0: canonical policy reason={policy_merge.reason}")
        write_outline_policy(out, policy)
        reuse_existing = False

    # locate chapters from policy
    locations = locate_chapters(transcript, policy)
    locations_path = out / "outline_locations.json"
    locations_path.write_text(format_chapter_locations(locations) + "\n", encoding="utf-8")
    chapter_transcripts = slice_chapter_transcripts(transcript, locations)

    top_level_items = [
        str(item).strip()
        for item in policy.get("top_level_items", [])
        if str(item).strip()
    ]
    course_title_heading = f"# {course_title}" if course_title else "# 课程大纲"
    first_heading = f"## {top_level_items[0]}" if top_level_items else ""

    # Pass 1.5: intro
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

    # build chapter map for context
    candidate_blocks = [
        b for b in policy.get("ordered_blocks", [])
        if isinstance(b, dict) and bool(b.get("candidate_top_level"))
    ]
    n = len(top_level_items)
    chapter_map = [
        {
            "block_id": str(b.get("block_id", f"C{i+1:02d}")),
            "title": str(top_level_items[i]),
            "scope_summary": str(b.get("scope_summary", "")),
        }
        for i, b in enumerate(candidate_blocks[:n])
    ]
    course_structure_summary = str(policy.get("course_structure_summary", ""))

    # Pass 2a + 2b: fill chapters
    chapters_dir = out / "outline_chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    filled_chapters: list[str] = []

    for loc in locations:
        chapter_id = loc.chapter_id
        heading = f"## {loc.heading}"
        chapter_path = chapters_dir / f"outline_chapter_{chapter_id:03d}.md"
        draft_path = chapters_dir / f"outline_chapter_{chapter_id:03d}_draft.md"
        should_reuse = (
            reuse_existing
            and chapter_path.exists()
            and chapter_path.stat().st_size > 0
            and (args.rerun_from <= 0 or chapter_id < args.rerun_from)
        )
        if should_reuse:
            print(f"Pass 2: reusing chapter {chapter_id}/{len(locations)} {heading}")
            filled_chapters.append(chapter_path.read_text(encoding="utf-8").strip())
            continue

        print(f"Pass 2a: drafting chapter {chapter_id}/{len(locations)} {heading}")
        draft = call_fill_chapter_draft(
            client=client,
            prompt_template=prompt_template,
            chapter_id=chapter_id,
            chapter_count=len(locations),
            chapter_title=heading,
            chapter_transcript=chapter_transcripts[chapter_id],
            course_structure_summary=course_structure_summary,
            chapter_map=chapter_map,
        )
        draft_path.write_text(draft + "\n", encoding="utf-8")

        print(f"Pass 2b: merging chapter {chapter_id}/{len(locations)} {heading}")
        filled = call_fill_chapter_merge(
            client=client,
            prompt_template=prompt_template,
            chapter_id=chapter_id,
            chapter_count=len(locations),
            draft=draft,
        )
        chapter_path.write_text(filled + "\n", encoding="utf-8")
        filled_chapters.append(filled)

    outline = merge_outline(course_title_heading, intro, filled_chapters)
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
