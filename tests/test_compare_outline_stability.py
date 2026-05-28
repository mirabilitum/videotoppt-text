from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(os.getenv("CODEX_TEST_ROOT", str(ROOT / ".codex_tmp" / "tests")))
TEST_ROOT.mkdir(parents=True, exist_ok=True)
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.compare_outline_stability import compare_outlines


class CompareOutlineStabilityTests(unittest.TestCase):
    def make_dir(self, name: str) -> Path:
        out = TEST_ROOT / "outline_stability" / name / uuid.uuid4().hex
        out.mkdir(parents=True, exist_ok=True)
        return out

    def test_heading_and_body_similarity_are_separate(self) -> None:
        out = self.make_dir("separate_metrics")
        first = out / "first.md"
        second = out / "second.md"
        first.write_text("# Title\n\n## A\n\nsame body\n", encoding="utf-8")
        second.write_text("# Title\n\n## B\n\nsame body\n", encoding="utf-8")

        result = compare_outlines(first, second)

        self.assertLess(result.heading_similarity, 1.0)
        self.assertEqual(result.body_similarity, 1.0)


if __name__ == "__main__":
    unittest.main()
