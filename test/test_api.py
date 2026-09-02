"""ルートを通しで動かす検証。Redisはtest/fake_redis.pyのインメモリ実装に差し替える。

純粋関数のテスト(test_validate.py等)では届かない部分——所有権の判定、管理者セッション、
レート制限、配信ペイロードに何が入るか——をここで確認する。
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://example.invalid")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "test-token")
os.environ["ADMIN_PASSWORD"] = "hunter2"
os.environ.pop("DISCORD_WEBHOOK_URL", None)

import fake_redis  # noqa: E402

fake_redis.install()

from fastapi.testclient import TestClient  # noqa: E402

import app as appmod  # noqa: E402

PASSPHRASE = "aikotoba"


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        fake_redis.reset()
        # CookieがSecure指定なので、httpsでないとテストクライアントがCookieを送らない
        self.client = TestClient(appmod.app, base_url="https://testserver")

    def login(self):
        self.assertEqual(self.client.post("/api/admin/login", json={"password": "hunter2"}).status_code, 200)

    def open_session(self, passphrase=PASSPHRASE):
        self.login()
        self.client.post("/api/admin/session", json={"open": True})
        self.client.post(
            "/api/admin/settings", json={"passphrase": passphrase, "securityMode": "very_easy"}
        )

    def join(self, cid, token, name="A", task="読書", passphrase=PASSPHRASE, **extra):
        body = {"id": cid, "token": token, "name": name, "task": task, **extra}
        if passphrase is not None:
            body["passphrase"] = passphrase
        return self.client.post("/api/board/join", json=body)

    def say(self, token, text, passphrase=PASSPHRASE, **extra):
        body = {"text": text, "token": token, **extra}
        if passphrase is not None:
            body["passphrase"] = passphrase
        return self.client.post("/api/messages", json=body)

    def last_snapshot(self):
        return json.loads(fake_redis.PUBLISHED[-1])


class AdminAuthTest(ApiTestCase):
    def test_unauthenticated_is_rejected(self):
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 401)

    def test_wrong_password_is_rejected(self):
        self.assertEqual(self.client.post("/api/admin/login", json={"password": "x"}).status_code, 401)

    def test_cookie_does_not_carry_a_server_secret(self):
        res = self.client.post("/api/admin/login", json={"password": "hunter2"})
        cookie = res.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        # Cookieの値がサーバー側の設定値に由来しないこと
        self.assertNotIn("hunter2", cookie)

    def test_login_then_access(self):
        self.login()
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 200)

    def test_logout_revokes_the_session(self):
        self.login()
        self.client.post("/api/admin/logout")
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 401)


class LoginRateLimitTest(ApiTestCase):
    HEADERS = {"x-vercel-forwarded-for": "203.0.113.9"}

    def test_blocks_after_the_limit(self):
        codes = [
            self.client.post("/api/admin/login", json={"password": "bad"}, headers=self.HEADERS).status_code
            for _ in range(12)
        ]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10:], [429, 429])

    def test_other_ips_are_not_affected(self):
        for _ in range(12):
            self.client.post("/api/admin/login", json={"password": "bad"}, headers=self.HEADERS)
        other = self.client.post(
            "/api/admin/login", json={"password": "bad"},
            headers={"x-vercel-forwarded-for": "198.51.100.1"},
        )
        self.assertEqual(other.status_code, 401)

    def test_unknown_ip_is_not_rate_limited(self):
        # IPが取れないときに全員を1つのバケツに入れると、攻撃者1人で全員を締め出せてしまう
        codes = [self.client.post("/api/admin/login", json={"password": "bad"}).status_code for _ in range(12)]
        self.assertEqual(set(codes), {401})


class BoardOwnershipTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.open_session()

    def test_join_succeeds_and_hides_owner(self):
        res = self.join("alice", "tok-alice")
        self.assertEqual(res.status_code, 201)
        self.assertNotIn("owner", res.json())

    def test_passphrase_is_required(self):
        self.assertEqual(self.join("bob", "tok-bob", passphrase=None).status_code, 401)

    def test_other_token_cannot_hijack_an_entry(self):
        self.join("alice", "tok-alice")
        # idは全員に配信されるので、idを知っているだけでは他人の入室を触れないこと
        res = self.join("alice", "tok-mallory", name="なりすまし")
        self.assertEqual(res.status_code, 403)

    def test_other_token_cannot_force_leave(self):
        self.join("alice", "tok-alice")
        res = self.client.post(
            "/api/board/leave", json={"id": "alice", "token": "tok-mallory", "passphrase": PASSPHRASE}
        )
        self.assertEqual(res.status_code, 403)

    def test_owner_can_update_and_leave(self):
        self.join("alice", "tok-alice")
        self.assertEqual(self.join("alice", "tok-alice", task="執筆").status_code, 201)
        res = self.client.post(
            "/api/board/leave", json={"id": "alice", "token": "tok-alice", "passphrase": PASSPHRASE}
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])


class MessageAuthorTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.open_session()
        self.join("alice", "tok-alice", name="A")

    def test_author_comes_from_the_board_not_the_request(self):
        res = self.say("tok-alice", "こんにちは", name="別人")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["name"], "A")

    def test_unjoined_token_is_rejected(self):
        self.assertEqual(self.say("tok-nobody", "x").status_code, 403)


class InputLimitTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.open_session()
        self.join("alice", "tok-alice")

    def test_text_over_limit_is_rejected(self):
        self.assertEqual(self.say("tok-alice", "a" * 501).status_code, 400)

    def test_text_at_limit_is_accepted(self):
        self.assertEqual(self.say("tok-alice", "a" * 500).status_code, 201)

    def test_name_over_limit_is_rejected(self):
        self.assertEqual(self.join("carol", "tok-carol", name="a" * 41).status_code, 400)

    def test_non_string_time_does_not_500(self):
        self.assertEqual(self.join("dave", "tok-dave", start=930).status_code, 400)

    def test_non_object_body_does_not_500(self):
        self.assertIn(self.client.post("/api/board/join", json=[1, 2, 3]).status_code, (400, 401))

    def test_oversized_body_is_rejected(self):
        res = self.client.post(
            "/api/messages", content=b"x" * 1_000_001, headers={"content-type": "application/json"}
        )
        self.assertEqual(res.status_code, 413)

    def test_malformed_id_is_rejected(self):
        self.assertEqual(self.join("bad id!", "tok-x").status_code, 400)

    def test_history_is_capped(self):
        for i in range(320):
            self.say("tok-alice", f"m{i}")
        messages = self.last_snapshot()["messages"]
        self.assertEqual(len(messages), 300)
        self.assertEqual(messages[-1]["text"], "m319")


class SnapshotTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.open_session()
        self.join("alice", "tok-alice")

    def test_config_is_the_public_subset(self):
        self.assertEqual(set(self.last_snapshot()["config"]), {"open", "roomImage"})

    def test_passphrase_never_leaves(self):
        self.assertNotIn(PASSPHRASE, json.dumps(self.last_snapshot()))

    def test_board_entries_hide_owner(self):
        self.assertTrue(all("owner" not in e for e in self.last_snapshot()["board"]))

    def test_each_write_publishes_a_fresh_snapshot(self):
        before = len(fake_redis.PUBLISHED)
        self.say("tok-alice", "hello")
        self.assertEqual(len(fake_redis.PUBLISHED), before + 1)
        self.assertEqual(self.last_snapshot()["messages"][-1]["text"], "hello")

    def test_snapshot_carries_no_rev(self):
        # 版番号は廃止した。受信側が再び依存し始めないよう固定しておく
        self.assertNotIn("rev", self.last_snapshot())


class RoomImageTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_value_outside_the_whitelist_is_ignored(self):
        self.client.post("/api/admin/settings", json={"roomImage": "../../etc/passwd"})
        self.assertEqual(self.client.get("/api/admin/settings").json()["roomImage"], "room-image-1.png")

    def test_value_inside_the_whitelist_is_accepted(self):
        self.client.post("/api/admin/settings", json={"roomImage": "room-image-2.png"})
        self.assertEqual(self.client.get("/api/admin/settings").json()["roomImage"], "room-image-2.png")


class SessionLifecycleTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_opening_clears_leftover_board_and_messages(self):
        # closeを経由せず、何らかの理由でboard/messagesにデータが残ったままの状態を再現する
        self.open_session()
        self.join("alice", "tok-alice")
        self.say("tok-alice", "residual message")

        res = self.client.post("/api/admin/session", json={"open": True})

        self.assertEqual(res.status_code, 200)
        snapshot = self.last_snapshot()
        self.assertEqual(snapshot["board"], [])
        self.assertEqual(snapshot["messages"], [])


if __name__ == "__main__":
    unittest.main()
