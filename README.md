# もくもく会チャット

もくもく会（それぞれが黙々と自分の作業をする集まり）を、オンラインでゆるくつながりながらやるためのアプリです。

![もくもく会の画面](assets/readme-screenshot.png)

ブラウザで開くと9つの部屋があるピクセルアートの建物が表示され、「入室」すると自分のキャラクターがどこかの部屋に入ります。誰が・いつまで・何をもくもくしているかが一覧で見え、ひとことチャットもできます。退室すると「今日なにをどれだけやったか」の記録が1行で出てくるので、日報などにコピペできます。

- アカウント登録は不要。名前を入れるだけ
- 閲覧は誰でも自由。書き込みは部屋共通の「合言葉」で保護できます（任意）
- チャットをDiscordのチャンネルに流すこともできます（任意）

---

## 参加する人向け：使い方

サーバーは主催者が立てています。**主催者からURL（と合言葉）を教えてもらってから**始めてください。

1. 教えてもらったURLをブラウザで開く
2. 画面下のフォームに **名前** と **もくもくする内容**（例:「読書」「資料づくり」）を入力する
   - 開始時刻は自動で入ります。終了予定は任意です
3. **「入室」ボタン** を押す
   - 合言葉を聞かれたら、主催者から教えてもらったものを入力します（最初の1回だけ。以降はこのブラウザが覚えます）
4. どこかの部屋に自分のキャラクターが現れたら、もくもく開始!
   - チャット欄が使えるようになります。Enterで送信、Shift+Enterで改行です
   - 内容や終了予定を変えたくなったら「編集」ボタンで変更できます
5. 終わったら **「退室」ボタン** を押す
   - 「📋 今日の記録」（日付・時間・実績分数・やったこと）が表示されるので、「コピー」して日報などに貼れます

### ちょっとした補足

- 部屋は9つです。満室のときは空きが出るまで待ってください
- 「本人かどうか」はブラウザ単位で覚えています。同じブラウザで開き直せば続きから使えますが、別の端末からは別人扱いになります
- 名前は途中で変えても大丈夫です（変更したことがチャットに流れます）

---

## 主催者向け：サーバーの立て方

参加者にHTTPSの使い捨てURLでアクセスしてもらう仕組みを、AWS EC2上で自動化しています。イベントのたびにインスタンスを起動 → URLを取得 → 終わったら破棄、という流れをスクリプト2本で行います。**AWSアカウントでの一度だけの準備**が必要ですが、それさえ済ませれば毎回はコマンド2つで完結します。

### 必要なもの

- AWSアカウント（`t3.micro`は無料枠の対象になる場合が多いですが、課金の可能性があることは理解した上で進めてください）
- Python 3.x が入ったパソコン（Mac / Linux / Windows(WSL)）※アプリ本体はAWS上で動くので、参加者を招く用途でこのパソコン自体を公開する必要はありません
- git

### 初回だけの準備

一度やれば、以降は「イベントごと」の2コマンドだけで済みます。

```bash
# 1. このリポジトリを取得
git clone https://github.com/haya256/mokumoku-suru-tameno-nanika.git
cd mokumoku-suru-tameno-nanika

# 2. 起動スクリプトが使うライブラリを入れる
python3 -m venv venv
venv/bin/pip install boto3 awscli
```

#### AWSコンソールでの設定（一度だけ）

1. **IAMロール（兼インスタンスプロフィール）を作る** — 起動したインスタンスがSSM経由で操作できるようにするためのものです
   - IAM → ロール → 「ロールを作成」→ 信頼するエンティティ: **AWSのサービス** → ユースケース: **EC2**
   - 許可ポリシーで `AmazonSSMManagedInstanceCore` を検索してチェック
   - ロール名は `mokumoku-ssm-role` など分かりやすい名前に。作成すると同じ名前のインスタンスプロフィールも自動的にできます
2. **セキュリティグループを作る** — インバウンドルールは追加不要です。cloudflaredもSSM Agentもインスタンス側から外向きに接続するだけなので、ポートを開ける必要がありません
   - EC2 → セキュリティグループ → 「セキュリティグループを作成」→ VPCは**デフォルトVPC**を選択
   - インバウンドルールは何も追加しない（アウトバウンドは初期状態の「すべて許可」のままでOK）
