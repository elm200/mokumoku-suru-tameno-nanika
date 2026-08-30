# Discord Webhook連携(任意)。server.pyのpost_to_discord相当。
# 直近の送信結果をUpstashに保存し、画面のバッジ表示(オン/オフ/エラー)に使う。
import os
import re

import httpx

from lib.redis_client import KEYS, redis_command

TIMEOUT_SECONDS = 5.0

# Discordの書式記号。表示名に混ぜられると「誰の発言か」を偽装できるので落とす
_MARKDOWN = re.compile(r"[`*_~|\\]")


def sanitize_display_name(name: str) -> str:
    """Discordに出す発言者名。書式崩しと改行を潰す"""
    return _MARKDOWN.sub("", name).replace("\n", " ").replace("\r", " ").strip()


async def post_to_discord(content: str) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            res = await client.post(
                url,
                headers={"Content-Type": "application/json", "User-Agent": "mokumoku-bot/1.0"},
                # parse: [] にすると @everyone / @here / ロールメンションが一切発火しない。
                # このアプリは実質的に不特定多数からの投稿をDiscordに転送するので、
                # これが無いと主催者のサーバー全員を鳴らす手段になる。
                json={"content": content, "allowed_mentions": {"parse": []}},
            )
        await redis_command(["SET", KEYS["discord_status"], "ok" if res.is_success else "error"])
    except httpx.HTTPError:
        await redis_command(["SET", KEYS["discord_status"], "error"])


async def get_discord_status() -> str:
    """"off"=URL未設定 / "on"=設定済み / "error"=直近の送信が失敗(URL失効など)"""
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        return "off"
    status = await redis_command(["GET", KEYS["discord_status"]])
    return "error" if status == "error" else "on"
