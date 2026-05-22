from __future__ import annotations

import json
import sys
import uuid
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / ".codex_tmp" / "tests"
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
            "习近平": "某甲课程人物陆号",
            "习近平法治思想": "某甲课程理念叁号",
        }
        text = "贯彻习近平法治思想，也提到习近平。"

        encrypted = encrypt_text(text, word_map)

        self.assertIn("某甲课程理念叁号", encrypted)
        self.assertIn("某甲课程人物陆号", encrypted)
        self.assertNotIn("习近平", encrypted)
        self.assertEqual(decrypt_text(encrypted, word_map), text)

    def test_empty_word_map_is_noop(self) -> None:
        text = "普通课程内容"

        self.assertEqual(encrypt_text(text, {}), text)
        self.assertEqual(decrypt_text(text, {}), text)

    def test_repeated_sensitive_words_are_all_replaced(self) -> None:
        word_map = {"总书记": "某甲课程职务柒号"}
        text = "总书记讲话，总书记强调。"

        encrypted = encrypt_text(text, word_map)

        self.assertEqual(encrypted.count("某甲课程职务柒号"), 2)
        self.assertEqual(decrypt_text(encrypted, word_map), text)

    def test_adjust_span_expands_when_boundary_splits_alias(self) -> None:
        alias = "某甲课程理念叁号"
        text = f"前文{alias}后文"
        start = text.index(alias) + 2
        end = text.index(alias) + len(alias) - 1

        adjusted = adjust_span_to_alias_boundary(text, start, end, {alias})

        self.assertEqual(adjusted, (text.index(alias), text.index(alias) + len(alias)))

    def test_alias_fragment_raises(self) -> None:
        word_map = {"习近平法治思想": "某甲课程理念叁号"}

        with self.assertRaises(RuntimeError):
            assert_no_alias_fragments("模型写成了某甲课程理念", word_map)

    def test_full_alias_does_not_raise(self) -> None:
        word_map = {"习近平法治思想": "某甲课程理念叁号"}

        assert_no_alias_fragments("模型保留了某甲课程理念叁号。", word_map)

    def test_full_aliases_with_shared_prefix_do_not_raise(self) -> None:
        word_map = {
            "习近平法治思想": "某甲课程理念叁号",
            "习近平强军思想": "某甲课程理念肆号",
        }

        assert_no_alias_fragments(
            "模型保留了某甲课程理念叁号，也保留了某甲课程理念肆号。",
            word_map,
        )

    def test_load_sensitive_word_map_validates_duplicates(self) -> None:
        tempdir = TEST_ROOT / "text_filter" / uuid.uuid4().hex
        tempdir.mkdir(parents=True, exist_ok=True)
        path = tempdir / "sensitive_words.json"
        path.write_text(
            json.dumps({"甲": "某甲课程人物", "乙": "某甲课程人物"}, ensure_ascii=False),
            encoding="utf-8",
        )

        with self.assertRaises(RuntimeError):
            load_sensitive_word_map(path)


if __name__ == "__main__":
    unittest.main()