3. **操作用のIAMユーザーを作る** — このパソコンから起動・終了スクリプトを実行するための認証情報です
   - IAM → ユーザー → 「ユーザーを作成」→ アクセスキーを発行
   - 以下をインラインポリシーとして貼り付けます（`<ロールのARN>` は手順1で作ったロールのARNに置き換え）

     ```json
     {
       "Version": "2012-10-17",
       "Statement": [
         {
           "Effect": "Allow",
           "Action": [
             "ec2:RunInstances",
             "ec2:TerminateInstances",
             "ec2:CreateTags",
             "ec2:Describe*",
             "ssm:SendCommand",
             "ssm:GetCommandInvocation",
             "ssm:DescribeInstanceInformation"
           ],
           "Resource": "*"
         },
         {
           "Effect": "Allow",
           "Action": "iam:PassRole",
           "Resource": "<ロールのARN>"
         }
       ]
     }
     ```

   - 発行されたアクセスキーID・シークレットアクセスキーを控えておく
4. **ローカルにAWS認証情報を設定**

   ```bash
   venv/bin/aws configure
   # アクセスキーID、シークレットアクセスキー、デフォルトリージョン(ap-northeast-1)を入力
   ```

5. **`config/settings.json` を作って `deploy` セクションを埋める**

   ```bash
   cp config/settings.sample.json config/settings.json
   ```

   `ami_id` は最新のUbuntuイメージIDを次のコマンドで調べられます（実行するたびに最新版が得られます）。

   ```bash
   venv/bin/aws ssm get-parameters \
     --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
     --region ap-northeast-1 --query 'Parameters[0].Value' --output text
   ```

   `security_group_id` / `subnet_id` は手順2で作ったセキュリティグループのID、デフォルトVPC内の適当なサブネットのID（AWSコンソールの「サブネット」一覧で確認できます）。`iam_instance_profile_name` は手順1で作ったロール名です。

   ```json
   {
     "deploy": {
       "region": "ap-northeast-1",
       "instance_type": "t3.micro",
       "ami_id": "ami-xxxxxxxxxxxxxxxxx",
       "security_group_id": "sg-xxxxxxxxxxxxxxxxx",
       "subnet_id": "subnet-xxxxxxxxxxxxxxxxx",
       "iam_instance_profile_name": "mokumoku-ssm-role",
       "auto_terminate_hours": 6
     }
   }
   ```

6. **合言葉を設定する**（推奨。詳しくは後述の「合言葉を設定する」を参照）

   ```bash
   mkdir -p config
   echo "好きな合言葉" > config/合言葉.txt
   ```

これで準備は完了です。

### イベントごと：起動と終了

もくもく会を開くたびに、この2コマンドだけです。

```bash
# イベント開始時: インスタンスを起動してURLを取得(数分かかります)
venv/bin/python deploy/start_event.py
```

`参加者に共有するURL: https://xxxxx.trycloudflare.com` のように表示されたら、そのURLを参加者に伝えます。

```bash
# イベント終了時: インスタンスを完全に破棄(データも消えます)
venv/bin/python deploy/stop_event.py
```

### 知っておいてほしいこと

- **データも環境もイベントごとに使い捨てです。** `stop_event.py` でインスタンスごと破棄され、チャット履歴や入室記録は一切残りません
- URLは起動するたびに変わります。もくもく会のたびに新しいURLを伝えてください
- `stop_event.py` を実行し忘れた場合に備えて、インスタンス内で**6時間後に自動シャットダウン**する保険が入っています。ただし課金を確実に止めるには、シャットダウン待ちにせず `stop_event.py` で明示的に終了させることを推奨します
- インスタンス起動時に渡す情報(合言葉やDiscord Webhook URLを含む)は、同じAWSアカウント内で権限を持つ人なら閲覧できる状態になります。SSHでログインされるのと同程度の信頼範囲だと考えてください
- AWSを使わずローカルで動作確認だけしたい場合は `venv/bin/pip install -r requirements.txt && venv/bin/python server.py` で `http://localhost:5000` を開けます

### 困ったとき（デプロイ関連）

URLが出ずタイムアウトする場合は、SSM経由でインスタンスに入ってログを確認できます。

