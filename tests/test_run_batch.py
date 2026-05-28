from __future__ import annotations

import os
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(os.getenv("CODEX_TEST_ROOT", str(ROOT / ".codex_tmp" / "tests")))
TEST_ROOT.mkdir(parents=True, exist_ok=True)
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import scripts.run_batch as run_batch


class RunBatchTests(unittest.TestCase):
    def make_xlsx(self, rows: list[tuple[object, object, object]]) -> Path:
        tempdir = TEST_ROOT / "batch" / uuid.uuid4().hex
        tempdir.mkdir(parents=True, exist_ok=True)
        xlsx_path = tempdir / "courses.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["\u5e8f\u53f7", "\u8bfe\u7a0b\u540d\u79f0", "\u89c6\u9891\u94fe\u63a5"])
        for row in rows:
            sheet.append(list(row))
        workbook.save(xlsx_path)
        workbook.close()
        return xlsx_path

    def test_read_tasks_and_filter_rows(self) -> None:
        xlsx_path = self.make_xlsx(
            [
                (1, "Course A/B", "https://example.com/a"),
                (2, "   ", ""),
                (3, "Course B", "https://example.com/b"),
            ]
        )
        tasks = run_batch.read_tasks(xlsx_path, Path(r"D:\video\output"))
        filtered = run_batch.filter_tasks(tasks, start_row=4, end_row=4)

        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].progress_key, "001_Course A_B")
        self.assertEqual(tasks[0].output_dir.name, "001_Course A_B")
        self.assertEqual(filtered[0].seq, 3)

    def test_build_pipeline_command_and_skip_logic(self) -> None:
        task = run_batch.BatchTask(
            row_number=2,
            seq=7,
            title="Course A",
            page_url="https://example.com/page",
            output_dir=TEST_ROOT / "batch" / "007_Course A",
            progress_key="007_Course A",
        )
        cmd = run_batch.build_pipeline_command(task, 900, True)

        self.assertIn("--resume", cmd)
        self.assertIn("--part-seconds", cmd)
        self.assertNotIn("--grade", cmd)
        self.assertNotIn("--subject", cmd)
        self.assertFalse(run_batch.should_skip_task(task, {}, False))
        self.assertIsNone(run_batch.skipped_outline_path(task, False))

    def test_resume_skip_requires_fresh_outline_metadata(self) -> None:
        task = run_batch.BatchTask(
            row_number=2,
            seq=7,
            title="Course A",
            page_url="https://example.com/page",
            output_dir=TEST_ROOT / "batch" / "007_Course A",
            progress_key="007_Course A",
        )
        task.output_dir.mkdir(parents=True, exist_ok=True)
        (task.output_dir / "outline.md").write_text("# stale\n", encoding="utf-8")

        with patch.object(run_batch, "outline_complete", return_value=False):
            self.assertFalse(run_batch.should_skip_task(task, {}, True))
            self.assertIsNone(run_batch.skipped_outline_path(task, True))

    def test_build_pipeline_command_can_forward_skip_llm_flags(self) -> None:
        task = run_batch.BatchTask(
            row_number=2,
            seq=7,
            title="Course A",
            page_url="https://example.com/page",
            output_dir=TEST_ROOT / "batch" / "007_Course A",
            progress_key="007_Course A",
        )

        cmd = run_batch.build_pipeline_command(
            task,
            900,
            True,
            skip_clean=True,
            skip_context_infer=True,
        )

        self.assertIn("--skip-clean", cmd)
        self.assertIn("--skip-context-infer", cmd)

    def test_workers_over_one_rejected_when_llm_steps_active(self) -> None:
        with self.assertRaises(ValueError):
            run_batch.validate_worker_policy(
                workers=2,
                skip_context_infer=True,
                skip_clean=True,
            )
        with self.assertRaises(ValueError):
            run_batch.validate_worker_policy(
                workers=2,
                skip_context_infer=False,
                skip_clean=True,
            )
        run_batch.validate_worker_policy(workers=1, skip_context_infer=False, skip_clean=False)

    def test_load_progress_and_sanitize_title(self) -> None:
        path = TEST_ROOT / "batch" / uuid.uuid4().hex / "batch_progress.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"abc": "done"}', encoding="utf-8")
        progress = run_batch.load_progress(path)

        self.assertEqual(progress["abc"]["status"], "done")
        self.assertEqual(run_batch.sanitize_title('  a/b:c*?"<>| course  '), "a_b_c______ course")


if __name__ == "__main__":
    unittest.main()
