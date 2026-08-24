# クライアントから届いたJSONフィールドの検証。すべて純粋関数(Redisに触らない)。
#
# 方針: 上限を超えた入力は切り詰めずにNoneを返し、呼び出し側が400で拒否する。
# 黙って内容を書き換えると、ユーザーは自分が何を送ったのか分からなくなるため。
import re

# 1リクエストのボディ上限。カスタムキャラ画像がbase64で最大700KBなので、その分の余裕を見る。
# (Flask版のserver.pyにあった MAX_CONTENT_LENGTH = 2MB に相当するものが、Vercel移植で失われていた)
MAX_BODY_BYTES = 1_000_000

MAX_NAME = 40
MAX_TASK = 100
MAX_TEXT = 500
MAX_ID = 64
MAX_TOKEN = 128

# 制御文字。改行を許すフィールド(チャット本文)用に、\n を残す版も持つ
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CONTROL_KEEP_NEWLINE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")

_OPAQUE = re.compile(r"^[A-Za-z0-9_-]+$")
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


def clean_text(value: object, limit: int, *, allow_newline: bool = False) -> str | None:
    """必須のテキストフィールド。制御文字を除いてstripし、空または上限超過ならNone"""
    if not isinstance(value, str):
        return None
    pattern = _CONTROL_KEEP_NEWLINE if allow_newline else _CONTROL
    cleaned = pattern.sub("", value).strip()
    if not cleaned or len(cleaned) > limit:
        return None
    return cleaned


def clean_opaque(value: object, limit: int) -> str | None:
    """id・トークンのような不透明な識別子。英数と - _ のみ許す"""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit or not _OPAQUE.match(cleaned):
        return None
    return cleaned


def clean_hhmm(value: object) -> str | None:
    """<input type="time"> 由来の HH:MM。未入力("")も正当な値として通す。不正ならNone"""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return ""
    m = _HHMM.match(cleaned)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return cleaned