```bash
venv/bin/aws ssm start-session --target <start_event.pyが表示したインスタンスID>
cat /var/log/mokumoku_userdata.log   # セットアップ全体のログ
cat /var/log/mokumoku_server.log     # server.py自体のログ
cat /var/log/tunnel_raw.log          # cloudflaredのログ
```

インスタンスが残っていないか不安なときは、AWSコンソールのEC2画面か、以下のコマンドで確認できます。

```bash
venv/bin/aws ec2 describe-instances --filters "Name=tag:Name,Values=mokumoku-event" "Name=instance-state-name,Values=running"
```

### 代替手段：VPS + Cloudflare Tunnel（手動・AWSを使いたくない場合）

AWSアカウントを作りたくない場合や、すでに自分のVPSを持っていて使い慣れている場合は、こちらの手動デプロイもできます。EC2自動化と違い、`server.py` と `cloudflared` の起動・停止は自分で行う必要があります。

- Python 3.x が入ったVPS（レンタルサーバー）（Mac / Linux / Windows(WSL)でも試せますが、**参加者を集めて実際に公開するならVPSを推奨**します。理由は後述）

```bash
# 1. このリポジトリを取得
git clone https://github.com/haya256/mokumoku-suru-tameno-nanika.git
cd mokumoku-suru-tameno-nanika

# 2. Python の仮想環境を作って必要なライブラリを入れる（初回だけ）
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 3. サーバーを起動
venv/bin/python server.py
```

起動したら、ブラウザで http://localhost:5000 を開いて動作を確認してください。

参加者にインターネット越しにアクセスしてもらう方法として、Cloudflare の「クイックトンネル」が使えます。アカウント登録なし・無料で、サーバーに一時的なURL（HTTPS付き）でアクセスできるようになります。

> ⚠️ **自分の使っているパソコンで直接動かすのは非推奨です。** 万一このアプリに脆弱性があった場合、パソコンそのものがインターネットに直接晒される形になり、被害が他の作業データやブラウザのログイン状態などパソコン全体に及ぶ可能性があります。**参加者を集めて実際に使うときは、VPS（レンタルサーバー）上で動かしてください。** 何なら侵害されてもそのVPSだけの被害で済みます。

#### VPS上で動かす場合

VPSにSSHでログインした状態で実行します。`server.py`（アプリ本体）と `cloudflared`（公開用トンネル）の2つを起動しっぱなしにする必要があります。やり方は2パターンあるので、好みで選んでください。

**パターンA: 2つのSSHセッションを開く（シンプル）**

上の `venv/bin/python server.py` を実行したセッションをそのまま残しておき、**別のSSHセッションをもう1つ開いて**、以下を実行します。SSHセッションを閉じると両方止まるので、動作確認だけしたい・短時間だけ使う場合向けです。

```bash
# 1. cloudflaredをインストール（初回だけ、Ubuntu/Debian系）
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
sudo dpkg -i /tmp/cloudflared.deb

# 2. トンネルを開始(--protocol http2: 既定のQUICだと長時間接続が途中で切れることがあるため)
cloudflared tunnel --protocol http2 --url http://localhost:5000
```

**パターンB: nohup でバックグラウンド化（SSHセッション1つで完結、おすすめ）**

`server.py` を `nohup` でバックグラウンドに回すと、SSHセッションを閉じてもサーバーが動き続けます。同じセッションのまま続けて `cloudflared` も起動できます。

```bash
# 1. サーバーをバックグラウンドで起動
nohup venv/bin/python server.py > server.log 2>&1 &
disown

# 2. 動いているか確認（HTMLが返ってくればOK）
curl http://localhost:5000

# 3. cloudflaredをインストール（初回だけ、Ubuntu/Debian系）
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
sudo dpkg -i /tmp/cloudflared.deb

# 4. トンネルを開始(--protocol http2: 既定のQUICだと長時間接続が途中で切れることがあるため)
cloudflared tunnel --protocol http2 --url http://localhost:5000
```

- ログを見たいときは `tail -f server.log`
- サーバーだけ止めたいときは `pkill -f server.py`

少し待つと `https://ほにゃらら.trycloudflare.com` のようなURLが表示されます。これをそのまま参加者に伝えます（サーバーは`127.0.0.1`でしか待ち受けないので、ファイアウォールを開ける必要はありません）。

終わったら、サーバーとトンネル両方のプロセスを止めます。

