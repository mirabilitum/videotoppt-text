from __future__ import annotations

import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(os.getenv("CODEX_TEST_ROOT", str(ROOT / ".codex_tmp" / "tests")))
TEST_ROOT.mkdir(parents=True, exist_ok=True)
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from outline_experiment import run_skeleton_only_experiment
from outline_granularity import (
    build_granularity_plan_from_skeleton,
    min_subsections_for_chars,
)
from outline_io import (
    outline_complete,
    select_transcript_source,
    write_outline_source,
)
from outline_llm import (
    call_anchor_repair_pass,
    call_granularity_repair_pass,
    call_outline_policy_pass,
    call_skeleton_pass_chunked,
    generate_skeleton_with_granularity,
    merge_outline_policy_runs,
)
from outline_locations import (
    format_chapter_locations,
    parse_chapter_locations,
    validate_anchor_chapter_locations,
    validate_chapter_locations,
    validate_final_chapter_locations,
)
from outline_models import (
    ChapterLocation,
    ChatResult,
    PolicyMergeResult,
    SkeletonGenerationResult,
    SkeletonRepairError,
)
from outline_policy import parse_outline_policy
from outline_skeleton import (
    build_skeleton_prompt,
    collect_granularity_failures,
    normalize_anchored_skeleton,
    normalize_skeleton,
    parse_chapters,
    parse_skeleton_anchor_locations,
    repair_skeleton_anchors_from_policy,
    strip_skeleton_anchors,
    validate_skeleton_anchors_against_policy,
    validate_skeleton_matches_granularity,
    validate_skeleton_matches_policy,
)
from scripts.generate_outline_deepseek import (
    parse_args,
)


