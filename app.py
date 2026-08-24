"""もくもく会チャットのVercel + Upstash版(FastAPI)。
全APIルートをこの単一ファイルに集約している(mainブランチのserver.pyに近い単一ファイル構成に合わせるため)。
"""
import base64
import binascii
import json
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from lib.admin_auth import (
    build_admin_auth_cookie,
    build_admin_logout_cookie,
    create_admin_session,
    destroy_admin_session,
    is_request_admin_authenticated,
)
from lib.board import (
    append_message,
    close_session,
    find_entry_by_token,
    get_chara_image,
    get_config,
    join_board,
    leave_board,
    open_session,
    set_config,
)
from lib.compare import timing_safe_string_equal
from lib.participant_auth import check_passphrase
from lib.rate_limit import (
    LOGIN_MAX_FAILURES,
    LOGIN_WINDOW_SECONDS,
    clear as clear_rate_limit,
    client_ip,
    is_blocked,
    record_failure,
)
from lib.redis_client import subscribe_events
from lib.snapshot import build_snapshot, publish_snapshot
from lib.validate import (
    MAX_BODY_BYTES,
    MAX_ID,
    MAX_NAME,
    MAX_TASK,
    MAX_TEXT,
    MAX_TOKEN,
    clean_hhmm,
    clean_opaque,
    clean_text,
)

# 自動生成ドキュメント(/docs, /redoc, /openapi.json)は無効にする。
# public/にマッチしないパスは全てこのアプリに来るため、既定のままだと管理APIを含む
# 全ルート一覧が誰でも取得できる状態になる(実際に本番で公開されていた)。
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# subscribeのソケット読み取りに掛ける締め切り。購読の間隔ではない(購読は接続時の1回きり)。
# この秒数だけ待って何も届かなければNoneが返り、そこで初めて切断確認・経過時間の判定・
# keep-aliveのping送出ができる。つまりこのループが目を覚ます唯一のタイミング。
# 待っている間はRedisにコマンドを送らないので、Upstashのコマンド数は消費しない。
PING_INTERVAL_SEC = 15
# Vercelの既定実行時間上限(300秒)より手前で自発的に切る。EventSourceがブラウザ側で自動再接続する。
# pub/subはat-most-onceだが、取りこぼしてもこの再接続時のスナップショットで必ず追いつく。
# 以前あった定期resyncは、これと同じ役目を1接続あたり毎分5コマンド払って前倒ししていただけなので廃止した。
MAX_DURATION_SEC = 280

ADMIN_LOGIN_BUCKET = "admin_login"

# 部屋画像は public/assets/ に実在するファイルだけを許す(管理画面のselectと同じ集合)。
# 以前はAPIが任意の文字列を受けて保存していた。
ROOM_IMAGE_OPTIONS = ("room-image-1.png", "room-image-2.png")


def _closed_response() -> JSONResponse:
    return JSONResponse({"closed": True})


def _auth_required_response() -> JSONResponse:
    return JSONResponse({"error": "wrong passphrase", "authRequired": True}, status_code=401)


def _forbidden_response() -> JSONResponse:
    return JSONResponse({"error": "forbidden", "forbidden": True}, status_code=403)


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """巨大なボディを本文を読む前に弾く。
    Content-Lengthが無い(chunked)場合は素通りするが、Vercel側でも4.5MBで頭打ちになる。"""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return JSONResponse({"error": "request too large"}, status_code=413)
    return await call_next(request)


async def _json_body(request: Request) -> dict:
    """JSONオブジェクト以外(配列・数値・壊れたJSON)が来ても落ちないようにする"""
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


@app.post("/api/messages")
async def post_message(request: Request):
    body = await _json_body(request)
    config = await get_config()
    if not config["open"]:
        return _closed_response()
    if not check_passphrase(config, body):
        return _auth_required_response()

    token = clean_opaque(body.get("token"), MAX_TOKEN)
    text = clean_text(body.get("text"), MAX_TEXT, allow_newline=True)
    if not token:
        return _bad_request("token required")
    if not text:
        return _bad_request(f"text required (max {MAX_TEXT} chars)")

    # 発言者名はクライアントの申告ではなく、入室エントリから引く。
    # そうしないと誰でも他人の名前で書き込めてしまう(Discordにもその名前で流れる)。
    entry = await find_entry_by_token(token)
    if entry is None:
        return _forbidden_response()

    msg = await append_message(False, text, entry["name"])
    await publish_snapshot()
    return JSONResponse(msg, status_code=201)


@app.post("/api/board/join")
async def board_join(request: Request):
    body = await _json_body(request)
    config = await get_config()
    if not config["open"]:
        return _closed_response()
    if not check_passphrase(config, body):
        return _auth_required_response()

    cid = clean_opaque(body.get("id"), MAX_ID)
    token = clean_opaque(body.get("token"), MAX_TOKEN)
    name = clean_text(body.get("name"), MAX_NAME)
    task = clean_text(body.get("task"), MAX_TASK)
    start = clean_hhmm(body.get("start"))
    end = clean_hhmm(body.get("end"))
    if not cid or not token:
        return _bad_request("id and token required")
    if not name:
        return _bad_request(f"name required (max {MAX_NAME} chars)")
    if not task:
        return _bad_request(f"task required (max {MAX_TASK} chars)")
    if start is None or end is None:
        return _bad_request("start and end must be HH:MM")

    result = await join_board(
        cid, token, name=name, task=task, start=start, end=end, image=body.get("image")
    )
    if result.get("forbidden"):
        return _forbidden_response()
    if "error" in result:
        return JSONResponse(result, status_code=400)
    await publish_snapshot()
    return JSONResponse(result, status_code=201)


