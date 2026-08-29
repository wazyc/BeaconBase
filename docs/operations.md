# 運用

推奨は Docker コンテナとして常駐し、WEB で状況確認と設定変更を行うことである。
CLI の都度実行（cron）も引き続き使える。

## docker compose で常駐

```bash
mkdir -p data ssh
docker compose up -d --build
```

| 項目 | 内容 |
|------|------|
| WEB | http://localhost:8080/ （状況）と `/config`（設定） |
| 永続化 | `./data` → コンテナの `/data`（設定・結果） |
| SSH 鍵 | `./ssh` → `/ssh`（読み取り専用） |
| 監視間隔 | `settings.check_interval`（秒）。環境変数 `BEACONBASE_INTERVAL` で上書き可 |

初回のみ `docker/config.yaml` と `docker/config.d/` のサンプルが `./data` にコピーされる。以降は WEB の設定画面、またはホスト側の YAML を直接編集する。

```bash
docker compose logs -f
docker compose down
```

コンテナ内のエントリは `serve.py` であり、バックグラウンドで定期監視しつつ Flask で WEB を提供する。

## ホストで常駐（docker なし）

```bash
python serve.py -c config.yaml --port 8080
```

## 都度実行（cron）

常駐させず OS スケジューラから起動する場合:

```cron
*/5 * * * * /opt/beaconbase/venv/bin/python /opt/beaconbase/monitor.py -c /opt/beaconbase/config.yaml -q
```

`contrib/crontab.example` も同じ内容である。結果フォルダの `index.html` をブラウザで開いても状態を見られる。

## 通知

`alerts.log` は実行のたびに追記する。状態は `runtime_state.json` に残る。

外部へ出す場合:

- `alerts.webhook.url` … Slack / Discord / 汎用 JSON POST
- `alerts.command` … 通知文を標準入力に渡して実行
- `alerts.email` … SMTP。パスワードは `BEACONBASE_SMTP_PASSWORD` を推奨

`fail_count`（既定 2）で連続失敗してから障害とみなす。`remind_seconds` で継続中の再通知間隔を決める。

## 結果ファイル

| ファイル | 内容 |
|----------|------|
| `latest.json` | 今回の全結果（WEB UI が参照） |
| `index.html` | 単体 HTML ダッシュボード |
| `runtime_state.json` | 連続失敗・障害開始時刻 |
| `alerts.log` | 障害 / 回復の履歴 |
| `check_summary.json` | 今回のサマリー |
| `error_summary.json` | 今回の異常のみ（無ければ削除） |

日次 JSON は `settings.retain_days`（既定 14）より古ければ削除する。

## 設定のコツ

- 使わない監視セクションは書かない
- `group` を付けるとダッシュボードで役割が分かる
- コンテナ利用時、SSH 鍵パスは `/ssh/...` を指す
- `check_interval` を短くしすぎると対象機器やネットワークに負荷がかかる
