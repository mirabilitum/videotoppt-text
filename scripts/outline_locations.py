from __future__ import annotations

import json
import re
from bisect import bisect_left

from outline_models import ChapterLocation
from outline_text import chapter_heading, find_first, normalize_with_index, strip_heading_number, strip_markdown_fence
from text_filter import adjust_span_to_alias_boundary, assert_no_alias_fragments, load_sensitive_word_map


def find_anchor_quote_start(text: str, quote: str, start: int = 0) -> int:
    exact = text.find(quote, start)
    if exact >= 0:
        return exact

    normalized_text, index_map = normalize_with_index(text)
    normalized_quote, _ = normalize_with_index(quote)
    if not normalized_quote or not index_map:
        return -1

    normalized_start = bisect_left(index_map, start)
    normalized_exact = normalized_text.find(normalized_quote, normalized_start)
    return index_map[normalized_exact] if normalized_exact >= 0 else -1

def find_unique_anchor_quote_start(text: str, quote: str, start: int = 0) -> int:
    exact = text.find(quote, start)
    if exact >= 0:
        next_exact = text.find(quote, exact + len(quote))
        if next_exact >= 0:
            return -2
        return exact

    normalized_text, index_map = normalize_with_index(text)
    normalized_quote, _ = normalize_with_index(quote)
    if not normalized_quote or not index_map:
        return -1

    normalized_start = bisect_left(index_map, start)
    normalized_exact = normalized_text.find(normalized_quote, normalized_start)
    if normalized_exact < 0:
        return -1
    normalized_next = normalized_text.find(
        normalized_quote,
        normalized_exact + len(normalized_quote),
    )
    if normalized_next >= 0:
        return -2
    return index_map[normalized_exact]

def validate_chapter_locations(locations: list[ChapterLocation], transcript_len: int) -> None:
    if not locations:
        raise RuntimeError("Chapter locations must not be empty.")
    previous_start = -1
    seen_ids: set[int] = set()
    for location in locations:
        if location.chapter_id in seen_ids:
            raise RuntimeError(f"Duplicate chapter location ID: {location.chapter_id}")
        seen_ids.add(location.chapter_id)
        if location.start < 0 or location.start >= transcript_len:
            raise RuntimeError(
                f"Chapter {location.chapter_id} start is outside transcript bounds: "
                f"{location.start}"
            )
        if location.start <= previous_start:
            raise RuntimeError(
                "Chapter location starts must be strictly increasing: "
                f"chapter {location.chapter_id} start={location.start}, "
                f"previous_start={previous_start}"
            )
        previous_start = location.start

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
    validate_chapter_locations(locations, len(transcript))
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

def validate_anchor_chapter_locations(locations: list[ChapterLocation], transcript_len: int) -> None:
    validate_chapter_locations(locations, transcript_len)
    invalid = [
        location.chapter_id
        for location in locations
        if location.source != "skeleton_anchor"
    ]
    if invalid:
        raise RuntimeError(
            "New outline generation requires skeleton_anchor chapter locations: "
            f"{invalid}"
        )

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

    expected_ids = [chapter_id for chapter_id, _ in chapters]
    missing = [chapter_id for chapter_id in expected_ids if chapter_id not in by_id]
    if missing:
        raise RuntimeError(f"Chapter location response missing IDs: {missing}")

    locations: list[ChapterLocation] = []
    search_from = 0
    for chapter_id, chapter_subtree in chapters:
        start_quote = by_id[chapter_id]["start_quote"]
        source = by_id[chapter_id]["source"]
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
                source=source,
            )
        )
        search_from = start + len(start_quote)

    validate_chapter_locations(locations, len(transcript))
    return locations

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

def read_legacy_chapter_locations(
    locations_path: Path,
    transcript: str,
    chapters: list[tuple[int, str]],
) -> list[ChapterLocation]:
    locations = parse_chapter_locations(
        locations_path.read_text(encoding="utf-8"),
        transcript,
        chapters,
    )
    return [
        ChapterLocation(
            chapter_id=location.chapter_id,
            heading=location.heading,
            start_quote=location.start_quote,
            start=location.start,
            source="reused",
        )
        for location in locations
    ]
