from __future__ import annotations

import os
import json
import sys
import uuid
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(os.getenv("CODEX_TEST_ROOT", str(ROOT / ".codex_tmp" / "tests")))
TEST_ROOT.mkdir(parents=True, exist_ok=True)
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.infer_course_context import (
    infer_context_complete,
    sample_transcript,
    validate_grade_subject_args,
    write_context_override,
)


class InferCourseContextTests(unittest.TestCase):
    def make_output_dir(self, name: str) -> Path:
        out = TEST_ROOT / "context" / name / uuid.uuid4().hex
        out.mkdir(parents=True, exist_ok=True)
        return out

    def test_sample_transcript_uses_head_and_tail_for_long_text(self) -> None:
        transcript = "A" * 1200 + "MIDDLE" + "B" * 1200

        sample = sample_transcript(transcript, sample_chars=1000)

        self.assertEqual(sample.head, "A" * 1000)
        self.assertEqual(sample.tail, "B" * 1000)
        self.assertNotIn("MIDDLE", sample.text)

    def test_sample_transcript_short_text_is_not_duplicated(self) -> None:
        transcript = "short transcript"

        sample = sample_transcript(transcript, sample_chars=1000)

        self.assertEqual(sample.text, transcript)
        self.assertEqual(sample.head_chars, len(transcript))
        self.assertEqual(sample.tail_chars, 0)

    def test_partial_grade_subject_args_raise(self) -> None:
        with self.assertRaises(ValueError):
            validate_grade_subject_args("grade", None)
        with self.assertRaises(ValueError):
            validate_grade_subject_args(None, "subject")

    def test_context_override_writes_completion_metadata(self) -> None:
        out = self.make_output_dir("override")
        (out / "transcript.txt").write_text("course transcript", encoding="utf-8")
        (out / "course_info.json").write_text(
            json.dumps({"seq": 1, "title": "Title", "page_url": "https://example.com"}),
            encoding="utf-8",
        )

        write_context_override(out, grade="小学二年级", subject="数学")

        payload = json.loads((out / "course_info.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["grade"], "小学二年级")
        self.assertEqual(payload["subject"], "数学")
        self.assertEqual(payload["context_inference"]["source"], "cli_override")
        self.assertTrue(infer_context_complete(out))

    def test_context_complete_false_after_transcript_changes(self) -> None:
        out = self.make_output_dir("sha_change")
        transcript = out / "transcript.txt"
        transcript.write_text("before", encoding="utf-8")
        write_context_override(out, grade="小学二年级", subject="数学")

        transcript.write_text("after", encoding="utf-8")

        self.assertFalse(infer_context_complete(out))


if __name__ == "__main__":
    unittest.main()
