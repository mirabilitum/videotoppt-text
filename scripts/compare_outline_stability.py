from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import unicodedata


HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class StabilityResult:
    outline_a: str
    outline_b: str
    heading_similarity: float
    body_similarity: float
    a_heading_count: int
    b_heading_count: int
    a_depth_counts: dict[str, int]
    b_depth_counts: dict[str, int]


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(char for char in text if not char.isspace())


def split_outline(markdown: str) -> tuple[list[tuple[int, str]], str]:
    headings: list[tuple[int, str]] = []
    body_lines: list[str] = []
    for raw_line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = HEADING_RE.match(raw_line)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip()))
            continue
        if raw_line.strip().startswith("```"):
            continue
        body_lines.append(raw_line)
    return headings, "\n".join(body_lines)


def heading_text(headings: list[tuple[int, str]]) -> str:
    return "\n".join(f"{'#' * depth} {title}" for depth, title in headings)


def depth_counts(headings: list[tuple[int, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for depth, _ in headings:
        key = str(depth)
        counts[key] = counts.get(key, 0) + 1
    return counts


def ratio(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio()


def compare_outlines(path_a: Path, path_b: Path) -> StabilityResult:
    markdown_a = path_a.read_text(encoding="utf-8-sig")
    markdown_b = path_b.read_text(encoding="utf-8-sig")
    headings_a, body_a = split_outline(markdown_a)
    headings_b, body_b = split_outline(markdown_b)
    return StabilityResult(
        outline_a=str(path_a),
        outline_b=str(path_b),
        heading_similarity=ratio(heading_text(headings_a), heading_text(headings_b)),
        body_similarity=ratio(body_a, body_b),
        a_heading_count=len(headings_a),
        b_heading_count=len(headings_b),
        a_depth_counts=depth_counts(headings_a),
        b_depth_counts=depth_counts(headings_b),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two generated outline Markdown files.")
    parser.add_argument("outline_a", type=Path)
    parser.add_argument("outline_b", type=Path)
    parser.add_argument("--json", type=Path, help="Optional JSON report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = compare_outlines(args.outline_a, args.outline_b)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"heading_similarity={result.heading_similarity:.6f}")
    print(f"body_similarity={result.body_similarity:.6f}")
    print(f"a_heading_count={result.a_heading_count}")
    print(f"b_heading_count={result.b_heading_count}")
    print(f"a_depth_counts={result.a_depth_counts}")
    print(f"b_depth_counts={result.b_depth_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
