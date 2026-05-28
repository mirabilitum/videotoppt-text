from __future__ import annotations

import os
import json
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(os.getenv("CODEX_TEST_ROOT", str(ROOT / ".codex_tmp" / "tests")))
TEST_ROOT.mkdir(parents=True, exist_ok=True)
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import scripts.run_pipeline as run_pipeline


class RunPipelineTests(unittest.TestCase):
    def make_workdir(self, name: str) -> Path:
        workdir = TEST_ROOT / "pipeline" / name / uuid.uuid4().hex
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir

    def test_extract_frames_uses_one_shared_frames_dir(self) -> None:
        out = self.make_workdir("extract_frames")
        (out / "audio_parts").mkdir(parents=True, exist_ok=True)
        (out / "audio_parts" / "audio_part_000.wav").write_bytes(b"wav")
        (out / "audio_parts" / "audio_part_001.wav").write_bytes(b"wav")
        (out / "video_parts").mkdir(parents=True, exist_ok=True)
        (out / "video_parts" / "video_part_000.ts").write_bytes(b"ts0")
        (out / "video_parts" / "video_part_001.ts").write_bytes(b"ts1")

        calls: list[list[str]] = []

        def fake_run_child(args: list[str], env: dict[str, str]) -> None:
            calls.append(args)

        with patch.object(run_pipeline, "download_video_parts") as download_video_parts, patch.object(
            run_pipeline, "run_child", side_effect=fake_run_child
        ):
            download_video_parts.side_effect = lambda *args, **kwargs: None
            run_pipeline.extract_frames_by_video_parts(out, "https://video", 900, {})

        self.assertEqual(len(calls), 2)
        for index, args in enumerate(calls):
            self.assertIn("--frames-dir", args)
            self.assertIn(str(out / "frames"), args)
            self.assertNotIn("frames_part_", " ".join(args))
            self.assertIn("--time-offset-seconds", args)
            self.assertEqual(args[args.index("--time-offset-seconds") + 1], str(index * 900))

    def test_download_video_parts_requires_positive_part_seconds(self) -> None:
        out = self.make_workdir("download_parts")
        with self.assertRaises(ValueError):
            run_pipeline.download_video_parts(out, "https://video", 0, {})

    def test_write_course_info_and_state_round_trip(self) -> None:
        out = self.make_workdir("state_round_trip")
        state_path = out / "pipeline_state.json"
        run_pipeline.write_course_info(out, "3.0", "\u8bfe\u7a0b\u6807\u9898", "https://page")
        run_pipeline.mark_state(state_path, {}, "frames", "done")

        course_info = json.loads((out / "course_info.json").read_text(encoding="utf-8"))
        state = run_pipeline.load_state(state_path)

        self.assertEqual(course_info["seq"], 3)
        self.assertEqual(course_info["title"], "\u8bfe\u7a0b\u6807\u9898")
        self.assertEqual(course_info["page_url"], "https://page")
        self.assertEqual(state["frames"], "done")

    def test_validate_grade_subject_args_requires_pair(self) -> None:
        with self.assertRaises(ValueError):
            run_pipeline.validate_grade_subject_args("grade", None)
        with self.assertRaises(ValueError):
            run_pipeline.validate_grade_subject_args(None, "subject")
        run_pipeline.validate_grade_subject_args("grade", "subject")

    def test_merge_and_clean_completion_are_independent(self) -> None:
        out = self.make_workdir("completion")
        (out / "transcript.txt").write_text("raw", encoding="utf-8")

        self.assertTrue(run_pipeline.merge_raw_exists(out))
        self.assertFalse(run_pipeline.merge_clean_exists(out))

    def test_outline_completion_requires_source_metadata(self) -> None:
        out = self.make_workdir("outline_completion")
        (out / "transcript.txt").write_text("raw", encoding="utf-8")
        (out / "outline.md").write_text("# outline\n", encoding="utf-8")

        self.assertFalse(run_pipeline.outline_exists(out))

    def test_outline_completion_can_force_raw_when_clean_exists(self) -> None:
        out = self.make_workdir("outline_force_raw")
        prompt = out / "prompt.md"
        prompt.write_text("prompt", encoding="utf-8")
        (out / "transcript.txt").write_text("raw", encoding="utf-8")
        (out / "transcript_clean.txt").write_text("clean", encoding="utf-8")
        (out / "outline.md").write_text("# outline\n", encoding="utf-8")
        from scripts.generate_outline_deepseek import select_transcript_source, write_outline_source

        with patch.dict("os.environ", {"OUTLINE_PROMPT_PATH": str(prompt)}):
            write_outline_source(
                out,
                select_transcript_source(out, preferred="raw"),
                prompt,
                model="deepseek-chat",
            )

            self.assertFalse(run_pipeline.outline_exists(out))
            self.assertTrue(run_pipeline.outline_exists(out, force_raw=True))

    def test_clean_command_uses_context_before_clean_and_merge_clean(self) -> None:
        out = self.make_workdir("commands")
        env = run_pipeline.child_env(out, video_url="video", audio_url="audio")

        context_cmd = run_pipeline.infer_context_command(grade="grade", subject="subject", skip_context_infer=True)
        clean_cmd = run_pipeline.clean_command()
        merge_cmd = run_pipeline.merge_clean_command()

        self.assertIn("infer_course_context.py", context_cmd[1])
        self.assertIn("--grade", context_cmd)
        self.assertIn("--subject", context_cmd)
        self.assertNotIn("--skip-context-infer", context_cmd)
        self.assertIn("clean_transcript.py", clean_cmd[1])
        self.assertEqual(merge_cmd[-1], "--clean")
        self.assertEqual(env["OUTPUT_DIR"], str(out))

    def test_skipped_state_does_not_block_later_resume_when_completion_missing(self) -> None:
        out = self.make_workdir("skipped_resume")
        state_path = out / "pipeline_state.json"
        state = {"clean": "skipped"}
        calls: list[str] = []

        run_pipeline.run_pipeline_step(
            step="clean",
            state_path=state_path,
            state=state,
            resume=True,
            completion=lambda: bool(calls),
            action=lambda: calls.append("ran"),
        )

        self.assertEqual(calls, ["ran"])
        self.assertEqual(run_pipeline.load_state(state_path)["clean"], "done")

    def test_outline_command_for_skip_clean_forces_raw_source(self) -> None:
        cmd = run_pipeline.outline_command(resume=True, force_raw=True)

        self.assertIn("--resume", cmd)
        self.assertIn("--transcript-source", cmd)
        self.assertEqual(cmd[cmd.index("--transcript-source") + 1], "raw")


if __name__ == "__main__":
    unittest.main()
