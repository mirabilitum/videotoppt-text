from __future__ import annotations

import os
import re


HEADING_RE = re.compile(r"^(#{1,})\s+(.+?)\s*$")
ROOT_HEADING_RE = re.compile(r"^#\s+.+$", re.MULTILINE)


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


def normalize_with_index(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    index_map: list[int] = []
    for index, char in enumerate(text):
        if char.isalnum():
            chars.append(char.lower())
            index_map.append(index)
    return "".join(chars), index_map


def strip_heading_number(title: str) -> str:
    text = re.sub(r"^[一二三四五六七八九十百]+[、.．]\s*", "", title).strip()
    return re.sub(r"^\d+(?:\.\d+)*[、.．]?\s*", "", text).strip()


def chapter_heading(chapter_subtree: str) -> str:
    first_line = chapter_subtree.splitlines()[0] if chapter_subtree.splitlines() else ""
    match = HEADING_RE.match(first_line.strip())
    return match.group(2).strip() if match else first_line.strip().lstrip("#").strip()


def find_first(text: str, needles: tuple[str, ...], start: int = 0) -> int:
    matches = [text.find(needle, start) for needle in needles]
    matches = [idx for idx in matches if idx >= 0]
    return min(matches) if matches else -1
