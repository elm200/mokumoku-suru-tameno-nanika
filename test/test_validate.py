import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.validate import (
    MAX_ID,
    MAX_NAME,
    MAX_TEXT,
    clean_hhmm,
    clean_opaque,
    clean_text,
)


class CleanTextTest(unittest.TestCase):
    def test_strips_and_returns(self):
        self.assertEqual(clean_text("  こんにちは  ", MAX_NAME), "こんにちは")

    def test_over_limit_returns_none(self):
        self.assertIsNone(clean_text("a" * (MAX_TEXT + 1), MAX_TEXT))

    def test_at_limit_is_accepted(self):
        self.assertEqual(clean_text("a" * MAX_TEXT, MAX_TEXT), "a" * MAX_TEXT)

    def test_empty_returns_none(self):
        self.assertIsNone(clean_text("", MAX_NAME))
        self.assertIsNone(clean_text("   ", MAX_NAME))

    def test_non_string_returns_none(self):
        self.assertIsNone(clean_text(None, MAX_NAME))
        self.assertIsNone(clean_text(123, MAX_NAME))
        self.assertIsNone(clean_text({"a": 1}, MAX_NAME))

    def test_control_chars_are_removed(self):
        self.assertEqual(clean_text("a\x00b\x07c", MAX_NAME), "abc")

    def test_newline_removed_unless_allowed(self):
        self.assertEqual(clean_text("a\nb", MAX_NAME), "ab")
        self.assertEqual(clean_text("a\nb", MAX_TEXT, allow_newline=True), "a\nb")

    def test_control_chars_do_not_bypass_limit(self):
        # 制御文字を詰めて上限を超えさせようとしても、除去後の長さで判定される
        self.assertEqual(clean_text("\x00" * 100 + "ab", MAX_NAME), "ab")


class CleanOpaqueTest(unittest.TestCase):
    def test_accepts_id_charset(self):
        self.assertEqual(clean_opaque("abc_XYZ-019", MAX_ID), "abc_XYZ-019")

    def test_rejects_other_charset(self):
        for value in ("a b", "a:b", "a/b", "あ", "a.b", "a*"):
            self.assertIsNone(clean_opaque(value, MAX_ID), value)

    def test_rejects_over_limit(self):
        self.assertIsNone(clean_opaque("a" * (MAX_ID + 1), MAX_ID))

    def test_rejects_empty_and_non_string(self):
        self.assertIsNone(clean_opaque("", MAX_ID))
        self.assertIsNone(clean_opaque(None, MAX_ID))
        self.assertIsNone(clean_opaque(12345, MAX_ID))


class CleanHhmmTest(unittest.TestCase):
    def test_accepts_valid(self):
        self.assertEqual(clean_hhmm("09:30"), "09:30")
        self.assertEqual(clean_hhmm("9:05"), "9:05")
        self.assertEqual(clean_hhmm("23:59"), "23:59")

    def test_missing_is_empty_string(self):
        # 終了予定は任意なので、未入力は「不正」ではなく空として通す
        self.assertEqual(clean_hhmm(None), "")
        self.assertEqual(clean_hhmm(""), "")
        self.assertEqual(clean_hhmm("  "), "")

    def test_rejects_out_of_range(self):
        self.assertIsNone(clean_hhmm("24:00"))
        self.assertIsNone(clean_hhmm("12:60"))

    def test_rejects_malformed(self):
        for value in ("930", "9:5", "09:30:00", "aa:bb", "09-30"):
            self.assertIsNone(clean_hhmm(value), value)

    def test_rejects_non_string(self):
        # 以前は .strip() を直接呼んでいたため、数値やdictが来ると500になっていた
        self.assertIsNone(clean_hhmm(930))
        self.assertIsNone(clean_hhmm(["09:30"]))


if __name__ == "__main__":
    unittest.main()
