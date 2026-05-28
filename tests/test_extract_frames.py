from __future__ import annotations

import os
import subprocess
import sys
import uuid
import unittest
from pathlib import Path

from scripts.common import find_ffmpeg


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(os.getenv("CODEX_TEST_ROOT", str(ROOT / ".codex_tmp" / "tests")))
TEST_ROOT.mkdir(parents=True, exist_ok=True)
PYTHON = Path(os.environ.get("PYTHON_EXE", r"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"))


class ExtractFramesTests(unittest.TestCase):
    def test_extract_frames_writes_to_shared_unicode_frames_dir(self) -> None:
        workdir = TEST_ROOT / "extract_frames" / uuid.uuid4().hex
        workdir.mkdir(parents=True, exist_ok=True)
        video = workdir / "fixture.avi"
        frames_dir = workdir / "frames_中文"
        ffmpeg = find_ffmpeg()
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=4:size=320x180:rate=1",
                "-c:v",
                "mpeg4",
                "-q:v",
                "2",
                str(video),
            ],
            check=True,
            cwd=str(ROOT),
        )

        env = os.environ.copy()
        env["OUTPUT_DIR"] = str(workdir / "unused_output")
        result = subprocess.run(
            [
                str(PYTHON),
                str(ROOT / "scripts" / "extract_frames.py"),
                "--source",
                str(video),
                "--frames-dir",
                str(frames_dir),
                "--threshold",
                "0.028",
                "--max-seconds",
                "120",
            ],
            check=True,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        frames = sorted(frames_dir.glob("*.jpg"))
        self.assertGreaterEqual(len(frames), 1)
        self.assertIn("saved_count=", result.stdout)
        for frame in frames:
            self.assertGreater(frame.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
