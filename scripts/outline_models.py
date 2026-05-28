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
    source: str = "llm"


@dataclass(frozen=True)
class TranscriptSource:
    name: str
    path: Path
    text: str
    sha256: str


OutlinePolicy = dict[str, object]
GranularityPlan = list[dict[str, object]]


@dataclass(frozen=True)
class PolicyMergeResult:
    policy: OutlinePolicy
    reason: str
    source_run: int | None = None


@dataclass(frozen=True)
class SkeletonGenerationResult:
    skeleton: str
    anchored_skeleton: str
    granularity_plan: GranularityPlan
    locations: list[ChapterLocation]
    retry_report: dict[str, object] | None = None


class SkeletonRepairError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: str,
        retry_report: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_report = dict(retry_report)