```bash
ps aux | grep -E "server.py|cloudflared"
kill <server.pyのPID> <cloudflaredのPID>
```

知っておいてほしいこと：

- URLは実行するたびに変わります。もくもく会のたびに新しいURLを伝えてください
- インターネット上の誰でもURLさえ知ればアクセスできる状態になります。**後述の「合言葉を設定する」を必ず設定してから**URLを共有するのがおすすめです
- `python3 -m venv venv` で「ensurepipが無い」というエラーが出た場合は、`sudo apt install python3.12-venv` を実行してから作り直してください
- **データはサーバーを止めると消えます**（メモリ上にだけ保存しています）。1回のもくもく会ごとに使い切るイメージです
- 止めるときはターミナルで `Ctrl+C` です。反応しない場合は `ps aux | grep server.py` でプロセスを探し、`kill <PID>` で止めてください

---

## 合言葉を設定する（おすすめ）

そのままでも使えますが、URLを知っていれば誰でも書き込めてしまいます。**ファイルを1つ置くだけ**で、書き込み・入室に合言葉が必要になります（閲覧は誰でも可能なまま）。

```bash
mkdir -p config
echo "好きな合言葉" > config/合言葉.txt
```

- サーバーの再起動は不要です。置いた瞬間から合言葉がかかります
- 合言葉を変えたいときはファイルを書き換えるだけ、外したいときはファイルを消すだけです
- 参加者には口頭やDiscordなどで合言葉を伝えてください

### もっと細かく設定したい場合（config/settings.json）

セキュリティの動作は `config/settings.json` で変えられます（このファイルが無ければ既定値で動くので、作るのは変えたいときだけでOKです）。

サンプルをコピーして作ります。

```bash
cp config/settings.sample.json config/settings.json
```

中身はこうなっています。

```json
{
  "security": {
    "mode": "very_easy",
    "passphrase_file": "config/合言葉.txt"
  },
  "appearance": {
    "room_image": "assets/room-image-1.png"
  }
}
```

| 設定 | 意味 |
| --- | --- |
| `security.mode: "very_easy"` | 閲覧は自由、書き込み・入室・退室に合言葉が必要（既定）。ただし合言葉ファイルが無い間は合言葉なしで通ります |
| `security.mode: "none"` | 完全に認証なし。合言葉ファイルがあっても聞かれません |
| `security.passphrase_file` | 合言葉を書いたファイルの場所（既定: `config/合言葉.txt`） |
| `appearance.room_image` | 画面左に表示する部屋の画像（既定: `assets/room-image-1.png`）。`assets/room-image-2.png` に変えたり、自分で用意した画像のパスを指定できます |

設定の変更はサーバー再起動なしで反映されます（部屋の画像はブラウザの再読み込みで切り替わります）。なお `config/` ディレクトリはgit管理外なので、合言葉をうっかり公開してしまう心配はありません。

---

## Discord連携（任意）

チャットや入退室の通知をDiscordのチャンネルにも流せます。

### Webhook URL の取得手順

1. Discord で通知を送りたいチャンネルを右クリック → **「チャンネルの編集」**
2. **「連携サービス」** タブを開く
3. **「ウェブフック」** → **「新しいウェブフック」** をクリック
4. 名前を設定し、**「ウェブフック URL をコピー」** をクリック

> 開発者モードや Bot の登録は不要です。

### 環境変数に設定して起動

```bash
export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
venv/bin/python server.py
```

EC2で起動する場合も同様に、`deploy/start_event.py` を実行する**前**に同じシェルで `export DISCORD_WEBHOOK_URL=...` しておけば、インスタンス起動時に自動で渡されます。

> ⚠️ Webhook URL は秘密情報です。チャットなどに貼ると自動で無効化されることがあります。

---

## 困ったときは

| 症状 | 対処 |
| --- | --- |
| 「満室です」と出る | 部屋は9つまでです。誰かが退室するのを待ってください |
| 「合言葉が違います」と出続ける | 主催者に正しい合言葉を確認してください。正しいものを入れ直せば通ります |
| 他の端末から自分の入室が消せない | 本人確認はブラウザ単位です。入室したのと同じブラウザから退室してください |
| ページは開くがチャット欄が出ない | チャットは入室中だけ使えます。まず「入室」してください |

## ライセンス

MIT
