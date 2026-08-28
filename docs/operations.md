# 運用

起動したときだけ監視する。常駐プロセスは持たない。定期的に見たい場合は OS の cron やタスク スケジューラから、同じコマンドを都度起動する。

## まず動かす

```bash
python monitor.py -c config.yaml --validate
python monitor.py -c config.yaml --only ping,ports
python monitor.py -c config.yaml
```

結果は `storage.output_folder` に出る。ダッシュボードは `index.html` をブラウザで開く。

## cron（Linux）

5 分ごとに1回実行する例:

```cron
*/5 * * * * /opt/beaconbase/venv/bin/python /opt/beaconbase/monitor.py -c /opt/beaconbase/config.yaml -q
```

`contrib/crontab.example` も同じ内容である。ログ収集を毎回走らせたくないときは `--only ping,ports,disk,docker,web_health` を付ける。

## Windows タスク スケジューラ

管理者 PowerShell 例（5 分ごと）:

```powershell
schtasks /Create /SC MINUTE /MO 5 /TN BeaconBase /TR "C:\beaconbase\venv\Scripts\python.exe C:\beaconbase\monitor.py -c C:\beaconbase\config.yaml -q"
```

## 通知

`alerts.log` は実行のたびに追記する。状態は `runtime_state.json` に残るので、cron で間をおいて起動しても連続失敗や回復を判定できる。

外部へ出す場合:

- `alerts.webhook.url` … Slack / Discord / 汎用 JSON POST。`format: auto` で URL から判別する
- `alerts.command` … 通知文を標準入力に渡して実行する
- `alerts.email` … SMTP。パスワードは `BEACONBASE_SMTP_PASSWORD` を推奨

`fail_count`（既定 2）で連続失敗してから障害とみなす。瞬断の誤報を減らす。回復したら回復通知を出す。`remind_seconds` で継続中の再通知間隔を決める。

## 結果ファイル

| ファイル | 内容 |
|----------|------|
| `index.html` | ダッシュボード |
| `latest.json` | 今回の全結果 |
| `runtime_state.json` | 連続失敗・障害開始時刻 |
| `alerts.log` | 障害 / 回復の履歴 |
| `check_summary.json` | 今回のサマリー |
| `error_summary.json` | 今回の異常のみ（無ければ削除） |

日次 JSON は `settings.retain_days`（既定 14）より古ければ削除する。

## 設定のコツ

- 使わない監視セクションは書かない
- `group` を付けるとダッシュボードで役割が分かる
- Ping だけでなく、NAS なら 445、ルータなら 53 などポートも見る
- ディスクは SSH 鍵が届くサーバだけでよい
