from __future__ import annotations

import os
import uuid
import unittest
from pathlib import Path

import cv2
import numpy as np

from scripts.common import write_cv_image


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(os.getenv("CODEX_TEST_ROOT", str(ROOT / ".codex_tmp" / "tests")))
TEST_ROOT.mkdir(parents=True, exist_ok=True)


class WriteCvImageTests(unittest.TestCase):
    def test_write_cv_image_supports_unicode_paths(self) -> None:
        base = TEST_ROOT / "common" / uuid.uuid4().hex / "中文路径写入"
        target = base / "frame_0000m05s_0000.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)

        image = np.zeros((48, 64, 3), dtype=np.uint8)
        write_cv_image(target, image, [cv2.IMWRITE_JPEG_QUALITY, 90])

        self.assertTrue(target.exists())
        self.assertGreater(target.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
