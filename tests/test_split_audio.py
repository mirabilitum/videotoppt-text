from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

from scripts.common import find_ffmpeg


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(os.getenv("CODEX_TEST_ROOT", str(ROOT / ".codex_tmp" / "tests")))
PYTHON = Path(os.environ.get("PYTHON_EXE", r"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"))


class SplitAudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workdir = TEST_ROOT / "split_audio"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = find_ffmpeg()
        self.env = os.environ.copy()
        self.env["OUTPUT_DIR"] = str(self.workdir)
        self.env["PYTHONIOENCODING"] = "utf-8"

    def make_audio(self) -> Path:
        source = self.workdir / "audio.wav"
        subprocess.run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(source),
            ],
            check=True,
            cwd=str(ROOT),
            env=self.env,
        )
        return source

    def run_split(self, part_seconds: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(PYTHON),
                str(ROOT / "scripts" / "split_audio.py"),
                "--part-seconds",
                str(part_seconds),
            ],
            check=True,
            cwd=str(ROOT),
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_part_seconds_zero_copies_single_part(self) -> None:
        source = self.make_audio()
        result = self.run_split(0)
        parts = sorted((self.workdir / "audio_parts").glob("audio_part_*.wav"))

        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].name, "audio_part_000.wav")
        self.assertEqual(parts[0].stat().st_size, source.stat().st_size)
        self.assertIn("part_seconds=0", result.stdout)

    def test_part_seconds_900_keeps_single_short_clip(self) -> None:
        self.make_audio()
        result = self.run_split(900)
        parts = sorted((self.workdir / "audio_parts").glob("audio_part_*.wav"))

        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].name, "audio_part_000.wav")
        self.assertGreater(parts[0].stat().st_size, 0)
        self.assertIn("part_seconds=900", result.stdout)


if __name__ == "__main__":
    unittest.main()