@app.post("/api/board/leave")
async def board_leave(request: Request):
    body = await _json_body(request)
    config = await get_config()
    if not config["open"]:
        return _closed_response()
    if not check_passphrase(config, body):
        return _auth_required_response()

    cid = clean_opaque(body.get("id"), MAX_ID)
    token = clean_opaque(body.get("token"), MAX_TOKEN)
    if not cid or not token:
        return _bad_request("id and token required")

    result = await leave_board(cid, token)
    if result.get("forbidden"):
        return _forbidden_response()
    await publish_snapshot()
    return JSONResponse(result)


@app.get("/api/chara-custom")
async def chara_custom(id: str = ""):
    cid = clean_opaque(id, MAX_ID)
    if not cid:
        return JSONResponse({"error": "id required"}, status_code=400)
    b64 = await get_chara_image(cid)
    if not b64:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        raw = base64.b64decode(b64)
    except (ValueError, binascii.Error):
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(
        content=raw,
        media_type="image/png",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=86400",
        },
    )


@app.post("/api/admin/login")
async def admin_login(request: Request):
    ip = client_ip(request)
    if await is_blocked(ADMIN_LOGIN_BUCKET, ip, LOGIN_MAX_FAILURES):
        return JSONResponse(
            {"error": "試行回数が多すぎます。しばらく待ってからやり直してください"},
            status_code=429,
        )

    body = await _json_body(request)
    password = body.get("password")
    expected = os.environ.get("ADMIN_PASSWORD")
    if not expected:
        return JSONResponse({"error": "サーバー側の管理者認証設定がありません"}, status_code=500)
    if not isinstance(password, str) or not timing_safe_string_equal(password, expected):
        await record_failure(ADMIN_LOGIN_BUCKET, ip, LOGIN_WINDOW_SECONDS)
        return JSONResponse({"error": "パスワードが違います"}, status_code=401)

    await clear_rate_limit(ADMIN_LOGIN_BUCKET, ip)
    session_id = await create_admin_session()
    response = JSONResponse({"ok": True})
    response.headers["Set-Cookie"] = build_admin_auth_cookie(session_id)
    return response


@app.post("/api/admin/logout")
async def admin_logout(request: Request):
    await destroy_admin_session(request)
    response = JSONResponse({"ok": True})
    response.headers["Set-Cookie"] = build_admin_logout_cookie()
    return response


@app.post("/api/admin/session")
async def admin_session(request: Request):
    if not await is_request_admin_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await _json_body(request)
    config = await open_session() if body.get("open") is True else await close_session()
    await publish_snapshot()
    return JSONResponse(config)


@app.get("/api/admin/settings")
async def admin_settings_get(request: Request):
    if not await is_request_admin_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(await get_config())


@app.post("/api/admin/settings")
async def admin_settings_post(request: Request):
    if not await is_request_admin_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await _json_body(request)
    patch = {}
    if isinstance(body.get("passphrase"), str):
        patch["passphrase"] = body["passphrase"].strip()
    if body.get("roomImage") in ROOM_IMAGE_OPTIONS:
        patch["roomImage"] = body["roomImage"]
    if body.get("securityMode") in ("very_easy", "none"):
        patch["securityMode"] = body["securityMode"]
    config = await set_config(patch)
    await publish_snapshot()
    return JSONResponse(config)



async def _event_generator(request: Request):
    # 接続直後(初回ログイン・リロード含む)は必ず現在の全状態を1回配信する
    try:
        yield f"data: {json.dumps(await build_snapshot())}\n\n"
    except Exception:
        pass

    started_at = time.monotonic()
    async for message in subscribe_events(idle_timeout=PING_INTERVAL_SEC):
        if await request.is_disconnected():
            break
        if time.monotonic() - started_at > MAX_DURATION_SEC:
            break

        if message is None:
            # PING_INTERVAL_SEC秒、状態変化の通知が無かった(接続維持のためのping)
            yield ": ping\n\n"
            continue

        # publish_snapshotが配ったJSONをそのまま流す(パースし直す必要が無い)。
        # json.dumpsは文字列中の改行を\nにエスケープするためペイロードは必ず1行に収まり、
        # SSEの\n\n区切りを壊さない。publish側を整形出力(indent=)に変えるとここが壊れる。
        data = message.get("data")
        if not isinstance(data, str):
            continue
        yield f"data: {data}\n\n"


@app.get("/api/events")
async def events(request: Request):
    return StreamingResponse(
        _event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# 静的ファイル(public/配下)はVercelが自動でゼロコンフィグ静的配信するため、FastAPI側でのマウントは不要
# (StaticFilesでマウントすると、public/がPython関数バンドルに含まれずimportエラーになる)。
