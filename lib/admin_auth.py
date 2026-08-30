# 管理者向けのCookie認証。参加者用の合言葉(lib/participant_auth.py)とは完全に別系統。
#
# Cookieに入れるのはログインのたびに作り直す使い捨てのセッションIDで、
# サーバー側の固定シークレットではない。固定値を入れると、Cookieの漏洩が
# そのまま設定シークレットの漏洩になり、ログアウトによる個別失効もできない。
import hashlib
import secrets

from fastapi import Request

from lib.redis_client import KEY_PREFIX, redis_command

ADMIN_COOKIE_NAME = "mokumokuAdminSession"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7日


def _session_key(session_id: str) -> str:
    # Redisには生のセッションIDではなくハッシュを置く。
    # Upstashのデータが見えても、そこから使えるCookieは作れない。
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    return f"{KEY_PREFIX}admin_sess:{digest}"


async def create_admin_session() -> str:
    session_id = secrets.token_urlsafe(32)
    await redis_command(["SET", _session_key(session_id), "1", "EX", str(SESSION_TTL_SECONDS)])
    return session_id


async def is_request_admin_authenticated(request: Request) -> bool:
    session_id = request.cookies.get(ADMIN_COOKIE_NAME)
    if not session_id:
        return False
    return await redis_command(["GET", _session_key(session_id)]) == "1"


async def destroy_admin_session(request: Request) -> None:
    session_id = request.cookies.get(ADMIN_COOKIE_NAME)
    if session_id:
        await redis_command(["DEL", _session_key(session_id)])


def build_admin_auth_cookie(session_id: str) -> str:
    return (
        f"{ADMIN_COOKIE_NAME}={session_id}; HttpOnly; Secure; SameSite=Lax; "
        f"Path=/; Max-Age={SESSION_TTL_SECONDS}"
    )


def build_admin_logout_cookie() -> str:
    return f"{ADMIN_COOKIE_NAME}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0"
