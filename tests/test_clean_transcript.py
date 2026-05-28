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

from scripts.clean_transcript import (
    clean_complete,
    clean_part_with_client,
    prompt_sha256,
)


class FakeChoice:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.message = type("Message", (), {"content": content})()
        self.finish_reason = finish_reason


class FakeResponse:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.choices = [FakeChoice(content, finish_reason)]


class FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse(self.content)


class FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = type(
            "Chat",
            (),
            {"completions": FakeCompletions(content)},
        )()


class CleanTranscriptTests(unittest.TestCase):
    def make_output_dir(self, name: str) -> Path:
        out = TEST_ROOT / "clean" / name / uuid.uuid4().hex
        (out / "transcript_parts").mkdir(parents=True, exist_ok=True)
        return out

    def make_prompt(self, out: Path, content: str = "clean prompt") -> Path:
        prompt = out / "clean_prompt.md"
        prompt.write_text(content, encoding="utf-8")
        return prompt

    def write_course_info(self, out: Path) -> None:
        (out / "course_info.json").write_text(
            json.dumps(
                {
                    "grade": "\u5c0f\u5b66\u4e8c\u5e74\u7ea7",
                    "subject": "\u6570\u5b66",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_clean_part_writes_clean_file_and_meta(self) -> None:
        out = self.make_output_dir("write")
        self.write_course_info(out)
        raw = out / "transcript_parts" / "transcript_part_000.txt"
        raw.write_text("\u55ef\uff0c\u6211\u4eec\u6765\u770b\u4e58\u6cd5\u53e3\u8bc0\u3002", encoding="utf-8")
        prompt = self.make_prompt(out)

        with patch.dict(os.environ, {"CLEAN_PROMPT_PATH": str(prompt)}):
            clean_part_with_client(
                out,
                0,
                FakeClient("\u6211\u4eec\u6765\u770b\u4e58\u6cd5\u53e3\u8bc0\u3002"),
            )

            clean_path = out / "transcript_parts" / "transcript_part_000_clean.txt"
            meta_path = out / "transcript_parts" / "transcript_part_000_clean.meta.json"
            self.assertEqual(
                clean_path.read_text(encoding="utf-8"),
                "\u6211\u4eec\u6765\u770b\u4e58\u6cd5\u53e3\u8bc0\u3002\n",
            )
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta["index"], 0)
            self.assertEqual(meta["grade"], "\u5c0f\u5b66\u4e8c\u5e74\u7ea7")
            self.assertEqual(meta["subject"], "\u6570\u5b66")
            self.assertEqual(meta["clean_prompt_sha256"], prompt_sha256())
            self.assertTrue(clean_complete(out, 0))

    def test_clean_complete_false_when_raw_changes(self) -> None:
        out = self.make_output_dir("raw_change")
        self.write_course_info(out)
        raw = out / "transcript_parts" / "transcript_part_000.txt"
        raw.write_text("before", encoding="utf-8")
        prompt = self.make_prompt(out)
        with patch.dict(os.environ, {"CLEAN_PROMPT_PATH": str(prompt)}):
            clean_part_with_client(out, 0, FakeClient("cleaned"))

            raw.write_text("after", encoding="utf-8")

            self.assertFalse(clean_complete(out, 0))

    def test_clean_complete_false_when_prompt_changes(self) -> None:
        out = self.make_output_dir("prompt_change")
        self.write_course_info(out)
        raw = out / "transcript_parts" / "transcript_part_000.txt"
        raw.write_text("raw", encoding="utf-8")
        prompt = self.make_prompt(out, "prompt one")
        with patch.dict(os.environ, {"CLEAN_PROMPT_PATH": str(prompt)}):
            clean_part_with_client(out, 0, FakeClient("cleaned"))

            prompt.write_text("prompt two", encoding="utf-8")

            self.assertFalse(clean_complete(out, 0))

    def test_clean_failure_does_not_leave_partial_clean_file(self) -> None:
        out = self.make_output_dir("failure")
        self.write_course_info(out)
        raw = out / "transcript_parts" / "transcript_part_000.txt"
        raw.write_text("raw", encoding="utf-8")
        prompt = self.make_prompt(out)

        class FailingCompletions:
            def create(self, **kwargs: object) -> FakeResponse:
                raise RuntimeError("boom")

        client = type(
            "Client",
            (),
            {"chat": type("Chat", (), {"completions": FailingCompletions()})()},
        )()
        with patch.dict(os.environ, {"CLEAN_PROMPT_PATH": str(prompt)}):
            with self.assertRaises(RuntimeError):
                clean_part_with_client(out, 0, client)

        self.assertFalse((out / "transcript_parts" / "transcript_part_000_clean.txt").exists())
        self.assertFalse((out / "transcript_parts" / "transcript_part_000_clean.meta.json").exists())


if __name__ == "__main__":
    unittest.main()
