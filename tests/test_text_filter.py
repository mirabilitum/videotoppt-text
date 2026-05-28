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

from scripts.text_filter import (
    adjust_span_to_alias_boundary,
    assert_no_alias_fragments,
    decrypt_text,
    encrypt_text,
    load_sensitive_word_map,
)


class TextFilterTests(unittest.TestCase):
    def test_encrypt_decrypt_round_trip_and_longest_word_first(self) -> None:
        word_map = {
            "习近平": "__SW_0006__",
            "习近平法治思想": "__SW_0003__",
        }
        text = "贯彻习近平法治思想，也提到习近平。"

        encrypted = encrypt_text(text, word_map)

        self.assertIn("__SW_0003__", encrypted)
        self.assertIn("__SW_0006__", encrypted)
        self.assertNotIn("习近平", encrypted)
        self.assertEqual(decrypt_text(encrypted, word_map), text)

    def test_empty_word_map_is_noop(self) -> None:
        text = "普通课程内容"

        self.assertEqual(encrypt_text(text, {}), text)
        self.assertEqual(decrypt_text(text, {}), text)

    def test_repeated_sensitive_words_are_all_replaced(self) -> None:
        word_map = {"总书记": "__SW_0007__"}
        text = "总书记讲话，总书记强调。"

        encrypted = encrypt_text(text, word_map)

        self.assertEqual(encrypted.count("__SW_0007__"), 2)
        self.assertEqual(decrypt_text(encrypted, word_map), text)

    def test_adjust_span_expands_when_boundary_splits_alias(self) -> None:
        alias = "__SW_0003__"
        text = f"前文{alias}后文"
        start = text.index(alias) + 2
        end = text.index(alias) + len(alias) - 1

        adjusted = adjust_span_to_alias_boundary(text, start, end, {alias})

        self.assertEqual(adjusted, (text.index(alias), text.index(alias) + len(alias)))

    def test_alias_fragment_raises(self) -> None:
        word_map = {"习近平法治思想": "__SW_0003__"}

        for fragment in ("__SW_0003", "SW_0003", "SW_0003__"):
            with self.subTest(fragment=fragment):
                with self.assertRaises(RuntimeError):
                    assert_no_alias_fragments(f"模型写成了{fragment}", word_map)

    def test_full_alias_does_not_raise(self) -> None:
        word_map = {"习近平法治思想": "__SW_0003__"}

        assert_no_alias_fragments("模型保留了__SW_0003__。", word_map)

    def test_full_aliases_with_shared_prefix_do_not_raise(self) -> None:
        word_map = {
            "习近平法治思想": "__SW_0003__",
            "习近平强军思想": "__SW_0004__",
        }

        assert_no_alias_fragments(
            "模型保留了__SW_0003__，也保留了__SW_0004__。",
            word_map,
        )

    def test_internal_alias_phrase_does_not_raise(self) -> None:
        word_map = {"政治属性": "某甲课程属性拾捌号"}

        assert_no_alias_fragments("这里正常写到了课程属性。", word_map)

    def test_load_sensitive_word_map_validates_duplicates(self) -> None:
        tempdir = TEST_ROOT / "text_filter" / uuid.uuid4().hex
        tempdir.mkdir(parents=True, exist_ok=True)
        path = tempdir / "sensitive_words.json"
        path.write_text(
            json.dumps({"甲": "__SW_0001__", "乙": "__SW_0001__"}, ensure_ascii=False),
            encoding="utf-8",
        )

        with self.assertRaises(RuntimeError):
            load_sensitive_word_map(path)


if __name__ == "__main__":
    unittest.main()
