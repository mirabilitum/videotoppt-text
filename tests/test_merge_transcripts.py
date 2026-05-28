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

from scripts.clean_transcript import clean_part_with_client
from scripts.merge_transcripts import merge_clean_complete, merge_clean_parts, merge_raw_parts


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = type("Message", (), {"content": content})()
        self.finish_reason = "stop"


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content

    def create(self, **kwargs: object) -> FakeResponse:
        return FakeResponse(self.content)


class FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = type(
            "Chat",
            (),
            {"completions": FakeCompletions(content)},
        )()


class MergeTranscriptsTests(unittest.TestCase):
    def make_output_dir(self, name: str) -> Path:
        out = TEST_ROOT / "merge" / name / uuid.uuid4().hex
        (out / "transcript_parts").mkdir(parents=True, exist_ok=True)
        return out

    def write_part(self, out: Path, index: int, text: str) -> None:
        parts_dir = out / "transcript_parts"
        (parts_dir / f"transcript_part_{index:03d}.txt").write_text(text, encoding="utf-8")
        (parts_dir / f"transcript_part_{index:03d}.json").write_text(
            json.dumps({"index": index, "text": text}, ensure_ascii=False),
            encoding="utf-8",
        )

    def make_clean_part(self, out: Path, index: int, clean_text: str, prompt: Path) -> None:
        with patch.dict(os.environ, {"CLEAN_PROMPT_PATH": str(prompt)}):
            clean_part_with_client(out, index, FakeClient(clean_text))

    def write_course_info(self, out: Path) -> None:
        (out / "course_info.json").write_text(
            json.dumps({"grade": "\u5c0f\u5b66", "subject": "\u6570\u5b66"}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_raw_merge_ignores_clean_txt_files(self) -> None:
        out = self.make_output_dir("raw_ignores_clean")
        self.write_part(out, 0, "raw zero")
        (out / "transcript_parts" / "transcript_part_000_clean.txt").write_text(
            "clean zero",
            encoding="utf-8",
        )

        merge_raw_parts(out)

        self.assertEqual((out / "transcript.txt").read_text(encoding="utf-8"), "[Part 000]\nraw zero\n")
        self.assertFalse((out / "transcript_clean.json").exists())

    def test_clean_merge_writes_manifest_and_completion(self) -> None:
        out = self.make_output_dir("clean_manifest")
        self.write_course_info(out)
        self.write_part(out, 0, "raw zero")
        self.write_part(out, 1, "raw one")
        prompt = out / "clean_prompt.md"
        prompt.write_text("clean prompt", encoding="utf-8")
        self.make_clean_part(out, 0, "clean zero", prompt)
        self.make_clean_part(out, 1, "clean one", prompt)

        with patch.dict(os.environ, {"CLEAN_PROMPT_PATH": str(prompt)}):
            merge_clean_parts(out)

            clean_text = (out / "transcript_clean.txt").read_text(encoding="utf-8")
            self.assertEqual(clean_text, "[Part 000]\nclean zero\n\n[Part 001]\nclean one\n")
            manifest = json.loads((out / "transcript_clean_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source"], "clean_parts")
            self.assertEqual(len(manifest["parts"]), 2)
            self.assertEqual(manifest["parts"][0]["raw_path"], "transcript_parts/transcript_part_000.txt")
            self.assertEqual(manifest["parts"][0]["clean_path"], "transcript_parts/transcript_part_000_clean.txt")
            self.assertTrue(merge_clean_complete(out))
            self.assertFalse((out / "transcript_clean.json").exists())

    def test_clean_merge_completion_false_after_clean_file_changes(self) -> None:
        out = self.make_output_dir("clean_change")
        self.write_course_info(out)
        self.write_part(out, 0, "raw zero")
        prompt = out / "clean_prompt.md"
        prompt.write_text("clean prompt", encoding="utf-8")
        self.make_clean_part(out, 0, "clean zero", prompt)

        with patch.dict(os.environ, {"CLEAN_PROMPT_PATH": str(prompt)}):
            merge_clean_parts(out)
            (out / "transcript_parts" / "transcript_part_000_clean.txt").write_text(
                "changed\n",
                encoding="utf-8",
            )

            self.assertFalse(merge_clean_complete(out))

    def test_clean_merge_completion_false_after_merged_file_changes(self) -> None:
        out = self.make_output_dir("merged_change")
        self.write_course_info(out)
        self.write_part(out, 0, "raw zero")
        prompt = out / "clean_prompt.md"
        prompt.write_text("clean prompt", encoding="utf-8")
        self.make_clean_part(out, 0, "clean zero", prompt)

        with patch.dict(os.environ, {"CLEAN_PROMPT_PATH": str(prompt)}):
            merge_clean_parts(out)
            (out / "transcript_clean.txt").write_text("changed\n", encoding="utf-8")

            self.assertFalse(merge_clean_complete(out))

    def test_clean_merge_refuses_missing_clean_part(self) -> None:
        out = self.make_output_dir("missing_clean")
        self.write_part(out, 0, "raw zero")

        with self.assertRaises(FileNotFoundError):
            merge_clean_parts(out)


if __name__ == "__main__":
    unittest.main()
