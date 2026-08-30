# Upstash Redis REST APIへの薄いラッパー。SDKは使わず、依存を増やさないよう生httpxでコマンドを送る。
# 他アプリと同じUpstashインスタンスを共有することがあるため、全キーに"mokumoku:"を付ける。
#
# SUBSCRIBEだけは長時間つなぎっぱなしの接続が要るため、使い捨てのRESTコマンドとは別扱いになる。
# ここではredis-pyのasyncioクライアントでTCP(RESP)接続を張っている。
# ただし「RESTではSUBSCRIBEできない」わけではない。Upstashは2024-07に
# GET /subscribe/[channel-name](Accept: text/event-stream)をSSEで提供している。
# そちらに寄せればredis-py依存を1つ落とせるが、課金のされ方を未確認のため据え置き。
# UpstashのRedisパスワードはREST TOKENと同じ値が使える(Upstash公式の仕様)。
import os
from urllib.parse import urlparse

import httpx
import redis.asyncio as aioredis

KEY_PREFIX = "mokumoku:"
EVENTS_CHANNEL = f"{KEY_PREFIX}events"

KEYS = {
    "config": f"{KEY_PREFIX}config",
    "board": f"{KEY_PREFIX}board",
    "messages": f"{KEY_PREFIX}messages",
    "img_seq": f"{KEY_PREFIX}img_seq",
    "discord_status": f"{KEY_PREFIX}discord_status",
}


def img_key(cid: str) -> str:
    return f"{KEY_PREFIX}img:{cid}"


def _credentials() -> tuple[str, str]:
    url = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        raise RuntimeError("UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN が設定されていません")
    return url, token


async def redis_command(command: list):
    url, token = _credentials()
    async with httpx.AsyncClient() as client:
        res = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=command,
        )
    data = res.json()
    if data.get("error"):
        raise RuntimeError(f"Upstash Redis error: {data['error']}")
    return data.get("result")


async def redis_pipeline(commands: list[list]):
    url, token = _credentials()
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{url}/pipeline",
            headers={"Authorization": f"Bearer {token}"},
            json=commands,
        )
    data = res.json()
    results = []
    for entry in data:
        if entry.get("error"):
            raise RuntimeError(f"Upstash Redis error: {entry['error']}")
        results.append(entry.get("result"))
    return results


async def publish_event(payload: str) -> None:
    """購読中の全SSE接続に、状態そのもの(スナップショットJSON)を配る。

    以前は「変更を通知するだけ」で、受信側が各自Redisを読み直していた。その形だと
    Upstashへのコマンド数が接続数に比例して増え、接続を増やすだけで課金を膨らませられた。
    """
    await redis_command(["PUBLISH", EVENTS_CHANNEL, payload])


def _pubsub_url() -> str:
    url, token = _credentials()
    host = urlparse(url).hostname
    if not host:
        raise RuntimeError(f"UPSTASH_REDIS_REST_URLからホスト名を取り出せません: {url}")
    return f"rediss://default:{token}@{host}:6379"


async def subscribe_events(idle_timeout: float):
    """状態変化の通知を非同期ジェネレータとしてyieldする。idle_timeout秒メッセージが無ければNoneをyieldする(呼び出し側のping送出・切断確認用)"""
    client = aioredis.from_url(_pubsub_url(), decode_responses=True)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(EVENTS_CHANNEL)
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=idle_timeout)
            yield message
    finally:
        await pubsub.unsubscribe(EVENTS_CHANNEL)
        await pubsub.aclose()
        await client.aclose()
