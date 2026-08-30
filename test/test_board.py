import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.board import decode_chara_image, owner_hash, to_public_entry

# 1x1の透明PNG(有効なPNGマジックバイトを持つ最小データ)
VALID_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class DecodeCharaImageTest(unittest.TestCase):
    def test_valid_data_url_returns_payload(self):
        result = decode_chara_image(f"data:image/png;base64,{VALID_PNG_B64}")
        self.assertEqual(result, VALID_PNG_B64)

    def test_without_prefix_returns_none(self):
        self.assertIsNone(decode_chara_image(VALID_PNG_B64))

    def test_wrong_magic_bytes_returns_none(self):
        not_png = base64.b64encode(b"not a png").decode()
        self.assertIsNone(decode_chara_image(f"data:image/png;base64,{not_png}"))

    def test_non_string_returns_none(self):
        self.assertIsNone(decode_chara_image(None))
        self.assertIsNone(decode_chara_image(123))

    def test_too_long_returns_none(self):
        huge = "A" * 800_000
        self.assertIsNone(decode_chara_image(f"data:image/png;base64,{huge}"))


class OwnerHashTest(unittest.TestCase):
    def test_same_token_gives_same_hash(self):
        self.assertEqual(owner_hash("abc"), owner_hash("abc"))

    def test_different_tokens_differ(self):
        self.assertNotEqual(owner_hash("abc"), owner_hash("abd"))

    def test_raw_token_is_not_recoverable(self):
        # 生のトークンはRedisに置かない。ハッシュ値そのものが元の値と一致しないこと
        self.assertNotIn("abc", owner_hash("abc"))
        self.assertEqual(len(owner_hash("abc")), 64)


class ToPublicEntryTest(unittest.TestCase):
    def test_owner_is_removed(self):
        entry = {"id": "x", "owner": "deadbeef", "name": "A", "room": 1}
        self.assertEqual(to_public_entry(entry), {"id": "x", "name": "A", "room": 1})

    def test_other_fields_survive(self):
        # idは描画に使うので残す。落とすのはownerだけ
        self.assertIn("id", to_public_entry({"id": "x", "owner": "y"}))

    def test_entry_without_owner_is_unchanged(self):
        self.assertEqual(to_public_entry({"id": "x"}), {"id": "x"})


if __name__ == "__main__":
    unittest.main()
