# IPあたりの試行回数制限。現状は管理者ログインの総当たり対策にのみ使う。
#
# 重要: クライアントIPが特定できない場合は「制限しない」側に倒す。
# ここで「不明なら全部同じバケツ」にすると、攻撃者1人の失敗で全員が締め出される
# (レート制限そのものがDoSの手段になる)ため。
from fastapi import Request

from lib.redis_client import KEY_PREFIX, redis_command, redis_pipeline

LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SECONDS = 900  # 15分


def client_ip(request: Request) -> str | None:
    """Vercelが付与するヘッダからクライアントIPを取る。
    クライアントが自由に詐称できる x-forwarded-for は使わない。"""
    for header in ("x-vercel-forwarded-for", "x-real-ip"):
        value = request.headers.get(header)
        if value:
            # 念のため先頭要素だけを使い、Redisキーに使えない文字は弾く
            candidate = value.split(",")[0].strip()
            if candidate and len(candidate) <= 64 and " " not in candidate:
                return candidate
    return None


def _key(bucket: str, ip: str) -> str:
    return f"{KEY_PREFIX}ratelimit:{bucket}:{ip}"


async def is_blocked(bucket: str, ip: str | None, limit: int) -> bool:
    if ip is None:
        return False
    raw = await redis_command(["GET", _key(bucket, ip)])
    try:
        return int(raw) >= limit
    except (TypeError, ValueError):
        return False


async def record_failure(bucket: str, ip: str | None, window: int) -> None:
    """失敗を1回数える。EXPIREを毎回打ち直すので、叩き続ける相手はブロックが伸びる"""
    if ip is None:
        return
    key = _key(bucket, ip)
    await redis_pipeline([["INCR", key], ["EXPIRE", key, str(window)]])


async def clear(bucket: str, ip: str | None) -> None:
    """認証に成功したらカウンタを捨てる"""
    if ip is None:
        return
    await redis_command(["DEL", _key(bucket, ip)])
