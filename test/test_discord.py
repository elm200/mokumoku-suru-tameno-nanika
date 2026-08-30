import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.discord import sanitize_display_name


class SanitizeDisplayNameTest(unittest.TestCase):
    def test_plain_name_is_unchanged(self):
        self.assertEqual(sanitize_display_name("さかい"), "さかい")

    def test_markdown_is_stripped(self):
        # "**A**: fake" のような名前で、他人の発言に見せかけられないようにする
        self.assertEqual(sanitize_display_name("**A**"), "A")
        self.assertEqual(sanitize_display_name("a`b`c"), "abc")
        self.assertEqual(sanitize_display_name("a_b_c"), "abc")
        self.assertEqual(sanitize_display_name("a~~b~~"), "ab")
        self.assertEqual(sanitize_display_name("a||b||"), "ab")
        self.assertEqual(sanitize_display_name(r"a\*b"), "ab")

    def test_newlines_become_spaces(self):
        self.assertEqual(sanitize_display_name("a\nb"), "a b")
        self.assertEqual(sanitize_display_name("a\r\nb"), "a  b")

    def test_result_is_stripped(self):
        self.assertEqual(sanitize_display_name("  *A*  "), "A")


if __name__ == "__main__":
    unittest.main()
