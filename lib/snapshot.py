# SSEで配る「現在の全状態」の組み立てと配信。
#
# 以前は publish が「変わった」とだけ伝え、受け取った各クライアントが個別にRedisを
# 読み直していた。そのためUpstashへのコマンド数が (接続数 × イベント数 × 4) で増え、
# 接続をたくさん張って書き込むだけで課金を膨らませられる状態だった。
# いまは書き込んだ側が1回だけスナップショットを作って配るので、接続数に依存しない。
import json
import os

from lib.board import (
    get_public_config,
    parse_board,
    parse_config,
    parse_messages,
    to_public_entry,
)
from lib.redis_client import KEYS, publish_event, redis_pipeline


async def build_snapshot() -> dict:
    """配信用スナップショットを1回のパイプラインで組み立てる。読み取りだけで完結する。

    以前はここで版番号(rev)をINCRし、受信側が古いペイロードを捨てられるようにしていた。
    だがpub/subは単一チャンネル・単一インスタンスなので全購読者が同じ順序で受け取り、
    並べ直す必要が無い。順序逆転を起こしうる唯一の要因だった定期resyncを廃止したので、
    revごと外した(読み取りのつもりの処理が書き込みになる、という筋の悪さも解消する)。
    """
    config_raw, board_flat, messages_raw, discord_raw = await redis_pipeline([
        ["GET", KEYS["config"]],
        ["HGETALL", KEYS["board"]],
        ["LRANGE", KEYS["messages"], "0", "-1"],
        ["GET", KEYS["discord_status"]],
    ])

    config = parse_config(config_raw)
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        discord = "off"
    else:
        discord = "error" if discord_raw == "error" else "on"

    return {
        "config": await get_public_config(config),
        "board": [to_public_entry(e) for e in parse_board(board_flat)],
        "messages": parse_messages(messages_raw),
        "discord": discord,
    }


async def publish_snapshot() -> dict:
    """状態を変えたあとに呼ぶ。組み立てたスナップショットをそのまま購読者に配る"""
    snapshot = await build_snapshot()
    await publish_event(json.dumps(snapshot))
    return snapshot