class GenerateOutlineSourceTests(unittest.TestCase):
    def make_output_dir(self, name: str) -> Path:
        out = TEST_ROOT / "outline_source" / name / uuid.uuid4().hex
        out.mkdir(parents=True, exist_ok=True)
        return out

    def make_chat_client(self, responses: list[str]) -> object:
        class FakeChoice:
            def __init__(self, content: str) -> None:
                self.message = type("Message", (), {"content": content})()
                self.finish_reason = "stop"

        class FakeCompletions:
            def __init__(self, items: list[str]) -> None:
                self.items = list(items)

            def create(self, **kwargs: object) -> object:
                if not self.items:
                    raise RuntimeError("no fake responses left")
                return type("Response", (), {"choices": [FakeChoice(self.items.pop(0))]})()

        class FakeChat:
            def __init__(self, items: list[str]) -> None:
                self.completions = FakeCompletions(items)

        return type("FakeClient", (), {"chat": FakeChat(responses)})()

    def make_prompt(self, out: Path, content: str = "outline prompt") -> Path:
        prompt = out / "prompt.md"
        prompt.write_text(content, encoding="utf-8")
        return prompt

    def test_select_transcript_prefers_clean(self) -> None:
        out = self.make_output_dir("select_clean")
        (out / "transcript.txt").write_text("raw transcript", encoding="utf-8")
        (out / "transcript_clean.txt").write_text("clean transcript", encoding="utf-8")

        source = select_transcript_source(out)

        self.assertEqual(source.name, "clean")
        self.assertEqual(source.path, out / "transcript_clean.txt")
        self.assertEqual(source.text, "clean transcript")

    def test_select_transcript_falls_back_to_raw(self) -> None:
        out = self.make_output_dir("select_raw")
        (out / "transcript.txt").write_text("raw transcript", encoding="utf-8")

        source = select_transcript_source(out)

        self.assertEqual(source.name, "raw")
        self.assertEqual(source.path, out / "transcript.txt")

    def test_select_transcript_can_force_raw(self) -> None:
        out = self.make_output_dir("force_raw")
        (out / "transcript.txt").write_text("raw transcript", encoding="utf-8")
        (out / "transcript_clean.txt").write_text("clean transcript", encoding="utf-8")

        source = select_transcript_source(out, preferred="raw")

        self.assertEqual(source.name, "raw")
        self.assertEqual(source.path, out / "transcript.txt")

    def test_outline_complete_false_when_clean_replaces_raw_source(self) -> None:
        out = self.make_output_dir("source_change")
        prompt = self.make_prompt(out)
        (out / "transcript.txt").write_text("raw transcript", encoding="utf-8")
        (out / "outline.md").write_text("# outline\n", encoding="utf-8")
        with patch.dict(os.environ, {"OUTLINE_PROMPT_PATH": str(prompt)}):
            write_outline_source(out, select_transcript_source(out), prompt, model="deepseek-chat")
            self.assertTrue(outline_complete(out))

            (out / "transcript_clean.txt").write_text("clean transcript", encoding="utf-8")

            self.assertFalse(outline_complete(out))

    def test_outline_complete_false_when_prompt_changes(self) -> None:
        out = self.make_output_dir("prompt_change")
        prompt = self.make_prompt(out, "prompt one")
        (out / "transcript.txt").write_text("raw transcript", encoding="utf-8")
        (out / "outline.md").write_text("# outline\n", encoding="utf-8")
        with patch.dict(os.environ, {"OUTLINE_PROMPT_PATH": str(prompt)}):
            write_outline_source(out, select_transcript_source(out), prompt, model="deepseek-chat")

            prompt.write_text("prompt two", encoding="utf-8")

            self.assertFalse(outline_complete(out))

    def test_outline_source_json_schema(self) -> None:
        out = self.make_output_dir("schema")
        prompt = self.make_prompt(out)
        (out / "transcript.txt").write_text("raw transcript", encoding="utf-8")
        source = select_transcript_source(out)

        write_outline_source(out, source, prompt, model="deepseek-chat")

        payload = json.loads((out / "outline_source.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["transcript_source"], "raw")
        self.assertEqual(payload["transcript_path"], "transcript.txt")
        self.assertEqual(payload["outline_prompt_path"], str(prompt))
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertIn("generated_at", payload)

    def test_validate_chapter_locations_rejects_duplicate_starts(self) -> None:
        locations = [
            ChapterLocation(1, "A", "first quote", 5),
            ChapterLocation(2, "B", "second quote", 5),
        ]

        with self.assertRaisesRegex(RuntimeError, "strictly increasing"):
            validate_chapter_locations(locations, transcript_len=100)

    def test_validate_final_chapter_locations_rejects_estimated_sources(self) -> None:
        locations = [
            ChapterLocation(1, "Alpha", "alpha", 0, source="llm"),
            ChapterLocation(2, "Beta", "beta", 10, source="estimated_after_failure"),
        ]

        with self.assertRaisesRegex(RuntimeError, "estimated"):
            validate_final_chapter_locations(locations, transcript_len=100)

    def test_validate_final_chapter_locations_accepts_llm_candidate_and_reused_sources(self) -> None:
        locations = [
            ChapterLocation(1, "Alpha", "alpha", 0, source="candidate"),
            ChapterLocation(2, "Beta", "beta", 10, source="reused"),
        ]

        validate_final_chapter_locations(locations, transcript_len=100)

    def test_validate_anchor_chapter_locations_rejects_non_anchor_sources(self) -> None:
        locations = [
            ChapterLocation(1, "Alpha", "alpha", 0, source="skeleton_anchor"),
            ChapterLocation(2, "Beta", "beta", 10, source="reused"),
        ]

        with self.assertRaisesRegex(RuntimeError, "skeleton_anchor"):
            validate_anchor_chapter_locations(locations, transcript_len=100)

    def test_format_and_parse_chapter_locations_preserve_source(self) -> None:
        transcript = "alpha start beta start"
        locations = [
            ChapterLocation(1, "Alpha", "alpha start", 0, source="candidate"),
            ChapterLocation(2, "Beta", "beta start", 12, source="llm"),
        ]

        content = format_chapter_locations(locations)
        parsed = parse_chapter_locations(
            content,
            transcript,
            [(1, "## Alpha"), (2, "## Beta")],
        )

        self.assertEqual([item.source for item in parsed], ["candidate", "llm"])

    def test_normalize_skeleton_preserves_fourth_level_headings(self) -> None:
        skeleton = normalize_skeleton(
            """# Course Title

## Chapter
### Section
#### Detail
##### Too Deep
"""
        )

        self.assertIn("### Section", skeleton)
        self.assertIn("#### Detail", skeleton)
        self.assertNotIn("#####", skeleton)
        self.assertNotIn("Too Deep", skeleton)

    def test_normalize_anchored_skeleton_preserves_top_level_anchor_comments(self) -> None:
        skeleton = normalize_anchored_skeleton(
            """# Course Title

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->
### One

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""
        )

        self.assertIn('<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->', skeleton)
        self.assertIn("### One", skeleton)

    def test_strip_skeleton_anchors_returns_clean_markdown(self) -> None:
        clean = strip_skeleton_anchors(
            """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->
### One
"""
        )

        self.assertEqual(clean, "# T\n\n## Alpha\n### One")

    def test_parse_skeleton_anchor_locations_returns_anchor_locations(self) -> None:
        transcript = "intro alpha start details beta start tail"
        anchored = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->
### One

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""

        title, chapters, locations = parse_skeleton_anchor_locations(anchored, transcript)

        self.assertEqual(title, "# T")
        self.assertEqual([chapter for _, chapter in chapters], ["## Alpha\n### One", "## Beta"])
        self.assertEqual([location.source for location in locations], ["skeleton_anchor", "skeleton_anchor"])
        self.assertEqual([location.start for location in locations], [transcript.index("alpha start"), transcript.index("beta start")])

    def test_parse_skeleton_anchor_locations_uses_heading_order_when_anchor_id_is_wrong(self) -> None:
        transcript = "alpha start"
        anchored = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "alpha start"} -->
"""

        _, _, locations = parse_skeleton_anchor_locations(anchored, transcript)

        self.assertEqual([location.chapter_id for location in locations], [1])
        self.assertEqual(locations[0].start_quote, "alpha start")

    def test_parse_skeleton_anchor_locations_rejects_fuzzy_only_quote(self) -> None:
        transcript = "alpha start and more text"
        anchored = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start rewritten"} -->
"""

        with self.assertRaisesRegex(RuntimeError, "not found exactly"):
            parse_skeleton_anchor_locations(anchored, transcript)

    def test_parse_skeleton_anchor_locations_rejects_repeated_anchor_quote(self) -> None:
        transcript = "intro repeated marker details repeated marker tail"
        anchored = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "repeated marker"} -->
"""

        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            parse_skeleton_anchor_locations(anchored, transcript)

    def test_validate_skeleton_anchors_against_policy_rejects_shifted_quotes(self) -> None:
        transcript = "alpha start details beta start details gamma start tail"
        policy = {
            "top_level_items": ["Alpha", "Beta", "Gamma"],
            "ordered_blocks": [
                {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
                {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
                {"title": "Gamma", "start_quote": "gamma start", "candidate_top_level": True},
            ],
            "merge_policy": "",
            "parallel_groups": [],
        }
        shifted = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "beta start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "gamma start"} -->

## Gamma
"""

        with self.assertRaisesRegex(RuntimeError, "policy anchor mismatch"):
            validate_skeleton_anchors_against_policy(shifted, transcript, policy)

    def test_repair_skeleton_anchors_from_policy_replaces_shifted_and_missing_anchors(self) -> None:
        transcript = "alpha start details beta start details gamma start tail"
        policy = {
            "top_level_items": ["Alpha", "Beta", "Gamma"],
            "ordered_blocks": [
                {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
                {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
                {"title": "Gamma", "start_quote": "gamma start", "candidate_top_level": True},
            ],
            "merge_policy": "",
            "parallel_groups": [],
        }
        shifted = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "beta start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "gamma start"} -->

## Gamma
"""

        repaired = repair_skeleton_anchors_from_policy(shifted, policy)

        self.assertIn(
            '## Alpha\n<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->',
            repaired,
        )
        self.assertIn(
            '## Beta\n<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->',
            repaired,
        )
        self.assertIn(
            '## Gamma\n<!-- outline-anchor: {"chapter_id": 3, "start_quote": "gamma start"} -->',
            repaired,
        )
        validate_skeleton_anchors_against_policy(repaired, transcript, policy)

    def test_build_skeleton_prompt_allows_fourth_level_but_prefers_third(self) -> None:
        prompt = build_skeleton_prompt("base prompt", "transcript")

        self.assertIn("确有必要时可使用 `####`", prompt)
        self.assertIn("不得使用 `#####`", prompt)
        self.assertNotIn("不得使用 `####`", prompt)
        self.assertNotIn("不得输出 `####`", prompt)
        self.assertIn("outline-anchor", prompt)
        self.assertIn("start_quote", prompt)

    def test_chunked_skeleton_pass_forwards_policy_to_each_chunk(self) -> None:
        policy = parse_outline_policy(
            json.dumps(
                {
                    "top_level_items": ["Chapter"],
                    "merge_policy": "",
                    "parallel_groups": [],
                },
                ensure_ascii=False,
            )
        )

        with (
            patch("outline_llm.call_skeleton_pass") as call_skeleton_pass,
            patch("outline_llm.call_skeleton_merge_pass") as call_merge_pass,
        ):
            call_skeleton_pass.side_effect = ["# T\n\n## A\n", "# T\n\n## B\n"]
            call_merge_pass.return_value = "# T\n\n## A\n"

            call_skeleton_pass_chunked(
                object(),
                "prompt",
                "abcdefghij",
                chunk_size=6,
                overlap=1,
                policy=policy,
            )

        self.assertEqual(call_skeleton_pass.call_count, 2)
        for call in call_skeleton_pass.call_args_list:
            forwarded_policy = call.kwargs.get("policy") if call.kwargs else call.args[3]
            self.assertIs(forwarded_policy, policy)
        self.assertIs(call_merge_pass.call_args.args[2], policy)

    def test_call_anchor_repair_pass_sends_policy_titles_and_quotes(self) -> None:
        broken = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->
"""
        policy = {
            "top_level_items": ["Alpha", "Beta"],
            "ordered_blocks": [
                {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
                {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
            ],
            "merge_policy": "",
            "parallel_groups": [],
        }
        repaired = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""
        with patch("outline_llm.call_chat") as call_chat:
            call_chat.return_value = ChatResult(
                content=repaired,
                finish_reason="stop",
                continuations=0,
            )

            result = call_anchor_repair_pass(
                object(),
                "prompt",
                broken,
                policy,
                "chapter count mismatch",
            )

        self.assertIn("## Beta", result)
        prompt = call_chat.call_args.kwargs["user_prompt"]
        self.assertIn("chapter count mismatch", prompt)
        self.assertIn("Beta", prompt)
        self.assertIn("beta start", prompt)
        self.assertIn("outline-anchor", prompt)

    def test_call_granularity_repair_pass_sends_limited_repair_prompt(self) -> None:
        anchored = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""
        plan = [
            {"top_level_item": "Alpha", "source_chars": 2000, "min_subsections": 3, "max_depth": 4},
            {"top_level_item": "Beta", "source_chars": 100, "min_subsections": 0, "max_depth": 4},
        ]
        failures = [
            {
                "chapter_id": 1,
                "top_level_item": "Alpha",
                "source_chars": 2000,
                "min_subsections": 3,
                "actual_subsections": 0,
            }
        ]
        with patch("outline_llm.call_chat") as call_chat:
            call_chat.return_value = ChatResult(
                content=anchored.replace("## Beta", "### One\n### Two\n### Three\n\n## Beta"),
                finish_reason="stop",
                continuations=0,
            )

            repaired = call_granularity_repair_pass(object(), "prompt", anchored, plan, failures)

        self.assertIn("### One", repaired)
        prompt = call_chat.call_args.kwargs["user_prompt"]
        self.assertIn("chapter_id", prompt)
        self.assertIn("min_subsections", prompt)
        self.assertIn("actual_subsections", prompt)
        self.assertIn("Do not modify `#`, any `##` heading", prompt)
        self.assertIn("outline-anchor", prompt)

    def test_parse_args_supports_skeleton_only(self) -> None:
        with patch.object(sys, "argv", ["generate_outline_deepseek.py", "--skeleton-only"]):
            args = parse_args()

        self.assertTrue(args.skeleton_only)

    def test_min_subsections_for_chars_uses_length_thresholds(self) -> None:
        self.assertEqual(min_subsections_for_chars(199), 0)
        self.assertEqual(min_subsections_for_chars(999), 0)
        self.assertEqual(min_subsections_for_chars(1000), 2)
        self.assertEqual(min_subsections_for_chars(1999), 2)
        self.assertEqual(min_subsections_for_chars(2000), 3)

    def test_build_granularity_plan_from_skeleton_uses_skeleton_order_and_lengths(self) -> None:
        transcript = "intro " + ("x" * 1200) + " Beta " + ("y" * 100)
        skeleton = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "intro"} -->
### One

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "Beta"} -->
"""
        plan, chapters = build_granularity_plan_from_skeleton(transcript, skeleton)

        self.assertEqual([item["top_level_item"] for item in plan], ["Alpha", "Beta"])
        self.assertEqual([heading for _, heading in chapters], ["## Alpha\n### One", "## Beta"])
        self.assertGreaterEqual(plan[0]["source_chars"], 1000)
        self.assertEqual(plan[0]["min_subsections"], 2)
        self.assertEqual([item["location_source"] for item in plan], ["skeleton_anchor", "skeleton_anchor"])
        self.assertEqual(plan[0]["start_quote"], "intro")
        self.assertLess(plan[1]["source_chars"], 200)
        self.assertEqual(plan[1]["min_subsections"], 0)

    def test_build_granularity_plan_from_skeleton_rejects_missing_anchor(self) -> None:
        transcript = "alpha start beta start"
        skeleton = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
"""

        with self.assertRaisesRegex(RuntimeError, "missing"):
            build_granularity_plan_from_skeleton(transcript, skeleton)

    def test_generate_skeleton_with_granularity_uses_single_anchored_skeleton(self) -> None:
        transcript = "alpha start short beta start tail"
        policy = {
            "top_level_basis": "x",
            "top_level_items": ["Alpha", "Beta"],
            "ordered_blocks": [
                {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
                {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
            ],
            "merge_policy": "",
            "parallel_groups": [],
        }
        anchored = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""

        with patch(
            "outline_llm.generate_skeleton_from_policy",
            return_value=anchored,
        ) as generate_skeleton:
            result = generate_skeleton_with_granularity(
                object(),
                "prompt",
                transcript,
                None,
                policy,
                len(transcript),
            )

        generate_skeleton.assert_called_once()
        self.assertIsNone(generate_skeleton.call_args.kwargs["granularity_plan"])
        self.assertIn("outline-anchor", result.anchored_skeleton)
        self.assertNotIn("outline-anchor", result.skeleton)
        self.assertEqual([location.source for location in result.locations], ["skeleton_anchor", "skeleton_anchor"])
        self.assertEqual([item["location_source"] for item in result.granularity_plan], ["skeleton_anchor", "skeleton_anchor"])
        self.assertEqual(result.retry_report["status"], "valid")

    def test_generate_skeleton_with_granularity_ignores_wrong_anchor_ids(self) -> None:
        transcript = "alpha start short beta start tail"
        policy = {
            "top_level_basis": "x",
            "top_level_items": ["Alpha", "Beta"],
            "ordered_blocks": [
                {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
                {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
            ],
            "merge_policy": "",
            "parallel_groups": [],
        }
        final_wrong_ids = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 3, "start_quote": "beta start"} -->
"""

        with patch(
            "outline_llm.generate_skeleton_from_policy",
            return_value=final_wrong_ids,
        ):
            result = generate_skeleton_with_granularity(
                object(),
                "prompt",
                transcript,
                None,
                policy,
                len(transcript),
            )

        self.assertEqual([location.chapter_id for location in result.locations], [1, 2])
        self.assertEqual([location.start_quote for location in result.locations], ["alpha start", "beta start"])
        self.assertIn('{"chapter_id": 1, "start_quote": "alpha start"}', result.anchored_skeleton)
        self.assertIn('{"chapter_id": 2, "start_quote": "beta start"}', result.anchored_skeleton)

    def test_generate_skeleton_with_granularity_repairs_shifted_policy_anchors_before_granularity(self) -> None:
        transcript = "alpha start details beta start tail"
        policy = {
            "top_level_items": ["Alpha", "Beta"],
            "ordered_blocks": [
                {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
                {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
            ],
            "merge_policy": "",
            "parallel_groups": [],
        }
        shifted = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "beta start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""

        with patch("outline_llm.generate_skeleton_from_policy", return_value=shifted):
            result = generate_skeleton_with_granularity(
                object(),
                "prompt",
                transcript,
                None,
                policy,
                len(transcript),
            )

        self.assertEqual([location.start_quote for location in result.locations], ["alpha start", "beta start"])
        self.assertEqual(result.retry_report["anchor_repair_count"], 1)
        self.assertEqual(result.retry_report["status"], "valid")

    def test_generate_skeleton_with_granularity_uses_anchor_llm_fallback_when_chapter_is_missing(self) -> None:
        transcript = "alpha start details beta start tail"
        policy = {
            "top_level_items": ["Alpha", "Beta"],
            "ordered_blocks": [
                {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
                {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
            ],
            "merge_policy": "",
            "parallel_groups": [],
        }
        missing = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->
"""
        repaired = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""

        with (
            patch("outline_llm.generate_skeleton_from_policy", return_value=missing),
            patch("outline_llm.call_anchor_repair_pass", return_value=repaired) as anchor_repair,
        ):
            result = generate_skeleton_with_granularity(
                object(),
                "prompt",
                transcript,
                None,
                policy,
                len(transcript),
            )

        anchor_repair.assert_called_once()
        self.assertEqual([location.start_quote for location in result.locations], ["alpha start", "beta start"])
        self.assertEqual(result.retry_report["anchor_repair_count"], 1)
        self.assertEqual(result.retry_report["status"], "valid")

    def test_generate_skeleton_with_granularity_repairs_granularity_after_anchor_success(self) -> None:
        transcript = "alpha start " + ("a" * 2200) + " beta start tail"
        policy = {
            "top_level_items": ["Alpha", "Beta"],
            "ordered_blocks": [
                {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
                {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
            ],
            "merge_policy": "",
            "parallel_groups": [],
        }
        coarse = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""
        repaired = coarse.replace(
            "## Beta",
            "### One\n### Two\n### Three\n\n## Beta",
        )

        with (
            patch("outline_llm.generate_skeleton_from_policy", return_value=coarse),
            patch("outline_llm.call_granularity_repair_pass", return_value=repaired) as repair_pass,
        ):
            result = generate_skeleton_with_granularity(
                object(),
                "prompt",
                transcript,
                None,
                policy,
                len(transcript),
            )

        repair_pass.assert_called_once()
        self.assertIn("### Three", result.skeleton)
        self.assertEqual(result.retry_report["granularity_repair_count"], 1)
        self.assertEqual(result.retry_report["status"], "valid")

    def test_generate_skeleton_with_granularity_reports_granularity_fail_after_repair_exhaustion(self) -> None:
        transcript = "alpha start " + ("a" * 2200) + " beta start tail"
        policy = {
            "top_level_items": ["Alpha", "Beta"],
            "ordered_blocks": [
                {"title": "Alpha", "start_quote": "alpha start", "candidate_top_level": True},
                {"title": "Beta", "start_quote": "beta start", "candidate_top_level": True},
            ],
            "merge_policy": "",
            "parallel_groups": [],
        }
        coarse = """# T

## Alpha
<!-- outline-anchor: {"chapter_id": 1, "start_quote": "alpha start"} -->

## Beta
<!-- outline-anchor: {"chapter_id": 2, "start_quote": "beta start"} -->
"""

        with (
            patch.dict(os.environ, {"OUTLINE_GRANULARITY_REPAIR_MAX_ATTEMPTS": "1"}),
            patch("outline_llm.generate_skeleton_from_policy", return_value=coarse),
            patch("outline_llm.call_granularity_repair_pass", return_value=coarse),
        ):
            with self.assertRaises(SkeletonRepairError) as raised:
                generate_skeleton_with_granularity(
                    object(),
                    "prompt",
                    transcript,
                    None,
                    policy,
                    len(transcript),
                )

        self.assertEqual(raised.exception.status, "GRANULARITY_FAIL")
        self.assertEqual(raised.exception.retry_report["granularity_repair_count"], 1)

    def test_validate_skeleton_matches_granularity_rejects_under_split_long_chapter(self) -> None:
        _, chapters = parse_chapters("# T\n\n## Alpha\n### One\n\n## Beta\n")
        plan = [
            {"top_level_item": "Alpha", "source_chars": 1200, "min_subsections": 2, "max_depth": 4},
            {"top_level_item": "Beta", "source_chars": 100, "min_subsections": 0, "max_depth": 4},
        ]

        with self.assertRaisesRegex(RuntimeError, "granularity plan"):
            validate_skeleton_matches_granularity(chapters, plan)

    def test_collect_granularity_failures_reports_short_subsection_counts(self) -> None:
        _, chapters = parse_chapters("# T\n\n## Alpha\n\n## Beta\n### One\n")
        plan = [
            {"top_level_item": "Alpha", "source_chars": 2000, "min_subsections": 3, "max_depth": 4},
            {"top_level_item": "Beta", "source_chars": 1200, "min_subsections": 2, "max_depth": 4},
        ]

        failures = collect_granularity_failures(chapters, plan)

        self.assertEqual(
            failures,
            [
                {
                    "chapter_id": 1,
                    "top_level_item": "Alpha",
                    "source_chars": 2000,
                    "min_subsections": 3,
                    "actual_subsections": 0,
                },
                {
                    "chapter_id": 2,
                    "top_level_item": "Beta",
                    "source_chars": 1200,
                    "min_subsections": 2,
                    "actual_subsections": 1,
                },
            ],
        )

    def test_validate_skeleton_matches_granularity_allows_short_unsplit_chapter(self) -> None:
        _, chapters = parse_chapters("# T\n\n## Alpha\n### One\n### Two\n\n## Beta\n")
        plan = [
            {"top_level_item": "Alpha", "source_chars": 1200, "min_subsections": 2, "max_depth": 4},
            {"top_level_item": "Beta", "source_chars": 100, "min_subsections": 0, "max_depth": 4},
        ]

        validate_skeleton_matches_granularity(chapters, plan)

    def test_skeleton_only_experiment_generates_each_skeleton_from_canonical_policy(self) -> None:
        out = self.make_output_dir("skeleton_experiment")
        policies = [
            {"top_level_basis": "x", "top_level_items": ["A"], "merge_policy": "", "parallel_groups": []},
            {"top_level_basis": "x", "top_level_items": ["B"], "merge_policy": "", "parallel_groups": []},
        ]
        canonical_policy = {
            "top_level_basis": "x",
            "top_level_items": ["A", "B"],
            "merge_policy": "",
            "parallel_groups": [],
        }
        skeleton_result = SkeletonGenerationResult(
            skeleton="# T\n\n## A\n\n## B\n",
            anchored_skeleton=(
                '# T\n\n## A\n<!-- outline-anchor: {"chapter_id": 1, "start_quote": "A start"} -->'
                '\n\n## B\n<!-- outline-anchor: {"chapter_id": 2, "start_quote": "B start"} -->'
            ),
            granularity_plan=[
                {
                    "top_level_item": "A",
                    "source_chars": 100,
                    "min_subsections": 0,
                    "max_depth": 4,
                    "location_source": "skeleton_anchor",
                },
                {
                    "top_level_item": "B",
                    "source_chars": 100,
                    "min_subsections": 0,
                    "max_depth": 4,
                    "location_source": "skeleton_anchor",
                },
            ],
            locations=[
                ChapterLocation(1, "A", "A start", 0, source="skeleton_anchor"),
                ChapterLocation(2, "B", "B start", 100, source="skeleton_anchor"),
            ],
            retry_report={
                "status": "valid",
                "anchor_repair_count": 1,
                "granularity_repair_count": 1,
                "anchor_error": "shifted",
                "granularity_repair_chapters": [{"chapter_id": 1}],
            },
        )

        with (
            patch("outline_experiment.call_outline_policy_pass", side_effect=policies),
            patch(
                "outline_experiment.merge_outline_policy_runs",
                return_value=PolicyMergeResult(canonical_policy, "llm_union"),
            ) as merge_policy,
            patch(
                "outline_experiment.generate_skeleton_with_granularity",
                side_effect=[skeleton_result, skeleton_result],
            ) as generate_skeleton,
        ):
            run_skeleton_only_experiment(
                client=object(),
                prompt_template="prompt",
                transcript="transcript",
                course_title=None,
                out=out,
                policy_runs=2,
                skeleton_runs=2,
            )

        merge_policy.assert_called_once()
        self.assertEqual(generate_skeleton.call_count, 2)
        for call in generate_skeleton.call_args_list:
            self.assertIs(call.args[4], canonical_policy)
        manifest = json.loads((out / "outline_skeleton_experiment.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["policy_runs"]), 2)
        self.assertEqual(manifest["policy_merge_reason"], "llm_union")
        self.assertEqual(manifest["canonical_policy_path"], "outline_policy_canonical.json")
        self.assertEqual(len(manifest["runs"]), 2)
        self.assertEqual(
            [item["skeleton_run"] for item in manifest["runs"]],
            [1, 2],
        )
        self.assertTrue((out / "outline_policy_run_01.json").exists())
        self.assertTrue((out / "outline_policy_run_02.json").exists())
        self.assertTrue((out / "outline_policy_canonical.json").exists())
        self.assertTrue((out / "outline_skeleton_run_01.md").exists())
        self.assertTrue((out / "outline_skeleton_run_02.md").exists())
        self.assertTrue((out / "outline_skeleton_anchored_run_01.md").exists())
        self.assertTrue((out / "outline_skeleton_anchored_run_02.md").exists())
        self.assertIn("anchored_skeleton_path", manifest["runs"][0])
        self.assertIn("anchored_skeleton_sha256", manifest["runs"][0])
        self.assertEqual(manifest["runs"][0]["skeleton_retry_status"], "valid")
        self.assertEqual(manifest["runs"][0]["anchor_repair_count"], 1)
        self.assertEqual(manifest["runs"][0]["granularity_repair_count"], 1)
        self.assertIn("granularity_repair_chapters", manifest["runs"][0])
        self.assertIn("outline-anchor", (out / manifest["runs"][0]["anchored_skeleton_path"]).read_text(encoding="utf-8"))
        self.assertNotIn("outline-anchor", (out / manifest["runs"][0]["skeleton_path"]).read_text(encoding="utf-8"))

    def test_skeleton_only_experiment_records_invalid_skeleton_and_continues(self) -> None:
        out = self.make_output_dir("skeleton_invalid")
        policy = {
            "top_level_basis": "x",
            "top_level_items": ["A", "B"],
            "merge_policy": "",
            "parallel_groups": [],
        }

        with (
            patch("outline_experiment.call_outline_policy_pass", return_value=policy),
            patch(
                "outline_experiment.generate_skeleton_with_granularity",
                side_effect=[
                    SkeletonGenerationResult(
                        skeleton="# T\n\n## A\n",
                        anchored_skeleton='# T\n\n## A\n<!-- outline-anchor: {"chapter_id": 1, "start_quote": "A start"} -->',
                        granularity_plan=[
                            {
                                "top_level_item": "A",
                                "source_chars": 100,
                                "min_subsections": 0,
                                "max_depth": 4,
                                "location_source": "skeleton_anchor",
                            }
                        ],
                        locations=[ChapterLocation(1, "A", "A start", 0, source="skeleton_anchor")],
                    ),
                    SkeletonGenerationResult(
                        skeleton="# T\n\n## A\n\n## B\n",
                        anchored_skeleton=(
                            '# T\n\n## A\n<!-- outline-anchor: {"chapter_id": 1, "start_quote": "A start"} -->'
                            '\n\n## B\n<!-- outline-anchor: {"chapter_id": 2, "start_quote": "B start"} -->'
                        ),
                        granularity_plan=[
                            {
                                "top_level_item": "A",
                                "source_chars": 100,
                                "min_subsections": 0,
                                "max_depth": 4,
                                "location_source": "skeleton_anchor",
                            },
                            {
                                "top_level_item": "B",
                                "source_chars": 100,
                                "min_subsections": 0,
                                "max_depth": 4,
                                "location_source": "skeleton_anchor",
                            },
                        ],
                        locations=[
                            ChapterLocation(1, "A", "A start", 0, source="skeleton_anchor"),
                            ChapterLocation(2, "B", "B start", 100, source="skeleton_anchor"),
                        ],
                    ),
                ],
            ),
        ):
                run_skeleton_only_experiment(
                    client=object(),
                    prompt_template="prompt",
                    transcript="transcript",
                    course_title=None,
                    out=out,
                    policy_runs=1,
                    skeleton_runs=2,
                )

        manifest = json.loads((out / "outline_skeleton_experiment.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["runs"][0]["valid"])
        self.assertIn("chapter count", manifest["runs"][0]["validation_error"])
        self.assertTrue(manifest["runs"][1]["valid"])
        self.assertEqual(
            (out / "outline_skeleton.md").read_text(encoding="utf-8"),
            (out / "outline_skeleton_run_02.md").read_text(encoding="utf-8"),
        )

    def test_parse_outline_policy_rejects_duplicate_top_level_items(self) -> None:
        payload = {
            "top_level_items": ["一、课程整体说明", "课程整体说明"],
            "merge_policy": "",
            "parallel_groups": [],
        }

        with self.assertRaisesRegex(RuntimeError, "duplicates"):
            parse_outline_policy(json.dumps(payload, ensure_ascii=False))

    def test_call_outline_policy_pass_builds_prompt_with_json_example(self) -> None:
        payload = {
            "course_structure_summary": "summary",
            "ordered_blocks": [
                {
                    "block_id": "B01",
                    "title": "课程整体说明",
                    "scope_summary": "opening",
                    "role": "overview",
                    "start_quote": "课程开头",
                    "candidate_top_level": True,
                }
            ],
            "top_level_items": ["课程整体说明"],
            "candidate_top_level_items": ["课程整体说明"],
            "merge_policy": "",
            "parallel_groups": [],
        }
        with patch("outline_llm.call_chat") as call_chat:
            call_chat.return_value = ChatResult(
                content=json.dumps(payload, ensure_ascii=False),
                finish_reason="stop",
                continuations=0,
            )

            policy = call_outline_policy_pass(object(), "prompt", "transcript")

        self.assertEqual(policy["top_level_items"], ["课程整体说明"])
        self.assertEqual(policy["course_structure_summary"], "summary")
        self.assertEqual(policy["ordered_blocks"][0]["block_id"], "B01")
        self.assertEqual(policy["candidate_top_level_items"], ["课程整体说明"])
        self.assertIn("top_level_items", call_chat.call_args.kwargs["user_prompt"])
        self.assertIn("ordered_blocks", call_chat.call_args.kwargs["user_prompt"])
        self.assertIn("course_structure_summary", call_chat.call_args.kwargs["user_prompt"])
        self.assertNotIn("chapter_sections", call_chat.call_args.kwargs["user_prompt"])

    def test_merge_outline_policy_runs_uses_strict_superset_without_llm(self) -> None:
        subset = {
            "top_level_basis": "x",
            "top_level_items": ["A"],
            "merge_policy": "",
            "parallel_groups": [],
        }
        superset = {
            "top_level_basis": "x",
            "top_level_items": ["A", "B"],
            "merge_policy": "",
            "parallel_groups": [],
        }

        with patch("outline_llm.call_outline_policy_merge_pass") as merge_pass:
            result = merge_outline_policy_runs(object(), "prompt", "transcript", [subset, superset])

        merge_pass.assert_not_called()
        self.assertIs(result.policy, superset)
        self.assertEqual(result.reason, "policy_run_02_strict_superset")
        self.assertEqual(result.source_run, 2)

    def test_merge_outline_policy_runs_calls_llm_for_union(self) -> None:
        left = {
            "top_level_basis": "x",
            "top_level_items": ["A", "C"],
            "merge_policy": "left",
            "parallel_groups": [],
        }
        right = {
            "top_level_basis": "x",
            "top_level_items": ["A", "B"],
            "merge_policy": "right",
            "parallel_groups": [],
        }
        merged = {
            "course_structure_summary": "merged summary",
            "ordered_blocks": [
                {
                    "block_id": "C01",
                    "title": "A",
                    "scope_summary": "merged A",
                    "role": "main_section",
                    "start_quote": "A start",
                    "candidate_top_level": True,
                }
            ],
            "top_level_basis": "x",
            "top_level_items": ["A", "B", "C"],
            "candidate_top_level_items": ["A", "B", "C"],
            "merge_policy": "left and right",
            "parallel_groups": [],
            "ordering_basis": "by start_quote",
            "merge_trace": [
                {
                    "canonical_item": "A",
                    "sources": ["policy_run_01:B01", "policy_run_02:B01"],
                    "decision": "merged",
                    "reason": "same scope",
                }
            ],
            "dropped_or_merged_items": [
                {
                    "source": "policy_run_02:B01",
                    "decision": "merged_into",
                    "target": "A",
                    "reason": "same scope",
                }
            ],
        }
        with patch("outline_llm.call_chat") as call_chat:
            call_chat.return_value = ChatResult(
                content=json.dumps(merged, ensure_ascii=False),
                finish_reason="stop",
                continuations=0,
            )

            result = merge_outline_policy_runs(object(), "prompt", "transcript", [left, right])

        self.assertEqual(result.policy["top_level_items"], ["A", "B", "C"])
        self.assertEqual(result.policy["ordering_basis"], "by start_quote")
        self.assertEqual(result.policy["merge_trace"][0]["decision"], "merged")
        self.assertEqual(result.policy["dropped_or_merged_items"][0]["target"], "A")
        self.assertEqual(result.reason, "llm_union")
        self.assertIsNone(result.source_run)
        self.assertIn("Pass 0 merge", call_chat.call_args.kwargs["user_prompt"])
        self.assertIn("Policy run 1", call_chat.call_args.kwargs["user_prompt"])
        self.assertIn("merge_trace", call_chat.call_args.kwargs["user_prompt"])
        self.assertIn("dropped_or_merged_items", call_chat.call_args.kwargs["user_prompt"])

    def test_validate_skeleton_matches_policy_accepts_numbered_headings(self) -> None:
        policy = parse_outline_policy(
            json.dumps(
                {
                    "top_level_items": ["课程整体说明", "第一单元：分类与整理"],
                    "merge_policy": "",
                    "parallel_groups": [],
                },
                ensure_ascii=False,
            )
        )
        _, chapters = parse_chapters(
            """# 课程标题

## 一、课程整体说明
### 教材变化

## 二、第一单元：分类与整理
### 单元目标
"""
        )

        validate_skeleton_matches_policy(chapters, policy)

    def test_validate_skeleton_matches_policy_rejects_extra_chapter(self) -> None:
        policy = parse_outline_policy(
            json.dumps(
                {
                    "top_level_items": ["课程整体说明", "第一单元：分类与整理"],
                    "merge_policy": "",
                    "parallel_groups": [],
                },
                ensure_ascii=False,
            )
        )
        _, chapters = parse_chapters(
            """# 课程标题

## 一、课程整体说明

## 二、表内乘除法单元整体说明

## 三、第一单元：分类与整理
"""
        )

        with self.assertRaisesRegex(RuntimeError, "chapter count"):
            validate_skeleton_matches_policy(chapters, policy)

    def test_validate_skeleton_matches_policy_rejects_title_mismatch(self) -> None:
        policy = parse_outline_policy(
            json.dumps(
                {
                    "top_level_items": ["课程整体说明", "第一单元：分类与整理"],
                    "merge_policy": "",
                    "parallel_groups": [],
                },
                ensure_ascii=False,
            )
        )
        _, chapters = parse_chapters(
            """# 课程标题

## 一、课程整体说明

## 二、第二单元：一到六的表内乘法
"""
        )

        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            validate_skeleton_matches_policy(chapters, policy)


if __name__ == "__main__":
    unittest.main()
