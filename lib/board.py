# もくもく会の状態(設定・入退室・メッセージ)を扱うドメインロジック。
# 元のFlask版(server.py)のグローバル変数dictをUpstashに置き換えたもの。
#
# このモジュールは状態を変えるだけで、SSEへの配信(publish)は行わない。
# 配信はlib/snapshot.pyが担当し、ルート側が「変更 → 配信」の順で呼ぶ。
# (board → snapshot → board の循環importを避けるため)
import base64
import binascii
import hashlib
import json
import random
import re
import time
from datetime import datetime
from typing import Any, TypedDict

from lib.discord import post_to_discord, sanitize_display_name
from lib.redis_client import KEYS, img_key, redis_command, redis_pipeline

ROOM_COUNT = 9
MAX_IMAGE_B64 = 700_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# チャット履歴の保持件数。これを超えた古いものから捨てる。
# 上限が無いと、履歴がそのまま配信ペイロードのサイズになり、書き込みだけで無制限に膨らませられる。
MAX_MESSAGES = 300


class Config(TypedDict):
    open: bool
    passphrase: str
    roomImage: str
    securityMode: str  # "very_easy" | "none"


DEFAULT_CONFIG: Config = {
    "open": False,
    "passphrase": "",
    "roomImage": "room-image-1.png",
    "securityMode": "very_easy",
}


def _now_hhmm() -> str:
    now = datetime.now()
    return f"{now.hour:02d}:{now.minute:02d}"


def _now_datetime() -> str:
    now = datetime.now()
    return f"{now.year:04d}-{now.month:02d}-{now.day:02d} {_now_hhmm()}"


def _today_local_date_str() -> str:
    now = datetime.now()
    return f"{now.year:04d}-{now.month:02d}-{now.day:02d}"


def owner_hash(token: str) -> str:
    """入室エントリの所有者を表す値。生のトークンはRedisにもクライアントにも出さない。

    公開されるidと違い、tokenは本人のブラウザだけが持つ。idは全員に配信されるので、
    idを所有権の根拠にすると「見えている値を送るだけ」で他人の入室を操作できてしまう。
    """
    return hashlib.sha256(token.encode()).hexdigest()


def decode_chara_image(image: Any) -> str | None:
    """クライアントがcanvasで縮小・PNG化したデータURLを検証し、base64ペイロードを返す。不正ならNone"""
    prefix = "data:image/png;base64,"
    if not isinstance(image, str) or not image.startswith(prefix) or len(image) > MAX_IMAGE_B64:
        return None
    b64 = image[len(prefix):]
    try:
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not raw.startswith(PNG_MAGIC):
        return None
    return b64


# --- Redisの生レスポンス → ドメインの値 への変換 ---------------------------------
# ここを唯一の組み立て場所にしておく。lib/snapshot.pyは自前でパースせず、
# 1回のパイプラインで読んだ生データをこの関数群に渡す。

def parse_config(raw: Any) -> Config:
    if not raw:
        return dict(DEFAULT_CONFIG)  # type: ignore[return-value]
    return {**DEFAULT_CONFIG, **json.loads(raw)}


def parse_board(flat: Any) -> list[dict]:
    entries = []
    if isinstance(flat, list):
        for i in range(0, len(flat), 2):
            entries.append(json.loads(flat[i + 1]))
    # Redisのハッシュはフィールドの並び順を保証しないため、入室時刻の降順(新しい順)で安定させる
    entries.sort(key=lambda e: e.get("joinedAt") or 0, reverse=True)
    return entries


