import base64
import binascii
import hmac
import os
import queue
import random
import threading
import time
import urllib.request
import json as _json
from flask import Flask, request, jsonify, send_from_directory, Response
from datetime import datetime

# サーバーのOSタイムゾーン(EC2は既定でUTC)に関わらず、入退室記録をJST(クライアント側の時刻)と揃える
os.environ["TZ"] = "Asia/Tokyo"
time.tzset()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
messages = []
board = {}
# カスタムキャラ画像は board と同じライフサイクル(退室で破棄、再起動で消える)
custom_images = {}  # cid -> {"data": bytes, "v": int}
_img_seq = 0  # キャッシュバスター用の通し番号。退室しても巻き戻さない(再入室時のキャッシュ誤爆防止)
MAX_IMAGE_B64 = 700_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ROOM_COUNT = 9
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
# 直近のDiscord送信結果。None=未送信。URL失効(404)等に画面で気づけるように保持する
discord_last_ok = None
SETTINGS_FILE = "config/settings.json"
DEFAULT_PASSPHRASE_FILE = "config/合言葉.txt"

# 設定は毎回読む(サーバー再起動なしでモード切替できるようにするため)
def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError) as e:
        print(f"[settings] {SETTINGS_FILE} を読めないためデフォルト(mode=very_easy)で動作: {e}")
        return {}

# セキュリティモード(デフォルト: very_easy):
#   none      … 認証なし(閲覧・書き込みとも自由)
#   very_easy … 閲覧は自由。書き込み系(投稿/入室/退室)は部屋共通の合言葉が必要。
#               ただし合言葉ファイルが未設置(または空)の間は認証なしで通す
def check_passphrase(data):
    security = load_settings().get("security", {})
    if security.get("mode", "very_easy") != "very_easy":
        return None
    path = security.get("passphrase_file", DEFAULT_PASSPHRASE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            expected = f.read().strip()
    except OSError:
        expected = ""
    if not expected:
        return None
    supplied = ((data or {}).get("passphrase") or "").strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        return jsonify({"error": "wrong passphrase", "authRequired": True}), 401
    return None

# クライアントがcanvasで縮小・PNG化したデータURLを検証してPNGバイト列を返す。不正ならNone
def decode_chara_image(image):
    prefix = "data:image/png;base64,"
    if not isinstance(image, str) or not image.startswith(prefix) or len(image) > MAX_IMAGE_B64:
        return None
    try:
        raw = base64.b64decode(image[len(prefix):], validate=True)
    except (ValueError, binascii.Error):
        return None
    if not raw.startswith(PNG_MAGIC):
        return None
    return raw

def post_to_discord(content):
    global discord_last_ok
    if not DISCORD_WEBHOOK_URL:
        return
    payload = _json.dumps({"content": content}).encode()
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "mokumoku-bot/1.0"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        discord_last_ok = True
    except Exception as e:
        discord_last_ok = False
        print(f"[Discord] error: {e}")

def add_system_message(text):
    messages.append({
        "name": "",
        "text": text,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "system": True,
    })
    post_to_discord(text)

# Discord連携の現在状態: off=URL未設定 / on=設定済み / error=直近の送信が失敗(URL失効など)
def get_discord_status():
    if not DISCORD_WEBHOOK_URL:
        return "off"
    return "error" if discord_last_ok is False else "on"

# SSE購読者(/events に接続中の各クライアント用キュー)。書き込み系ルートの最後にbroadcast()を呼んで配信する
subscribers_lock = threading.Lock()
subscribers = set()

def snapshot():
    return {
        "messages": messages,
        "board": list(board.values()),
        "status": {"discord": get_discord_status()},
    }

def broadcast():
    data = _json.dumps(snapshot())
    with subscribers_lock:
        dead = []
        for q in subscribers:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            subscribers.discard(q)

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# 部屋の画像は config/settings.json の appearance.room_image で差し替え可能(再起動不要)
@app.route("/room-image.png")
def room_image():
    path = load_settings().get("appearance", {}).get("room_image") or "assets/room-image-1.png"
    directory, filename = os.path.split(path)
    return send_from_directory(directory or ".", filename)

@app.route("/chara-image.png")
def chara_image():
    return send_from_directory("assets", "chara-image-1.png")

# インメモリdictの参照のみ(ファイルシステム非接触)。バージョン付きURLで配信するので長めにキャッシュ可
@app.route("/chara-custom/<cid>.png")
def chara_custom(cid):
    img = custom_images.get(cid)
    if not img:
        return jsonify({"error": "not found"}), 404
    return Response(img["data"], mimetype="image/png",
                    headers={"X-Content-Type-Options": "nosniff",
                             "Cache-Control": "public, max-age=86400"})

@app.route("/status")
def get_status():
    return jsonify({"discord": get_discord_status()})

# 状態変化をプッシュ配信するSSEエンドポイント。接続直後に現在の全状態を1回送り、
# 以後は書き込み系ルートがbroadcast()した時点の全状態を都度配信する
@app.route("/events")
def sse_events():
    q = queue.Queue(maxsize=10)
    with subscribers_lock:
        subscribers.add(q)

    def gen():
        try:
            yield f"data: {_json.dumps(snapshot())}\n\n"
            while True:
                try:
                    data = q.get(timeout=15)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    # プロキシ(cloudflaredなど)やブラウザ側のアイドルタイムアウトによる切断を防ぐ
                    yield ": keep-alive\n\n"
        finally:
            with subscribers_lock:
                subscribers.discard(q)

    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })

