from __future__ import annotations

import json
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / ".codex_tmp" / "tests"
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


if __name__ == "__main__":
    unittest.main()