def parse_messages(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    return [json.loads(m) for m in raw]


def to_public_entry(entry: dict) -> dict:
    """配信用。所有者ハッシュを落とす(get_public_configと同じ趣旨)"""
    return {k: v for k, v in entry.items() if k != "owner"}


async def get_config() -> Config:
    return parse_config(await redis_command(["GET", KEYS["config"]]))


async def get_public_config(config: Config | None = None) -> dict:
    """一般ユーザー(SSE配信含む)向けに安全なフィールドだけを返す。passphrase・securityModeは管理者専用"""
    if config is None:
        config = await get_config()
    return {"open": config["open"], "roomImage": config["roomImage"]}


async def set_config(patch: dict) -> Config:
    current = await get_config()
    next_config = {**current, **patch}
    await redis_command(["SET", KEYS["config"], json.dumps(next_config)])
    return next_config  # type: ignore[return-value]


async def list_board() -> list[dict]:
    """内部用。ownerを含むので、そのまま配信してはいけない"""
    return parse_board(await redis_command(["HGETALL", KEYS["board"]]))


async def find_entry_by_token(token: str) -> dict | None:
    owner = owner_hash(token)
    return next((e for e in await list_board() if e.get("owner") == owner), None)


async def append_message(system: bool, text: str, name: str = "") -> dict:
    if system:
        msg = {"name": "", "text": text, "time": _now_datetime(), "system": True}
    else:
        msg = {"name": name, "text": text, "time": _now_hhmm()}
    await redis_pipeline([
        ["RPUSH", KEYS["messages"], json.dumps(msg)],
        ["LTRIM", KEYS["messages"], f"-{MAX_MESSAGES}", "-1"],
    ])
    await post_to_discord(text if system else f"**{sanitize_display_name(name)}**: {text}")
    return msg


async def list_messages() -> list[dict]:
    return parse_messages(await redis_command(["LRANGE", KEYS["messages"], "0", "-1"]))


async def join_board(cid: str, token: str, *, name: str, task: str, start: str, end: str,
                     image: str | None) -> dict:
    """入室(新規)または情報更新(既存)。部屋番号・ポーズは新規入室時のみ割り当てる"""
    # 画像の検証は、システムメッセージなど状態を変える処理より先に済ませる
    # (不正な画像で400を返すのに、名前変更のメッセージだけ残ってしまうのを防ぐ)
    b64 = None
    if image:
        b64 = decode_chara_image(image)
        if b64 is None:
            return {"error": "invalid image"}

    entries = await list_board()
    existing = next((e for e in entries if e["id"] == cid), None)
    owner = owner_hash(token)

    if existing:
        # ownerを持たない古いエントリも一致しない扱いにする。「無ければ通す」にすると
        # 旧クライアントを装うだけで検証を素通りでき、塞いだはずの穴が残る。
        if existing.get("owner") != owner:
            return {"forbidden": True}
        room = existing["room"]
        pose = existing["pose"]
        if existing["name"] != name:
            await append_message(True, f"✏️ {existing['name']} が {name} に名前を変更")
    else:
        used = {e["room"] for e in entries}
        free = [r for r in range(1, ROOM_COUNT + 1) if r not in used]
        if not free:
            return {"roomFull": True}
        room = random.choice(free)
        pose = random.randint(0, 2)

    imgv = (existing or {}).get("imgv", 0)
    if b64 is not None:
        imgv = await redis_command(["INCR", KEYS["img_seq"]])
        await redis_command(["SET", img_key(cid), b64])

    final_start = start or _now_hhmm()
    joined_at = (existing or {}).get("joinedAt") or int(time.time() * 1000)
    entry = {
        "id": cid,
        "owner": owner,
        "name": name,
        "start": final_start,
        "end": end,
        "task": task,
        "room": room,
        "pose": pose,
        "imgv": imgv,
        "joinedAt": joined_at,
    }
    await redis_command(["HSET", KEYS["board"], cid, json.dumps(entry)])

    if not existing:
        until = f"〜{entry['end']}" if entry["end"] else "〜"
        await append_message(
            True,
            f"🟢 {name} がルーム{room}に入室してもくもく開始({final_start}{until}): {task}",
        )
    return to_public_entry(entry)


async def leave_board(cid: str, token: str) -> dict:
    entries = await list_board()
    entry = next((e for e in entries if e["id"] == cid), None)
    if not entry:
        # 既にいない場合は成功扱い(二重退室・リロード後の退室を素直に通す)
        return {"ok": True}
    if entry.get("owner") != owner_hash(token):
        return {"forbidden": True}

    await redis_command(["HDEL", KEYS["board"], cid])
    await redis_command(["DEL", img_key(cid)])

    end_str = _now_hhmm()
    minutes = 0
    m = re.match(r"^(\d{1,2}):(\d{2})$", entry["start"])
    if m:
        now = datetime.now()
        start_minutes = int(m.group(1)) * 60 + int(m.group(2))
        now_minutes = now.hour * 60 + now.minute
        minutes = (now_minutes - start_minutes) % (24 * 60)
    record = f"{_today_local_date_str()} | {entry['start']}〜{end_str} | {minutes}分 | {entry['task']}"
    await append_message(True, f"🔴 {entry['name']} がルーム{entry['room']}から退室")
    return {"ok": True, "record": record}


async def get_chara_image(cid: str) -> str | None:
    """base64 PNGペイロードを返す"""
    return await redis_command(["GET", img_key(cid)])


async def close_session() -> Config:
    """休会にする。board上の全員分の入室データ・カスタム画像・メッセージを一括削除する"""
    entries = await list_board()
    commands = [["DEL", img_key(e["id"])] for e in entries]
    commands.append(["DEL", KEYS["board"]])
    commands.append(["DEL", KEYS["messages"]])
    await redis_pipeline(commands)
    return await set_config({"open": False})


async def open_session() -> Config:
    return await set_config({"open": True})
