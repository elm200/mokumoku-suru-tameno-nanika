#!/bin/bash
set -eu -o pipefail
exec > /var/log/mokumoku_userdata.log 2>&1

apt-get update
apt-get install -y python3-pip python3-venv git

git clone --branch main --depth 1 https://github.com/haya256/mokumoku-suru-tameno-nanika.git /opt/mokumoku
cd /opt/mokumoku

python3 -m venv venv
venv/bin/pip install -r requirements.txt

mkdir -p config
echo '${PASSPHRASE_B64}' | base64 -d > config/合言葉.txt
echo '${SETTINGS_JSON_B64}' | base64 -d > config/settings.json

export DISCORD_WEBHOOK_URL='${DISCORD_WEBHOOK_URL}'
nohup venv/bin/python3 server.py > /var/log/mokumoku_server.log 2>&1 &

curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
dpkg -i /tmp/cloudflared.deb || apt-get install -f -y

# --protocol http2: 既定のQUICは長時間張りっぱなしの接続(WebSocketの/wsなど)が
# "stream canceled by remote with error code 0" で断続的に切れる既知の問題があるため固定する
nohup cloudflared tunnel --protocol http2 --url http://localhost:5000 > /var/log/tunnel_raw.log 2>&1 &

: > /var/log/tunnel_url.log
for i in $$(seq 1 60); do
  url=$$(grep -o 'https://[a-zA-Z0-9.-]*\.trycloudflare\.com' /var/log/tunnel_raw.log | head -n1 || true)
  if [ -n "$$url" ]; then
    echo "$$url" > /var/log/tunnel_url.log
    break
  fi
  sleep 1
done

# 忘れて stop_event.py を実行し損ねても課金が続かないようにする保険(正規の停止手段は stop_event.py)
shutdown -h +${AUTO_TERMINATE_MINUTES}
