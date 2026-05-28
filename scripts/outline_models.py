from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
    source: str = "policy"


@dataclass(frozen=True)
class TranscriptSource:
    name: str
    path: Path
    text: str
    sha256: str


OutlinePolicy = dict[str, object]


@dataclass(frozen=True)
class PolicyMergeResult:
    policy: OutlinePolicy
    reason: str
    source_run: int | None = None