@app.route("/messages", methods=["GET"])
def get_messages():
    return jsonify(messages)

@app.route("/messages", methods=["POST"])
def post_message():
    data = request.get_json()
    err = check_passphrase(data)
    if err:
        return err
    name = data.get("name", "").strip()
    text = data.get("text", "").strip()
    if not name or not text:
        return jsonify({"error": "name and text required"}), 400
    msg = {
        "name": name,
        "text": text,
        "time": datetime.now().strftime("%H:%M"),
    }
    messages.append(msg)
    post_to_discord(f"**{name}**: {text}")
    broadcast()
    return jsonify(msg), 201

@app.route("/board", methods=["GET"])
def get_board():
    return jsonify(list(board.values()))

# board はクライアントID(ブラウザごとに固定)をキーに持つ。名前は表示用で変更可
@app.route("/board/join", methods=["POST"])
def join_board():
    data = request.get_json()
    err = check_passphrase(data)
    if err:
        return err
    cid = (data.get("id") or "").strip()
    name = (data.get("name") or "").strip()
    task = (data.get("task") or "").strip()
    if not cid or not name or not task:
        return jsonify({"error": "id, name and task required"}), 400
    start = (data.get("start") or "").strip() or datetime.now().strftime("%H:%M")
    end = (data.get("end") or "").strip()
    is_new = cid not in board
    if is_new:
        used = {e["room"] for e in board.values()}
        free = [r for r in range(1, ROOM_COUNT + 1) if r not in used]
        if not free:
            return jsonify({"roomFull": True}), 200
        room = random.choice(free)
        pose = random.randint(0, 2)
    else:
        room = board[cid]["room"]
        pose = board[cid]["pose"]
        old_name = board[cid]["name"]
        if old_name != name:
            add_system_message(f"✏️ {old_name} が {name} に名前を変更")
    # 画像は任意。未送信なら既存のカスタム画像を維持(imgvはcustom_imagesから再計算)
    image = data.get("image")
    if image:
        raw = decode_chara_image(image)
        if raw is None:
            return jsonify({"error": "invalid image"}), 400
        global _img_seq
        _img_seq += 1
        custom_images[cid] = {"data": raw, "v": _img_seq}
    imgv = custom_images.get(cid, {}).get("v", 0)
    board[cid] = {"id": cid, "name": name, "start": start, "end": end, "task": task, "room": room, "pose": pose, "imgv": imgv}
    if is_new:
        until = f"〜{end}" if end else "〜"
        add_system_message(f"🟢 {name} がルーム{room}に入室してもくもく開始({start}{until}): {task}")
    broadcast()
    return jsonify(board[cid]), 201

@app.route("/board/leave", methods=["POST"])
def leave_board():
    data = request.get_json()
    err = check_passphrase(data)
    if err:
        return err
    cid = (data.get("id") or "").strip()
    entry = board.pop(cid, None)
    custom_images.pop(cid, None)  # 画像はその入室の間だけ有効
    if not entry:
        return jsonify({"ok": True})
    now = datetime.now()
    end_str = now.strftime("%H:%M")
    try:
        h, m = map(int, entry["start"].split(":"))
        minutes = (now.hour * 60 + now.minute - h * 60 - m) % (24 * 60)
    except ValueError:
        minutes = 0
    # 人間可読かつ機械処理しやすい固定順の1行記録
    record = f"{now.strftime('%Y-%m-%d')} | {entry['start']}〜{end_str} | {minutes}分 | {entry['task']}"
    add_system_message(f"🔴 {entry['name']} がルーム{entry['room']}から退室")
    broadcast()
    return jsonify({"ok": True, "record": record})

if __name__ == "__main__":
    from waitress import serve
    print("もくもくサーバー起動: http://127.0.0.1:5000 (停止は Ctrl+C)", flush=True)
    # SSE接続は張りっぱなしでスレッドを1本占有し続けるため、既定の4スレッドでは閲覧者が数人いるだけで
    # 書き込みAPIがブロックされる。部屋数(9)+閲覧のみのユーザーを見込んで余裕を持たせる
    serve(app, host="127.0.0.1", port=5000, threads=32)
